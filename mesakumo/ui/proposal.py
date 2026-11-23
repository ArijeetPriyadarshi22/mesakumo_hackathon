"""Proposal Draft and Final Review pages: draft, decide on each section, approve the draft for the PPTX.

Workflow: a section is drafted → the reviewer approves it or declines it with a comment →
a declined section is redrafted using that comment → once the wanted sections are approved,
the reviewer approves the draft as a package → Final Review & Export receives the package
(CSV for the PPTX-compiling model, and a Project for the PPTX exporter) for PPTX generation
and the second review.
"""
import json

import streamlit as st

from ..models import DisabledModel, ModelUnavailable, configure_team_one, configured_model
from ..persistence import ConflictError
from ..proposal import (PROSE_KINDS, ProposalDrafts, check_wording, handoff_csv, handoff_project, open_items,
                        review_summary)

FIT_SHORT = {'Strong match': 'Strong', 'Partial match': 'Partial', 'No match': 'No match'}
STATUS_ICON = {'Approved': '✅ Approved', 'Declined': '❌ Declined', 'Draft': '📝 To review',
               'Edited': '✏️ Edited — to review', None: '— Not drafted'}


def _overview(rows, drafts):
    table = []
    for n, row in enumerate(rows, 1):
        draft = drafts.get(row['requirement']['id'])
        table.append({
            '#': n, 'Requirement': row['requirement']['text'], 'Category': row['requirement'].get('kind', ''),
            'Evidence fit': FIT_SHORT.get(row['fit'], row['fit']),
            'Status': STATUS_ICON[draft['status'] if draft else None],
            'Open items': len(open_items(draft['text'])) if draft else None,
            'Check wording': ', '.join(check_wording(draft['text'])) if draft else '',
        })
    return table


def _source(citation, excerpt_key, used=False):
    st.markdown(f"**{citation.get('filename', '')} · {citation.get('location', '')}**"
                + (' — cited in the draft' if used else ''))
    st.text(citation.get(excerpt_key, ''))


def _run(action, *args, **kwargs):
    """Run a write action and show its error instead of a traceback."""
    try:
        action(*args, **kwargs)
        return True
    except (ValueError, ConflictError, ModelUnavailable) as error:
        st.error(str(error))
        return False


def proposal_view(store, approved, project_name=''):
    review = approved['review']
    service = ProposalDrafts(store, review)
    rows, corpus = review['rows'], review['corpus']
    drafts = service.all()
    numbers = {row['requirement']['id']: n for n, row in enumerate(rows, 1)}

    configure_team_one()
    model = configured_model()
    live = not isinstance(model, DisabledModel)
    if not live:
        st.info('Model access is not configured, so drafting is unavailable. Reading, editing and deciding on '
                'saved drafts still works.')
    reviewer = st.text_input('Your name (reviewer)', key='proposal_reviewer')

    # Review summary for the person who approves.
    statuses = [d['status'] for d in drafts.values()]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric('Requirements', len(rows))
    c2.metric('To review', sum(s in ('Draft', 'Edited') for s in statuses))
    c3.metric('Approved', statuses.count('Approved'))
    c4.metric('Declined', statuses.count('Declined'))
    c5.metric('Not drafted', len(rows) - len(drafts))
    summary = review_summary(review, drafts, project_name)
    with st.expander('Review summary — what needs attention'):
        attention = summary.split('## Needs attention', 1)[1].split('## Sections', 1)[0].strip()
        st.markdown(attention)
        st.download_button('Download review summary (Markdown)', summary.encode('utf-8'),
                           f"{review['project_id']}-draft-review.md", mime='text/markdown')

    # Filters: by default show the categories that need proposal prose.
    kinds = sorted({row['requirement'].get('kind', '') for row in rows})
    f1, f2, f3 = st.columns([2, 2, 1])
    chosen_kinds = f1.multiselect('Category', kinds, default=[k for k in kinds if k in PROSE_KINDS] or kinds,
                                  key='proposal_kinds')
    fits = list(FIT_SHORT)
    chosen_fits = f2.multiselect('Evidence fit', fits, default=fits, format_func=FIT_SHORT.get, key='proposal_fits')
    pending_only = f3.checkbox('Needs a decision', key='proposal_pending_only')
    shown = [row for row in rows if row['requirement'].get('kind', '') in chosen_kinds and row['fit'] in chosen_fits
             and not (pending_only and drafts.get(row['requirement']['id'], {}).get('status') == 'Approved')]
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
        st.session_state['proposal_failures'] = failures
        st.rerun()
    for failure in st.session_state.pop('proposal_failures', []):
        st.warning(failure)

    if shown:
        st.divider()
        _section_panel(service, shown, drafts, numbers, corpus, reviewer, model, live)
    else:
        st.caption('No requirements match the filters.')

    st.divider()
    _package_panel(service, review, drafts, reviewer, project_name)


