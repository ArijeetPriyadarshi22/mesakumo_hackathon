import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from mesakumo.evidence_workflow import (
    BASE_ROLE, INVENTORY_FIELDS, ChecklistColumns, read_checklist, reviewed_handoff, run_workflow,
)
from mesakumo.models import ModelUnavailable


class WorkshopModel:
    def __init__(self):
        self.scanned = []

    def complete(self, prompt):
        data = json.loads(prompt.split('\n', 1)[1])
        requirement = data['requirement']
        if 'passages' in data:
            passages = data['passages']
            self.scanned.extend(passages)
            return json.dumps({
                'reviewed_source_ids': [p['id'] for p in passages],
                'findings': [
                    {'source_id': p['id'], 'quote': 'Zwei Workshops'}
                    for p in passages if 'Workshop' in requirement['text'] and 'Zwei Workshops' in p['text']
                ],
            })
        ids = [finding['source_id'] for finding in data['findings']]
        return json.dumps({
            'clauses': [{'clause': requirement['text'], 'coverage': 'full', 'source_ids': ids,
                         'support': 'Two workshops are recorded.', 'gap': ''}],
            'constraints': [
                {'clause': item, 'coverage': 'none', 'source_ids': [], 'support': '', 'gap': 'Certification is missing.'}
                for item in requirement['constraints']
            ],
        })


class EvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.participants = self.root / 'inputs'
        self.participants.mkdir()
        self.checklist = self.root / 'approved.csv'
        self.inventory = self.root / 'inventory.csv'
        self.write_checklist()
        (self.participants / 'a').mkdir()
        (self.participants / 'b').mkdir()
        (self.participants / 'a/reference.txt').write_text('Zwei Workshops durchgeführt.')
        (self.participants / 'b/reference.txt').write_text('Zwei Workshops dokumentiert.')
        (self.participants / 'tender.txt').write_text('Zwei Workshops gefordert.')
        self.write_inventory([
            (BASE_ROLE, 'File', 'a/reference.txt'),
            (BASE_ROLE, 'File', 'b/reference.txt'),
            ('Historical tender input', 'File', 'tender.txt'),
        ])

    def write_checklist(self):
        with self.checklist.open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file, delimiter=';')
            writer.writerow(['requirement_id', 'requirement_text', 'approval', 'constraints', 'tender_source'])
            writer.writerows([
                ('R1', 'Zwei Workshops durchführen.', 'approved', '', 'Tender p. 2'),
                ('R2', 'Workshop-Erfahrung bei öffentlicher Verwaltung.', 'freigegeben',
                 'Trainerzertifikat erforderlich.', 'Tender p. 3'),
                ('R3', 'Solaranlagen installieren.', 'approved', '', 'Tender p. 4'),
                ('R4', 'Not part of the approved checklist.', 'draft', '', 'Tender p. 5'),
            ])

    def write_inventory(self, rows):
        with self.inventory.open('w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(INVENTORY_FIELDS)
            writer.writerows(rows)

    def run_workflow(self, model, **options):
        return run_workflow(self.checklist, self.inventory, self.participants, model=model, **options)

    def test_two_csv_inputs_company_filter_and_verified_evidence(self):
        model = WorkshopModel()
        report = self.run_workflow(model)
        self.assertEqual(report['checklist']['unapproved_ids'], ['R4'])
        self.assertEqual(report['search']['passages'], 2)
        self.assertEqual({p['source_role'] for p in model.scanned}, {BASE_ROLE})
        self.assertNotIn('gefordert', repr(model.scanned))
        first, partial, gap = report['results']
        self.assertEqual([r['suggested']['quality'] for r in report['results']], ['sufficient', 'partial', 'none'])
        self.assertEqual(partial['requirement']['provenance'], 'Tender p. 3')
        self.assertEqual(partial['requirement']['constraints'], ['Trainerzertifikat erforderlich.'])
        self.assertEqual(partial['suggested']['clauses'][1]['gap'], 'Certification is missing.')
        self.assertEqual(gap['evidence'], [])
        self.assertEqual({item['path'] for item in first['evidence']}, {'a/reference.txt', 'b/reference.txt'})
        self.assertEqual(len({item['document_id'] for item in first['evidence']}), 2)
        self.assertTrue(all(item['quotes'] == ['Zwei Workshops'] for item in first['evidence']))
        stable = self.run_workflow(WorkshopModel())['results'][0]['suggested']['source_ids']
        self.assertEqual(stable, first['suggested']['source_ids'])

    def test_explicit_approval_mapping_and_malformed_rows(self):
        self.checklist.write_text('Key,Text\nR1,Projektarbeit\n')
        fields = ChecklistColumns(id='Key', text='Text')
        with self.assertRaisesRegex(ValueError, 'approval'):
            read_checklist(self.checklist, fields)
        imported, _ = read_checklist(self.checklist, fields, approved_export=True)
        self.assertEqual(imported[0].text, 'Projektarbeit')
        self.checklist.write_text('Key,Text,approval\nR1,Projektarbeit,draft\n')
        with self.assertRaisesRegex(ValueError, 'no approved'):
            read_checklist(self.checklist, fields, approved_export=True)
        self.checklist.write_text('Key,Text,approval\nR1,Projektarbeit,unsure\n')
        with self.assertRaisesRegex(ValueError, 'unknown approval'):
            read_checklist(self.checklist, fields)
        self.checklist.write_text('Key,Text,approval\nR1,Projektarbeit,approved\nR1,Lehre,approved\n')
        with self.assertRaisesRegex(ValueError, 'unique requirement ID'):
            read_checklist(self.checklist, fields)
        self.checklist.write_text('Key,Text,approval\nR1,Projektarbeit,approved,extra\n')
        with self.assertRaisesRegex(ValueError, 'wrong number of fields'):
            read_checklist(self.checklist, fields)

    def test_unreadable_or_unsafe_selected_sources_block_false_gaps(self):
        (self.root / 'outside.txt').write_text('Zwei Workshops')
        (self.participants / 'link.txt').symlink_to(self.root / 'outside.txt')
        self.write_inventory([
            (BASE_ROLE, 'File', 'a/reference.txt'),
            (BASE_ROLE, 'File', '../outside.txt'),
            (BASE_ROLE, 'File', 'link.txt'),
            (BASE_ROLE, 'File', 'missing.txt'),
        ])
        model = Mock()
        report = self.run_workflow(model)
        model.complete.assert_not_called()
        self.assertEqual(report['search']['failed_files'], 3)
        self.assertEqual(report['search']['read_files'], 1)
        self.assertTrue(all(r['search_state'] == 'incomplete' and r['suggested'] is None for r in report['results']))

    def test_selected_optional_file_duplicate_and_credential_exclusion(self):
        (self.participants / 'profile.txt').write_text('Train-the-Trainer qualification.')
        (self.participants / 'duplicate.txt').write_bytes((self.participants / 'a/reference.txt').read_bytes())
        self.write_inventory([
            (BASE_ROLE, 'File', 'a/reference.txt'),
            (BASE_ROLE, 'File', 'duplicate.txt'),
            ('Team information', 'File', 'profile.txt'),
            ('Access credentials', 'File', 'credentials.md'),
        ])
        report = self.run_workflow(WorkshopModel(), include_paths=('profile.txt',))
        self.assertEqual(report['search']['read_files'], 2)
        self.assertEqual(report['search']['skipped_inventory_rows'], 2)
        self.assertNotIn('credentials.md', repr(report))
        with self.assertRaisesRegex(ValueError, 'eligible evidence role'):
            self.run_workflow(WorkshopModel(), include_paths=('credentials.md',))

    def test_provider_failure_remains_incomplete(self):
        model = Mock()
        model.complete.side_effect = ModelUnavailable('fixture error')
        report = self.run_workflow(model)
        self.assertTrue(all(r['suggested'] is None and r['search_state'] == 'incomplete' for r in report['results']))

    def test_review_round_trip_rerun_and_stale_after_source_change(self):
        report = self.run_workflow(WorkshopModel())
        row, partial, gap = report['results']
        chosen = row['suggested']['source_ids'][0]
        row['review'] = {'quality': 'partial', 'source_ids': [chosen], 'comment': 'Verify the dates.',
                         'fingerprint': row['fingerprint']}
        gap['review'] = {'quality': 'none', 'source_ids': [], 'comment': 'Ask team for a reference.',
                         'fingerprint': gap['fingerprint']}
        reopened = json.loads(json.dumps(report))
        handoff = reviewed_handoff(reopened)
        self.assertEqual(handoff['reviewed'][0]['reviewer_comment'], 'Verify the dates.')
        self.assertEqual(handoff['reviewed'][0]['evidence'][0]['passage_id'], chosen)
        self.assertEqual(handoff['reviewed'][1]['evidence'], [])
        self.assertEqual(handoff['pending'][0]['requirement_id'], partial['requirement']['id'])

        def nothing(prompt):
            rows = json.loads(prompt.split('\n', 1)[1])['passages']
            return json.dumps({'reviewed_source_ids': [p['id'] for p in rows], 'findings': []})

        rerun = self.run_workflow(Mock(complete=nothing), previous_report=reopened)
        self.assertEqual(rerun['results'][0]['review'], row['review'])
        self.assertEqual(reviewed_handoff(rerun)['reviewed'][0]['evidence'][0]['passage_id'], chosen)
        (self.participants / 'a/reference.txt').write_text('Drei Workshops.')
        changed = self.run_workflow(WorkshopModel(), previous_report=reopened)
        self.assertEqual(changed['results'][0]['review']['comment'], 'Verify the dates.')
        self.assertNotEqual(changed['results'][0]['review']['fingerprint'], changed['results'][0]['fingerprint'])
        self.assertEqual(reviewed_handoff(changed)['reviewed'], [])

    def test_handoff_rejects_invented_review_citation(self):
        report = self.run_workflow(WorkshopModel())
        row = report['results'][0]
        row['review'] = {'quality': 'sufficient', 'source_ids': ['invented'], 'comment': '',
                         'fingerprint': row['fingerprint']}
        with self.assertRaisesRegex(ValueError, 'invalid verdict or evidence'):
            reviewed_handoff(report)

    def test_selected_unsupported_source_blocks_verified_gap(self):
        self.write_inventory([(BASE_ROLE, 'File', 'a/reference.txt'),
                              ('Historical proposal', 'File', 'prices.xlsx')])
        model = Mock()
        report = self.run_workflow(model, include_roles=('Historical proposal',))
        model.complete.assert_not_called()
        self.assertEqual(report['search']['failed_files'], 1)
        self.assertTrue(all(row['search_state'] == 'incomplete' for row in report['results']))

    def test_provisional_note_needs_review_after_successful_retry(self):
        failed = Mock()
        failed.complete.side_effect = ModelUnavailable('Fixture failure')
        report = self.run_workflow(failed)
        row = report['results'][0]
        row['review'] = {'quality': None, 'source_ids': [], 'comment': 'Retry.',
                         'fingerprint': row['fingerprint']}
        retry = self.run_workflow(WorkshopModel(), previous_report=report)
        self.assertEqual(retry['results'][0]['review']['comment'], 'Retry.')
        handoff = reviewed_handoff(retry)
        self.assertFalse(handoff['reviewed'])
        self.assertEqual(len(handoff['pending']), 3)

    def test_reviewed_excerpt_survives_new_suggestion_on_same_passage(self):
        report = self.run_workflow(WorkshopModel())
        row = report['results'][0]
        item = next(i for i in row['evidence'] if i['path'] == 'a/reference.txt')
        item['quotes'].append('durchgeführt.')
        row['review'] = {'quality': 'partial', 'source_ids': [item['passage_id']],
                         'comment': 'Verified wording.', 'fingerprint': row['fingerprint']}
        retry = self.run_workflow(WorkshopModel(), previous_report=report)
        evidence = reviewed_handoff(retry)['reviewed'][0]['evidence']
        self.assertIn('durchgeführt.', evidence[0]['quotes'])

    def test_handoff_rechecks_originals_and_recognizes_legacy_unsupported_files(self):
        report = self.run_workflow(WorkshopModel())
        gap = report['results'][2]
        gap['review'] = dict(quality='none', source_ids=[], comment='', fingerprint=gap['fingerprint'])
        self.assertEqual(len(reviewed_handoff(report, participant_root=self.participants)['reviewed']), 1)
        report['source_reading'].append(dict(state='skipped', path='prices.xlsx', role='Historical proposal',
                                             reason='File format is unsupported by the evidence reader.'))
        report['search']['skipped_inventory_rows'] += 1
        self.assertFalse(reviewed_handoff(report)['reviewed'])
        self.assertIsNone(gap['suggested'])
        (self.participants / 'a/reference.txt').write_text('Changed.')
        with self.assertRaisesRegex(ValueError, 'changed or is missing'):
            reviewed_handoff(report, participant_root=self.participants)
