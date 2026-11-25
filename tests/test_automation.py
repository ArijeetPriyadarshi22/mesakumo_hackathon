"""Offline acceptance tests for the automated Streamlit path and background worker."""
import io
import json
import os
import smtplib
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

from pypdf import PdfWriter

from mesakumo.automation import approve_current, run_once
from mesakumo.automation_deck import NS, SLOTS, TEMPLATE_SHA256, render, xml
from mesakumo.automation_evidence import Assessments, eligible, match, suggest_reviewer
from mesakumo.automation_mail import dispatch, statuses
from mesakumo.automation_model import ModelCalls
from mesakumo.automation_proposal import check_claims
from mesakumo.automation_requirements import Reclassification, consolidate, merge_paraphrases, parse_grounded
from mesakumo.automation_store import AutomationStore, discover
from mesakumo.discovery import ROOT
from mesakumo.documents import LocalProcessor
from mesakumo.domain import SourcePassage
from mesakumo.evidence import EvidenceReading, EvidenceSource
from mesakumo.evidence_matching import LexicalIndex
from mesakumo.gemini import GeminiModel, ProviderError


def criterion(text, citation, *, chapter='approach', response='method', scope='proposal', conditions=None):
    return dict(text=text, scope=scope, obligation='mandatory', response=response,
                chapter=chapter, conditions=conditions or [], citations=[citation])


class FixtureModel:
    model = 'offline-fixture'

    def __init__(self, fail_after=None):
        self.count = 0
        self.fail_after = fail_after

    def complete_task(self, prompt, *, schema_name, **kwargs):
        if self.count == self.fail_after:
            raise ProviderError('Fixture HTTP 403.')
        self.count += 1
        if schema_name == 'Extraction':
            refs = json.loads(prompt.rsplit('Sources:\n', 1)[1])
            ref = next(iter(refs))
            result = dict(criteria=[
                criterion('Describe the workshop approach.', ref),
                criterion('Specify how facilitators will collect feedback.', ref),
                criterion('Upload the bid through the procurement portal.', ref, response='proof'),
                criterion('Show two workshop delivery references.', ref, response='proof'),
            ], facts=[dict(field='scope', value='Workshop services', citations=[ref]),
                      dict(field='language', value='English', citations=[ref])])
        elif schema_name == 'Assessments':
            request = json.loads(prompt[prompt.index('{'):])
            result = dict(assessments=[
                dict(criterion=rid, status='confirmed', citations=[next(iter(item['candidates']))],
                     reason='Fixture workshop records.', rejected=[])
                for rid, item in request.items()
            ])
        elif schema_name == 'Chapter':
            request = json.loads(prompt[prompt.index('{'):])
            method = [rid for rid, item in request['criteria'].items() if item['response'] == 'method']
            proof = [rid for rid, item in request['criteria'].items() if item['response'] == 'proof']
            claims = [dict(text='We propose facilitating a workshop and collecting participant feedback.',
                           kind='proposal', requirements=method, sources=[])] if method else []
            for rid in proof:
                sources = request['criteria'][rid]['evidence_ids']
                claims.append(dict(text='Two workshop delivery references' if sources else 'Verify references.',
                                   kind='source fact' if sources else 'open', requirements=[rid], sources=sources[:1]))
            result = dict(title='Workshops', claims=claims)
        else:
            raise AssertionError(schema_name)
        return json.dumps(result)


