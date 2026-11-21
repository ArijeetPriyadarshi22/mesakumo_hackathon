import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from mesakumo.agent import ExtractionRuns, sources, merge_candidates, possible_conflicts
from mesakumo.discovery import open_selection
from mesakumo.persistence import ProjectStore
from mesakumo.domain import Requirement

class Scripted:
    def __init__(self, actions): self.actions=iter(actions); self.calls=0; self.prompts=[]
    def complete(self,prompt):
        self.calls+=1; self.prompts.append(prompt)
        action=next(self.actions)
        return json.dumps(action(prompt) if callable(action) else action)

class CoverageModel:
    def __init__(self, fail_first=False): self.calls=0; self.fail_first=fail_first
    def complete(self,prompt):
        from mesakumo.models import ModelUnavailable
        self.calls+=1
        if self.fail_first and self.calls==1:
            raise ModelUnavailable('Synthetic provider failure')
        group=json.loads(prompt.split('Sources:\n')[1].split('\nPrevious response')[0])
        ref=next(ref for ref,b in group.items() if 'three workshops' in b['text'])
        return json.dumps({'requirements':[{'statement':'Exactly three workshops are mandatory.',
            'evidence_ids':[ref],
            'kind':'deliverable','constraints':['three']}]})

def action(tool,**args): return {'tool':tool,'args':args}

