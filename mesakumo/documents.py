"""Future format-specific extraction boundary."""
from pathlib import Path
from typing import Protocol
from .domain import SourcePassage

class DocumentProcessor(Protocol):
    def extract(self, path: Path, document_id: str) -> list[SourcePassage]: ...

class UnconfiguredProcessor:
    def extract(self, path: Path, document_id: str) -> list[SourcePassage]:
        raise NotImplementedError('Document processing is not implemented. No file was analysed.')

import hashlib
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from uuid import uuid4
from .domain import Document
from .workflow import invalidate

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_PPTX_FILE_BYTES = 30 * 1024 * 1024
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
ROLES = ('tender', 'knowledge', 'text_module', 'template')

PPTX_NS = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel': 'http://schemas.openxmlformats.org/package/2006/relationships',
}

def _slide_part(relationship, number):
    error = f'PowerPoint slide reference at position {number} is invalid.'
    if (relationship is None
            or relationship.get('Type') != PPTX_NS['r'] + '/slide'
            or relationship.get('TargetMode', 'Internal') != 'Internal'):
        raise ValueError(error)
    target = urlsplit(relationship.get('Target', ''))
    if (target.scheme or target.netloc or target.query or target.fragment
            or not target.path or '\\' in target.path):
        raise ValueError(error)
    if target.path.startswith('/'):
        part = posixpath.normpath(target.path[1:])
    else:
        part = posixpath.normpath(posixpath.join('ppt', target.path))
    if part in ('.', '..') or part.startswith(('../', '/')):
        raise ValueError(error)
    return part

def _slide_text(slide):
    drawing = '{' + PPTX_NS['a'] + '}'
    paragraphs = []
    for paragraph in slide.iterfind('p:cSld/p:spTree//a:p', PPTX_NS):
        runs = []
        for node in paragraph:
            if node.tag == drawing + 'br':
                runs.append(' ')
            elif node.tag in (drawing + 'r', drawing + 'fld'):
                runs.append(node.findtext('a:t', default='', namespaces=PPTX_NS))
        text = ' '.join(''.join(runs).split())
        if text:
            paragraphs.append(text)
    return '\n'.join(paragraphs)

def _pptx_chunks(path):
    chunks = []
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise ValueError('PowerPoint expanded size exceeds 40 MB.')
        presentation = ET.fromstring(archive.read('ppt/presentation.xml'))
        relationships = ET.fromstring(archive.read('ppt/_rels/presentation.xml.rels'))
        if (presentation.tag != '{' + PPTX_NS['p'] + '}presentation'
                or relationships.tag != '{' + PPTX_NS['rel'] + '}Relationships'):
            raise ValueError('PowerPoint presentation or relationships XML has an invalid root.')
        by_id = {}
        for relationship in relationships.findall('rel:Relationship', PPTX_NS):
            identifier = relationship.get('Id')
            if not identifier or identifier in by_id:
                raise ValueError('PowerPoint contains an invalid relationship ID.')
            by_id[identifier] = relationship
        seen_parts = set()
        references = presentation.iterfind('p:sldIdLst/p:sldId', PPTX_NS)
        for number, reference in enumerate(references, 1):
            identifier = reference.get('{' + PPTX_NS['r'] + '}id')
            part = _slide_part(by_id.get(identifier), number)
            if part in seen_parts:
                raise ValueError(f'PowerPoint repeats a slide reference at position {number}.')
            seen_parts.add(part)
            try:
                slide = ET.fromstring(archive.read(part))
            except KeyError:
                raise ValueError(f'PowerPoint slide {number} is missing from the archive.') from None
            if slide.tag != '{' + PPTX_NS['p'] + '}sld':
                raise ValueError(f'PowerPoint slide {number} has an invalid slide part.')
            chunks.append((f'Slide {number}', _slide_text(slide)))
    return chunks

class LocalProcessor:
    def extract(self, path, document_id, *, unreadable_pdf_pages=None):
        suffix = path.suffix.lower()
        version = hashlib.sha256(path.read_bytes()).hexdigest()
        chunks = []
        if suffix == '.txt':
            lines = path.read_text(encoding='utf-8-sig').splitlines()
            for start in range(0, len(lines), 20):
                chunks.append((f'Lines {start+1}–{min(start+20, len(lines))}', '\n'.join(lines[start:start+20])))
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise ValueError('Encrypted PDF: supply an unencrypted copy.')
            for index, page in enumerate(reader.pages, 1):
                try:
                    text = page.extract_text() or ''
                except NotImplementedError:
                    # An unsupported page encoding need not invalidate other historical pages.
                    if unreadable_pdf_pages is None:
                        raise
                    text = ''
                if not text.strip() and unreadable_pdf_pages is not None:
                    unreadable_pdf_pages.append(index)
                elif not text.strip():
                    raise ValueError(f'PDF page {index} has no extractable text; possible scan. OCR is not supported. No pages included.')
                chunks.append((f'Page {index}', text))
        elif suffix == '.docx':
            with zipfile.ZipFile(path) as archive:
                if sum(i.file_size for i in archive.infolist()) > MAX_EXPANDED_BYTES:
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
        elif suffix == '.pptx':
            chunks = _pptx_chunks(path)
        else:
            raise ValueError('Supported extraction formats: UTF-8 TXT, text PDF, DOCX and PPTX.')
        passages = [SourcePassage(f'{document_id}-{version[:12]}-P{i}', document_id, location, text, version)
                    for i, (location, text) in enumerate(chunks, 1) if text.strip()]
        if not passages:
            raise ValueError('No readable text found. Empty documents and scans are not analysed.')
        return passages

def intake(project, storage_root, filename, content, role, *, unreadable_pdf_pages=None):
    if role not in ROLES:
        raise ValueError('Select a supported document role.')
    name = Path(filename.replace('\\', '/')).name
    suffix = Path(name).suffix.lower()
    max_bytes = MAX_PPTX_FILE_BYTES if suffix == '.pptx' else MAX_FILE_BYTES
    if not content or len(content) > max_bytes:
        raise ValueError(f'Upload a nonempty file up to {max_bytes // (1024 * 1024)} MB.')
    if suffix not in ('.txt', '.pdf', '.docx', '.pptx'):
        raise ValueError('Only .txt, .pdf, .docx and .pptx are accepted in this milestone.')
    version = hashlib.sha256(content).hexdigest()
    # Same filename and role identify a document lineage; changed bytes create a retained version.
    prior = next((d for d in project.documents if d.name == name and d.role == role), None)
    document_id = prior.id if prior else 'D-' + uuid4().hex
    if any(d.id == document_id and d.version == version for d in project.documents):
        raise ValueError('This exact document version is already present.')
    path = storage_root / 'originals' / project.id / document_id / (version + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    doc = Document(document_id, name, role, 'Stored', version, original_path=str(path.relative_to(storage_root)))
    passages = []
    if role == 'template':
        doc.status = 'Stored only — template rendering not implemented'
    else:
        try:
            passages = LocalProcessor().extract(
                path, document_id, unreadable_pdf_pages=unreadable_pdf_pages if role == 'knowledge' else None,
            )
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
