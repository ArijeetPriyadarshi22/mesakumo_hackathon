"""Approved checklist handoff, matching, and evidence review."""
import streamlit as st

from ..checklist import ChecklistReview
from ..evidence import evidence_inventory, read_evidence
from ..evidence_matching import GRADES, match_evidence
from ..evidence_review import EvidenceReview, coverage_issue, documents_current, review_table
from ..models import configure_team_one, configured_model


def evidence_view(store, project, rows, directory, phase):
    try:
        approved = ChecklistReview(store).current(project, rows)
        if approved is None:
            st.warning('Evidence Matching is locked. Approve and Save the current checklist on Tender Requirements first.')
            return
        inventory = evidence_inventory()
        service = EvidenceReview(store, approved)
        draft = service.load()
        _evidence_view(service, draft, approved, project, directory, phase, inventory)
    except (OSError, ValueError) as exc:
        st.error(str(exc))


def _evidence_view(service, draft, approved, project, directory, phase, inventory):
    revision = approved['revision']
    st.success(f"Received approved checklist revision {revision} · {project.name}")
    st.caption(f"Project {approved['project_id']} · Phase {phase} · {len(service.requirements)} approved proposal requirements")
    with st.expander('Approved requirements'):
        st.dataframe([{'Requirement ID': row['id'], 'Requirement': row['text']}
                      for row in service.requirements], hide_index=True, width='stretch')
    if not service.requirements:
        st.info('This checklist has no proposal requirements to match.')
        return

    st.subheader('Evidence sources')
    scope = f'{project.id}:{phase}:{revision}:{service.store.path}'
    selection_key = 'evidence_selection:' + scope
    available = [path for path in draft['selected'] if path in inventory]
    if available != draft['selected']:
        draft = service.select(draft['review_revision'], available)
    st.session_state[selection_key] = available
    selection_error = 'evidence_selection_error:' + scope
    if selection_error in st.session_state:
        st.error(st.session_state.pop(selection_error))

    def set_selection(paths=None):
        try:
            requested = st.session_state[selection_key] if paths is None else paths
            if set(requested) != set(draft['selected']):
                service.select(draft['review_revision'], requested)
        except ValueError as exc:
            st.session_state[selection_error] = str(exc)

    left, right = st.columns(2)
    left.button('Select all eligible', disabled=not inventory, on_click=set_selection, args=(list(inventory),))
    right.button('Clear selection', disabled=not available, on_click=set_selection, args=([],))
    selected = st.multiselect('Select evidence files', list(inventory), key=selection_key,
                              format_func=lambda path: f'{inventory[path].filename} · {inventory[path].role}',
                              on_change=set_selection)
    st.caption('Historical proposals are past bid material. Verify their claims and commitments before reuse.')

    def get_model():
        configure_team_one()
        return configured_model()

    start = st.button('Start Evidence Matching', type='primary', disabled=not selected)
    has_gaps = 'rows' in draft and coverage_issue(draft)
    retry = bool(has_gaps) and st.button('Retry Evidence Matching')
    if start or retry:
        progress = st.progress(0, text='Reading selected evidence files…')

        def update_progress(done, total):
            progress.progress(done / max(total, 1), text=f'Evidence lookups attempted: {done} of {total}')

        readings = read_evidence(directory, inventory, selected)
        run = match_evidence(approved, phase, readings, get_model, on_progress=update_progress,
                             confirm_all=bool(retry))
        service.save_run(draft['review_revision'], run, readings, directory)
        st.rerun()

    if not selected:
        return
    statuses = {doc['relative_path']: doc for doc in draft.get('documents', [])}
    st.dataframe([{
        'File': inventory[path].filename, 'Role': inventory[path].role,
        'Reading status': statuses.get(path, {}).get('status', 'Ready to read'),
        'Details': statuses.get(path, {}).get('detail', ''),
    } for path in selected], hide_index=True, width='stretch')
    if 'rows' not in draft:
        return
    fresh = documents_current(draft, directory, inventory)
    if not fresh:
        st.warning('Selected evidence files changed or could not be verified. Run matching again before approval.')
    render_review(service, draft, directory, inventory, scope, fresh)
    with st.expander('Selected document versions'):
        st.dataframe(draft['documents'], hide_index=True, width='stretch')

    corpus = draft['corpus']
    if not corpus:
        st.warning('No readable evidence was found in the selection.')
        return
    with st.expander('Local evidence corpus'):
        paths = list(dict.fromkeys(citation['relative_path'] for citation in corpus.values()))
        source = st.selectbox('Preview evidence file', paths, key='evidence_file:' + scope,
                             format_func=lambda path: f'{inventory[path].filename} · {inventory[path].role}')
        ids = [pid for pid, citation in corpus.items() if citation['relative_path'] == source]
        location = st.selectbox('Source location', ids, format_func=lambda pid: corpus[pid]['location'],
                                key=f"evidence_location:{scope}:{source}:{draft['review_revision']}")
        st.text(corpus[location]['excerpt'])


