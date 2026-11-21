import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from mesakumo.domain import Project, Requirement
from mesakumo.evidence_workflow import INVENTORY_FIELDS, reviewed_handoff
from mesakumo.persistence import ProjectStore
from mesakumo.ui.evidence_review import EvidenceReviewStore, EvidenceSettings
from mesakumo.ui.server import serve


class LocalModel:
    def __init__(self):
        self.requests = []

    def complete(self, prompt):
        data = json.loads(prompt.split('\n', 1)[1])
        req = data['requirement']
        self.requests.append(req['text'])
        if 'passages' in data:
            return json.dumps({
                'reviewed_source_ids': [p['id'] for p in data['passages']],
                'findings': [{'source_id': p['id'], 'quote': 'Zwei Workshops'}
                             for p in data['passages']
                             if 'Workshop' in req['text'] and 'Zwei Workshops' in p['text']],
            })
        return json.dumps({
            'clauses': [{'clause': req['text'], 'coverage': 'full',
                         'source_ids': [p['source_id'] for p in data['findings']],
                         'support': 'Two workshops are documented.', 'gap': ''}],
            'constraints': [{'clause': text, 'coverage': 'none', 'source_ids': [], 'support': '',
                             'gap': 'Trainer qualification is missing.'} for text in req['constraints']],
        })


class TestSocket:
    def __init__(self, request):
        self.request, self.output = io.BytesIO(request), io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.request

    def sendall(self, value):
        self.output.write(value)


class EvidencePageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.store = ProjectStore(root / 'workspace.sqlite3')
        self.project = Project('tender', 'Example procurement', False, 'Training tender', requirements=[
            Requirement('REQ-1', 'Zwei Workshops', ['buyer'], reviewed=True,
                        constraints=['Zertifizierte Trainer'], provenance='Buyer p. 2'),
            Requirement('REQ-2', 'PV-Installation', ['buyer'], reviewed=True),
        ])
        self.store.create(self.project)
        self.participant = root / 'local'
        self.participant.mkdir()
        (self.participant / 'reference.txt').write_text('Zwei Workshops durchgeführt.')
        (self.participant / 'profile.txt').write_text('Trainerausbildung dokumentiert.')
        inventory = root / 'inventory.csv'
        with inventory.open('w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(INVENTORY_FIELDS)
            writer.writerows([
                ('Previous project sources', 'File', 'reference.txt'),
                ('Team information', 'File', 'profile.txt'),
                ('Historical tender input', 'File', 'buyers-request.txt'),
            ])
        self.settings = EvidenceSettings(inventory, self.participant)
        self.model = LocalModel()

    def run_page(self, exercise):
        outer = self

        class Server:
            server_port = 8501

            def __init__(self, address, handler):
                self.handler = handler

            def server_close(self):
                pass

            def request(self, path='/evidence?project=tender', fields=None, checklist=None):
                payload, encoding = b'', 'application/x-www-form-urlencoded'
                if fields is not None:
                    payload = urlencode(fields, doseq=True).encode()
                if checklist is not None:
                    encoding = 'multipart/form-data; boundary=checklist-boundary'
                    chunks = [f'--checklist-boundary\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                              for key, value in fields.items()]
                    chunks.append(b'--checklist-boundary\r\nContent-Disposition: form-data; name="file"; filename="approved.csv"\r\n'
                                  b'Content-Type: text/csv\r\n\r\n' + checklist + b'\r\n--checklist-boundary--\r\n')
                    payload = b''.join(chunks)
                method = 'POST' if fields is not None else 'GET'
                request = (f'{method} {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n'
                           f'Content-Type: {encoding}\r\nContent-Length: {len(payload)}\r\n\r\n').encode() + payload
                socket = TestSocket(request)
                self.handler(socket, ('127.0.0.1', 1234), self)
                return socket.output.getvalue()

            def serve_forever(self):
                page = self.request().decode()
                outer.assertIn('Evidence Matching', page)
                outer.assertNotRegex(page, r'name="roles"[^>]*checked')
                token = re.search(r'name="token" value="([^"]+)"', page)[1]

                def submit(action, revision, checklist=None, **values):
                    return self.request('/evidence', {'action': action, 'revision': revision, 'token': token,
                                                      'project': 'tender', **values}, checklist)

                exercise(self.request, submit)

        with patch('mesakumo.ui.server.ThreadingHTTPServer', Server):
            serve(self.store, evidence_settings=self.settings, evidence_model_factory=lambda: self.model)

    def test_match_review_add_remove_and_csv_download(self):
        def exercise(request, submit):
            self.assertIn(b'303 See Other', submit('match', 0, roles='Team information'))
            page = request().decode()
            self.assertIn('Suggested: <strong>Partial</strong>', page)
            self.assertIn('Suggested: <strong>Gap</strong>', page)
            self.assertIn('reference.txt · Lines 1–1', page)
            reviews = EvidenceReviewStore(self.store)
            revision, report = reviews.load('tender')
            profile = next(source for source in report['source_reading'] if source['path'] == 'profile.txt')
            self.assertIn(b'400 Bad Request', submit('save', revision, target='REQ-1', quality='partial',
                                                     citations='invented'))
            self.assertIn(b'303 See Other', submit(
                'save', revision, target='REQ-1', quality='partial', comment='=1+1',
                document=profile['document_id'], location='Lines 1–1', quote='Trainerausbildung dokumentiert.',
            ))
            self.assertIn(b'400 Bad Request', submit('exclude', revision, target='REQ-2'))
            self.assertIn(b'303 See Other', submit('exclude', revision + 1, target='REQ-2'))
            requests_before = len(self.model.requests)
            self.assertIn(b'303 See Other', submit('add_requirement', revision + 2, text='Workshop delivery'))
            self.assertTrue(all(req == 'Workshop delivery' for req in self.model.requests[requests_before:]))
            saved = request('/evidence?project=tender&download=csv')
            headers, content = saved.split(b'\r\n\r\n', 1)
            self.assertIn(b'attachment; filename="task2-evidence.csv"', headers)
            rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[0]['tender_source_ids']), ['buyer'])
            self.assertEqual(rows[0]['category'], 'Partial')
            self.assertEqual(rows[0]['reviewer_comment'], "'=1+1")
            self.assertIn('profile.txt · Lines 1–1', rows[0]['reference'])
            self.assertNotIn('reference.txt', rows[0]['reference'])
            self.assertEqual(rows[0]['match'], 'Trainerausbildung dokumentiert.')
            self.assertEqual(rows[1]['category'], 'Strong')
            self.assertEqual(self.store.load('tender').requirements, self.project.requirements)
            self.assertIn('Saved verdict', request().decode())
            (self.participant / 'profile.txt').write_text('Updated source.')
            self.assertIn(b'rerun', request('/evidence?project=tender&download=csv').lower())

        self.run_page(exercise)

    def test_csv_intake_and_incomplete_search_do_not_create_false_gap(self):
        with self.settings.inventory.open('a', encoding='utf-8') as file:
            file.write('Historical proposal,File,missing-historical.pdf\n')
        checklist = ('requirement_id,requirement_text,approval\n'
                     'CSV-1,Workshop reference,approved\n'
                     'CSV-2,Unproved work,approved\n'
                     'CSV-3,Not approved,draft\n').encode()

        def exercise(request, submit):
            self.assertIn(b'303 See Other', submit('match', 0, checklist=checklist, roles='Historical proposal'))
            page = request().decode()
            self.assertIn('Search incomplete', page)
            self.assertIn('Provisional: <strong>Strong</strong>', page)
            self.assertNotIn('<strong>Gap</strong>', page)
            self.assertIn('missing-historical.pdf', page)
            self.assertIn('2 have an incomplete search', page)
            self.assertNotIn('CSV-3', page)
            self.assertIn('Unapproved checklist rows skipped: 1', page)
            self.assertIn(b'303 See Other', submit('save', 1, target='CSV-2', comment='Check the missing file.'))
            _, content = request('/evidence?project=tender&download=csv').split(b'\r\n\r\n', 1)
            rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            self.assertEqual({row['category'] for row in rows}, {'Incomplete'})
            self.assertEqual(rows[1]['reviewer_comment'], 'Check the missing file.')
            self.assertEqual(rows[1]['review_state'], 'Provisional')

        self.run_page(exercise)

    def test_restoring_excluded_row_after_sources_change_requires_rerun(self):
        def exercise(request, submit):
            self.assertIn(b'303', submit('match', 0))
            self.assertIn(b'303', submit('save', 1, target='REQ-2', quality='none'))
            self.assertIn(b'303', submit('exclude', 2, target='REQ-2'))
            (self.participant / 'reference.txt').write_text('Changed reference.')
            self.assertIn(b'303', submit('rerun', 3))
            self.assertIn(b'303', submit('restore', 4, target='REQ-2'))
            _, report = EvidenceReviewStore(self.store).load('tender')
            self.assertEqual(report['results'][1]['search_state'], 'incomplete')
            self.assertFalse(reviewed_handoff(report)['reviewed'])

        self.run_page(exercise)

    def test_shortlist_only_review_can_request_full_scan_before_downgrade(self):
        with self.settings.inventory.open('a', newline='') as inventory:
            writer = csv.writer(inventory)
            for number in range(16):
                filename = f'notes-{number}.txt'
                (self.participant / filename).write_text(f'Unrelated inventory {number}.')
                writer.writerow(('Previous project sources', 'File', filename))
        checklist = b'requirement_id,requirement_text,approval\nR,Workshops,approved\n'

        def exercise(request, submit):
            self.assertIn(b'303', submit('match', 0, checklist=checklist))
            self.assertIn(b'retrieved candidates', request())
            self.assertIn(b'400', submit('save', 1, target='R', quality='none'))
            self.assertIn(b'303', submit('rerun', 1, full_scan='yes'))
            self.assertIn(b'303', submit('save', 2, target='R', quality='none', comment='Reviewed all sources.'))
            self.assertIn(b'303', submit('rerun', 3))
            report = EvidenceReviewStore(self.store).load('tender')[1]
            self.assertEqual(report['results'][0]['evaluated_count'], 17)
            self.assertEqual(reviewed_handoff(report)['reviewed'][0]['quality'], 'none')

        self.run_page(exercise)

    def test_missing_setup_and_missing_checklist_explain_next_action(self):
        self.project.requirements = []
        self.store.save(self.project, self.project.revision)
        self.settings = EvidenceSettings(self.participant / 'missing.csv', self.participant)

        def exercise(request, submit):
            page = request().decode()
            self.assertIn('no fully approved checklist', page)
            self.assertIn('MESAKUMO_DOCUMENT_INVENTORY', page)
            self.assertIn('MESAKUMO_PARTICIPANT_ROOT', page)
            self.assertIn('TASK2_README.md', page)
            self.assertIn('disabled>Run evidence matching', page)
            self.assertNotIn('Download saved report (CSV)', page)
            self.assertIn(b'Choose the approved Task 1 CSV', submit('match', 0))
            self.assertFalse(self.model.requests)

        self.run_page(exercise)

    def test_approved_csv_to_reviewed_download(self):
        checklist = ('requirement_id,requirement_text,approval,tender_source\n'
                     'R-W,Workshops durchführen,approved,Buyer item 4\n'
                     'R-X,Unrelated qualification,approved,Buyer item 5\n').encode()

        def exercise(request, submit):
            page = request().decode()
            self.assertIn('2 approved requirements', page)
            for step in ('Choose requirements', 'Choose sources and run', 'Review evidence and gaps', 'Save and download'):
                self.assertIn(step, page)
            self.assertIn(b'303', submit('match', 0, checklist=checklist))
            page = request().decode()
            self.assertIn('approved.csv', page)
            self.assertIn('2 need a saved review', page)
            self.assertIn('Buyer reference: Buyer item 4', page)
            self.assertIn('reference.txt · Lines 1–1', page)
            self.assertLess(page.index('Save review</button>'), page.index('Download saved report (CSV)'))
            _, report = EvidenceReviewStore(self.store).load('tender')
            citation, = report['results'][0]['suggested']['source_ids']
            self.assertIn(b'303', submit('save', 1, target='R-W', quality='sufficient',
                                         citations=citation, comment='Verified against the original.'))
            self.assertIn(b'303', submit('save', 2, target='R-X', quality='none'))
            self.assertIn('0 need a saved review', request().decode())
            headers, content = request('/evidence?project=tender&download=csv').split(b'\r\n\r\n', 1)
            self.assertIn(b'attachment;', headers)
            strong, gap = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
            self.assertEqual((strong['category'], strong['review_state']), ('Strong', 'Reviewed'))
            self.assertEqual(strong['reviewer_comment'], 'Verified against the original.')
            self.assertIn('reference.txt · Lines 1–1', strong['reference'])
            self.assertEqual((gap['category'], gap['reference']), ('Gap', ''))

        self.run_page(exercise)
