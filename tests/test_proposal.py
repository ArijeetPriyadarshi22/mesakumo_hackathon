"""Proposal drafting from an approved evidence review: prompt, validation, storage and page."""
import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from mesakumo.persistence import ConflictError, ProjectStore
from mesakumo.proposal import (DRAFTING_SYSTEM, SCHEMA, ProposalDrafts, build_prompt, draft_row,
                               handoff_csv, handoff_project, open_items, pending_items, review_summary,
                               validate_draft)


def citation(pid, location, excerpt):
    return dict(passage_id=pid, document_id='D1', filename='Referenzen.pptx', relative_path='Referenzen.pptx',
                role='Historical proposal', location=location, excerpt=excerpt, document_version='v1')


def requirement(rid, text, kind='eligibility/evidence'):
    return dict(id=rid, text=text, kind=kind, obligation_level='Mandatory', constraints=['bis 16.09.2026'],
                source_ids=['T1'], reviewed=True, provenance='Human', original_excerpts=[])


TENDER = [dict(passage_id='T1', document_id='DT', filename='Leistungsbeschreibung.pdf', location='Page 10',
               original_excerpt='Nachweis: Qualifikationen und akademische Hintergründe der Mitarbeitenden.',
               document_version='t1')]
CORPUS = {
    'P10': citation('P10', 'Slide 10', 'Manuela Engl, Manager. Process Consultant in Industrie 4.0.'),
    'P11': citation('P11', 'Slide 11', 'Weitere Beraterin mit Erfahrung im Maschinenbau.'),
    'P99': citation('P99', 'Slide 99', 'Nicht zitierte Passage.'),
}
ROWS = [
    dict(requirement=requirement('R1', 'Qualifikationen und akademische Hintergründe nachweisen'), tender_sources=TENDER,
         citation_ids=['P10', 'P11'], fit='Partial match', gap='Akademischer Hintergrund fehlt.', notes='Engl passt fachlich.',
         current_action='Verfügbarkeit bestätigen.', status='Reviewed', error='', trace=[]),
    dict(requirement=requirement('R2', 'Projektleitung benennen', 'deliverable'), tender_sources=TENDER,
         citation_ids=['P10'], fit='Strong match', gap='', notes='', current_action='', status='Judged', error='', trace=[]),
    dict(requirement=requirement('R3', 'ISO-9001-Zertifikat vorlegen'), tender_sources=TENDER,
         citation_ids=[], fit='No match', gap='Kein Zertifikat in den Unterlagen.', notes='', current_action='',
         status='Reviewed', error='', trace=[]),
]
REVIEW = dict(project_id='p1', checklist_revision=3, review_revision=7, rows=ROWS, corpus=CORPUS,
              phase='single', selected=[], directory='', documents=[])


def save_approval_fixture(store, review=REVIEW):
    """Represent an already-approved synthetic checklist and evidence review."""
    from mesakumo.checklist import ChecklistReview
    from mesakumo.domain import Project
    from mesakumo.evidence_review import EvidenceReview
    key = (review['project_id'], review['checklist_revision'])
    project = Project(key[0], 'Synthetic tender', True, 'single', revision=key[1])
    ChecklistReview(store)
    EvidenceReview(store, {'project_id': key[0], 'revision': key[1],
                           'rows': [row['requirement'] for row in review['rows']]})
    csv = b'Synthetic approved evidence CSV'
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO projects VALUES (?,?,?)', (*key, json.dumps(project.to_dict())))
        db.execute('INSERT OR IGNORE INTO checklist_approvals VALUES (?,?,?,?,?,?,?)',
                   (*key, project.fingerprint(), '', '{}', csv, hashlib.sha256(csv).hexdigest()))
        db.execute('INSERT OR REPLACE INTO evidence_reviews VALUES (?,?,?,?)',
                   (*key, review['review_revision'], json.dumps(review)))
        db.execute('INSERT OR REPLACE INTO evidence_approvals VALUES (?,?,?,?,?,?)',
                   (*key, review['review_revision'], json.dumps({'review': review}),
                    csv, hashlib.sha256(csv).hexdigest()))

