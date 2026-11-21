"""Drafting handover files from evidence CSVs (both layouts), with synthetic files only."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from mesakumo.evidence_csv_drafts import SourceFinder, check_wording, draft_all, load_rows, write_outputs
from mesakumo.proposal import open_items

TABLE = ['Requirement', 'Category', 'Tender sources', 'Evidence', 'Evidence sources', 'Evidence fit',
         'Gap / next action', 'Status']


class Stub:
    model = 'stub'

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def complete_task(self, prompt, **options):
        self.prompts.append(prompt)
        return json.dumps(self.answers.pop(0), ensure_ascii=False)


class EvidenceCsvDraftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'inputs'
        for folder, text in (('Beispiel A', 'Projekt Alpha: Workshops für Mittelstand.'),
                             ('Beispiel B', 'Projekt Beta: Datenstrategie.')):
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / 'Referenz.txt').write_text(text, encoding='utf-8')  # Same name, two folders.
        (self.root / 'Ausschreibung 2' / 'anschreiben').mkdir(parents=True)
        (self.root / 'Ausschreibung 2' / 'anschreiben' / 'Bedingungen.txt').write_text('Zwei Referenzen vorlegen.', encoding='utf-8')
        (self.root / '99 LLM keys').mkdir()
        (self.root / '99 LLM keys' / 'Referenz.txt').write_text('SECRET', encoding='utf-8')
        self.csv = Path(temp.name) / 'export.csv'
        self.out = Path(temp.name) / 'out' / 'drafts'

    def write(self, rows, header=TABLE):
        with open(self.csv, 'w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

    def table_row(self, **values):
        return {'Requirement': 'Zwei Referenzen vorlegen', 'Category': 'eligibility/evidence',
                'Tender sources': 'anschreiben/Bedingungen.txt · Lines 1–1',
                'Evidence': 'Projekt Beta: Datenstrategie.', 'Evidence sources': 'Referenz.txt · Lines 1–1',
                'Evidence fit': 'Partial match', 'Gap / next action': 'Zweite Referenz fehlt.', 'Status': 'Judged',
                **values}

    def test_table_download_is_matched_back_to_exact_passages(self):
        self.write([self.table_row(), self.table_row(Status='Failed', **{'Evidence fit': ''})])
        rows, skipped = load_rows(self.csv, SourceFinder(self.root))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Same file name in two folders: the one whose text the matcher saw is chosen; key folder never used.
        self.assertEqual([c['excerpt'] for c in row['corpus'].values()], ['Projekt Beta: Datenstrategie.'])
        self.assertEqual(row['tender_sources'][0]['original_excerpt'], 'Zwei Referenzen vorlegen.')
        self.assertEqual((row['gap'], row['notes']), ('Zweite Referenz fehlt.', ''))
        self.assertEqual(skipped, [(2, 'Zwei Referenzen vorlegen', 'no evidence verdict (status Failed)')])

    def test_strong_note_is_context_not_a_gap(self):
        self.write([self.table_row(**{'Evidence fit': 'Strong match', 'Gap / next action': 'Requirement met.'})])
        row = load_rows(self.csv, SourceFinder(self.root))[0][0]
        self.assertEqual((row['gap'], row['notes']), ('', 'Requirement met.'))

    def test_unfound_reference_is_reported_and_row_without_evidence_skipped(self):
        self.write([self.table_row(**{'Evidence sources': 'Fehlt.pdf · Page 3'})])
        rows, skipped = load_rows(self.csv, SourceFinder(self.root))
        self.assertEqual(rows, [])
        self.assertIn('no cited evidence could be found', skipped[0][2])

    def test_approved_export_uses_structured_citations(self):
        citation = dict(passage_id='P1', filename='Referenz.txt', location='Lines 1–1', excerpt='Projekt Alpha.',
                        relative_path='Beispiel A/Referenz.txt')
        tender = [dict(filename='Bedingungen.txt', location='Lines 1–1', original_excerpt='Zwei Referenzen vorlegen.')]
        header = TABLE + ['Requirement ID', 'Tender citations', 'Evidence citations']
        self.write([{**self.table_row(Status='Reviewed'), 'Requirement ID': 'R-7',
                     'Tender citations': json.dumps(tender), 'Evidence citations': json.dumps([citation])}], header)
        row = load_rows(self.csv, SourceFinder(self.root))[0][0]
        self.assertEqual((row['requirement']['id'], row['citation_ids'], row['status']), ('R-7', ['P1'], 'Reviewed'))

    def test_drafting_retry_and_outputs(self):
        self.write([self.table_row(), self.table_row(**{'Evidence fit': 'No match', 'Evidence sources': '', 'Evidence': ''})])
        rows, skipped = load_rows(self.csv, SourceFinder(self.root))
        bad = {'title': 'T', 'text': 'Keine Lücke markiert.', 'used_citations': ['Q1']}
        good = {'title': 'Referenzen', 'text': 'mesakumo benennt Projekt Beta. [OFFEN: Zweite Referenz ergänzen]',
                'used_citations': ['Q1']}
        model = Stub(bad, good)  # First answer is rejected, the retry succeeds.
        results = draft_all(rows, model, workers=1)
        self.assertEqual(len(model.prompts), 2)
        self.assertTrue(all(draft for _, draft, _ in results))
        md, out_csv = write_outputs(self.out, self.csv, results, skipped, reviewed_source=False)
        text = md.read_text(encoding='utf-8')
        self.assertIn('Unreviewed evidence', text)
        self.assertIn('Check wording:* benennt', text)
        with open(out_csv, encoding='utf-8-sig', newline='') as handle:
            table = list(csv.DictReader(handle))
        self.assertEqual([r['Evidence fit'] for r in table], ['Partial match', 'No match'])
        self.assertEqual(table[0]['Open items'], 'Zweite Referenz ergänzen')
        self.assertEqual(len(open_items(table[1]['Section text'])), 1)

    def test_check_wording(self):
        self.assertEqual(check_wording('Sie ist als Hauptdozentin benannt und zertifiziert.'),
                         ['benannt', 'hauptdozentin', 'zertifiziert'])
        self.assertEqual(check_wording('Wir schlagen sie als Kandidatin vor.'), [])


if __name__ == '__main__':
    unittest.main()
