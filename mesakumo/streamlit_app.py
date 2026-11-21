"""Task 1 shell. Run with streamlit run mesakumo/streamlit_app.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
import streamlit as st
from mesakumo.discovery import ROOT, participant_directory, packs, open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.agent import ExtractionRuns, sources
from mesakumo.models import configure_team_one, configured_model, ModelUnavailable

st.set_page_config(page_title='Mesakumo', layout='wide')
pages = ['Tender Requirements', 'Evidence Matching', 'Proposal Draft', 'Final Review & Export']
page = st.sidebar.radio('Page', pages, index=0)
st.title(page)
directory = participant_directory()
st.sidebar.caption(f'Participant directory: {directory}')
choices = packs(directory)
if not choices:
    st.error('No top-level Ausschreibung folders found. Set MESAKUMO_PARTICIPANT_DIR and restart.')
    st.stop()
pack_name = st.sidebar.selectbox('Tender pack', [p.name for p in choices])
pack = next(p for p in choices if p.name == pack_name)
phase = st.sidebar.selectbox('Phase', ['participation', 'offer']) if pack.name == 'Ausschreibung 3' else 'single'
st.sidebar.button('Rescan local files')
data = Path(os.environ.get('MESAKUMO_DATA_DIR', 'data'))
if not data.is_absolute(): data = ROOT / data
if not data.resolve().is_relative_to(ROOT):
    st.error('MESAKUMO_DATA_DIR must remain inside the repository.')
    st.stop()
store = ProjectStore(data / 'workspace.sqlite3')
project, rows = open_selection(store, pack, phase)
st.caption(f'{project.name} · Project {project.id} · Local sources; extraction status below')
if page == pages[0]:
    st.write('Inspect the inventory and source-reading status before working with requirements.')
    st.dataframe(rows, width='stretch')
    st.caption(f'{len(rows)} inventory entries · {len({r["source_id"] for r in rows if r["source_id"]})} distinct sources. Unsupported files and aliases remain visible. ZIP members are available in the offer phase.')
    names = {d.id: d.name for d in project.documents}
    for passage in project.passages:
        with st.expander(f'{names.get(passage.document_id, passage.document_id)} · {passage.location}'):
            st.text(passage.excerpt)
    configure_team_one()
    runs = ExtractionRuns(store)
    saved = runs.get(project, rows)
    retry = st.button('Retry failed extraction') if saved and saved['status'] in ('failed', 'running') else False
    progress = st.empty()
    document_progress = st.empty()
    current_docs, current_passages, current_batches = sources(project, rows)
    def update_progress(state):
        progress.info(f"Extraction: {state['status']} · {state['steps']} actions · {len(state['processed'])} batches processed")
        document_progress.dataframe([{'document': d.name, 'reading_status': d.status,
            'read': sum(bid in state['read'] for bid,b in current_batches.items() if b['document_id']==did),
            'processed': sum(bid in state['processed'] for bid,b in current_batches.items() if b['document_id']==did),
            'total': sum(b['document_id']==did for b in current_batches.values())} for did,d in current_docs.items()], width='stretch')
    try:
        model = configured_model()
        result = runs.ensure(project, rows, model, retry=retry,
            on_progress=update_progress)
    except (ModelUnavailable, ValueError) as exc:
        st.error(str(exc))
        result = saved
    if result:
        document_progress.empty()
        progress.info(f"Extraction: {result['status']} · {result['steps']} actions")
        if result.get('error'): st.error(result['error'])
        docs, passages, batches = sources(project, rows)
        st.dataframe([{'document': d.name, 'reading_status':d.status,
            'batches_read':sum(bid in result['read'] for bid,b in batches.items() if b['document_id']==did),
            'batches_processed':sum(bid in result['processed'] for bid,b in batches.items() if b['document_id']==did),
            'batches_total':sum(b['document_id']==did for b in batches.values())} for did,d in docs.items()], width='stretch')
        st.warning(f"Coverage gaps: {sum(b not in result['processed'] for b in batches)} unprocessed batches; {sum(not any(p.document_id == did for p in passages) for did in docs)} documents without readable passages. Read coverage does not prove requirement completeness.")
        st.subheader('Candidate checklist — requires human review')
        project = store.load(project.id)
        for requirement in project.requirements:
            with st.expander(requirement.text):
                st.write(requirement.kind, requirement.constraints)
                st.write('Supporting passage IDs:', requirement.source_ids)
                for sid in requirement.source_ids:
                    passage = next((p for p in project.passages if p.id == sid), None)
                    if passage: st.text(passage.excerpt)
        for conflict in result.get('conflicts', []): st.warning(str(conflict))
        with st.expander('Execution trace'):
            st.json(result['trace'])
    else:
        st.info('Model disabled. Configure Requesty EU or provide the authorized local Team 1 access file. No extraction has run.')
else:
    st.info({'Evidence Matching': 'Evidence matching is not implemented. Existing evidence records and review logic are preserved.', 'Proposal Draft': 'Proposal generation is not implemented.', 'Final Review & Export': 'Existing approval logic is preserved. Corporate export is not implemented.'}[page])
