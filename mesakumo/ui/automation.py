"""Projects and the four review screens for unattended tender preparation."""
from pathlib import Path
from uuid import uuid4

import streamlit as st
from pydantic import ValidationError
from streamlit import config

from ..automation import approve_current, prepare_inputs, start_worker
from ..automation_deck import open_local_deck, preview_pdf
from ..automation_evidence import Proof, corpus, eligible, manifest
from ..automation_mail import statuses
from ..automation_proposal import blockers
from ..automation_requirements import CHAPTERS, Criterion, parse_grounded
from ..automation_store import AutomationStore, fingerprint, workspace_path
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


def location(project=None, tab='review'):
    if project:
        st.query_params.from_dict({'project': project, 'tab': tab})
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
    identifiers = store.register(Path(root))
    if not identifiers:
        st.info('The selected folder has no Ausschreibung folders.')
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
        project_progress(store, identifiers)

    archived = [row for row in store.projects() if row['id'] not in identifiers]
    if archived:
        with st.expander('Projects from previous folders'):
            for row in archived:
                st.button(f"{Path(row['folder']).name} · {row['phase']}", key='old-' + row['id'],
                          on_click=lambda key=row['id']: st.query_params.from_dict({'project': key, 'tab': 'review'}))


@st.fragment(run_every=3)
def project_progress(store, identifiers):
    for row in jobs_table(store, identifiers):
        key = row['id']
        info, open_col = st.columns([4, 1])
        info.write(f"**{Path(row['folder']).name}** · {row['phase']} · {row['status'] or 'Not prepared'}")
        if row['materials'] is not None:
            info.caption(f"{row['materials']} materials · {row['gaps'] or 0} reading gaps"
                         + (' · Draft saved' if row['checksum'] else ''))
        if row['deadline']:
            info.caption('Deadline · ' + row['deadline'])
        if row['stage'] and row['status'] in ('queued', 'running'):
            info.caption(f"{row['stage']} · {row['done'] or 0}/{row['total'] or '—'}")
        if row['diagnostic']:
            info.warning(row['diagnostic'])
        if open_col.button('Open', key='open-' + key):
            location(key)


@st.fragment(run_every=2)
def status_view(store, key, original_status):
    row = jobs_table(store, [key])[0]
    if row['status'] != original_status and row['status'] not in ('queued', 'running'):
        st.rerun(scope='app')
    if row['status'] in ('queued', 'running'):
        st.info(f"{row['status'].capitalize()} · {row['stage'] or 'Preparing'} · "
                f"{row['done'] or 0}/{row['total'] or '—'}")
    if row['diagnostic']:
        st.warning(row['diagnostic'])


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
        save = st.form_submit_button('Save criterion', disabled=locked)
        remove = st.form_submit_button('Remove criterion', disabled=locked or row is None)
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
        snapshot.setdefault('criterion_edits', {})[key] = None
        snapshot['criteria'] = [r for r in snapshot['criteria'] if r['id'] != key]
        save_edit(store, project, snapshot, 'upstream')


