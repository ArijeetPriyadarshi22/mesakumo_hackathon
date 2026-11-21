import csv
import io
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from mesakumo.checklist import ChecklistReview
from mesakumo.discovery import ROOT, open_selection, participant_directory
from mesakumo.evidence import EVIDENCE_ROLES, evidence_inventory, read_evidence
from mesakumo.persistence import ProjectStore
from mesakumo.models import ModelUnavailable, RequestyModel
from mesakumo.evidence_review import COLUMNS, EvidenceReview

MAIN_DECK = 'Input Angebote_Referenzen und Beraterprofile.pptx'


def write_deck(path):
    """Use reordered slide parts and a blank first slide."""
    p = 'http://schemas.openxmlformats.org/presentationml/2006/main'
    a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('ppt/presentation.xml',
            f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst>'
            '<p:sldId r:id="blank"/><p:sldId r:id="text"/></p:sldIdLst></p:presentation>')
        archive.writestr('ppt/_rels/presentation.xml.rels',
            f'<Relationships><Relationship Id="text" Type="{r}/slide" Target="slides/slide1.xml"/>'
            f'<Relationship Id="blank" Type="{r}/slide" Target="slides/slide2.xml"/></Relationships>')
        archive.writestr('ppt/slides/slide1.xml',
            f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><a:p><a:r><a:t>Project evidence</a:t></a:r></a:p></p:sld>')
        archive.writestr('ppt/slides/slide2.xml', f'<p:sld xmlns:p="{p}"/>')


class EvidenceTests(unittest.TestCase):
    def test_inventory_excludes_buyer_inputs_and_preserves_historical_roles(self):
        inventory = evidence_inventory()
        self.assertEqual({source.role for source in inventory.values()}, set(EVIDENCE_ROLES))
        self.assertEqual(inventory[MAIN_DECK].role, 'Previous project sources')
        self.assertFalse(any(path.startswith(('Ausschreibung', '99 LLM keys')) or
                             'Ausschreibungsunterlagen' in path or path == 'Angebotstemplate.pptx'
                             for path in inventory))
        completed = [item for item in inventory.values() if item.filename == 'Leistungsverzeichnis.xlsx']
        self.assertEqual([item.role for item in completed], ['Historical proposal'])

    def test_unicode_selected_only_and_file_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inventory_path = root / 'inventory.csv'
            selected = ['Änhänge/Deck.pptx', 'missing.txt', 'notes.bin', 'broken.pptx', 'linked.txt']
            with inventory_path.open('w', encoding='utf-8', newline='') as file:
                writer = csv.writer(file)
                writer.writerow(['Primary role', 'Location type', 'Filename', 'Path relative to participant input folder'])
                for name in selected + ['unselected.txt', '../escape.txt']:
                    writer.writerow(['Historical proposal', 'File', Path(name).name, name])
            inventory = evidence_inventory(inventory_path)
            self.assertNotIn('../escape.txt', inventory)
            folder = root / unicodedata.normalize('NFD', 'Änhänge')
            folder.mkdir()
            write_deck(folder / 'Deck.pptx')
            (root / 'notes.bin').write_bytes(b'unsupported')
            (root / 'broken.pptx').write_bytes(b'corrupt')
            (root / 'unselected.txt').write_text('Never ingested')
            (root / 'linked.txt').symlink_to(root / 'unselected.txt')
            readings = read_evidence(root, inventory, selected)
            self.assertEqual([r.status for r in readings], ['Read', 'Missing', 'Unsupported', 'Unreadable', 'Unreadable'])
            self.assertEqual([r.source.relative_path for r in readings], selected)
            self.assertEqual(readings[0].source.role, 'Historical proposal')
            self.assertEqual([(p.location, p.excerpt) for p in readings[0].passages],
                             [('Slide 2', 'Project evidence')])
            self.assertFalse(any(r.passages for r in readings[1:]))

    def test_main_reference_deck_slide_citations(self):
        directory = participant_directory()
        if not (directory / MAIN_DECK).is_file():
            self.skipTest('Participant reference deck is unavailable')
        reading, = read_evidence(directory, evidence_inventory(), [MAIN_DECK])
        self.assertEqual(reading.status, 'Read', reading.detail)
        citations = {p.location: p.excerpt for p in reading.passages}
        for number in (3, 15, 65):
            self.assertTrue(citations[f'Slide {number}'].strip())

    def test_streamlit_approved_offer_handoff_selection_and_stale_gate(self):
        from streamlit.testing.v1 import AppTest
        (ROOT / 'data').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory(dir=ROOT / 'data') as data:
            directory = Path(tmp)
            pack = directory / 'Ausschreibung 3'
            pack.mkdir()
            (pack / 'spec.txt').write_text('Submit three references.')
            write_deck(directory / MAIN_DECK)
            store = ProjectStore(Path(data) / 'workspace.sqlite3')
            project, rows = open_selection(store, pack, 'offer')
            passage = project.passages[0]
            review = ChecklistReview(store)
            requirement = review.edit(project.id, project.revision, rows, '', 'Submit three references.',
                'eligibility/evidence', 'Mandatory', [passage.id], ['three'], 'Reviewer', 'Checked the source')

            class FailingModel:
                def complete(self, prompt):
                    raise ModelUnavailable('Synthetic provider failure')

            class WorkingModel:
                def complete(self, prompt):
                    import json
                    details = json.loads(prompt.rsplit('\n', 1)[1])
                    if 'passages' in details:
                        return json.dumps({'reviewed_source_ids': [p['passage_id'] for p in details['passages']],
                                           'findings': []})
                    return ('{"tool":"submit","args":{"fit":"No match","passage_ids":[],'
                            '"gap":"No suitable reference was found."}}')

            with patch.dict('os.environ', {'MESAKUMO_PARTICIPANT_DIR': tmp, 'MESAKUMO_DATA_DIR': data,
                    'MESAKUMO_KEEP_SESSION': 'yes', 'MESAKUMO_LLM_PROVIDER': 'disabled'}), patch(
                    'mesakumo.ui.evidence.configured_model', side_effect=[FailingModel(), WorkingModel()]) as factory:
                def widget(kind, label):
                    return next(w for w in getattr(app, kind) if w.label == label)

                app = AppTest.from_file('mesakumo/streamlit_app.py').run()
                app.sidebar.radio[0].set_value('Evidence Matching').run()
                self.assertFalse(app.multiselect)
                self.assertTrue(any('locked' in item.value for item in app.warning))
                app.sidebar.radio[0].set_value('Tender Requirements').run()
                widget('selectbox', 'Phase').select('offer').run()
                widget('text_input', 'Approving reviewer').set_value('Reviewer')
                app.checkbox[0].check()
                widget('button', 'Approve and Save').click().run()
                approved = review.current(store.load(project.id), rows)
                csv_row, = csv.DictReader(io.StringIO(approved['csv'].decode()))
                self.assertEqual((csv_row['project_id'], csv_row['phase'], csv_row['requirement_id'],
                                  int(csv_row['approved_revision'])),
                                 (project.id, 'offer', requirement, approved['revision']))
                self.assertTrue(app.get('download_button'))
                widget('button', 'Continue to Evidence Matching').click().run()
                self.assertFalse(app.exception)
                self.assertIn(requirement, app.dataframe[0].value['Requirement ID'].tolist())
                self.assertIn(project.id, ' '.join(item.value for item in app.caption))
                self.assertTrue(any('Phase offer' in item.value for item in app.caption))
                self.assertTrue(any(f"revision {approved['revision']}" in item.value for item in app.success))

                widget('button', 'Select all eligible').click().run()
                self.assertEqual(set(app.multiselect[0].value), set(evidence_inventory()))
                widget('button', 'Clear selection').click().run()
                self.assertEqual(app.multiselect[0].value, [])
                self.assertTrue(widget('button', 'Start Evidence Matching').disabled)
                app.multiselect[0].set_value([MAIN_DECK]).run()
                factory.assert_not_called()
                widget('button', 'Start Evidence Matching').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.dataframe[1].value['Reading status'].tolist(), ['Read'])
                self.assertEqual(app.dataframe[2].value['Status'].tolist(), ['Failed'])
                self.assertEqual(app.dataframe[2].value['Evidence fit'].tolist(), [''])
                self.assertTrue(widget('button', 'Approve and Save Evidence').disabled)
                self.assertEqual(factory.call_count, 1)
                widget('button', 'Retry Evidence Matching').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.dataframe[2].value['Evidence fit'].tolist(), ['No match'])
                self.assertEqual(factory.call_count, 2)
                self.assertEqual(widget('selectbox', 'Source location').options, ['Slide 2'])
                self.assertIn('Project evidence', [item.value for item in app.text])
                self.assertIn('Submit three references.', [item.value for item in app.text])
                service = EvidenceReview(store, approved)
                draft = service.load()
                pid, = draft['corpus']
                widget('selectbox', 'Evidence fit').select('Strong match')
                widget('button', 'Save evidence review').click().run()
                self.assertTrue(any('require an evidence citation' in e.value for e in app.error))
                widget('multiselect', 'Evidence citations').set_value([pid])
                widget('text_area', 'Evidence notes').set_value('Consultant reference checked – résumé')
                widget('text_area', 'Gap / next action').set_value('Confirm availability')
                widget('selectbox', 'Evidence fit').select('Partial match')
                widget('button', 'Save evidence review').click().run()
                self.assertFalse(app.exception)
                stored_table = app.dataframe[2].value
                self.assertEqual(stored_table['Evidence fit'].tolist(), ['Partial match'])

                from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
                original_load = MemoryMediaFileStorage.load_and_get_id
                widget('text_input', 'Evidence reviewer').set_value('Reviewer')
                with patch.object(MemoryMediaFileStorage, 'load_and_get_id', autospec=True,
                                  side_effect=original_load) as media:
                    widget('button', 'Approve and Save Evidence').click().run()
                self.assertFalse(app.exception)
                current = service.current(directory, evidence_inventory())
                download, = app.get('download_button')
                storage = media.call_args.args[0]
                downloaded = storage.get_file(download.proto.url.rsplit('/', 1)[1]).content
                self.assertEqual(downloaded, current['csv'])
                exported, = csv.DictReader(io.StringIO(downloaded.decode('utf-8')))
                self.assertEqual(exported['Requirement ID'], requirement)
                self.assertEqual(exported['Checklist revision'], str(approved['revision']))
                for field in [*COLUMNS, 'Gap / next action']:
                    self.assertEqual(exported[field], stored_table.iloc[0][field])
                import json
                tender_source, = json.loads(exported['Tender citations'])
                evidence_source, = json.loads(exported['Evidence citations'])
                self.assertEqual(tender_source['document_id'], passage.document_id)
                self.assertEqual(tender_source['filename'], 'spec.txt')
                self.assertEqual(evidence_source['document_id'], draft['corpus'][pid]['document_id'])
                self.assertEqual(evidence_source['location'], 'Slide 2')
                self.assertEqual(review.current(store.load(project.id), rows)['csv'], approved['csv'])
                import httpx
                from mesakumo.proposal import DRAFTING_SYSTEM, SCHEMA

                drafting_calls = []

                def draft_response(request):
                    body = json.loads(request.content)
                    drafting_calls.append(body)
                    self.assertEqual(body['messages'][0]['content'], DRAFTING_SYSTEM)
                    self.assertEqual(body['response_format'], {'type': 'json_schema', 'json_schema': {
                        'name': 'proposal_section', 'strict': True, 'schema': SCHEMA,
                    }})
                    self.assertEqual(body['max_tokens'], 3000)
                    self.assertIn('Project evidence', body['messages'][1]['content'])
                    answer = {'title': 'Projektreferenz',
                              'text': 'Die Projektreferenz liegt vor. [OFFEN: Verfügbarkeit bestätigen]',
                              'used_citations': ['Q1']}
                    return httpx.Response(200, json={'choices': [{
                        'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': json.dumps(answer)},
                    }]})

                with httpx.Client(transport=httpx.MockTransport(draft_response)) as http_client:
                    model = RequestyModel('https://router.eu.requesty.ai/v1', 'fixture', 'dummy-key',
                                          http_client=http_client)
                    with patch('mesakumo.ui.proposal.configured_model', return_value=model):
                        widget('button', 'Continue to Proposal Draft').click().run()
                        self.assertTrue(any('Received approved evidence' in item.value for item in app.success))
                        self.assertEqual(drafting_calls, [])
                        widget('button', 'Draft this section').click().run()
                        self.assertFalse(app.exception)
                        self.assertEqual(len(drafting_calls), 1)
                        self.assertEqual(widget('text_input', 'Section title').value, 'Projektreferenz')

                app = AppTest.from_file('mesakumo/streamlit_app.py').run()
                widget('selectbox', 'Phase').select('offer').run()
                app.sidebar.radio[0].set_value('Evidence Matching').run()
                self.assertEqual(widget('multiselect', 'Select evidence files').value, [MAIN_DECK])
                self.assertTrue(app.get('download_button'))
                self.assertEqual(factory.call_count, 2)

                widget('selectbox', 'Evidence fit').select('No match')
                widget('multiselect', 'Evidence citations').set_value([])
                widget('text_area', 'Evidence notes').set_value('Evidence removed by reviewer')
                widget('text_area', 'Gap / next action').set_value('Find a current reference')
                widget('button', 'Save evidence review').click().run()
                self.assertFalse(app.get('download_button'))
                self.assertIsNone(service.current(directory, evidence_inventory()))
                app.sidebar.radio[0].set_value('Proposal Draft').run()
                self.assertTrue(any('locked' in item.value for item in app.warning))
                app.sidebar.radio[0].set_value('Evidence Matching').run()
                widget('text_input', 'Evidence reviewer').set_value('Reviewer')
                widget('button', 'Approve and Save Evidence').click().run()
                self.assertTrue(any('Acknowledge' in item.value for item in app.error))
                widget('checkbox', 'I acknowledge open No match items').check()
                with store.connect() as db:
                    db.execute('''CREATE TRIGGER fail_evidence_approval BEFORE INSERT ON evidence_approvals
                                  BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END''')
                before_failure = service.load()
                widget('button', 'Approve and Save Evidence').click().run()
                self.assertTrue(any('Could not save evidence' in item.value for item in app.error))
                self.assertEqual(service.load(), before_failure)
                self.assertIsNone(service.current(directory, evidence_inventory()))
                with store.connect() as db:
                    db.execute('DROP TRIGGER fail_evidence_approval')
                widget('button', 'Approve and Save Evidence').click().run()
                self.assertTrue(app.get('download_button'))

                deck = directory / MAIN_DECK
                original = deck.read_bytes()
                deck.write_bytes(original + b'changed version')
                app.sidebar.radio[0].set_value('Proposal Draft').run()
                self.assertTrue(any('locked' in item.value for item in app.warning))
                app.sidebar.radio[0].set_value('Evidence Matching').run()
                self.assertFalse(app.get('download_button'))
                self.assertTrue(widget('button', 'Approve and Save Evidence').disabled)
                deck.write_bytes(original)
                app.run()
                self.assertTrue(app.get('download_button'))

                widget('button', 'Clear selection').click().run()
                widget('multiselect', 'Select evidence files').set_value([MAIN_DECK]).run()
                self.assertFalse(app.get('download_button'))
                self.assertIsNone(service.current(directory, evidence_inventory()))
                (pack / 'spec.txt').write_text('Submit four references.')
                app.run()
                self.assertTrue(any('locked' in item.value for item in app.warning))
                self.assertFalse(app.multiselect)
                self.assertFalse(app.dataframe)

    def test_only_proposal_requirements_reach_matching_and_approved_handoff(self):
        import hashlib
        import json
        from mesakumo.domain import ADMIN_SCOPE, PROPOSAL_SCOPE
        from mesakumo.evidence_matching import match_evidence

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            pack = directory / 'Ausschreibung 1'
            pack.mkdir()
            (pack / 'spec.txt').write_text('Include a project reference. Submit via the portal.')
            write_deck(directory / MAIN_DECK)
            store = ProjectStore(directory / 'workspace.sqlite3')
            project, rows = open_selection(store, pack, 'single')
            checklist = ChecklistReview(store)

            def edit(target, text, *, scope=None):
                return checklist.edit(project.id, store.load(project.id).revision, rows, target,
                                      text, 'submission instruction' if 'portal' in text else 'deliverable',
                                      'Mandatory', [project.passages[0].id], [], 'Reviewer', 'Checked', scope=scope)

            proposal = edit('', 'Include a project reference')
            administrative = edit('', 'Submit via the portal')
            legacy = checklist.approve(project.id, store.load(project.id).revision, rows, 'Reviewer', True)
            # Remove scope from the saved project, approval payload and CSV to simulate an old workspace.
            old_project = store.load(project.id).to_dict()
            for row in old_project['requirements']:
                row.pop('scope')
            old_metadata = {key: value for key, value in legacy.items() if key != 'csv'}
            for row in old_metadata['rows']:
                row.pop('scope')
            records = list(csv.reader(io.StringIO(legacy['csv'].decode())))
            column = records[0].index('requirement_scope')
            for record in records:
                record.pop(column)
            output = io.StringIO(newline='')
            csv.writer(output).writerows(records)
            old_csv = output.getvalue().encode()
            with store.connect() as db:
                db.execute('UPDATE projects SET payload=? WHERE id=?', (json.dumps(old_project), project.id))
                db.execute('UPDATE checklist_approvals SET payload=?,csv=?,checksum=? WHERE project_id=?',
                           (json.dumps(old_metadata), old_csv, hashlib.sha256(old_csv).hexdigest(), project.id))
            reopened = checklist.current(store.load(project.id), rows)
            self.assertEqual({r['scope'] for r in reopened['rows']}, {PROPOSAL_SCOPE})
            self.assertEqual({r['requirement_scope'] for r in csv.DictReader(io.StringIO(reopened['csv'].decode()))},
                             {PROPOSAL_SCOPE})

            edit(administrative, 'Submit via the portal', scope=ADMIN_SCOPE)
            self.assertIsNone(checklist.current(store.load(project.id), rows))
            approved = checklist.approve(project.id, store.load(project.id).revision, rows, 'Reviewer', True)
            exported = {r['requirement_id']: r['requirement_scope']
                        for r in csv.DictReader(io.StringIO(approved['csv'].decode()))}
            self.assertEqual(exported, {proposal: PROPOSAL_SCOPE, administrative: ADMIN_SCOPE})

            inventory = evidence_inventory()
            readings = read_evidence(directory, inventory, [MAIN_DECK])
            passage_id = readings[0].passages[0].id
            prompts = []

            class Model:
                def complete(self, prompt):
                    prompts.append(prompt)
                    return json.dumps({'tool': 'submit', 'args': {
                        'fit': 'Strong match', 'passage_ids': [passage_id], 'gap': ''}})

            run = match_evidence(approved, 'single', readings, Model)
            self.assertEqual(len(prompts), 1)
            self.assertNotIn('Submit via the portal', prompts[0])
            self.assertEqual([row.requirement['id'] for row in run.rows], [proposal])
            service = EvidenceReview(store, approved)
            draft = service.select(0, [MAIN_DECK])
            draft = service.save_run(draft['review_revision'], run, readings, directory)
            service.approve(draft['review_revision'], directory, inventory, 'Reviewer', False)
            handoff = service.current(directory, inventory)
            self.assertEqual([row['requirement']['id'] for row in handoff['review']['rows']], [proposal])
            evidence_row, = csv.DictReader(io.StringIO(handoff['csv'].decode()))
            self.assertEqual(evidence_row['Requirement ID'], proposal)
