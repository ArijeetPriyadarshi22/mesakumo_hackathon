import tempfile
import unittest
import zipfile
from pathlib import Path
from mesakumo.documents import LocalProcessor

class FormatTests(unittest.TestCase):
    def test_windows_text_and_aidf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.txt';path.write_bytes('Angebot muss vollständig sein.'.encode('cp1252'))
            self.assertIn('vollständig',LocalProcessor().extract(path,'d')[0].excerpt)
            path=Path(tmp)/'source.aidf'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('data.lv','<lv><requirement>Drei Workshops sind erforderlich.</requirement></lv>')
            p=LocalProcessor().extract(path,'d')[0]
            self.assertIn('data.lv',p.location);self.assertIn('Drei Workshops',p.excerpt)

    def test_xlsx_sheet_row_cell_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.xlsx'
            ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Prices" r:id="s1"/></sheets></workbook>')
                z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="s1" Target="worksheets/sheet1.xml"/></Relationships>')
                z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="{ns}"><sheetData><row r="4"><c r="A4" t="inlineStr"><is><t>Three workshops required</t></is></c><c r="B4"><v>3</v></c></row></sheetData></worksheet>')
            p=LocalProcessor().extract(path,'d')[0]
            self.assertEqual(p.location,'Sheet Prices · Row 4')
            self.assertIn('A4: Three workshops required',p.excerpt)
            self.assertIn('B4: 3',p.excerpt)