def _source(citation, excerpt_key):
    st.text(f"{citation['filename']} · {citation['location']}")
    st.caption(f"{citation['passage_id']} · {citation['document_id']} · version {citation['document_version']}")
    if citation.get('role'):
        st.caption(citation['role'])
    st.text(citation[excerpt_key])


def render_review(service, draft, directory, inventory, scope, fresh):
    st.subheader('Evidence review')
    st.dataframe(review_table(draft), hide_index=True, width='stretch')
    by_id = {row['requirement']['id']: row for row in draft['rows']}
    issue = coverage_issue(draft)
    if issue:
        st.warning(issue)
    if not by_id:
        return

    rid = st.selectbox('Read evidence and its sources', list(by_id), key='read_evidence:' + scope,
                       format_func=lambda key: f"{key} · {by_id[key]['requirement']['text'][:100]}")
    row = by_id[rid]
    st.text(row['requirement']['text'])
    for constraint in row['requirement'].get('constraints', []):
        st.text(constraint)
    with st.expander('Original tender citations'):
        for citation in row['tender_sources']:
            _source(citation, 'original_excerpt')
    with st.expander('Historical evidence citations', expanded=True):
        for pid in row['citation_ids']:
            _source(draft['corpus'][pid], 'excerpt')
        if not row['citation_ids']:
            st.caption('No evidence citations on this row.')
    scope = {'retrieval': 'Retrieved passages', 'full_readable_scan': 'All readable selected passages',
             'current_action': 'Current action required', 'incomplete': 'Incomplete, findings are provisional'}
    if row.get('search_scope') in scope:
        st.caption(f"Search coverage: {scope[row['search_scope']]}")
    if row['current_action']:
        st.info(row['current_action'])
    with st.expander('Evidence tool trace'):
        st.json(row['trace'])

    with st.form(f"edit_evidence:{scope}:{draft['review_revision']}:{rid}"):
        options = ('Choose fit', *GRADES)
        fit = st.selectbox('Evidence fit', options, index=options.index(row['fit'] or 'Choose fit'))
        notes = st.text_area('Evidence notes', row['notes'])
        gap = st.text_area('Gap / next action', row['gap'])
        corpus = draft['corpus']
        citations = st.multiselect('Evidence citations', list(corpus), default=row['citation_ids'],
                                   format_func=lambda pid: f"{corpus[pid]['filename']} · {corpus[pid]['location']} · {pid}")
        save = st.form_submit_button('Save evidence review', disabled=not fresh)
    if save:
        service.edit(draft['review_revision'], rid, fit, notes, gap, citations)
        st.rerun()

    approved = service.current(directory, inventory) if fresh else None
    st.subheader('Evidence approval')
    if approved:
        st.success(f"Approved evidence review revision {draft['review_revision']} by {approved['reviewer']}")
        st.download_button('Download approved evidence CSV', approved['csv'], approved['filename'],
                           mime='text/csv; charset=utf-8')
        st.button('Continue to Proposal Draft', on_click=lambda: st.session_state.update(page='Proposal Draft'))
    else:
        if not fresh:
            st.caption('Run matching on the current evidence files before approval.')
        elif issue:
            st.caption('Approval will carry unresolved items into Proposal Draft.')
        with st.form(f"approve_evidence:{scope}:{draft['review_revision']}"):
            reviewer = st.text_input('Evidence reviewer')
            acknowledge = st.checkbox('I acknowledge open No match items')
            approve = st.form_submit_button('Approve and Save Evidence')
        if approve:
            try:
                service.approve(draft['review_revision'], directory, inventory, reviewer, acknowledge)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.rerun()


def proposal_handoff(store, project, rows, directory):
    try:
        checklist = ChecklistReview(store).current(project, rows)
        service = EvidenceReview(store, checklist) if checklist else None
        if service is not None and not service.requirements:
            st.info('This checklist has no proposal requirements for drafting.')
            return
        inventory = evidence_inventory() if service else None
        approved = service.current(directory, inventory) if service else None
        if approved:
            draft = approved['review']
            st.success(f"Received approved evidence review revision {draft['review_revision']} "
                       f"for checklist revision {draft['checklist_revision']} · {draft['phase']} · "
                       f"{len(draft['rows'])} requirements")
            from .proposal import proposal_view
            proposal_view(store, approved, project.name, directory=directory, inventory=inventory)
        else:
            st.warning('Proposal Draft is locked. Approve and Save Evidence for the current inputs first.')
    except (OSError, ValueError) as exc:
        st.error(str(exc))
