"""Reopen a saved Tender 2 review in the regular Streamlit app."""
import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from .discovery import ROOT, identity, packs, participant_directory


def _saved_review(path, project_id):
    """Find a current checklist and a populated review from that revision."""
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('''SELECT p.revision, r.revision, r.payload FROM projects p
            JOIN checklist_approvals c ON c.project_id=p.id AND c.revision=p.revision
            JOIN evidence_reviews r ON r.project_id=p.id AND r.checklist_revision=p.revision
            WHERE p.id=?''', (project_id,)).fetchone()
        if not row:
            return None
        draft = json.loads(row[2])
        if not draft.get('rows'):
            return None
        approved = db.execute('''SELECT 1 FROM evidence_approvals
            WHERE project_id=? AND checklist_revision=? AND revision=?''',
            (project_id, row[0], row[1])).fetchone()
        return bool(approved), len(draft['rows'])


def _find_saved_review(base, project_id):
    candidates = [base / 'workspace.sqlite3', *base.glob('sessions/*/workspace.sqlite3')]
    matches = []
    for path in candidates:
        if not path.is_file():
            continue
        try:
            saved = _saved_review(path, project_id)
            if saved:
                matches.append((saved[0], path.stat().st_mtime_ns, path))
        except (OSError, sqlite3.Error, ValueError):
            # Older or incomplete workspaces may not have evidence tables.
            continue
    if not matches:
        raise ValueError('No saved Tender 2 checklist and evidence review found. Pass --source with its database.')
    return max(matches)[2]


def _backup(source, target):
    """Publish a consistent SQLite copy without overwriting a workspace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        staged = Path(folder) / target.name
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original, closing(
            sqlite3.connect(staged)
        ) as copy:
            original.backup(copy)
        try:
            os.link(staged, target)
        except FileExistsError:
            raise ValueError(f'The destination already exists: {target}. Choose a new --workspace.') from None


def prepare_workspace(base, workspace, project_id, source=None):
    target = workspace / 'workspace.sqlite3'
    if target.exists():
        if source is not None:
            raise ValueError('The workspace already exists. Omit --source to reopen it or choose a new --workspace.')
        return target
    source = source.resolve() if source is not None else _find_saved_review(base, project_id)
    if not _saved_review(source, project_id):
        raise ValueError('The selected database has no saved Tender 2 checklist and evidence review.')
    _backup(source, target)
    overview = source.parent / 'tender_overviews.sqlite3'
    if not overview.is_file():
        overview = base / 'tender_overviews.sqlite3'
    target_overview = workspace / overview.name
    if overview.is_file() and not target_overview.exists():
        try:
            _backup(overview, target_overview)
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f'Checklist and evidence restored. The overview cache could not be copied: {exc}', file=sys.stderr)
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Explicit saved workspace.sqlite3 to copy on first launch.')
    parser.add_argument('--workspace', type=Path, help='Folder for the independent testing workspace.')
    parser.add_argument('--port', type=int, default=8505, help='Local Streamlit port (default: 8505).')
    parser.add_argument('--enable-model', action='store_true', help='Use the normal model configuration for manual runs.')
    args = parser.parse_args(argv)

    base = (ROOT / Path(os.environ.get('MESAKUMO_DATA_DIR', 'data'))).resolve()
    workspace = (ROOT / args.workspace).resolve() if args.workspace else base / 'recovered-tender-2'
    if not workspace.is_relative_to(ROOT):
        parser.error('The testing workspace must be inside the repository.')
    directory = participant_directory()
    tender = next((pack for pack in packs(directory) if re.match(r'^Ausschreibung\s+2(?:\s|$)', pack.name)), None)
    if tender is None:
        parser.error('No Tender 2 folder found. Check MESAKUMO_PARTICIPANT_DIR.')
    try:
        database = prepare_workspace(base, workspace, 'local-' + identity(tender.name + '/single'), args.source)
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.error(str(exc))

    env = dict(os.environ, MESAKUMO_DATA_DIR=str(workspace), MESAKUMO_KEEP_SESSION='yes',
               MESAKUMO_LEGACY_STREAMLIT='yes', MESAKUMO_INITIAL_TENDER=tender.name, MESAKUMO_PARTICIPANT_DIR=str(directory))
    if not args.enable_model:
        env['MESAKUMO_LLM_PROVIDER'] = 'disabled'
    print(f'Opening {tender.name} from {database}', flush=True)
    os.execve(sys.executable, [sys.executable, '-m', 'streamlit', 'run', str(ROOT / 'mesakumo/streamlit_app.py'),
                              '--server.address', '127.0.0.1', '--server.port', str(args.port),
                              '--server.headless', 'true', '--browser.gatherUsageStats', 'false'], env)


if __name__ == '__main__':
    main()
