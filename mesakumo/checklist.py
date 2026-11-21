"""Checklist review and atomic approval + UTF-8 CSV persistence."""
import csv
import hashlib
import io
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from .domain import PROPOSAL_SCOPE, REQUIREMENT_SCOPES, ReviewDecision
from .persistence import ConflictError
from .requirements import save_requirement, delete_requirement, review_checklist, KINDS
from .agent import sources, snapshot

LEVELS = ('Unspecified', 'Mandatory', 'Optional', 'Informational')


def validate_rows(project, rows):
    _, passages, _ = sources(project, rows)
    allowed = {p.id for p in passages}
    if not project.requirements:
        raise ValueError('A nonempty checklist is required.')
    for r in project.requirements:
        if not r.text.strip() or len(r.text) > 5000 or r.kind not in KINDS or r.obligation_level not in LEVELS:
            raise ValueError('Each requirement needs a valid statement, category and obligation level.')
        if r.scope not in REQUIREMENT_SCOPES:
            raise ValueError('Select a valid requirement scope.')
        if not r.source_ids or any(s not in allowed for s in r.source_ids):
            raise ValueError('Every citation must reference a current buyer passage in this tender and phase.')
        if any(not isinstance(c, str) for c in r.constraints):
            raise ValueError('Exact constraints must be text.')


def make_csv(project, rows, reviewer):
    """One CSV row per approved requirement; citations are lossless JSON arrays."""
    docs, passages, _ = sources(project, rows)
    lookup = {p.id: p for p in passages}
    output = io.StringIO(newline='')
    tender, separator, phase = project.name.rpartition(' · ')
    if not separator: tender, phase = project.name, project.scope
    fields = ['project_id', 'tender', 'phase', 'approved_revision', 'reviewer', 'requirement_id',
              'requirement', 'category', 'requirement_scope', 'obligation_level', 'exact_constraints', 'source_references', 'human_provenance']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for r in project.requirements:
        references = [{'passage_id': sid, 'document_id': lookup[sid].document_id,
            'filename': docs[lookup[sid].document_id].name, 'location': lookup[sid].location,
            'original_excerpt': lookup[sid].excerpt, 'document_version':lookup[sid].document_version} for sid in r.source_ids]
        writer.writerow(dict(project_id=project.id, tender=tender, phase=phase,
            approved_revision=project.revision, reviewer=reviewer, requirement_id=r.id,
            requirement=r.text, category=r.kind, requirement_scope=r.scope, obligation_level=r.obligation_level,
            exact_constraints=json.dumps(r.constraints, ensure_ascii=False),
            source_references=json.dumps(references, ensure_ascii=False), human_provenance=r.provenance))
    return output.getvalue().encode('utf-8')


