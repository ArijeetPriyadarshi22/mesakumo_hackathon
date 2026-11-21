import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from mesakumo.domain import Document, EvidenceMatch, Project, Requirement, SourcePassage
from mesakumo.evidence import (
    EVIDENCE_SYSTEM_INSTRUCTION,
    MAX_PROMPT_BYTES,
    EvidenceMatchingError,
    match_requirements,
    resolve_evidence_citations,
)
from mesakumo.models import ModelUnavailable, RequestyModel, TENDER_SYSTEM_INSTRUCTION, configured_model


def scan_result(passages, findings=()):
    return json.dumps({
        'reviewed_source_ids': [p['id'] for p in passages],
        'findings': [{'source_id': source_id, 'quote': quote} for source_id, quote in findings],
    })


def assessment(clause, coverage, source_ids, *, support='', gap=''):
    return dict(clause=clause, coverage=coverage, source_ids=source_ids, support=support, gap=gap)


def prompt_data(prompt):
    return json.loads(prompt.split('\n', 1)[1])


class EvidenceMatchingTests(unittest.TestCase):
    def setUp(self):
        self.project = Project('test', 'Synthetic evidence', False, 'Company evidence matching')
        self.project.documents.extend([
            Document('D-company', 'Project Atlas.pptx', 'knowledge', version='v1'),
            Document('D-tender', 'Tender.pdf', 'tender'),
            Document('D-company', 'Project Atlas.pptx', 'knowledge', version='v2'),
        ])
        self.project.passages.extend([
            SourcePassage('old', 'D-company', 'Slide 2', 'Project Atlas: certified trainers.', 'v1'),
            SourcePassage('tender', 'D-tender', 'Page 4', 'All trainers must be certified.'),
            SourcePassage('workshops', 'D-company', 'Slide 10', 'Project Atlas delivered two workshops.', 'v2'),
            SourcePassage('report', 'D-company', 'Slide 18', 'Project Atlas completed a final report.', 'v2'),
        ])
        self.requirement = Requirement('R1', 'Deliver workshops and a final report.', ['tender'])

    @patch('mesakumo.evidence.MAX_BATCH_PASSAGES', 1)
    def test_three_verdicts_with_joint_evidence_and_current_sources(self):
        partial = Requirement('R2', 'Deliver workshops.', ['tender'], constraints=['Use certified trainers.'])
        absent = Requirement('R3', 'Install solar panels.', ['tender'])
        scans = []

        def reply(prompt):
            data = prompt_data(prompt)
            text = data['requirement']['text']
            if 'passages' in data:
                passage, = data['passages']
                scans.append((text, passage['id']))
                relevant = text != absent.text and (text != partial.text or passage['id'] == 'workshops')
                quotes = [(passage['id'], passage['text'])] if relevant else []
                return scan_result(data['passages'], quotes)

            findings = [item['source_id'] for item in data['findings']]
            workshops = assessment('Deliver workshops.', 'full', ['workshops'], support='Two workshops delivered.')
            if text == self.requirement.text:
                self.assertEqual(findings, ['workshops', 'report'])
                joint = assessment(
                    'Deliver workshops and a final report.', 'full', ['workshops', 'report'],
                    support='Project Atlas delivered the workshops and completed its report.',
                )
                return json.dumps({'clauses': [joint], 'constraints': []})

            self.assertEqual(text, partial.text)
            self.assertEqual(findings, ['workshops'])
            trainers = assessment('Use certified trainers.', 'none', [], gap='Certification is not documented.')
            return json.dumps({'clauses': [workshops], 'constraints': [trainers]})

        before = self.project.to_dict()
        result = match_requirements(self.project, [self.requirement, partial, absent], Mock(complete=reply))
        self.assertEqual([m.requirement_id for m in result], ['R1', 'R2', 'R3'])
        self.assertEqual([m.quality for m in result], ['sufficient', 'partial', 'none'])
        self.assertEqual([m.source_ids for m in result], [['workshops', 'report'], ['workshops'], []])
        self.assertIn('Two workshops delivered.', result[1].limitations)
        self.assertIn('Certification is not documented.', result[1].limitations)
        self.assertEqual(scans, [(r.text, source_id) for r in (self.requirement, partial, absent)
                                 for source_id in ('workshops', 'report')])
        citations = resolve_evidence_citations(self.project, result[0])
        self.assertEqual(
            [(c.document_name, c.document_version, c.location) for c in citations],
            [('Project Atlas.pptx', 'v2', 'Slide 10'), ('Project Atlas.pptx', 'v2', 'Slide 18')],
        )
        self.assertEqual(self.project.to_dict(), before)

    def test_citation_lookup_rejects_superseded_version(self):
        old_match = EvidenceMatch('match', 'R1', ['old'], 'partial', '')
        with self.assertRaisesRegex(EvidenceMatchingError, 'could not be resolved'):
            resolve_evidence_citations(self.project, old_match)

    def test_partial_support_within_one_clause(self):
        scan = scan_result([{'id': 'workshops'}, {'id': 'report'}], [('workshops', 'two workshops')])
        clause = assessment(
            'Deliver three workshops.', 'partial', ['workshops'],
            support='Two workshops documented.', gap='A third workshop is missing.',
        )
        model = Mock()
        model.complete.side_effect = [scan, json.dumps({'clauses': [clause], 'constraints': []})]
        match, = match_requirements(self.project, [Requirement('R4', clause['clause'], [])], model)
        self.assertEqual((match.quality, match.source_ids), ('partial', ['workshops']))
        self.assertIn(clause['gap'], match.limitations)

    def test_rejects_invented_stale_tender_and_unquoted_assessment_citations(self):
        passages = [{'id': 'workshops'}, {'id': 'report'}]
        for source_id in ('invented', 'old', 'tender', 'report'):
            with self.subTest(source_id=source_id):
                model = Mock()
                finding = scan_result(passages, [('workshops', 'two workshops')])
                clause = assessment('Deliver workshops.', 'full', [source_id], support='Workshops delivered.')
                model.complete.side_effect = [finding, json.dumps({'clauses': [clause], 'constraints': []})]
                with self.assertRaisesRegex(EvidenceMatchingError, 'invalid clause or citation'):
                    match_requirements(self.project, [self.requirement], model)

    @patch('mesakumo.evidence.MAX_BATCH_PASSAGES', 1)
    def test_rejects_out_of_batch_source_and_unverified_quotation(self):
        for source_id, quote, error in [
            ('report', 'completed a final report', 'outside its batch'),
            ('workshops', 'three workshops', 'quotation could not be verified'),
        ]:
            with self.subTest(source_id=source_id, error=error):
                model = Mock()
                model.complete.return_value = scan_result([{'id': 'workshops'}], [(source_id, quote)])
                with self.assertRaisesRegex(EvidenceMatchingError, error):
                    match_requirements(self.project, [self.requirement], model)
                self.assertEqual(model.complete.call_count, 1)

    @patch('mesakumo.evidence.MAX_BATCH_PASSAGES', 1)
    def test_incomplete_or_failed_scan_cannot_return_no_evidence(self):
        for invalid, error in [
            (scan_result([]), 'complete batch coverage'),
            ('broken JSON', 'malformed JSON'),
            (ModelUnavailable('fixture detail'), 'scan request failed'),
        ]:
            with self.subTest(error=error):
                model = Mock()
                model.complete.side_effect = [scan_result([{'id': 'workshops'}]), invalid]
                with self.assertRaisesRegex(EvidenceMatchingError, error):
                    match_requirements(self.project, [self.requirement], model)
                self.assertEqual(model.complete.call_count, 2)
                self.assertEqual(self.project.evidence, [])

    def test_rejects_assessment_with_missing_explicit_constraint(self):
        req = Requirement('R5', 'Deliver workshops.', ['tender'], constraints=['Use certified trainers.'])
        model = Mock()
        clause = assessment('Deliver workshops.', 'full', ['workshops'], support='Two workshops delivered.')
        model.complete.side_effect = [
            scan_result([{'id': 'workshops'}, {'id': 'report'}], [('workshops', 'two workshops')]),
            json.dumps({'clauses': [clause], 'constraints': []}),
        ]
        with self.assertRaisesRegex(EvidenceMatchingError, 'each explicit constraint'):
            match_requirements(self.project, [req], model)

    def test_failed_latest_document_blocks_the_scan(self):
        self.project.documents.append(Document(
            'D-company', 'Project Atlas.pptx', 'knowledge', version='v3', error='Extraction failed.',
        ))
        model = Mock()
        with self.assertRaisesRegex(EvidenceMatchingError, 'unreadable current company documents'):
            match_requirements(self.project, [self.requirement], model)
        model.complete.assert_not_called()

    def test_batch_limit_counts_utf8_bytes_and_preserves_all_text(self):
        first, second = self.project.passages[-2:]
        first.excerpt = 'ü' * 13_000
        second.excerpt = 'ä' * 13_000
        seen = []

        def reply(prompt):
            self.assertLessEqual(len(prompt.encode('utf-8')), MAX_PROMPT_BYTES)
            passages = prompt_data(prompt)['passages']
            seen.extend((p['id'], p['text']) for p in passages)
            return scan_result(passages)

        model = Mock()
        model.complete.side_effect = reply
        match, = match_requirements(self.project, [self.requirement], model)
        self.assertEqual(model.complete.call_count, 2)
        self.assertEqual(seen, [(p.id, p.excerpt) for p in (first, second)])
        self.assertEqual(match.quality, 'none')

    def test_oversized_passage_fails_without_truncating(self):
        self.project.passages[-2].excerpt = 'ü' * MAX_PROMPT_BYTES
        model = Mock()
        with self.assertRaisesRegex(EvidenceMatchingError, 'exceed the matching input limit'):
            match_requirements(self.project, [self.requirement], model)
        model.complete.assert_not_called()

    def test_malformed_provider_envelope_is_a_matching_error(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"choices":[null]}')
        model = RequestyModel('https://router.eu.requesty.ai/v1', 'fixture', 'fixture-key', opener=opener)
        with self.assertRaisesRegex(EvidenceMatchingError, 'request failed'):
            match_requirements(self.project, [self.requirement], model)

    def test_requesty_evidence_instruction_keeps_tender_default(self):
        requests = []

        def reply(request, timeout):
            self.assertEqual(request.full_url, 'https://router.eu.requesty.ai/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer fixture-key')
            payload = json.loads(request.data)
            self.assertEqual(payload['model'], 'fixture-approved-claude')
            requests.append(payload['messages'])
            messages = payload['messages']
            content = ('{}' if messages[0]['content'] == TENDER_SYSTEM_INSTRUCTION
                       else scan_result(prompt_data(messages[1]['content'])['passages']))
            return io.BytesIO(json.dumps({
                'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            }).encode())

        env = {
            'MESAKUMO_LLM_PROVIDER': 'requesty',
            'MESAKUMO_LLM_APPROVED': 'yes',
            'MESAKUMO_LLM_BASE_URL': 'https://router.eu.requesty.ai/v1',
            'MESAKUMO_LLM_MODEL': 'fixture-approved-claude',
            'MESAKUMO_LLM_API_KEY': 'fixture-key',
        }
        with patch.dict(os.environ, env, clear=True), patch('mesakumo.models.build_opener') as opener:
            opener.return_value.open.side_effect = reply
            match, = match_requirements(self.project, [self.requirement])
            self.assertEqual(match.quality, 'none')
            self.assertEqual(configured_model().complete('Synthetic tender request'), '{}')
        self.assertEqual([m[0]['content'] for m in requests],
                         [EVIDENCE_SYSTEM_INSTRUCTION, TENDER_SYSTEM_INSTRUCTION])
