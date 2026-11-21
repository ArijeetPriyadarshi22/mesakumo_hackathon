import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from mesakumo.documents import intake
from mesakumo.domain import Project


PRESENTATION_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
DRAWING_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
OFFICE_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'


def slide(shapes=''):
    return (
        f'<p:sld xmlns:p="{PRESENTATION_NS}" xmlns:a="{DRAWING_NS}">'
        f'<p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'
    )


TEXT_BOX = (
    '<p:sp><p:txBody><a:p>'
    '<a:r><a:t>Berat</a:t></a:r><a:r><a:t>ung für Öl und Gas</a:t></a:r>'
    '</a:p></p:txBody></p:sp>'
)
TABLE = (
    '<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:p>'
    '<a:r><a:t>Industrie </a:t></a:r><a:r><a:t>4.0</a:t></a:r><a:br/>'
    '<a:r><a:t>Übersicht</a:t></a:r>'
    '</a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
)


def deck(slides, targets):
    buffer = io.BytesIO()
    references = ''.join(
        f'<p:sldId id="{255 + index}" r:id="rId{index}"/>'
        for index in range(1, len(targets) + 1)
    )
    relationships = ''.join(
        f'<Relationship Id="rId{index}" Type="{OFFICE_REL_NS}/slide" Target="{target}"/>'
        for index, target in enumerate(targets, 1) if target is not None
    )
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            'ppt/presentation.xml',
            f'<p:presentation xmlns:p="{PRESENTATION_NS}" xmlns:r="{OFFICE_REL_NS}">'
            f'<p:sldIdLst>{references}</p:sldIdLst></p:presentation>',
        )
        archive.writestr(
            'ppt/_rels/presentation.xml.rels',
            f'<Relationships xmlns="{PACKAGE_REL_NS}">{relationships}</Relationships>',
        )
        for part, xml in slides.items():
            archive.writestr(f'ppt/slides/{part}', xml)
    return buffer.getvalue()


class PowerPointIntakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = Project('pptx-test', 'PowerPoint intake', False, 'Synthetic parser test')

    def test_presentation_order_blank_slide_and_source_metadata(self):
        content = deck(
            {'slide1.xml': slide(TABLE), 'slide2.xml': slide(), 'slide3.xml': slide(TEXT_BOX)},
            ['slides/slide3.xml', 'slides/slide2.xml', '/ppt/slides/slide1.xml'],
        )
        doc = intake(self.project, self.root, 'company.PPTX', content, role='knowledge')

        self.assertFalse(doc.error)
        self.assertEqual(doc.role, 'knowledge')
        self.assertEqual(doc.version, hashlib.sha256(content).hexdigest())
        self.assertEqual((self.root / doc.original_path).read_bytes(), content)
        self.assertEqual(
            [(p.location, p.excerpt) for p in self.project.passages],
            [('Slide 1', 'Beratung für Öl und Gas'), ('Slide 3', 'Industrie 4.0 Übersicht')],
        )
        for passage, number in zip(self.project.passages, (1, 3)):
            self.assertEqual(passage.document_id, doc.id)
            self.assertEqual(passage.document_version, doc.version)
            self.assertEqual(passage.id, f'{doc.id}-{doc.version[:12]}-P{number}')

    def test_bad_decks_leave_no_partial_passages(self):
        readable = {'slide1.xml': slide(TEXT_BOX)}
        cases = {
            'corrupt': b'not a ZIP archive',
            'empty': deck({'slide1.xml': slide()}, ['slides/slide1.xml']),
            'missing-relationship': deck(readable, ['slides/slide1.xml', None]),
            'missing-slide': deck(readable, ['slides/slide1.xml', 'slides/missing.xml']),
            'external-target': deck(readable, ['slides/slide1.xml', 'https://example.invalid/slide.xml']),
            'invalid-slide-xml': deck(
                {**readable, 'slide2.xml': '<broken'}, ['slides/slide1.xml', 'slides/slide2.xml'],
            ),
        }
        for name, content in cases.items():
            with self.subTest(name=name):
                doc = intake(self.project, self.root, f'{name}.pptx', content, role='knowledge')
                self.assertEqual(doc.status, 'Failed — excluded from extraction')
                self.assertTrue(doc.error)
                self.assertEqual(self.project.passages, [])