class ChecklistReview:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS checklist_approvals (
                project_id TEXT, revision INTEGER, fingerprint TEXT, source_snapshot TEXT,
                payload TEXT, csv BLOB NOT NULL, checksum TEXT,
                PRIMARY KEY(project_id,revision))''')

    def current(self, project, rows):
        with self.store.connect() as db:
            row = db.execute('SELECT fingerprint,source_snapshot,payload,csv,checksum FROM checklist_approvals WHERE project_id=? AND revision=?',
                (project.id,project.revision)).fetchone()
        if row and row[0]==project.fingerprint() and row[1]==snapshot(project,rows):
            data = bytes(row[3])
            if not data or hashlib.sha256(data).hexdigest()!=row[4]:
                raise ValueError('Saved CSV integrity check failed. Restore the database backup; progression is blocked.')
            approved = json.loads(row[2])
            scope_by_id = {}
            for requirement in approved['rows']:
                requirement.setdefault('scope', PROPOSAL_SCOPE)
                scope_by_id[requirement['id']] = requirement['scope']
            # Upgrade old download headers after verifying the archived bytes.
            records = list(csv.reader(io.StringIO(data.decode('utf-8'), newline='')))
            if records:
                header = records[0]
                renamed = 'statement' in header and 'requirement' not in header
                add_scope = 'requirement_scope' not in header and 'requirement_id' in header
                if renamed:
                    header[header.index('statement')] = 'requirement'
                if add_scope:
                    id_column = header.index('requirement_id')
                    header.append('requirement_scope')
                    for record in records[1:]:
                        record.append(scope_by_id.get(record[id_column], PROPOSAL_SCOPE))
                if renamed or add_scope:
                    output = io.StringIO(newline='')
                    csv.writer(output).writerows(records)
                    data = output.getvalue().encode('utf-8')
            return {**approved, 'csv': data}
        return None

    def edit(self, project_id, expected_revision, rows, target, text, category, level, citations, constraints, reviewer, note, *, scope=None):
        project = self.store.load(project_id)
        if project.revision != expected_revision: raise ConflictError('Project changed in another tab. Reload before editing.')
        if level not in LEVELS: raise ValueError('Select a valid obligation level.')
        if scope is not None and scope not in REQUIREMENT_SCOPES:
            raise ValueError('Select a valid requirement scope.')
        allowed = {p.id for p in sources(project,rows)[1]}
        if any(s not in allowed for s in citations): raise ValueError('Select current buyer passages for this tender/phase.')
        if not isinstance(text,str) or len(text)>5000: raise ValueError('Statement limit is 5000 characters.')
        save_requirement(project,target,text,category,citations,constraints,reviewer,note)
        item = next(r for r in project.requirements if r.id==target) if target else project.requirements[-1]
        item.obligation_level = level
        if scope is not None:
            item.scope = scope
        # Validate this edit independently so other stale rows can be corrected one by one.
        all_rows = project.requirements
        project.requirements = [item]
        validate_rows(project,rows)
        project.requirements = all_rows
        self.store.save(project,expected_revision)
        return item.id

    def remove(self, project_id, expected_revision, target, reviewer):
        project = self.store.load(project_id)
        if project.revision != expected_revision: raise ConflictError('Project changed in another tab. Reload before editing.')
        if not any(r.id==target for r in project.requirements): raise ValueError('Requirement not found.')
        delete_requirement(project,target,reviewer)
        self.store.save(project,expected_revision)

    def approve(self, project_id, expected_revision, rows, reviewer, acknowledge_gaps=False, csv_factory=None):
        project = self.store.load(project_id)
        existing = self.current(project,rows)
        # Idempotent repeated submission, including a retry of the just-approved revision.
        if existing and expected_revision in (project.revision,existing['input_revision']): return existing
        if project.revision != expected_revision: raise ConflictError('Project changed in another tab. Reload before approval.')
        if not reviewer.strip(): raise ValueError('Reviewer label is required.')
        if not acknowledge_gaps: raise ValueError('Acknowledge source/extraction gaps and manual completeness review.')
        validate_rows(project,rows)
        review_checklist(project,reviewer,allow_source_gaps=True)
        project.decisions.append(ReviewDecision(project.id,project.revision,project.fingerprint(),reviewer.strip(),
            datetime.now(timezone.utc).isoformat(),'Checklist approved and saved','Source gaps acknowledged; not final proposal approval.'))
        try:
            artifact = (csv_factory or make_csv)(project,rows,reviewer.strip())
            if not isinstance(artifact,bytes) or not artifact: raise ValueError('Empty CSV')
            artifact.decode('utf-8')
        except Exception:
            raise ValueError('CSV creation failed. No approval was saved. Retry Approve and Save to recover.') from None
        metadata = dict(project_id=project.id,revision=project.revision,input_revision=expected_revision,
            reviewer=reviewer.strip(),rows=[asdict(r) for r in project.requirements],
            filename=f'{project.id}-checklist-r{project.revision}.csv')
        try:
            with self.store.connect() as db:
                cursor = db.execute('UPDATE projects SET revision=?,payload=? WHERE id=? AND revision=?',
                    (project.revision,json.dumps(project.to_dict()),project.id,expected_revision))
                if cursor.rowcount != 1: raise ConflictError('Project changed in another tab. Reload before approval.')
                db.execute('INSERT INTO checklist_approvals VALUES (?,?,?,?,?,?,?)',
                    (project.id,project.revision,project.fingerprint(),snapshot(project,rows),json.dumps(metadata),artifact,hashlib.sha256(artifact).hexdigest()))
        except sqlite3.Error:
            raise ValueError('Could not save the checklist and CSV. No approval was saved. Retry Approve and Save.') from None
        return {**metadata,'csv':artifact}
