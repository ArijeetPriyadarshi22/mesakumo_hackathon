"""Launch the local workspace on the loopback interface."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Launch TenderWorkbench on this workstation.')
    parser.add_argument('--port', type=int, default=os.environ.get('MESAKUMO_PORT', '8501'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--legacy-http', action='store_true')
    mode.add_argument('--worker', action='store_true', help='Process saved jobs without launching a browser.')
    args = parser.parse_args()
    app = Path(__file__).resolve().parent
    if args.worker:
        from .automation import work_loop
        from .automation_store import AutomationStore, workspace_path

        try:
            work_loop(AutomationStore(workspace_path()), args.port)
        except KeyboardInterrupt:
            return
        return
    if args.legacy_http:
        from .persistence import ProjectStore
        from .ui.server import serve

        data = (app.parent / os.environ.get('MESAKUMO_DATA_DIR', 'data')).resolve()
        if not data.is_relative_to(app.parent):
            parser.error('Legacy HTTP data must stay inside the checkout.')
        serve(ProjectStore(data / 'workspace.sqlite3'), args.port)
        return
    from streamlit.web.cli import main as streamlit

    sys.argv = ['streamlit', 'run', str(app / 'streamlit_app.py'),
                '--server.address', '127.0.0.1', '--server.port', str(args.port)]
    streamlit()


if __name__ == '__main__':
    main()
