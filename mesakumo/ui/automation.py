"""Projects and the four review screens for unattended tender preparation."""
import re
from collections import Counter
from pathlib import Path
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError
from streamlit import config

from ..automation import approve_current, approved_download, start_worker
from ..automation_deck import COVER_LIMITS, open_local_deck, preview_pdf
from ..automation_evidence import Proof, applicable_review, corpus, effective_assessments, eligible, manifest, review_basis
from ..automation_mail import statuses
from ..automation_proposal import confirm_fields, coverage, review_issues
from ..automation_requirements import CHAPTERS, Criterion, parse_grounded
from ..automation_store import ACTIVE_JOB_STATUSES, AutomationStore, fingerprint, workspace_path
from ..discovery import participant_directory
from ..evidence import evidence_inventory
from ..evidence_matching import LexicalIndex
from ..final_export import PPTX_MIME
from ..persistence import ConflictError

TABS = {
    'requirements': 'Tender requirements',
    'evidence': 'Evidence',
    'proposal': 'Proposal',
    'review': 'Final review',
}


@st.cache_resource
def service(path, port):
    store = AutomationStore(Path(path))
    start_worker(store, port)
    return store


@st.cache_data(show_spinner=False, max_entries=4)
def pdf_preview(checksum, _deck):
    return preview_pdf(_deck)


def location(project=None, tab=None):
    if project:
        params = {'project': project}
        if tab is not None:
            params['tab'] = tab
        st.query_params.from_dict(params)
    else:
        st.query_params.clear()
    st.rerun()


def jobs_table(store, identifiers):
    return [row for row in store.projects() if row['id'] in identifiers]


def projects_view(store):
    st.title('TenderWorkbench')
    root = store.setting('input_root')
    with st.expander('Local folders and language', expanded=not root):
        with st.form('workspace-settings'):
            folder = st.text_input('Participant folder', value=root or str(participant_directory()))
            languages = ['auto', 'de', 'en']
            language = st.selectbox('Proposal language', languages,
                                    index=languages.index(store.setting('language', 'auto')),
                                    format_func={'auto': 'From tender', 'de': 'German', 'en': 'English'}.get)
            recipient = st.text_input('Review delivery address', value=store.setting('review_recipient') or '')
            triage = st.text_input('Local triage address', value=store.setting('triage_recipient') or '')
            if st.form_submit_button('Save local settings'):
                try:
                    selected = Path(folder).expanduser().resolve()
                    if not folder.strip() or not selected.is_dir():
                        raise ValueError('Choose an existing local directory.')
                    store.set_setting('input_root', str(selected))
                    store.set_setting('language', language)
                    store.set_setting('review_recipient', recipient.strip())
                    store.set_setting('triage_recipient', triage.strip())
                except (OSError, ValueError) as error:
                    st.error(str(error))
                else:
                    st.rerun()
    if not root:
        return
    try:
        identifiers = store.register(Path(root))
    except ValueError as error:
        st.error(str(error))
        return
    if not identifiers:
        st.info('No tender folders found. Use an Ausschreibung folder or declare phases in .tender-phases.json.')
    listing = jobs_table(store, identifiers)
    if listing:
        choices = {row['id']: f"{Path(row['folder']).name} · {row['phase']}" for row in listing}
        all_tenders = st.checkbox('Select all tenders and phases', value=True)
        selected = identifiers if all_tenders else st.multiselect(
            'Select projects', identifiers, format_func=choices.get)
        ops = st.columns(2)
        if ops[0].button('Prepare proposals', type='primary', disabled=not selected):
            store.enqueue(selected)
            if len(selected) == 1:
                location(selected[0])
            st.rerun()
        if ops[1].button('Update with current model', disabled=not selected,
                         help='Rebuild affected material using the configured provider and model.'):
            store.enqueue(selected, 'update')
            st.rerun()
        project_progress(store, identifiers, selected)

    archived = [row for row in store.projects() if row['id'] not in identifiers]
    if archived:
        with st.expander('Projects from previous folders'):
            project_progress(store, [row['id'] for row in archived])


def stop_job(container, store, row, *, key):
    if row['status'] in ('queued', 'running'):
        container.button('Stop job', key=key, on_click=store.request_stop,
                         args=([(row['id'], row['request_id'])],))