def requirements_view(store, project, snapshot, locked):
    st.header('Tender requirements')
    with st.expander('Tender brief', expanded=True):
        for label, item in snapshot.get('facts', {}).items():
            st.write(f"**{label.capitalize()}** · {item['value']}")
            citations(item['citations'], snapshot.get('sources', {}))
            for alternative in item.get('alternatives', []):
                st.write(alternative['value'])
                citations(alternative['citations'], snapshot.get('sources', {}))
    gaps = snapshot.get('reading_gaps', [])
    with st.expander(f'Reading status · {len(gaps)} gaps'):
        st.dataframe(snapshot.get('manifest', []), hide_index=True)
        for gap in gaps:
            signature = fingerprint({k: v for k, v in gap.items() if k != 'resolution'})
            st.warning(f"{gap['file']} · {gap['detail']}")
            with st.form('gap-' + signature):
                note = st.text_input('Resolution and reviewer', value=gap.get('resolution') or '')
                if st.form_submit_button('Record resolution', disabled=locked):
                    if not note.strip():
                        st.error('Enter the resolution before saving.')
                    else:
                        snapshot.setdefault('gap_resolutions', {})[signature] = note.strip()
                        gap['resolution'] = note.strip()
                        save_edit(store, project, snapshot, 'render')
    for conflict in snapshot.get('conflicts', []):
        st.warning(conflict)
    if snapshot.get('conflicts'):
        with st.form('review-conflicts'):
            note = st.text_area('Resolve listed conflicts with explanation and source references')
            if st.form_submit_button('Record conflict review', disabled=locked):
                if note.strip():
                    snapshot.setdefault('conflict_reviews', {})[fingerprint(snapshot['conflicts'])] = note.strip()
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
        selected = st.selectbox('Requirement to inspect', visible, format_func=lambda r: r['text'])
        st.caption(f"{selected['id']} · {selected['obligation']} · {selected['response']} · "
                   f"{selected.get('duplicates', 0)} merged repeats")
        citations(selected['citations'], snapshot.get('sources', {}))
        if selected.get('superseded'):
            with st.expander('Explicitly superseded clauses'):
                for old in selected['superseded']:
                    st.write(old['conditions'])
                    citations(old['citations'], snapshot.get('sources', {}))
        if selected.get('merged'):
            with st.expander('Equivalent wording from other sources'):
                for duplicate in selected['merged']:
                    st.write(duplicate['text'])
                    citations(duplicate['citations'], snapshot.get('sources', {}))
        edit_criterion(store, project, snapshot, selected, locked)
    else:
        st.info('No criteria in this view.')
    with st.expander('Add a requirement'):
        edit_criterion(store, project, snapshot, None, locked)


def evidence_view(store, project, snapshot, locked):
    st.header('Evidence')
    assessments = snapshot.get('evidence', {})
    rows = [row for row in snapshot.get('criteria', []) if eligible(row)]
    choices = {row['id']: f"{row['chapter']} · {row['text']} · "
               f"{assessments.get(row['id'], {}).get('status', 'awaiting assessment')}" for row in rows}
    if choices:
        key = st.selectbox('Proof criterion', list(choices), format_func=choices.get)
        item = assessments.get(key, {})
        sources = snapshot.get('evidence_sources', {})
        st.write(item.get('reason', 'Prepare the project to assess this criterion.'))
        citations(item.get('citations', []), sources)
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
            if st.form_submit_button('Save assessment', disabled=locked):
                if not reason.strip() or bool(selected) != (status in ('confirmed', 'partial')):
                    st.error('Provide a reason and citations for positive assessments.')
                else:
                    row = Proof(criterion=key, status=status, citations=selected, reason=reason, rejected=[]).model_dump()
                    snapshot.setdefault('evidence_edits', {})[key] = row
                    assessments[key] = row
                    save_edit(store, project, snapshot, 'upstream')
    else:
        st.info('This checklist has no proposal proof criteria.')
    reviewer = snapshot.get('reviewer', {})
    match = reviewer.get('match')
    st.subheader('Suggested reviewer')
    st.write(match['name'] if match else 'Local triage')
    if reviewer.get('tied'):
        st.caption('Candidates tied on core criteria and role suitability.')
    if reviewer.get('candidates'):
        for candidate in reviewer['candidates']:
            with st.expander(candidate['name'] + ' · ' + candidate['role']):
                st.dataframe(candidate['reasons'], hide_index=True)
                citations(candidate['citations'], snapshot.get('evidence_sources', {}))
    with st.form('reviewer-override'):
        reviewer_name = st.text_input('Reviewer override', value=snapshot.get('reviewer_override') or '')
        if st.form_submit_button('Save reviewer', disabled=locked):
            snapshot['reviewer_override'] = reviewer_name.strip()
            save_edit(store, project, snapshot, 'render')
    if snapshot.get('knowledge_gaps'):
        with st.expander('Knowledge base reading gaps'):
            st.dataframe(snapshot['knowledge_gaps'], hide_index=True)


