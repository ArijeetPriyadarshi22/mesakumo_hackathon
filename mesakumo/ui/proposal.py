"""Proposal Draft page: draft, read, edit and review German sections from approved evidence."""
import streamlit as st

from ..models import DisabledModel, ModelUnavailable, configure_team_one, configured_model
from ..persistence import ConflictError
from ..proposal import PROSE_KINDS, ProposalDrafts, open_items

FIT_SHORT = {'Strong match': 'Strong', 'Partial match': 'Partial', 'No match': 'No match'}


def _overview(rows, drafts):
    table = []
    for n, row in enumerate(rows, 1):
        draft = drafts.get(row['requirement']['id'])
        table.append({
            '#': n, 'Requirement': row['requirement']['text'], 'Category': row['requirement'].get('kind', ''),
            'Evidence fit': FIT_SHORT.get(row['fit'], row['fit']),
            'Draft': draft['status'] if draft else '—',
            'Open items': len(open_items(draft['text'])) if draft else None,
        })
    return table


def _source(citation, excerpt_key, used=False):
    st.markdown(f"**{citation.get('filename', '')} · {citation.get('location', '')}**"
                + (' — cited in the draft' if used else ''))
    st.text(citation.get(excerpt_key, ''))


def _run(action, *args):
    """Run a write action and show its error instead of a traceback."""
    try:
        action(*args)
        return True
    except (ValueError, ConflictError, ModelUnavailable) as error:
        st.error(str(error))
        return False


def proposal_view(store, approved):
    review = approved['review']
    service = ProposalDrafts(store, review)
    rows, corpus = review['rows'], review['corpus']
    drafts = service.all()
    numbers = {row['requirement']['id']: n for n, row in enumerate(rows, 1)}

    configure_team_one()
    model = configured_model()
    live = not isinstance(model, DisabledModel)
    if not live:
        st.info('Model access is not configured, so drafting is unavailable. Reading and editing saved drafts still works.')

    reviewer = st.text_input('Your name (reviewer)', key='proposal_reviewer')
    texts = [d['text'] for d in drafts.values()]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Requirements', len(rows))
    c2.metric('Drafted', len(drafts))
    c3.metric('Reviewed', sum(d['status'] == 'Reviewed' for d in drafts.values()))
    c4.metric('Open items', sum(len(open_items(t)) for t in texts))

    # Filters: by default show the categories that need proposal prose.
    kinds = sorted({row['requirement'].get('kind', '') for row in rows})
    f1, f2, f3 = st.columns([2, 2, 1])
    chosen_kinds = f1.multiselect('Category', kinds, default=[k for k in kinds if k in PROSE_KINDS] or kinds,
                                  key='proposal_kinds')
    fits = list(FIT_SHORT)
    chosen_fits = f2.multiselect('Evidence fit', fits, default=fits, format_func=FIT_SHORT.get, key='proposal_fits')
    only_open = f3.checkbox('Not drafted yet', key='proposal_only_open')
    shown = [row for row in rows if row['requirement'].get('kind', '') in chosen_kinds and row['fit'] in chosen_fits
             and not (only_open and row['requirement']['id'] in drafts)]
    st.dataframe(_overview(shown, drafts), hide_index=True, width='stretch')

    missing = [row for row in shown if row['requirement']['id'] not in drafts]
    calls = sum(row['fit'] != 'No match' for row in missing)
    if missing and st.button(f'Draft the {len(missing)} shown requirements without a draft ({calls} model calls)',
                             disabled=not live):
        progress, failures = st.progress(0.0), []
        for done, row in enumerate(missing, 1):
            rid = row['requirement']['id']
            progress.progress(done / len(missing), text=f'Drafting {done} of {len(missing)}: requirement {numbers[rid]}')
            try:
                service.generate(rid, model, expected_version=0)
            except (ValueError, ConflictError, ModelUnavailable) as error:
                failures.append(f'Requirement {numbers[rid]}: {error}')
        if failures:
            st.session_state['proposal_failures'] = failures
        st.rerun()
    for failure in st.session_state.pop('proposal_failures', []):
        st.warning(failure)

    if not shown:
        st.caption('No requirements match the filters.')
        return
    st.divider()
    ids = [row['requirement']['id'] for row in shown]
    rid = st.selectbox('Open a section', ids, key='proposal_open',
                       format_func=lambda key: f"{numbers[key]}. {service.rows[key]['requirement']['text'][:110]}")
    row, draft = service.rows[rid], drafts.get(rid)
    requirement = row['requirement']
    st.markdown(f"#### {numbers[rid]}. {requirement['text']}")
    st.caption(' · '.join(filter(None, [requirement.get('kind'), requirement.get('obligation_level'),
                                        f"Evidence: {row['fit']}", f"Draft: {draft['status']}" if draft else 'Not drafted'])))

    with st.expander('Tender source'):
        for citation in row.get('tender_sources', []):
            _source(citation, 'original_excerpt')
    with st.expander(f"Approved company evidence ({len(row['citation_ids'])})", expanded=not draft):
        if row.get('notes'):
            st.info('Evidence reviewer notes: ' + row['notes'])
        if row.get('gap'):
            st.warning('Gap / next action: ' + row['gap'])
        if row.get('current_action'):
            st.warning('Current action: ' + row['current_action'])
        used = set(draft.get('citation_ids', [])) if draft else set()
        for pid in row['citation_ids']:
            _source(corpus[pid], 'excerpt', used=pid in used)
        if not row['citation_ids']:
            st.caption('No company evidence was approved for this requirement.')

    if not draft:
        if st.button('Draft this section', disabled=not live, key=f'draft:{rid}'):
            with st.spinner('Drafting…'):
                if _run(service.generate, rid, model, 0):
                    st.rerun()
        return

    items = open_items(draft['text'])
    if items:
        st.warning('Open items — resolve each one and remove its [OFFEN: …] marker:\n'
                   + '\n'.join(f'- {item}' for item in items))
    st.caption(f"{draft['origin']} · updated {draft['updated_at'][:16].replace('T', ' ')} UTC"
               + (f" · reviewed by {draft['reviewer']}" if draft['status'] == 'Reviewed' else ''))
    with st.form(f"edit:{rid}:{draft['version']}"):
        title = st.text_input('Section title', draft['title'])
        text = st.text_area('Section text', draft['text'], height=260)
        save = st.form_submit_button('Save edits')
    if save and _run(service.edit, rid, title, text, draft['version']):
        st.rerun()

    b1, b2 = st.columns(2)
    if b1.button('Mark section reviewed', key=f"review:{rid}:{draft['version']}", disabled=draft['status'] == 'Reviewed'):
        if _run(service.review, rid, reviewer, draft['version']):
            st.rerun()
    if b2.button('Redraft (replaces this text and its edits)', key=f"redraft:{rid}:{draft['version']}", disabled=not live):
        with st.spinner('Drafting…'):
            if _run(service.generate, rid, model, draft['version']):
                st.rerun()
