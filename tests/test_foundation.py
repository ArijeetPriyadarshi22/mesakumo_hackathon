import tempfile
import unittest
from pathlib import Path
from mesakumo import workflow
from mesakumo.persistence import ProjectStore, ConflictError
from mesakumo.sample import sample_project
from mesakumo.models import DisabledModel, ModelUnavailable
from mesakumo.documents import UnconfiguredProcessor
from mesakumo.export import UnconfiguredExporter

class FoundationTests(unittest.TestCase):
    def test_restart_retains_edits_and_reviews(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.sqlite3'
            store = ProjectStore(path)
            project = sample_project()
            store.create(project)
            workflow.edit_section(project, 'S1', 'Human correction')
            workflow.resolve_issue(project, 'I1', '2025 (fictional)', 'Synthetic exercise answer')
            workflow.review(project, 'Test reviewer')
            workflow.approve(project, 'Test reviewer')
            store.save(project, 1)
            restored = ProjectStore(path).load(project.id)
            self.assertEqual(restored.to_dict(), project.to_dict())
            self.assertTrue(restored.approved)

    def test_blocker_and_review_gate(self):
        project = sample_project()
        with self.assertRaises(ValueError):
            workflow.approve(project, 'Reviewer')
        workflow.review(project, 'Reviewer')
        with self.assertRaises(ValueError):
            workflow.approve(project, 'Reviewer')
        with self.assertRaises(ValueError):
            workflow.resolve_issue(project, 'I1', 'Date', '')
        self.assertFalse(project.issues[0].resolved)

    def test_edits_invalidate_approval_and_source_badges(self):
        project = sample_project()
        workflow.resolve_issue(project, 'I1', 'Fictional date', 'Fixture note')
        workflow.review(project, 'Reviewer')
        workflow.approve(project, 'Reviewer')
        self.assertTrue(project.approved)
        workflow.edit_section(project, 'S2', 'Changed claim')
        self.assertFalse(project.approved)
        self.assertFalse(project.sections[1].reviewed)
        self.assertEqual(project.sections[1].source_ids, [])
        self.assertEqual(project.decisions[-1].decision, 'Approved')  # historical decision retained
        with self.assertRaises(ValueError):
            workflow.approve(project, 'Reviewer')

    def test_source_changes_cannot_reuse_approval(self):
        project = sample_project()
        workflow.resolve_issue(project, 'I1', 'Fictional date', 'Fixture')
        workflow.review(project, 'Reviewer')
        workflow.approve(project, 'Reviewer')
        project.passages[0].excerpt = 'Changed source'
        self.assertFalse(project.approved)

    def test_stale_writes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(Path(folder) / 'state.sqlite3')
            project = sample_project()
            store.create(project)
            stale = store.load(project.id)
            workflow.edit_section(project, 'S1', 'First edit')
            store.save(project, 1)
            workflow.edit_section(stale, 'S1', 'Stale edit')
            with self.assertRaises(ConflictError):
                store.save(stale, 1)
            self.assertEqual(store.load(project.id).sections[0].text, 'First edit')

    def test_adapters_fail_honestly(self):
        with self.assertRaises(ModelUnavailable):
            DisabledModel().complete('Prompt')
        with self.assertRaises(NotImplementedError):
            UnconfiguredProcessor().extract(Path('absent.pdf'), 'D1')
        with self.assertRaises(NotImplementedError):
            UnconfiguredExporter().render(sample_project())

if __name__ == '__main__':
    unittest.main()