PARTIAL = {'title': 'Qualifikation des Projektteams',
           'text': 'Als Kandidatin schlagen wir Manuela Engl vor, zuletzt Process Consultant in Industrie 4.0. '
                   '[OFFEN: Akademischen Hintergrund von Manuela Engl belegen] [OFFEN: Verfügbarkeit bestätigen]',
           'used_citations': ['Q1']}


class Stub:
    model = 'stub-model'

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def complete_task(self, prompt, **options):
        self.calls.append((prompt, options))
        answer = self.answers.pop(0)
        return answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)


class DraftingLogicTests(unittest.TestCase):
    def test_prompt_contains_only_the_rows_approved_evidence(self):
        prompt, aliases = build_prompt(ROWS[0], CORPUS)
        self.assertEqual({a: c['passage_id'] for a, c in aliases.items()}, {'Q1': 'P10', 'Q2': 'P11'})
        for expected in ('Manuela Engl', 'Weitere Beraterin', 'akademische Hintergründe', 'teilweise belegt',
                         'Akademischer Hintergrund fehlt.', 'Verfügbarkeit bestätigen.', 'Engl passt fachlich.'):
            self.assertIn(expected, prompt)
        self.assertNotIn('Nicht zitierte Passage', prompt)
        self.assertNotIn('P10', prompt)  # Short aliases only.

    def test_partial_draft_uses_drafting_system_and_schema(self):
        model = Stub(PARTIAL)
        result = draft_row(ROWS[0], CORPUS, model)
        _, options = model.calls[0]
        self.assertEqual(options['system'], DRAFTING_SYSTEM)
        self.assertEqual(options['schema'], SCHEMA)
        self.assertEqual(result['citation_ids'], ['P10'])
        self.assertEqual(open_items(result['text']),
                         ['Akademischen Hintergrund von Manuela Engl belegen', 'Verfügbarkeit bestätigen'])

    def test_no_match_gets_fixed_text_without_model_call(self):
        model = Stub()
        result = draft_row(ROWS[2], CORPUS, model)
        self.assertEqual(model.calls, [])
        self.assertEqual(result['citation_ids'], [])
        self.assertEqual(len(open_items(result['text'])), 1)

    def test_rejections(self):
        _, aliases = build_prompt(ROWS[0], CORPUS)
        cases = {
            'not valid JSON': '{"title": ',
            'exactly title': {**PARTIAL, 'extra': 1},
            'not an approved citation': {**PARTIAL, 'used_citations': ['Q9']},
            'cites no approved evidence': {**PARTIAL, 'used_citations': []},
            'not closed': {**PARTIAL, 'text': PARTIAL['text'] + ' [OFFEN: offen'},
            'title is empty': {**PARTIAL, 'title': ' '},
        }
        for expected, raw in cases.items():
            with self.assertRaisesRegex(ValueError, expected, msg=expected):
                validate_draft(raw if isinstance(raw, str) else json.dumps(raw), ROWS[0], aliases)

    def test_strong_row_without_gaps_needs_no_marker(self):
        _, aliases = build_prompt(ROWS[1], CORPUS)
        title, text, ids = validate_draft(json.dumps({'title': 'Projektleitung', 'text': 'Erfahrung belegt.',
                                                      'used_citations': ['Q1']}), ROWS[1], aliases)
        self.assertEqual(ids, ['P10'])

    def test_fenced_json_is_accepted(self):
        _, aliases = build_prompt(ROWS[0], CORPUS)
        self.assertEqual(validate_draft('```json\n' + json.dumps(PARTIAL) + '\n```', ROWS[0], aliases)[2], ['P10'])


class DraftStorageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.store = ProjectStore(Path(temp.name) / 'workspace.sqlite3')
        save_approval_fixture(self.store)
        self.drafts = ProposalDrafts(self.store, REVIEW)

    def test_generate_edit_approve_and_edit_cancels_approval(self):
        self.assertEqual(self.drafts.generate('R1', Stub(PARTIAL), expected_version=0), 1)
        saved = self.drafts.all()['R1']
        self.assertEqual((saved['status'], saved['fit'], saved['citation_ids']), ('Draft', 'Partial match', ['P10']))
        self.drafts.edit('R1', saved['title'], 'Manuela Engl, MBA. [OFFEN: Verfügbarkeit bestätigen]', 1)
        self.assertEqual(self.drafts.all()['R1']['status'], 'Edited')
        with self.assertRaisesRegex(ValueError, 'Reconfirm'):
            self.drafts.approve('R1', 'Luba', 2)
        with self.assertRaisesRegex(ValueError, 'approved for this requirement'):
            self.drafts.approve('R1', 'Luba', 2, accept_open_items=True, citation_ids=['P99'])
        with self.assertRaisesRegex(ValueError, '3 open item'):
            self.drafts.approve('R1', 'Luba', 2, citation_ids=['P11'])
        self.drafts.approve('R1', 'Luba', 2, accept_open_items=True, citation_ids=['P11'])
        saved = self.drafts.all()['R1']
        self.assertEqual((saved['status'], saved['approved_by'], saved['approved_open_items']),
                         ('Approved', 'Luba', ['Akademischer Hintergrund fehlt.', 'Verfügbarkeit bestätigen.',
                                                'Akademischen Hintergrund von Manuela Engl belegen']))
        self.assertEqual(saved['citation_ids'], ['P11'])
        self.drafts.edit('R1', saved['title'], 'Manuela Engl, MBA, verfügbar ab Januar.', 3)
        saved = self.drafts.all()['R1']
        self.assertEqual(saved['status'], 'Edited')
        self.assertNotIn('approved_by', saved)

    def test_actual_open_items_survive_an_unrelated_marker_and_require_resolution(self):
        text = 'Vorschlag für das Projektteam. [OFFEN: Sonstiges klären]'
        self.drafts.generate('R1', Stub({**PARTIAL, 'text': text}), 0)
        self.drafts.edit('R1', 'Projektteam', 'Neuer Entwurf ohne Marker.', 1)
        edited = self.drafts.all()['R1']
        self.assertEqual(list(pending_items(ROWS[0], edited).values()), [
            'Akademischer Hintergrund fehlt.', 'Verfügbarkeit bestätigen.', 'Sonstiges klären'])
        self.drafts.resolve_item('R1', 'gap', 'Reviewer', 'Evidence verified in the supplied record.', 2)
        self.drafts.approve('R1', 'Reviewer', 3, accept_open_items=True, citation_ids=['P10'])
        package = self.drafts.approve_package('Reviewer', acknowledge_excluded=True)
        section, = package['sections']
        self.assertEqual(open_items(section['text']), ['Verfügbarkeit bestätigen.', 'Sonstiges klären'])
        self.assertEqual(section['open_item_resolutions']['gap']['reviewer'], 'Reviewer')
        fixed = draft_row({**ROWS[2], 'current_action': 'Aktuelles Zertifikat anfordern.'}, CORPUS, Stub())
        self.assertEqual(open_items(fixed['text']),
                         ['Kein Zertifikat in den Unterlagen.', 'Aktuelles Zertifikat anfordern.'])

    def test_late_writes_and_stale_package_are_rejected_after_upstream_change(self):
        self.drafts.generate('R1', Stub(PARTIAL), 0)
        self.drafts.approve('R1', 'Reviewer', 1, accept_open_items=True)
        self.drafts.approve_package('Reviewer', acknowledge_excluded=True)
        for upstream in ('evidence', 'checklist'):
            save_approval_fixture(self.store)

            def invalidate(*args, **kwargs):
                with self.store.connect() as db:
                    if upstream == 'evidence':
                        db.execute('UPDATE evidence_reviews SET revision=revision+1')
                    else:
                        db.execute('UPDATE projects SET revision=revision+1')
                return json.dumps(PARTIAL)

            model = Stub()
            model.complete_task = invalidate
            with self.subTest(upstream=upstream), self.assertRaisesRegex(ConflictError, f'approved {upstream} changed'):
                self.drafts.generate('R1', model, 2)
            self.assertEqual(self.drafts.all()['R1']['version'], 2)
            with self.assertRaises(ConflictError):
                self.drafts.edit('R1', 'New title', 'New text', 2)
            with self.assertRaises(ConflictError):
                self.drafts.approve_package('Reviewer', acknowledge_excluded=True)
            self.assertEqual(self.drafts.latest_package(), (None, False))

    def test_decline_then_redraft_uses_the_comment(self):
        self.drafts.generate('R1', Stub(PARTIAL), expected_version=0)
        with self.assertRaisesRegex(ValueError, 'what should change'):
            self.drafts.decline('R1', 'Luba', ' ', 1)
        self.drafts.decline('R1', 'Luba', 'Bitte Studium nicht behaupten, nur Praxiserfahrung.', 1)
        self.assertEqual(self.drafts.all()['R1']['status'], 'Declined')
        model = Stub(PARTIAL)
        self.drafts.generate('R1', model, expected_version=2)
        prompt = model.calls[0][0]
        self.assertIn('Bitte Studium nicht behaupten', prompt)
        self.assertIn(PARTIAL['text'][:40], prompt)  # The declined text is shown for revision.
        self.assertIn('abgelehnt', prompt)
        saved = self.drafts.all()['R1']
        self.assertEqual((saved['status'], saved['redraft_of_decline']), ('Draft', True))
        self.assertEqual(saved['declines'][0]['comment'], 'Bitte Studium nicht behaupten, nur Praxiserfahrung.')
        self.assertNotIn('decline_comment', saved)
        # A later plain redraft carries no feedback.
        model = Stub(PARTIAL)
        self.drafts.generate('R1', model, expected_version=3)
        self.assertNotIn('abgelehnt', model.calls[0][0])

    def test_package_approval_handoff_and_staleness(self):
        self.drafts.generate('R1', Stub(PARTIAL), expected_version=0)
        self.drafts.generate('R2', Stub({'title': 'Projektleitung', 'text': 'Erfahrung belegt.',
                                         'used_citations': ['Q1']}), expected_version=0)
        with self.assertRaisesRegex(ValueError, 'at least one section'):
            self.drafts.approve_package('Luba')
        self.drafts.approve('R2', 'Luba', 1)
        with self.assertRaisesRegex(ValueError, '2 requirement'):
            self.drafts.approve_package('Luba')
        package = self.drafts.approve_package('Luba', acknowledge_excluded=True, project_name='Ausschreibung 9')
        self.assertEqual([s['requirement_id'] for s in package['sections']], ['R2'])
        self.assertEqual([(e['requirement_id'], e['status']) for e in package['excluded']],
                         [('R1', 'Draft'), ('R3', 'Not drafted')])
        self.assertEqual(self.drafts.latest_package(), (package, True))

        import csv, io
        table = list(csv.DictReader(io.StringIO(handoff_csv(package).decode('utf-8-sig'), newline='')))
        self.assertEqual([(r['Requirement ID'], r['Section title']) for r in table], [('R2', 'Projektleitung')])
        self.assertEqual([c['passage_id'] for c in json.loads(table[0]['Citations'])], ['P10'])
        project = handoff_project(package, REVIEW)
        self.assertEqual((project.name, [s.title for s in project.sections]), ('Ausschreibung 9', ['Projektleitung']))
        self.assertEqual([r.id for r in project.requirements], ['R1', 'R2', 'R3'])
        self.assertEqual([(e.quality, e.decision) for e in project.evidence], [('sufficient', 'Approved')])
        self.assertEqual([p.id for p in project.passages], ['P10'])

        self.drafts.decline('R1', 'Luba', 'Zu allgemein.', 1)  # Any change makes the package stale.
        self.assertEqual(self.drafts.latest_package()[1], False)

    def test_review_summary_lists_what_needs_attention(self):
        self.drafts.generate('R1', Stub({**PARTIAL, 'text': PARTIAL['text'] + ' Sie ist als Hauptdozentin benannt.'}),
                             expected_version=0)
        self.drafts.generate('R3', Stub(), expected_version=0)
        self.drafts.decline('R3', 'Luba', 'Zertifikat anfragen.', 1)
        summary = review_summary(REVIEW, self.drafts.all(), 'Ausschreibung 9')
        attention = summary.split('## Needs attention')[1].split('## Sections')[0]
        self.assertIn('waiting for your decision', attention)
        self.assertIn('check wording: benannt, hauptdozentin', attention)
        self.assertIn('declined by Luba', attention)
        self.assertIn('not drafted yet', attention)
        self.assertIn('| 3 | 0 | 1 | 1 | 1 |', summary)
        self.assertIn('> Declined by Luba: Zertifikat anfragen.', summary)

    def test_stale_version_is_rejected(self):
        self.drafts.generate('R1', Stub(PARTIAL), expected_version=0)
        with self.assertRaises(ConflictError):
            self.drafts.edit('R1', 'T', 'Text', expected_version=0)
        with self.assertRaises(ConflictError):
            self.drafts.generate('R1', Stub(PARTIAL), expected_version=0)

    def test_failed_model_call_writes_nothing(self):
        with self.assertRaises(ValueError):
            self.drafts.generate('R1', Stub('not json'), expected_version=0)
        self.assertEqual(self.drafts.all(), {})

    def test_decisions_need_name_and_edit_needs_closed_markers(self):
        self.drafts.generate('R3', Stub(), expected_version=0)
        with self.assertRaisesRegex(ValueError, 'reviewer'):
            self.drafts.approve('R3', ' ', 1, accept_open_items=True)
        with self.assertRaisesRegex(ValueError, 'reviewer'):
            self.drafts.decline('R3', ' ', 'Kommentar', 1)
        with self.assertRaisesRegex(ValueError, 'Close every'):
            self.drafts.edit('R3', 'T', 'Text [OFFEN: offen', 1)

    def test_drafts_belong_to_one_evidence_revision(self):
        self.drafts.generate('R3', Stub(), expected_version=0)
        newer = ProposalDrafts(self.store, {**REVIEW, 'review_revision': 8})
        self.assertEqual(newer.all(), {})
        self.assertIn('R3', self.drafts.all())

    def test_unknown_requirement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'not part of the approved'):
            self.drafts.generate('R9', Stub(), expected_version=0)


