import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from mesakumo.documents import intake
from mesakumo.domain import Document, Project, Requirement, SourcePassage
from mesakumo.evidence import EvidenceMatchingError, PassageIndex, assess_requirements
from mesakumo.evidence_workflow import (
    BASE_ROLE, INVENTORY_FIELDS, reviewed_handoff, run_workflow, verify_local_reference,
)


def scan(passages, hits):
    return {'reviewed_source_ids': [p['id'] for p in passages],
            'findings': [{'source_id': p['id'], 'quote': p['text']} for p in passages if p['id'] in hits]}


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.project = Project('test', 'Synthetic sources', False, 'Retrieval test')
        self.project.documents.append(Document('deck', 'example.pptx', 'knowledge', version='v2'))
        texts = ['Workshop durchgeführt.', *(['Inventar.'] * 68), 'Abschlussbericht übergeben.']
        self.project.passages = [
            SourcePassage(str(i), 'deck', f'Slide {i+1}', text, 'v2') for i, text in enumerate(texts)
        ]
        self.index = PassageIndex(self.project.passages)

    def evaluate(self, index, joint=False):
        constraints = ['Abschließende Dokumentation'] if joint else []
        requirement = Requirement('R', 'Workshop', [], constraints=constraints)
        batches = []

        def reply(prompt):
            data = json.loads(prompt.split('\n', 1)[1])
            if 'passages' in data:
                batches.append([p['id'] for p in data['passages']])
                return json.dumps(scan(data['passages'], {'0', '69'}))
            full = any(hit['source_id'] == '69' for hit in data['findings'])
            clause = dict(clause='Workshop', coverage='full', source_ids=['0'], support='Delivered.', gap='')
            checks = [dict(clause=text, coverage='full' if full else 'none',
                           source_ids=['69'] if full else [], support='Report delivered.' if full else '',
                           gap='' if full else 'Report missing.') for text in constraints]
            return json.dumps(dict(clauses=[clause], constraints=checks))

        model = Mock()
        model.complete.side_effect = reply
        assessment, = assess_requirements(self.project, [requirement], model, index=index)
        return assessment, batches, model.complete.call_count

    def test_exact_term_strong_uses_two_calls_instead_of_four(self):
        result, batches, calls = self.evaluate(self.index)
        baseline, _, baseline_calls = self.evaluate(None)
        self.assertEqual(result.match.quality, baseline.match.quality)
        self.assertEqual((result.search_scope, batches, calls, baseline_calls),
                         ('retrieved candidates', [['0']], 2, 4))

    def test_fallback_recovers_joint_support_without_rescanning(self):
        result, batches, calls = self.evaluate(self.index, joint=True)
        baseline, _, baseline_calls = self.evaluate(None, joint=True)
        self.assertEqual(batches[0], ['0'])
        self.assertEqual(sorted(i for batch in batches for i in batch), sorted(p.id for p in self.project.passages))
        self.assertEqual(result.match.source_ids, ['0', '69'])
        self.assertEqual((result.match.quality, baseline.match.quality, calls, baseline_calls),
                         ('sufficient', 'sufficient', 6, 4))
        self.assertEqual(self.index.candidates(dict(text='Beleg', constraints=['Abschlussbericht'])), ['69'])

    def test_failed_fallback_cannot_return_shortlist_verdict(self):
        model = Mock()
        model.complete.side_effect = [json.dumps(scan([{'id': '0'}], set())), '{}']
        with self.assertRaisesRegex(EvidenceMatchingError, 'complete batch coverage'):
            assess_requirements(self.project, [Requirement('R', 'Workshop', [])], model, index=self.index)

    def test_gap_without_lexical_overlap_scans_every_passage(self):
        model = Mock()
        model.complete.side_effect = lambda prompt: json.dumps(scan(json.loads(prompt.split('\n')[1])['passages'], set()))
        result, = assess_requirements(self.project, [Requirement('R', 'Solaranlage', [])], model, index=self.index)
        self.assertEqual((result.match.quality, result.match.source_ids, result.evaluated_count), ('none', [], 70))
        self.assertEqual(model.complete.call_count, 3)


