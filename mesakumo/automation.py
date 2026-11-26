"""Restart-safe orchestration of the local tender-to-deck workflow."""
import copy
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from . import automation_deck as deck
from . import automation_evidence as evidence
from . import automation_proposal as proposal
from . import automation_requirements as requirements
from .automation_mail import dispatch, notice_for
from .automation_model import ModelCalls, model_profile, recoverable_content
from .automation_store import fingerprint
from .evidence import evidence_inventory
from .final_export import read_template
from .gemini import ProviderError
from .models import DisabledModel, ModelUnavailable, configured_model
from .persistence import ConflictError

LOG = logging.getLogger(__name__)
WORKFLOW = 'automation-5'


def get_template(root):
    try:
        return read_template(root), None
    except (OSError, ValueError) as error:
        return None, str(error)


def language_label(language):
    return 'English' if language == 'en' else 'German'


@dataclass
class Inputs:
    rows: list
    inventory: dict
    knowledge: dict
    profiles: list
    template: bytes | None
    template_hash: str | None
    template_issue: str | None
    language: str
    content_key: str
    key: str


def prepare_inputs(project, snapshot, store, *, capture=False):
    folder = Path(project['folder'])
    if not folder.is_dir():
        raise ValueError('The tender folder is unavailable. Restore the folder and retry.')
    root = folder.parent
    rows, manifest = requirements.read_manifest(folder, project['phase'], capture=capture)
    inventory = evidence_inventory()
    knowledge = evidence.manifest(root, inventory)
    profile_path = store.path.parent / 'reviewer_profiles.json'
    profiles, profile_hash = evidence.profile_data(profile_path)
    template, template_issue = get_template(root)
    template_hash = hashlib.sha256(template).hexdigest() if template else None
    requested_language = snapshot.get('language_preference') or store.setting('language', 'auto')
    content_key = fingerprint([WORKFLOW, requirements.PARSER, requirements.EXTRACTION, evidence.ASSESSMENT,
                               proposal.PLAN_VERSION, project['phase'], manifest, knowledge,
                               {path: s.role for path, s in inventory.items()}, requested_language, profile_hash,
                               requirements.ocr_profile(), os.environ.get('MESAKUMO_EXTRACTION_BYTES')])
    input_key = fingerprint([content_key, deck.RENDERER, template_hash, template_issue])
    return Inputs(rows, inventory, knowledge, profiles, template, template_hash, template_issue,
                  requested_language, content_key, input_key)


def apply_edits(criteria, edits, sources):
    result, conflicts = [], []
    for row in criteria:
        edit = edits.get(row['id'])
        if edit is None:
            if row['id'] not in edits:
                result.append(row)
            continue
        if set(edit['citations']) <= sources.keys():
            result.append(dict(row, **{k: v for k, v in edit.items() if k != 'id'}))
        else:
            result.append(row)
            conflicts.append(f"Saved edit {row['id']} needs current tender citations.")
    current = {row['id'] for row in criteria}
    for key, edit in edits.items():
        if edit is not None and key not in current:
            if set(edit['citations']) <= sources.keys():
                result.append(edit)
            else:
                conflicts.append(f'Saved edit {key} needs current tender citations.')
    return result, conflicts


