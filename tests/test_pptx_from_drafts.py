"""PPTX generation from approved sections: inputs, safeguards, storage, second review and page."""
import csv
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from mesakumo import export, pptx_from_drafts
from mesakumo.persistence import ConflictError, ProjectStore
from mesakumo.pptx_from_drafts import GeneratedDecks, open_markers, project_from_csv
from mesakumo.proposal import ProposalDrafts, handoff_csv

TEMPLATE = Path(__file__).resolve().parent.parent.parent / 'mesakumo_Input für Teilnehmer' / 'Angebotstemplate.pptx'
CITATION = dict(passage_id='P10', document_id='D1', filename='Referenzen.pptx', relative_path='Referenzen.pptx',
                location='Slide 10', excerpt='Manuela Engl, Manager. Industrie 4.0.', document_version='v1')
REVIEW = dict(project_id='p1', checklist_revision=1, review_revision=1, phase='single', corpus={'P10': CITATION}, rows=[
    dict(requirement=dict(id='R1', text='Qualifikationen nachweisen', kind='eligibility/evidence', source_ids=[]),
         tender_sources=[dict(filename='LB.pdf', location='Page 10', original_excerpt='Nachweis.')],
         citation_ids=['P10'], fit='Partial match', gap='Studium fehlt.', notes='', current_action=''),
    dict(requirement=dict(id='R2', text='Preisblatt', kind='deliverable', source_ids=[]), tender_sources=[],
         citation_ids=[], fit='No match', gap='Kein Preisblatt.', notes='', current_action='')])
PAYLOAD = {'meta': {'project_title': 'Studie', 'offer_id': '[OFFEN: Angebotsnummer]'},
           'conditions': {'remuneration': '[OFFEN: Tagessatz]'}, 'team_members': [{'name': 'Manuela Engl'}]}


class Stub:
    model = 'stub'

    def complete_task(self, prompt, **options):
        return json.dumps({'title': 'Qualifikation', 'text': 'Kandidatin Manuela Engl. [OFFEN: Studium belegen]',
                           'used_citations': ['Q1']})


def approved_package(store):
    drafts = ProposalDrafts(store, REVIEW)
    drafts.generate('R1', Stub(), expected_version=0)
    drafts.approve('R1', 'Luba', 1, accept_open_items=True)
    return drafts.approve_package('Luba', acknowledge_excluded=True, project_name='Demo')


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.store = ProjectStore(self.dir / 'workspace.sqlite3')


class InputTests(Base):
    def test_approved_sections_csv(self):
        path = self.dir / 'approved.csv'
        path.write_bytes(handoff_csv(approved_package(self.store)))
        project, reviewed = project_from_csv(path, 'Demo', client='VDI Technologiezentrum')
        self.assertTrue(reviewed)
        self.assertEqual([(s.title, s.reviewed) for s in project.sections], [('Qualifikation', True)])
        self.assertEqual([(p.id, p.excerpt) for p in project.passages], [('P10', CITATION['excerpt'])])
        self.assertEqual([(e.quality, e.limitations) for e in project.evidence], [('partial', 'Studium fehlt.')])
        self.assertIn('Auftraggeber: VDI Technologiezentrum', project.scope)

    def test_drafts_file_needs_explicit_permission(self):
        path = self.dir / 'drafts.csv'
        header = ['Source row', 'Requirement', 'Category', 'Evidence fit', 'Section title', 'Section text',
                  'Open items', 'Check wording', 'Cited evidence', 'Tender sources', 'Unresolved evidence references',
                  'Draft origin', 'Error']
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerow({**dict.fromkeys(header, ''), 'Source row': '7', 'Requirement': 'A', 'Category': 'deliverable',
                             'Evidence fit': 'Partial match', 'Section title': 'T', 'Section text': 'Text.',
                             'Cited evidence': 'Ref.pdf · Page 2', 'Draft origin': 'Model draft'})
            writer.writerow({**dict.fromkeys(header, ''), 'Source row': '8', 'Error': 'Draft rejected'})
        with self.assertRaisesRegex(ValueError, 'allow-unreviewed'):
            project_from_csv(path)
        project, reviewed = project_from_csv(path, allow_unreviewed=True)
        self.assertFalse(reviewed)
        self.assertEqual([s.id for s in project.sections], ['S-row-7'])
        self.assertEqual([(p.document_id, p.location) for p in project.passages], [('Ref.pdf', 'Page 2')])
        self.assertIn('UNGEPRÜFTER ENTWURF', project.scope)

    def test_unknown_layout_is_rejected(self):
        path = self.dir / 'other.csv'
        path.write_text('a,b\n1,2\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unknown CSV layout'):
            project_from_csv(path)


