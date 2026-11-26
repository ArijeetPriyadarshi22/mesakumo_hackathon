"""Read local tender documents and store uploaded source files."""
from pathlib import Path
import posixpath
from .domain import SourcePassage

import hashlib
import zipfile
import xml.etree.ElementTree as ET
from uuid import uuid4
from .domain import Document
from .workflow import invalidate

MAX_FILE_BYTES = 15 * 1024 * 1024
ROLES = ('tender', 'knowledge', 'text_module', 'template')

class LocalProcessor:
    def extract(self, path, document_id, *, gaps=None):
        suffix = path.suffix.lower()
        with path.open('rb') as file:
            version = hashlib.file_digest(file, 'sha256').hexdigest()
        chunks = []
        if suffix == '.txt':
            try:
                text = path.read_text(encoding='utf-8-sig')
            except UnicodeDecodeError:
                text = path.read_text(encoding='cp1252')
            lines = text.splitlines()
            for start in range(0, len(lines), 20):
                chunks.append((f'Lines {start+1}–{min(start+20, len(lines))}', '\n'.join(lines[start:start+20])))
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(''):
                raise ValueError('Encrypted PDF: supply an unencrypted copy.')
            for index, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ''
                except Exception:
                    if gaps is None:
                        raise
                    text = ''
                location = f'Page {index}'
                if not text.strip():
                    if gaps is None:
                        raise ValueError(f'PDF page {index} has no extractable text; possible scan. OCR is not supported. No pages included.')
                    text = _ocr(path, index)
                    if not text.strip():
                        gaps.append(f'{location}: no extractable text or local OCR result.')
                        continue
                    location += ' (OCR)'
                chunks.append((location, text))
        elif suffix == '.xlsx':
            ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
                    raise ValueError('XLSX expanded size exceeds 40 MB.')
                strings = []
                if 'xl/sharedStrings.xml' in archive.namelist():
                    root=ET.fromstring(archive.read('xl/sharedStrings.xml'))
                    strings=[''.join(si.itertext()) for si in root.findall('s:si',ns)]
                workbook=ET.fromstring(archive.read('xl/workbook.xml'))
                rels=ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
                targets={r.attrib['Id']:r.attrib['Target'] for r in rels}
                for sheet in workbook.findall('s:sheets/s:sheet',ns):
                    rid=sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']
                    target=targets[rid]
                    member=target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/'+target)
                    root=ET.fromstring(archive.read(member))
                    for row in root.findall('s:sheetData/s:row',ns):
                        cells=[]
                        for cell in row.findall('s:c',ns):
                            value=cell.findtext('s:v',default='',namespaces=ns)
                            if cell.attrib.get('t')=='s': value=strings[int(value)] if value else ''
                            elif cell.attrib.get('t')=='inlineStr':
                                value=''.join(t.text or '' for t in cell.findall('.//s:t',ns))
                            formula=cell.findtext('s:f',default='',namespaces=ns)
                            if formula: value += ' [formula: '+formula+']'
                            if value.strip(): cells.append(cell.attrib.get('r','')+': '+value)
                        if cells: chunks.append((f"Sheet {sheet.attrib['name']} · Row {row.attrib.get('r','?')}", '\n'.join(cells)))
        elif suffix == '.aidf':
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
                    raise ValueError('AIDF expanded size exceeds 40 MB.')
                root=ET.fromstring(archive.read('data.lv'))
            for index, element in enumerate(root.iter(),1):
                if element.text and element.text.strip():
                    chunks.append((f'data.lv · {element.tag.split("}")[-1]} · element {index}',element.text.strip()))
        elif suffix == '.pptx':
            chunks = presentation_text(path)
        elif suffix == '.docx':
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024:
                    raise ValueError('DOCX expanded size exceeds 40 MB.')
                xml = archive.read('word/document.xml')
            root = ET.fromstring(xml)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            heading = ''
            for index, paragraph in enumerate(root.findall('.//w:body//w:p', ns), 1):
                text = ''.join((node.text or '') if node.tag == '{'+ns['w']+'}t' else ('\t' if node.tag == '{'+ns['w']+'}tab' else '\n') for node in paragraph.iter() if node.tag in ('{'+ns['w']+'}t', '{'+ns['w']+'}tab', '{'+ns['w']+'}br', '{'+ns['w']+'}cr'))
                style = paragraph.find('w:pPr/w:pStyle', ns)
                if style is not None and 'heading' in next(iter(style.attrib.values()), '').lower():
                    heading = text
                chunks.append((f'Body paragraph {index}' + (f' · {heading}' if heading else ''), text))
            with zipfile.ZipFile(path) as archive:
                for name in sorted(archive.namelist()):
                    if name.startswith('word/') and (Path(name).name.startswith(('header','footer')) or Path(name).name in ('footnotes.xml','endnotes.xml')) and name.endswith('.xml'):
                        extra=ET.fromstring(archive.read(name))
                        for index, paragraph in enumerate(extra.findall('.//w:p',ns),1):
                            text=''.join(n.text or '' for n in paragraph.findall('.//w:t',ns))
                            if text.strip(): chunks.append((f'{Path(name).name} · paragraph {index}',text))

        else:
            raise ValueError('Supported extraction formats: TXT, text PDF, DOCX, XLSX, AIDF and PPTX.')
        passages = [SourcePassage(f'{document_id}-{version[:12]}-P{i}', document_id, location, text, version)
                    for i, (location, text) in enumerate(chunks, 1) if text.strip()]
        if not passages:
            raise ValueError('No readable text found. Empty documents and scans are not analysed.')
        return passages


