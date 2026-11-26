"""Measure local fingerprint cost for an eligible, near-limit fixture pack."""
import argparse
import hashlib
import os
import tempfile
import time
import tracemalloc
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from mesakumo.automation import prepare_inputs, run_once
from mesakumo.automation_store import AutomationStore
from mesakumo.discovery import ROOT
from mesakumo.evidence import EvidenceSource


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare-download', action='store_true')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        pack = root / 'Ausschreibung 1'
        pack.mkdir()
        for i, size in enumerate((14, 14, 14, 16)):
            (pack / f'tender-{i}.txt').write_bytes(b'A' * (size * 1024**2))
        inventory = {'evidence.pptx': EvidenceSource('evidence.pptx', 'evidence.pptx', 'Team information')}
        (root / 'evidence.pptx').write_bytes(b'B' * (24 * 1024**2))
        store = AutomationStore(root / 'workspace.sqlite3')
        key, = store.register(root)
        with patch('mesakumo.automation.evidence_inventory', return_value=inventory):
            inputs = prepare_inputs(store.project(key), {}, store)
        oversized = next(row['error'] for row in inputs.rows if row['path'] == 'tender-3.txt')
        print('Oversized:', oversized)
        del inputs
        with patch('mesakumo.automation.evidence_inventory', return_value=inventory):
            fingerprint = prepare_inputs(store.project(key), {}, store).key
        store.save_revision(key, 0, {}, fingerprint, b'test approved deck')
        store.approve(key, 1, store.project(key)['checksum'], 'Fixture', template_hash=None, blockers=lambda _: [])
        app = AppTest.from_file(str(ROOT / 'mesakumo' / 'streamlit_app.py'), default_timeout=30)
        app.query_params = {'project': key, 'tab': 'review'}
        real_read, real_digest = Path.read_bytes, hashlib.file_digest
        read = 0

        def measure(label, action):
            nonlocal read
            read = 0
            tracemalloc.start()
            start = time.monotonic()
            action()
            elapsed = time.monotonic() - start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            print(f'{label}: {read / 1024**2:.1f} MiB hashed/read, {peak / 1024**2:.1f} MiB traced allocations, {elapsed:.3f} s')

        def read_bytes(path):
            nonlocal read
            data = real_read(path)
            if path.is_relative_to(root):
                read += len(data)
            return data

        def digest(handle, *args, **kwargs):
            nonlocal read
            if isinstance(handle.name, (str, Path)) and Path(handle.name).is_relative_to(root):
                read += os.fstat(handle.fileno()).st_size
            return real_digest(handle, *args, **kwargs)

        with patch.dict(os.environ, {'MESAKUMO_AUTOMATION_DIR': directory}), patch.object(
            Path, 'read_bytes', read_bytes
        ), patch('hashlib.file_digest', digest), patch('mesakumo.automation.evidence_inventory', return_value=inventory), patch(
            'mesakumo.ui.automation.start_worker'
        ):
            app.run()
            measure('Approved review rerun', app.run)
            if options.prepare_download:
                click = next(w for w in app.button if w.label == 'Verify approved download')
                measure('Explicit download verification', lambda: click.click().run())
            store.enqueue([key])
            measure('Worker warm verify', lambda: run_once(store, lambda: None))
            assert store.project(key)['revision'] == 1
            print('Warm model calls:', store.metrics(key)['calls'])


if __name__ == '__main__':
    main()
