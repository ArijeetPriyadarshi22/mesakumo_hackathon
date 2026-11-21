"""Proposal Draft and Final Review pages: draft, decide on each section, approve the draft for the PPTX.

Workflow: a section is drafted → the reviewer approves it or declines it with a comment →
a declined section is redrafted using that comment → once the wanted sections are approved,
the reviewer approves the draft as a package → Final Review & Export receives the package
(CSV for the PPTX-compiling model, and a Project for the PPTX exporter) for PPTX generation
and the second review.
"""
import json

import streamlit as st

from ..final_export import FinalExports, PPTX_MIME, TEMPLATE_NAME, final_blockers
from ..models import DisabledModel, ModelUnavailable, configure_team_one, configured_model
from ..persistence import ConflictError
from ..proposal import (PROSE_KINDS, ProposalDrafts, check_wording, handoff_csv, pending_items,
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
            'Open items': len(pending_items(row, draft)),
            'Check wording': ', '.join(check_wording(draft['text'])) if draft else '',
        })
    return table


def _source(citation, excerpt_key, used=False):
    st.markdown(f"**{citation.get('filename', '')} · {citation.get('location', '')}**"
                + (' — cited in the draft' if used else ''))
    if citation.get('passage_id'):
        details = [citation['passage_id']]
        if citation.get('document_version'):
            details.append('Version ' + citation['document_version'])
        st.caption(' · '.join(details))
    if citation.get(excerpt_key):
        st.text(citation[excerpt_key])


def _run(action, *args, **kwargs):
    """Run a write action and show its error instead of a traceback."""
    try:
        action(*args, **kwargs)
        return True
    except (ValueError, ConflictError, ModelUnavailable) as error:
        st.error(str(error))
        return False


def proposal_view(store, approved, project_name='', *, directory=None, inventory=None):
    review = approved['review']
    service = ProposalDrafts(store, review, directory=directory, inventory=inventory)
    scope = ':'.join(map(str, service.key))
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
                             disabled=not live, key=f'proposal_batch:{scope}:{tuple(r["requirement"]["id"] for r in missing)}'):
        progress, failures = st.progress(0.0), []
        for done, row in enumerate(missing, 1):
            rid = row['requirement']['id']
            progress.progress(done / len(missing), text=f'Drafting {done} of {len(missing)}: requirement {numbers[rid]}')
            try:
                service.generate(rid, model, expected_version=0)
            except (ValueError, ConflictError, ModelUnavailable) as error:
                failures.append(f'Requirement {numbers[rid]}: {error}')
        st.session_state[f'proposal_failures:{scope}'] = failures
        st.rerun()
    for failure in st.session_state.pop(f'proposal_failures:{scope}', []):
        st.warning(failure)

    if shown:
        st.divider()
        _section_panel(service, shown, drafts, numbers, corpus, reviewer, model, live, scope)
    else:
        st.caption('No requirements match the filters.')

    st.divider()
    _package_panel(service, drafts, reviewer, project_name, scope)