def make_pdf(pages):
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for text in pages:
        page = writer.add_blank_page(width=300, height=300)
        if text:
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 10 100 Td ({text}) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class HistoricalPdfTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.inventory = self.root / 'inventory.csv'
        with self.inventory.open('w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(INVENTORY_FIELDS)
            writer.writerow((BASE_ROLE, 'File', 'example.pdf'))
        self.checklist = self.root / 'approved.csv'
        self.checklist.write_text('requirement_id,requirement_text,approval\nR,Workshop,approved\n')

    def test_partial_pages_provisional_with_original_coordinates(self):
        content = make_pdf(['Overview', '', 'Workshop delivered'])
        (self.root / 'example.pdf').write_bytes(content)
        tender = Project('test', 'Example', False, 'Test')
        self.assertTrue(intake(tender, self.root, 'buyer.pdf', content, 'tender').error)
        self.assertFalse(tender.passages)

        def reply(prompt):
            data = json.loads(prompt.split('\n', 1)[1])
            if 'passages' in data:
                first, last = data['passages']
                self.assertEqual((first['location'], last['location']), ('Page 1', 'Page 3'))
                return json.dumps(scan(data['passages'], {last['id']}))
            return json.dumps(dict(clauses=[dict(clause='Workshop', coverage='full', gap='',
                                                 support='Delivered.', source_ids=[data['findings'][0]['source_id']])],
                                   constraints=[]))

        report = run_workflow(self.checklist, self.inventory, self.root, model=Mock(complete=reply))
        row, = report['results']
        source, = report['source_reading']
        self.assertEqual((source['state'], source['unreadable_pages']), ('partial', [2]))
        self.assertEqual((row['search_state'], row['suggested']['quality']), ('incomplete', 'sufficient'))
        verified = verify_local_reference(report, self.root, source['document_id'], 'Page 3', 'Workshop delivered')
        self.assertEqual(verified['passage_id'], row['evidence'][0]['passage_id'])
        self.assertEqual(verified['document_version'], hashlib.sha256(content).hexdigest())
        self.assertTrue(verified['passage_id'].endswith('-P3'))
        self.assertFalse(reviewed_handoff(report, participant_root=self.root)['reviewed'])
        model = Mock(complete=lambda p: json.dumps(scan(json.loads(p.split('\n')[1])['passages'], set())))
        empty = run_workflow(self.checklist, self.inventory, self.root, model=model)['results'][0]
        self.assertEqual((empty['search_state'], empty['suggested']), ('incomplete', None))

    def test_unsupported_page_encoding_keeps_later_page(self):
        (self.root / 'example.pdf').write_bytes(make_pdf(['First', 'Second', 'Third']))
        model = Mock(complete=lambda p: json.dumps(scan(json.loads(p.split('\n')[1])['passages'], set())))
        with patch('pypdf.PageObject.extract_text', side_effect=['First', NotImplementedError(), 'Third']):
            report = run_workflow(self.checklist, self.inventory, self.root, model=model)
        source, = report['source_reading']
        self.assertEqual((source['state'], source['unreadable_pages'], source['passages']), ('partial', [2], 2))
        self.assertIsNone(report['results'][0]['suggested'])

    def test_empty_and_corrupt_pdfs_remain_failed(self):
        for content in (make_pdf(['', '']), b'invalid fixture'):
            with self.subTest(size=len(content)):
                (self.root / 'example.pdf').write_bytes(content)
                model = Mock()
                report = run_workflow(self.checklist, self.inventory, self.root, model=model)
                self.assertEqual(report['source_reading'][0]['state'], 'failed')
                self.assertEqual(report['search']['passages'], 0)
                model.complete.assert_not_called()
