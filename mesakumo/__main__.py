"""Launch the local workspace on the loopback interface."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Launch TenderWorkbench on this workstation.')
    parser.add_argument('--port', type=int, default=os.environ.get('MESAKUMO_PORT', '8501'))
    parser.add_argument('--worker', action='store_true', help='Process saved jobs without launching a browser.')
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
    from streamlit.web.cli import main as streamlit

    sys.argv = ['streamlit', 'run', str(app / 'streamlit_app.py'),
                '--server.address', '127.0.0.1', '--server.port', str(args.port)]
    streamlit()


if __name__ == '__main__':
    main()
