"""Revision-bound evidence review and atomic approval exports."""
import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from .evidence import resolve_evidence_path
from .evidence_matching import GRADES, LexicalIndex
from .documents import MAX_FILE_BYTES
from .domain import proposal_requirements
from .persistence import ConflictError

COLUMNS = ['Requirement', 'Category', 'Tender sources', 'Evidence', 'Evidence sources', 'Evidence fit']


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _checksum(data):
    return hashlib.sha256(data).hexdigest()


def review_table(draft):
    rows = []
    for row in draft['rows']:
        requirement = row['requirement']
        evidence = [draft['corpus'][pid] for pid in row['citation_ids']]
        references = lambda citations: '\n'.join(f"{c['filename']} · {c['location']}" for c in citations)
        rows.append({'Requirement ID': requirement['id']} | dict(zip(COLUMNS, (
            requirement['text'], requirement['kind'], references(row['tender_sources']),
            '\n\n'.join(filter(None, [row['notes'], *(c['excerpt'] for c in evidence)])),
            references(evidence), row['fit'],
        ))) | {'Gap / next action': '\n'.join(filter(None, (row['gap'], row['current_action'], row['error']))),
               'Status': row['status']})
    return rows


def evidence_csv(draft):
    output = io.StringIO(newline='')
    fields = [*COLUMNS, 'Requirement ID', 'Checklist revision', 'Evidence review revision',
              'Gap / next action', 'Status', 'Tender citations', 'Evidence citations']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for values, row in zip(review_table(draft), draft['rows']):
        writer.writerow(values | {
            'Checklist revision': draft['checklist_revision'],
            'Evidence review revision': draft['review_revision'],
            'Tender citations': _json(row['tender_sources']),
            'Evidence citations': _json([draft['corpus'][pid] for pid in row['citation_ids']]),
        })
    return output.getvalue().encode('utf-8')


def documents_current(draft, directory, inventory):
    if draft['directory'] != str(directory.resolve()):
        return False
    for document in draft['documents']:
        relative = document['relative_path']
        if relative not in inventory or asdict(inventory[relative]) != {
                key: document[key] for key in ('relative_path', 'filename', 'role')}:
            return False
        try:
            path = resolve_evidence_path(directory, relative)
            if not document['version'] or document['status'] == 'Missing':
                return False
            limit = 64 * 1024**2 if path.suffix.lower() == '.pptx' else MAX_FILE_BYTES
            if path.stat().st_size > limit:
                return False
            with path.open('rb') as source:
                if hashlib.file_digest(source, 'sha256').hexdigest() != document['version']:
                    return False
        except FileNotFoundError:
            if document['status'] != 'Missing':
                return False
        except (OSError, ValueError):
            return False
    return True


def row_coverage_issue(row):
    scope = row.get('search_scope')
    if not row['fit'] or row['status'] not in ('Judged', 'Reviewed') or scope == 'incomplete':
        return 'Complete the incomplete evidence search.'
    if scope == 'current_action' and row['fit'] != 'No match':
        return 'A current signature remains a current action.'
    if row['fit'] in GRADES[1:] and scope not in ('full_readable_scan', 'current_action'):
        return 'Confirm the readable evidence for this assessment.'
    return ''


def coverage_issue(draft):
    if any(doc['status'] != 'Read' for doc in draft['documents']):
        return 'Resolve unreadable selected evidence or change the selection and rerun matching.'
    return next((issue for row in draft['rows'] if (issue := row_coverage_issue(row))), '')


def evidence_file_issues(draft):
    return [f"{doc['filename']} · {doc['status']}" for doc in draft['documents'] if doc['status'] != 'Read']


