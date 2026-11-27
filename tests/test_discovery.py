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
