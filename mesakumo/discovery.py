"""Local-only tender discovery. Never imports a model adapter."""
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import asdict
from pathlib import Path
from .domain import Document, Project, SourcePassage
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

def scan(pack, phase):
    """Return actual files plus applicable ZIP members; keep aliases visible."""
    roles = inventory()
    rows = []
    def add(name, content=None, error=''):
        role = roles.get(unicodedata.normalize('NFC', pack.name + '/' + name), 'Unclassified — inspect manually')
        if name.endswith('Leistungsverzeichnis.aidf'): role = 'Buyer submission form'
        if name.endswith('Leistungsverzeichnis.pdf'): role = 'Readable schedule'
        if name == 'Bearbeitungshinweis.txt': role = 'Internal hackathon guidance'
        rows.append(dict(path=name, role=role, content=content, error=error, inventory='Listed' if unicodedata.normalize('NFC', pack.name + '/' + name) in roles else 'Actual file absent from CSV'))
    for path in sorted(pack.rglob('*')):
        if not path.is_file() or path.is_symlink() or any(s.startswith('.') for s in path.relative_to(pack).parts): continue
        name = path.relative_to(pack).as_posix()
        try:
            if path.stat().st_size > MAX_FILE_BYTES: raise ValueError('File exceeds 15 MB local parsing limit')
            content = path.read_bytes()
            add(name, content)
            if path.suffix.lower() == '.zip' and phase == 'offer':
                try:
                    with zipfile.ZipFile(path) as archive:
                        if sum(i.file_size for i in archive.infolist()) > 40 * 1024 * 1024: raise ValueError('ZIP expanded size exceeds 40 MB')
                        for member in sorted(archive.infolist(), key=lambda i: i.filename):
                            if not member.is_dir(): add(name + '!/' + member.filename, archive.read(member))
                except Exception as exc: rows[-1]['error'] = 'Unreadable ZIP: ' + type(exc).__name__
        except Exception as exc: add(name, error=str(exc))
    return rows

PARSED_FORMATS = ('.txt', '.pdf', '.docx', '.xlsx', '.aidf')
_PARSED = {}          # In-process cache: new browser sessions reuse parses while the server runs.
_PARSED_LIMIT = 2000  # Entries (one per document version); a full tender pack is about 20.

def _cache_key(doc_id, version, suffix):
    # The passage IDs depend on the document ID and content hash, so this key fully
    # determines the parse result; a new PARSER_VERSION invalidates every entry.
    return hashlib.sha256(f'{PARSER_VERSION}|{doc_id}|{version}|{suffix}'.encode()).hexdigest()

def _cached_parse(key, cache_dir):
    if key in _PARSED:
        return _PARSED[key]
    if cache_dir is None:
        return None
    try:
        hit = json.loads((Path(cache_dir) / f'{key}.json').read_text(encoding='utf-8'))
        hit['passages'] = [SourcePassage(**p) for p in hit['passages']]
    except (OSError, ValueError, TypeError, KeyError):
        return None  # Missing or damaged entries are simply parsed again.
    return _remember(key, hit)

def _remember(key, result):
    if len(_PARSED) >= _PARSED_LIMIT:
        _PARSED.pop(next(iter(_PARSED)))
    _PARSED[key] = result
    return result

def _parse(key, doc_id, suffix, content, cache_dir):
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ('source' + suffix)
            path.write_bytes(content)
            result = dict(status='Read — inspect completeness manually', error='',
                          passages=LocalProcessor().extract(path, doc_id))
    except Exception as exc:
        result = dict(status='Unreadable — excluded', passages=[],
                      error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
    if cache_dir is not None:
        try:  # Best effort: write to a temporary name, then rename, so readers never see half a file.
            folder = Path(cache_dir)
            folder.mkdir(parents=True, exist_ok=True)
            payload = {**result, 'passages': [asdict(p) for p in result['passages']]}
            temp = folder / f'{key}.{os.getpid()}.tmp'
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            os.replace(temp, folder / f'{key}.json')
        except OSError:
            pass
    return _remember(key, result)

def open_selection(store, pack, phase, *, cache_dir=None, on_progress=None):
    """Open (or create) the project for a tender pack and read any new or changed documents.

    cache_dir: optional folder for parse results shared across workspaces and restarts.
    on_progress(done, total, name): called before each document that has to be parsed,
    and once more with done == total when reading is finished.
    """
    if phase not in ('participation', 'offer', 'single'): raise ValueError('Unknown phase')
    project_id = 'local-' + identity(pack.name + '/' + phase)
    try: project = store.load(project_id)
    except ValueError:
        project = Project(project_id, pack.name + ' · ' + phase, False, 'Local tender discovery: ' + phase)
        store.create(project)
    revision = project.revision
    rows = scan(pack, phase)
    # Count documents that really need parsing first, so progress can show "n of total".
    known = {(d.id, d.version) for d in project.documents if d.parser_version == PARSER_VERSION}
    pending, versions = [], set()
    for row in rows:
        suffix = Path(row['path']).suffix.lower()
        if row['content'] is None or row['error'] or suffix == '.zip' or suffix not in PARSED_FORMATS:
            continue
        version = hashlib.sha256(row['content']).hexdigest()
        doc_id = 'S-' + identity(project_id + '/' + row['path'])
        if version in versions:
            continue  # Identical alias, counted once below.
        versions.add(version)
        if (doc_id, version) not in known and _cached_parse(_cache_key(doc_id, version, suffix), cache_dir) is None:
            pending.append(row['path'])
    done = 0
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
            elif suffix not in PARSED_FORMATS:
                doc.status = 'Unsupported format — not read'
            else:
                key = _cache_key(doc_id, version, suffix)
                result = _cached_parse(key, cache_dir)
                if result is None:
                    if on_progress:
                        on_progress(done, len(pending), row['path'])
                    result = _parse(key, doc_id, suffix, content, cache_dir)
                    done += 1
                doc.status, doc.error = result['status'], result['error']
                project.passages.extend(SourcePassage(**asdict(p)) for p in result['passages'])
            project.documents.append(doc)
            changed = True
        row.update(status=doc.status, error=doc.error)
    if on_progress and pending:
        on_progress(len(pending), len(pending), '')
    if changed:
        invalidate(project)
        store.save(project, revision)
    return project, rows
