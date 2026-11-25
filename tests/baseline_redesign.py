"""Read-only Tender 2 baseline and repeatable legacy drafting outcomes."""
import argparse
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from mesakumo.models import ModelUnavailable
from mesakumo.proposal import MAX_PROMPT_BYTES, draft_row
from test_proposal import CORPUS, PARTIAL, ROWS, Stub


class Unavailable:
    def __init__(self):
        self.calls = []

    def complete_task(self, *_args, **_kwargs):
        self.calls.append('Synthetic call')
        raise ModelUnavailable('Synthetic provider failure.')


def baseline(path):
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / 'baseline.sqlite3'
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(copy) as db:
            src.backup(db)
            projects = [(pid, json.loads(payload)) for pid, payload in db.execute('SELECT id,payload FROM projects')]
            pid, tender = next((pid, tender) for pid, tender in projects
                               if tender['name'].startswith('Ausschreibung 2') and tender['requirements'])
            requirements = tender['requirements']
            print('Requirements', len(requirements), 'obligation', Counter(r['obligation_level'] for r in requirements))
            print('Raw scope', Counter(r.get('scope') for r in requirements))
            review, = db.execute('SELECT payload FROM evidence_reviews WHERE project_id=? ORDER BY revision DESC LIMIT 1', (pid,)).fetchone()
            evidence = json.loads(review)
            print('Evidence sources', len(evidence['selected']), 'fit', Counter(row['fit'] for row in evidence['rows']))
            payload, = db.execute('SELECT payload FROM proposal_packages WHERE project_id=? ORDER BY revision DESC LIMIT 1', (pid,)).fetchone()
            package = json.loads(payload)
            print('Package', {k: len(package[k]) for k in ('sections', 'excluded', 'evidence_file_issues')})


def drafting():
    oversized = dict(CORPUS, P10=dict(CORPUS['P10'], excerpt='x' * (MAX_PROMPT_BYTES + 1)))
    cases = [('valid', CORPUS, Stub(PARTIAL)), ('invalid JSON', CORPUS, Stub('{')),
             ('provider failure', CORPUS, Unavailable()), ('oversized prompt', oversized, Stub())]
    for label, corpus, model in cases:
        try:
            title = draft_row(ROWS[0], corpus, model)['title']
            print(label, 'saved candidate', bool(title), 'provider calls', len(model.calls))
        except (ValueError, ModelUnavailable) as error:
            print(label, type(error).__name__, str(error), 'provider calls', len(getattr(model, 'calls', [])))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=Path('data/demo-tender-2/workspace.sqlite3'))
    args = parser.parse_args()
    if args.database.is_file():
        baseline(args.database)
    else:
        print('Saved baseline database unavailable. Running synthetic drafting cases.')
    drafting()
