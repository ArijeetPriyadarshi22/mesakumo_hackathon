"""Task 1 shell. Run with streamlit run mesakumo/streamlit_app.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
import streamlit as st
from mesakumo.discovery import ROOT, participant_directory, packs, open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.agent import ExtractionRuns, sources
from mesakumo.models import configure_team_one, configured_model, ModelUnavailable, DisabledModel
from mesakumo.ui.checklist import review_view
from mesakumo.checklist import ChecklistReview

st.set_page_config(page_title='Mesakumo', layout='wide')
def begin_extraction():
    st.session_state.extraction_busy = True

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
pack_name = st.sidebar.selectbox('Tender pack', [p.name for p in choices], disabled=busy)
pack = next(p for p in choices if p.name == pack_name)
phase = st.sidebar.selectbox('Phase', ['participation', 'offer'], disabled=busy) if pack.name == 'Ausschreibung 3' else 'single'
st.sidebar.button('Rescan local files', disabled=busy)
data = Path(os.environ.get('MESAKUMO_DATA_DIR', 'data'))
if not data.is_absolute(): data = ROOT / data
if not data.resolve().is_relative_to(ROOT):
    st.error('MESAKUMO_DATA_DIR must remain inside the repository.')
    st.stop()
store = ProjectStore(data / 'workspace.sqlite3')
project, rows = open_selection(store, pack, phase)
st.caption(project.name)
if page == pages[0]:
    configure_team_one()
    runs = ExtractionRuns(store)
    saved = runs.get(project, rows)
    retry = bool(saved and saved['status'] in ('failed', 'running'))
    start = st.button('Retry failed extraction' if retry else 'Start extraction',
        type='primary', disabled=busy or bool(saved and saved['status'] in ('completed', 'partial')),
        key=f'extract_{project.id}', on_click=begin_extraction)
    progress = st.empty()
    document_progress = st.empty()
    current_docs, current_passages, current_batches = sources(project, rows)
    def update_progress(state):
        progress.info(f"Extraction: {state['status']} · {state['steps']} actions · {len(state['processed'])} batches processed")
        document_progress.dataframe([{'document': d.name, 'reading_status': d.status,
            'read': sum(bid in state['read'] for bid,b in current_batches.items() if b['document_id']==did),
            'processed': sum(bid in state['processed'] for bid,b in current_batches.items() if b['document_id']==did),
            'total': sum(b['document_id']==did for b in current_batches.values())} for did,d in current_docs.items()], width='stretch')
    result = saved
    error_key = f'extraction_error_{project.id}'
    if error_key in st.session_state:
        st.error(st.session_state.pop(error_key))
    if start:
        blocker = st.empty()
        blocker.markdown("""
<style>
.extraction-blocker {position:fixed; inset:0; z-index:999999;
    background:rgba(0,0,0,.65); display:flex; align-items:center;
    justify-content:center; cursor:wait; pointer-events:auto;}
.extraction-notice {background:#182332; color:white; padding:2rem;
    border-radius:12px; text-align:center; max-width:460px;}
.extraction-wheel {width:36px; height:36px; margin:0 auto 1rem;
    border:4px solid #64748b; border-top-color:#60a5fa;
    border-radius:50%; animation:extraction-spin 1s linear infinite;}
@keyframes extraction-spin {to {transform:rotate(360deg);}}
@media (prefers-reduced-motion:reduce) {.extraction-wheel {animation:none;}}
</style>
<div class="extraction-blocker" role="status" aria-live="polite" aria-busy="true">
<div class="extraction-notice"><div class="extraction-wheel"></div>
<strong>Extracting tender requirements…</strong>
<p>Please wait. This may take several minutes.<br>Controls will return when extraction finishes.</p>
</div></div>
""", unsafe_allow_html=True)
        try:
            model = configured_model()
            if isinstance(model, DisabledModel):
                raise ModelUnavailable('Extraction is disabled. Configure Requesty access and click Start extraction again.')
            with st.spinner('Extracting tender requirements…', show_time=True):
                result = runs.ensure(project, rows, model, retry=retry,
                    on_progress=update_progress)
        except (ModelUnavailable, ValueError) as exc:
            st.session_state[error_key] = str(exc)
        except Exception:
            st.session_state[error_key] = 'Extraction could not finish. Please retry extraction.'
        finally:
            st.session_state.extraction_busy = False
            blocker.empty()
        st.rerun()
    document_progress.empty()
    progress.empty()
    if result and result['status'] == 'failed':
        st.warning('Extraction stopped. Retry extraction or add requirements manually below.')
        with st.expander('Why extraction stopped'):
            error = result.get('error', 'No failure detail was recorded.')
            if error == 'Model request failed or returned an invalid JSON action; explicit retry required':
                st.write('This saved attempt failed before the old error handler recorded a specific cause. It could have been a connection, authentication or response-format error. Retry to get a fresh diagnostic.')
            else:
                st.error(error)
            st.caption(f"Saved attempt {result.get('attempt', 1)} · {result['steps']} completed actions. Reopening does not run extraction again.")
    elif result:
        st.caption(f"Extraction {result['status']} · {result['steps']} actions")
    else:
        st.info('Ready to extract requirements. Click Start extraction for the selected tender. You can also add requirements manually below.')
    review_view(store,store.load(project.id),rows)
    with st.expander('Documents and reading status'):
        st.dataframe([{'File':r['path'], 'Role':r['role'], 'Reading status':r['status'],
            'Details':r['error']} for r in rows], hide_index=True, width='stretch')
        st.caption('ZIP members are included in the offer phase. Identical source aliases are counted once.')
    if result:
        with st.expander('Execution trace'):
            st.json(result['trace'])
elif page == pages[1]:
    try:
        approved = ChecklistReview(store).current(project,rows)
    except ValueError as exc:
        st.error(str(exc)); approved = None
    if approved is None:
        st.warning('Evidence Matching is locked. Approve and Save the current checklist on Tender Requirements first.')
    else:
        st.success(f"Received approved checklist revision {approved['revision']} · {project.name}")
        st.dataframe(approved['rows'],width='stretch')
        st.info('Evidence retrieval is not implemented; it belongs to a later task.')
else:
    st.info({'Proposal Draft': 'Proposal generation is not implemented.', 'Final Review & Export': 'Final proposal review and corporate export are not implemented.'}[page])