def _section_panel(service, shown, drafts, numbers, corpus, reviewer, model, live):
    ids = [row['requirement']['id'] for row in shown]
    rid = st.selectbox('Open a section', ids, key='proposal_open',
                       format_func=lambda key: f"{numbers[key]}. {STATUS_ICON[drafts[key]['status'] if key in drafts else None]}"
                                               f" · {service.rows[key]['requirement']['text'][:100]}")
    row, draft = service.rows[rid], drafts.get(rid)
    requirement = row['requirement']
    st.markdown(f"#### {numbers[rid]}. {requirement['text']}")
    st.caption(' · '.join(filter(None, [requirement.get('kind'), requirement.get('obligation_level'),
                                        f"Evidence: {row['fit']}", STATUS_ICON[draft['status'] if draft else None]])))

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

    version = draft['version']
    if draft['status'] == 'Declined':
        st.error(f"Declined by {draft['declined_by']}: {draft['decline_comment']}\n\n"
                 + ('Redraft to get a new version that addresses this comment, or edit the text yourself.'
                    if row['fit'] != 'No match' else
                    'No match sections have a fixed text: edit it yourself, or resolve the evidence gap first.'))
    elif draft['status'] == 'Approved':
        st.success(f"Approved by {draft['approved_by']}"
                   + (f" with {len(draft['approved_open_items'])} open item(s) kept for the PPTX"
                      if draft.get('approved_open_items') else ''))
    if draft.get('redraft_of_decline'):
        last = draft['declines'][-1]
        st.caption(f"Redrafted after a decline by {last['by']}: “{last['comment']}”")
    items = open_items(draft['text'])
    if items:
        st.warning('Open items — resolve each one and remove its [OFFEN: …] marker, or approve with them kept:\n'
                   + '\n'.join(f'- {item}' for item in items))
    flags = check_wording(draft['text'])
    if flags:
        st.warning('Check wording: ' + ', '.join(flags) + ' — may carry an earlier offer’s roles or a commitment over.')
    st.caption(f"{draft['origin']} · updated {draft['updated_at'][:16].replace('T', ' ')} UTC")

    with st.form(f'edit:{rid}:{version}'):
        title = st.text_input('Section title', draft['title'])
        text = st.text_area('Section text', draft['text'], height=260)
        save = st.form_submit_button('Save edits')
    if save and _run(service.edit, rid, title, text, version):
        st.rerun()

    decide, refuse = st.columns(2)
    with decide.form(f'approve:{rid}:{version}'):
        keep = st.checkbox(f'Keep the {len(items)} open item(s) marked in the PPTX', disabled=not items)
        approve = st.form_submit_button('✅ Approve this section', disabled=draft['status'] == 'Approved')
    if approve and _run(service.approve, rid, reviewer, version, accept_open_items=keep):
        st.rerun()
    with refuse.form(f'decline:{rid}:{version}'):
        comment = st.text_area('What should change?', height=80)
        decline = st.form_submit_button('❌ Decline with comment', disabled=draft['status'] == 'Declined')
    if decline and _run(service.decline, rid, reviewer, comment, version):
        st.rerun()

    label = ('Redraft using the decline comment' if draft['status'] == 'Declined'
             else 'Redraft (replaces this text and its edits)')
    if st.button(label, key=f'redraft:{rid}:{version}', disabled=not live or row['fit'] == 'No match'):
        with st.spinner('Drafting…'):
            if _run(service.generate, rid, model, version):
                st.rerun()


def _package_panel(service, review, drafts, reviewer, project_name):
    st.subheader('Approve draft for PPTX')
    package, current = service.latest_package()
    approved = sum(d['status'] == 'Approved' for d in drafts.values())
    excluded = len(service.rows) - approved
    if package and current:
        st.success(f"Draft approved for the PPTX by {package['reviewer']} (package {package['revision']}): "
                   f"{len(package['sections'])} sections, {len(package['excluded'])} requirements left out. "
                   'Continue on Final Review & Export.')
        st.button('Continue to Final Review & Export',
                  on_click=lambda: st.session_state.update(page='Final Review & Export'))
        return
    if package:
        st.warning('Sections changed after the draft was approved for the PPTX. Approve it again to hand over '
                   'the current state.')
    st.caption(f'{approved} approved section(s) go into the PPTX. Requirements without an approved section '
               f'({excluded}) are left out and listed as missing.')
    with st.form(f"approve_package:{review['review_revision']}"):
        acknowledge = st.checkbox(f'Leave out the {excluded} requirement(s) without an approved section',
                                  disabled=not excluded)
        submit = st.form_submit_button('Approve draft for PPTX', type='primary', disabled=not approved)
    if submit and _run(service.approve_package, reviewer, acknowledge_excluded=acknowledge, project_name=project_name):
        st.rerun()


def final_review_handoff(store, project, rows, directory):
    """Final Review & Export: receive the approved draft package for PPTX generation."""
    from ..checklist import ChecklistReview
    from ..evidence import evidence_inventory
    from ..evidence_review import EvidenceReview
    try:
        checklist = ChecklistReview(store).current(project, rows)
        approved = EvidenceReview(store, checklist).current(directory, evidence_inventory()) if checklist else None
    except (OSError, ValueError) as error:
        st.error(str(error))
        return
    if not approved:
        st.warning('Final Review is locked. Approve the draft for the PPTX on Proposal Draft first.')
        return
    final_review_view(store, approved['review'])


