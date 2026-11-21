"""Draft proposal sections and export the saved package as PowerPoint."""
import json

import streamlit as st

from ..evidence_review import evidence_file_issues
from ..final_export import FinalExports, PPTX_MIME, TEMPLATE_NAME
from ..models import DisabledModel, ModelUnavailable, configured_model
from ..proposal import CITED_FITS, ProposalDrafts, handoff_csv, pending_items

FIT_LABEL = {'Strong match': 'Strong', 'Partial match': 'Partial', 'No match': 'No match', '': 'Assessment open'}


def _sources(citations, excerpt_key):
    for citation in citations:
        st.markdown(f"**{citation['filename']} · {citation['location']}**")
        st.caption(' · '.join(filter(None, (citation.get('passage_id'), citation.get('document_version')))))
        if citation.get(excerpt_key):
            st.text(citation[excerpt_key])


def _run(action, *args, **kwargs):
    try:
        action(*args, **kwargs)
        return True
    except (ValueError, ModelUnavailable) as error:
        st.error(str(error))
        return False


def proposal_view(store, approved, project_name='', *, directory=None, inventory=None):
    review = approved['review']
    service = ProposalDrafts(store, review, directory=directory, inventory=inventory)
    scope = ':'.join(map(str, service.key))
    drafts = service.all()
    rows = service.rows
    model = configured_model()
    live = not isinstance(model, DisabledModel)
    if not live:
        st.caption('Edit sections by hand or generate placeholders for open assessments and No match rows.')

    kinds = sorted({row['requirement'].get('kind', '') for row in rows.values()})
    with st.expander('Filter requirements'):
        left, right = st.columns(2)
        categories = left.multiselect('Category', kinds, default=kinds, key=f'proposal_kinds:{scope}')
        fits = right.multiselect('Evidence fit', list(FIT_LABEL), default=list(FIT_LABEL),
                                 format_func=FIT_LABEL.get, key=f'proposal_fits:{scope}')
    shown = [rid for rid, row in rows.items()
             if row['requirement'].get('kind', '') in categories and row['fit'] in fits]
    if shown:
        st.dataframe([{
            'Requirement': rows[rid]['requirement']['text'],
            'Category': rows[rid]['requirement'].get('kind', ''),
            'Evidence fit': FIT_LABEL[rows[rid]['fit']],
            'Section': drafts[rid]['status'] if rid in drafts else 'Empty',
            'Open items': len(pending_items(rows[rid], drafts.get(rid))),
        } for rid in shown], hide_index=True, width='stretch', height=230)
        _draft_actions(service, shown, drafts, model, live, scope)
    else:
        st.caption('No requirements match these filters.')

    if issues := evidence_file_issues(review):
        with st.expander(f'Evidence file issues ({len(issues)})'):
            for issue in issues:
                st.text(issue)
    _export_action(service, drafts, project_name, scope)


def _draft_actions(service, shown, drafts, model, live, scope):
    rows = service.rows
    missing = [rid for rid in shown if rid not in drafts and (live or rows[rid]['fit'] not in CITED_FITS)]
    if missing and st.button(f'Generate missing drafts ({len(missing)})', key=f'proposal_batch:{scope}:{tuple(missing)}'):
        fixed = [rid for rid in missing if rows[rid]['fit'] not in CITED_FITS]
        if not fixed or _run(service.generate_placeholders, fixed):
            progress, failures = st.progress(len(fixed) / len(missing)), []
            for done, rid in enumerate((rid for rid in missing if rows[rid]['fit'] in CITED_FITS), len(fixed) + 1):
                try:
                    service.generate(rid, model, expected_version=0)
                except (ValueError, ModelUnavailable) as error:
                    failures.append(f'{rid} · {error}')
                progress.progress(done / len(missing), text=f'Drafting {done} of {len(missing)}')
            st.session_state[f'proposal_failures:{scope}'] = failures
            st.rerun()
    for failure in st.session_state.pop(f'proposal_failures:{scope}', []):
        st.error(failure)

    first_draft = next((index for index, rid in enumerate(shown) if rid in drafts), 0)
    rid = st.selectbox('Open a section', shown, index=first_draft, key=f'proposal_open:{scope}',
                       format_func=lambda rid: f"{rid} · {rows[rid]['requirement']['text'][:100]}")
    row, draft = rows[rid], drafts.get(rid)
    version = draft['version'] if draft else 0
    section_key = f'{scope}:{rid}:{version}'
    st.write(row['requirement']['text'])
    items = pending_items(row, draft)
    if items:
        with st.expander(f'Open items ({len(items)})'):
            for item in items.values():
                st.text(item)
    with st.expander('Sources'):
        if row.get('tender_sources'):
            st.caption('Tender sources')
            _sources(row['tender_sources'], 'original_excerpt')
        if row['citation_ids']:
            st.caption('Evidence from Page 2')
            _sources([service.corpus[pid] for pid in row['citation_ids']], 'excerpt')
    with st.form(f'edit:{section_key}'):
        title = st.text_input('Section title', value=draft['title'] if draft else row['requirement']['text'][:100])
        text = st.text_area('Section text', value=draft['text'] if draft else '', height=200)
        save = st.form_submit_button('Save section')
    if save and _run(service.edit, rid, title, text, version):
        st.rerun()
    if live or row['fit'] not in CITED_FITS:
        label = 'Draft this section' if not draft else 'Regenerate section (replace saved text)'
        if st.button(label, key=f'draft:{section_key}'):
            with st.spinner('Drafting…'):
                if _run(service.generate, rid, model, version):
                    st.rerun()