class StructurerSafeguardTests(unittest.TestCase):
    def stream(self, *chunks, finish='stop'):
        events = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c), finish_reason=None)])
                  for c in chunks]
        events.append(SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason=finish)]))
        sent = {}
        def create(**kwargs):
            sent.update(kwargs)
            return iter(events)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return mock.patch.object(export, 'OpenAI', return_value=client), sent

    def structurer(self):
        return export.ClaudeProposalStructurer(api_key='fixture-key', on_progress=None)

    def test_reasoning_is_switched_off_and_text_returned(self):
        patcher, sent = self.stream('{"meta": ', '{}}')
        with patcher:
            self.assertEqual(self.structurer()._invoke_llm('system', 'user'), '{"meta": {}}')
        self.assertEqual((sent['reasoning_effort'], sent['max_tokens'], sent['stream']), ('none', 16000, True))

    def test_cut_off_or_empty_answer_raises(self):
        for chunks, finish, message in ((('{"meta": ',), 'length', 'output limit'), ((), 'stop', 'no slide data')):
            patcher, _ = self.stream(*chunks, finish=finish)
            with patcher, self.assertRaisesRegex(RuntimeError, message):
                self.structurer()._invoke_llm('system', 'user')


class DeckStorageTests(Base):
    def test_save_decide_and_newer_version(self):
        decks = GeneratedDecks(self.store, approved_package(self.store))
        self.assertIsNone(decks.latest())
        self.assertEqual(decks.save(b'pptx-1', PAYLOAD, 'model'), 1)
        revision, meta, payload, data = decks.latest()
        self.assertEqual((revision, meta['status'], data), (1, 'To review', b'pptx-1'))
        self.assertEqual(meta['open_markers'], ['[OFFEN: Angebotsnummer]', '[OFFEN: Tagessatz]'])
        with self.assertRaisesRegex(ValueError, 'what should change'):
            decks.decide(1, 'Luba', approve=False)
        decks.save(b'pptx-2', PAYLOAD, 'model')
        with self.assertRaises(ConflictError):
            decks.decide(1, 'Luba', approve=True)
        decks.decide(2, 'Luba', approve=False, comment='Folie 5 zu lang.')
        self.assertEqual((decks.latest()[1]['status'], decks.latest()[1]['comment']), ('Declined', 'Folie 5 zu lang.'))

    def test_open_markers(self):
        self.assertEqual(open_markers({'a': ['x [OFFEN: A]', {'b': '[OFFEN: B]'}], 'c': '[OFFEN: A]'}),
                         ['[OFFEN: A]', '[OFFEN: B]'])


