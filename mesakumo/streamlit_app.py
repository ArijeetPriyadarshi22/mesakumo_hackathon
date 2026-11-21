"""Task 1 shell. Run with streamlit run mesakumo/streamlit_app.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
import streamlit as st
from mesakumo.discovery import ROOT, participant_directory, packs, open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.coverage import CoverageRuns as ExtractionRuns
from mesakumo.models import configure_team_one, configured_model, ModelUnavailable, DisabledModel
from mesakumo.ui.checklist import review_view
from mesakumo.ui.evidence import evidence_view, proposal_handoff

st.set_page_config(page_title='Mesakumo', layout='wide')
def begin_extraction():
    st.session_state.extraction_busy = True
    st.session_state.page = 'Tender Requirements'

busy = st.session_state.get('extraction_busy', False)
pages = ['Tender Requirements', 'Evidence Matching', 'Proposal Draft', 'Final Review & Export']
page = st.sidebar.radio('Page', pages, index=0, key='page', disabled=busy)
st.title(page)
directory = participant_directory()
with st.sidebar.expander('Local configuration'):
    st.caption(f'Participant directory: {directory}')
choices = packs(directory)
if not choices:
    st.error('No top-level Ausschreibung folders found. Set MESAKUMO_PARTICIPANT_DIR and restart.')
    st.stop()
pack_name = st.sidebar.selectbox('Tender pack', [p.name for p in choices], disabled=busy, key='selected_tender')
pack = next(p for p in choices if p.name == pack_name)
phase = st.sidebar.selectbox('Phase', ['participation', 'offer'], disabled=busy) if pack.name == 'Ausschreibung 3' else 'single'
actions = st.sidebar.columns(2)
actions[0].button('Rescan', disabled=busy)
start = actions[1].button('Start extraction', type='primary', disabled=busy, on_click=begin_extraction)
if os.environ.get('MESAKUMO_KEEP_SESSION') != 'yes':
    from uuid import uuid4
    if 'fresh_workspace_v3' not in st.session_state:
        st.session_state.fresh_workspace_v3 = uuid4().hex
    workspace_dir = str(Path(os.environ.get('MESAKUMO_DATA_DIR', 'data')) / 'sessions' / st.session_state.fresh_workspace_v3)
else:
    workspace_dir = os.environ.get('MESAKUMO_DATA_DIR', 'data')
data = Path(workspace_dir)
if not data.is_absolute(): data = ROOT / data
if not data.resolve().is_relative_to(ROOT):
    st.error('MESAKUMO_DATA_DIR must remain inside the repository.')
    st.stop()
store = ProjectStore(data / 'workspace.sqlite3')
# Parsed documents are shared by all sessions (and survive restarts), so only the first
# visit to a tender pack reads its files; show progress while that happens.
base_data = Path(os.environ.get('MESAKUMO_DATA_DIR', 'data'))
parse_cache = (base_data if base_data.is_absolute() else ROOT / base_data) / 'parse_cache'
reading = st.empty()
def show_reading(done, total, name):
    if done < total:
        reading.progress(done / total, text=f'Reading tender documents for {pack.name}: {done + 1} of {total} — {name}')
project, rows = open_selection(store, pack, phase, cache_dir=parse_cache, on_progress=show_reading)
reading.empty()
st.subheader(project.name)
st.caption('Results are isolated by tender and phase. ' +
    ('Workspace persistence enabled.' if os.environ.get('MESAKUMO_KEEP_SESSION') == 'yes' else 'A new browser session starts fresh.'))
if page == pages[0]:
    configure_team_one()
    runs = ExtractionRuns(store)
    saved = runs.get(project, rows)
    retry = bool(saved)
    if saved and saved['status'] in ('failed', 'running'):
        st.sidebar.caption('A previous attempt stopped. Start extraction retries it.')
    result = saved
    error_key = f'extraction_error_{project.id}'
    if error_key in st.session_state:
        st.error(st.session_state.pop(error_key))

    @st.dialog('Extracting tender requirements', dismissible=False)
    def extraction_dialog():
        st.write('Please wait. This may take several minutes. Controls will return when extraction finishes.')
        progress = st.empty()
        progress.info('Preparing the selected tender…')

        def update_progress(state):
            progress.info(f"{project.name} · {len(state['processed'])} / {state.get('total_batches', '?')} batches processed · {len(state['candidates'])} candidates")

        try:
            model = configured_model()
            if isinstance(model, DisabledModel):
                raise ModelUnavailable('Extraction is disabled. Configure Requesty access and click Start extraction again.')
            with st.spinner('Reading documents and extracting requirements…', show_time=True):
                runs.ensure(project, rows, model, retry=retry, on_progress=update_progress)
        except (ModelUnavailable, ValueError) as exc:
            st.session_state[error_key] = str(exc)
        except Exception:
            st.session_state[error_key] = 'Extraction could not finish. Please retry extraction.'
        finally:
            st.session_state.extraction_busy = False
        st.rerun(scope='app')

    if start:
        extraction_dialog()
        st.stop()
    if result and result['status'] == 'failed':
        st.warning('Extraction stopped. Click Start extraction beside Rescan to retry, or add requirements manually below.')
        with st.expander('Why extraction stopped'):
            error = result.get('error', 'No failure detail was recorded.')
            if error == 'Model request failed or returned an invalid JSON action; explicit retry required':
                st.write('This saved attempt failed before the old error handler recorded a specific cause. It could have been a connection, authentication or response-format error. Retry to get a fresh diagnostic.')
            else:
                st.error(error)
            st.caption(f"Saved attempt {result.get('attempt', 1)} · {result['steps']} completed actions. Reopening does not run extraction again.")
    elif result and result['status'] == 'partial':
        st.warning(f"Extraction saved {len(result['candidates'])} requirement candidates, but coverage is incomplete: "
            f"{len(result.get('gaps', []))} unprocessed source batches and "
            f"{len(result.get('unreadable_documents', []))} documents without readable passages. "
            'Review the coverage gaps before approving the checklist.')
        st.caption(f"Extraction partial · {result['steps']} actions")
    elif result:
        st.caption(f"Extraction {result['status']} · {result['steps']} actions")
    else:
        st.info('Ready to extract requirements. Click Start extraction beside Rescan in the sidebar. You can also add requirements manually below.')
    review_view(store,store.load(project.id),rows)
    with st.expander('Documents and reading status'):
        st.dataframe([{'File':r['path'], 'Role':r['role'], 'Reading status':r['status'],
            'Details':r['error']} for r in rows], hide_index=True, width='stretch')
        st.caption('ZIP members are included in the offer phase. Identical source aliases are counted once.')
    if result:
        st.caption(f'Extraction log: {store.path.parent / "extraction.jsonl"}')
        with st.expander('Execution trace'):
            st.json(result['trace'])
elif page == pages[1]:
    evidence_view(store, project, rows, directory, phase)
elif page == pages[2]:
    proposal_handoff(store, project, rows, directory)
else:
    from mesakumo.ui.proposal import final_review_handoff
    final_review_handoff(store, project, rows, directory)
