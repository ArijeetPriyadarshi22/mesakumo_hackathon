import io
import json
import posixpath
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from mesakumo.discovery import participant_directory
from mesakumo.final_export import FinalExports, TEMPLATE_NAME, final_blockers
from mesakumo.persistence import ConflictError, ProjectStore
from mesakumo.pptx_export import A, NS, render_proposal
from mesakumo.proposal import ProposalDrafts

from test_proposal import PARTIAL, REVIEW, ROWS, Stub, save_approval_fixture

LONG = 'Wir liefern die vereinbarten Workshops und einen Abschlussbericht. ' * 36


def page_script(db_path, directory, review):
    import json
    from pathlib import Path
    import streamlit as st
    from mesakumo.persistence import ProjectStore
    from mesakumo.ui.proposal import final_review_view, proposal_view

    page = st.sidebar.radio('Page', ('Proposal Draft', 'Final Review & Export'), key='page')
    store = ProjectStore(Path(db_path))
    if page == 'Proposal Draft':
        proposal_view(store, {'review': json.loads(review)}, 'Fiktives Angebot')
    else:
        final_review_view(store, json.loads(review), directory=Path(directory), inventory={})


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

    def test_normal_page3_to_page4_and_exact_download(self):
        from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
        from streamlit.testing.v1 import AppTest

        original = MemoryMediaFileStorage.load_and_get_id

        def widget(widgets, label):
            return next(w for w in widgets if w.label.startswith(label))

        def downloaded(app, media, label):
            button = widget(app.get('download_button'), label)
            return media.call_args.args[0].get_file(button.proto.url.rsplit('/', 1)[1]).content

        with patch.dict('os.environ', {'MESAKUMO_LLM_PROVIDER': 'disabled'}), patch.object(
                MemoryMediaFileStorage, 'load_and_get_id', autospec=True, side_effect=original) as media:
            app = AppTest.from_function(page_script, args=(str(self.store.path), str(self.root),
                                                           json.dumps(self.review))).run()
            widget(app.text_input, 'Your name').set_value('Reviewer').run()
            widget(app.button, '✅ Approve this section').click().run()
            widget(app.checkbox, 'Leave out').check()
            widget(app.button, 'Approve draft for PPTX').click().run()
            widget(app.button, 'Continue to Final Review').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any('proposal package 1' in item.value for item in app.success))

            widget(app.button, 'Generate draft preview').click().run()
            preview = downloaded(app, media, 'Download draft preview')
            widget(app.button, 'Approve rendered PPTX').click().run()
            self.assertTrue(any('final reviewer name' in item.value for item in app.error))
            widget(app.text_input, 'Final reviewer').set_value('Final reviewer')
            widget(app.button, 'Approve rendered PPTX').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(downloaded(app, media, 'Download approved PPTX'), preview)
            self.assertEqual(self.template_path.read_bytes(), self.template_bytes)
            with ZipFile(io.BytesIO(preview)) as deck:
                self.assertIsNone(deck.testzip())
                parts = deck.namelist()
                for rels in (part for part in parts if part.endswith('.rels')):
                    owner_directory = rels.split('/_rels/', 1)[0] if not rels.startswith('_rels/') else ''
                    for relationship in ET.fromstring(deck.read(rels)):
                        self.assertNotEqual(relationship.get('TargetMode'), 'External')
                        target = posixpath.normpath(posixpath.join(owner_directory, relationship.attrib['Target']))
                        self.assertIn(target, parts)
                slides = sorted((p for p in parts if p.startswith('ppt/slides/slide') and p.endswith('.xml')),
                                key=lambda path: int(Path(path).stem.removeprefix('slide')))
                self.assertGreater(len(slides), 2)
                def text(path):
                    return ' '.join(n.text for n in ET.fromstring(deck.read(path)).iter('{' + A + '}t') if n.text)
                bodies = []
                for path in slides[1:]:
                    root = ET.fromstring(deck.read(path))
                    for shape in root.findall('.//p:sp', NS):
                        if shape.find('p:nvSpPr/p:cNvPr', NS).get('name') == 'Approved text':
                            bodies.extend(n.text for n in shape.findall('.//a:t', NS) if n.text)
                self.assertEqual(' '.join(' '.join(bodies).split()), LONG.strip())
                notes = text('ppt/notesSlides/notesSlide2.xml')
                for value in ('R2', 'Referenzen.pptx', 'Slide 10', 'P10', 'Leistungsbeschreibung.pdf', 'T1'):
                    self.assertIn(value, notes)
                xml = b' '.join(deck.read(path) for path in parts if path.endswith('.xml'))
                for example in ('AN00345', 'Münchner Leukämie Labor', 'Yvonne Bonasch', '09.10.2026',
                                'Geschäftsbedingungen', 'IT-Zielbild', 'Kundenname'):
                    self.assertNotIn(example.encode(), xml)
                with ZipFile(io.BytesIO(self.template_bytes)) as original_deck:
                    for asset in ('ppt/media/image4.jpeg', 'ppt/media/image5.png', 'ppt/theme/theme5.xml'):
                        self.assertEqual(deck.read(asset), original_deck.read(asset))
                self.assertFalse(any('/embeddings/' in part for part in parts))

            reopened = AppTest.from_function(page_script, args=(str(self.store.path), str(self.root),
                                                                json.dumps(self.review))).run()
            reopened.sidebar.radio[0].set_value('Final Review & Export').run()
            self.assertEqual(downloaded(reopened, media, 'Download approved PPTX'), preview)

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
