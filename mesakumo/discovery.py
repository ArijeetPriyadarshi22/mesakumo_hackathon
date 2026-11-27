"""Local-only tender discovery. Never imports a model adapter."""
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from .domain import Document, Project
from .documents import LocalProcessor, MAX_FILE_BYTES
from .workflow import invalidate

ROOT = Path(__file__).resolve().parent.parent
PARSER_VERSION = 'formats-v2'

def participant_directory():
    configured = os.environ.get('MESAKUMO_PARTICIPANT_DIR')
    return Path(configured).expanduser().resolve() if configured else next(ROOT.glob('mesakumo_Input*'), ROOT / 'private_documents')

def packs(directory):
    return sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink() and re.match(r'^Ausschreibung\s+\d+(?:\s|$)', p.name)) if directory.is_dir() else []

def identity(value):
    return hashlib.sha256(unicodedata.normalize('NFC', value).encode()).hexdigest()[:24]

def inventory():
    with (ROOT / 'Mesakumo_Document_Inventory.csv').open(encoding='utf-8-sig', newline='') as f:
        return {unicodedata.normalize('NFC', r['Path relative to participant input folder'].replace(' / ', '!/')): r['Primary role'] for r in csv.DictReader(f)}

PHASE_FILE = '.tender-phases.json'


def offer_archives(pack):
    marker = pack / PHASE_FILE
    if marker.is_symlink():
        raise ValueError('The tender phase declaration must be a local file.')
    if marker.is_file():
        try:
            config = json.loads(marker.read_text(encoding='utf-8'))
            archives = config['offer_archives']
            if (not isinstance(archives, list) or any(
                not isinstance(name, str) or not name.lower().endswith('.zip') or
                Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name
                for name in archives
            )):
                raise ValueError
            return set(archives)
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError(f'Invalid {PHASE_FILE} in {pack.name}. Specify relative ZIP paths in offer_archives.') from None
    return {
        path.relative_to(pack).as_posix() for path in pack.rglob('*')
        if path.is_file() and not path.is_symlink()
        and not any(part.startswith('.') for part in path.relative_to(pack).parts)
        and unicodedata.normalize('NFC', path.name).casefold() in ('unterlage für die angebotsphase.zip', 'offer.zip')
    }


def scan(pack, phase, *, phase_archives=None, capture=True, check_active=None):
    """Return actual files plus applicable ZIP members; keep aliases visible."""
    roles = inventory()
    archive_policy = phase_archives is not None
    rows = []
    def add(name, content=None, error='', version=None):
        role = roles.get(unicodedata.normalize('NFC', pack.name + '/' + name), 'Unclassified — inspect manually')
        if name.endswith('Leistungsverzeichnis.aidf'): role = 'Buyer submission form'
        if name.endswith('Leistungsverzeichnis.pdf'): role = 'Readable schedule'
        if name == 'Bearbeitungshinweis.txt': role = 'Internal hackathon guidance'
        if not capture:
            version = hashlib.sha256(content).hexdigest() if content is not None else version
            content = None
        rows.append(dict(path=name, role=role, content=content, error=error, version=version, inventory='Listed' if unicodedata.normalize('NFC', pack.name + '/' + name) in roles else 'Actual file absent from CSV'))
    for path in sorted(pack.rglob('*')):
        if check_active is not None:
            check_active()
        if not path.is_file() or path.is_symlink() or any(s.startswith('.') for s in path.relative_to(pack).parts): continue
        name = path.relative_to(pack).as_posix()
        if archive_policy and name in phase_archives and phase != 'offer':
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES: raise ValueError('File exceeds 15 MB local parsing limit')
            expand = path.suffix.lower() == '.zip' and (archive_policy or phase == 'offer')
            if not capture and not expand:
                with path.open('rb') as handle:
                    add(name, version=hashlib.file_digest(handle, 'sha256').hexdigest())
                continue
            content = path.read_bytes()
            add(name, content)
            if expand:
                container = rows[-1]
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as archive:
                        if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024: raise ValueError('ZIP expanded size exceeds 40 MB')
                        for member in sorted(archive.infolist(), key=lambda i: i.filename):
                            if not member.is_dir(): add(name + '!/' + member.filename, archive.read(member))
                except Exception as exc: container['error'] = 'Unreadable ZIP: ' + type(exc).__name__
        except Exception as exc: add(name, error=str(exc))
    return rows

def open_selection(store, pack, phase):
    if phase not in ('participation', 'offer', 'single'): raise ValueError('Unknown phase')
    project_id = 'local-' + identity(pack.name + '/' + phase)
    try: project = store.load(project_id)
    except ValueError:
        project = Project(project_id, pack.name + ' · ' + phase, False, 'Local tender discovery: ' + phase)
        store.create(project)
    revision = project.revision
    rows = scan(pack, phase)
    seen = {}
    changed = False
    for row in rows:
        content = row.pop('content')
        version = hashlib.sha256(content).hexdigest() if content is not None else 'unreadable'
        suffix = Path(row['path']).suffix.lower()
        if suffix == '.zip':
            row.update(status='Archive — members included in offer phase' if phase == 'offer' else 'Archive — reserved for offer phase', source_id='')
            continue
        if content is not None and version in seen:
            row.update(status='Identical source alias — counted once', source_id=seen[version])
            continue
        doc_id = 'S-' + identity(project_id + '/' + row['path'])
        seen[version] = doc_id
        row['source_id'] = doc_id
        doc = next((d for d in project.documents if d.id == doc_id and d.version == version), None)
        if doc is not None and doc.parser_version != PARSER_VERSION:
            project.passages = [p for p in project.passages if not (p.document_id == doc_id and p.document_version == version)]
            project.documents.remove(doc)
            doc = None
        if doc is None:
            doc = Document(doc_id, row['path'], 'knowledge' if row['role'] == 'Internal hackathon guidance' else 'tender', version=version)
            doc.parser_version = PARSER_VERSION
            if row['error']:
                doc.status, doc.error = 'Unreadable', row['error']
            elif suffix not in ('.txt', '.pdf', '.docx', '.xlsx', '.aidf'):
                doc.status = 'Unsupported format — not read'
            else:
                try:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp) / ('source' + suffix)
                        path.write_bytes(content)
                        project.passages.extend(LocalProcessor().extract(path, doc_id))
                    doc.status = 'Read — inspect completeness manually'
                except Exception as exc:
                    doc.status, doc.error = 'Unreadable — excluded', str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            project.documents.append(doc)
            changed = True
        row.update(status=doc.status, error=doc.error)
    if changed:
        invalidate(project)
        store.save(project, revision)
    return project, rows