def _ocr(path, page):
    import os
    import shutil
    import subprocess
    import tempfile

    render, ocr = shutil.which('pdftoppm'), shutil.which('tesseract')
    if not render or not ocr:
        return ''
    with tempfile.TemporaryDirectory() as folder:
        image = Path(folder) / 'page'
        try:
            subprocess.run([render, '-f', str(page), '-l', str(page), '-r', '180',
                            '-singlefile', '-png', str(path), str(image)],
                           check=True, timeout=30, capture_output=True)
            result = subprocess.run([ocr, str(image.with_suffix('.png')), 'stdout', '-l',
                                     os.environ.get('MESAKUMO_OCR_LANGUAGES') or 'deu+eng'],
                                    check=True, timeout=40, capture_output=True, encoding='utf-8')
            return result.stdout
        except (OSError, subprocess.SubprocessError):
            return ''

def presentation_text(path):
    """Read slide text in presentation order without expanding media."""
    namespaces = {
        'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
        'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    }
    relationship_id = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
    chunks = []
    remaining = 40 * 1024 * 1024
    with zipfile.ZipFile(path) as archive:
        def read_xml(member):
            nonlocal remaining
            remaining -= archive.getinfo(member).file_size
            if remaining < 0:
                raise ValueError('PPTX XML exceeds the 40 MB expanded reading limit.')
            return ET.fromstring(archive.read(member))

        presentation = read_xml('ppt/presentation.xml')
        relationships = read_xml('ppt/_rels/presentation.xml.rels')
        slides = {
            item.attrib['Id']: item.attrib['Target'] for item in relationships
            if item.get('Type', '').endswith('/slide') and item.get('TargetMode') != 'External'
        }
        for number, slide in enumerate(presentation.findall('p:sldIdLst/p:sldId', namespaces), 1):
            target = slides[slide.attrib[relationship_id]]
            member = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'ppt/' + target)
            if not member.startswith('ppt/slides/'):
                raise ValueError('PPTX contains an invalid slide path.')
            root = read_xml(member)
            paragraphs = [''.join(run.text or '' for run in paragraph.findall('.//a:t', namespaces))
                          for paragraph in root.findall('.//a:p', namespaces)]
            chunks.append((f'Slide {number}', '\n'.join(text for text in paragraphs if text.strip())))
    return chunks


def intake(project, storage_root, filename, content, role):
    if role not in ROLES:
        raise ValueError('Select a supported document role.')
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('Upload a nonempty file up to 15 MB.')
    name = Path(filename.replace('\\', '/')).name
    if Path(name).suffix.lower() not in ('.txt', '.pdf', '.docx'):
        raise ValueError('Only .txt, .pdf and .docx are accepted in this milestone.')
    version = hashlib.sha256(content).hexdigest()
    # Same filename and role identify a document lineage; changed bytes create a retained version.
    prior = next((d for d in project.documents if d.name == name and d.role == role), None)
    document_id = prior.id if prior else 'D-' + uuid4().hex
    if any(d.id == document_id and d.version == version for d in project.documents):
        raise ValueError('This exact document version is already present.')
    path = storage_root / 'originals' / project.id / document_id / (version + Path(name).suffix.lower())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    doc = Document(document_id, name, role, 'Stored', version, original_path=str(path.relative_to(storage_root)))
    passages = []
    if role == 'template':
        doc.status = 'Stored only — template rendering not implemented'
    else:
        try:
            passages = LocalProcessor().extract(path, document_id)
            doc.status = 'Extracted — inspect completeness manually'
        except ValueError as error:
            doc.status = 'Failed — excluded from extraction'
            doc.error = str(error)
        except Exception:
            doc.status = 'Failed — excluded from extraction'
            doc.error = 'Cannot extract this file. Check format, UTF-8 encoding, encryption, empty/scanned pages or file corruption. OCR is unavailable.'
    project.documents.append(doc)
    project.passages.extend(passages)
    for requirement in project.requirements:
        requirement.reviewed = False
    invalidate(project)
    return doc
