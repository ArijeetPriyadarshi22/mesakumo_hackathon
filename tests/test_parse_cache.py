"""Parsed tender documents are reused across workspaces, restarts and sessions."""
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

from mesakumo import discovery
from mesakumo.discovery import open_selection
from mesakumo.persistence import ProjectStore


def snapshot(project):
    return ([asdict(p) for p in project.passages],
            [(d.id, d.name, d.version, d.status, d.error) for d in project.documents])


class ParseCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.pack = self.root / 'Ausschreibung 9'
        (self.pack / 'sub').mkdir(parents=True)
        (self.pack / 'spec.txt').write_text('Zwei Workshops.\nAbgabe bis 01.12.2026.', encoding='utf-8')
        (self.pack / 'sub' / 'copy.txt').write_text('Zwei Workshops.\nAbgabe bis 01.12.2026.', encoding='utf-8')
        (self.pack / 'terms.txt').write_text('Vertragsbedingungen.', encoding='utf-8')
        (self.pack / 'empty.txt').write_text(' ', encoding='utf-8')  # Parses to an error result.
        (self.pack / 'form.bin').write_bytes(b'unsupported')
        self.cache = self.root / 'parse_cache'
        discovery._PARSED.clear()
        self.addCleanup(discovery._PARSED.clear)
        self.workspaces = 0

    def open(self, **options):
        self.workspaces += 1  # A fresh workspace, as for a new browser session.
        store = ProjectStore(self.root / f'ws{self.workspaces}' / 'workspace.sqlite3')
        calls = []
        project, rows = open_selection(store, self.pack, 'single', on_progress=lambda *a: calls.append(a), **options)
        return project, rows, calls

    def no_parsing(self):
        return mock.patch.object(discovery.LocalProcessor, 'extract', side_effect=AssertionError('parsed again'))

    def test_first_open_reports_progress_for_each_parsed_document(self):
        project, _, calls = self.open(cache_dir=self.cache)
        # spec, terms and empty are parsed; the alias and the unsupported file are not.
        self.assertEqual([c[:2] for c in calls], [(0, 3), (1, 3), (2, 3), (3, 3)])
        self.assertEqual(sorted(c[2] for c in calls[:-1]), ['empty.txt', 'spec.txt', 'terms.txt'])
        self.assertEqual(len(list(self.cache.glob('*.json'))), 3)

    def test_disk_cache_gives_identical_project_after_restart(self):
        first, _, _ = self.open(cache_dir=self.cache)
        discovery._PARSED.clear()  # Simulate a server restart.
        with self.no_parsing():
            second, _, calls = self.open(cache_dir=self.cache)
        self.assertEqual(snapshot(second), snapshot(first))
        self.assertEqual(calls, [])

    def test_memory_cache_serves_new_sessions_without_disk(self):
        first, _, _ = self.open()
        with self.no_parsing():
            second, _, calls = self.open()
        self.assertEqual(snapshot(second), snapshot(first))
        self.assertEqual(calls, [])

    def test_changed_file_and_damaged_entry_are_parsed_again(self):
        self.open(cache_dir=self.cache)
        (self.pack / 'terms.txt').write_text('Geänderte Vertragsbedingungen.', encoding='utf-8')
        discovery._PARSED.clear()
        for entry in self.cache.glob('*.json'):
            entry.write_text('{damaged', encoding='utf-8')
        project, _, calls = self.open(cache_dir=self.cache)
        # spec and empty are unchanged but damaged; terms changed: three parses plus the final call.
        self.assertEqual(sorted(c[2] for c in calls[:-1]), ['empty.txt', 'spec.txt', 'terms.txt'])
        self.assertTrue(any('Geänderte' in p.excerpt for p in project.passages))

    def test_parser_version_change_invalidates_cache(self):
        self.open(cache_dir=self.cache)
        discovery._PARSED.clear()
        with mock.patch.object(discovery, 'PARSER_VERSION', 'formats-v3'):
            _, _, calls = self.open(cache_dir=self.cache)
        self.assertEqual(len(calls), 4)

    def test_cached_passages_are_independent_copies(self):
        first, _, _ = self.open()
        second, _, _ = self.open()
        first.passages[0].excerpt = 'changed'
        self.assertNotEqual(second.passages[0].excerpt, 'changed')

    def test_unwritable_cache_folder_does_not_break_reading(self):
        blocker = self.root / 'not_a_folder'
        blocker.write_text('x', encoding='utf-8')
        project, rows, _ = self.open(cache_dir=blocker)
        self.assertTrue(project.passages)


if __name__ == '__main__':
    unittest.main()