def prepare(store, key, action, token, model_factory=configured_model, *, port=8501):
    project = store.project(key)
    snapshot = copy.deepcopy(project['snapshot'])
    folder, phase = Path(project['folder']), project['phase']

    def progress(stage, done=0, total=0):
        store.progress(key, token, stage, done, total)

    progress('Checking inputs')
    inputs = prepare_inputs(project, snapshot, store)
    retry_content = action == 'reuse' and bool(snapshot.get('extraction_gaps') or snapshot.get('generation_gaps'))
    if (action == 'reuse' and inputs.key == project['input_key'] and project['deck']
            and not snapshot.get('dirty') and not retry_content):
        return
    same_content = snapshot.get('content_key') == inputs.content_key and snapshot.get('dirty') != 'upstream'
    if action == 'render' and not same_content:
        raise ValueError('Preparation inputs changed. Prepare proposals to validate the current content first.')
    snapshot.update(project_name=folder.name, phase=phase)
    language = inputs.language
    active = action == 'update' or not same_content or retry_content
    if active:
        captured = prepare_inputs(project, snapshot, store, capture=True)
        if captured.key != inputs.key:
            raise ConflictError('Sources changed during preparation. Retry with the current material.')
        inputs = captured
        generation_gaps = []
        model = model_factory()
        profile = model_profile(model)
        model_key = snapshot.get('model_profile', profile) if action == 'reuse' else profile
        snapshot.update(model_profile=model_key, effective_model=profile)
        call_service = ModelCalls(store, model, key, progress)

        def call(*args, **kwargs):
            if isinstance(model, DisabledModel):
                raise ModelUnavailable('Configure an approved model to prepare new material.')
            if model_key != profile:
                raise ModelUnavailable('Saved work uses another model. Use Update with current model to generate changed content.')
            return call_service(*args, **kwargs)

        def unit(stage, inputs, work):
            cache_key = fingerprint(inputs)
            saved = store.cached(key, stage, cache_key)
            if saved is not None:
                return saved
            value = work()
            store.save_unit(key, stage, cache_key, value, token)
            return value

        def extract(batch):
            def request():
                try:
                    return requirements.extract_batch(call, batch, phase, language_label(language) if language != 'auto' else 'the language of the cited source')
                except (ProviderError, ValueError) as error:
                    if not recoverable_content(error) or len(batch) < 2:
                        raise
                    return {'split': True}

            try:
                result = unit('extraction', [requirements.EXTRACTION, model_key, phase,
                                             'auto-source-language-1' if language == 'auto' else language, batch], request)
            except (ProviderError, ValueError) as error:
                if isinstance(error, ConflictError) or not recoverable_content(error):
                    raise
                source = next(iter(batch.values()))
                extraction_gaps.append(dict(file=source['file'], detail=f"{source['location']}: {error}"))
                return {'criteria': [], 'facts': []}
            if not result.get('split'):
                return result
            entries = list(batch.items())
            pivot = len(entries) // 2
            parts = [extract(dict(entries[:pivot])), extract(dict(entries[pivot:]))]
            return {name: parts[0][name] + parts[1][name] for name in ('criteria', 'facts')}

        def write_chapter(role, label, draft_inputs, source_inputs, version):
            def request():
                try:
                    return proposal.draft_chapter(call, role, label, draft_inputs, source_inputs)
                except (ProviderError, ValueError) as error:
                    if not recoverable_content(error) or len(draft_inputs) < 2:
                        raise
                    return {'split': True}

            try:
                saved = unit('chapter', [proposal.PLAN_VERSION, model_key, role, label, draft_inputs, source_inputs,
                                         version], request)
            except (ProviderError, ValueError) as error:
                if isinstance(error, ConflictError) or not recoverable_content(error):
                    raise
                generation_gaps.append(dict(file='Chapter ' + role, detail=str(error)))
                i = requirements.CHAPTERS.index(role)
                wording = 'Provide a validated response.' if label == 'English' else 'Geprüfte Antwort ergänzen.'
                return dict(title=proposal.TITLES['en' if label == 'English' else 'de'][i],
                            claims=[dict(text=wording, kind='open', requirements=list(draft_inputs), sources=[])])
            if not saved.get('split'):
                return saved
            items = list(draft_inputs.items())
            pivot = len(items) // 2
            drafts = []
            for part in (dict(items[:pivot]), dict(items[pivot:])):
                cited = {sid for criterion in part.values() for sid in criterion['citations']}
                excerpts = {sid: source_inputs[sid] for sid in sorted(cited)}
                drafts.append(write_chapter(role, label, part, excerpts, version))
            return dict(title=drafts[0]['title'], claims=drafts[0]['claims'] + drafts[1]['claims'])

        try:
            progress('Reading tender')
            manifest, sources, gaps = requirements.reading(inputs.rows, unit)
            snapshot.update(manifest=manifest, sources=sources, reading_gaps=gaps)
            if not sources:
                raise ValueError('No readable tender source was found.')
            budget = int(os.environ.get('MESAKUMO_EXTRACTION_BYTES') or 16000)
            if budget < 4000:
                raise ValueError('Use at least 4000 bytes for extraction groups.')
            chunks = list(requirements.batches(sources, budget))
            extracted, extraction_gaps = [], []
            for index, batch in enumerate(chunks):
                progress('Extracting criteria', index, len(chunks))
                extracted.append(extract(batch))
            snapshot['extraction_gaps'] = extraction_gaps
            criteria, facts, conflicts = requirements.consolidate(extracted, sources)
            progress('Checking uncertain criteria')
            rechecked = requirements.review_uncertain(criteria, sources, unit, call, model_key)
            progress('Consolidating equivalent criteria')
            rechecked = requirements.merge_paraphrases(rechecked, unit, call, model_key)
            criteria, edit_conflicts = apply_edits(rechecked, snapshot.get('criterion_edits', {}), sources)
            for item in gaps:
                item['resolution'] = snapshot.get('gap_resolutions', {}).get(fingerprint(item))
            language, language_issue = requirements.output_language(language, facts, sources)
            internal = requirements.planning_context(inputs.rows)
            snapshot.update(criteria=criteria, facts=facts, conflicts=conflicts + edit_conflicts, language=language,
                            language_issue=language_issue, internal_planning=internal)
            if internal:
                snapshot['conflicts'].append('Review internal planning assumptions and confirm any proposed partner commitments.')
            if not criteria:
                snapshot['conflicts'].append('No criteria were extracted. Review the tender before approval.')

            progress('Indexing evidence')
            readings = evidence.corpus(store, folder.parent, inputs.inventory, inputs.knowledge)
            snapshot['knowledge_gaps'] = [dict(file=r.source.relative_path, detail=r.detail)
                                          for r in readings if r.status != 'Read']
            assessments, citations, index = evidence.match(
                store, key, token, call, model_key, criteria, readings, snapshot.get('evidence_edits', {}), progress, gaps=generation_gaps)
            profiles = inputs.profiles or evidence.detected_profiles(readings)
            reviewer = evidence.suggest_reviewer(profiles, criteria, index)
            for candidate in reviewer['candidates']:
                citations.update({pid: index.citation(pid) for pid in candidate['citations']})
            snapshot.update(evidence=assessments, evidence_sources=citations, reviewer=reviewer)

            plan = list(proposal.chapter_units(criteria))
            generated = {group['id']: group for group in snapshot.get('chapter_edits', {}).values()}
            groups = []
            refresh = snapshot.get('refresh_group', {})
            chapter_versions = snapshot.setdefault('chapter_versions', {})
            if refresh:
                chapter_versions[refresh['id']] = refresh['version']
            for count, (role, group_id, items) in enumerate(plan):
                progress('Drafting chapters', count, len(plan))
                draft_inputs, source_inputs = proposal.plan_context(items, sources, citations, assessments)
                manual = generated.pop(group_id, None)
                if manual is not None:
                    group = manual
                    if group['context_key'] != fingerprint([draft_inputs, source_inputs]):
                        snapshot['conflicts'].append(f'Review saved wording in {group_id} after its inputs changed.')
                else:
                    group = write_chapter(role, language_label(language), draft_inputs, source_inputs,
                                          chapter_versions.get(group_id))
                groups.append(dict(group, id=group_id, role=role,
                                   context_key=fingerprint([draft_inputs, source_inputs])))
            if generated:
                snapshot['conflicts'].append('Saved slide edits need reassignment after the chapter structure changed.')
            snapshot['chapters'] = groups
            snapshot['generation_gaps'] = generation_gaps
            snapshot.pop('refresh_group', None)
            snapshot['content_key'] = inputs.content_key
        except (ModelUnavailable, ValueError, OSError) as error:
            # Leave last committed content visible. Unit checkpoints remain available to resume.
            if not project['deck']:
                snapshot['dirty'] = 'upstream'
                store.save_revision(key, project['revision'], snapshot, project['input_key'], token=token)
            raise error

    snapshot['coverage'] = list(proposal.coverage(snapshot.get('criteria', []), snapshot.get('chapters', []),
                                                  snapshot.get('evidence', {})))
    snapshot.update(template_hash=inputs.template_hash, render_issues=[], dirty=None)
    progress('Rendering PowerPoint')
    result = None
    try:
        if inputs.template is None:
            raise ValueError(inputs.template_issue or 'A readable Angebotstemplate.pptx is required.')
        result, issues = deck.render(inputs.template, folder.name, phase, snapshot)
        snapshot['render_issues'] = issues
    except (OSError, ValueError, KeyError) as error:
        snapshot['render_issues'] = [str(error)]
        snapshot['dirty'] = 'render'
    except Exception as error:
        snapshot['render_issues'] = [f'PowerPoint renderer failed ({type(error).__name__}).']
        snapshot['dirty'] = 'render'
    notice = notice_for(key, project, snapshot, port) if result else None
    store.save_revision(key, project['revision'], snapshot, inputs.key, result, token=token, notice=notice)
    if result is None:
        raise ValueError('PowerPoint render needs attention. The project work was saved.')


