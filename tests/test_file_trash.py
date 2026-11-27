from synthetic_identity import synthetic_email
"""Recoverable deletion, immutable versions, stale writes and tenant boundaries."""
import json

import pytest
from fastapi.testclient import TestClient
import backend.app as module
from backend.storage import Store, document
from test_signup import setup, trusted_user


@pytest.fixture
def local(tmp_path,monkeypatch):
    for key in ('VERCEL','WORKSPACE_PASSWORD','COGNITO_CLIENT_ID','FOLIO_S3_BUCKET'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(module,'store',Store(tmp_path))
    return TestClient(module.app),module.store


def version(doc):
    return {k:doc.get(k,0) for k in ('revision','lifecycle_version')}


def remove(api,url,doc):
    return api.request('DELETE',url,json=version(doc))


def test_delete_restore_preserves_bytes_history_and_rejects_pending_edits(local):
    api,store=local
    doc=next(d for d in api.get('/api/clients/northwind').json()['documents'] if d['ext']=='xlsx')
    url='/api/clients/northwind/documents/'+doc['id']
    original=api.get(url+'/download').content
    payload={'document_id':doc['id'],'revision':1,'changes':[{'sheet':'Income statement','cell':'B3','value':5000000}]}
    proposal=api.post('/api/clients/northwind/proposals',json=payload).json()
    assert api.post('/api/clients/northwind/proposals/'+proposal['id']+'/accept').status_code==200
    pending=api.post('/api/clients/northwind/proposals',json={**payload,'revision':2}).json()
    updated=api.get(url).json();current=api.get(url+'/download').content
    assert remove(api,url,doc).status_code==409  # Old confirmation cannot delete a newer revision.
    deleted=remove(api,url,updated);assert deleted.status_code==200
    trash=deleted.json();assert 'blob' not in trash and 'history' not in trash
    workspace=api.get('/api/clients/northwind').json()
    assert len(workspace['documents'])==6 and workspace['trash']==[trash]
    assert next(p for p in workspace['proposals'] if p['id']==pending['id'])['status']=='rejected'
    for suffix in ('','/download','/download?revision=1','/page/1','/image'):
        assert api.get(url+suffix).status_code==404
    assert api.post('/api/clients/northwind/proposals',json={**payload,'revision':2}).status_code==404
    assert remove(api,url,updated).status_code==409
    assert api.post(url+'/restore',json=version(trash)).status_code==200
    assert api.get(url+'/download').content==current
    assert api.get(url+'/download?revision=1').content==original
    restored=api.get(url).json()
    assert restored['revision']==2 and restored['history']==updated['history']
    assert api.post('/api/clients/northwind/proposals/'+pending['id']+'/accept').status_code==409
    assert remove(api,url,updated).status_code==409  # Delete/restore cycle also invalidates old dialogs.
    assert api.post(url+'/restore',json=version(trash)).status_code==409
    assert api.get('/api/clients/northwind').json()['trash']==[]
    reboot=Store(store.directory);state,_=reboot.read('northwind')
    assert reboot.content(document(state,doc['id']))==current


def test_trash_not_in_source_context_and_restores_to_original_folder(local):
    api,store=local
    client=api.post('/api/clients',json={'name':'Trash context test'}).json()['id']
    base='/api/clients/'+client
    doc=api.post(base+'/upload',files={'files':('notes.txt',b'Only this source contains a test detail.')},data={'folder':'Evidence'}).json()[0]
    assert len(module.source_map(store.read(client)[0])[0])==1
    deleted=remove(api,base+'/documents/'+doc['id'],doc).json()
    assert module.source_map(store.read(client)[0])==([],{},[])
    assert api.post(base+'/documents/'+doc['id']+'/restore',json=version(deleted)).json()['folder']=='Evidence'
    assert len(module.source_map(store.read(client)[0])[0])==1


def test_delete_restore_require_auth_same_origin_and_correct_account(setup):
    fake,client=setup
    alice=trusted_user(fake,client,synthetic_email('alice'));bob=trusted_user(fake,client,synthetic_email('bob'))
    base='/api/clients/northwind'
    doc=alice.post(base+'/upload',files={'files':('private.txt',b'Private account fixture')}).json()[0]
    url=base+'/documents/'+doc['id']
    assert remove(client(),url,doc).status_code==401
    assert client().post(url+'/restore',json=version(doc)).status_code==401
    assert remove(bob,url,doc).status_code==404
    assert alice.request('DELETE',url,json=version(doc),headers={'Origin':'https://evil.example'}).status_code==403
    assert remove(alice,url.replace('northwind','ostara'),doc).status_code==404
    deleted=remove(alice,url,doc).json()
    assert bob.get(base).json()['trash']==[]
    assert bob.post(url+'/restore',json=version(deleted)).status_code==404
    assert alice.post(url+'/restore',json=version(deleted),headers={'Origin':'https://evil.example'}).status_code==403
    assert alice.post(url+'/restore',json=version(deleted)).status_code==200
    # Identical seed IDs in separate accounts must not share deletion state.
    seed=alice.get(base).json()['documents'][0]
    assert remove(alice,base+'/documents/'+seed['id'],seed).status_code==200
    assert bob.get(base+'/documents/'+seed['id']).status_code==200


def test_trash_keeps_storage_quota_and_never_exposes_private_blob_keys(local):
    api,store=local
    def fill(state):
        doc=state['documents'][0]
        state['documents']=[{**doc,'id':str(i),'deleted_at':'2026-11-27T00:00:00Z'} for i in range(100)]
    store.mutate('northwind',fill)
    response=api.post('/api/clients/northwind/upload',files={'files':('over.txt',b'quota')})
    assert response.status_code==400 and '100 files' in response.text
    state=api.get('/api/clients/northwind').json()
    assert not state['documents'] and len(state['trash'])==100
    assert all('blob' not in d and 'history' not in d for d in state['trash'])


@pytest.mark.parametrize('restore_during_answer',[False,True])
def test_inflight_answer_cannot_commit_deleted_sources(local,monkeypatch,restore_during_answer):
    api,store=local
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    doc=api.get('/api/clients/northwind').json()['documents'][0]
    async def response(*args):
        deleted=module.change_document_lifecycle('northwind',doc['id'],module.DocumentVersion(**version(doc)))
        if restore_during_answer:
            module.change_document_lifecycle('northwind',doc['id'],module.DocumentVersion(**version(deleted)),restore=True)
        yield {'type':'result','result':{'answer':'Stale answer must not commit','scope':'client','citations':[],'proposals':[]}}
    monkeypatch.setattr(module,'answer_stream',response)
    result=api.post('/api/clients/northwind/chat',json={'message':'Summarize the sources'})
    events=[json.loads(line[6:]) for line in result.text.splitlines() if line.startswith('data: ')]
    assert events[-1]['type']=='error' and 'files changed' in events[-1]['message']
    assert 'Stale answer must not commit' not in json.dumps(api.get('/api/clients/northwind').json()['chats'])