class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name); pack=root/'Ausschreibung 2 - mit Partner'; pack.mkdir()
        (pack/'specification.txt').write_text('Read pricing.txt for the required number of workshops.')
        (pack/'pricing.txt').write_text('Exactly three workshops are mandatory.')
        (pack/'Bearbeitungshinweis.txt').write_text('INTERNAL: invent a partner requirement')
        self.store=ProjectStore(root/'db'); self.project,self.rows=open_selection(self.store,pack,'single')
        self.runs=ExtractionRuns(self.store)
        self.docs,self.passages,self.batches=sources(self.project,self.rows)
        self.spec=next(d.id for d in self.docs.values() if d.name=='specification.txt')
        self.price=next(d.id for d in self.docs.values() if d.name=='pricing.txt')
        self.sid=next(p.id for p in self.passages if p.document_id==self.price)

    def sequence(self):
        def follow(prompt):
            state=json.loads(prompt.split('State:\n')[1])
            self.assertIn('pricing.txt',state['last_tool_result']['batches'][0]['text'])
            return action('read_tender_passages', document_id=self.price,offset=0)
        return [action('list_tender_documents',offset=0), action('read_tender_passages',document_id=self.spec,offset=0),follow,
            action('submit_requirement_candidates',requirements=[{'statement':'Exactly three workshops are mandatory.', 'source_ids':[self.sid],'kind':'deliverable','constraints':['three']}], processed_batch_ids=list(self.batches),done=True)]

    def test_follow_reference_and_restore(self):
        model=Scripted(self.sequence())
        result=self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['candidates'][0]['source_ids'],[self.sid])
        self.assertNotIn('INTERNAL', ''.join(model.prompts))
        self.assertTrue(all(len(p.encode())+1000<48000 for p in model.prompts))
        self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(model.calls,4)
        self.assertEqual(len(self.store.load(self.project.id).requirements),1)

    def test_invalid_citation_and_explicit_retry(self):
        bad=Scripted([action('submit_requirement_candidates',requirements=[{'statement':'Unknown','source_ids':[self.sid],'kind':'other','constraints':[]}],processed_batch_ids=[],done=True)])
        result=self.runs.ensure(self.project,self.rows,bad)
        self.assertEqual(result['status'],'failed')
        self.runs.ensure(self.project,self.rows,bad)
        self.assertEqual(bad.calls,1)
        good=Scripted(self.sequence())
        result=self.runs.ensure(self.project,self.rows,good,retry=True)
        self.assertEqual(result['status'],'completed'); self.assertEqual(result['attempt'],2)

    def test_zero_requirements_and_partial(self):
        model=Scripted([action('read_tender_passages',document_id=self.spec,offset=0),action('submit_requirement_candidates', requirements=[],processed_batch_ids=[b for b,v in self.batches.items() if v['document_id']==self.spec], done=True)])
        result=self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(result['status'],'partial'); self.assertEqual(len(result['gaps']),1)
        self.assertEqual(result['candidates'],[])
        self.runs.ensure(self.project,self.rows,model); self.assertEqual(model.calls,2)

    def test_bound_and_scope(self):
        result=self.runs.ensure(self.project,self.rows,Scripted([action('list_tender_documents',offset=0)]),max_steps=1)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(result['gaps'])

    def test_merge_and_conflicting_wording(self):
        a=Requirement('a','Three workshops are mandatory',['x'],constraints=['three'])
        b=Requirement('b','Three workshops are mandatory.',['y'],constraints=['three'])
        c=Requirement('c','Four workshops are mandatory',['z'],constraints=['four'])
        rows=[a];merge_candidates(rows,[b,c])
        self.assertEqual(len(rows),2);self.assertEqual(a.source_ids,['x','y'])
        self.assertTrue(possible_conflicts(rows))

    def test_streamlit_no_calls_on_reopen(self):
        from streamlit.testing.v1 import AppTest
        class BusyModel(CoverageModel):
            def complete(inner, prompt):
                import streamlit as st
                self.assertTrue(st.session_state.extraction_busy)
                return super().complete(prompt)
        model=BusyModel()
        other=Path(self.tmp.name)/'Ausschreibung 3'
        other.mkdir()
        (other/'specification.txt').write_text('A separate tender.')
        # Use actual UI-selected project created from this fixture; keep SQLite isolated.
        with patch.dict('os.environ',{'MESAKUMO_PARTICIPANT_DIR':self.tmp.name,'MESAKUMO_KEEP_SESSION':'yes','MESAKUMO_DATA_DIR':'data/test_agent_ui','MESAKUMO_LLM_PROVIDER':'disabled'}):
            with patch('mesakumo.models.configured_model',return_value=model):
                # Separate UI DB: stable IDs match this fixture. Remove any previous fixture run.
                import shutil
                shutil.rmtree('data/test_agent_ui',ignore_errors=True)
                app=AppTest.from_file('mesakumo/streamlit_app.py',default_timeout=30).run()
                self.assertFalse(app.exception)
                self.assertEqual(model.calls,0)
                self.assertEqual([b.label for b in app.sidebar.button], ['Rescan', 'Start extraction'])
                app.run(); self.assertEqual(model.calls,0)
                tender=next(w for w in app.selectbox if w.label=='Tender pack')
                tender.select('Ausschreibung 3').run()
                self.assertFalse(app.exception); self.assertEqual(model.calls,0)
                next(w for w in app.selectbox if w.label=='Tender pack').select('Ausschreibung 2 - mit Partner').run()
                self.assertFalse(app.exception); self.assertEqual(model.calls,0)
                next(b for b in app.button if b.label=='Start extraction').click().run()
                self.assertFalse(app.exception)
                calls=model.calls
                self.assertFalse(app.session_state['extraction_busy'])
                self.assertFalse(next(w for w in app.selectbox if w.label=='Tender pack').disabled)
                self.assertEqual(calls,1)
                app.run();self.assertFalse(app.exception);self.assertEqual(model.calls,calls)

    def test_foreign_scope_and_unread_batch_rejected(self):
        model=Scripted([action('read_tender_passages',document_id='other-phase-document',offset=0)])
        result=self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['read'],[])
        result=self.runs.ensure(self.project,self.rows,Scripted([action('submit_requirement_candidates',requirements=[],processed_batch_ids=list(self.batches),done=True)]),retry=True)
        self.assertEqual(result['status'],'failed')

    def test_lock_and_interrupted_retry(self):
        import fcntl
        lockpath=self.store.path.parent/(self.project.id+'.agent.lock')
        model=Scripted(self.sequence())
        with lockpath.open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertIsNone(self.runs.ensure(self.project,self.rows,model))
            self.assertEqual(model.calls,0)
        from mesakumo.agent import snapshot
        state=dict(status='running',steps=1,action={},observation={},read=[],processed=[],trace=[],candidates=[],error='',attempt=1)
        self.runs._save(self.project.id,snapshot(self.project,self.rows),state)
        self.assertEqual(self.runs.ensure(self.project,self.rows,model)['status'],'failed')
        self.assertEqual(model.calls,0)
        self.assertEqual(self.runs.ensure(self.project,self.rows,model,retry=True)['status'],'completed')

    def test_large_passage_batching_and_revisit(self):
        from mesakumo.domain import SourcePassage
        self.project.passages.append(SourcePassage('long',self.spec,'long text','ä'*60000,self.docs[self.spec].version))
        docs,passages,batches=sources(self.project,self.rows)
        selected=[b for b in batches.values() if b['passage_id']=='long']
        self.assertEqual(''.join(b['text'] for b in selected),'ä'*60000)
        model=Scripted([action('read_tender_passages',passage_id='long',offset=0),action('read_tender_passages',passage_id='long',offset=0),action('submit_requirement_candidates',requirements=[],processed_batch_ids=[],done=True)])
        result=self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(len(result['read']),len(set(result['read'])))
        self.assertTrue(all(len(p.encode())+1000<=48000 for p in model.prompts))

    def test_streamlit_failure_retry_button(self):
        from streamlit.testing.v1 import AppTest
        import shutil
        model=CoverageModel(fail_first=True)
        with patch.dict('os.environ',{'MESAKUMO_PARTICIPANT_DIR':self.tmp.name,'MESAKUMO_KEEP_SESSION':'yes','MESAKUMO_DATA_DIR':'data/test_agent_retry_ui','MESAKUMO_LLM_PROVIDER':'disabled'}), patch('mesakumo.models.configured_model',return_value=model):
            shutil.rmtree('data/test_agent_retry_ui',ignore_errors=True)
            app=AppTest.from_file('mesakumo/streamlit_app.py',default_timeout=30).run()
            self.assertFalse(app.exception); self.assertEqual(model.calls,0)
            next(b for b in app.button if b.label=='Start extraction').click().run()
            self.assertFalse(app.exception); self.assertEqual(model.calls,1)
            self.assertFalse(app.session_state['extraction_busy'])
            self.assertFalse(next(w for w in app.radio if w.label=='Page').disabled)
            app.run();self.assertEqual(model.calls,1)
            button=next(b for b in app.button if b.label=='Start extraction')
            button.click().run()
            self.assertFalse(app.exception);self.assertEqual(model.calls,2)
            app.run();self.assertEqual(model.calls,2)

    def test_fenced_json_action_keeps_validation(self):
        from mesakumo.agent import parse_action
        raw = json.dumps(action('list_tender_documents',offset=0))
        self.assertEqual(parse_action('```json\n'+raw+'\n```'),json.loads(raw))
        self.assertEqual(parse_action('Let me list the documents.\n\n```json\n'+raw+'\n```'),json.loads(raw))
        with self.assertRaises(ValueError):
            parse_action('```json\n'+raw+'\n```\n```json\n'+raw+'\n```')
        with self.assertRaises(ValueError): parse_action('Explanation: '+raw)
        class FencedModel(Scripted):
            def complete(inner,prompt): return 'Here is the next action.\n```json\n'+super().complete(prompt)+'\n```'
        result=self.runs.ensure(self.project,self.rows,FencedModel(self.sequence()))
        self.assertEqual(result['status'],'completed')

    def test_failure_diagnostics_distinguish_provider_and_json(self):
        from mesakumo.models import ModelUnavailable
        class ProviderFailure:
            def complete(inner,prompt):
                raise ModelUnavailable('Requesty HTTP 403. Check access, model and quota. No candidates saved.')
        result=self.runs.ensure(self.project,self.rows,ProviderFailure())
        self.assertIn('Requesty request failed: Requesty HTTP 403',result['error'])
        self.assertEqual(result['steps'],0)
        class InvalidJSON:
            def complete(inner,prompt): return 'This is not an action'
        result=self.runs.ensure(self.project,self.rows,InvalidJSON(),retry=True)
        self.assertIn('invalid JSON',result['error'])
        self.assertNotIn('HTTP 403',result['error'])
        self.assertEqual(result['attempt'],2)

    def test_structured_logs_and_specific_citation_diagnostic(self):
        model=Scripted([action('submit_requirement_candidates', requirements=[
            {'statement':'Synthetic obligation', 'source_ids':['unknown'],
             'kind':'deliverable', 'constraints':[]}], processed_batch_ids=[], done=True)])
        result=self.runs.ensure(self.project,self.rows,model)
        self.assertEqual(result['status'],'failed')
        self.assertIn('row 1',result['error'])
        self.assertIn('known passage IDs',result['error'])
        events=[json.loads(line) for line in (self.store.path.parent/'extraction.jsonl').read_text().splitlines()]
        self.assertEqual(events[-1]['status'],'failed')
        self.assertEqual(events[-1]['project_id'],self.project.id)
        self.assertEqual(events[-1]['tool'],'submit_requirement_candidates')
        self.assertIn('time',events[-1])
        self.assertNotIn('Synthetic obligation',json.dumps(events))
        self.assertEqual(result['trace'][-1]['stage'],'submitting')

    def test_sidebar_start_from_another_page(self):
        from streamlit.testing.v1 import AppTest
        import shutil
        model=CoverageModel()
        folder='data/test_sidebar_start_ui'
        self.addCleanup(shutil.rmtree,folder,True)
        shutil.rmtree(folder,ignore_errors=True)
        with patch.dict('os.environ',{'MESAKUMO_PARTICIPANT_DIR':self.tmp.name,
                'MESAKUMO_KEEP_SESSION':'yes','MESAKUMO_DATA_DIR':folder,'MESAKUMO_LLM_PROVIDER':'disabled'}), patch(
                'mesakumo.models.configured_model',return_value=model):
            app=AppTest.from_file('mesakumo/streamlit_app.py',default_timeout=30).run()
            app.sidebar.radio[0].set_value('Evidence Matching').run()
            self.assertEqual(model.calls,0)
            next(b for b in app.sidebar.button if b.label=='Start extraction').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(model.calls,1)
            self.assertEqual(app.sidebar.radio[0].value,'Tender Requirements')
