"""PPTX generation from approved draft sections, and the second human review of the deck.

Uses the corporate exporter in export.py: a model structures the approved sections into
the slide JSON (missing facts become [OFFEN: ...]), and a deterministic renderer fills
Angebotstemplate.pptx. Approval is checked here instead of in the exporter: in the app the
approved draft package is the gate; on the command line the input must be the approved
sections CSV, or the caller must explicitly allow an unreviewed drafts file, which marks
the title slide as an unreviewed draft.

    python -m mesakumo.pptx_from_drafts APPROVED_SECTIONS.csv --out exports/angebot.pptx
    python -m mesakumo.pptx_from_drafts exports/ausschreibung-2-drafts.csv --out exports/test.pptx --allow-unreviewed
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .domain import EvidenceMatch, Project, ProposalSection, Requirement, SourcePassage
from .persistence import ConflictError
from .proposal import OFFEN, QUALITY

PPTX_MODEL = os.environ.get('MESAKUMO_PPTX_MODEL', 'bedrock/claude-sonnet-5@eu-west-1')
TEMPLATE_NAME = 'Angebotstemplate.pptx'
UNREVIEWED_MARK = 'ENTWURF (ungeprüft) – '


def find_template(participant_root):
    path = Path(participant_root) / TEMPLATE_NAME
    if not path.is_file():
        raise FileNotFoundError(f'{TEMPLATE_NAME} not found in {participant_root}.')
    return path


def _api_key():
    from .models import configure_team_one
    configure_team_one()  # Loads the Team 1 key into the environment if nothing is configured.
    key = os.environ.get('REQUESTY_API_KEY') or os.environ.get('MESAKUMO_LLM_API_KEY', '')
    if not key:
        raise ValueError('No Requesty key configured. Set REQUESTY_API_KEY or provide the Team 1 key file.')
    return key


def project_from_csv(path, project_name='', allow_unreviewed=False, client=''):
    """Read approved sections (or, if allowed, unreviewed drafts) into a Project. Returns (project, reviewed)."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        columns, records = set(reader.fieldnames or []), list(reader)
    if {'Requirement ID', 'Approved by', 'Section text'} <= columns:
        reviewed, records = True, [r for r in records if r['Section text'].strip()]
        rid = lambda r: r['Requirement ID']
        citations = lambda r: json.loads(r['Citations'] or '[]')
        gap = lambda r: r.get('Evidence gap', '')
    elif {'Source row', 'Section text', 'Draft origin'} <= columns:
        if not allow_unreviewed:
            raise ValueError('This is a drafts file whose sections nobody has approved. Use the approved sections '
                             'CSV from Final Review & Export, or pass --allow-unreviewed for a test deck.')
        reviewed, records = False, [r for r in records if r['Section text'].strip() and not r.get('Error')]
        rid = lambda r: 'row-' + r['Source row']
        citations = lambda r: [dict(passage_id=f"{line}", document_id=line.split(' · ', 1)[0],
                                    location=line.split(' · ', 1)[-1], excerpt='', document_version='')
                               for line in r['Cited evidence'].splitlines() if line.strip()]
        gap = lambda r: ''
    else:
        raise ValueError('Unknown CSV layout: expected the approved sections CSV or the drafting tool output.')
    if not records:
        raise ValueError('The CSV contains no sections to put into a presentation.')
    passages = {}
    for record in records:
        for c in citations(record):
            passages[c['passage_id']] = SourcePassage(c['passage_id'], c.get('document_id', ''), c.get('location', ''),
                                                      c.get('excerpt', ''), c.get('document_version', ''))
    project = Project(
        id=Path(path).stem, name=project_name or Path(path).stem, synthetic=False,
        scope=('Approved proposal sections' if reviewed else 'UNGEPRÜFTER ENTWURF — sections not approved')
              + (f' · Auftraggeber: {client}' if client else ''),
        requirements=[Requirement(rid(r), r['Requirement'], [], r['Category']) for r in records],
        passages=list(passages.values()),
        evidence=[EvidenceMatch('E-' + rid(r), rid(r), [c['passage_id'] for c in citations(r)],
                                QUALITY.get(r['Evidence fit'], 'partial'), gap(r),
                                decision='Approved' if reviewed else 'Unreviewed') for r in records],
        sections=[ProposalSection('S-' + rid(r), r['Section title'], r['Section text'], [rid(r)],
                                  [c['passage_id'] for c in citations(r)], reviewed=reviewed) for r in records])
    return project, reviewed


def render_pptx(project, template, *, reviewed, api_key=None, model=None, on_progress=None, structurer=None):
    """Structure the sections with the model, fill the template. Returns (pptx bytes, slide payload)."""
    from .export import ClaudeProposalStructurer, PptxProposalExporter
    structurer = structurer or ClaudeProposalStructurer(model_id=model or PPTX_MODEL, api_key=api_key or _api_key(),
                                                        on_progress=on_progress)
    for attempt in (1, 2):  # The model occasionally emits invalid JSON (e.g. an unescaped quote); retry once.
        try:
            payload = structurer.build_payload(project)
            break
        except json.JSONDecodeError as error:
            if attempt == 2:
                raise ValueError(f'The structuring model returned invalid slide data twice ({error}). '
                                 'No presentation was created; try again.') from None
            if on_progress:
                on_progress('Slide data was not valid JSON — asking the model once more…')
    if not reviewed:
        meta = payload.setdefault('meta', {})
        meta['project_title'] = UNREVIEWED_MARK + str(meta.get('project_title', ''))
    exporter = PptxProposalExporter(fallback_template_path=template, structurer=structurer,
                                    strict_approval=False, on_progress=on_progress)
    return exporter.render(project, prebuilt_payload=payload), payload


