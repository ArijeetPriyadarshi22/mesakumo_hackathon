import csv
import io
import tempfile
import unicodedata
import unittest
import zipfile
from pathlib import Path

from mesakumo.checklist import ChecklistReview
from mesakumo.discovery import open_selection, participant_directory
from mesakumo.evidence import EVIDENCE_ROLES, evidence_inventory, read_evidence
from mesakumo.persistence import ProjectStore
from mesakumo.evidence_review import EvidenceReview

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
