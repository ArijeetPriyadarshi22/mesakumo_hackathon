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
import zipfile
import xml.etree.ElementTree as ET
from uuid import uuid4
from .domain import Document
from .workflow import invalidate

MAX_FILE_BYTES = 15 * 1024 * 1024
ROLES = ('tender', 'knowledge', 'text_module', 'template')

class LocalProcessor:
    def extract(self, path, document_id):
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
                text = page.extract_text() or ''
                if not text.strip():
                    raise ValueError(f'PDF page {index} has no extractable text; possible scan. OCR is not supported. No pages included.')
                chunks.append((f'Page {index}', text))
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
        else:
            raise ValueError('Supported extraction formats: UTF-8 TXT, text PDF and DOCX.')
        passages = [SourcePassage(f'{document_id}-{version[:12]}-P{i}', document_id, location, text, version)
                    for i, (location, text) in enumerate(chunks, 1) if text.strip()]
        if not passages:
            raise ValueError('No readable text found. Empty documents and scans are not analysed.')
        return passages

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
