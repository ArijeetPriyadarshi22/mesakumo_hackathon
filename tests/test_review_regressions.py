"""Synthetic regressions for the automated tender workflow review."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from mesakumo.automation_store import AutomationStore
from mesakumo.discovery import ROOT
from test_automation import criterion


class ReviewActions(unittest.TestCase):
    def test_stale_approval_and_edit_forms_reject_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Ausschreibung 1').mkdir()
            store = AutomationStore(root / 'workspace.sqlite3')
            project, = store.register(root)
            row = dict(criterion('Supply two references', 'T', response='proof'), id='R')
            snapshot = dict(criteria=[row], sources={'T': dict(file='tender.txt', quote='Two references', location='L1')},
                            chapters=[dict(id='chapter', title='Original', role='approach',
                                           claims=[dict(text='Verified references', kind='manual', requirements=['R'], sources=[])])],
                            evidence={'R': dict(status='confirmed')},
                            template_hash='template')

            def open_tab(tab):
                app = AppTest.from_file(str(ROOT / 'mesakumo/streamlit_app.py'))
                app.query_params = {'project': project, 'tab': tab}
                return app.run()

            with patch.dict(os.environ, {'MESAKUMO_AUTOMATION_DIR': directory}), patch(
                'mesakumo.ui.automation.start_worker'
            ), patch('mesakumo.automation.prepare_inputs', return_value=SimpleNamespace(key='inputs', template_hash='template')):
                for tab, label in [('review', 'Approve current checksum'), ('requirements', 'Remove criterion'),
                                   ('evidence', 'Save assessment'), ('proposal', 'Save slide group')]:
                    with self.subTest(tab=tab):
                        prior = store.project(project)['revision']
                        displayed = store.save_revision(project, prior, snapshot, 'inputs', b'Deck A')
                        app = open_tab(tab)
                        if tab == 'review':
                            next(w for w in app.text_input if w.label == 'Approval reviewer').set_value('Consultant')
                        if tab == 'evidence':
                            next(w for w in app.text_area if w.label == 'Reviewer reasoning').set_value('Old assessment')
                        newer = store.save_revision(project, displayed, snapshot, 'inputs', b'Deck B')
                        next(w for w in app.button if w.label == label).click().run()
                        result = store.project(project)
                        self.assertIsNone(result['approval'])
                        self.assertEqual(result['revision'], newer)
                        self.assertTrue(any('changed' in w.value.lower() for w in app.error))
                fresh = open_tab('review')
                next(w for w in fresh.text_input if w.label == 'Approval reviewer').set_value('Consultant')
                next(w for w in fresh.button if w.label == 'Approve current checksum').click().run()
                self.assertEqual(store.project(project)['approval']['checksum'], store.history(project)[0]['checksum'])


class IntakeAndCoverage(unittest.TestCase):
    def test_mandatory_proposal_none_is_not_approvable(self):
        from mesakumo.automation_proposal import blockers, coverage
        from mesakumo.automation_requirements import parse_grounded

        for scope, obligation, blocked in [('proposal', 'mandatory', True), ('proposal', 'scored', True),
                                            ('proposal', 'informational', False), ('administrative', 'mandatory', False)]:
            with self.subTest(scope=scope, obligation=obligation):
                row = dict(criterion('Explain the approach', 'T', scope=scope, response='none'),
                           id='R', obligation=obligation)
                self.assertEqual(bool(list(coverage([row], [], {}))[0]['blocker']), blocked)
                parsed = parse_grounded(dict(criteria=[row], facts=[]), {'T': {}})['criteria'][0]
                self.assertEqual(bool(blockers(dict(criteria=[parsed], coverage=[]))), blocked)

    def test_supporting_zip_does_not_register_offer_phase(self):
        from zipfile import ZipFile
        from mesakumo.automation_store import discover

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'Ausschreibung 1'
            pack.mkdir()
            with ZipFile(pack / 'supporting-photos.zip', 'w') as archive:
                archive.writestr('photo.png', b'fixture')
            self.assertEqual([phase for _, _, phase in discover(root)], ['single'])


    def test_declared_folder_and_tender_three_phase_manifests(self):
        from zipfile import ZipFile
        from mesakumo.automation_requirements import read_manifest
        from mesakumo.automation_store import discover

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'Unfamiliar tender'
            pack.mkdir()
            (pack / '.tender-phases.json').write_text('{"offer_archives":["specification.zip"]}')
            for filename in ('specification.zip', 'common.zip'):
                with ZipFile(pack / filename, 'w') as archive:
                    archive.writestr('requirements.txt', filename)
            projects = list(discover(root))
            self.assertEqual([phase for _, _, phase in projects], ['participation', 'offer'])
            manifests = {phase: {r['path'] for r in read_manifest(pack, phase)[0]} for _, _, phase in projects}
            self.assertIn('common.zip!/requirements.txt', manifests['participation'])
            self.assertNotIn('specification.zip!/requirements.txt', manifests['participation'])
            self.assertIn('specification.zip!/requirements.txt', manifests['offer'])
            (pack / 'specification.zip').unlink()
            missing_offer, _ = read_manifest(pack, 'offer')
            self.assertTrue(any(row['path'] == 'specification.zip' and row['error'] for row in missing_offer))
        known = next(ROOT.glob('mesakumo_Input*/Ausschreibung 3'), None)
        if known:
            phases = [phase for _, path, phase in discover(known.parent) if path == known]
            self.assertEqual(phases, ['participation', 'offer'])
            manifests = {phase: read_manifest(known, phase)[0] for phase in phases}
            self.assertFalse(any('!/ ' in r['path'] or '!/' in r['path'] for r in manifests['participation']))
            self.assertEqual(sum('!/' in r['path'] for r in manifests['offer']), 9)

    def test_mixed_submission_language_and_local_partner_context(self):
        from mesakumo.automation_requirements import output_language, planning_context, read_manifest, reading

        for text, expected in [('The tender requires the bid and the schedules.', 'en'),
                               ('Das Angebot und die Anlage sind erforderlich.', 'de')]:
            self.assertEqual(output_language('auto', {}, {'T': {'quote': text}}), (expected, ''))
        mixed = {'T': {'quote': 'The bid and das Angebot require submission in English.'}}
        self.assertEqual(output_language('auto', {'language': {'value': 'English'}}, mixed), ('en', ''))
        self.assertTrue(output_language('auto', {'language': {'value': 'French'}}, mixed)[1])
        self.assertTrue(output_language('auto', {}, mixed)[1])
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory) / 'Ausschreibung 2'
            pack.mkdir()
            (pack / 'Bearbeitungshinweis.txt').write_text('Partner may deliver workshops.')
            (pack / 'scope.txt').write_text('The bidder must deliver a report.')
            rows, _ = read_manifest(pack, 'single')
            _, sources, _ = reading(rows, lambda stage, key, work: work())
            self.assertEqual({record['file'] for record in sources.values()}, {'scope.txt'})
            self.assertIn('Partner may deliver', planning_context(rows)[0]['text'])


class StageRecovery(unittest.TestCase):
    def test_content_failures_isolate_units_and_resume_uncached_work(self):
        import json

        from mesakumo.automation import approve_current, run_once
        from mesakumo.automation_proposal import blockers
        from mesakumo.evidence import EvidenceSource
        from mesakumo.gemini import ProviderError

        class Model:
            model = 'stage-fixture'

            def __init__(self, broken):
                self.broken = broken
                self.calls = []

            def complete_task(self, prompt, *, schema_name, **kwargs):
                if schema_name == 'Extraction':
                    sources = json.loads(prompt.rsplit('Sources:\n', 1)[1])
                    sid, source = next(iter(sources.items()))
                    self.calls.append(('extract', source['file']))
                    if source['file'] == 'bad.txt':
                        if self.broken:
                            raise ProviderError('Fixture single source output exceeded budget.', truncated=True)
                        return json.dumps({'criteria': [], 'facts': []})
                    return json.dumps(dict(criteria=[
                        criterion('Supply workshop references', sid, response='proof'),
                        criterion('Supply railway references', sid, response='proof'),
                        criterion('Describe interviews with residents', sid),
                    ], facts=[dict(field='language', value='English', citations=[sid])]))
                request = json.loads(prompt[prompt.index('{'):])
                if schema_name == 'Assessments':
                    self.calls.append(('evidence', len(request)))
                    if len(request) > 1 and self.broken:
                        raise ProviderError('Fixture evidence truncated.', truncated=True)
                    return json.dumps(dict(assessments=[
                        dict(criterion=rid, status='confirmed', citations=[next(iter(row['candidates']))],
                             reason='Fixture evidence', rejected=[]) for rid, row in request.items()
                    ]))
                ids = request['criteria']
                has_interviews = any('interviews' in row['text'] for row in ids.values())
                self.calls.append(('draft', len(ids), has_interviews))
                claims = []
                for rid, row in ids.items():
                    if 'interviews' in row['text']:
                        claims.append(dict(text='Interview residents using open questions.', kind='proposal',
                                           requirements=[rid], sources=['invalid'] if self.broken else []))
                    else:
                        claims.append(dict(text='Workshop and railway references', kind='source fact',
                                           requirements=[rid], sources=row['evidence_ids'][:1]))
                return json.dumps(dict(title='Fixture', claims=claims))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = root / 'Ausschreibung 1'
            pack.mkdir()
            (pack / 'bad.txt').write_text('A distinct source.')
            (pack / 'good.txt').write_text('The bid shall include the references and the interview plan.')
            (root / 'Angebotstemplate.pptx').write_bytes(b'fixture template')
            (root / 'evidence.txt').write_text('Workshop and railway references')
            inventory = {'evidence.txt': EvidenceSource('evidence.txt', 'evidence.txt', 'Previous project sources')}
            store = AutomationStore(root / 'workspace.sqlite3')
            key, = store.register(root)
            with patch.dict(os.environ, {'MESAKUMO_RPM': '100'}), patch(
                'mesakumo.automation.evidence_inventory', return_value=inventory
            ), patch('mesakumo.automation.deck.render', return_value=(b'draft deck', [])):
                model = Model(True)
                store.enqueue([key])
                run_once(store, lambda: model)
                first = store.project(key)
                self.assertEqual(store.projects()[0]['status'], 'partial')
                self.assertEqual(len(first['snapshot']['extraction_gaps']), 1)
                self.assertEqual(len(first['snapshot']['generation_gaps']), 1)
                self.assertEqual([call[1] for call in model.calls if call[0] == 'evidence'], [2, 1, 1])
                self.assertTrue(blockers(first['snapshot']))
                with self.assertRaisesRegex(ValueError, 'blockers'):
                    approve_current(store, first, 'Consultant')
                model = Model(False)
                store.enqueue([key])
                run_once(store, lambda: model)
                self.assertEqual(store.projects()[0]['status'], 'ready')
                self.assertEqual(model.calls, [('extract', 'bad.txt'), ('draft', 1, True)])
                self.assertFalse(blockers(store.project(key)['snapshot']))


class EvidenceIntegrity(unittest.TestCase):
    def test_only_profile_owner_can_claim_competencies(self):
        from mesakumo.automation_evidence import suggest_reviewer
        from mesakumo.domain import SourcePassage
        from mesakumo.evidence import EvidenceReading, EvidenceSource
        from mesakumo.evidence_matching import LexicalIndex

        evidence = EvidenceReading(EvidenceSource('people.pptx', 'people.pptx', 'Team information'), 'Read', passages=[
            SourcePassage('alex', 'deck', 'Slide 1', 'Alex Engineer\nFlood modelling and coastal infrastructure', '1'),
            SourcePassage('robin', 'deck', 'Slide 2', 'Robin Analyst\nRailway signaling and traffic control', '1'),
        ])
        profiles = [dict(name='Alex Engineer', role='Engineer', profile_path='people.pptx', location='Slide 1'),
                    dict(name='Robin Analyst', role='Analyst', profile_path='people.pptx', location='Slide 2')]
        criterion_row = dict(criterion('Provide railway signaling and traffic control', 'T'), id='R')
        result = suggest_reviewer(profiles, [criterion_row], LexicalIndex([evidence]))
        self.assertEqual(result['match']['name'], 'Robin Analyst')
        self.assertEqual(result['match']['citations'], ['robin'])

    def test_draft_retains_three_assessed_references_and_late_support(self):
        from mesakumo.automation_proposal import check_claims, coverage, plan_context

        row = dict(criterion('Provide three references', 'T', response='proof'), id='R')
        ids = ['a', 'b', 'c']
        extracts = {sid: ('Context ' * 140) + f'Qualification {sid}' for sid in ids}
        assessment = {'R': dict(citations=ids, status='confirmed', proof_excerpts=extracts)}
        inputs, quotes = plan_context([row], {'T': {'quote': 'Provide three references'}},
                                      {sid: {'excerpt': text} for sid, text in extracts.items()}, assessment)
        self.assertEqual(inputs['R']['evidence_ids'], ids)
        claims = [dict(kind='source fact', text=f'Qualification {sid}', requirements=['R'], sources=[sid])
                  for sid in ids]
        check_claims(dict(claims=claims), inputs, quotes)
        with self.assertRaisesRegex(ValueError, 'confirmed proof'):
            check_claims(dict(claims=claims[:1]), inputs, quotes)
        manual = dict(claims[0], kind='manual')
        gaps = list(coverage([row], [dict(id='G', claims=[manual])], assessment))
        self.assertIn('confirmed proof', gaps[0]['issue'])

    def test_manual_confirmation_is_bound_to_requirement_and_source(self):
        from mesakumo.automation_evidence import applicable_review, effective_assessments, match, review_basis
        from mesakumo.domain import SourcePassage
        from mesakumo.evidence import EvidenceReading, EvidenceSource

        row = dict(criterion('Supply two references', 'T', response='proof'), id='R')
        manual = dict(criterion='R', status='confirmed', citations=['E'], rejected=[], reason='Two reviewed')
        sources = {'E': {'document_version': 'v1'}}
        self.assertFalse(applicable_review(manual, row, sources))
        manual['basis'] = review_basis(row, ['E'], sources)
        same = dict(row, text='Supply  two references', chapter='team', resolution='Typographic edit')
        self.assertTrue(applicable_review(manual, same, sources))
        changed = dict(row, text='Supply five references')
        self.assertFalse(applicable_review(manual, changed, sources))
        self.assertFalse(applicable_review(manual, row, {'E': {'document_version': 'v2'}}))
        self.assertEqual(effective_assessments(dict(criteria=[changed], evidence={'R': manual},
                                                   evidence_edits={'R': manual}, evidence_sources=sources)), {})
        with tempfile.TemporaryDirectory() as directory:
            store = AutomationStore(Path(directory) / 'workspace.sqlite3')
            reading = EvidenceReading(EvidenceSource('proof.txt', 'proof.txt', 'Previous project sources'), 'Read',
                                      passages=[SourcePassage('E', 'D', 'Line 1', 'Two references verified', 'v1')])
            called = []

            def assess(*args, **kwargs):
                called.append(True)
                return dict(assessments=[dict(manual, status='partial', reason='Only two of five')])

            initial, _, _ = match(store, 'project', None, assess, 'fixture', [same], [reading],
                                  {'R': manual}, lambda *_: None)
            self.assertEqual(initial['R']['status'], 'confirmed')
            self.assertFalse(called)
            rerun, _, _ = match(store, 'project', None, assess, 'fixture', [changed], [reading],
                                {'R': manual}, lambda *_: None)
            self.assertEqual(rerun['R']['status'], 'partial')
            self.assertEqual(called, [True])

    def test_individual_cv_continuation_and_a_real_tie(self):
        from mesakumo.automation_evidence import detected_profiles, suggest_reviewer
        from mesakumo.domain import SourcePassage
        from mesakumo.evidence import EvidenceReading, EvidenceSource
        from mesakumo.evidence_matching import LexicalIndex

        def cv(name):
            file = f'{name} Lebenslauf.docx'
            return EvidenceReading(EvidenceSource(file, file, 'Team information'), 'Read', passages=[
                SourcePassage(name + '1', file, 'Paragraph 1', name, '1'),
                SourcePassage(name + '2', file, 'Paragraph 2', 'Traffic control and railway signaling', '1'),
            ])
        alex, robin = cv('Alex Engineer'), cv('Robin Analyst')
        row = dict(criterion('Explain railway signaling and traffic control', 'T'), id='R')
        profiles = detected_profiles([alex])
        result = suggest_reviewer(profiles, [row], LexicalIndex([alex]))
        self.assertEqual(result['match']['citations'], ['Alex Engineer1', 'Alex Engineer2'])
        tied = suggest_reviewer(detected_profiles([alex, robin]), [row], LexicalIndex([alex, robin]))
        self.assertIsNone(tied['match'])
        self.assertTrue(tied['tied'])


class PrivateTemplateIntegration(unittest.TestCase):
    def test_template_keeps_wrapped_claim_and_full_cover_in_mapped_shapes(self):
        import io
        from zipfile import ZipFile
        from mesakumo.automation_deck import NS, render, shape_id, xml
        from test_automation import template_path

        template = template_path()
        if template is None:
            self.skipTest('Private corporate template unavailable.')
        claim = 'A background statement with independently verifiable context. ' * 3 + 'BACKGROUND-END'
        client = 'Regional Joint Commission for North District Transport, Infrastructure, Procurement and Long-Term Service Operations'
        scope = 'Joint procurement and implementation of an interregional infrastructure services programme for tender evaluation'
        groups = [
            dict(id='c', role='context', title='Background', claims=[
                dict(text=claim, kind='proposal'), dict(text='A different challenge', kind='proposal')]),
            dict(id='t', role='team', title='Team', fields=[['Lead', 'Robin Analyst']],
                 claims=[dict(text='Project coordination', kind='proposal')]),
            dict(id='p', role='pricing', title='Pricing', fields=[['Delivery', 'EUR 45,000']],
                 claims=[dict(text='Budget detail', kind='proposal')]),
        ]
        snapshot = dict(language='en', facts={'client': {'value': client}, 'scope': {'value': scope}}, chapters=groups)
        data, issues = render(template.read_bytes(), 'Project', 'single', snapshot)
        with ZipFile(io.BytesIO(data)) as archive:
            def strings(slide):
                root = xml(archive.read(f'ppt/slides/proposal{slide}.xml'))
                return {shape_id(shape): ' '.join(shape.xpath('.//a:t/text()', namespaces=NS))
                        for shape in root.findall('.//p:sp', NS)}
            self.assertEqual(strings(1)[4], client)
            self.assertEqual(strings(1)[3], scope)
            self.assertIn('BACKGROUND-END', strings(2)[10])
            self.assertEqual(strings(2)[15], 'A different challenge')
            all_text = ' '.join(text for slide in range(1, len([name for name in archive.namelist()
                                if name.startswith('ppt/slides/proposal')]) + 1) for text in strings(slide).values())
            self.assertIn('Robin Analyst', all_text)
            self.assertIn('EUR 45,000', all_text)
        self.assertTrue(any('cover' in issue.lower() for issue in issues))

    def test_edited_commercial_fields_require_current_attestation(self):
        from mesakumo.automation_proposal import blockers, confirm_fields

        group = dict(id='pricing', role='pricing', title='Pricing', context_key='draft', claims=[],
                     fields=[['Delivery', 'EUR 45,000']])
        self.assertTrue(blockers({'chapters': [group]}))
        with self.assertRaises(ValueError):
            confirm_fields(group, 'Morgan', '')
        group['field_review'] = confirm_fields(group, 'Morgan', 'Finance decision PR-42')
        self.assertFalse(blockers({'chapters': [group]}))
        group['fields'][0][1] = 'EUR 54,000'
        self.assertTrue(blockers({'chapters': [group]}))


class NotificationRouting(unittest.TestCase):
    def test_retry_routes_current_assignment_past_unroutable_backlog(self):
        import smtplib
        import time

        from mesakumo.automation_mail import dispatch, notice_for, statuses

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = AutomationStore(root / 'workspace.sqlite3')
            (root / 'Ausschreibung 1').mkdir()
            key, = store.register(root)

            def publish(project_id, **kwargs):
                project = store.project(project_id)
                snapshot = dict(project['snapshot'], project_name='Fixture', **kwargs)
                return store.save_revision(project_id, project['revision'], snapshot, 'inputs', b'deck',
                                           notice=notice_for(project_id, project, snapshot, 8501))

            publish(key, reviewer_override='Original reviewer')
            captured = []

            def temporary(payload, **kwargs):
                captured.append((payload, kwargs))
                raise smtplib.SMTPDataError(451, 'Definite rejection')

            self.assertTrue(dispatch(store, temporary, recipient='reviews@example.test', sender='local@example.test'))
            self.assertEqual(statuses(store, key)[0]['state'], 'retry')
            publish(key, reviewer_override='')
            with store.connect() as db:
                db.executemany('INSERT INTO automation_projects(id,folder,phase) VALUES (?,?,?)',
                               [(f'backlog-{i}', str(root), 'single') for i in range(101)])
            for i in range(101):
                publish(f'backlog-{i}', reviewer_override='Unroutable')
            with store.connect() as db:
                db.execute('UPDATE review_outbox SET due=? WHERE project<>?', (time.time() - 2, key))
                db.execute('UPDATE review_outbox SET due=? WHERE project=?', (time.time() - 1, key))
            self.assertTrue(dispatch(store, lambda payload, **kwargs: captured.append((payload, kwargs)),
                                     triage='triage@example.test', sender='local@example.test'))
            self.assertEqual(len(captured), 2)
            self.assertEqual(captured[-1][0]['reviewer'], 'Local triage')
            self.assertEqual(captured[-1][1]['recipient'], 'triage@example.test')
            self.assertEqual(captured[0][0]['episode'], captured[-1][0]['episode'])
            self.assertEqual(captured[0][1]['message_id'], captured[-1][1]['message_id'])
            self.assertEqual(statuses(store, key)[0]['state'], 'sent')
            publish(key, reviewer_override='New reviewer after send')
            self.assertEqual(statuses(store, key)[0]['state'], 'sent')
            self.assertFalse(dispatch(store, lambda *_args, **_kwargs: self.fail('sent episode was reissued'),
                                      triage='triage@example.test', sender='local@example.test'))


class DistributableEvaluation(unittest.TestCase):
    def test_labeled_cases_run_through_parser_routing_coverage_and_scorer(self):
        import json
        from evaluate_redesign import CASES, score
        from mesakumo.automation_evidence import eligible
        from mesakumo.automation_proposal import chapter_units, coverage
        from mesakumo.automation_requirements import Extraction, extract_batch

        cases = json.loads(CASES.read_text())
        observations = {}
        for case in cases:
            with self.subTest(case=case['case']):
                row = dict(criterion(case['criterion'], 'T', chapter=case['chapter'],
                                     response=case['response'], scope=case['scope']), obligation=case['obligation'])
                # The portal holdout exercises correction of a realistic model misroute.
                model_row = dict(row)
                if case['case'] == 'Translated holdout':
                    model_row.update(scope='proposal', response='proof')
                sources = {'T': dict(quote=case['synthetic_source'])}

                def fixture(prompt, schema, **kwargs):
                    self.assertEqual(schema, Extraction)
                    self.assertIn(case['synthetic_source'], prompt)
                    return schema.model_validate(dict(criteria=[model_row], facts=[])).model_dump()

                parsed = extract_batch(fixture, sources, 'single', 'English')['criteria'][0]
                parsed['id'] = 'R'
                self.assertEqual(eligible(parsed), case['scope'] == 'proposal' and case['response'] == 'proof')
                self.assertEqual(bool(list(chapter_units([parsed]))), case['scope'] == 'proposal')
                self.assertTrue(list(coverage([parsed], [], {}))[0]['blocker'])
                observations[case['case']] = dict(recalled=True, scope=parsed['scope'], response=parsed['response'],
                                                   claims_checked=0, unsupported_claims=0, correction_seconds=None)
        result = score(cases, observations)
        self.assertEqual(result['routed_correctly'], len(cases))
        self.assertEqual(result['timed_cases'], 0)
