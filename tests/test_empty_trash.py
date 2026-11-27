from synthetic_identity import synthetic_email
import copy
import json
from pathlib import Path

import pytest
from backend import trash
from backend.storage import Store, Conflict, storage_usage, workspace_scope
from test_file_trash import local, version, remove
from test_signup import setup, trusted_user


def snapshot(api,base):
    return {'files':[{k:d[k] for k in ('id','revision','lifecycle_version')} for d in api.get(base).json()['trash']]}


def upload(api,base,name='permanent.txt'):
    return api.post(base+'/upload',files={'files':(name,b'Permanent deletion fixture')}).json()[0]


def test_empty_trash_deletes_local_versions_reclaims_quota_and_survives_restart(local):
    api,store=local;base='/api/clients/northwind'
    original_state,_=store.read('northwind');seed_bytes=[store.content(d) for d in original_state['documents']]
    doc=upload(api,base);url=base+'/documents/'+doc['id']
    # An older immutable revision must be removed too, not just the latest file.
    previous=store.save_bytes('northwind',b'Old saved revision')
    def history(s):
        d=next(d for d in s['documents'] if d['id']==doc['id'])
        d['history']=[{**d,'blob':previous,'revision':0}]
    store.mutate('northwind',history)
    state,_=store.read('northwind');paths=[store.directory/ref for ref in trash.blob_refs([state['documents'][-1]])]
    assert all(p.exists() for p in paths)
    assert remove(api,url,doc).status_code==200
    result=api.post(base+'/trash/empty',json=snapshot(api,base));assert result.status_code==200
    assert result.json()=={'deleted':1,'cleanup_pending':0}
    assert all(not p.exists() for p in paths)
    assert api.get(url+'/download').status_code==404
    assert api.post(url+'/restore',json={'revision':1,'lifecycle_version':1}).status_code==404
    restarted=Store(store.directory);state,_=restarted.read('northwind')
    assert len(state['documents'])==7 and not state['purge_jobs'] and storage_usage(state)[0]==7
    assert [store.content(d) for d in state['documents']]==seed_bytes
    assert api.post(base+'/trash/empty',json={'files':[]}).json()['deleted']==0


@pytest.mark.parametrize('change',['restore','another_delete','delete_restore_delete'])
def test_stale_confirmation_never_purges_changed_trash(local,change):
    api,store=local;base='/api/clients/northwind';doc=upload(api,base);url=base+'/documents/'+doc['id']
    deleted=remove(api,url,doc).json();body=snapshot(api,base)
    if change=='another_delete':
        other=upload(api,base,'other.txt');remove(api,base+'/documents/'+other['id'],other)
    else:
        restored=api.post(url+'/restore',json=version(deleted)).json()
        if change=='delete_restore_delete':remove(api,url,restored)
    before=store.read('northwind')[0]
    assert api.post(base+'/trash/empty',json=body).status_code==409
    assert store.read('northwind')[0]==before
    assert all(store.content(d) for d in before['documents'])


def test_failed_cleanup_is_durable_private_and_retryable(local,monkeypatch):
    api,store=local;base='/api/clients/northwind';doc=upload(api,base)
    remove(api,base+'/documents/'+doc['id'],doc)
    purge=trash.purge_bytes
    def fail(*args):raise RuntimeError('Sensitive storage error must not escape')
    monkeypatch.setattr(trash,'purge_bytes',fail)
    result=api.post(base+'/trash/empty',json=snapshot(api,base));assert result.status_code==200
    assert result.json()=={'deleted':1,'cleanup_pending':1}
    public=api.get(base).json();assert not public['trash'] and public['cleanup_pending']==1
    assert 'purge_jobs' not in public and 'Sensitive' not in json.dumps(public)
    state,_=Store(store.directory).read('northwind');assert storage_usage(state)[0]==8
    monkeypatch.setattr(trash,'purge_bytes',purge)
    assert api.post(base+'/trash/cleanup').json()=={'cleanup_pending':0}
    assert storage_usage(store.read('northwind')[0])[0]==7
    assert api.post(base+'/trash/cleanup').json()=={'cleanup_pending':0}