@st.fragment(run_every=3)
def project_progress(store, identifiers, selected=()):
    rows = jobs_table(store, identifiers)
    selected_jobs = [(row['id'], row['request_id']) for row in rows
                     if row['id'] in selected and row['status'] in ('queued', 'running')]
    if selected_jobs:
        st.button('Stop selected jobs', on_click=store.request_stop, args=(selected_jobs,))
    for row in rows:
        key = row['id']
        info, open_col, stop_col = st.columns([4, 1, 1])
        status = row['status'].capitalize() if row['status'] else 'Not prepared'
        info.write(f"**{Path(row['folder']).name}** · {row['phase']} · {status}")
        if row['materials'] is not None:
            info.caption(f"{row['materials']} materials · {row['gaps'] or 0} reading gaps"
                         + (' · Draft saved' if row['checksum'] else ''))
        if row['deadline']:
            info.caption('Deadline · ' + row['deadline'])
        if row['stage'] and row['status'] in ACTIVE_JOB_STATUSES:
            info.caption(f"{row['stage']} · {row['done'] or 0}/{row['total'] or '—'}")
        if open_col.button('Open', key='open-' + key):
            location(key)
        stop_job(stop_col, store, row, key='stop-list-' + key)


@st.fragment(run_every=2)
def status_view(store, previous):
    key = previous['id']
    row = jobs_table(store, [key])[0]
    if any(row[field] != previous[field] for field in ('request_id', 'status', 'workflow_stage')):
        st.rerun(scope='app')
    if row['status'] in ('queued', 'running'):
        st.info(f"{row['status'].capitalize()} · {row['stage'] or 'Preparing'} · "
                f"{row['done'] or 0}/{row['total'] or '—'}")
        stop_job(st, store, row, key='stop-project-' + key)
    elif row['status'] == 'stopping':
        st.info('Stopping after the current operation reaches a checkpoint.')
    elif row['status'] == 'stopped':
        st.info('Stopped. Use Resume preparation to continue.')
    if row['status'] in ('failed', 'partial'):
        summary = 'Preparation failed' if row['status'] == 'failed' else 'Preparation needs a retry'
        if row['diagnostic']:
            with st.expander(summary):
                st.text(row['diagnostic'])
        else:
            st.caption(summary)


def remember_review(key, revision, checksum):
    st.session_state['submitted_review'] = (key, revision, checksum)


def review_action(project):
    # Streamlit calls this callback with arguments from the previously rendered page.
    return dict(on_click=remember_review,
                args=(project['id'], project['revision'], project['checksum']))


def save_edit(store, project, snapshot, dirty):
    existing = snapshot.get('dirty')
    snapshot['dirty'] = 'upstream' if 'upstream' in (existing, dirty) else 'render'
    # Preserve the review episode through edits until after an approval.
    if project['approval']:
        snapshot.pop('review_episode', None)
    try:
        store.save_revision(project['id'], project['revision'], snapshot, project['input_key'])
    except ConflictError as error:
        st.error(str(error))
    else:
        st.rerun()


def citations(ids, sources):
    for sid in ids:
        item = sources.get(sid)
        if item:
            st.caption(f"{sid} · {item.get('file', item.get('filename', ''))} · {item['location']}")
            st.text(item.get('quote', item.get('excerpt', '')))


def source_locations(ids, sources):
    return ', '.join(
        f"{item.get('file', item.get('filename', ''))} · {item['location']}"
        for sid in ids if (item := sources.get(sid))
    )