def _section_panel(service, shown, drafts, numbers, corpus, reviewer, model, live, scope):
    ids = [row['requirement']['id'] for row in shown]
    rid = st.selectbox('Open a section', ids, key=f'proposal_open:{scope}',
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
        used = set(draft['citation_ids']) if draft and not draft.get('citations_need_review') else set()
        for pid in row['citation_ids']:
            _source(corpus[pid], 'excerpt', used=pid in used)
        if not row['citation_ids']:
            st.caption('No company evidence was approved for this requirement.')

    items = pending_items(row, draft)
    if items:
        st.warning('Open items. Record each resolution or keep the item open in the handoff.\n'
                   + '\n'.join(f'- {item}' for item in items.values()))

    if not draft:
        if st.button('Draft this section', disabled=not live, key=f'draft:{scope}:{rid}'):
            with st.spinner('Drafting…'):
                if _run(service.generate, rid, model, 0):
                    st.rerun()
        return

    version = draft['version']
    section_key = f'{scope}:{rid}:{version}'
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
    needs_citations = draft.get('citations_need_review', False)
    if needs_citations:
        st.warning('Confirm which approved citations support the edited text before approving.')
    flags = check_wording(draft['text'])
    if flags:
        st.warning('Check wording: ' + ', '.join(flags) + ' — may carry an earlier offer’s roles or a commitment over.')
    st.caption(f"{draft['origin']} · updated {draft['updated_at'][:16].replace('T', ' ')} UTC")

    with st.form(f'edit:{section_key}'):
        title = st.text_input('Section title', draft['title'])
        text = st.text_area('Section text', draft['text'], height=260)
        save = st.form_submit_button('Save edits')
    if save and _run(service.edit, rid, title, text, version):
        st.rerun()

    if items:
        with st.expander('Record an open-item resolution'):
            with st.form(f'resolve:{section_key}'):
                item = st.selectbox('Open item', list(items), format_func=items.get)
                resolution = st.text_area('How was this resolved? Include the source or decision.')
                resolve = st.form_submit_button('Record resolution')
            if resolve and _run(service.resolve_item, rid, item, reviewer, resolution, version):
                st.rerun()
    if draft.get('open_item_resolutions'):
        with st.expander('Recorded resolutions'):
            for resolution in draft['open_item_resolutions'].values():
                st.text(f"{resolution['text']} · {resolution['reviewer']} · {resolution['note']}")

    decide, refuse = st.columns(2)
    with decide.form(f'approve:{section_key}'):
        citations = None
        if needs_citations:
            citations = st.multiselect('Citations that support the edited text', row['citation_ids'],
                                       format_func=lambda pid: f"{corpus[pid]['filename']} · {corpus[pid]['location']}")
        keep = st.checkbox(f'Keep the {len(items)} open item(s) marked in the PPTX', disabled=not items)
        approve = st.form_submit_button('✅ Approve this section', disabled=draft['status'] == 'Approved')
    if approve and _run(service.approve, rid, reviewer, version, accept_open_items=keep, citation_ids=citations):
        st.rerun()
    with refuse.form(f'decline:{section_key}'):
        comment = st.text_area('What should change?', height=80)
        decline = st.form_submit_button('❌ Decline with comment', disabled=draft['status'] == 'Declined')
    if decline and _run(service.decline, rid, reviewer, comment, version):
        st.rerun()

    label = ('Redraft using the decline comment' if draft['status'] == 'Declined'
             else 'Redraft (replaces this text and its edits)')
    if st.button(label, key=f'redraft:{section_key}', disabled=not live or row['fit'] == 'No match'):
        with st.spinner('Drafting…'):
            if _run(service.generate, rid, model, version):
                st.rerun()


def _package_panel(service, drafts, reviewer, project_name, scope):
    st.subheader('Approve draft for PPTX')
    package, current = service.latest_package()
    approved = sum(d['status'] == 'Approved' for d in drafts.values())
    excluded = len(service.rows) - approved
    if package and current:
        st.success(f"Draft approved for the PPTX by {package['reviewer']} (package {package['revision']}): "
                   f"{len(package['sections'])} sections, {len(package['excluded'])} requirements left out. "
                   'Continue on Final Review & Export.')
        st.button('Continue to Final Review & Export', key=f"proposal_continue:{scope}:{package['revision']}",
                  on_click=lambda: st.session_state.update(page='Final Review & Export'))
        return
    if package:
        st.warning('Sections changed after the draft was approved for the PPTX. Approve it again to hand over '
                   'the current state.')
    st.caption(f'{approved} approved section(s) go into the PPTX. Requirements without an approved section '
               f'({excluded}) are left out and listed as missing.')
    with st.form(f"approve_package:{scope}:{service._fingerprint(drafts)}"):
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
        inventory = evidence_inventory() if checklist else None
        approved = EvidenceReview(store, checklist).current(directory, inventory) if checklist else None
    except (OSError, ValueError) as error:
        st.error(str(error))
        return
    if not approved:
        st.warning('Final Review is locked. Approve the draft for the PPTX on Proposal Draft first.')
        return
    final_review_view(store, approved['review'], directory=directory, inventory=inventory)


def final_review_view(store, review, *, directory=None, inventory=None):
    """Review a saved proposal package and approve its exact rendered bytes."""
    service = ProposalDrafts(store, review, directory=directory, inventory=inventory)
    package, current = service.latest_package()
    if not (package and current):
        st.warning('Final Review is locked. Approve the current proposal package on Proposal Draft first.')
        return

    scope = ':'.join(map(str, (*service.key, package['revision'])))
    st.success(f"Reviewing proposal package {package['revision']} · {len(package['sections'])} approved sections "
               f"· approved on Page 3 by {package['reviewer']}")
    st.button('Back to Proposal Draft', key=f'final_back:{scope}',
              on_click=lambda: st.session_state.update(page='Proposal Draft'))

    blockers = final_blockers(package, service.rows)
    summary, rendered = st.columns([3, 2])
    with summary:
        st.subheader('Review summary')
        if blockers:
            st.warning(f'{len(blockers)} blocking items must be resolved before final approval.')
            for blocker in blockers:
                st.text(blocker)
        if package['excluded']:
            with st.expander(f"Excluded requirements ({len(package['excluded'])})", expanded=True):
                for item in package['excluded']:
                    st.text(f"{item['requirement_id']} · {item['status']} · {item['requirement']}")
                    for open_item in item.get('open_items', []):
                        st.text(open_item)
        for section in package['sections']:
            with st.expander(f"{section['title']} · {section['requirement_id']}"):
                st.text(section['text'])
                for item in section['open_items']:
                    st.caption('Open item · ' + item)
                if section['tender_sources']:
                    st.markdown('**Tender sources**')
                    for source in section['tender_sources']:
                        _source(source, 'original_excerpt')
                if section['citations']:
                    st.markdown('**Approved company evidence**')
                    for source in section['citations']:
                        _source(source, 'excerpt')

    with rendered:
        st.subheader('PowerPoint preview and final approval')
        st.caption(f'Corporate template · {TEMPLATE_NAME}')
        st.download_button('Download approved sections for PPTX generation (CSV)', handoff_csv(package),
                           f"{package['project_id']}-approved-sections-p{package['revision']}.csv", mime='text/csv',
                           key=f'proposal_csv:{scope}')
        st.download_button('Download approved package (JSON)', json.dumps(package, ensure_ascii=False, indent=1).encode(),
                           f"{package['project_id']}-approved-sections-p{package['revision']}.json",
                           mime='application/json', key=f'proposal_json:{scope}')
        if directory is None:
            st.error('The participant folder is unavailable.')
            return
        try:
            exports = FinalExports(service, package, directory)
            preview = exports.current()
            render_key = f'{scope}:{exports.template_sha}'
            st.caption(f'Template version · {exports.template_sha[:16]}')
            if preview is None and st.button('Generate draft preview', key=f'preview:{render_key}', type='primary'):
                with st.spinner('Rendering the approved proposal text…'):
                    preview = exports.preview()
            if preview is None:
                return
            artifact_key = f"{render_key}:{preview['checksum']}"
            st.caption(f"Rendered file SHA-256 · {preview['checksum']}")
            basename = f"{package['project_id']}-p{package['revision']}"
            st.download_button('Download draft preview (PPTX)', preview['data'], f'{basename}-draft.pptx',
                               mime=PPTX_MIME, key=f'preview_download:{artifact_key}')
            if preview['reviewer'] is None:
                with st.form(f'final_approve:{artifact_key}'):
                    reviewer = st.text_input('Final reviewer')
                    approve = st.form_submit_button('Approve rendered PPTX', disabled=bool(blockers))
                if approve:
                    exports.approve(reviewer, preview['checksum'])
                    st.rerun()
            else:
                st.success(f"Final PPTX approved by {preview['reviewer']}")
                st.download_button('Download approved PPTX', preview['data'], f'{basename}-approved.pptx',
                                   mime=PPTX_MIME, key=f'approved_download:{artifact_key}')
        except (OSError, ValueError) as error:
            st.error(str(error))
