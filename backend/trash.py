"""Confirmed permanent deletion with durable, retryable storage cleanup."""
import re

import httpx
from pydantic import BaseModel, Field
from . import cloud
from .storage import Conflict, now, uid


class TrashFile(BaseModel):
    id:str=Field(min_length=1,max_length=64)
    revision:int=Field(ge=1)
    lifecycle_version:int=Field(ge=1)


class EmptyTrash(BaseModel):
    files:list[TrashFile]=Field(max_length=100)


def blob_refs(documents):
    return {version['blob'] for doc in documents for version in [doc,*doc.get('history',[])]}


def owned_refs(store,client,refs):
    """Only immutable object keys belonging to this account AND client may be removed."""
    scope=re.escape(store.scoped_key(client))
    for ref in refs:
        if ref.startswith('seed:'):continue  # Bundled originals are shared across accounts.
        if not re.fullmatch(r'(?:s3:files/|folio/files/)'+scope+r'/[a-f0-9]{32}(?:/[a-f0-9]{32})?',ref):
            raise ValueError('Storage cleanup refused a file outside this client.')
    return sorted(ref for ref in refs if not ref.startswith('seed:'))


def purge_bytes(store,client,refs):
    refs=owned_refs(store,client,refs)
    s3keys={ref[3:] for ref in refs if ref.startswith('s3:')}
    if s3keys:
        s3=cloud.client('s3');prefix='files/'+store.scoped_key(client)+'/'
        versions=[]
        for page in s3.get_paginator('list_object_versions').paginate(Bucket=cloud.bucket(),Prefix=prefix):
            versions.extend({'Key':v['Key'],'VersionId':v['VersionId']} for v in page.get('Versions',[])+page.get('DeleteMarkers',[]) if v['Key'] in s3keys)
        for offset in range(0,len(versions),1000):
            result=s3.delete_objects(Bucket=cloud.bucket(),Delete={'Objects':versions[offset:offset+1000],'Quiet':True})
            if result.get('Errors'):raise RuntimeError('Some stored file versions could not be removed.')
    paths=[ref for ref in refs if not ref.startswith('s3:')]
    if paths and store.cloud:
        import os
        # Same POST /delete protocol used by the official @vercel/blob del helper.
        response=httpx.post('https://vercel.com/api/blob/delete',json={'urls':paths},headers={
            'Authorization':'Bearer '+os.environ['BLOB_READ_WRITE_TOKEN'],'x-api-version':'13'},timeout=45)
        response.raise_for_status()
    elif paths:
        root=(store.directory/'folio/files'/store.scoped_key(client)).resolve()
        for key in paths:
            path=(store.directory/key).resolve()
            if not path.is_relative_to(root):raise ValueError('Storage cleanup refused an invalid path.')
            path.unlink(missing_ok=True)


def install(app,get_store,clear_cache):
    def cleanup(client):
        store=get_store();state,_=store.read(client)
        for job in state.get('purge_jobs',[]):
            try:
                purge_bytes(store,client,job['refs'])
            except Exception:
                # The committed job survives a storage outage and can be retried safely.
                # Do not return private keys, credential errors or provider payloads.
                continue
            def finish(s):
                s['purge_jobs']=[p for p in s.get('purge_jobs',[]) if p['id']!=job['id']]
            store.mutate(client,finish)
        state,_=store.read(client)
        return {'cleanup_pending':sum(j['count'] for j in state.get('purge_jobs',[]))}

    @app.post('/api/clients/{client}/trash/empty')
    def empty(client:str,body:EmptyTrash):
        store=get_store()
        expected=sorted((d.id,d.revision,d.lifecycle_version) for d in body.files)
        def detach(state):
            trash=[d for d in state['documents'] if d.get('deleted_at')]
            actual=sorted((d['id'],d['revision'],d.get('lifecycle_version',0)) for d in trash)
            if expected!=actual:raise Conflict('Trash changed since you opened this confirmation. Review the current files and try again.')
            if not trash:return 0
            ids={d['id'] for d in trash}
            retained=[d for d in state['documents'] if d['id'] not in ids]
            refs=owned_refs(store,client,blob_refs(trash)-blob_refs(retained))
            state.setdefault('purge_jobs',[]).append({'id':uid(),'refs':refs,'count':len(trash),'size':sum(d['size'] for d in trash),'time':now()})
            state['documents']=retained
            state['proposals']=[p for p in state['proposals'] if p.get('document_id') not in ids]
            # An old direct-upload completion must not return a purged file as usable.
            state['transfers']=[t for t in state.get('transfers',[]) if t.get('result',{}).get('document',{}).get('id') not in ids]
            state['file_epoch']=state.get('file_epoch',0)+1
            state['activity'].insert(0,{'id':uid(),'text':f'Emptied Trash: {len(trash)} file(s) permanently removed','time':now()})
            return len(trash)
        count=store.mutate(client,detach)  # Commit first; no restore can race byte deletion.
        clear_cache()
        return {'deleted':count,**cleanup(client)}

    @app.post('/api/clients/{client}/trash/cleanup')
    def retry_cleanup(client:str):
        return cleanup(client)  # Only already-confirmed, server-stored jobs; no supplied object keys.