def run_once(store, model_factory=configured_model, *, port=8501):
    job = store.claim()
    if not job:
        return False
    key, action, token = job
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(min(15, store.lease / 3)):
            try:
                if not store.heartbeat(key, token):
                    return
            except OSError:
                LOG.warning('Could not extend tender job lease.')

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        prepare(store, key, action, token, model_factory, port=port)
        prepared = store.project(key)['snapshot']
        gaps = prepared.get('extraction_gaps') or prepared.get('generation_gaps')
        store.finish(key, token, 'partial' if gaps else 'ready',
                     'Some preparation units need a retry.' if gaps else None)
    except (ModelUnavailable, ValueError, OSError) as error:
        try:
            store.finish(key, token, 'failed', str(error))
        except ConflictError:
            pass
    except Exception as error:
        LOG.error('Tender preparation failed (%s).', type(error).__name__)
        try:
            store.finish(key, token, 'failed', 'Unexpected preparation error (' + type(error).__name__ + ').')
        except ConflictError:
            pass
    finally:
        stop.set()
        thread.join()
    return True


def work_loop(store, port):
    idle = threading.Event()
    while True:
        try:
            worked = run_once(store, port=port)
            delivered = dispatch(store)
            if worked or delivered:
                continue
        except Exception as error:
            LOG.error('Workstation worker error (%s).', type(error).__name__)
        idle.wait(1)