class EvidenceReview:
    def __init__(self, store, approved):
        self.store = store
        self.approved = approved
        self.requirements = proposal_requirements(approved['rows'])
        self.key = (approved['project_id'], approved['revision'])
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS evidence_reviews (
                project_id TEXT, checklist_revision INTEGER, revision INTEGER, payload TEXT NOT NULL,
                PRIMARY KEY(project_id,checklist_revision))''')
            db.execute('''CREATE TABLE IF NOT EXISTS evidence_approvals (
                project_id TEXT, checklist_revision INTEGER, revision INTEGER, payload TEXT NOT NULL,
                csv BLOB NOT NULL, checksum TEXT NOT NULL,
                PRIMARY KEY(project_id,checklist_revision,revision))''')

    def _check_checklist(self, db):
        current = db.execute('SELECT revision FROM projects WHERE id=?', (self.key[0],)).fetchone()
        if current != (self.key[1],):
            raise ConflictError('The approved checklist changed. Reopen the current checklist first.')

    def _load(self, db):
        row = db.execute('SELECT payload FROM evidence_reviews WHERE project_id=? AND checklist_revision=?',
                         self.key).fetchone()
        return json.loads(row[0]) if row else {'review_revision': 0, 'selected': []}

    def load(self):
        with self.store.connect() as db:
            return self._load(db)

    def _change(self, revision, update):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._check_checklist(db)
            draft = self._load(db)
            if draft['review_revision'] != revision:
                raise ConflictError('The evidence review changed. Try again with the latest review.')
            replacement = update(draft)
            replacement['review_revision'] = revision + 1
            db.execute('''INSERT INTO evidence_reviews VALUES (?,?,?,?)
                ON CONFLICT(project_id,checklist_revision) DO UPDATE
                SET revision=excluded.revision,payload=excluded.payload''',
                (*self.key, revision + 1, _json(replacement)))
            return replacement

    def select(self, revision, selected):
        return self._change(revision, lambda _: {'selected': list(selected)})

    def save_run(self, revision, run, readings, directory):
        tender = {row['requirement_id']: json.loads(row['source_references'])
                  for row in csv.DictReader(io.StringIO(self.approved['csv'].decode('utf-8')))}
        requirements = self.requirements
        if ((run.project_id, run.revision) != self.key
                or [row.requirement['id'] for row in run.rows] != [row['id'] for row in requirements]):
            raise ValueError('Evidence results must cover the approved checklist in order.')
        versions = {doc['relative_path']: doc['version'] for doc in run.documents}
        if ([item.source.relative_path for item in readings] != list(versions)
                or any(p.document_version != versions[item.source.relative_path]
                       for item in readings for p in item.passages)):
            raise ValueError('Evidence passages do not match the selected document versions.')
        index = LexicalIndex(readings)
        corpus = {pid: index.citation(pid) for pid in index.entries}
        rows = []
        for result, requirement in zip(run.rows, requirements):
            ids = [citation['passage_id'] for citation in result.evidence]
            if any(pid not in corpus for pid in ids):
                raise ValueError('Evidence result cites a passage outside the selected corpus.')
            rows.append({'requirement': requirement, 'tender_sources': tender[requirement['id']],
                         'citation_ids': ids, 'fit': result.fit, 'gap': result.gap,
                         'notes': '',
                         'current_action': result.current_action, 'status': result.status,
                         'error': result.error, 'trace': result.trace, 'search_scope': result.search_scope})
        selected = [doc['relative_path'] for doc in run.documents]

        def replace(draft):
            if draft['selected'] != selected:
                raise ConflictError('The evidence selection changed while matching.')
            return dict(project_id=run.project_id, phase=run.phase, checklist_revision=run.revision,
                        selected=selected, directory=str(directory.resolve()), documents=run.documents,
                        corpus=corpus, rows=rows)
        return self._change(revision, replace)

    @staticmethod
    def _validate_row(row, corpus, *, allow_open=False):
        fit, ids = row['fit'], row['citation_ids']
        if fit not in GRADES and not (allow_open and fit == ''):
            raise ValueError('Resolve every incomplete requirement before approving evidence.')
        if any(pid not in corpus for pid in ids):
            raise ValueError('Only citations from the selected corpus are allowed.')
        if fit in GRADES[:2] and not ids:
            raise ValueError('Strong and Partial match require an evidence citation.')
        if fit == 'No match' and not corpus and not allow_open:
            raise ValueError('No match requires review of readable selected evidence.')
        if fit == 'No match' and ids:
            raise ValueError('Remove evidence citations from a No match row.')
        if fit in GRADES[1:] and not row['gap'].strip() and not allow_open:
            raise ValueError('Partial and No match require a Gap / next action note.')

    def edit(self, revision, requirement_id, fit, notes, gap, citation_ids):
        def update(draft):
            row = next(item for item in draft['rows'] if item['requirement']['id'] == requirement_id)
            prior_status, prior_error = row['status'], row['error']
            row.update(fit=fit, notes=notes.strip(), gap=gap.strip(),
                       citation_ids=list(dict.fromkeys(citation_ids)), status='Reviewed', error='')
            if prior_status not in ('Judged', 'Reviewed') or row.get('search_scope') == 'incomplete':
                row.update(status=prior_status, error=prior_error)
                if fit in ('Choose fit', 'No match') and not row['citation_ids']:
                    row['fit'] = ''
                    return draft
            self._validate_row(row, draft['corpus'])
            return draft
        return self._change(revision, update)

    def _covers_proposal_requirements(self, draft):
        return [row['requirement']['id'] for row in draft.get('rows', [])] == [
            row['id'] for row in self.requirements
        ]

    def current(self, directory, inventory):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._check_checklist(db)
            draft = self._load(db)
            row = db.execute('''SELECT payload,csv,checksum FROM evidence_approvals
                WHERE project_id=? AND checklist_revision=? AND revision=?''',
                (*self.key, draft['review_revision'])).fetchone()
        if (not row or not self._covers_proposal_requirements(draft)
                or not documents_current(draft, directory, inventory)):
            return None
        snapshot = json.loads(row[0])
        if snapshot['review'] != draft or _checksum(bytes(row[1])) != row[2]:
            raise ValueError('Saved evidence snapshot or CSV failed its integrity check.')
        return {**snapshot, 'csv': bytes(row[1])}

    def approve(self, revision, directory, inventory, reviewer, acknowledge):
        if not reviewer.strip():
            raise ValueError('Enter the evidence reviewer.')
        try:
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                self._check_checklist(db)
                draft = self._load(db)
                if draft['review_revision'] != revision:
                    raise ConflictError('The evidence review changed. Review the latest results before approving.')
                if (not draft.get('rows') or not self._covers_proposal_requirements(draft)
                        or not documents_current(draft, directory, inventory)):
                    raise ValueError('Evidence inputs are missing or changed. Run matching on the current selection.')
                for row in draft['rows']:
                    self._validate_row(row, draft['corpus'], allow_open=True)
                if db.execute('''SELECT 1 FROM evidence_approvals
                        WHERE project_id=? AND checklist_revision=? AND revision=?''',
                        (*self.key, revision)).fetchone():
                    return
                try:
                    data = evidence_csv(draft)
                except Exception:
                    raise ValueError('Evidence CSV creation failed. No approval was saved.') from None
                meta = dict(review=draft, reviewer=reviewer.strip(), acknowledged=acknowledge,
                            approved_at=datetime.now(timezone.utc).isoformat(),
                            filename=f'{self.key[0]}-evidence-c{self.key[1]}-r{revision}.csv')
                db.execute('INSERT INTO evidence_approvals VALUES (?,?,?,?,?,?)',
                           (*self.key, revision, _json(meta), data, _checksum(data)))
        except sqlite3.Error:
            raise ValueError('Could not save evidence approval and CSV. Retry Approve and Save Evidence.') from None
