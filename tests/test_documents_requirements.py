import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from mesakumo.domain import Project
from mesakumo.documents import intake, LocalProcessor
from mesakumo.persistence import ProjectStore
from mesakumo.requirements import (extract_candidates, save_requirement, review_checklist,
                                   validate_candidates, tender_passages, delete_requirement)
from mesakumo.models import RequestyModel, DisabledModel, ModelUnavailable
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject, DictionaryObject

class DocumentsRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = Project('test', 'Test document project', False, 'Synthetic parser test')
        self.content = Path('tests/fixtures/synthetic_tender.txt').read_bytes()

    def upload(self, name='tender.txt', content=None):
        return intake(self.project, self.root, name, self.content if content is None else content, 'tender')

    def test_text_versions_originals_and_locations(self):
        first = self.upload()
        second = self.upload(content=self.content + b'5. Retain audit notes.\n')
        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.version, second.version)
        self.assertEqual((self.root / first.original_path).read_bytes(), self.content)
        self.assertEqual(self.project.passages[0].location, 'Lines 1–5')
        self.assertTrue(all(p.document_version == second.version for p in tender_passages(self.project)))

    def test_docx_body_heading_table_location(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Scope</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Two workshops required.</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        doc = self.upload('fixture.docx', buffer.getvalue())
        self.assertFalse(doc.error)
        self.assertEqual(self.project.passages[1].location, 'Body paragraph 2 · Scope')
        self.assertEqual(self.project.passages[1].excerpt, 'Two workshops required.')

    def test_pdf_page_and_scanned_failure(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 100 Td (Two workshops required.) Tj ET')
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO(); writer.write(buffer)
        self.upload('text.pdf', buffer.getvalue())
        self.assertEqual(self.project.passages[0].location, 'Page 1')
        self.assertIn('Two workshops', self.project.passages[0].excerpt)
        blank = PdfWriter(); blank.add_blank_page(width=300, height=300)
        buffer = io.BytesIO(); blank.write(buffer)
        failed = self.upload('scan.pdf', buffer.getvalue())
        self.assertIn('possible scan', failed.error)
        self.assertEqual(len(self.project.passages), 1)
        self.assertTrue((self.root / failed.original_path).exists())

    def test_empty_and_corrupt_do_not_create_sources(self):
        for name, content in [('empty.txt', b' \n'), ('bad.docx', b'corrupt')]:
            doc = self.upload(name, content)
            self.assertTrue(doc.error)
        self.assertEqual(self.project.passages, [])

    def test_candidate_validation_and_failure_preserves_edits(self):
        self.upload()
        source = tender_passages(self.project)[0].id
        save_requirement(self.project, '', 'Must deliver two workshops', 'deliverable', [source], ['two'], 'Reviewer', 'Manual addition')
        before = self.project.to_dict()
        with self.assertRaises(ModelUnavailable):
            extract_candidates(self.project, DisabledModel())
        self.assertEqual(before, self.project.to_dict())
        row = dict(statement='Deliver two workshops', source_ids=['invented'], kind='deliverable', constraints=['two'])
        with self.assertRaises(ValueError):
            validate_candidates(json.dumps({'requirements': [row]}), tender_passages(self.project))
        row['source_ids'] = [source]
        class FixtureModel:
            def complete(self, prompt):
                return json.dumps({'requirements': [row]})
        extract_candidates(self.project, FixtureModel())
        self.assertEqual(len(self.project.requirements), 2)
        self.assertEqual(self.project.requirements[1].original_excerpts, [self.content.decode().rstrip()])

    def test_review_edit_delete_and_reopen(self):
        self.upload()
        source = tender_passages(self.project)[0].id
        save_requirement(self.project, '', 'Two workshops', 'deliverable', [source], ['two'], 'Reviewer', 'Manual')
        review_checklist(self.project, 'Reviewer')
        self.assertTrue(self.project.requirements[0].reviewed)
        store = ProjectStore(self.root / 'state.sqlite3'); store.create(self.project)
        restored = ProjectStore(store.path).load('test')
        self.assertTrue(restored.requirements[0].reviewed)
        target = restored.requirements[0].id
        save_requirement(restored, target, 'Two mandatory workshops', 'deliverable', [source], ['two'], 'Reviewer', 'Correction')
        self.assertFalse(restored.requirements[0].reviewed)
        delete_requirement(restored, target, 'Reviewer')
        self.assertEqual(restored.requirements, [])

    def test_new_document_version_requires_source_correction(self):
        self.upload()
        source = tender_passages(self.project)[0].id
        save_requirement(self.project, '', 'Workshop', 'deliverable', [source], [], 'Reviewer', 'Manual')
        self.upload(content=self.content+b'New amendment')
        with self.assertRaises(ValueError):
            review_checklist(self.project, 'Reviewer')

    def test_context_limit_does_not_call_model(self):
        self.upload(content=b'Long tender ' * 6000)
        class NeverCall:
            def complete(self, prompt):
                raise AssertionError('Must not send oversized input')
        with self.assertRaisesRegex(ValueError, 'No text was truncated'):
            extract_candidates(self.project, NeverCall())

class ProviderBoundaryTests(unittest.TestCase):
    def test_request_contract_and_retry(self):
        class Opener:
            calls = 0
            def open(inner, request, timeout):
                inner.calls += 1
                self.assertEqual(request.full_url, 'https://router.eu.requesty.ai/v1/chat/completions')
                self.assertEqual(request.headers['Authorization'], 'Bearer fixture-key')
                self.assertEqual(timeout, 45)
                self.assertEqual(json.loads(request.data)['model'], 'fixture-approved-claude')
                if inner.calls < 3:
                    raise HTTPError(request.full_url, 503, 'unavailable', {}, None)
                return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}).encode())
        opener = Opener()
        model = RequestyModel('https://router.eu.requesty.ai/v1', 'fixture-approved-claude', 'fixture-key', opener=opener, sleep=lambda _: None)
        self.assertEqual(model.complete('Synthetic prompt'), '{}')
        self.assertEqual(opener.calls, 3)

    def test_no_unapproved_endpoint_or_leaked_http_body(self):
        with self.assertRaises(ModelUnavailable):
            RequestyModel('https://other.example/v1', 'model', 'fixture-key')
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 401, 'secret body', {}, None)
        model = RequestyModel('https://router.eu.requesty.ai/v1', 'approved-model', 'fixture-key', opener=Opener())
        with self.assertRaises(ModelUnavailable) as context:
            model.complete('Private content')
        self.assertNotIn('secret body', str(context.exception))
        self.assertNotIn('fixture-key', str(context.exception))