@unittest.skipUnless(TEMPLATE.is_file(), 'Corporate template not available on this machine.')
class TemplateRenderTests(Base):
    def test_unreviewed_deck_is_marked_on_the_title(self):
        from pptx import Presentation
        import io
        payload = {'slide_decisions': {'include_org_chart': False, 'timeline_variant': 1, 'include_team_slide': False},
                   'meta': {'project_title': 'Studie', 'client_name': 'VDI', 'offer_id': '[OFFEN: Nr]', 'date': 'heute'},
                   'approach_slides': [{'title': 'Analyse', 'bullets': ['Schritt']}],
                   'timeline': {'gantt_tasks': [{'task': 'Analyse', 'start_week': 1, 'end_week': 2, 'pt': '2 PT'}]}}
        structurer = SimpleNamespace(build_payload=lambda project: json.loads(json.dumps(payload)))
        project, _ = mock.MagicMock(), None
        data, used = pptx_from_drafts.render_pptx(project_from_stub(), TEMPLATE, reviewed=False, structurer=structurer)
        self.assertTrue(used['meta']['project_title'].startswith('ENTWURF (ungeprüft)'))
        title_texts = [s.text_frame.text for s in Presentation(io.BytesIO(data)).slides[0].shapes if s.has_text_frame]
        self.assertTrue(any('ENTWURF (ungeprüft) – Studie' in t for t in title_texts))


class RetryTests(unittest.TestCase):
    def structurer(self, *outcomes):
        outcomes = list(outcomes)
        def build(project):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return SimpleNamespace(build_payload=build)

    def test_invalid_json_is_retried_once(self):
        bad = json.JSONDecodeError("Expecting ',' delimiter", '{"a" "b"}', 5)
        messages = []
        with mock.patch.object(export.PptxProposalExporter, 'render', return_value=b'PPTX'):
            data, payload = pptx_from_drafts.render_pptx(project_from_stub(), 'T.pptx', reviewed=True,
                                                         structurer=self.structurer(bad, dict(PAYLOAD)),
                                                         on_progress=messages.append)
        self.assertEqual((data, payload['meta']['project_title']), (b'PPTX', 'Studie'))
        self.assertIn('not valid JSON', messages[0])
        with self.assertRaisesRegex(ValueError, 'invalid slide data twice'):
            pptx_from_drafts.render_pptx(project_from_stub(), 'T.pptx', reviewed=True,
                                         structurer=self.structurer(bad, bad))


def project_from_stub():
    from mesakumo.domain import Project
    return Project('p', 'Demo', False, 'scope')


def final_page(db_path, review_json):
    import json
    from pathlib import Path
    from mesakumo.persistence import ProjectStore
    from mesakumo.ui.proposal import final_review_view
    final_review_view(ProjectStore(Path(db_path)), json.loads(review_json))


class FinalReviewPageTests(Base):
    def test_generate_download_and_approve(self):
        from streamlit.testing.v1 import AppTest
        approved_package(self.store)
        calls = []
        def fake_render(project, template, **options):
            calls.append((project.sections[0].title, options['reviewed']))
            options['on_progress']('Strukturiere Folien…')
            return b'PPTX-BYTES', PAYLOAD
        with mock.patch.object(pptx_from_drafts, 'find_template', return_value=Path('Angebotstemplate.pptx')), \
             mock.patch.object(pptx_from_drafts, 'render_pptx', side_effect=fake_render):
            app = AppTest.from_function(final_page, args=(str(self.dir / 'workspace.sqlite3'), json.dumps(REVIEW)),
                                        default_timeout=30).run()
            self.assertFalse(app.exception)
            next(b for b in app.button if b.label == 'Generate PPTX').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(calls, [('Qualifikation', True)])
            labels = [b.label for b in app.get('download_button')]
            self.assertIn('Download presentation (PPTX, version 1)', labels)
            next(t for t in app.text_input if t.label.startswith('Your name')).set_value('Luba').run()
            next(b for b in app.button if b.label.startswith('✅ Approve presentation')).click().run()
            self.assertFalse(app.exception)
        package, _ = ProposalDrafts(self.store, REVIEW).latest_package()
        self.assertEqual(GeneratedDecks(self.store, package).latest()[1]['status'], 'Approved')
        self.assertTrue(any('Presentation approved by Luba' in s.value for s in app.success))


if __name__ == '__main__':
    unittest.main()