def test_failed_metadata_commit_never_deletes_bytes(local,monkeypatch):
    api,store=local;base='/api/clients/northwind';doc=upload(api,base)
    remove(api,base+'/documents/'+doc['id'],doc);body=snapshot(api,base)
    state,_=store.read('northwind');stored=state['documents'][-1]
    def fail(*args):raise Conflict('Simulated conditional-write failure')
    monkeypatch.setattr(store,'write',fail)
    assert api.post(base+'/trash/empty',json=body).status_code==409
    assert store.content(stored)==b'Permanent deletion fixture'
    assert store.read('northwind')[0]==state


def test_empty_trash_and_cleanup_enforce_auth_csrf_and_account_client_scope(setup):
    fake,client=setup;a=trusted_user(fake,client,synthetic_email('a'));b=trusted_user(fake,client,synthetic_email('b'))
    base='/api/clients/northwind';doc=upload(a,base);remove(a,base+'/documents/'+doc['id'],doc);body=snapshot(a,base)
    assert client().post(base+'/trash/empty',json=body).status_code==401
    assert client().post(base+'/trash/cleanup').status_code==401
    assert b.post(base+'/trash/empty',json=body).status_code==409
    assert a.post('/api/clients/ostara/trash/empty',json=body).status_code==409
    for endpoint in ('empty','cleanup'):
        assert a.post(base+'/trash/'+endpoint,json=body,headers={'Origin':'https://evil.example'}).status_code==403
    assert a.post(base+'/trash/empty',json=body).json()['cleanup_pending']==0
    assert len(b.get(base).json()['documents'])==7


def test_seed_purge_removes_only_this_accounts_reference(setup):
    fake,client=setup;a=trusted_user(fake,client,synthetic_email('a'));b=trusted_user(fake,client,synthetic_email('b'));base='/api/clients/northwind'
    doc=a.get(base).json()['documents'][0];url=base+'/documents/'+doc['id'];original=b.get(url+'/download').content
    remove(a,url,doc)
    assert a.post(base+'/trash/empty',json=snapshot(a,base)).json()=={'deleted':1,'cleanup_pending':0}
    assert a.get(url).status_code==404 and b.get(url+'/download').content==original
    assert len(a.get(base).json()['documents'])==6


def test_s3_cleanup_deletes_all_exact_versions_and_markers_only(tmp_path,monkeypatch):
    store=Store(tmp_path);target='files/northwind/'+'a'*32;other=target+'f'
    pages=[{'Versions':[{'Key':target,'VersionId':'v1'},{'Key':other,'VersionId':'foreign'}]},
           {'Versions':[{'Key':target,'VersionId':'v2'}],'DeleteMarkers':[{'Key':target,'VersionId':'marker'}]}]
    class S3:
        calls=[]
        def get_paginator(self,operation):assert operation=='list_object_versions';return self
        def paginate(self,**kwargs):assert kwargs=={'Bucket':'test-bucket','Prefix':'files/northwind/'};return iter(pages)
        def delete_objects(self,**kwargs):self.calls.append(kwargs);return {}
    s3=S3();monkeypatch.setattr(trash.cloud,'client',lambda _:s3);monkeypatch.setattr(trash.cloud,'bucket',lambda:'test-bucket')
    trash.purge_bytes(store,'northwind',['s3:'+target])
    assert s3.calls[0]['Delete']['Objects']==[{'Key':target,'VersionId':v} for v in ['v1','v2','marker']]
    s3.delete_objects=lambda **kwargs:{'Errors':[{'Code':'AccessDenied'}]}
    with pytest.raises(RuntimeError):trash.purge_bytes(store,'northwind',['s3:'+target])
    with pytest.raises(ValueError):trash.purge_bytes(store,'ostara',['s3:'+target])
    with pytest.raises(ValueError):trash.purge_bytes(store,'northwind',['folio/files/northwind/../../secret'])


def test_shared_blob_reference_is_retained_until_last_document_is_purged(local):
    api,store=local;base='/api/clients/northwind';doc=upload(api,base)
    def duplicate(s):
        twin=copy.deepcopy(s['documents'][-1]);twin['id']='another-document';s['documents'].append(twin)
    store.mutate('northwind',duplicate)
    remove(api,base+'/documents/'+doc['id'],doc)
    assert api.post(base+'/trash/empty',json=snapshot(api,base)).json()['cleanup_pending']==0
    assert api.get(base+'/documents/another-document/download').content==b'Permanent deletion fixture'