def edit_criterion(store, project, snapshot, row, locked):
    sources = snapshot.get('sources', {})
    key = row['id'] if row else 'new'
    def get(field, fallback):
        return row.get(field, fallback) if row else fallback

    with st.form('criterion-' + key):
        text = st.text_input('Requirement', get('text', ''), max_chars=260)
        options = st.columns(4)
        fields = [
            ('scope', ('proposal', 'administrative', 'uncertain')),
            ('obligation', ('mandatory', 'scored', 'optional', 'informational', 'uncertain')),
            ('response', ('proof', 'method', 'current_action', 'none', 'uncertain')),
            ('chapter', CHAPTERS),
        ]
        changed = {field: column.selectbox(field.capitalize(), values, index=values.index(get(field, values[0])))
                   for column, (field, values) in zip(options, fields)}
        conditions = st.text_area('Exact conditions, one per line', '\n'.join(get('conditions', [])))
        links = st.multiselect('Tender citations', list(sources), default=get('citations', []),
                               format_func=lambda sid: f"{sources[sid]['file']} · {sources[sid]['location']} · {sid[-12:]}")
        resolution = st.text_input('Confirmation and reviewer note', value=get('resolution', '') or '')
        save = st.form_submit_button('Save criterion', **review_action(project), disabled=locked)
        remove = st.form_submit_button('Remove criterion', **review_action(project), disabled=locked or row is None)
    if save:
        try:
            item = Criterion(text=text.strip(), citations=links,
                             conditions=[line.strip() for line in conditions.splitlines() if line.strip()],
                             **changed).model_dump()
            item = parse_grounded(dict(criteria=[item], facts=[]), sources)['criteria'][0]
        except (ValidationError, ValueError):
            st.error('Enter a short statement and at least one current tender citation.')
        else:
            rid = key if row else 'H-' + uuid4().hex[:20]
            item.update(id=rid, resolution=resolution.strip(), duplicates=get('duplicates', 0))
            snapshot.setdefault('criterion_edits', {})[rid] = item
            snapshot['criteria'] = [r for r in snapshot.get('criteria', []) if r['id'] != rid] + [item]
            save_edit(store, project, snapshot, 'upstream')
    if remove:
        if row['obligation'] in ('mandatory', 'scored', 'uncertain') and not resolution.strip():
            st.error('Record the reason in the reviewer note before removing this requirement.')
            return
        if resolution.strip():
            snapshot.setdefault('criterion_removals', {})[key] = dict(text=row['text'], reason=resolution.strip())
        snapshot.setdefault('criterion_edits', {})[key] = None
        snapshot['criteria'] = [r for r in snapshot['criteria'] if r['id'] != key]
        save_edit(store, project, snapshot, 'upstream')


def requirements_view(store, project, snapshot, locked):
    st.header('Tender requirements')
    sources = snapshot.get('sources', {})
    if snapshot.get('internal_planning'):
        with st.expander('Internal planning · verify assumptions before committing'):
            for item in snapshot['internal_planning']:
                st.caption(item['file'])
                st.text(item['text'] or item['issue'])
    with st.expander('Tender brief'):
        for label, item in snapshot.get('facts', {}).items():
            st.write(f"**{label.capitalize()}** · {item['value']}")
            citations(item['citations'], sources)
            for alternative in item.get('alternatives', []):
                st.write(alternative['value'])
                citations(alternative['citations'], sources)
    gaps = snapshot.get('reading_gaps', [])
    with st.expander(f'Reading status · {len(gaps)} gaps'):
        st.dataframe(snapshot.get('manifest', []), hide_index=True)
        if gaps:
            st.dataframe([{'File': gap['file'], 'Reading gap': gap['detail'],
                           'Resolution': gap.get('resolution') or ''} for gap in gaps], hide_index=True)
            by_signature = {fingerprint({k: v for k, v in gap.items() if k != 'resolution'}): gap for gap in gaps}
            signature = st.selectbox('Reading gap to review', list(by_signature),
                                     format_func=lambda key: f"{by_signature[key]['file']} · {by_signature[key]['detail']}")
            gap = by_signature[signature]
            with st.form('gap-' + signature):
                note = st.text_input('Resolution and reviewer', value=gap.get('resolution') or '')
                if st.form_submit_button('Record resolution', **review_action(project), disabled=locked):
                    if not note.strip():
                        st.error('Enter the resolution before saving.')
                    else:
                        snapshot.setdefault('gap_resolutions', {})[signature] = note.strip()
                        gap['resolution'] = note.strip()
                        save_edit(store, project, snapshot, 'render')
    conflicts = snapshot.get('conflicts', [])
    language_issue = snapshot.get('language_issue')
    if conflicts or language_issue:
        with st.expander('Requirement issues'):
            if language_issue:
                st.text(language_issue)
            if conflicts:
                for conflict in conflicts:
                    st.text(conflict)
                with st.form('review-conflicts'):
                    note = st.text_area('Resolve listed conflicts with explanation and source references')
                    if st.form_submit_button('Record conflict review', **review_action(project), disabled=locked):
                        if note.strip():
                            snapshot.setdefault('conflict_reviews', {})[fingerprint(conflicts)] = note.strip()
                            save_edit(store, project, snapshot, 'render')
                        else:
                            st.error('Enter the resolution before saving.')
    scope = st.radio('Scope', ('proposal', 'administrative', 'uncertain'), horizontal=True,
                     format_func={'proposal': 'Proposal', 'administrative': 'General and administrative',
                                  'uncertain': 'Uncertain'}.get)
    filters = st.multiselect('Obligation filter', ('mandatory', 'scored', 'optional', 'informational', 'uncertain'))
    visible = [r for r in snapshot.get('criteria', []) if r['scope'] == scope and
               (not filters or r['obligation'] in filters)]
    if visible:
        st.dataframe([
            {'Requirement ID': row['id'], 'Requirement': row['text'],
             'Obligation': row['obligation'], 'Response': row['response'], 'Chapter': row['chapter'],
             'Exact conditions': ' | '.join(row.get('conditions', [])),
             'Tender sources': source_locations(row['citations'], sources)}
            for row in visible
        ], hide_index=True, column_config={
            'Requirement': st.column_config.TextColumn(width=360),
            'Exact conditions': st.column_config.TextColumn(width=230),
            'Tender sources': st.column_config.TextColumn(width=260),
        })
        selected = st.selectbox('Requirement to inspect', visible,
                                format_func=lambda row: f"{row['text']} · {row['id']}")
        st.caption(f"{selected['id']} · {selected['obligation']} · {selected['response']} · "
                   f"{selected.get('duplicates', 0)} merged repeats")
        with st.expander('Tender sources'):
            citations(selected['citations'], sources)
        if selected.get('superseded'):
            with st.expander('Explicitly superseded clauses'):
                for old in selected['superseded']:
                    st.write(old['conditions'])
                    citations(old['citations'], sources)
        if selected.get('merged'):
            with st.expander('Equivalent wording from other sources'):
                for duplicate in selected['merged']:
                    st.write(duplicate['text'])
                    citations(duplicate['citations'], sources)
        edit_criterion(store, project, snapshot, selected, locked)
    else:
        st.info('No criteria in this view.')
    with st.expander('Add a requirement'):
        edit_criterion(store, project, snapshot, None, locked)
    if snapshot.get('criterion_removals'):
        with st.expander('Recorded criterion removals'):
            st.dataframe(list(snapshot['criterion_removals'].values()), hide_index=True)


