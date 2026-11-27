import asyncio
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
import backend.app as module
from backend import security
from backend.storage import Store, document
from backend.documents import Calculator, inspect_document
from synthetic_identity import synthetic_password

PASSWORD = synthetic_password()


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('WORKSPACE_PASSWORD', PASSWORD)
    monkeypatch.setattr(module, 'store', Store(tmp_path))
    return TestClient(module.app, base_url='https://testserver'), module.store


def sign_in(api):
    response=api.post('/api/auth/login',json={'password':PASSWORD},headers={'Origin':'https://testserver'})
    assert response.status_code==200
    api.headers['Origin']='https://testserver'
    return response


def test_auth_covers_every_data_and_mutation_route(secured):
    api,store=secured
    state,_=store.read('northwind');d=state['documents'][0]['id']
    urls=['/api/clients','/api/status','/api/clients/northwind',f'/api/clients/northwind/documents/{d}',f'/api/clients/northwind/documents/{d}/download',f'/api/clients/northwind/documents/{d}/page/1']
    for url in urls:assert api.get(url).status_code==401,url
    for suffix in ['memory','upload','chat','proposals','proposals/forged/accept','proposals/forged/reject']:
        assert api.post('/api/clients/northwind/'+suffix,json={}).status_code==401,suffix
    assert api.get('/api/health').status_code==200
    assert api.get('/api/auth/session').json()=={'required':True,'authenticated':False}
    basic = base64.b64encode(('reviewer:' + PASSWORD).encode()).decode()
    assert api.get('/api/clients',headers={'Authorization':'Basic ' + basic}).status_code==401


def test_login_cookie_restart_logout_revocation_and_password_rotation(secured,monkeypatch):
    api,store=secured
    assert api.post('/api/auth/login',json={'password':'wrong'}).status_code==401
    response=sign_in(api);cookie=api.cookies.get(security.COOKIE)
    flags=response.headers['set-cookie'].lower()
    assert all(x in flags for x in ['httponly','secure','samesite=strict','max-age=28800'])
    assert 'password' not in response.text and cookie not in json.dumps(store.auth_state())
    monkeypatch.setattr(module,'store',Store(store.directory))
    assert api.get('/api/clients').status_code==200
    assert api.post('/api/auth/logout').status_code==200
    api.cookies.set(security.COOKIE,cookie)
    assert api.get('/api/clients').status_code==401
    api.cookies.clear();sign_in(api)
    monkeypatch.setenv('WORKSPACE_PASSWORD',synthetic_password())
    assert api.get('/api/clients').status_code==401


def test_expired_and_forged_sessions_fail_closed(secured,monkeypatch):
    api,store=secured;sign_in(api)
    issued=int(api.cookies.get(security.COOKIE).split('.')[0])
    monkeypatch.setattr(security.time,'time',lambda: issued+security.TTL+2)
    assert api.get('/api/clients').status_code==401
    api.cookies.clear();api.cookies.set(security.COOKIE,'9999999999.fake.forged')
    assert api.get('/api/clients').status_code==401


time_anchor=time.time()


def test_login_throttling_is_durable(secured,monkeypatch):
    api,store=secured
    for _ in range(10):assert api.post('/api/auth/login',json={'password':'wrong'}).status_code==401
    monkeypatch.setattr(module,'store',Store(store.directory))
    assert api.post('/api/auth/login',json={'password':PASSWORD}).status_code==429


def test_csrf_and_security_headers(secured):
    api,_=secured;sign_in(api)
    for headers in [{'Origin':'https://evil.example'},{'Origin':'null'},{'Origin':'https://testserver','Sec-Fetch-Site':'cross-site'}]:
        assert api.post('/api/clients/northwind/memory',json={'text':'forged'},headers=headers).status_code==403
    del api.headers['Origin']
    assert api.post('/api/clients/northwind/memory',json={'text':'forged'}).status_code==403
    response=api.get('/')
    assert "img-src 'self' data:" in response.headers['content-security-policy']
    assert "connect-src 'self'" in response.headers['content-security-policy']
    assert response.headers['x-content-type-options']=='nosniff'
    assert api.get('/api/clients/_auth').status_code==404
    assert api.get('/openapi.json').status_code==404


def test_request_size_limit_and_malicious_uploads(secured):
    api,_=secured;sign_in(api)
    assert api.post('/api/clients/northwind/memory',content=b'x'*(1024*1024+1),headers={'Content-Type':'application/json'}).status_code==413
    assert api.post('/api/clients/northwind/upload',files={'files':('bad.docx',b'not a zip')}).status_code==400
    assert api.post('/api/clients/northwind/upload',files={'files':('bad.png',b'not an image')}).status_code==400
    response=api.post('/api/clients/northwind/upload',files={'files':('../../untrusted.html',b'<script>alert(1)</script>')})
    assert response.status_code==200
    doc=response.json()[0];assert doc['name']=='untrusted.html'
    download=api.get('/api/clients/northwind/documents/'+doc['id']+'/download')
    assert download.headers['content-disposition'].startswith('attachment;')
    assert download.headers['x-content-type-options']=='nosniff'
    assert api.post('/api/clients/northwind/upload',files={'files':('a.txt',b'x')},data={'folder':'../ostara'}).status_code==400


