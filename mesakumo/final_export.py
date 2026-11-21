"""Persist and approve the exact PowerPoint preview for a current proposal package."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .evidence import resolve_evidence_path
from .persistence import ConflictError
from .pptx_export import MAX_TEMPLATE_BYTES, render_proposal
from .proposal import open_items

TEMPLATE_NAME = 'Angebotstemplate.pptx'
PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
RENDERER_VERSION = 1


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def read_template(directory):
    try:
        path = resolve_evidence_path(directory, TEMPLATE_NAME)
        with path.open('rb') as template:
            content = template.read(MAX_TEMPLATE_BYTES + 1)
    except FileNotFoundError as exc:
        raise ValueError(f'Corporate template {TEMPLATE_NAME} was not found in the participant folder.') from exc
    if not content or len(content) > MAX_TEMPLATE_BYTES:
        raise ValueError('The corporate template is empty or exceeds the supported size limit.')
    return content


def final_blockers(package, proposal_rows):
    """Treat unresolved included items and omitted required or unclassified rows as blocking."""
    blockers = []
    for section in package['sections']:
        pending = dict.fromkeys([*section['open_items'], *open_items(section['text'])])
        blockers.extend(f"{section['requirement_id']} · {item}" for item in pending)
    for item in package['excluded']:
        rid = item['requirement_id']
        obligation = proposal_rows.get(rid, {}).get('requirement', {}).get('obligation_level')
        if obligation not in ('Optional', 'Informational'):
            blockers.append(f"{rid} · Excluded required or unclassified requirement: {item['requirement']}")
    return blockers


class FinalExports:
    def __init__(self, drafts, package, directory):
        self.drafts = drafts
        self.store = drafts.store
        self.package = package
        self.directory = directory
        self.template = read_template(directory)
        self.template_sha = _digest(self.template)
        package_sha = _digest(json.dumps(package, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        self.key = (*drafts.key, package['revision'], package_sha, self.template_sha, RENDERER_VERSION)
        with self.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS final_exports (
                project_id TEXT, checklist_revision INTEGER, evidence_revision INTEGER, package_revision INTEGER,
                package_hash TEXT, template_hash TEXT, renderer_version INTEGER,
                artifact BLOB NOT NULL, checksum TEXT NOT NULL, reviewer TEXT, approved_at TEXT,
                PRIMARY KEY (project_id, checklist_revision, evidence_revision, package_revision,
                             package_hash, template_hash, renderer_version))''')

    def _verify_inputs(self, db):
        package, current = self.drafts.latest_package(db=db)
        if not current or package != self.package:
            raise ConflictError('The proposal package changed. Return to Proposal Draft and reopen Final Review.')
        if _digest(read_template(self.directory)) != self.template_sha:
            raise ConflictError('The corporate template changed. Generate a new preview before approving.')

    def _load(self, db):
        row = db.execute('''SELECT artifact, checksum, reviewer, approved_at FROM final_exports
            WHERE project_id=? AND checklist_revision=? AND evidence_revision=? AND package_revision=?
            AND package_hash=? AND template_hash=? AND renderer_version=?''', self.key).fetchone()
        if not row:
            return None
        data = bytes(row[0])
        if not data or _digest(data) != row[1]:
            raise ValueError('The saved PowerPoint artifact failed its integrity check.')
        return dict(data=data, checksum=row[1], reviewer=row[2], approved_at=row[3])

    def current(self):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._verify_inputs(db)
            return self._load(db)

    def preview(self):
        existing = self.current()
        if existing:
            return existing
        data = render_proposal(self.package, self.template)
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._verify_inputs(db)
                db.execute('INSERT OR IGNORE INTO final_exports VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)',
                           (*self.key, data, _digest(data)))
                return self._load(db)
        except sqlite3.Error as exc:
            raise ValueError('The preview could not be saved. Generate it again to retry.') from exc

    def approve(self, reviewer, expected_checksum):
        if not reviewer.strip():
            raise ValueError('Enter the final reviewer name.')
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._verify_inputs(db)
                if final_blockers(self.package, self.drafts.rows):
                    raise ValueError('Resolve the blocking items in Proposal Draft before approving the final file.')
                preview = self._load(db)
                if preview is None or preview['checksum'] != expected_checksum:
                    raise ConflictError('The preview changed or is missing. Review the current rendered file first.')
                if preview['reviewer'] is None:
                    db.execute('''UPDATE final_exports SET reviewer=?, approved_at=?
                        WHERE project_id=? AND checklist_revision=? AND evidence_revision=? AND package_revision=?
                        AND package_hash=? AND template_hash=? AND renderer_version=? AND checksum=?''',
                        (reviewer.strip(), datetime.now(timezone.utc).isoformat(), *self.key, expected_checksum))
                return self._load(db)
        except sqlite3.Error as exc:
            raise ValueError('Final approval could not be saved. Please retry.') from exc
