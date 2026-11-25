"""Default local Streamlit entry point."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if os.environ.get('MESAKUMO_LEGACY_STREAMLIT') == 'yes':
    import runpy

    runpy.run_module("mesakumo.legacy_streamlit", run_name="__main__")
else:
    from mesakumo.ui.automation import run

    run()
