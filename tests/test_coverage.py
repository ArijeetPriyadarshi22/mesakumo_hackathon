import json
import tempfile
import unittest
from pathlib import Path
from mesakumo.discovery import open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.agent import sources
from mesakumo.coverage import CoverageRuns, referenced_candidates, source_groups

class CoverageTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        pack=Path(tmp.name)/'Ausschreibung 2';pack.mkdir()
        (pack/'spec.txt').write_text('Exactly three workshops are mandatory.')
        self.store=ProjectStore(Path(tmp.name)/'db')
        self.project,self.rows=open_selection(self.store,pack,'single')
        self.docs,self.passages,self.batches=sources(self.project,self.rows)
        self.sid=self.passages[0].id

    def test_all_batches_and_exact_quotes(self):
        class Model:
            def complete(inner,prompt):
                group=json.loads(prompt.split('Sources:\n')[1].split('\nPrevious response')[0])
                return json.dumps({'requirements':[{'statement':'Three workshops required','evidence_ids':[next(iter(group))],'kind':'deliverable','constraints':['three']}]})
        result=CoverageRuns(self.store).ensure(self.project,self.rows,Model())
        self.assertEqual(result['status'],'completed')
        self.assertEqual(set(result['processed']),set(self.batches))
        self.assertFalse(result['gaps'])
        self.assertTrue(self.store.load(self.project.id).requirements)

    def test_invalid_quote_retries_without_marking_processed(self):
        class Model:
            def complete(inner,prompt):
                return json.dumps({'requirements':[{'statement':'invented','evidence_ids':['E9999'],'kind':'other','constraints':[]}]})
        result=CoverageRuns(self.store).ensure(self.project,self.rows,Model())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['steps'],3)
        self.assertFalse(result['processed'])
        self.assertFalse(result['candidates'])

    def test_citations_are_original_text_at_the_selected_offsets(self):
        from mesakumo.coverage import evidence_catalog, referenced_candidates
        self.project.passages[0].excerpt='original evidence '*150
        group=list(sources(self.project,self.rows)[2].values())
        evidence=evidence_catalog(group)
        raw=json.dumps({'requirements':[{'statement':'Supported statement','evidence_ids':['E4'],
            'kind':'deliverable','constraints':[]}]})
        result=referenced_candidates(raw,evidence,self.project.passages)
        self.assertEqual(result[0].source_ids,[self.sid])
        self.assertEqual(result[0].original_excerpts,[evidence['E4']['text']])

    def test_all_groups_are_visited_even_with_no_requirements(self):
        self.project.passages[0].excerpt='No obligations. '*9000
        self.store.save(self.project,self.project.revision)
        class Model:
            def complete(inner,prompt): return '{"requirements":[]}'
        result=CoverageRuns(self.store).ensure(self.project,self.rows,Model())
        self.assertGreater(result['steps'],1)
        self.assertEqual(set(result['processed']),set(sources(self.project,self.rows)[2]))
        self.assertEqual(result['status'],'completed')

class TenderIsolationTests(unittest.TestCase):
    def test_switching_tenders_never_displays_other_tender_rows(self):
        import os
        from unittest.mock import patch
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for number, text in [(1,'FIRST UNIQUE requirement.'),(2,'SECOND UNIQUE requirement.')]:
                pack=root/f'Ausschreibung {number}';pack.mkdir()
                (pack/'spec.txt').write_text(text)
            class Model:
                def complete(inner,prompt):
                    group=json.loads(prompt.split('Sources:\n')[1])
                    ref=next(iter(group)); b=group[ref]
                    return json.dumps({'requirements':[{'statement':b['text'],'evidence_ids':[ref],'kind':'deliverable','constraints':[]}]})
            with patch.dict(os.environ,{'MESAKUMO_PARTICIPANT_DIR':tmp,'MESAKUMO_LLM_PROVIDER':'disabled'}),patch('mesakumo.models.configured_model',return_value=Model()):
                previous=os.environ.pop('MESAKUMO_DATA_DIR',None)
                try:
                    app=AppTest.from_file('mesakumo/legacy_streamlit.py',default_timeout=30).run()
                    workspace=app.session_state['fresh_workspace_v3']
                    next(b for b in app.sidebar.button if b.label=='Start extraction').click().run()
                    self.assertIn('FIRST UNIQUE',str(app.dataframe[0].value))
                    app.sidebar.selectbox[0].select('Ausschreibung 2').run()
                    self.assertEqual(len(app.dataframe[0].value),0)
                    next(b for b in app.sidebar.button if b.label=='Start extraction').click().run()
                    self.assertFalse(app.exception)
                    self.assertIn('SECOND UNIQUE',str(app.dataframe[0].value))
                    self.assertNotIn('FIRST UNIQUE',str(app.dataframe[0].value))
                    self.assertEqual(app.subheader[0].value,'Ausschreibung 2 · single')
                    fresh=AppTest.from_file('mesakumo/legacy_streamlit.py',default_timeout=30).run()
                    self.assertNotEqual(fresh.session_state['fresh_workspace_v3'],workspace)
                    self.assertEqual(len(fresh.dataframe[0].value),0)
                finally:
                    if previous is not None: os.environ['MESAKUMO_DATA_DIR']=previous

class AdaptiveCoverageTests(unittest.TestCase):
    setUp = CoverageTests.setUp
    def test_output_limit_splits_group_and_covers_every_batch(self):
        from mesakumo.models import ModelUnavailable
        self.project.passages[0].excerpt='Neutral source text. '*200
        self.store.save(self.project,self.project.revision)
        class Model:
            calls=0
            def complete(inner,prompt):
                inner.calls+=1
                if inner.calls==1: raise ModelUnavailable('Provider did not finish normally')
                return '{"requirements":[]}'
        result=CoverageRuns(self.store).ensure(self.project,self.rows,Model())
        self.assertEqual(result['status'],'completed')
        self.assertEqual(set(result['processed']),set(sources(self.project,self.rows)[2]))

class ResumeCoverageTests(unittest.TestCase):
    setUp = CoverageTests.setUp
    def test_interrupted_run_resumes_only_unprocessed_batches(self):
        from mesakumo.models import ModelUnavailable
        self.project.passages[0].excerpt='Neutral source text. '*1300
        self.store.save(self.project,self.project.revision)
        class First:
            calls=0
            def complete(inner,prompt):
                inner.calls+=1
                if inner.calls==2: raise ModelUnavailable('Synthetic connection failure')
                return '{"requirements":[]}'
        runs=CoverageRuns(self.store)
        failed=runs.ensure(self.project,self.rows,First())
        self.assertEqual(failed['status'],'failed')
        already=set(failed['processed'])
        self.assertTrue(already)
        class Resume:
            calls=0
            def complete(inner,prompt):
                inner.calls+=1
                return '{"requirements":[]}'
        resumed=runs.ensure(self.project,self.rows,Resume(),retry=True)
        self.assertEqual(resumed['status'],'completed')
        self.assertEqual(len(resumed['processed']),len(set(resumed['processed'])))
        self.assertTrue(already.issubset(resumed['processed']))