def final_review_view(store, review):
    """Show the current approved draft package and its handoff downloads."""
    package, current = ProposalDrafts(store, review).latest_package()
    if not (package and current):
        st.warning('Final Review is locked. Approve the draft for the PPTX on Proposal Draft first.')
        return
    st.success(f"Received the draft approved by {package['reviewer']} (package {package['revision']}): "
               f"{len(package['sections'])} sections.")
    open_count = sum(len(s['open_items']) for s in package['sections'])
    if open_count:
        st.warning(f'{open_count} open item(s) are still marked in the approved text and will appear in the PPTX.')
    if package['excluded']:
        with st.expander(f"Left out of the PPTX ({len(package['excluded'])})"):
            for item in package['excluded']:
                st.text(f"{item['status']}: {item['requirement'][:140]}")
    st.download_button('Download approved sections for PPTX generation (CSV)', handoff_csv(package),
                       f"{package['project_id']}-approved-sections-p{package['revision']}.csv", mime='text/csv')
    st.download_button('Download approved package (JSON)', json.dumps(package, ensure_ascii=False, indent=1).encode(),
                       f"{package['project_id']}-approved-sections-p{package['revision']}.json",
                       mime='application/json')
    with st.expander(f"Approved sections ({len(package['sections'])})"):
        for section in package['sections']:
            st.markdown(f"**{section['title']}**\n\n{section['text']}")
            st.caption(' · '.join(c['filename'] + ' · ' + c['location'] for c in section['citations']))
    st.divider()
    _presentation_panel(store, review, package)


def _presentation_panel(store, review, package):
    """Generate the PPTX from the approved package, then the second human review of the deck."""
    from .. import pptx_from_drafts as decks_module
    from ..discovery import participant_directory
    st.subheader('Presentation')
    decks = decks_module.GeneratedDecks(store, package)
    latest = decks.latest()
    try:
        template = decks_module.find_template(participant_directory())
    except FileNotFoundError as error:
        st.error(f'{error} Set MESAKUMO_PARTICIPANT_DIR to the folder with the corporate template.')
        return
    st.caption(f'A model ({decks_module.PPTX_MODEL}) structures the approved sections into slide data; missing '
               f'facts become [OFFEN: …]. The data then fills {template.name}. Takes about a minute.')
    if st.button('Generate PPTX' if not latest else 'Generate a new PPTX', type='primary' if not latest else 'secondary'):
        with st.status('Generating the presentation…', expanded=True) as status:
            try:
                pptx, payload = decks_module.render_pptx(handoff_project(package, review), template, reviewed=True,
                                                         on_progress=status.write)
                decks.save(pptx, payload, decks_module.PPTX_MODEL)
                status.update(label='Presentation ready', state='complete')
            except Exception as error:  # Model, network, template or rendering failure: nothing is saved.
                status.update(label='No presentation was created', state='error')
                st.error(f'{type(error).__name__}: {error}')
                return
        st.rerun()
    if not latest:
        return

    revision, meta, payload, pptx = latest
    st.download_button(f'Download presentation (PPTX, version {revision})', pptx,
                       f"{package['project_id']}-angebot-p{package['revision']}-v{revision}.pptx",
                       mime='application/vnd.openxmlformats-officedocument.presentationml.presentation')
    st.download_button('Download slide data (JSON)', json.dumps(payload, ensure_ascii=False, indent=1).encode(),
                       f"{package['project_id']}-slides-p{package['revision']}-v{revision}.json",
                       mime='application/json')
    st.caption(f"Generated {meta['created_at'][:16].replace('T', ' ')} UTC with {meta['model']}")
    if meta['open_markers']:
        with st.expander(f"{len(meta['open_markers'])} open point(s) in the presentation"):
            st.markdown('\n'.join(f'- {marker}' for marker in meta['open_markers']))

    st.markdown('**Second review: the presentation**')
    if meta['status'] == 'Approved':
        st.success(f"Presentation approved by {meta['reviewer']}.")
        return
    if meta['status'] == 'Declined':
        st.error(f"Declined by {meta['reviewer']}: {meta['comment']}\n\nChange the sections on Proposal Draft "
                 '(then approve the draft for the PPTX again), or generate a new PPTX.')
        return
    reviewer = st.text_input('Your name (reviewer)', key='pptx_reviewer')
    left, right = st.columns(2)
    with left.form(f'approve_pptx:{revision}'):
        st.caption('Approve after opening the PPTX and checking every slide and open point.')
        approve = st.form_submit_button('✅ Approve presentation')
    with right.form(f'decline_pptx:{revision}'):
        comment = st.text_area('What should change?', height=80)
        decline = st.form_submit_button('❌ Decline with comment')
    if (approve or decline) and _run(decks.decide, revision, reviewer, bool(approve), comment):
        st.rerun()
