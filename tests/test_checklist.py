import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from mesakumo.discovery import open_selection
from mesakumo.persistence import ProjectStore, ConflictError
from mesakumo.checklist import ChecklistReview
from mesakumo.agent import sources

class ChecklistTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);pack=root/'Ausschreibung 1';pack.mkdir()
        (pack/'spec.txt').write_text('Drei Workshops. Abgabe am 01.12.2026.\nÖffentliche Referenzen erforderlich.')
        self.store=ProjectStore(root/'db.sqlite3');self.project,self.rows=open_selection(self.store,pack,'single')
        self.pid=self.project.id;self.service=ChecklistReview(self.store)
        self.sid=sources(self.project,self.rows)[1][0].id
        self.first=self.edit('', 'Original candidate')
        self.second=self.edit('', 'Remove candidate')

    def edit(self,target,text):
        p=self.store.load(self.pid)
        return self.service.edit(self.pid,p.revision,self.rows,target,text,'deliverable','Mandatory',[self.sid],['drei; exakt'], 'Reviewer','Human correction')

    def approve(self,**kwargs):
        return self.service.approve(self.pid,self.store.load(self.pid).revision,self.rows,'Reviewer',True,**kwargs)

    def test_edit_add_remove_approve_csv_restart_invalidate(self):
        self.edit(self.first,'Drei Workshops – geprüft')
        manual=self.edit('', 'Manuell ergänzt: öffentliche Referenzen')
        p=self.store.load(self.pid);self.service.remove(self.pid,p.revision,self.second,'Reviewer')
        input_revision=self.store.load(self.pid).revision
        approved=self.approve()
        table=list(csv.DictReader(io.StringIO(approved['csv'].decode('utf-8'))))
        current=self.store.load(self.pid)
        self.assertEqual({r['requirement_id'] for r in table},{self.first,manual})
        for row,r in zip(table,current.requirements):
            self.assertEqual(row['requirement'],r.text)
            self.assertEqual(row['obligation_level'],r.obligation_level)
            self.assertEqual(json.loads(row['exact_constraints']),r.constraints)
            self.assertEqual(json.loads(row['source_references'])[0]['passage_id'],self.sid)
            self.assertEqual(json.loads(row['source_references'])[0]['filename'],'spec.txt')
            self.assertEqual(json.loads(row['source_references'])[0]['original_excerpt'],current.passages[0].excerpt)
            self.assertEqual(int(row['approved_revision']),current.revision)
        restarted=ChecklistReview(ProjectStore(self.store.path))
        self.assertEqual(restarted.current(current,self.rows)['csv'],approved['csv'])
        repeated=restarted.approve(self.pid,input_revision,self.rows,'Reviewer',True)
        self.assertEqual(repeated['revision'],approved['revision'])
        with self.store.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM checklist_approvals').fetchone()[0],1)
        self.edit(self.first,'Vier Workshops – erneute Prüfung')
        self.assertIsNone(restarted.current(self.store.load(self.pid),self.rows))

    def test_older_saved_csv_uses_requirement_header_on_download(self):
        from mesakumo.checklist import make_csv
        def legacy(*args):
            return make_csv(*args).replace(b',requirement,', b',statement,', 1)
        approved=self.approve(csv_factory=legacy)
        current=self.service.current(self.store.load(self.pid),self.rows)
        original=list(csv.reader(io.StringIO(approved['csv'].decode())))
        downloaded=list(csv.reader(io.StringIO(current['csv'].decode())))
        self.assertIn('requirement',downloaded[0])
        self.assertNotIn('statement',downloaded[0])
        self.assertEqual(original[1:],downloaded[1:])

    def test_csv_failure_atomic_recovery(self):
        before=self.store.load(self.pid).to_dict()
        def fail(*args): raise OSError('simulated failure')
        with self.assertRaisesRegex(ValueError,'No approval was saved'):
            self.approve(csv_factory=fail)
        self.assertEqual(self.store.load(self.pid).to_dict(),before)
        self.assertIsNone(self.service.current(self.store.load(self.pid),self.rows))
        approved=self.approve()
        self.assertTrue(approved['csv'])
        self.assertEqual(sum(d.decision=='Checklist approved and saved' for d in self.store.load(self.pid).decisions),1)

    def test_stale_writes_citations_and_source_changes(self):
        old=self.store.load(self.pid).revision
        self.edit(self.first,'Edited')
        with self.assertRaises(ConflictError):
            self.service.approve(self.pid,old,self.rows,'Reviewer',True)
        with self.assertRaises(ConflictError):
            self.service.remove(self.pid,old,self.first,'Reviewer')
        with self.assertRaises(ValueError):
            self.service.edit(self.pid,self.store.load(self.pid).revision,self.rows,'','invalid','other','Mandatory',['foreign'],[],'Reviewer','note')
        self.approve()
        self.assertIsNone(self.service.current(self.store.load(self.pid),[]))

    def test_backward_compatible_obligation_default(self):
        from mesakumo.domain import Project
        raw=self.store.load(self.pid).to_dict()
        for r in raw['requirements']:
            r.pop('obligation_level')
            r.pop('scope')
        restored = Project.from_dict(raw)
        self.assertEqual(restored.requirements[0].obligation_level,'Unspecified')
        self.assertEqual(restored.requirements[0].scope, 'Proposal requirement')
        import hashlib
        raw.pop('decisions')
        legacy = hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()
        self.assertEqual(restored.fingerprint(),legacy)

    def test_database_failure_rolls_back_approval_and_recovers(self):
        before=self.store.load(self.pid).to_dict()
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER fail_approval BEFORE INSERT ON checklist_approvals BEGIN SELECT RAISE(ABORT,'synthetic save failure'); END")
        with self.assertRaisesRegex(ValueError,'No approval was saved'):
            self.approve()
        self.assertEqual(self.store.load(self.pid).to_dict(),before)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM checklist_approvals').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_approval')
        self.assertTrue(self.approve()['csv'])
