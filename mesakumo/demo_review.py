"""Demo data for trying the draft review workflow without extraction and evidence approval.

Builds an evidence review from an evidence CSV (see evidence_csv_drafts) and pre-fills
drafts from that tool's output CSV. The result is a DEMO: in the table-download layout the
evidence verdicts were never approved by a person. It is stored in its own database and
never mixed with the real workspace.
"""
import csv
from pathlib import Path

from .evidence_csv_drafts import SourceFinder, load_rows
from .proposal import ProposalDrafts

DEMO_PROJECT = 'demo-evidence-csv'


def build_review(evidence_csv, participant_root):
    """An evidence review dict (the shape Proposal Draft receives) from an evidence CSV."""
    rows, skipped = load_rows(evidence_csv, SourceFinder(participant_root))
    corpus = {}
    for row in rows:
        for pid, citation in row.pop('corpus').items():
            corpus[pid] = {'document_id': citation.get('relative_path', ''), 'document_version': '', **citation}
        row['requirement'].setdefault('source_ids', [])
    return dict(project_id=DEMO_PROJECT, checklist_revision=1, review_revision=1, phase='demo',
                rows=rows, corpus=corpus, skipped=len(skipped), source=Path(evidence_csv).name)


def seed_drafts(store, review, drafts_csv):
    """Load drafts from evidence_csv_drafts output as 'Draft' sections; returns how many were loaded."""
    drafts = ProposalDrafts(store, review)
    existing = drafts.all()
    by_number = {row['number']: row for row in review['rows']}
    loaded = 0
    with open(drafts_csv, encoding='utf-8-sig', newline='') as handle:
        for record in csv.DictReader(handle):
            row = by_number.get(int(record['Source row'] or 0))
            if not row or not record['Section text'] or row['requirement']['id'] in existing:
                continue
            refs = {f"{c['filename']} · {c['location']}": pid for pid, c in review['corpus'].items()
                    if pid in row['citation_ids']}
            cited = [refs[line] for line in record['Cited evidence'].splitlines() if line in refs]
            draft = dict(title=record['Section title'], text=record['Section text'], citation_ids=cited,
                         origin=f"Imported from {Path(drafts_csv).name} · {record['Draft origin']}")
            drafts._write(row['requirement']['id'], 0,
                          lambda _, d=draft, f=row['fit']: {**d, 'fit': f, 'status': 'Draft', 'declines': []})
            loaded += 1
    return loaded


def reset(store):
    """Remove all demo drafts and draft approvals."""
    with store.connect() as db:
        for table in ('proposal_drafts', 'proposal_packages'):
            db.execute(f'DELETE FROM {table} WHERE project_id=?', (DEMO_PROJECT,))