def template_path():
    return next(ROOT.glob('mesakumo_Input*/Angebotstemplate.pptx'), None)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'input'
        self.pack = self.root / 'Ausschreibung 2'
        self.pack.mkdir(parents=True)
        (self.pack / 'scope.txt').write_text('Workshop services, project references and portal submission.', encoding='utf-8')
        self.store = AutomationStore(self.base / 'workspace.sqlite3')
        self.key, = self.store.register(self.root)

    def test_phase_discovery_persistence_and_concurrent_claim(self):
        other = self.base / 'other'
        (other / self.pack.name).mkdir(parents=True)
        self.assertNotEqual(self.key, self.store.register(other)[0])
        with ZipFile(self.pack / 'Offer.ZIP', 'w') as bundle:
            bundle.writestr('tender.txt', 'Offer phase')
        discovered = list(discover(self.root))
        self.assertEqual([p[2] for p in discovered], ['participation', 'offer'])
        ids = self.store.register(self.root)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.store.enqueue(ids + ids), range(8)))
            claims = list(pool.map(lambda _: self.store.claim(), range(4)))
        self.assertEqual({item[0] for item in claims if item}, set(ids))
        claim = next(item for item in claims if item)
        self.store.save_unit(claim[0], 'example', 'done', {'data': True}, claim[2])
        reopened = AutomationStore(self.store.path)
        with reopened.connect() as db:
            db.execute('UPDATE jobs SET lease_until=0 WHERE project=?', (claim[0],))
        reclaimed = reopened.claim()
        self.assertEqual(reclaimed[:2], claim[:2])
        self.assertNotEqual(reclaimed[2], claim[2])
        self.assertEqual(reopened.cached(claim[0], 'example', 'done'), {'data': True})
        with self.assertRaisesRegex(ValueError, 'lease'):
            self.store.progress(claim[0], claim[2], 'expired')

    def test_checkpoint_reuse_final_approval_edit_and_outbox(self):
        template = template_path()
        if template is None:
            self.skipTest('Inspected participant template is unavailable.')
        (self.root / template.name).write_bytes(template.read_bytes())
        evidence_file = self.root / 'experience.txt'
        evidence_file.write_text('Two workshop delivery references', encoding='utf-8')
        inventory = {'experience.txt': EvidenceSource('experience.txt', 'experience.txt', 'Previous project sources')}
        with patch('mesakumo.automation.evidence_inventory', return_value=inventory):
            failing = FixtureModel(fail_after=1)
            self.store.enqueue([self.key])
            self.assertTrue(run_once(self.store, lambda: failing))
            self.assertEqual(self.store.projects()[0]['status'], 'failed')
            self.assertEqual(failing.count, 1)
            restarted = AutomationStore(self.store.path)
            self.assertTrue(restarted.project(self.key)['snapshot']['criteria'])
            model = FixtureModel()
            restarted.enqueue([self.key])
            self.assertTrue(run_once(restarted, lambda: model))
            self.assertEqual(restarted.projects()[0]['status'], 'ready', restarted.projects())
            self.assertEqual(model.count, 2)
            first = restarted.project(self.key)
            requirements = first['snapshot']['criteria']
            portal, = [r for r in requirements if r['scope'] == 'administrative']
            self.assertNotIn(portal['id'], first['snapshot']['evidence'])
            self.assertTrue(first['deck'])
            self.assertEqual(len(first['snapshot']['chapters'][0]['claims'][0]['requirements']), 2)
            self.assertEqual(len(statuses(restarted, self.key)), 1)
            deliveries = []

            def capture(payload, **kwargs):
                deliveries.append((payload, kwargs))

            self.assertTrue(dispatch(restarted, capture, triage='triage@example.test', sender='local@example.test'))
            self.assertFalse(dispatch(restarted, capture, triage='triage@example.test', sender='local@example.test'))
            self.assertIn(f'?project={self.key}&tab=review', deliveries[0][0]['url'])
            self.assertEqual(deliveries[0][0]['reviewer'], 'Local triage')
            with self.assertRaisesRegex(ValueError, 'blockers'):
                approve_current(restarted, first, 'Alex')
            restarted.enqueue([self.key])
            run_once(restarted, lambda: self.fail('Warm run should not configure the model.'))
            self.assertEqual(restarted.project(self.key)['revision'], first['revision'])
            edited = first['snapshot']
            portal['resolution'] = 'Consultant verified the portal instructions.'
            edited['dirty'] = 'render'
            edited['chapters'][0]['title'] = 'Edited workshop approach'
            restarted.save_revision(self.key, first['revision'], edited, first['input_key'])
            with self.assertRaisesRegex(ValueError, 'changed'):
                approve_current(restarted, first, 'Alex')
            restarted.enqueue([self.key], 'render')
            run_once(restarted, lambda: self.fail('Text-only render must not configure the model.'))
            second = restarted.project(self.key)
            self.assertIsNone(second['approval'])
            self.assertNotEqual(first['checksum'], second['checksum'])
            self.assertEqual(approve_current(restarted, second, 'Alex')['checksum'], second['checksum'])
            self.assertEqual(len(statuses(restarted, self.key)), 1)
            self.assertEqual(AutomationStore(self.store.path).project(self.key)['approval']['reviewer'], 'Alex')
            self.assertEqual(len(restarted.history(self.key)), 4)
            (self.pack / 'scope.txt').write_text('Changed tender.', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Inputs changed'):
                approve_current(restarted, second, 'Alex')

    def test_render_failure_keeps_proposal_units_and_retries_without_model(self):
        template = template_path()
        if template is None:
            self.skipTest('Inspected participant template is unavailable.')
        (self.root / template.name).write_bytes(template.read_bytes())
        environment = patch.dict(os.environ, {'MESAKUMO_RPM': '100'})
        with environment, patch('mesakumo.automation.evidence_inventory', return_value={}):
            with patch('mesakumo.automation.deck.render', side_effect=ValueError('Fixture render failure.')):
                self.store.enqueue([self.key])
                run_once(self.store, FixtureModel)
            saved = self.store.project(self.key)
            self.assertTrue(saved['snapshot']['chapters'])
            self.assertIn('Fixture render failure.', saved['snapshot']['render_issues'])
            self.assertIsNone(saved['deck'])
            self.store.enqueue([self.key], 'render')
            run_once(self.store, lambda: self.fail('Render retry should not configure a model.'))
            self.assertIsNotNone(self.store.project(self.key)['deck'])

    def test_review_link_reopens_in_two_streamlit_sessions(self):
        from streamlit.testing.v1 import AppTest

        def connect():
            app = AppTest.from_file(str(ROOT / 'mesakumo' / 'streamlit_app.py'))
            app.query_params = {'project': self.key, 'tab': 'review'}
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, self.pack.name)
            self.assertEqual(app.header[0].value, 'Final review')
            return app

        with patch.dict(os.environ, {'MESAKUMO_AUTOMATION_DIR': str(self.base)}), patch('mesakumo.ui.automation.start_worker'):
            first, second = connect(), connect()
            self.assertEqual(first.caption[0].value, second.caption[0].value)
            first.query_params['project'] = 'unknown'
            first.run()
            self.assertIn('was not found', first.error[0].value)

    def test_projects_to_review_link_edit_regenerate_and_approve_in_streamlit(self):
        from streamlit.testing.v1 import AppTest
        from mesakumo.ui.automation import service

        template = template_path()
        if template is None:
            self.skipTest('Inspected participant template is unavailable.')
        (self.root / template.name).write_bytes(template.read_bytes())
        (self.root / 'experience.txt').write_text('Two workshop delivery references', encoding='utf-8')
        inventory = {'experience.txt': EvidenceSource('experience.txt', 'experience.txt', 'Previous project sources')}
        self.store.set_setting('input_root', str(self.root))

        def button(app, label):
            return next(widget for widget in app.button if widget.label == label)

        def app_session(tab=None):
            app = AppTest.from_file(str(ROOT / 'mesakumo' / 'streamlit_app.py'))
            if tab:
                app.query_params = {'project': self.key, 'tab': tab}
            return app.run()

        environment = {'MESAKUMO_AUTOMATION_DIR': str(self.base), 'MESAKUMO_RPM': '100'}
        with patch.dict(os.environ, environment), patch('mesakumo.ui.automation.start_worker'), patch(
            'mesakumo.automation.evidence_inventory', return_value=inventory
        ):
            launcher = app_session()
            button(launcher, 'Prepare proposals').click().run()
            self.assertFalse(launcher.exception)
            self.assertEqual(self.store.projects()[0]['status'], 'queued')
            run_once(self.store, FixtureModel)
            original = self.store.project(self.key)

            service.clear()
            opened = app_session('review')
            self.assertEqual(opened.header[0].value, 'Final review')
            self.assertEqual(opened.get('download_button')[0].label, 'Download draft PowerPoint')
            button(opened, 'Review requirements').click().run()
            next(item for item in opened.radio if item.label == 'Scope').set_value('administrative').run()
            note = next(item for item in opened.text_input if item.label == 'Confirmation and reviewer note')
            note.set_value('The reviewer completed the filing requirement.')
            button(opened, 'Save criterion').click().run()
            self.assertFalse(opened.exception)
            next(item for item in opened.sidebar.button if item.label == 'Prepare proposals').click().run()
            run_once(self.store, FixtureModel)

            editor = app_session('proposal')
            next(item for item in editor.text_input if item.label == 'Slide title').set_value('Updated workshop plan')
            button(editor, 'Save slide group').click().run()
            self.assertFalse(editor.exception)
            review = app_session('review')
            button(review, 'Regenerate from saved slides').click().run()
            run_once(self.store, lambda: self.fail('Text-only regeneration cannot call the model.'))
            service.clear()
            review = app_session('review')
            self.assertFalse(review.exception)
            next(item for item in review.text_input if item.label == 'Approval reviewer').set_value('Alex')
            button(review, 'Approve current checksum').click().run()
            self.assertFalse(review.exception)
            current = self.store.project(self.key)
            self.assertNotEqual(original['checksum'], current['checksum'])
            self.assertEqual(current['approval']['checksum'], current['checksum'])
            self.assertIn('Download approved PowerPoint', [b.label for b in review.get('download_button')])

    def test_outbox_retries_only_definite_rejection(self):
        payload = dict(episode='episode', project='Fixture', reviewer='Morgan', triage=False,
                       reason='Profile overlap', url='http://localhost:8501/?project=fixture&tab=review')
        self.store.save_revision(self.key, 0, {'review_episode': 'episode'}, 'fixture', b'fixture', notice=payload)
        ids = []

        def reject(payload, **kwargs):
            ids.append(kwargs['message_id'])
            raise smtplib.SMTPDataError(451, 'Temporary fixture error')

        options = dict(recipient='review@example.test', sender='local@example.test')
        self.assertTrue(dispatch(self.store, reject, **options))
        self.assertEqual(statuses(self.store, self.key)[0]['state'], 'retry')
        self.assertFalse(dispatch(self.store, reject, **options))
        with self.store.connect() as db:
            db.execute('UPDATE review_outbox SET due=?', (time.time() - 1,))

        def capture(payload, **kwargs):
            ids.append(kwargs['message_id'])

        self.assertTrue(dispatch(self.store, capture, **options))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(statuses(self.store, self.key)[0]['state'], 'sent')

    def test_knowledge_change_rejudges_only_changed_shortlists(self):
        references = EvidenceSource('references.txt', 'references.txt', 'Previous project sources')
        other = EvidenceSource('other.txt', 'other.txt', 'Previous project sources')
        row = dict(criterion('Provide workshop references', 'T', response='proof'), id='R-proof')
        calls = []

        def assess(prompt, schema, **kwargs):
            self.assertEqual(schema, Assessments)
            candidates = json.loads(prompt[prompt.index('{'):])[row['id']]['candidates']
            calls.append(tuple(candidates))
            return dict(assessments=[dict(criterion=row['id'], status='confirmed',
                                          citations=[next(iter(candidates))], reason='Test fixture.', rejected=[])])

        for relevant_version, other_version, expected in (('v1', 'v1', 1), ('v1', 'v2', 1), ('v2', 'v2', 2)):
            readings = [
                EvidenceReading(references, 'Read', passages=[
                    SourcePassage('P-' + relevant_version, 'D1', 'Lines 1–2', 'Workshop references', relevant_version)]),
                EvidenceReading(other, 'Read', passages=[
                    SourcePassage('N-' + other_version, 'D2', 'Lines 1–2', 'Astronomy geology', other_version)]),
            ]
            result, citations, _ = match(self.store, self.key, None, assess, 'fixture',
                                         [row], readings, {}, lambda *args: None)
            self.assertEqual(len(calls), expected)
            self.assertEqual(result[row['id']]['citations'], ['P-' + relevant_version])
            self.assertEqual(citations['P-' + relevant_version]['location'], 'Lines 1–2')


