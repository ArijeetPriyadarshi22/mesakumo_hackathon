import os
from pathlib import Path
from .persistence import ProjectStore
from .ui.server import serve

if __name__ == '__main__':
    root = Path(__file__).resolve().parent.parent
    data = Path(os.environ.get('MESAKUMO_DATA_DIR', 'data'))
    if not data.is_absolute():
        data = root / data
    if not data.resolve().is_relative_to(root):
        raise SystemExit('MESAKUMO_DATA_DIR must remain inside the project root.')
    serve(ProjectStore(data / 'workspace.sqlite3'), int(os.environ.get('MESAKUMO_PORT', '8501')))