def proposal_view(store, project, snapshot, locked):
    st.header('Proposal')
    chapters = snapshot.get('chapters', [])
    if not chapters:
        st.info('Prepare proposals to build the editable slide plan.')
        return
    outline, editor = st.columns([1, 3])
    with outline:
        group_id = st.radio('Slide groups', [g['id'] for g in chapters],
                            format_func={g['id']: g['title'] for g in chapters}.get)
    group = next(g for g in chapters if g['id'] == group_id)
    source_pool = snapshot.get('sources', {}) | snapshot.get('evidence_sources', {})
    criterion_map = {row['id']: row for row in snapshot.get('criteria', [])}
    with editor:
        with st.form('slide-' + group_id):
            new_title = st.text_input('Slide title', group['title'], max_chars=90)
            claims = []
            for i, existing in enumerate(group['claims']):
                st.caption(' · '.join(existing['requirements']))
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
            if field_label := field_labels.get(group['role']):
                existing_fields = '\n'.join(' | '.join(row) for row in group.get('fields', []))
                lines = st.text_area(field_label + ' · one per line', existing_fields)
                fields = [[value.strip() for value in line.split('|')] for line in lines.splitlines() if line.strip()]
                limit = 2 if group['role'] == 'team' else 3
                valid_fields = len(fields) <= limit and all(
                    len(row) == 2 and all(0 < len(value) <= 45 for value in row) for row in fields)
            if st.form_submit_button('Save slide group', disabled=locked):
                if not valid_fields:
                    st.error('Use the displayed two-field format and keep each field within 45 characters.')
                    return
                if new_title.strip() and all(claim['text'] for claim in claims):
                    group.update(title=new_title.strip(), claims=claims, fields=fields)
                    snapshot.setdefault('chapter_edits', {})[group_id] = group
                    save_edit(store, project, snapshot, 'render')
                else:
                    st.error('Enter a title and text for every slide field.')
        if st.button('Refresh this slide group', disabled=locked):
            snapshot.setdefault('chapter_edits', {}).pop(group_id, None)
            snapshot['refresh_group'] = {'id': group_id, 'version': uuid4().hex}
            save_edit(store, project, snapshot, 'upstream')
        for claim in group['claims']:
            if claim['kind'] == 'open':
                st.warning(claim['text'])
            with st.expander('Claim sources · ' + ', '.join(claim['requirements'])):
                citations(claim['sources'], snapshot.get('sources', {}) | snapshot.get('evidence_sources', {}))
        inactive = set(snapshot.get('chapter_edits', {})) - {g['id'] for g in chapters}
        if inactive:
            with st.expander('Saved edits from earlier slide groups'):
                for orphan in sorted(inactive):
                    item = snapshot['chapter_edits'][orphan]
                    st.write(item['title'])
                    st.write([claim['text'] for claim in item['claims']])