def start_worker(store, port):
    thread = threading.Thread(target=work_loop, args=(store, port), daemon=True, name='tender-worker')
    thread.start()
    return thread


def approve_current(store, project, reviewer):
    inputs = prepare_inputs(project, project['snapshot'], store)
    if inputs.key != project['input_key']:
        raise ConflictError('Inputs changed since this deck was generated. Prepare a new draft.')
    return store.approve(project['id'], project['revision'], project['checksum'], reviewer,
                         template_hash=inputs.template_hash, blockers=proposal.blockers)


def approved_download(store, project):
    """Verify freshness at the action that issues the exact approved bytes."""
    if not project.get('approval') or not project.get('deck'):
        raise ConflictError('Approve the current deck before downloading it.')
    if proposal.blockers(project['snapshot']):
        raise ConflictError('Resolve final blockers before downloading the approved deck.')
    inputs = prepare_inputs(project, project['snapshot'], store)
    if inputs.key != project['input_key']:
        raise ConflictError('Project files have changed since approval. Prepare a current deck.')
    current = store.project(project['id'])
    checksum = project['checksum']
    if (current['revision'] != project['revision'] or current['checksum'] != checksum
            or (current['approval'] or {}).get('checksum') != checksum
            or hashlib.sha256(current['deck'] or b'').hexdigest() != checksum):
        raise ConflictError('The approved deck changed while it was being checked.')
    return current['deck']
