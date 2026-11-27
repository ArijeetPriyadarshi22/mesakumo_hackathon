"""Offline timing probe for Tender 2 and both Tender 3 phases.

Run from the repository root with PYTHONPATH=. .venv/bin/python tests/pilot_automation.py.
The private files stay local and a temporary database is removed on exit.
"""
import json
import os
import tempfile
import time
from pathlib import Path

from mesakumo.automation import run_once
from mesakumo.automation_store import AutomationStore
from mesakumo.discovery import participant_directory
from test_automation import FixtureModel


class PilotModel(FixtureModel):
    def complete_task(self, prompt, *, schema_name, **kwargs):
        result = super().complete_task(prompt, schema_name=schema_name, **kwargs)
        if schema_name == 'Chapter':
            draft = json.loads(result)
            for claim in draft['claims']:
                if claim['kind'] == 'source fact':
                    claim.update(text='Consultant to verify supporting material.', kind='open', sources=[])
            return json.dumps(draft)
        return result


def main():
    root = participant_directory()
    os.environ.update(MESAKUMO_RPM='1000', MESAKUMO_INPUT_TPM='10000000')
    print('Tender · Phase | Cold seconds · calls | Warm seconds · calls | Reading gaps | Result')
    with tempfile.TemporaryDirectory() as directory:
        store = AutomationStore(Path(directory) / 'workspace.sqlite3')
        store.register(root)
        relevant = [row for row in store.projects() if Path(row['folder']).name.startswith(('Ausschreibung 2', 'Ausschreibung 3'))]
        if not relevant:
            raise SystemExit('Tender 2 and Tender 3 test inputs were not found.')
        for row in relevant:
            key = row['id']
            times = []
            for _ in range(2):
                model = PilotModel()
                start = time.monotonic()
                store.enqueue([key])
                run_once(store, lambda: model)
                times.append(f'{time.monotonic() - start:.3f}s · {model.count}')
            project = store.project(key)
            info = next(item for item in store.projects() if item['id'] == key)
            print(f"{Path(row['folder']).name} · {row['phase']} | {' | '.join(times)} | "
                  f"{len(project['snapshot'].get('reading_gaps', []))} | {info['status']}")
            if info['diagnostic']:
                print(info['diagnostic'])


if __name__ == '__main__':
    main()