def page_script(db_path, review_json):
    import json
    from pathlib import Path
    from mesakumo.persistence import ProjectStore
    from mesakumo.ui.proposal import proposal_view
    proposal_view(ProjectStore(Path(db_path)), {'review': json.loads(review_json)})


def current_revision_page(db_path):
    import json
    from pathlib import Path
    from mesakumo.persistence import ProjectStore
    from mesakumo.ui.proposal import proposal_view
    store = ProjectStore(Path(db_path))
    with store.connect() as db:
        payload, = db.execute('SELECT payload FROM evidence_reviews').fetchone()
    proposal_view(store, {'review': json.loads(payload)})


class ProposalPageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'workspace.sqlite3'
        self.store = ProjectStore(self.path)
        save_approval_fixture(self.store)
        os.environ['MESAKUMO_LLM_PROVIDER'] = 'disabled'  # Never reach a real provider from tests.
        self.addCleanup(os.environ.pop, 'MESAKUMO_LLM_PROVIDER', None)

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_function(page_script, args=(str(self.path), json.dumps(REVIEW)), default_timeout=30)

    @staticmethod
    def widget(items, label):
        return next(w for w in items if w.label.startswith(label))

    def test_page_lists_sections_and_edits_a_saved_draft(self):
        ProposalDrafts(self.store, REVIEW).generate('R1', Stub(PARTIAL), expected_version=0)
        app = self.app().run()
        self.assertFalse(app.exception)
        self.assertIn('drafting is unavailable', app.info[0].value)
        self.assertEqual([m.value for m in app.metric], ['3', '1', '0', '0', '2'])
        self.assertTrue(any(w.value.startswith('Open items') for w in app.warning))
        self.widget(app.text_area, 'Section text').set_value(
            'Manuela Engl, MBA (laut Dozentenblatt). [OFFEN: Verfügbarkeit bestätigen]')
        self.widget(app.button, 'Save edits').click().run()
        self.assertFalse(app.exception)
        saved = ProposalDrafts(self.store, REVIEW).all()['R1']
        self.assertEqual((saved['status'], open_items(saved['text'])), ('Edited', ['Verfügbarkeit bestätigen']))

    def test_page_approve_decline_and_package(self):
        ProposalDrafts(self.store, REVIEW).generate('R1', Stub(PARTIAL), expected_version=0)
        app = self.app().run()
        self.widget(app.button, '✅ Approve').click().run()
        self.assertIn('Enter your name', app.error[0].value)  # No reviewer name yet.
        self.widget(app.text_input, 'Your name').set_value('Luba').run()
        self.widget(app.button, '✅ Approve').click().run()
        self.assertIn('open item(s) remain', app.error[0].value)
        self.widget(app.checkbox, 'Keep the 3 open item').check()
        self.widget(app.button, '✅ Approve').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(ProposalDrafts(self.store, REVIEW).all()['R1']['status'], 'Approved')

        self.widget(app.checkbox, 'Leave out the 2 requirement').check()
        self.widget(app.button, 'Approve draft for PPTX').click().run()
        self.assertFalse(app.exception)
        package, current = ProposalDrafts(self.store, REVIEW).latest_package()
        self.assertTrue(current)
        self.assertEqual((package['reviewer'], len(package['sections'])), ('Luba', 1))
        self.assertTrue(any('Draft approved for the PPTX' in s.value for s in app.success))

    def test_page_decline_needs_comment(self):
        ProposalDrafts(self.store, REVIEW).generate('R1', Stub(PARTIAL), expected_version=0)
        app = self.app().run()
        self.widget(app.text_input, 'Your name').set_value('Luba').run()
        self.widget(app.button, '❌ Decline').click().run()
        self.assertIn('what should change', app.error[0].value)
        self.widget(app.text_area, 'What should change').set_value('Kein Studium behaupten.')
        self.widget(app.button, '❌ Decline').click().run()
        self.assertFalse(app.exception)
        saved = ProposalDrafts(self.store, REVIEW).all()['R1']
        self.assertEqual((saved['status'], saved['decline_comment']), ('Declined', 'Kein Studium behaupten.'))
        self.assertTrue(any('Declined by Luba' in e.value for e in app.error))

    def test_stale_tab_cannot_approve_an_unseen_revision(self):
        from streamlit.testing.v1 import AppTest
        ProposalDrafts(self.store, REVIEW).generate('R1', Stub(PARTIAL), 0)
        tab_a = AppTest.from_function(current_revision_page, args=(str(self.path),)).run()
        self.widget(tab_a.text_input, 'Your name').set_value('Tab A').run()
        self.widget(tab_a.checkbox, 'Keep the 3 open item').check()
        old_button = self.widget(tab_a.button, '✅ Approve')

        newer = {**REVIEW, 'review_revision': REVIEW['review_revision'] + 1}
        save_approval_fixture(self.store, newer)
        new_service = ProposalDrafts(self.store, newer)
        new_service.generate('R1', Stub({**PARTIAL, 'title': 'Unseen draft'}), 0)
        tab_b = AppTest.from_function(current_revision_page, args=(str(self.path),)).run()
        self.assertEqual(self.widget(tab_b.text_input, 'Section title').value, 'Unseen draft')

        old_button.click().run()
        self.assertFalse(tab_a.exception)
        self.assertEqual(new_service.all()['R1']['status'], 'Draft')
        self.assertEqual(self.widget(tab_a.text_input, 'Section title').value, 'Unseen draft')

    def test_filters_default_to_prose_categories(self):
        app = self.app().run()
        table = app.dataframe[0].value
        self.assertEqual(sorted(table['Category'].unique()), ['deliverable', 'eligibility/evidence'])
        self.assertEqual(len(table), 3)


if __name__ == '__main__':
    unittest.main()