def evidence_view(store, project, snapshot, locked):
    st.header('Evidence')
    assessments = effective_assessments(snapshot)
    sources = snapshot.get('evidence_sources', {})
    rows = [row for row in snapshot.get('criteria', []) if eligible(row)]
    if rows:
        summary, choices = [], {}
        for row in rows:
            rid = row['id']
            item = assessments.get(rid, {})
            status = item.get('status', 'awaiting assessment')
            summary.append({
                'Requirement ID': rid, 'Proof criterion': row['text'], 'Assessment': status,
                'Supporting sources': source_locations(item.get('citations', []), sources),
                'Reasoning': item.get('reason', ''),
            })
            choices[rid] = f"{row['chapter']} · {row['text']} · {status} · {rid}"
        st.dataframe(summary, hide_index=True, column_config={
            'Proof criterion': st.column_config.TextColumn(width=360),
            'Supporting sources': st.column_config.TextColumn(width=260),
            'Reasoning': st.column_config.TextColumn(width=300),
        })
        key = st.selectbox('Proof criterion', list(choices), format_func=choices.get)
        item = assessments.get(key, {})
        criterion = next(row for row in rows if row['id'] == key)
        manual = snapshot.get('evidence_edits', {}).get(key)
        if manual and not applicable_review(manual, criterion, sources):
            with st.expander('Previous assessment needs reconfirmation'):
                st.text(manual['reason'])
        st.write(item.get('reason', 'Prepare the project to assess this criterion.'))
        if item.get('citations'):
            with st.expander('Supporting passages'):
                citations(item['citations'], sources)
        if item.get('rejected'):
            with st.expander('Rejected candidates'):
                citations(item['rejected'], sources)
        listing = evidence_inventory()
        extra = st.selectbox('Browse another source', ['', *listing],
                             format_func=lambda name: listing[name].filename if name else 'Show current candidates')
        if extra:
            root = Path(project['folder']).parent
            selected_inventory = {extra: listing[extra]}
            indexed = corpus(store, root, selected_inventory, manifest(root, selected_inventory))
            index = LexicalIndex(indexed)
            sources.update({pid: index.citation(pid) for pid in index.entries})
            if not index.entries:
                st.caption(indexed[0].detail or 'No passages available.')
        with st.form('proof-' + key):
            labels = ['confirmed', 'partial', 'missing', 'search incomplete']
            status = st.selectbox('Assessment', labels, index=labels.index(item.get('status', labels[-1])))
            selected = st.multiselect('Evidence passages', list(sources),
                                      default=[pid for pid in item.get('citations', []) if pid in sources],
                                      format_func=lambda pid: f"{sources[pid]['filename']} · {sources[pid]['location']}")
            reason = st.text_area('Reviewer reasoning', item.get('reason', ''), max_chars=450)
            if st.form_submit_button('Save assessment', **review_action(project), disabled=locked):
                if len(selected) > 12:
                    st.error('Choose up to 12 passages for one criterion. Split the criterion if more are needed.')
                elif not reason.strip() or bool(selected) != (status in ('confirmed', 'partial')):
                    st.error('Provide a reason and citations for positive assessments.')
                else:
                    row = Proof(criterion=key, status=status, citations=selected, reason=reason, rejected=[]).model_dump()
                    row['basis'] = review_basis(criterion, selected, sources)
                    snapshot.setdefault('evidence_edits', {})[key] = row
                    snapshot.setdefault('evidence', {})[key] = row
                    save_edit(store, project, snapshot, 'upstream')
    else:
        st.info('This checklist has no proposal proof criteria.')
    reviewer = snapshot.get('reviewer', {})
    match = reviewer.get('match')
    st.subheader('Suggested reviewer')
    st.write(match['name'] if match else 'Local triage')
    if reviewer.get('tied'):
        st.caption('Candidates tied on core criteria and role suitability.')
    candidates = reviewer.get('candidates', [])
    if candidates:
        with st.expander(f'Reviewer candidates ({len(candidates)})'):
            candidate = st.selectbox('Candidate to inspect', candidates,
                                     format_func=lambda item: f"{item['name']} · {item['role']}")
            st.dataframe(candidate['reasons'], hide_index=True)
            citations(candidate['citations'], sources)
    with st.form('reviewer-override'):
        reviewer_name = st.text_input('Reviewer override', value=snapshot.get('reviewer_override') or '')
        if st.form_submit_button('Save reviewer', **review_action(project), disabled=locked):
            snapshot['reviewer_override'] = reviewer_name.strip()
            save_edit(store, project, snapshot, 'render')
    if snapshot.get('knowledge_gaps'):
        with st.expander('Knowledge base reading gaps'):
            st.dataframe(snapshot['knowledge_gaps'], hide_index=True)


