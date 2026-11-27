import io
import json
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from docx import Document
from pypdf import PdfReader
import backend.app as module
from backend.storage import Store, CLIENTS, document, digest, Conflict
from backend.documents import inspect_document, apply_edits, Calculator, word_paragraphs


@pytest.fixture
def env(tmp_path,monkeypatch):
    [monkeypatch.delenv(key,raising=False) for key in ('AWS_BEARER_TOKEN_BEDROCK','AWS_BEDROCK_ENABLED','FOLIO_AWS_ROLE_ARN')]
    monkeypatch.delenv('WORKSPACE_PASSWORD',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.setattr(module,'store',Store(tmp_path))
    return TestClient(module.app),module.store


def pick(store,client,fragment):
    state,_=store.read(client)
    return next(d for d in state['documents'] if fragment in d['name'])


def test_all_sources_and_real_formula_results(env):
    api,store=env
    expected={'northwind':7,'ostara':6,'corviq':4}
    for client,count in expected.items():
        state,_=store.read(client);assert len(state['documents'])==count
        for d in state['documents']:
            info=inspect_document(store.content(d),d['name']);assert info['kind']!='other'
    doc=pick(store,'northwind','Management accounts');info=inspect_document(store.content(doc),doc['name'])
    cells={c['ref']:c for c in info['sheets'][0]['cells']}
    assert cells['B5']['value']==6018750
    assert cells['B8']['value']==pytest.approx(2105970/6018750)
    debtors={c['ref']:c for c in info['sheets'][1]['cells']}
    assert debtors['C10']['value']==231720
    assert debtors['C12']['value']=='#REF!'


def test_review_accept_recalculate_export_and_restart(env):
    api,store=env;d=pick(store,'northwind','Management accounts');url=f'/api/clients/northwind/documents/{d["id"]}'
    original=api.get(url+'/download').content
    p=api.post('/api/clients/northwind/proposals',json={'document_id':d['id'],'revision':1,'changes':[{'sheet':'Income statement','cell':'B3','value':5000000}]}).json()
    assert api.get(url+'/download').content==original
    assert next(i for i in p['impacts'] if i['location']=='Income statement!B5')['after']==6206450
    assert api.post('/api/clients/northwind/proposals/'+p['id']+'/accept').status_code==200
    read=api.get(url).json();assert read['revision']==2
    cells={c['ref']:c for c in read['sheets'][0]['cells']}
    assert cells['B5']['value']==6206450
    export=api.get(url+'/download').content;wb=load_workbook(io.BytesIO(export))
    assert wb['Income statement']['B5'].value=='=SUM(B3:B4)'
    assert wb['Income statement']['B3'].value==5000000
    assert api.get(url+'/download?revision=1').content==original
    reboot=Store(store.directory);state,_=reboot.read('northwind')
    assert document(state,d['id'])['revision']==2
    # Accept is idempotent.
    assert api.post('/api/clients/northwind/proposals/'+p['id']+'/accept').status_code==200
    assert api.get(url).json()['revision']==2


def test_stale_proposal_cannot_overwrite_newer_changes(env):
    api,store=env;d=pick(store,'northwind','Management accounts')
    def proposal(value):return api.post('/api/clients/northwind/proposals',json={'document_id':d['id'],'revision':1,'changes':[{'sheet':'Income statement','cell':'B3','value':value}]}).json()
    first,second=proposal(5000000),proposal(5500000)
    assert api.post('/api/clients/northwind/proposals/'+first['id']+'/accept').status_code==200
    assert api.post('/api/clients/northwind/proposals/'+second['id']+'/accept').status_code==409


def test_word_roundtrip_retains_headings_numbering(env):
    api,store=env;d=pick(store,'northwind','Renewal call');raw=store.content(d);before=Document(io.BytesIO(raw))
    ps=word_paragraphs(before);target=next(i for i,p in enumerate(ps) if '45 days' in p.text)
    output=apply_edits(raw,d['name'],[{'paragraph':f'p{target}','value':'From 1 November 2026, invoices are due within 50 days.'}])
    after=Document(io.BytesIO(output));afterps=word_paragraphs(after)
    assert '50 days' in afterps[target].text
    assert [p.style.name for p in ps]==[p.style.name for p in afterps]
    assert [p._p.pPr.xml if p._p.pPr is not None else None for p in ps]==[p._p.pPr.xml if p._p.pPr is not None else None for p in afterps]


def test_pdf_fields_remain_interactive_and_truthful(env):
    api,store=env;d=pick(store,'northwind','Supplier');raw=store.content(d)
    output=apply_edits(raw,d['name'],[{'field':'legal_name','value':'Northwind Trading (Pty) Ltd'},{'field':'vat_registered','value':True}])
    fields=PdfReader(io.BytesIO(output)).get_fields()
    assert len(fields)==12
    assert fields['legal_name']['/V']=='Northwind Trading (Pty) Ltd'
    assert fields['vat_registered']['/V']=='/Yes'
    assert fields['bbbee_certificate_attached']['/V']=='/Off'
    assert fields['completed_by']['/V']==''


def test_client_boundary_applies_to_reads_downloads_edits_and_memory(env):
    api,store=env;d=pick(store,'ostara','Payroll')
    assert api.get('/api/clients/northwind/documents/'+d['id']).status_code==404
    assert api.get('/api/clients/northwind/documents/'+d['id']+'/download').status_code==404
    assert api.post('/api/clients/northwind/proposals',json={'document_id':d['id'],'revision':1,'changes':[{'sheet':'October 2026','cell':'B3','value':1}]}).status_code==404
    api.post('/api/clients/northwind/memory',json={'text':'Contact is Alex Test'})
    assert api.get('/api/clients/ostara').json()['memory']==[]
    assert len(api.get('/api/clients/northwind').json()['memory'])==1
    state,_=store.read('northwind');context,sources,images=module.source_map(state)
    assert all('Ostara' not in json.dumps(c) for c in context)
    assert len(images)==1


def test_injection_remains_data_and_is_flagged(env):
    api,store=env;d=pick(store,'ostara','Board minutes');info=inspect_document(store.content(d),d['name'])
    assert any('instructions directed' in w for w in info['warnings'])
    assert any('ignore your earlier' in b['text'] for b in info['blocks'])


def test_unknown_binary_upload_download_persists(env):
    api,store=env;raw=b'\x00\x01fictional-opaque-format\xff'
    result=api.post('/api/clients/corviq/upload',files=[('files',('archive.custom',raw,'application/octet-stream'))],data={'folder':'Inbox'})
    assert result.status_code==200
    d=result.json()[0]
    assert api.get('/api/clients/corviq/documents/'+d['id']+'/download').content==raw
    assert api.get('/api/clients/northwind/documents/'+d['id']).status_code==404


def test_reject_and_missing_key_are_honest(env):
    api,store=env
    r=api.post('/api/clients/northwind/chat',json={'message':'What is the gross margin?'})
    assert r.status_code==503 and 'AWS' in r.json()['detail']
    assert api.get('/api/clients/northwind').json()['chats']==[]


def test_ai_checked_box_requires_evidence_and_no_false_attachment(env):
    api,store=env;state,_=store.read('northwind');d=pick(store,'northwind','Supplier')
    with pytest.raises(ValueError,match='evidence'):
        module.prepare_proposal('northwind',state,d['id'],[{'field':'vat_registered','value':True}],'Fill','Fill','assistant',{})
    with pytest.raises(ValueError,match='attach'):
        module.prepare_proposal('northwind',state,d['id'],[{'field':'bbbee_certificate_attached','value':True,'source_ids':['ok']}],'Fill','Fill','assistant',{'ok':{}})


def test_unsafe_formulas_and_unknown_fields_are_rejected(env):
    api,store=env;d=pick(store,'northwind','Management')
    with pytest.raises(ValueError):apply_edits(store.content(d),d['name'],[{'sheet':'Income statement','cell':'B3','value':'=WEBSERVICE("https://example.com")'}])
    pdf=pick(store,'northwind','Supplier')
    with pytest.raises(ValueError):apply_edits(store.content(pdf),pdf['name'],[{'field':'not-a-field','value':'test'}])


def test_compare_and_swap_detects_concurrent_state_write(env):
    api,store=env;state,v=store.read('northwind');other=Store(store.directory)
    store.write('northwind',state,v)
    with pytest.raises(Conflict):other.write('northwind',state,v)


def test_streamed_answer_persists_valid_citations_and_pending_edits(env,monkeypatch):
    api,store=env;monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    d=pick(store,'northwind','Management')
    async def simulated(client_name,question,context,images,history,memory,provider,oidc_token):
        assert provider=='bedrock' and oidc_token is None
        assert client_name=='Northwind Trading'
        assert all('Ostara' not in json.dumps(item) for item in context)
        sid=d['id']+'::Income statement!B8'
        yield {'type':'text','text':'Gross margin is 34.99%.'}
        yield {'type':'result','result':{'answer':'Gross margin is 34.99% [1].','scope':'client','citations':[sid],'proposals':[{'document_id':d['id'],'title':'Add VAT','reason':'Requested column','changes':[{'sheet':'Debtors','cell':'F2','value':'VAT at 15%'},{'sheet':'Debtors','cell':'F3','value':'=C3*15%'}]}]}}
    monkeypatch.setattr(module,'answer_stream',simulated)
    r=api.post('/api/clients/northwind/chat',json={'message':'What is the margin, and add a VAT column to debtors?'})
    assert r.status_code==200
    events=[json.loads(s[6:]) for s in r.text.split('\n\n') if s.startswith('data: ')]
    final=next(e for e in events if e['type']=='done')
    assert final['message']['citations'][0]['locator']=='Income statement!B8'
    state,_=store.read('northwind')
    assert len(state['chats'][0]['messages'])==2
    assert document(state,d['id'])['revision']==1
    p=state['proposals'][0];assert p['status']=='pending'
    assert api.post('/api/clients/northwind/proposals/'+p['id']+'/accept').status_code==200
    changed=api.get('/api/clients/northwind/documents/'+d['id']).json()
    assert next(c for c in changed['sheets'][1]['cells'] if c['ref']=='F3')['value']==27630


def test_stream_rejects_forged_cross_client_citation(env,monkeypatch):
    api,store=env;monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    other=pick(store,'ostara','Payroll')
    async def simulated(*args):
        yield {'type':'result','result':{'answer':'This is a forged reference.','scope':'client','citations':[other['id']+'::October 2026!B8'],'proposals':[]}}
    monkeypatch.setattr(module,'answer_stream',simulated)
    r=api.post('/api/clients/northwind/chat',json={'message':'Show payroll'})
    assert 'source reference could not be verified' in r.text
    state,_=store.read('northwind')
    assert state['chats'][0]['messages'][-1]['error']
    assert state['proposals']==[]


def test_hosted_writes_require_durable_storage(env,monkeypatch):
    api,store=env;monkeypatch.setenv('VERCEL','1')
    monkeypatch.setattr(module.security,'authenticated',lambda request,store:True)
    r=api.post('/api/clients/northwind/memory',json={'text':'Do not lose this'})
    assert r.status_code==503
    assert api.get('/api/clients/northwind').json()['memory']==[]


def test_blob_state_uses_origin_reads_and_conditional_writes(env,monkeypatch):
    import httpx
    import backend.storage as storage
    api,store=env;store.cloud=True
    monkeypatch.setenv('BLOB_READ_WRITE_TOKEN','vercel_blob_rw_TESTSTORE_not-a-real-key')
    observed=[]
    def get(url,**kwargs):
        observed.append(('get',url,kwargs))
        return httpx.Response(200,json={'documents':[]},headers={'etag':'"v7"'},request=httpx.Request('GET',url))
    def put(url,**kwargs):
        observed.append(('put',url,kwargs))
        return httpx.Response(200,json={'etag':'"v8"'},request=httpx.Request('PUT',url))
    monkeypatch.setattr(storage.httpx,'get',get);monkeypatch.setattr(storage.httpx,'put',put)
    state,version=store.read('northwind');store.write('northwind',state,version)
    assert observed[0][2]['params']=={'cache':'0'}
    assert observed[0][2]['headers']['Accept-Encoding']=='identity'
    assert '.private.blob.vercel-storage.com/' in observed[0][1]
    assert observed[1][2]['headers']['x-if-match']=='"v7"'
    assert observed[1][2]['headers']['x-vercel-blob-access']=='private'


def test_daily_limit_bounds_public_api_spend(env,monkeypatch):
    api,store=env;monkeypatch.setenv('AWS_BEDROCK_ENABLED','1');monkeypatch.setenv('DAILY_CHAT_LIMIT','0')
    r=api.post('/api/clients/northwind/chat',json={'message':'hello'})
    assert r.status_code==400 and 'daily chat limit' in r.json()['detail']