def _export_action(service, drafts, project_name, scope):
    st.divider()
    included = sum(draft['status'] != 'Declined' for draft in drafts.values())
    st.caption(f'{included} of {len(service.rows)} sections ready to export.')
    package, current = service.latest_package()
    if package and current and not package['reviewer']:
        st.button('Continue to Final Review & Export', type='primary',
                  key=f"proposal_continue:{scope}:{package['revision']}",
                  on_click=lambda: st.session_state.update(page='Final Review & Export'))
    elif included:
        fingerprint = service._fingerprint(drafts)

        def open_export():
            if _run(service.save_package, fingerprint, project_name):
                st.session_state.page = 'Final Review & Export'

        st.button('Continue to Final Review & Export', type='primary',
                  key=f'proposal_export:{scope}:{fingerprint}', on_click=open_export)
    else:
        st.caption('Save or generate a section to continue.')


def final_review_handoff(store, project, rows, directory):
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
        st.info('Save the evidence on Page 2, then continue from Proposal Draft.')
        return
    final_review_view(store, approved['review'], directory=directory, inventory=inventory)


def final_review_view(store, review, *, directory=None, inventory=None):
    service = ProposalDrafts(store, review, directory=directory, inventory=inventory)
    package, current = service.latest_package()
    st.button('Back to Proposal Draft', on_click=lambda: st.session_state.update(page='Proposal Draft'))
    if not (package and current):
        st.info('Open export from Proposal Draft to use the latest saved sections.')
        return

    scope = ':'.join(map(str, (*service.key, package['revision'])))
    count = len(package['sections'])
    excluded = len(package['excluded'])
    st.caption(f"Proposal version {package['revision']} · {count} sections · {excluded} requirements without a section")
    try:
        if directory is None:
            raise ValueError('The participant folder is unavailable.')
        exports = FinalExports(service, package, directory)
        rendered = exports.current()
        render_key = f'{scope}:{exports.template_sha}'
        if rendered is None and st.button('Generate PowerPoint', key=f'render:{render_key}', type='primary'):
            with st.spinner('Generating the PowerPoint…'):
                rendered = exports.preview()
        if rendered is not None:
            st.download_button('Download PowerPoint', rendered['data'],
                               f"{package['project_id']}-p{package['revision']}.pptx",
                               mime=PPTX_MIME, key=f"pptx:{render_key}:{rendered['checksum']}", type='primary')
        st.caption(f'Corporate template · {TEMPLATE_NAME}')
    except (OSError, ValueError) as error:
        st.error(str(error))

    with st.expander('Sections and sources'):
        sections = {section['requirement_id']: section for section in package['sections']}
        rid = st.selectbox('Section', list(sections), format_func=lambda rid: sections[rid]['title'],
                           key=f'export_section:{scope}')
        if rid is not None:
            section = sections[rid]
            st.text(section['text'])
            if section['tender_sources']:
                st.caption('Tender sources')
                _sources(section['tender_sources'], 'original_excerpt')
            if section['citations']:
                st.caption('Evidence sources')
                _sources(section['citations'], 'excerpt')
    issues = package.get('evidence_file_issues', [])
    if excluded or issues:
        with st.expander('Excluded requirements and evidence file issues'):
            for issue in issues:
                st.text(issue)
            if excluded:
                st.dataframe([{'Requirement ID': item['requirement_id'], 'Requirement': item['requirement'],
                               'Open items': '\n'.join(item.get('open_items', []))}
                              for item in package['excluded']], hide_index=True, width='stretch')
    with st.expander('Other downloads'):
        basename = f"{package['project_id']}-p{package['revision']}"
        st.download_button('Download sections (CSV)', handoff_csv(package), f'{basename}-sections.csv', mime='text/csv',
                           key=f'proposal_csv:{scope}')
        st.download_button('Download package (JSON)', json.dumps(package, ensure_ascii=False, indent=1).encode(),
                           f'{basename}.json', mime='application/json', key=f'proposal_json:{scope}')
