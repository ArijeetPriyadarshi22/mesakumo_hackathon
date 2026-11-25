import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from mesakumo.discovery import ROOT, open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.tender_overview import FIELDS, SOURCE_BUDGET, TenderOverviews, overview_context


class OverviewModel:
    def __init__(self, invalid=False):
        self.invalid = invalid
        self.calls = 0

    def complete(self, prompt):
        excerpts = json.loads(prompt.split('Sources:\n', 1)[1])
        ref = next(iter(excerpts))
        return json.dumps({'requirements': [{
            'statement': 'Drei Workshops durchführen', 'kind': 'deliverable',
            'constraints': ['drei'], 'evidence_ids': [ref],
        }]})

    def complete_overview(self, prompt, schema):
        self.calls += 1
        context = json.loads(prompt.split('Quellenauszüge:\n', 1)[1])
        passage = context[0]['id']
        fields = {name: dict(status='fehlt', text='', citations=[]) for name in FIELDS}
        fields['procurement'] = dict(status='belegt', text='Drei Workshops für die Stadt Nord.',
                                     citations=['foreign-id' if self.invalid else passage])
        fields['buyer'] = dict(status='belegt', text='Stadt Nord', citations=[passage])
        fields['dates'] = dict(status='widerspruch', text='Die Quellen nennen den 3. und den 5. Dezember.',
                               citations=[passage])
        return json.dumps(fields)


class TenderOverviewTests(unittest.TestCase):
    def test_phase_context_and_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pack = root / 'Ausschreibung 3'
            pack.mkdir()
            (pack / 'Auswahlkriterium im Teilnahmewettbewerb.txt').write_text('Teilnahmewettbewerb: Referenzen.')
            (pack / 'Leistungsbeschreibung.txt').write_text('Stadt Nord beschafft Workshops.')
            (pack / 'Bearbeitungshinweis.txt').write_text('Interne Angaben')
            for i in range(18):
                (pack / f'Anhang {i}.txt').write_text('A' * 10000)
            with zipfile.ZipFile(pack / 'Unterlagen Angebotsphase.zip', 'w') as archive:
                archive.writestr('Wirtschaftlichkeitswertung.txt', 'Zuschlagskriterium: Konzeptqualität.')
            store = ProjectStore(root / 'workspace.sqlite3')
            for phase in ('participation', 'offer'):
                project, rows = open_selection(store, pack, phase)
                context = overview_context(project, rows, phase)
                names = [item['file'] for item in context]
                self.assertIn('Leistungsbeschreibung.txt', names[:2])
                self.assertNotIn('Bearbeitungshinweis.txt', names)
                self.assertEqual(any('Teilnahmewettbewerb' in name for name in names), phase == 'participation')
                self.assertEqual(any('Wirtschaftlichkeitswertung' in name for name in names), phase == 'offer')
                self.assertLessEqual(len(json.dumps(context, ensure_ascii=False).encode()), SOURCE_BUDGET)

    def test_invalid_citation_and_once_per_source_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pack = root / 'Ausschreibung 1'
            pack.mkdir()
            spec = pack / 'Leistungsbeschreibung.txt'
            spec.write_text('Die Stadt Nord beschafft drei Workshops.')
            store = ProjectStore(root / 'workspace.sqlite3')
            project, rows = open_selection(store, pack, 'single')
            cache = TenderOverviews(root / 'overview.sqlite3')
            model = OverviewModel(invalid=True)
            result = cache.ensure(project, rows, 'single', model)
            self.assertEqual(result['status'], 'failed')
            self.assertIn('ungültigen Quellenverweis', result['message'])
            self.assertEqual(cache.ensure(project, rows, 'single', model), result)
            self.assertEqual(model.calls, 1)
            spec.write_text('Die Stadt Nord beschafft vier Workshops.')
            project, rows = open_selection(store, pack, 'single')
            self.assertIsNone(cache.get(project, rows, 'single'))
            model.invalid = False
            self.assertEqual(cache.ensure(project, rows, 'single', model)['status'], 'ready')
            self.assertEqual(model.calls, 2)

    def test_ui_placement_sources_reload_and_failure_isolation(self):
        from streamlit.testing.v1 import AppTest
        (ROOT / 'data').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory(dir=ROOT / 'data') as data:
            root = Path(folder)
            for number in (1, 2):
                pack = root / f'Ausschreibung {number}'
                pack.mkdir()
                (pack / 'Leistungsbeschreibung.txt').write_text(
                    'Die Stadt Nord beschafft drei Workshops. Termine: 3. Dezember bzw. 5. Dezember.')
            model = OverviewModel()
            with patch.dict('os.environ', {'MESAKUMO_PARTICIPANT_DIR': folder, 'MESAKUMO_DATA_DIR': data,
                                          'MESAKUMO_KEEP_SESSION': 'no', 'MESAKUMO_LLM_PROVIDER': 'disabled'}), patch(
                                              'mesakumo.models.configured_model', return_value=model):
                app = AppTest.from_file('mesakumo/legacy_streamlit.py').run()
                self.assertEqual(model.calls, 0)
                next(b for b in app.sidebar.button if b.label == 'Start extraction').click().run()
                self.assertFalse(app.exception)
                headings = [widget.value for widget in app.subheader]
                self.assertLess(headings.index('Vergabeübersicht'), headings.index('Requirements'))
                self.assertTrue(any('Leistungsbeschreibung.txt · Lines' in e.label for e in app.expander))
                self.assertTrue(any('Die Stadt Nord beschafft' in item.value for item in app.text))
                self.assertTrue(any('Widersprüchliche' in item.value for item in app.warning))
                self.assertTrue(any('nicht gefunden' in item.value for item in app.caption))
                self.assertEqual(model.calls, 1)
                app = AppTest.from_file('mesakumo/legacy_streamlit.py').run()
                self.assertTrue(any('Drei Workshops für die Stadt Nord.' == item.value for item in app.text))
                self.assertEqual(model.calls, 1)
                next(b for b in app.sidebar.button if b.label == 'Start extraction').click().run()
                self.assertEqual(model.calls, 1)
                app.sidebar.selectbox[0].select('Ausschreibung 2').run()
                self.assertFalse(any(widget.value == 'Vergabeübersicht' for widget in app.subheader))
                model.invalid = True
                next(b for b in app.sidebar.button if b.label == 'Start extraction').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any('ungültigen Quellenverweis' in item.value for item in app.warning))
                self.assertIn('Drei Workshops durchführen', app.dataframe[0].value['requirement'].tolist())
