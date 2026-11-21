import unittest
from types import SimpleNamespace

from mesakumo.evidence_matching import LexicalIndex


def reading(*passages):
    return SimpleNamespace(status='Read', source=None, passages=[
        SimpleNamespace(id=pid, excerpt=text) for pid, text in passages
    ])


class EvidenceRankerTests(unittest.TestCase):
    def test_bm25_favors_compact_german_evidence_over_repeated_terms(self):
        index = LexicalIndex([reading(
            ('long', 'Trainer ' * 12 + 'Hintergrund ' * 300),
            ('certificate', 'Trainer Zertifikat für Online-Seminare.'),
            *[(f'other-{i}', 'Kurzbeschreibung eines anderen Projekts.') for i in range(6)],
        )])
        self.assertEqual(index.search('Trainer'), ['certificate', 'long'])
        self.assertEqual(index.search('Trainer Trainer Trainer'), ['certificate', 'long'])

    def test_bm25_keeps_five_hits_neighbors_and_deterministic_ties(self):
        index = LexicalIndex([reading(*[(str(i), 'Falcon certificate') for i in reversed(range(8))])])
        self.assertEqual(index.search('certificate'), ['0', '1', '2', '3', '4'])
        self.assertEqual(index.search('certificate', limit=2), ['0', '1'])
        self.assertEqual(index.neighbors['2'], ['3', '2', '1'])
        self.assertEqual(index.search('unrelated'), [])
        self.assertEqual(LexicalIndex([]).search('certificate'), [])