class ValidationTests(unittest.TestCase):
    def test_bilingual_routing_and_distinct_conditions(self):
        sources = {'x': dict(amendment=False), 'y': dict(amendment=True)}
        rows = [criterion('Upload the proposal via the procurement portal', 'x', response='proof'),
                criterion('Angebot im Vergabeportal einreichen', 'y', response='proof'),
                criterion('Conduct workshops', 'x', conditions=['By 1 June']),
                criterion('Conduct workshops', 'y', conditions=['By 1 July']),
                criterion('Show workshop delivery references', 'x', response='proof')]
        rows[-1]['obligation'] = 'scored'
        result = parse_grounded(dict(criteria=rows, facts=[]), sources)
        merged, _, conflicts = consolidate([result], sources)
        self.assertFalse(eligible(merged[0]))
        self.assertFalse(eligible(merged[1]))
        self.assertTrue(eligible(merged[-1]))
        self.assertEqual([r['conditions'] for r in merged[2:4]], [['By 1 June'], ['By 1 July']])
        self.assertTrue(conflicts)

    def test_explicit_amendment_retains_superseded_source(self):
        sources = {'x': dict(amendment=False, quote='The date is 1 June.'),
                   'y': dict(amendment=True, quote='The date 1 June is replaced by 1 July.')}
        result = dict(facts=[], criteria=[
            criterion('Conduct the workshop', 'x', conditions=['1 June']),
            criterion('Conduct the workshop', 'y', conditions=['1 July']),
        ])
        criteria, _, conflicts = consolidate([result], sources)
        self.assertFalse(conflicts)
        self.assertEqual(len(criteria), 1)
        self.assertEqual(criteria[0]['conditions'], ['1 July'])
        self.assertEqual(criteria[0]['superseded'][0]['citations'], ['x'])

    def test_semantic_merge_keeps_distinct_dates_and_source_locations(self):
        sources = {ref: dict(amendment=False) for ref in ('x', 'y', 'z')}
        result = dict(facts=[], criteria=[
            criterion('Plan workshops for review by 1 June', 'x'),
            criterion('Plan the workshops for a review by 1 June', 'y'),
            criterion('Plan workshops for review by 1 July', 'z'),
        ])
        rows, _, _ = consolidate([result], sources)

        def confirm(prompt, schema):
            request = json.loads(prompt[prompt.index('{'):])
            self.assertEqual(len(request), 1)
            self.assertEqual(schema.__name__, 'Consolidation')
            return dict(decisions=[dict(pair=next(iter(request)), equivalent=True)])

        merged = merge_paraphrases(rows, lambda stage, key, work: work(), confirm, 'fixture')
        self.assertEqual([row['citations'] for row in merged], [['x', 'y'], ['z']])

    def test_named_reviewer_and_triage(self):
        def profile(name, terms):
            return EvidenceReading(EvidenceSource(name + '.txt', name, 'Team information'), 'Read',
                                   passages=[SourcePassage(name, name, 'Page 1', f'{name} {terms}', 'v')])
        people = [profile('Alex', 'Workshop facilitation and stakeholder coaching'),
                  profile('Morgan', 'Cybersecurity audit')]
        candidates = [dict(name=r.source.filename, role='facilitation', profile_path=r.source.relative_path,
                           location='Page 1') for r in people]
        index = LexicalIndex(people)
        row = dict(criterion('Workshop facilitation and stakeholder coaching', 'T'), id='R-1')
        self.assertEqual(suggest_reviewer(candidates, [row], index)['match']['name'], 'Alex')
        row['text'] = 'Database migration and platform rollout'
        self.assertIsNone(suggest_reviewer(candidates, [row], index)['match'])

    def test_invalid_and_unsupported_claims(self):
        inputs = {'R1': dict(response='proof', citations=['S1'], evidence_ids=['S1'])}
        excerpts = {'S1': 'Actual reference text.'}
        base = dict(text='Invented certification', kind='source fact', requirements=['R1'], sources=['S1'])
        for claim, diagnostic in ((base, 'verbatim'), (dict(base, sources=['S404']), 'unknown'),
                                   (dict(base, kind='proposal'), 'criteria requiring proof')):
            with self.subTest(claim=claim), self.assertRaisesRegex(ValueError, diagnostic):
                check_claims({'claims': [claim]}, inputs, excerpts)

    def test_pdf_retains_readable_pages_and_reports_other_pages(self):
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mixed.pdf'
            pdf = PdfWriter()
            readable = pdf.add_blank_page(200, 200)
            stream = DecodedStreamObject()
            stream.set_data(b'BT /F1 12 Tf (Acceptance condition) Tj ET')
            readable[NameObject('/Contents')] = pdf._add_object(stream)
            readable[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): DictionaryObject({
                    NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                    NameObject('/BaseFont'): NameObject('/Helvetica')})})})
            pdf.add_blank_page(200, 200)
            pdf.write(path)
            gaps = []
            with patch('mesakumo.documents._ocr', return_value=''):
                passages = LocalProcessor().extract(path, 'fixture', gaps=gaps)
            self.assertEqual([p.location for p in passages], ['Page 1'])
            self.assertIn('Page 2', gaps[0])

    def test_gemini_429_403_bad_json_and_usage(self):
        class HTTP(RuntimeError):
            def __init__(self, code):
                self.code = code

        def response(text, finish='STOP'):
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=finish)], text=text,
                                   usage_metadata=SimpleNamespace(prompt_token_count=30, candidates_token_count=20,
                                                                  thoughts_token_count=7))
        with tempfile.TemporaryDirectory() as path, patch.dict(os.environ, {'MESAKUMO_RPM': '100'}):
            seen = iter([HTTP(429), response('{'), response('{"criteria":[]}'), HTTP(403),
                         response('{', 'MAX_TOKENS')])

            def content(**kwargs):
                self.assertIn('response_json_schema', kwargs['config'])
                result = next(seen)
                if isinstance(result, Exception):
                    raise result
                return result

            client = SimpleNamespace(models=SimpleNamespace(generate_content=content))
            model = GeminiModel('fixture-gemini', 'synthetic', client=client)
            store = AutomationStore(Path(path) / 'workspace.sqlite3')
            calls = ModelCalls(store, model, 'fixture', lambda _: None, sleep=lambda _: None)
            self.assertEqual(calls('Synthetic prompt only', Reclassification), {'criteria': []})
            with self.assertRaisesRegex(ProviderError, '403'):
                calls('Synthetic prompt only', Reclassification)
            with self.assertRaises(ProviderError) as raised:
                calls('Synthetic prompt only', Reclassification)
            self.assertTrue(raised.exception.truncated)
            with store.connect() as db:
                results = db.execute('SELECT input,output,thinking FROM model_calls ORDER BY id').fetchall()
            self.assertEqual(len(results), 5)
            self.assertEqual(tuple(results[2]), (30, 20, 7))

    def test_template_slide_relationships_and_sample_removal(self):
        path = template_path()
        if path is None:
            self.skipTest('Inspected participant template is unavailable.')
        template = path.read_bytes()
        import hashlib
        self.assertEqual(hashlib.sha256(template).hexdigest(), TEMPLATE_SHA256)
        snapshot = dict(language='en', chapters=[
            dict(role=role, title=role.title(), claims=[dict(kind='proposal', text='Editable fixture')])
            for role in SLOTS
        ])
        data, issues = render(template, 'Synthetic proposal', 'single', snapshot)
        self.assertFalse(issues)
        self.assertEqual(data, render(template, 'Synthetic proposal', 'single', snapshot)[0])
        with ZipFile(io.BytesIO(data)) as pptx:
            self.assertIsNone(pptx.testzip())
            self.assertIn('ppt/slideMasters/slideMaster1.xml', pptx.namelist())
            self.assertNotIn('ppt/slides/slide1.xml', pptx.namelist())
            self.assertFalse(any(name.startswith('ppt/notesSlides/') for name in pptx.namelist()))
            for index in range(1, 10):
                doc = xml(pptx.read(f'ppt/slides/proposal{index}.xml'))
                text = ' '.join(doc.xpath('.//a:t/text()', namespaces=NS))
                self.assertNotIn('AN00345', text)
                self.assertNotIn('Kundenname', text)
                if index > 1:
                    self.assertIn('Editable fixture', text)
