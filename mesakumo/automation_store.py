"""Persistent queue, project revisions, and reusable work units."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .discovery import PHASE_FILE, identity, offer_archives, packs
from .persistence import ConflictError, ProjectStore

ACTIVE_JOB_STATUSES = ('queued', 'running', 'stopping')


class JobStopped(Exception):
    """The worker still owns the job, but the user requested a stop."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def workspace_path():
    if path := os.environ.get('MESAKUMO_AUTOMATION_DIR'):
        return Path(path).expanduser().resolve() / 'workspace.sqlite3'
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'TenderWorkbench' / 'workspace.sqlite3'


def discover(root):
    if not root.is_dir():
        return
    declared = [folder for folder in root.iterdir() if folder.is_dir() and not folder.is_symlink()
                and not folder.name.startswith('.') and (folder / PHASE_FILE).is_file()]
    for folder in sorted(set(packs(root)) | set(declared)):
        has_offer = bool(offer_archives(folder))
        for phase in ('participation', 'offer') if has_offer else ('single',):
            key = 'project-' + identity(canonical([str(root.resolve()), folder.name, phase]))
            yield key, folder, phase


class AutomationStore(ProjectStore):
    def __init__(self, path, lease=120):
        super().__init__(path)
        self.lease = lease
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS automation_projects (
                    id TEXT PRIMARY KEY, folder TEXT NOT NULL, phase TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, input_key TEXT,
                    snapshot TEXT NOT NULL DEFAULT '{}', deck BLOB, checksum TEXT,
                    approval TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    project TEXT PRIMARY KEY, status TEXT NOT NULL, action TEXT NOT NULL,
                    token TEXT, lease_until REAL, stage TEXT, done INTEGER, total INTEGER,
                    diagnostic TEXT, updated REAL NOT NULL, request_id TEXT,
                    workflow_stage TEXT NOT NULL DEFAULT 'requirements'
                );
                CREATE TABLE IF NOT EXISTS automation_history (
                    project TEXT, revision INTEGER, input_key TEXT, snapshot TEXT,
                    deck BLOB, checksum TEXT, approval TEXT, created REAL,
                    PRIMARY KEY(project, revision)
                );
                CREATE TABLE IF NOT EXISTS automation_units (
                    project TEXT, stage TEXT, key TEXT, value TEXT, created REAL,
                    PRIMARY KEY(project, stage, key)
                );
                CREATE TABLE IF NOT EXISTS model_calls (
                    id INTEGER PRIMARY KEY, project TEXT, model TEXT, called REAL,
                    quota_bucket TEXT, estimated INTEGER, input INTEGER, output INTEGER, thinking INTEGER
                );
                CREATE INDEX IF NOT EXISTS model_quota_lookup ON model_calls (quota_bucket, called);
                CREATE TABLE IF NOT EXISTS review_outbox (
                    project TEXT, episode TEXT, payload TEXT, state TEXT, attempts INTEGER DEFAULT 0,
                    due REAL, token TEXT, diagnostic TEXT, PRIMARY KEY(project, episode)
                );
            """)
        with self.write() as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'request_id' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN request_id TEXT')
                db.execute('UPDATE jobs SET request_id=lower(hex(randomblob(16)))')
            if 'workflow_stage' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN workflow_stage TEXT NOT NULL DEFAULT 'requirements'")
                db.execute("""
                    UPDATE jobs SET workflow_stage=CASE
                        WHEN action='render' OR stage='Rendering PowerPoint' THEN 'review'
                        WHEN stage='Drafting chapters' THEN 'proposal'
                        WHEN stage IN ('Indexing evidence','Evidence') THEN 'evidence'
                        ELSE 'requirements' END
                """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    @contextmanager
    def write(self):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def setting(self, name, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE name=?', (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, name, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (name, canonical(value)))

    def register(self, root):
        entries = list(discover(root))
        with self.connect() as db:
            db.executemany('INSERT OR IGNORE INTO automation_projects(id,folder,phase) VALUES (?,?,?)',
                           [(key, str(folder), phase) for key, folder, phase in entries])
        return [key for key, _, _ in entries]

    @staticmethod
    def decode(row):
        if row is None:
            raise ValueError('Project was not found in this workspace.')
        result = dict(row)
        result['snapshot'] = json.loads(result['snapshot'])
        result['approval'] = json.loads(result['approval']) if result['approval'] else None
        return result

    def project(self, key, *, db=None):
        if db is None:
            with self.connect() as connection:
                return self.project(key, db=connection)
        return self.decode(db.execute('SELECT * FROM automation_projects WHERE id=?', (key,)).fetchone())

    def projects(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT p.id, p.folder, p.phase, p.revision, json_extract(p.snapshot, '$.facts.deadline.value') deadline,
                       j.status, j.stage, j.workflow_stage, j.done, j.total, j.diagnostic, j.request_id,
                       j.action AS job_action,
                       json_array_length(p.snapshot, '$.manifest') materials,
                       json_array_length(p.snapshot, '$.reading_gaps') gaps, p.checksum
                FROM automation_projects p LEFT JOIN jobs j ON p.id=j.project ORDER BY p.folder, p.phase
            """)]

    def history(self, key):
        with self.connect() as db:
            return [dict(row) for row in db.execute("""
                SELECT revision, checksum, approval, created FROM automation_history
                WHERE project=? ORDER BY revision DESC
            """, (key,))]

    def metrics(self, key):
        with self.connect() as db:
            return dict(db.execute("""
                SELECT count(*) calls, coalesce(sum(input),0) input,
                       coalesce(sum(output),0) output, coalesce(sum(thinking),0) thinking
                FROM model_calls WHERE project=?
            """, (key,)).fetchone())

    def enqueue(self, keys, action='reuse'):
        stage = 'review' if action == 'render' else 'requirements'
        with self.write() as db:
            for key in dict.fromkeys(keys):
                self.project(key, db=db)
                db.execute("""
                    INSERT INTO jobs(project,status,action,updated,request_id,workflow_stage) VALUES (?,'queued',?,?,?,?)
                    ON CONFLICT(project) DO UPDATE SET status='queued', action=excluded.action,
                        token=NULL, lease_until=NULL, stage=NULL, done=0, total=0, diagnostic=NULL,
                        updated=excluded.updated, request_id=excluded.request_id, workflow_stage=excluded.workflow_stage
                    WHERE jobs.status NOT IN ('queued','running','stopping')
                """, (key, action, time.time(), uuid4().hex, stage))

    def request_stop(self, requests):
        with self.write() as db:
            db.executemany("""
                UPDATE jobs SET status=CASE status WHEN 'queued' THEN 'stopped' ELSE 'stopping' END,
                    diagnostic=NULL, updated=?
                WHERE project=? AND request_id=? AND status IN ('queued','running')
            """, [(time.time(), key, request_id) for key, request_id in requests])

    def claim(self):
        now, token = time.time(), uuid4().hex
        with self.write() as db:
            db.execute("""
                UPDATE jobs SET status='stopped', token=NULL, lease_until=NULL, updated=?
                WHERE status='stopping' AND lease_until<=?
            """, (now, now))
            row = db.execute("""
                SELECT project,action FROM jobs
                WHERE status='queued' OR (status='running' AND lease_until<=?)
                ORDER BY updated,project LIMIT 1
            """, (now,)).fetchone()
            if row is None:
                return None
            db.execute("""
                UPDATE jobs SET status='running',token=?,lease_until=?,updated=?,stage=NULL,done=0,total=0,
                    workflow_stage=CASE action WHEN 'render' THEN 'review' ELSE 'requirements' END
                WHERE project=?
            """,
                       (token, now + self.lease, now, row['project']))
        return row['project'], row['action'], token

    def assert_claim(self, db, key, token):
        row = db.execute("SELECT status,token,lease_until FROM jobs WHERE project=?", (key,)).fetchone()
        if (row is None or row['status'] not in ('running', 'stopping') or row['token'] != token
                or row['lease_until'] <= time.time()):
            raise ConflictError('This job lease has expired or moved to another worker.')
        if row['status'] == 'stopping':
            raise JobStopped

    def progress(self, key, token, stage, done=0, total=0, *, workflow_stage=None):
        with self.write() as db:
            self.assert_claim(db, key, token)
            now = time.time()
            db.execute("""
                UPDATE jobs SET stage=?,done=?,total=?,lease_until=?,updated=?,
                    workflow_stage=coalesce(?,workflow_stage) WHERE project=?
            """, (stage, done, total, now + self.lease, now, workflow_stage, key))

    def heartbeat(self, key, token):
        now = time.time()
        with self.connect() as db:
            return db.execute("""
                UPDATE jobs SET lease_until=? WHERE project=? AND token=?
                    AND status IN ('running','stopping') AND lease_until>?
            """, (now + self.lease, key, token, now)).rowcount == 1

    def finish(self, key, token, status, diagnostic=None, *, db=None):
        if db is None:
            with self.write() as connection:
                self.finish(key, token, status, diagnostic, db=connection)
            return
        try:
            self.assert_claim(db, key, token)
        except JobStopped:
            status, diagnostic = 'stopped', None
        db.execute('UPDATE jobs SET status=?,diagnostic=?,token=NULL,lease_until=NULL,updated=? WHERE project=?',
                   (status, diagnostic, time.time(), key))

    def cached(self, project, stage, key):
        with self.connect() as db:
            row = db.execute('SELECT value FROM automation_units WHERE project=? AND stage=? AND key=?',
                             (project, stage, key)).fetchone()
        return json.loads(row[0]) if row else None

    def save_unit(self, project, stage, key, value, token):
        with self.write() as db:
            if token is not None:
                self.assert_claim(db, project, token)
            db.execute('INSERT OR IGNORE INTO automation_units VALUES (?,?,?,?,?)',
                       (project, stage, key, canonical(value), time.time()))

    def save_revision(self, key, expected, snapshot, input_key, deck=None, *, token=None, notice=None, job_result=None):
        checksum = hashlib.sha256(deck).hexdigest() if deck else None
        with self.write() as db:
            if token is not None:
                self.assert_claim(db, key, token)
            row = self.project(key, db=db)
            if row['revision'] != expected:
                raise ConflictError('Project changed. Reload before saving.')
            revision = expected + 1
            payload = canonical(snapshot)
            db.execute("""
                UPDATE automation_projects SET revision=?,input_key=?,snapshot=?,deck=?,checksum=?,approval=NULL WHERE id=?
            """, (revision, input_key, payload, deck, checksum, key))
            db.execute('INSERT INTO automation_history VALUES (?,?,?,?,?,?,NULL,?)',
                       (key, revision, input_key, payload, deck, checksum, time.time()))
            if notice is not None and deck:
                db.execute("""
                    INSERT INTO review_outbox(project,episode,payload,state,due)
                    VALUES (?, ?, ?, 'queued', ?)
                    ON CONFLICT(project,episode) DO UPDATE SET payload=excluded.payload
                    WHERE review_outbox.state IN ('queued', 'retry')
                """, (key, notice['episode'], canonical(notice), time.time()))
            if job_result is not None:
                self.finish(key, token, *job_result, db=db)
        return revision

    def approve(self, key, revision, checksum, reviewer, *, template_hash):
        if not reviewer.strip():
            raise ValueError('Enter the reviewer name.')
        with self.write() as db:
            current = self.project(key, db=db)
            if (not checksum or current['revision'] != revision or current['checksum'] != checksum
                    or current['snapshot'].get('template_hash') != template_hash
                    or hashlib.sha256(current['deck'] or b'').hexdigest() != checksum):
                raise ConflictError('The deck has changed. Regenerate or reload before approving.')
            running = db.execute("SELECT status FROM jobs WHERE project=?", (key,)).fetchone()
            if running and running['status'] in ACTIVE_JOB_STATUSES:
                raise ConflictError('Wait for preparation to finish and review the current deck.')
            if current['approval']:
                return current['approval']
            approval = {'reviewer': reviewer.strip(), 'checksum': checksum, 'time': time.time(), 'revision': revision}
            value = canonical(approval)
            db.execute('UPDATE automation_projects SET approval=? WHERE id=?', (value, key))
            db.execute('UPDATE automation_history SET approval=? WHERE project=? AND revision=?', (value, key, revision))
            return approval
