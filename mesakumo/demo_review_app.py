"""Demo: try the draft review workflow on an evidence CSV, without extraction or evidence approval.

    .venv\\Scripts\\python -m streamlit run mesakumo/demo_review_app.py

Settings (environment variables):
    MESAKUMO_PARTICIPANT_DIR  folder with the original files (citations are matched back to them)
    MESAKUMO_DEMO_CSV         evidence CSV (default: ../2026-11-21T07-49_export.csv)
    MESAKUMO_DEMO_DRAFTS      drafts to pre-fill (default: exports/ausschreibung-2-drafts.csv)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # Streamlit runs this file as a script.

import streamlit as st

from mesakumo.demo_review import build_review, reset, seed_drafts
from mesakumo.discovery import participant_directory
from mesakumo.persistence import ProjectStore
from mesakumo.ui.proposal import final_review_view, proposal_view

st.set_page_config(page_title='Draft review demo', layout='wide')
evidence_csv = Path(os.environ.get('MESAKUMO_DEMO_CSV', ROOT.parent / '2026-11-21T07-49_export.csv'))
drafts_csv = Path(os.environ.get('MESAKUMO_DEMO_DRAFTS', ROOT / 'exports' / 'ausschreibung-2-drafts.csv'))
if not evidence_csv.is_file():
    st.error(f'Evidence CSV not found: {evidence_csv}. Set MESAKUMO_DEMO_CSV.')
    st.stop()


@st.cache_resource(show_spinner='Reading the evidence CSV and matching its citations to the original files…')
def load(path, modified):
    return build_review(path, participant_directory())


review = load(str(evidence_csv), evidence_csv.stat().st_mtime)
store = ProjectStore(Path(os.environ.get('MESAKUMO_DEMO_DATA', ROOT / 'data' / 'demo')) / 'workspace.sqlite3')
pages = ['Proposal Draft', 'Final Review & Export']
page = st.sidebar.radio('Page', pages, key='page')
st.sidebar.caption(f"{review['source']} · {len(review['rows'])} rows with a verdict, {review['skipped']} without")
if st.sidebar.button('Reset demo', help='Remove all decisions and edits, and load the drafts again'):
    reset(store)
    st.session_state.pop('demo_seeded', None)
if 'demo_seeded' not in st.session_state and drafts_csv.is_file():
    st.session_state.demo_seeded = seed_drafts(store, review, drafts_csv)
if st.session_state.get('demo_seeded'):
    st.sidebar.caption(f"Pre-filled {st.session_state.demo_seeded} drafts from {drafts_csv.name}")

st.title(page)
st.warning('DEMO — the evidence comes from an exported evidence table, not from an approved evidence review. '
           'Its verdicts were never approved by a person. Decisions here are stored in a separate demo '
           'database (data/demo) and never reach the real workspace.')
if page == pages[0]:
    proposal_view(store, {'review': review}, 'DEMO · ' + review['source'])
else:
    final_review_view(store, review)