def final_view(store, project, snapshot, locked):
    st.header('Final review')
    key = project['id']
    name = snapshot.get('reviewer_override') or (snapshot.get('reviewer', {}).get('match') or {}).get('name', 'Local triage')
    st.write(f'Reviewer · {name}')
    for notice in statuses(store, key)[:1]:
        st.caption(f"Notification · {notice['state']} · {notice['attempts']} attempts")
        if notice['diagnostic']:
            st.warning(notice['diagnostic'])
    for issue in blockers(snapshot):
        st.warning(issue)
    if snapshot.get('knowledge_gaps'):
        with st.expander('Unavailable knowledge sources'):
            st.dataframe(snapshot['knowledge_gaps'], hide_index=True)
    for tab, label in (('requirements', 'Review requirements'), ('evidence', 'Check evidence'), ('proposal', 'Edit slides')):
        st.button(label, key='fix-' + tab,
                  on_click=lambda target=tab: st.query_params.from_dict({'project': key, 'tab': target}))
    if snapshot.get('coverage'):
        with st.expander('Requirement coverage'):
            st.dataframe([dict(criterion=r['criterion'], chapters=', '.join(x['group'] for x in r['chapters']),
                               issue=r['issue'], blocker=r['blocker']) for r in snapshot['coverage']], hide_index=True)
    active_bytes = project['deck']
    if active_bytes:
        st.caption('SHA-256 · ' + project['checksum'])
        st.download_button('Download draft PowerPoint', active_bytes, file_name='Proposal-DRAFT.pptx', mime=PPTX_MIME)
        if st.button('Open in PowerPoint'):
            try:
                open_local_deck(store.path.parent, active_bytes)
            except (OSError, ValueError) as error:
                st.warning(str(error))
        if st.button('Load deck preview'):
            data = pdf_preview(project['checksum'], _deck=active_bytes)
            if data:
                st.pdf(data, height=600)
                st.download_button('Download PDF preview', data, file_name='Proposal-preview.pdf', mime='application/pdf')
            else:
                st.info('Local preview unavailable. Download the draft and open it in PowerPoint.')
        if not blockers(snapshot) and not locked:
            with st.form('final-approval'):
                reviewer = st.text_input('Approval reviewer', '' if name == 'Local triage' else name)
                if st.form_submit_button('Approve current checksum', disabled=bool(project['approval'])):
                    try:
                        approve_current(store, project, reviewer)
                    except (OSError, ValueError) as error:
                        st.error(str(error))
                    else:
                        st.rerun()
        if project['approval'] and not locked:
            try:
                current_key = prepare_inputs(project, snapshot, store).key
                if current_key == project['input_key']:
                    st.success('Approved by ' + project['approval']['reviewer'])
                    st.download_button('Download approved PowerPoint', active_bytes, file_name='Proposal-APPROVED.pptx',
                                       mime=PPTX_MIME)
                else:
                    st.warning('Project files have changed since approval. Prepare a current deck.')
            except (OSError, ValueError) as error:
                st.warning(str(error))
    elif snapshot:
        st.info('Generate the PowerPoint after reviewing saved changes.')
    else:
        st.info('Prepare this project to generate the first review draft.')
    if st.button('Regenerate from saved slides', disabled=locked or not snapshot.get('content_key')
                 or snapshot.get('dirty') == 'upstream'):
        store.enqueue([key], 'render')
        st.rerun()
    with st.expander('Revision history'):
        st.dataframe(store.history(key), hide_index=True)
        usage = store.metrics(key)
        st.caption(f"{usage['calls']} model calls · {usage['input']:,} measured input tokens · "
                   f"{usage['output']:,} output tokens · {usage['thinking']:,} thinking tokens")


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
    snapshot = project['snapshot']
    tab = st.query_params.get('tab', 'review')
    if tab not in TABS:
        tab = 'review'
    if st.sidebar.button('Projects'):
        location()
    selected = st.sidebar.radio('Stage', list(TABS), index=list(TABS).index(tab),
                                format_func=TABS.get, key='stage-' + key + '-' + tab)
    if selected != tab:
        location(key, selected)
    st.title(Path(project['folder']).name)
    st.caption(f"{project['phase']} · Revision {project['revision']}")
    row = jobs_table(store, [key])[0]
    busy = row['status'] in ('queued', 'running')
    label = ('Resume preparation' if row['status'] in ('failed', 'partial') else
             'Prepare proposals' if not project['deck'] else 'Check for input changes')
    if st.sidebar.button(label, type='primary', disabled=busy):
        store.enqueue([key])
        st.rerun()
    status_view(store, key, row['status'])
    views = dict(requirements=requirements_view, evidence=evidence_view, proposal=proposal_view, review=final_view)
    views[tab](store, project, snapshot, busy)