@pytest.mark.parametrize('formula',['="x"*1000000000','=SUM(A1:XFD1048576)','=2^1000000','=2^100^100','=REF(999)','=1e309','=ROUND(1,1000000000)'])
def test_formula_resource_attacks_are_bounded(formula):
    wb=Workbook();ws=wb.active;ws['B1']=formula
    value=Calculator(wb).cell(ws.title,'B1')
    assert isinstance(value,(str,int,float))
    assert value in ('#UNSUPPORTED!','#LIMIT!',1.0)


def test_formula_external_edits_and_out_of_bounds_rejected(secured):
    api,store=secured;sign_in(api)
    state,_=store.read('northwind');doc=next(d for d in state['documents'] if d['ext']=='xlsx')
    for value,cell in [('=cmd|\'/C calc\'!A1','B3'),('=WEBSERVICE("https://evil.example")','B3'),('=HYPERLINK("https://evil.example")','B3'),('=INDIRECT("A1")','B3'),(1,'XFD99999')]:
        r=api.post('/api/clients/northwind/proposals',json={'document_id':doc['id'],'revision':1,'changes':[{'sheet':'Income statement','cell':cell,'value':value}]})
        assert r.status_code==400,(cell,value,r.text)
    assert api.get('/api/clients/northwind').json()['proposals']==[]


def test_all_cross_client_targets_and_forged_ai_edits(secured,monkeypatch):
    api,store=secured;sign_in(api);monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    other,_=store.read('ostara');foreign=next(d for d in other['documents'] if d['ext']=='docx')
    for suffix in ['', '/download','/download?revision=1','/page/1','/image']:
        assert api.get('/api/clients/northwind/documents/'+foreign['id']+suffix).status_code==404
    p=api.post('/api/clients/ostara/proposals',json={'document_id':foreign['id'],'revision':1,'changes':[{'paragraph':'p0','value':'test'}]}).json()
    for action in ['accept','reject']:
        assert api.post('/api/clients/northwind/proposals/'+p['id']+'/'+action).status_code==404
    async def forged(*args):
        yield {'type':'result','result':{'answer':'Forged edit','scope':'client','citations':[],'proposals':[{'document_id':foreign['id'],'title':'forged','reason':'forged','changes':[{'paragraph':'p0','value':'stolen'}]}]}}
    monkeypatch.setattr(module,'answer_stream',forged)
    r=api.post('/api/clients/northwind/chat',json={'message':'hello'})
    assert 'withheld' in r.text and not api.get('/api/clients/northwind').json()['proposals']
    chat=api.get('/api/clients/northwind').json()['chats'][0]
    assert api.post('/api/clients/ostara/chat',json={'message':'continue','chat_id':chat['id']}).status_code==404


def test_concurrent_acceptance_applies_one_version(secured):
    api,store=secured;sign_in(api)
    state,_=store.read('northwind');doc=next(d for d in state['documents'] if d['ext']=='xlsx')
    proposal=api.post('/api/clients/northwind/proposals',json={'document_id':doc['id'],'revision':1,'changes':[{'sheet':'Income statement','cell':'B3','value':5000000}]}).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:api.post('/api/clients/northwind/proposals/'+proposal['id']+'/accept').status_code,range(2)))
    assert results==[200,200]
    state,_=store.read('northwind');changed=document(state,doc['id'])
    assert changed['revision']==2 and len(changed['history'])==1


def test_created_client_is_empty_isolated_and_persistent(secured,monkeypatch):
    api,store=secured;sign_in(api)
    r=api.post('/api/clients',json={'name':'Assessment Fixture'})
    assert r.status_code==200
    client=r.json()['id'];state=api.get('/api/clients/'+client).json()
    assert all(state[k]==[] for k in ('documents','chats','memory','proposals'))
    assert api.post('/api/clients',json={'name':'assessment fixture'}).status_code==400
    api.post('/api/clients/'+client+'/memory',json={'text':'Only this new client'})
    monkeypatch.setattr(module,'store',Store(store.directory))
    assert any(c['id']==client for c in api.get('/api/clients').json())
    assert len(api.get('/api/clients/'+client).json()['memory'])==1
    assert not api.get('/api/clients/northwind').json()['memory']


def test_malformed_model_edits_are_rejected_before_persistence(secured,monkeypatch):
    api,store=secured;sign_in(api);monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    async def malformed(*args):
        yield {'type':'result','result':{'answer':'Bad shape','scope':'client','citations':[],'proposals':{'legal_name':'made up'}}}
    monkeypatch.setattr(module,'answer_stream',malformed)
    r=api.post('/api/clients/northwind/chat',json={'message':'Fill form'})
    assert 'invalid edit list' in r.text
    assert not api.get('/api/clients/northwind').json()['proposals']