def proposal_view(store, project, snapshot, locked):
    st.header('Proposal')
    with st.expander('Cover display text'):
        with st.form('cover-display'):
            cover = snapshot.get('cover', {})
            facts = snapshot.get('facts', {})
            values = {field: st.text_input(f'Cover {field}', cover.get(field) or facts.get(field, {}).get('value', ''),
                                          help=f'Mapped width guideline is {limit} characters.')
                      for field, limit in COVER_LIMITS.items()}
            if st.form_submit_button('Save cover', **review_action(project), disabled=locked):
                if any(len(value.strip()) > COVER_LIMITS[field] for field, value in values.items()):
                    st.error('Use shorter display text. The cited original stays in Requirements.')
                else:
                    snapshot['cover'] = {field: value.strip() for field, value in values.items()}
                    save_edit(store, project, snapshot, 'render')
    chapters = snapshot.get('chapters', [])
    if not chapters:
        st.info('Prepare proposals to build the editable slide plan.')
        return
    source_pool = snapshot.get('sources', {}) | snapshot.get('evidence_sources', {})
    slide_rows = [
        {'Slide group': group['title'], 'Slide text': claim['text'],
         'Claim type': 'manually reviewed' if claim['kind'] == 'manual' else claim['kind'],
         'Needs review': claim['kind'] != 'manual' and bool(claim['kind'] == 'open' or claim.get('review_issues')),
         'Role': group['role'], 'Requirement IDs': ', '.join(claim['requirements']),
         'Claim sources': source_locations(claim['sources'], source_pool)}
        for group in chapters for claim in group['claims']
    ]
    if slide_rows:
        st.dataframe(slide_rows, hide_index=True, column_config={
            'Slide group': st.column_config.TextColumn(width=190),
            'Slide text': st.column_config.TextColumn(width=360),
            'Requirement IDs': st.column_config.TextColumn(width=220),
            'Claim sources': st.column_config.TextColumn(width=260),
        })
    outline, editor = st.columns([1, 3])
    with outline:
        group_id = st.radio('Slide groups', [g['id'] for g in chapters],
                            format_func={g['id']: g['title'] for g in chapters}.get)
    group = next(g for g in chapters if g['id'] == group_id)
    criterion_map = {row['id']: row for row in snapshot.get('criteria', [])}
    with editor:
        with st.form('slide-' + group_id):
            new_title = st.text_input('Slide title', group['title'], max_chars=90)
            claims = []
            for i, existing in enumerate(group['claims']):
                claim_label = ' · '.join(existing['requirements'])
                needs_review = existing['kind'] != 'manual' and (existing['kind'] == 'open' or existing.get('review_issues'))
                if needs_review:
                    st.caption(f'Needs review · {claim_label}' if claim_label else 'Needs review')
                elif claim_label:
                    st.caption(claim_label)
                text = st.text_area('Slide text', existing['text'], max_chars=230, key=f'{group_id}-claim-{i}')
                resolved = st.checkbox('Confirmed wording and support reviewed', key=f'{group_id}-confirm-{i}',
                                       value=existing['kind'] == 'manual')
                possible = [
                    (set(criterion_map[rid]['citations']) |
                     set(snapshot.get('evidence', {}).get(rid, {}).get('citations', []))) & source_pool.keys()
                    for rid in existing['requirements'] if rid in criterion_map
                ]
                options = sorted(set.intersection(*possible)) if possible else []
                source_links = st.multiselect('Claim citations', options, default=[
                    sid for sid in existing['sources'] if sid in options
                ], key=f'{group_id}-sources-{i}',
                    format_func=lambda sid: f"{source_pool[sid]['location']} · {sid}")
                claims.append(dict(existing, text=text.strip(), sources=source_links, kind='manual' if resolved else
                                   ('open' if (existing['kind'] == 'manual' or text.strip() != existing['text']
                                               or set(source_links) != set(existing['sources']))
                                    else existing['kind'])))
            field_labels = {'timeline': 'Milestone | date', 'pricing': 'Pricing item | verified amount or terms',
                            'team': 'Role | confirmed team member'}
            fields = []
            valid_fields = True
            field_reviewer, field_basis = '', ''
            if field_label := field_labels.get(group['role']):
                existing_fields = '\n'.join(' | '.join(row) for row in group.get('fields', []))
                lines = st.text_area(field_label + ' · one per line', existing_fields)
                fields = [[value.strip() for value in line.split('|')] for line in lines.splitlines() if line.strip()]
                limit = 2 if group['role'] == 'team' else 3
                valid_fields = len(fields) <= limit and all(
                    len(row) == 2 and all(0 < len(value) <= 45 for value in row) for row in fields)
                prior_review = group.get('field_review') or {}
                field_reviewer = st.text_input('Commercial field reviewer', prior_review.get('reviewer', ''), max_chars=100)
                field_basis = st.text_area('Supporting decision or source', prior_review.get('basis', ''), max_chars=500)
            if st.form_submit_button('Save slide group', **review_action(project), disabled=locked):
                if not valid_fields:
                    st.error('Use the displayed two-field format and keep each field within 45 characters.')
                    return
                if new_title.strip() and all(claim['text'] for claim in claims):
                    group = dict(group, title=new_title.strip(), claims=claims, fields=fields)
                    group.pop('field_review', None)
                    if fields:
                        try:
                            group['field_review'] = confirm_fields(group, field_reviewer, field_basis)
                        except ValueError as error:
                            st.error(str(error))
                            return
                    snapshot['chapters'] = [group if item['id'] == group_id else item for item in chapters]
                    snapshot.setdefault('chapter_edits', {})[group_id] = group
                    save_edit(store, project, snapshot, 'render')
                else:
                    st.error('Enter a title and text for every slide field.')
        if st.button('Refresh this slide group', **review_action(project), disabled=locked):
            snapshot.setdefault('chapter_edits', {}).pop(group_id, None)
            snapshot['refresh_group'] = {'id': group_id, 'version': uuid4().hex}
            save_edit(store, project, snapshot, 'upstream')
        cited_claims = {i: claim for i, claim in enumerate(group['claims'], start=1) if claim['sources']}
        if cited_claims:
            with st.expander(f'Claim sources ({len(cited_claims)})'):
                claim_number = st.selectbox('Slide text to inspect', list(cited_claims), key=f'{group_id}-claim-source',
                                            format_func=lambda number: f"{number}. {cited_claims[number]['text']}")
                citations(cited_claims[claim_number]['sources'], source_pool)
        inactive = set(snapshot.get('chapter_edits', {})) - {g['id'] for g in chapters}
        if inactive:
            with st.expander('Saved edits from earlier slide groups'):
                for orphan in sorted(inactive):
                    item = snapshot['chapter_edits'][orphan]
                    st.write(item['title'])
                    st.write([claim['text'] for claim in item['claims']])


