import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from mesakumo.discovery import packs, open_selection
from mesakumo.persistence import ProjectStore

class DiscoveryTests(unittest.TestCase):
    def test_phases_aliases_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / 'Ausschreibung 3'
            pack.mkdir()
            (root / 'Beispielangebot').mkdir()
            (pack / 'one.txt').write_text('same source')
            with zipfile.ZipFile(pack / 'offer.zip', 'w') as z:
                z.writestr('alias.txt', 'same source')
                z.writestr('other.txt', 'offer only')
            store = ProjectStore(root / 'db.sqlite3')
            with patch('mesakumo.models.RequestyAdapter', create=True) as model:
                a, _ = open_selection(store, pack, 'participation')
                b, rows = open_selection(store, pack, 'offer')
                again, _ = open_selection(store, pack, 'offer')
                model.assert_not_called()
            self.assertEqual(packs(root), [pack])
            self.assertNotEqual(a.id, b.id)
            self.assertEqual(b.to_dict(), again.to_dict())
            self.assertEqual(len(b.documents), 2)
            self.assertEqual(len(store.list_projects()), 2)
            self.assertTrue(any('alias' in r['status'] for r in rows))

    def test_streamlit_entry(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'Ausschreibung 1').mkdir()
            with patch.dict('os.environ', {'MESAKUMO_PARTICIPANT_DIR': tmp, 'MESAKUMO_DATA_DIR': 'data/test_streamlit'}):
                app = AppTest.from_file('mesakumo/streamlit_app.py', default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(app.title[0].value, 'Tender Requirements')
                self.assertEqual(list(app.dataframe[0].value.columns),
                    ['statement','category','obligation_level','exact_constraints','source_references','requirement_id'])
                self.assertEqual(len(app.dataframe[0].value),0)
                self.assertLess(len(app.expander),10)
                app.sidebar.radio[0].set_value('Evidence Matching').run()
                self.assertFalse(app.exception)