def open_markers(payload):
    """Every distinct [OFFEN: ...] marker the slide JSON carries into the deck, in order."""
    found = []
    def walk(value):
        if isinstance(value, str):
            found.extend(f'[OFFEN: {m.strip()}]' for m in OFFEN.findall(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return list(dict.fromkeys(found))


class GeneratedDecks:
    """Generated PPTX files for one approved draft package, with the second review decision."""

    def __init__(self, store, package):
        self.store = store
        self.key = (package['project_id'], package['checklist_revision'], package['evidence_revision'],
                    package['revision'])
        with store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS proposal_decks (
                project_id TEXT, checklist_revision INTEGER, evidence_revision INTEGER, package_revision INTEGER,
                revision INTEGER NOT NULL, meta TEXT NOT NULL, payload TEXT NOT NULL, pptx BLOB NOT NULL,
                PRIMARY KEY(project_id, checklist_revision, evidence_revision, package_revision, revision))''')

    def save(self, pptx, payload, model):
        meta = dict(created_at=datetime.now(timezone.utc).isoformat(), model=model, status='To review',
                    open_markers=open_markers(payload))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = db.execute('''SELECT MAX(revision) FROM proposal_decks WHERE project_id=? AND checklist_revision=?
                AND evidence_revision=? AND package_revision=?''', self.key).fetchone()[0] or 0
            db.execute('INSERT INTO proposal_decks VALUES (?,?,?,?,?,?,?,?)',
                       (*self.key, latest + 1, json.dumps(meta, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False), pptx))
        return latest + 1

    def latest(self):
        """(revision, meta, payload, pptx bytes) of the newest deck for this package, or None."""
        with self.store.connect() as db:
            row = db.execute('''SELECT revision, meta, payload, pptx FROM proposal_decks WHERE project_id=? AND
                checklist_revision=? AND evidence_revision=? AND package_revision=? ORDER BY revision DESC LIMIT 1''',
                self.key).fetchone()
        return (row[0], json.loads(row[1]), json.loads(row[2]), bytes(row[3])) if row else None

    def decide(self, revision, reviewer, approve, comment=''):
        if not reviewer.strip():
            raise ValueError('Enter your name as reviewer first.')
        if not approve and not comment.strip():
            raise ValueError('Say what should change in the presentation.')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT meta FROM proposal_decks WHERE project_id=? AND checklist_revision=? AND
                evidence_revision=? AND package_revision=? AND revision=?''', (*self.key, revision)).fetchone()
            newest = db.execute('''SELECT MAX(revision) FROM proposal_decks WHERE project_id=? AND checklist_revision=?
                AND evidence_revision=? AND package_revision=?''', self.key).fetchone()[0]
            if row is None or revision != newest:
                raise ConflictError('A newer presentation exists. Review the latest one.')
            meta = {**json.loads(row[0]), 'status': 'Approved' if approve else 'Declined', 'reviewer': reviewer.strip(),
                    'comment': comment.strip(), 'decided_at': datetime.now(timezone.utc).isoformat()}
            db.execute('''UPDATE proposal_decks SET meta=? WHERE project_id=? AND checklist_revision=? AND
                evidence_revision=? AND package_revision=? AND revision=?''',
                       (json.dumps(meta, ensure_ascii=False), *self.key, revision))


def main(argv=None):
    from .discovery import participant_directory
    parser = argparse.ArgumentParser(description='Generate the proposal PPTX from approved draft sections.')
    parser.add_argument('csv', help='Approved sections CSV from Final Review & Export (or a drafts file, see below)')
    parser.add_argument('--out', required=True, help='Output .pptx path, e.g. exports/angebot.pptx')
    parser.add_argument('--name', default='', help='Project name shown to the structuring model')
    parser.add_argument('--client', default='', help='Client (Auftraggeber) name, e.g. "VDI Technologiezentrum GmbH"')
    parser.add_argument('--allow-unreviewed', action='store_true',
                        help='Accept the drafting tool output (sections nobody approved); marks the deck as unreviewed')
    parser.add_argument('--model', default=PPTX_MODEL, help=f'Structuring model (default {PPTX_MODEL})')
    args = parser.parse_args(argv)

    project, reviewed = project_from_csv(args.csv, args.name, args.allow_unreviewed, args.client)
    template = find_template(participant_directory())
    print(f'{len(project.sections)} sections ({"approved" if reviewed else "UNREVIEWED"}) → {template.name}', flush=True)
    pptx, payload = render_pptx(project, template, reviewed=reviewed, model=args.model,
                                on_progress=lambda message: print('  ' + message, flush=True))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pptx)
    out.with_suffix('.slides.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    markers = open_markers(payload)
    print(f'Wrote {out} ({len(pptx) / 1e6:.1f} MB) and {out.with_suffix(".slides.json").name}; '
          f'{len(markers)} open point(s) in the deck.')
    for marker in markers:
        print('  - ' + marker)


if __name__ == '__main__':
    sys.exit(main())