def show_open_issues(issues, requirements):
    if not issues:
        return

    def readable(text):
        return re.sub(r'\b[RH]-[0-9a-f]{20}\b',
                      lambda match: requirements.get(match[0], 'requirement wording unavailable'), text)

    rows = []
    for message, count in Counter(issues).items():
        reference, has_context, detail = message.partition(' · ')
        rid = reference.removeprefix('Evidence ')
        rows.append({
            'Issue': readable(detail if has_context else message),
            'Requirement or source': requirements.get(rid, readable(reference)) if has_context else '',
            'Occurrences': count,
        })
    with st.expander(f'Open issues ({len(issues)})'):
        st.caption('Reviewers can resolve these issues or approve the draft as it is.')
        st.dataframe(rows, hide_index=True, height='auto', column_config={
            'Issue': st.column_config.TextColumn(width=380),
            'Requirement or source': st.column_config.TextColumn(width=320),
            'Occurrences': None if all(row['Occurrences'] == 1 for row in rows) else st.column_config.NumberColumn(),
        })


def final_view(store, project, snapshot, locked):
    st.header('Final review')
    key = project['id']
    name = snapshot.get('reviewer_override') or (snapshot.get('reviewer', {}).get('match') or {}).get('name', 'Local triage')
    st.write(f'Reviewer · {name}')
    notice = next(iter(statuses(store, key)), None)
    if notice:
        label = {
            'failed': 'Review email could not be sent',
            'retry': 'Review email will retry',
            'uncertain': 'Review email delivery is unconfirmed',
        }.get(notice['state'], f"Review email · {notice['state']}")
        if notice['diagnostic']:
            with st.expander(label):
                st.caption(f"Delivery attempts · {notice['attempts']}")
                st.text(notice['diagnostic'])
        else:
            st.caption(label)
    issues = review_issues(snapshot)
    criteria = snapshot.get('criteria', [])
    requirements = {rid: row['text'] for rid, row in snapshot.get('criterion_removals', {}).items()}
    requirements.update({rid: row['text'] for rid, row in snapshot.get('criterion_edits', {}).items() if row})
    requirements.update({row['id']: row['text'] for row in criteria})
    show_open_issues(issues, requirements)
    if snapshot.get('knowledge_gaps'):
        with st.expander('Unavailable knowledge sources'):
            st.dataframe(snapshot['knowledge_gaps'], hide_index=True)
    for tab, label in (('requirements', 'Review requirements'), ('evidence', 'Check evidence'), ('proposal', 'Edit slides')):
        st.button(label, key='fix-' + tab,
                  on_click=lambda target=tab: st.query_params.from_dict({'project': key, 'tab': target}))
    chapters = snapshot.get('chapters', [])
    coverage_rows = (list(coverage(criteria, chapters, effective_assessments(snapshot))) if 'criteria' in snapshot
                     else snapshot.get('coverage', []))
    if coverage_rows:
        titles = {group['id']: group['title'] for group in chapters}
        with st.expander('Requirement coverage'):
            st.dataframe([
                {'Requirement': requirements.get(row['criterion'], 'Requirement wording unavailable'),
                 'Slide groups': ', '.join(dict.fromkeys(titles.get(link['group'], 'Slide group unavailable')
                                                        for link in row['chapters'])),
                 'Issue': row['issue']}
                for row in coverage_rows
            ], hide_index=True, column_config={'Requirement': st.column_config.TextColumn(width=420)})
    active_bytes = project['deck']
    if active_bytes:
        st.caption('SHA-256 · ' + project['checksum'])
        st.download_button('Download draft PowerPoint', active_bytes, file_name='Proposal-DRAFT.pptx', mime=PPTX_MIME)
        if st.button('Open in PowerPoint', **review_action(project)):
            try:
                open_local_deck(store.path.parent, active_bytes)
            except (OSError, ValueError) as error:
                st.warning(str(error))
        if st.button('Load deck preview', **review_action(project)):
            data = pdf_preview(project['checksum'], _deck=active_bytes)
            if data:
                st.pdf(data, height=600)
                st.download_button('Download PDF preview', data, file_name='Proposal-preview.pdf', mime='application/pdf')
            else:
                st.info('Local preview unavailable. Download the draft and open it in PowerPoint.')
        if not locked:
            with st.form('final-approval'):
                reviewer = st.text_input('Approval reviewer', '' if name == 'Local triage' else name)
                if st.form_submit_button('Approve current checksum', **review_action(project), disabled=bool(project['approval'])):
                    try:
                        approve_current(store, project, reviewer)
                    except (OSError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
        if project['approval'] and not locked:
            st.caption('Saved approval by ' + project['approval']['reviewer'])
            if st.button('Verify approved download', **review_action(project)):
                try:
                    verified = approved_download(store, project)
                except (OSError, ValueError) as error:
                    st.warning(str(error))
                else:
                    st.download_button('Download approved PowerPoint', verified, file_name='Proposal-APPROVED.pptx',
                                       mime=PPTX_MIME, on_click='ignore')
    elif snapshot:
        st.info('Generate the PowerPoint after reviewing saved changes.')
    else:
        st.info('Prepare this project to generate the first review draft.')
    if st.button('Regenerate from saved slides', **review_action(project), disabled=locked or not snapshot.get('content_key')
                 or snapshot.get('dirty') == 'upstream'):
        store.enqueue([key], 'render')
        location(key)
    with st.expander('Revision history'):
        st.dataframe(store.history(key), hide_index=True)
        usage = store.metrics(key)
        st.caption(f"{usage['calls']} model calls · {usage['input']:,} measured input tokens · "
                   f"{usage['output']:,} output tokens · {usage['thinking']:,} thinking tokens")


def review_tabs(project):
    snapshot = project['snapshot']
    if project['deck'] or snapshot.get('content_key'):
        return list(TABS)
    if 'chapters' in snapshot:
        return ['requirements', 'evidence', 'proposal']
    if 'evidence' in snapshot:
        return ['requirements', 'evidence']
    return ['requirements']


def run():
    st.set_page_config(page_title='TenderWorkbench', layout='wide')
    store = service(str(workspace_path()), config.get_option('server.port'))
    key = st.query_params.get('project')
    if not key:
        projects_view(store)
        return
    try:
        project = store.project(key)
    except ValueError as error:
        st.error(str(error))
        if st.button('Back to projects'):
            location()
        return
    row = jobs_table(store, [key])[0]
    if row['revision'] != project['revision']:
        st.rerun()
    submitted = st.session_state.pop('submitted_review', None)
    if submitted is not None and submitted != (key, project['revision'], project['checksum']):
        st.session_state['review_conflict'] = 'The project changed since you opened this view. Review the current revision and try again.'
        st.rerun()
    if message := st.session_state.pop('review_conflict', None):
        st.error(message)
    snapshot = project['snapshot']
    if st.sidebar.button('Projects'):
        location()
    busy = row['status'] in ACTIVE_JOB_STATUSES
    tabs = [row['workflow_stage']] if busy else review_tabs(project)
    tab = st.query_params.get('tab')
    if tab not in tabs:
        tab = tabs[-1]
    if len(tabs) == 1:
        st.sidebar.caption('Stage')
        st.sidebar.markdown(f'**{TABS[tab]}**')
    else:
        selected = st.sidebar.radio('Stage', tabs, index=tabs.index(tab),
                                    format_func=TABS.get, key='stage-' + key + '-' + tab)
        if selected != tab:
            location(key, selected)
    st.title(Path(project['folder']).name)
    st.caption(f"{project['phase']} · Revision {project['revision']}")
    label = ('Resume preparation' if row['status'] in ('failed', 'partial', 'stopped') else
             'Prepare proposals' if not project['deck'] else 'Check for input changes')
    if st.sidebar.button(label, type='primary', disabled=busy):
        store.enqueue([key], row['job_action'] if row['status'] == 'stopped' else 'reuse')
        location(key)
    if busy:
        st.header(TABS[tab])
        status_view(store, row)
        return
    status_view(store, row)
    if not snapshot:
        st.header(TABS[tab])
        if row['status'] is None:
            st.info('Prepare proposals to extract the tender requirements.')
        return
    views = dict(requirements=requirements_view, evidence=evidence_view, proposal=proposal_view, review=final_view)
    views[tab](store, project, snapshot, busy)
