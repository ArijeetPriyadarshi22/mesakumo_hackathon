import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from mesakumo.discovery import participant_directory
from mesakumo.final_export import FinalExports, TEMPLATE_NAME, final_blockers
from mesakumo.persistence import ConflictError, ProjectStore
from mesakumo.pptx_export import render_proposal
from mesakumo.proposal import ProposalDrafts

from test_proposal import PARTIAL, REVIEW, ROWS, Stub, save_approval_fixture

LONG = 'Wir liefern die vereinbarten Workshops und einen Abschlussbericht. ' * 36



class FinalExportTests(unittest.TestCase):
    def setUp(self):
        template = participant_directory() / TEMPLATE_NAME
        if not template.is_file():
            self.skipTest('The supplied corporate template is unavailable.')
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.template_path = self.root / TEMPLATE_NAME
        shutil.copyfile(template, self.template_path)
        self.template_bytes = template.read_bytes()
        optional = {**ROWS[2], 'requirement': {**ROWS[2]['requirement'], 'obligation_level': 'Optional'}}
        self.review = {**REVIEW, 'rows': [ROWS[1], optional]}
        self.store = ProjectStore(self.root / 'workspace.sqlite3')
        save_approval_fixture(self.store, self.review)
        self.drafts = ProposalDrafts(self.store, self.review)
        self.drafts.generate('R2', Stub({'title':'Workshop-Konzept', 'text':LONG, 'used_citations':['Q1']}), 0)

    def _package(self):
        self.drafts.approve('R2', 'Section reviewer', 1)
        return self.drafts.approve_package('Package reviewer', acknowledge_excluded=True, project_name='Fiktives Angebot')

    def test_stale_template_package_and_checksum(self):
        package = self._package()
        original = FinalExports(self.drafts, package, self.root)
        preview = original.preview()
        with self.assertRaisesRegex(ConflictError, 'preview changed'):
            original.approve('Reviewer', 'stale')
        stored = original.approve('Reviewer', preview['checksum'])
        self.assertEqual(stored['data'], preview['data'])

        self.template_path.write_bytes(self.template_bytes + b'new template version')
        with self.assertRaisesRegex(ConflictError, 'template changed'):
            original.current()
        current = FinalExports(self.drafts, package, self.root)
        self.assertIsNone(current.current())
        newer = current.preview()
        self.assertIsNone(newer['reviewer'])
        self.drafts.edit('R2', 'Changed', 'Edited text', 2)
        with self.assertRaisesRegex(ConflictError, 'proposal package changed'):
            current.approve('Reviewer', newer['checksum'])
        with self.assertRaisesRegex(ConflictError, 'proposal package changed'):
            current.current()

    def test_blockers_are_visible_in_preview_and_prevent_approval(self):
        review = {**REVIEW, 'rows': [ROWS[0], ROWS[2]]}
        save_approval_fixture(self.store, review)
        drafts = ProposalDrafts(self.store, review)
        drafts.generate('R1', Stub(PARTIAL), 0)
        drafts.approve('R1', 'Reviewer', 1, accept_open_items=True)
        package = drafts.approve_package('Reviewer', acknowledge_excluded=True)
        issues = final_blockers(package, drafts.rows)
        self.assertTrue(any('Verfügbarkeit bestätigen.' in issue for issue in issues))
        self.assertTrue(any('Excluded required' in issue for issue in issues))
        exports = FinalExports(drafts, package, self.root)
        preview = exports.preview()
        with ZipFile(io.BytesIO(preview['data'])) as deck:
            self.assertIn(b'[OFFEN:', b''.join(deck.read(part) for part in deck.namelist()
                                                if part.startswith('ppt/slides/slide')))
        with self.assertRaisesRegex(ValueError, 'blocking items'):
            exports.approve('Reviewer', preview['checksum'])
        self.assertIsNone(exports.current()['reviewer'])

    def test_template_errors_retry_and_late_render_is_rejected(self):
        package = self._package()
        self.template_path.unlink()
        with self.assertRaisesRegex(ValueError, 'was not found'):
            FinalExports(self.drafts, package, self.root)
        self.template_path.write_bytes(b'invalid ZIP')
        broken = FinalExports(self.drafts, package, self.root)
        with self.assertRaisesRegex(ValueError, 'invalid PPTX mapping'):
            broken.preview()
        self.assertIsNone(broken.current())
        self.template_path.write_bytes(self.template_bytes)
        exports = FinalExports(self.drafts, package, self.root)

        def render_and_invalidate(package, template):
            data = render_proposal(package, template)
            self.drafts.edit('R2', 'Changed after rendering', 'New text', 2)
            return data

        with patch('mesakumo.final_export.render_proposal', side_effect=render_and_invalidate):
            with self.assertRaisesRegex(ConflictError, 'proposal package changed'):
                exports.preview()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM final_exports').fetchone()[0], 0)
