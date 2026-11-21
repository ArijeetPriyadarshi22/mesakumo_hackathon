import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mesakumo.evidence import EvidenceSource, read_evidence
from mesakumo.evidence_matching import MAX_ACTIONS, match_evidence
from mesakumo.evidence_review import coverage_issue
from mesakumo.models import ModelUnavailable


def action(tool, **args):
    return json.dumps(dict(tool=tool, args=args))


def submit(fit, ids=(), gap=''):
    return action('submit', fit=fit, passage_ids=list(ids), gap=gap)


def scan_response(prompt, findings=()):
    passages = json.loads(prompt.split('\n', 1)[1])['passages']
    return json.dumps({
        'reviewed_source_ids': [passage['passage_id'] for passage in passages],
        'findings': [{'passage_id': pid, 'quote': quote} for pid, quote in findings],
    })


def assess_response(clauses, constraints=()):
    def checks(items):
        return [dict(clause=clause, coverage=coverage, passage_ids=list(ids), gap=gap)
                for clause, coverage, ids, gap in items]
    return json.dumps(dict(clauses=checks(clauses), constraints=checks(constraints)))


class Scripted:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response(prompt) if callable(response) else response


def req(rid, text, constraints=(), kind='eligibility/evidence'):
    return dict(id=rid, text=text, constraints=list(constraints), kind=kind,
                source_ids=['tender:p1'], original_excerpts=['Original tender wording.'])


class MatchingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        files = {
            'references.txt': ('Previous project sources',
                'Acme delivered the Aurora accessibility audit in 2024.\n' + '\n' * 19
                + 'Aurora was accepted by North County.\n'),
            'team.txt': ('Team information', 'Morgan completed Falcon certification in 2021.'),
            'proposal.txt': ('Historical proposal', 'Morgan was proposed to deliver the Aurora audit and sign a declaration.'),
            'unselected.txt': ('Previous project sources', 'Unselected secret reference for the current bid.'),
            'broken.pdf': ('Previous project sources', 'not a PDF'),
        }
        self.inventory = {}
        for name, (role, content) in files.items():
            (self.folder / name).write_text(content)
            self.inventory[name] = EvidenceSource(name, name, role)

    def readings(self, *names):
        return read_evidence(self.folder, self.inventory, names)

    def run_match(self, requirements, readings, responses):
        model = Scripted(responses)
        factories = []
        progress = []

        def factory():
            factories.append(model)
            return model

        run = match_evidence({'project_id':'tender-2','revision':12,'rows':requirements},
                             'offer', readings, factory, lambda done,total: progress.append((done,total)))
        self.assertEqual([row.requirement['id'] for row in run.rows], [row['id'] for row in requirements])
        self.assertEqual(len(factories), int(any(r.passages for r in readings)))
        self.assertEqual((run.project_id,run.phase,run.revision), ('tender-2','offer',12))
        return run, model, progress

    def test_grades_search_nearby_and_original_citations(self):
        readings = self.readings('references.txt', 'team.txt')
        first, second = [p.id for p in readings[0].passages]
        qualification = readings[1].passages[0].id
        requirements = [
            req('R1','Audit delivery and customer acceptance.', ['Aurora', '2024']),
            req('R2','Lead must hold active credentials.', ['Morgan']),
            req('R3','Sign the current bid declaration.', kind='submission instruction'),
        ]
        run, model, progress = self.run_match(requirements, readings, [
            action('read_nearby', passage_id=first), submit('Strong match', [first, second]),
            action('search', query='Falcon certification 2021'), submit('Partial match', [qualification], 'Current validity unconfirmed.'),
            lambda prompt: scan_response(prompt, [(qualification, 'Morgan completed Falcon certification in 2021.')]),
            assess_response([('Active credentials', 'partial', [qualification], 'Current validity unconfirmed.')],
                            [('Morgan', 'full', [qualification], '')]),
            submit('No match', gap='A new signature and declaration are required.'),
        ])
        self.assertEqual(run.status, 'Complete')
        self.assertEqual([row.fit for row in run.rows], ['Strong match', 'Partial match', 'No match'])
        self.assertEqual(run.rows[0].requirement, requirements[0])
        self.assertEqual(run.rows[0].evidence[1]['excerpt'], 'Aurora was accepted by North County.')
        self.assertEqual(run.rows[0].evidence[1]['filename'], 'references.txt')
        self.assertEqual(run.rows[0].evidence[1]['location'], 'Lines 21–21')
        self.assertEqual(run.rows[0].evidence[1]['document_version'], run.documents[0]['version'])
        self.assertEqual(run.rows[1].trace[1]['tool'], 'search')
        self.assertIn('Falcon certification', model.prompts[3])
        self.assertEqual(run.rows[2].evidence, [])
        self.assertTrue(run.rows[2].current_action)
        self.assertEqual([row.search_scope for row in run.rows],
                         ['retrieval', 'full_readable_scan', 'current_action'])
        self.assertEqual(len(model.prompts), 7)
        self.assertEqual(progress[-1], (3,3))

    def test_unknown_unselected_and_unretrieved_citations_are_rejected(self):
        readings = self.readings('references.txt', 'team.txt')
        team = readings[1].passages[0].id
        foreign = self.readings('unselected.txt')[0].passages[0].id
        run, model, _ = self.run_match(
            [req('R1','Aurora 2024'), req('R2','Aurora 2024'), req('R3','Aurora 2024')], readings,
            [submit('Strong match', ['invented']), submit('Strong match', [foreign]), submit('Strong match', [team])])
        self.assertEqual(run.status, 'Failed')
        self.assertTrue(all(row.status == 'Failed' and not row.fit and not row.evidence for row in run.rows))
        self.assertTrue(all('not retrieved' in row.error for row in run.rows))
        self.assertTrue(all('Unselected secret' not in prompt and 'Falcon certification' not in prompt
                            for prompt in model.prompts))

    def test_unreadable_coverage_and_provider_failure_are_not_no_match(self):
        readings = self.readings('references.txt', 'broken.pdf')
        run, _, _ = self.run_match([req('R1','Unknown criterion')], readings,
                                   [submit('No match', gap='No supporting passage in readable files.')])
        self.assertEqual(run.status,'Coverage gaps')
        self.assertEqual(run.documents[1]['status'],'Unreadable')
        self.assertTrue(run.documents[1]['version'])
        self.assertEqual(run.rows[0].status, 'Incomplete')
        self.assertFalse(run.rows[0].fit)
        run, model, _ = self.run_match([req('R1','Aurora'), req('R2','Aurora')], readings,
                                       [ModelUnavailable('Synthetic provider outage')])
        self.assertEqual(run.status,'Failed')
        self.assertEqual([row.status for row in run.rows], ['Failed','Unprocessed'])
        self.assertTrue(all(not row.fit for row in run.rows))
        self.assertEqual(len(model.prompts),1)

        run, model, _ = self.run_match([req('R1','Aurora')], self.readings('broken.pdf'), [])
        self.assertEqual(run.rows[0].status,'Unprocessed')
        self.assertEqual(model.prompts,[])

    def test_historical_bid_cannot_establish_current_or_delivered_claim(self):
        readings = self.readings('proposal.txt')
        pid = readings[0].passages[0].id
        run, _, _ = self.run_match([req('R1','Aurora delivered'), req('R2','Sign declaration')],
                                   readings, [
            submit('Strong match', [pid]),
            lambda prompt: scan_response(prompt, [(pid, readings[0].passages[0].excerpt)]),
            assess_response([('Aurora delivered', 'full', [pid], '')]),
            submit('Strong match', [pid]),
        ])
        self.assertEqual([row.fit for row in run.rows], ['Partial match', 'No match'])
        self.assertIn('actual delivery', run.rows[0].gap)
        self.assertTrue(run.rows[1].current_action)
        self.assertEqual(run.rows[1].evidence, [])

    def test_followup_bound_is_an_execution_gap(self):
        run, model, _ = self.run_match([req('R1','Aurora')], self.readings('references.txt'),
                                      [action('search',query='Aurora')]*MAX_ACTIONS)
        self.assertEqual(len(model.prompts),MAX_ACTIONS)
        self.assertEqual(run.rows[0].status,'Failed')
        self.assertIn('limit reached',run.rows[0].error)


    def test_confirmation_recovers_joint_support_and_checks_long_passage(self):
        path = self.folder / 'long.txt'
        path.write_text('Aurora ' + ('background ' * 175) + 'Lead holds Falcon certification.')
        self.inventory[path.name] = EvidenceSource(path.name, path.name, 'Team information')
        readings = self.readings('references.txt', path.name)
        first = readings[0].passages[0].id
        qualification = readings[1].passages[0].id
        target = req('R1', 'Provide proof of Aurora delivery and an accredited lead.')

        def certification_response(prompt):
            self.assertIn('Lead holds Falcon certification.', prompt)
            return scan_response(prompt, [(qualification, 'Lead holds Falcon certification.')])

        with patch('mesakumo.evidence_confirmation.MAX_BATCH_PASSAGES', 1):
            run, model, _ = self.run_match([target], readings, [
                submit('Partial match', [first], 'Accreditation missing.'),
                lambda prompt: scan_response(prompt, [(first, readings[0].passages[0].excerpt)]),
                scan_response,
                certification_response,
                assess_response([('Aurora delivery', 'full', [first], ''),
                                 ('Accredited lead', 'full', [qualification], '')]),
            ])
        row, = run.rows
        self.assertNotIn('Lead holds Falcon certification.', model.prompts[0])
        self.assertEqual((row.fit, row.search_scope), ('Strong match', 'full_readable_scan'))
        self.assertEqual([citation['passage_id'] for citation in row.evidence], [first, qualification])

    def test_confirmation_rejects_bad_quotes_and_missing_coverage(self):
        readings = self.readings('references.txt')
        first = readings[0].passages[0].id
        for confirmation, error in [
            (lambda prompt: scan_response(prompt, [(first, 'Invented quote')]), 'quote outside'),
            (json.dumps(dict(reviewed_source_ids=[], findings=[])), 'every passage'),
        ]:
            with self.subTest(error=error):
                run, _, _ = self.run_match([req('R1', 'Unknown')], readings, [
                    submit('No match', gap='Initial retrieval found none.'), confirmation,
                ])
                row, = run.rows
                self.assertEqual((row.status, row.search_scope, row.fit), ('Incomplete', 'incomplete', ''))
                self.assertIn(error, row.error)

    def test_confirmation_outage_stops_remaining_rows(self):
        run, model, _ = self.run_match([req('R1','Aurora'), req('R2','Aurora')],
                                       self.readings('references.txt'), [
            submit('No match', gap='Initial retrieval found none.'), ModelUnavailable('Synthetic outage'),
        ])
        self.assertEqual([row.status for row in run.rows], ['Incomplete', 'Unprocessed'])
        self.assertTrue(all(not row.fit for row in run.rows))
        self.assertEqual(len(model.prompts), 2)

    def test_approval_requires_confirmed_coverage_even_after_manual_review(self):
        row = {'status': 'Reviewed', 'fit': 'No match'}
        draft = {'documents': [{'status': 'Read'}], 'rows': [row]}
        self.assertIn('Confirm', coverage_issue(draft))
        for scope in ('retrieval', 'incomplete'):
            row['search_scope'] = scope
            self.assertTrue(coverage_issue(draft))
        row['search_scope'] = 'full_readable_scan'
        self.assertEqual(coverage_issue(draft), '')
        draft['documents'][0]['status'] = 'Unreadable'
        self.assertIn('unreadable', coverage_issue(draft))
