"""Create a tiny distributable OOXML package for workflow tests."""
import hashlib
import io
from unittest.mock import patch
from zipfile import ZipFile

from mesakumo.automation_deck import A, P, R, REL


def template_bytes(case):
    def relationship(target, kind):
        return f'<Relationship Id="rId1" Target="{target}" Type="{R}/{kind}"/>'

    def relations(target, kind):
        return f'<Relationships xmlns="{REL}">{relationship(target,kind)}</Relationships>'

    def slide(shape_ids):
        shapes = ''.join(f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Slot {sid}"/>'
                         '<p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody>'
                         '<a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>' for sid in shape_ids)
        return f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>{shapes}</p:spTree></p:cSld></p:sld>'

    output = io.BytesIO()
    with ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml',
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                         '</Types>')
        archive.writestr('_rels/.rels', relations('ppt/presentation.xml', 'officeDocument'))
        archive.writestr('ppt/presentation.xml', f'<p:presentation xmlns:p="{P}"><p:sldIdLst/>'
                         '<p:sldSz cx="9144000" cy="5143500"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        archive.writestr('ppt/_rels/presentation.xml.rels', f'<Relationships xmlns="{REL}"/>')
        archive.writestr('ppt/slideLayouts/slideLayout1.xml', f'<p:sldLayout xmlns:p="{P}"><p:cSld/></p:sldLayout>')
        for number, slots in ((1, [3, 4]), (6, [2, 3, 8])):
            archive.writestr(f'ppt/slides/slide{number}.xml', slide(slots))
            archive.writestr(f'ppt/slides/_rels/slide{number}.xml.rels',
                             relations('../slideLayouts/slideLayout1.xml', 'slideLayout'))
    data = output.getvalue()
    expected = patch('mesakumo.automation_deck.TEMPLATE_SHA256', hashlib.sha256(data).hexdigest())
    expected.start()
    case.addCleanup(expected.stop)
    return data
