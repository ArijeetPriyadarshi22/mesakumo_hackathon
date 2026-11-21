"""Streamlit checklist review forms and revision-bound download."""
import streamlit as st
from ..checklist import ChecklistReview, LEVELS
from ..requirements import KINDS
from ..domain import ADMIN_SCOPE, REQUIREMENT_SCOPES
from ..agent import sources, ExtractionRuns


def review_view(store, project, rows):
    service = ChecklistReview(store)
    docs, passages, batches = sources(project,rows)
    choices = {p.id:p for p in passages}
    key = f'rendered-review-revision:{project.id}'
    expected = st.session_state.get(key,project.revision)
    run = ExtractionRuns(store).get(project,rows)
    processed = set(run['processed']) if run else set()
    gaps = sum(b not in processed for b in batches)
    unreadable = sum(not any(p.document_id==did for p in passages) for did in docs)
    st.subheader('Requirements')
    st.caption(f'{len(project.requirements)} requirements · Revision {project.revision} · '
               f'{gaps} unprocessed source batches · {unreadable} unreadable or unsupported files')
    import pandas as pd
    columns = ['statement','category','obligation_level','exact_constraints','source_references','requirement_id']
    query = st.text_input('Search requirements', key=f'filter:{project.id}').strip().casefold()
    visible = [r for r in project.requirements if not query or query in (r.text + ' ' + r.kind + ' ' + '; '.join(r.constraints)).casefold()]
    visible.sort(key=lambda requirement: requirement.scope == ADMIN_SCOPE)
    st.caption(f'Showing {len(visible)} of {len(project.requirements)} requirements. Approval applies to the complete checklist.')
    table = [{'statement':r.text, 'category':r.kind, 'obligation_level':r.obligation_level,
        'exact_constraints':'; '.join(r.constraints),
        'source_references':'; '.join(f'{docs[choices[s].document_id].name} ({choices[s].location})' if s in choices else f'{s} (historical source)' for s in r.source_ids),
        'requirement_id':r.id} for r in visible]
    proposal = [row for row, item in zip(table, visible) if item.scope != ADMIN_SCOPE]
    administrative = [row for row, item in zip(table, visible) if item.scope == ADMIN_SCOPE]
    st.caption('Statement = checkable requirement; Category = type; Obligation = reviewer classification; '
        'Exact constraints = dates, quantities and conditions; Sources = original document and location. '
        'Unspecified obligation levels still need review.')
    def show_requirements(entries):
        st.dataframe(pd.DataFrame(entries, columns=columns), hide_index=True, width='stretch',
            column_order=['requirement_id','statement','category','source_references'],
            column_config={'requirement_id':st.column_config.TextColumn('Requirement ID'),
                'statement':st.column_config.TextColumn('Requirement',width='large'),
                'category':st.column_config.TextColumn('Category',width='small'),
                'source_references':st.column_config.TextColumn('Source document / location',width='large')})

    st.markdown('**Proposal requirements**')
    show_requirements(proposal)
    with st.expander(f'General & administrative requirements ({len(administrative)})'):
        if administrative:
            show_requirements(administrative)
        else:
            st.caption('No administrative requirements to show.')
    if visible:
        records = {r.id:r for r in visible}
        selected_detail = st.selectbox('Read requirement and its sources',list(records),
            format_func=lambda rid: f'{list(records).index(rid)+1}. {records[rid].text[:100]}',
            key=f'detail:{project.id}:{project.revision}')
        item = records[selected_detail]
        with st.container(border=True):
            st.markdown('**Requirement**')
            st.write(item.text)
            st.write(f'{item.scope} · Category: {item.kind} · Obligation: {item.obligation_level}')
            st.markdown('**Exact constraints**')
            st.write('; '.join(item.constraints) or 'None recorded')
            st.markdown('**Original sources**')
            for index, sid in enumerate(item.source_ids):
                if sid not in choices:
                    st.warning('This citation is no longer current. Correct it before approval.')
                    continue
                passage = choices[sid]
                st.write(f'{project.name} / {docs[passage.document_id].name} — {passage.location}')
                if index < len(item.original_excerpts):
                    st.markdown('**Supporting quotation**')
                    st.write(item.original_excerpts[index])
                with st.expander('Read full original passage · ' + passage.location):
                    st.write(passage.excerpt)
                st.caption(f'Passage {sid} · version {passage.document_version}')
            st.caption(item.provenance)
    if project.requirements and not visible:
        st.info('No requirements match this search.')
    if not project.requirements:
        st.info('No requirements yet. Retry extraction or add a requirement from a source passage below.')
    with st.expander('Coverage and extraction gaps'):
        st.write(f'{gaps} source batches have not been processed. Review the full tender before approval.')
        st.dataframe([{'Document':d.name,'Reading status':d.status,
            'Processed batches':sum(bid in processed for bid,b in batches.items() if b['document_id']==did),
            'Total batches':sum(b['document_id']==did for b in batches.values())} for did,d in docs.items()],hide_index=True,width='stretch')
        for conflict in run.get('conflicts',[]) if run else []: st.warning(str(conflict))
        for failure in run.get('batch_errors',[]) if run else []:
            st.error(f"Unprocessed batch {failure['batch_id']}: {failure['error']}")
    st.caption('Save each edit before approval. Approve and Save exports the saved table.')
    with st.expander('Add or edit a requirement'):
        records = {r.id:r for r in project.requirements}
        selected = st.selectbox('Requirement to edit', ['Add new requirement']+list(records), format_func=lambda rid: (records[rid].text[:90] if rid in records else rid), key=f'edit-choice:{project.id}')
        item = records.get(selected)
        formkey = f'requirement:{project.id}:{project.revision}:{selected}'
        with st.form(formkey):
            st.caption('Stable requirement ID: '+(item.id if item else 'Assigned on save'))
            text = st.text_area('Statement',value=item.text if item else '')
            category = st.selectbox('Category',KINDS,index=KINDS.index(item.kind) if item else 0)
            scope = st.selectbox('Requirement scope', REQUIREMENT_SCOPES,
                                 index=REQUIREMENT_SCOPES.index(item.scope) if item else 0)
            level = st.selectbox('Obligation level',LEVELS,index=LEVELS.index(item.obligation_level) if item else 0)
            constraints = st.text_area('Exact constraints (one per line)',value='\n'.join(item.constraints) if item else '')
            citations = st.multiselect('Source references',list(choices),default=[s for s in item.source_ids if s in choices] if item else [],
                format_func=lambda sid:f'{docs[choices[sid].document_id].name} · {choices[sid].location}')
            reviewer = st.text_input('Reviewer')
            note = st.text_input('Human provenance / reason for change')
            save = st.form_submit_button('Save requirement')
            remove = st.form_submit_button('Remove requirement',disabled=item is None)
        try:
            if save:
                service.edit(project.id,expected,rows,item.id if item else '',text,category,level,citations,
                    [c for c in constraints.splitlines() if c.strip()],reviewer,note,scope=scope)
                st.session_state[key] = store.load(project.id).revision
                st.rerun()
            if remove:
                service.remove(project.id,expected,item.id,reviewer)
                st.session_state[key] = store.load(project.id).revision
                st.session_state.pop(f'edit-choice:{project.id}',None)
                st.rerun()
        except ValueError as exc: st.error(str(exc))
    with st.expander('Original source view'):
        if choices:
            sid = st.selectbox('Inspect source passage',list(choices),format_func=lambda sid:f'{docs[choices[sid].document_id].name} · {choices[sid].location}',key=f'source-view:{project.id}')
            passage = choices[sid]
            st.write('Filename:',docs[passage.document_id].name)
            st.write('Page / paragraph / lines:',passage.location)
            st.caption(f'Passage {sid} · source version {passage.document_version}')
            st.text(passage.excerpt)
    try: approved = service.current(project,rows)
    except ValueError as exc:
        st.error(str(exc)); approved = None
    st.subheader('Approval')
    with st.form(f'approve:{project.id}:{project.revision}'):
        approver = st.text_input('Approving reviewer')
        acknowledge = st.checkbox('I reviewed the checklist and acknowledge the source/extraction gaps')
        submit = st.form_submit_button('Approve and Save',disabled=not project.requirements or approved is not None)
    if submit:
        try:
            service.approve(project.id,expected,rows,approver,acknowledge)
            st.session_state[key] = store.load(project.id).revision
            st.rerun()
        except ValueError as exc: st.error(str(exc))
    if approved:
        st.success(f"Approved and saved checklist revision {approved['revision']} by {approved['reviewer']}")
        st.download_button('Download approved checklist CSV',approved['csv'],file_name=approved['filename'],mime='text/csv; charset=utf-8')
    else:
        st.info('Fresh approval and a saved CSV are required before Evidence Matching.')
    st.button('Continue to Evidence Matching',disabled=approved is None,
        on_click=lambda: st.session_state.update(page='Evidence Matching'))
    st.session_state[key] = project.revision
