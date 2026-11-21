"""Demo review built from an evidence CSV and pre-filled drafts (synthetic files only)."""
import csv
import tempfile
import unittest
from pathlib import Path

from mesakumo.demo_review import DEMO_PROJECT, build_review, reset, seed_drafts
from mesakumo.persistence import ProjectStore
from mesakumo.proposal import ProposalDrafts, handoff_project

EVIDENCE = ['Requirement', 'Category', 'Tender sources', 'Evidence', 'Evidence sources', 'Evidence fit',
            'Gap / next action', 'Status']
DRAFTS = ['Source row', 'Requirement', 'Category', 'Evidence fit', 'Section title', 'Section text', 'Open items',
          'Check wording', 'Cited evidence', 'Tender sources', 'Unresolved evidence references', 'Draft origin', 'Error']


class DemoReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.inputs = base / 'inputs'
        (self.inputs / 'Beispiel').mkdir(parents=True)
        (self.inputs / 'Beispiel' / 'Referenz.txt').write_text('Projekt Beta: Datenstrategie.', encoding='utf-8')
        (self.inputs / 'Ausschreibung 2').mkdir()
        (self.inputs / 'Ausschreibung 2' / 'Bedingungen.txt').write_text('Zwei Referenzen.', encoding='utf-8')
        self.evidence, self.drafts = base / 'evidence.csv', base / 'drafts.csv'
        self.write(self.evidence, EVIDENCE, [
            dict(Requirement='Zwei Referenzen vorlegen', Category='eligibility/evidence',
                 **{'Tender sources': 'Bedingungen.txt · Lines 1–1', 'Evidence': 'Projekt Beta: Datenstrategie.',
                    'Evidence sources': 'Referenz.txt · Lines 1–1', 'Evidence fit': 'Partial match',
                    'Gap / next action': 'Zweite Referenz fehlt.', 'Status': 'Judged'}),
            dict(Requirement='Preisblatt ausfüllen', Category='deliverable',
                 **{'Tender sources': '', 'Evidence': '', 'Evidence sources': '', 'Evidence fit': '',
                    'Gap / next action': '', 'Status': 'Failed'})])
        self.write(self.drafts, DRAFTS, [
            {**dict.fromkeys(DRAFTS, ''), 'Source row': '1', 'Section title': 'Referenzen',
             'Section text': 'Projekt Beta. [OFFEN: Zweite Referenz ergänzen]',
             'Cited evidence': 'Referenz.txt · Lines 1–1', 'Draft origin': 'Model draft · stub'}])
        self.store = ProjectStore(base / 'demo' / 'workspace.sqlite3')

    @staticmethod
    def write(path, header, rows):
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

    def test_build_seed_reset(self):
        review = build_review(self.evidence, self.inputs)
        self.assertEqual((review['project_id'], len(review['rows']), review['skipped']), (DEMO_PROJECT, 1, 1))
        pid = review['rows'][0]['citation_ids'][0]
        self.assertEqual(review['corpus'][pid]['excerpt'], 'Projekt Beta: Datenstrategie.')
        self.assertEqual(seed_drafts(self.store, review, self.drafts), 1)
        self.assertEqual(seed_drafts(self.store, review, self.drafts), 0)  # Existing work is never overwritten.
        draft = ProposalDrafts(self.store, review).all()['row-1']
        self.assertEqual((draft['status'], draft['citation_ids']), ('Draft', [pid]))
        drafts = ProposalDrafts(self.store, review)
        drafts.approve('row-1', 'Luba', 1, accept_open_items=True)
        package = drafts.approve_package('Luba', project_name='DEMO')
        self.assertEqual([s.title for s in handoff_project(package, review).sections], ['Referenzen'])
        reset(self.store)
        self.assertEqual(ProposalDrafts(self.store, review).all(), {})
        self.assertEqual(ProposalDrafts(self.store, review).latest_package(), (None, False))


if __name__ == '__main__':
    unittest.main()
