import asyncio
import base64
import copy
import hmac
import io
import json
import mimetypes
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .storage import Store, CLIENTS, ROOT, now, uid, digest, document, public_doc, active_documents, storage_usage, Conflict, workspace_scope
from .documents import inspect_document, apply_edits, page_image
from .assistant import answer_stream, validate_result
from .providers import catalog
from . import security, cloud, accounts

store=Store()
app=FastAPI(title='Folio',docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(security.BodyLimit)


@app.middleware('http')
async def boundary(request:Request,call_next):
    identity_token=cloud.identity.set(request.headers.get('x-vercel-oidc-token'))
    scope_token=workspace_scope.set('')
    try:return await request_boundary(request,call_next)
    finally:
        workspace_scope.reset(scope_token)
        cloud.identity.reset(identity_token)


async def request_boundary(request,call_next):
    public=request.url.path in ('/', '/folio.svg', '/api/health', '/api/auth/session', '/api/auth/login',
        '/api/auth/signup','/api/auth/confirm','/api/auth/resend','/api/auth/forgot','/api/auth/reset') or request.url.path.startswith('/assets/')
    if not public and not await asyncio.to_thread(security.authenticated,request,store):
        return JSONResponse({'detail':'Sign in to access this workspace.'},status_code=401,headers={'Cache-Control':'no-store'})
    if not public:workspace_scope.set(getattr(request.state,'workspace_id',''))
    if request.method in ('POST','PUT','PATCH','DELETE'):
        if os.getenv('VERCEL') and not store.cloud:
            return JSONResponse({'detail':'Connect a private Vercel Blob store before saving work. This deployment is read-only until durable storage is configured.'},status_code=503)
        origin=request.headers.get('origin')
        if request.headers.get('sec-fetch-site')=='cross-site' or (origin and (urlparse(origin).netloc!=request.headers.get('host') or urlparse(origin).scheme not in ('http','https'))):
            return JSONResponse({'detail':'Cross-origin changes are not allowed.'},status_code=403)
        if request.cookies.get(security.COOKIE) and not origin:
            return JSONResponse({'detail':'Browser changes require a same-origin request.'},status_code=403)
    try:response=await call_next(request)
    except Conflict as e:return JSONResponse({'detail':str(e)},status_code=409)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Content-Security-Policy']=f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' blob:; connect-src 'self' https://{cloud.bucket()}.s3.us-east-1.amazonaws.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers['Permissions-Policy']='camera=(), microphone=(self), geolocation=()'
    if request.url.path=='/' or request.url.path.startswith('/api'):response.headers['Cache-Control']='no-store'
    return response


class LoginInput(BaseModel):
    password:str=Field(default='',max_length=512)
    username:str=Field(default='',max_length=128)
    challenge_session:str=Field(default='',max_length=4096)
    new_password:str=Field(default='',max_length=512)

@app.get('/api/auth/session')
async def session(request:Request):
    if accounts.enabled():return {'required':True,'mode':'cognito','signup':True,'authenticated':await asyncio.to_thread(security.authenticated,request,store)}
    return {'required':security.configured() or bool(os.getenv('VERCEL')), 'authenticated':await asyncio.to_thread(security.authenticated,request,store)}

@app.post('/api/auth/login')
async def login(body:LoginInput,request:Request):
    if accounts.enabled():
        result=await asyncio.to_thread(accounts.sign_in,store,request,body)
        if 'challenge' in result:return result
        response=JSONResponse({'authenticated':True})
        response.set_cookie(security.COOKIE,result['token'],max_age=result['expires'],httponly=True,secure=bool(os.getenv('VERCEL')) or request.url.scheme=='https',samesite='strict',path='/')
        return response
    if os.getenv('VERCEL'):raise HTTPException(503,'Configure Amazon Cognito before signing in.')
    token=await asyncio.to_thread(security.login,store,request,body.password)
    response=JSONResponse({'authenticated':True})
    response.set_cookie(security.COOKIE,token,max_age=security.TTL,httponly=True,secure=bool(os.getenv('VERCEL')) or request.url.scheme=='https',samesite='strict',path='/')
    return response


class SignupInput(BaseModel):
    email:str=Field(min_length=5,max_length=128)
    name:str=Field(min_length=1,max_length=100)
    password:str=Field(min_length=12,max_length=256)

class AccountInput(BaseModel):
    username:str=Field(min_length=1,max_length=128)

class ConfirmInput(AccountInput):
    code:str=Field(pattern=r'^\d{6}$')

class ResetInput(ConfirmInput):
    password:str=Field(min_length=12,max_length=256)

@app.post('/api/auth/signup')
async def signup(body:SignupInput,request:Request):
    return await asyncio.to_thread(accounts.sign_up,store,request,body)

@app.post('/api/auth/confirm')
async def confirm_signup(body:ConfirmInput,request:Request):
    return await asyncio.to_thread(accounts.confirm_signup,store,request,body)

@app.post('/api/auth/resend')
async def resend_code(body:AccountInput,request:Request):
    return await asyncio.to_thread(accounts.resend_code,store,request,body)

@app.post('/api/auth/forgot')
async def forgot_password(body:AccountInput,request:Request):
    return await asyncio.to_thread(accounts.forgot_password,store,request,body)

@app.post('/api/auth/reset')
async def reset_password(body:ResetInput,request:Request):
    return await asyncio.to_thread(accounts.reset_password,store,request,body)

@app.post('/api/auth/logout')
async def logout(request:Request):
    if accounts.enabled():await asyncio.to_thread(accounts.sign_out,store,request,security.COOKIE)
    else:await asyncio.to_thread(security.logout,store,request)
    response=JSONResponse({'authenticated':False})
    response.delete_cookie(security.COOKIE,path='/',secure=bool(os.getenv('VERCEL')),httponly=True,samesite='strict')
    return response


@app.exception_handler(KeyError)
async def missing(request,exc):return JSONResponse({'detail':'Not found in this client workspace.'},status_code=404)
@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)
@app.exception_handler(Conflict)
async def stale(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)


@lru_cache(maxsize=24)
def parsed_small(data,name):return inspect_document(data,name)

def parsed(data,name):
    return parsed_small(data,name) if len(data)<=2*1024*1024 else inspect_document(data,name)

def inspect(doc):return parsed(store.content(doc),doc['name'])

def source_map(state):
    context=[];sources={};images=[]
    for doc in active_documents(state):
        info=inspect(doc)
        blocks=[]
        for b in info['blocks']:
            sid=doc['id']+'::'+b['locator']
            source={'id':sid,'document_id':doc['id'],'name':doc['name'],'revision':doc['revision'],'hash':doc['hash'],**b}
            sources[sid]=source;blocks.append(source)
        context.append({'document_id':doc['id'],'name':doc['name'],'folder':doc['folder'],'revision':doc['revision'],'blocks':blocks,'fields':info.get('fields',[]),'warnings':info['warnings']})
        for page in info.get('pages',[]):
            if page['scan'] and not page['blocks']:
                images.append({'source_id':doc['id']+f'::page{page["number"]}:image','type':'image/png','data':page_image(store.content(doc),page['number'])})
        if info['kind']=='image':
            raw=store.content(doc)
            from PIL import Image
            im=Image.open(io.BytesIO(raw));im.thumbnail((1600,1600));out=io.BytesIO();im.convert('RGB').save(out,format='JPEG')
            images.append({'source_id':doc['id']+'::image','type':'image/jpeg','data':out.getvalue()})
    if len(images)>20:raise ValueError('Too many scanned pages for one question. Limit this folder to 20 image pages.')
    return context,sources,images


@app.get('/api/health')
def health():return {'status':'ok'}
@app.get('/api/status')
def status(request:Request):
    connections=catalog();connection=connections[0]
    return {'username':getattr(request.state,'username',None),'account_mode':'cognito' if accounts.enabled() else 'local','authentication_required':security.configured() or bool(os.getenv('VERCEL')),'providers':connections,'ai_ready':connection['configured'],'model':connection['model'],'storage':'Vercel private Blob' if store.cloud else ('Temporary preview storage' if os.getenv('VERCEL') else 'Local persistent storage'),'hosted':bool(os.getenv('VERCEL')),'durable':store.cloud or not bool(os.getenv('VERCEL')),'max_upload_mb':50 if cloud.bucket() or not os.getenv('VERCEL') else 4,'direct_upload':bool(cloud.bucket()),'voice_ready':bool(cloud.bucket())}
@app.get('/api/clients')
def clients():return store.clients()

class PasswordChange(BaseModel):
    previous:str=Field(min_length=1,max_length=512)
    proposed:str=Field(min_length=12,max_length=512)

@app.post('/api/auth/password')
async def change_password(body:PasswordChange,request:Request):
    if not accounts.enabled():raise ValueError('Individual accounts are not configured.')
    await asyncio.to_thread(accounts.change_password,request,body.previous,body.proposed,security.COOKIE)
    return {'changed':True}

class ClientInput(BaseModel):
    name:str=Field(min_length=2,max_length=80)

@app.post('/api/clients')
def create_client(body:ClientInput):return store.create_client(body.name)
@app.post('/api/clients/{client}/archive')
def archive_client(client:str,body:ClientInput):return store.archive_client(client,body.name)
@app.get('/api/clients/{client}')
def workspace(client:str):
    state,_=store.read(client)
    return {**{k:v for k,v in state.items() if k not in ('transfers','speech_usage','documents','purge_jobs','chat_usage')},'documents':[public_doc(d) for d in active_documents(state)],'trash':[public_doc(d) for d in state['documents'] if d.get('deleted_at')],'cleanup_pending':sum(j['count'] for j in state.get('purge_jobs',[]))}

class DocumentVersion(BaseModel):
    revision:int=Field(ge=1)
    lifecycle_version:int=Field(default=0,ge=0)

def change_document_lifecycle(client,doc_id,body,restore=False):
    def update(state):
        doc=document(state,doc_id,include_deleted=True)
        if (doc['revision'],doc.get('lifecycle_version',0))!=(body.revision,body.lifecycle_version):
            raise Conflict('This file changed. Refresh the workspace before trying again.')
        if bool(doc.get('deleted_at'))!=restore:
            raise Conflict('This file is already restored.' if restore else 'This file is already in Trash.')
        timestamp=now()
        if restore:doc.pop('deleted_at')
        else:
            doc['deleted_at']=timestamp
            for proposal in state['proposals']:
                if proposal.get('document_id')==doc_id and proposal['status']=='pending':
                    proposal.update(status='rejected',dismissed_at=timestamp,dismissed_reason='File moved to Trash')
        doc['lifecycle_version']=doc.get('lifecycle_version',0)+1
        # Reject an answer still using a source snapshot from before deletion/restoration.
        state['file_epoch']=state.get('file_epoch',0)+1
        state['activity'].insert(0,{'id':uid(),'text':('Restored ' if restore else 'Moved to Trash: ')+doc['name'],'time':timestamp})
        return public_doc(doc)
    return store.mutate(client,update)

@app.delete('/api/clients/{client}/documents/{doc_id}')
def delete_document(client:str,doc_id:str,body:DocumentVersion):
    return change_document_lifecycle(client,doc_id,body)

@app.post('/api/clients/{client}/documents/{doc_id}/restore')
def restore_document(client:str,doc_id:str,body:DocumentVersion):
    return change_document_lifecycle(client,doc_id,body,restore=True)

@app.get('/api/clients/{client}/documents/{doc_id}')
def read_document(client:str,doc_id:str):
    state,_=store.read(client);doc=document(state,doc_id)
    return {**public_doc(doc),**inspect(doc),'history':[{k:v for k,v in h.items() if k!='blob'} for h in doc['history']]}
@app.get('/api/clients/{client}/documents/{doc_id}/download')
def download(client:str,doc_id:str,revision:int|None=None):
    state,_=store.read(client);doc=document(state,doc_id)
    selected=doc
    if revision is not None and revision!=doc['revision']:
        selected=next((h for h in doc['history'] if h['revision']==revision),None)
        if not selected:raise KeyError('Revision not found')
    if selected['blob'].startswith('s3:'):return RedirectResponse(cloud.download_url(selected['blob'][3:],doc['name']),status_code=303,headers={'Cache-Control':'no-store'})
    return Response(store.content(selected),media_type=mimetypes.guess_type(doc['name'])[0] or 'application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(doc['name'])})
@app.get('/api/clients/{client}/documents/{doc_id}/page/{number}')
def pdf_page(client:str,doc_id:str,number:int,revision:int|None=None):
    state,_=store.read(client);doc=document(state,doc_id);selected=doc
    if revision is not None and revision!=doc['revision']:
        selected=next((h for h in doc['history'] if h['revision']==revision),None)
        if not selected:raise KeyError('Revision not found')
    return Response(page_image(store.content(selected),number),media_type='image/png')
@app.get('/api/clients/{client}/documents/{doc_id}/image')
def source_image(client:str,doc_id:str):
    state,_=store.read(client);doc=document(state,doc_id)
    if doc['ext'] not in ('png','jpg','jpeg','webp'):raise ValueError('Not a supported image')
    return Response(store.content(doc),media_type=mimetypes.guess_type(doc['name'])[0])


@app.post('/api/clients/{client}/upload')
async def upload(client:str,files:list[UploadFile]=File(...),folder:str=Form('Inbox')):
    state,_=await asyncio.to_thread(store.read,client)
    if len(files)>10:raise ValueError('Upload up to 10 files at a time.')
    if not re.fullmatch(r'[\w .-]{1,60}',folder) or folder in ('.','..'):raise ValueError('Use a simple folder name.')
    additions=[]
    for f in files:
        name=(f.filename or 'Untitled').replace('\\','/').split('/')[-1]
        if not name or name in ('.','..') or len(name)>200 or re.search(r'[\x00-\x1f\x7f]',name):raise ValueError('Invalid filename')
        cap=(4 if os.getenv('VERCEL') else 50)*1024*1024
        raw=await f.read(cap+1)
        if len(raw)>cap:raise ValueError(f'{name}: exceeds the {cap//1024//1024} MB upload limit.')
        if name.lower().endswith(('.docx','.xlsx')):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if len(z.infolist())>10000 or sum(i.file_size for i in z.infolist())>100*1024*1024:raise ValueError('Office file expands beyond the supported size.')
            except zipfile.BadZipFile:raise ValueError('This Office file is not a valid document package.')
        # Parsing errors are surfaced; original unsupported formats remain downloadable.
        try:await asyncio.to_thread(parsed,raw,name)
        except Exception as e:raise ValueError(f'{name}: could not read this document ({type(e).__name__}).')
        additions.append({'id':uid()[:20],'name':name,'folder':folder,'ext':Path(name).suffix.lower()[1:],'size':len(raw),'revision':1,'hash':digest(raw),'blob':store.save_bytes(client,raw),'updated':now(),'history':[]})
    def update(state):
        count,size=storage_usage(state)
        if count+len(additions)>100 or size+sum(d['size'] for d in additions)>1024*1024*1024:
            raise ValueError('Client folder exceeds 100 files or 1 GB. No files were added.')
        state['documents'].extend(additions)
        state['activity'].insert(0,{'id':uid(),'text':f'Uploaded {len(additions)} file'+('s' if len(additions)!=1 else ''),'time':now()})
    await asyncio.to_thread(store.mutate,client,update)
    return [public_doc(d) for d in additions]


class ProposalInput(BaseModel):
    document_id:str
    revision:int
    title:str=Field(default='Review document changes',max_length=200)
    changes:list[dict]=Field(min_length=1,max_length=500)


def prepare_proposal(client,state,doc_id,changes,title,reason,origin='manual',sources=None):
    doc=document(state,doc_id);before=inspect(doc);sources=sources or {}
    if not isinstance(changes,list) or not 1<=len(changes)<=500:raise ValueError('Provide between 1 and 500 changes.')
    rows=[];unique=set()
    for c in changes:
        if not isinstance(c,dict) or 'value' not in c:raise ValueError('Malformed edit.')
        if origin=='assistant' and any(s not in sources for s in c.get('source_ids',[])):raise ValueError('An edit cited evidence outside this client folder.')
        if isinstance(c['value'],str) and len(c['value'])>12000:raise ValueError('An edit is too long.')
        if doc['ext']=='xlsx':
            if not isinstance(c.get('sheet'),str) or not isinstance(c.get('cell'),str):raise ValueError('Specify an Excel sheet and cell.')
            sheet=next((s for s in before['sheets'] if s['name']==c['sheet']),None)
            if sheet is None:raise ValueError('Unknown worksheet')
            old=next((v for v in sheet['cells'] if v['ref']==c['cell']),{})
            locator=c['sheet']+'!'+c['cell'];value=old.get('input','')
        elif doc['ext']=='docx':
            locator=str(c.get('paragraph',''))
            block=next((b for b in before['blocks'] if b['locator']==locator),None)
            if block is None:raise ValueError('Unknown Word paragraph')
            value=block['text']
        elif doc['ext']=='pdf':
            locator=c.get('field');field=next((f for f in before['fields'] if f['name']==locator),None)
            if not field:raise ValueError('Unknown form field')
            value=field['value']
            if origin=='assistant' and field['type']=='checkbox' and c['value'] in (True,'true','/Yes','Yes'):
                if not c.get('source_ids') or not all(s in sources for s in c['source_ids']):raise ValueError('A proposed checked box needs valid client evidence.')
                if 'attached' in str(locator).lower():raise ValueError('This app does not attach certificates to outgoing forms. The attached-certificate checkbox must remain unchecked.')
        else:raise ValueError('This format cannot be edited in the workspace.')
        if locator in unique:raise ValueError('Multiple changes target the same location.')
        unique.add(locator)
        rows.append({'location':locator,'before':value,'after':c['value'],'sources':[sources[s] for s in c.get('source_ids',[]) if s in sources]})
    new_bytes=apply_edits(store.content(doc),doc['name'],changes)
    after=parsed(new_bytes,doc['name'])
    impacts=[]
    if doc['ext']=='xlsx':
        old_values={s['name']+'!'+c['ref']:c['value'] for s in before['sheets'] for c in s['cells']}
        for sheet in after['sheets']:
            for cell in sheet['cells']:
                loc=sheet['name']+'!'+cell['ref']
                if cell['formula'] and loc not in unique and old_values.get(loc)!=cell['value']:
                    impacts.append({'location':loc,'before':old_values.get(loc),'after':cell['value'],'formula':cell['formula'],'format':cell['format']})
    return {'id':uid(),'document_id':doc_id,'document_name':doc['name'],'base_revision':doc['revision'],'base_hash':doc['hash'],'base_lifecycle':doc.get('lifecycle_version',0),'title':title,'reason':reason,'changes':changes,'diff':rows,'impacts':impacts,'origin':origin,'status':'pending','time':now()}


@app.post('/api/clients/{client}/proposals')
def propose(client:str,body:ProposalInput):
    state,_=store.read(client);doc=document(state,body.document_id)
    if body.revision!=doc['revision']:raise Conflict('This document changed. Reload it before editing.')
    proposal=prepare_proposal(client,state,body.document_id,body.changes,body.title,'Your changes will be saved as a new version.')
    def add(s):
        current=document(s,body.document_id)
        if current['hash']!=proposal['base_hash'] or current.get('lifecycle_version',0)!=proposal['base_lifecycle']:raise Conflict('The file changed while preparing the preview.')
        s['proposals'].insert(0,proposal)
    store.mutate(client,add)
    return proposal


@app.post('/api/clients/{client}/proposals/{proposal_id}/accept')
def accept(client:str,proposal_id:str):
    def update(state):
        p=next((p for p in state['proposals'] if p['id']==proposal_id),None)
        if not p:raise KeyError('Proposal not found')
        if p['status']=='accepted':return p
        if p['status']!='pending':raise Conflict('This proposal has already been dismissed.')
        if p.get('memory'):
            state['memory'].append({'id':uid(),'text':p['memory'],'time':now(),'source':'User instruction'})
        else:
            doc=document(state,p['document_id'])
            if doc['hash']!=p['base_hash'] or doc['revision']!=p['base_revision'] or doc.get('lifecycle_version',0)!=p.get('base_lifecycle',0):raise Conflict('This suggestion is out of date. The document has changed; create a fresh proposal.')
            raw=apply_edits(store.content(doc),doc['name'],p['changes'])
            old={k:v for k,v in doc.items() if k!='history'}
            doc['history'].insert(0,old)
            doc.update(blob=store.save_bytes(client,raw),hash=digest(raw),revision=doc['revision']+1,updated=now(),size=len(raw))
        p['status']='accepted';p['accepted_at']=now()
        state['activity'].insert(0,{'id':uid(),'text':p['title'],'time':now()})
        return p
    return store.mutate(client,update)


@app.post('/api/clients/{client}/proposals/{proposal_id}/reject')
def reject(client:str,proposal_id:str):
    def update(state):
        p=next((p for p in state['proposals'] if p['id']==proposal_id),None)
        if not p:raise KeyError('Proposal not found')
        if p['status']!='pending':raise Conflict('This proposal is already resolved.')
        p['status']='rejected';return p
    return store.mutate(client,update)


class MemoryInput(BaseModel):text:str=Field(min_length=1,max_length=2000)
@app.post('/api/clients/{client}/memory')
def remember(client:str,body:MemoryInput):
    def update(state):
        item={'id':uid(),'text':body.text.strip(),'time':now(),'source':'User instruction'}
        state['memory'].append(item);return item
    return store.mutate(client,update)
@app.delete('/api/clients/{client}/memory/{memory_id}')
def forget(client:str,memory_id:str):
    def update(state):state['memory']=[m for m in state['memory'] if m['id']!=memory_id]
    store.mutate(client,update);return {'ok':True}


class ChatInput(BaseModel):
    provider:Literal["bedrock"]="bedrock"
    message:str=Field(min_length=1,max_length=12000)
    chat_id:str|None=None


def daily_chat_usage(state,day):
    # Migrate pre-counter workspaces without allowing chat deletion to reset limits.
    visible=sum(1 for t in state['chats'] for m in t['messages'] if m['role']=='user' and m.get('time','').startswith(day))
    return max(state.get('chat_usage',{}).get(day,0),visible)


@app.delete('/api/clients/{client}/chats/{chat_id}')
def delete_chat(client:str,chat_id:str):
    def remove(state):
        thread=next((t for t in state['chats'] if t['id']==chat_id),None)
        if not thread:raise KeyError('Chat not found')
        today=now()[:10];state['chat_usage']={today:daily_chat_usage(state,today)}
        ids={p for m in thread['messages'] for p in m.get('proposal_ids',[])}
        ids.update(p['id'] for p in state['proposals'] if p.get('chat_id')==chat_id)
        state['proposals']=[p for p in state['proposals'] if p['id'] not in ids]
        state['chats']=[t for t in state['chats'] if t['id']!=chat_id]
        state['activity'].insert(0,{'id':uid(),'text':'Deleted a chat','time':now()})
        return {'deleted':chat_id,'chat_count':len(state['chats']),'deleted_proposal_ids':sorted(ids)}
    return store.mutate(client,remove)


@app.post('/api/clients/{client}/chat')
async def chat(client:str,body:ChatInput,request:Request):
    state,_=await asyncio.to_thread(store.read,client)
    connection=next(p for p in catalog() if p['id']==body.provider)
    if not connection['configured']:
        raise HTTPException(503,'Claude is not connected. Configure an AWS role, AWS_BEARER_TOKEN_BEDROCK, or a local AWS SDK profile on the server.')
    oidc_token=request.headers.get('x-vercel-oidc-token') if os.getenv('VERCEL') else None
    thread=next((t for t in state['chats'] if t['id']==body.chat_id),None) if body.chat_id else None
    if body.chat_id and not thread:raise KeyError('Chat not found')
    if thread and len(thread['messages'])>=80:raise ValueError('Start a new chat to keep this conversation focused.')
    context,sources,images=await asyncio.to_thread(source_map,state)
    thread_id=body.chat_id or uid()
    question=body.message.strip()
    user_message={'id':uid(),'role':'user','text':question,'time':now()}
    history=[{'role':m['role'],'text':m['text']} for m in thread['messages']] if thread else []
    def begin(s):
        today=now()[:10]
        daily=daily_chat_usage(s,today)
        if daily>=int(os.getenv('DAILY_CHAT_LIMIT','30')):
            raise ValueError('This client has reached the daily chat limit. Please try again tomorrow.')
        t=next((t for t in s['chats'] if t['id']==thread_id),None)
        if not t:
            if body.chat_id:raise KeyError('Chat was deleted')
            t={'id':thread_id,'title':question[:72],'time':now(),'messages':[]};s['chats'].insert(0,t)
        t['messages'].append(user_message)
        s['chat_usage']={today:daily+1}
    await asyncio.to_thread(store.mutate,client,begin)
    async def events():
        def sse(event):return 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
        yield sse({'type':'start','chat_id':thread_id,'message':user_message})
        try:
            async for event in answer_stream(next(c['name'] for c in store.clients() if c['id']==client),question,context,images,history,state['memory'],body.provider,oidc_token):
                if await request.is_disconnected():return
                if event['type'] in ('text','status'):yield sse(event);continue
                result=event['result'];validate_result(result);ids=result.get('citations',[])
                if not isinstance(ids,list) or any(not isinstance(i,str) or i not in sources for i in ids):raise ValueError('A source reference could not be verified. Please retry the question.')
                citations=[sources[i] for i in ids]
                proposals=[];warnings=[]
                for p in result.get('proposals',[])[:8]:
                    try:proposals.append(prepare_proposal(client,state,p['document_id'],p['changes'],p['title'],p['reason'],'assistant',sources))
                    except (ValueError,KeyError,TypeError) as e:warnings.append('An edit was withheld: '+str(e))
                if result.get('memory') and isinstance(result['memory'],str):
                    proposals.append({'id':uid(),'title':'Remember this client detail','reason':'This will be available in future chats for this client.','memory':result['memory'][:2000],'diff':[{'location':'Client memory','before':'','after':result['memory'][:2000]}],'status':'pending','origin':'assistant','time':now()})
                for proposal in proposals:proposal['chat_id']=thread_id
                message={'id':uid(),'role':'assistant','text':result['answer'],'provider':body.provider,'model':connection['model'],'scope':result.get('scope','client'),'citations':citations,'proposal_ids':[p['id'] for p in proposals],'warnings':warnings,'time':now()}
                def finish(s):
                    if s.get('file_epoch',0)!=state.get('file_epoch',0):raise Conflict('The client files changed while answering. Please ask again using the current files.')
                    t=next((t for t in s['chats'] if t['id']==thread_id),None)
                    if not t:raise Conflict('This chat was deleted. Start a new conversation.')
                    t['messages'].append(message);s['proposals'][0:0]=proposals
                await asyncio.to_thread(store.mutate,client,finish)
                yield sse({'type':'done','chat_id':thread_id,'message':message,'proposals':proposals})
        except asyncio.CancelledError:raise
        except Exception as e:
            text=str(e) if isinstance(e,(ValueError,Conflict)) else 'The request could not finish. Please retry; no edits were applied.'
            failure={'id':uid(),'role':'assistant','text':text,'error':True,'time':now()}
            def failed(s):
                t=next((t for t in s['chats'] if t['id']==thread_id),None)
                if t:t['messages'].append(failure)
            await asyncio.to_thread(store.mutate,client,failed)
            yield sse({'type':'error','message':text})
    return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})


from . import trash
trash.install(app,lambda:store,parsed_small.cache_clear)

if (ROOT/'dist'/'assets').exists():app.mount('/assets',StaticFiles(directory=ROOT/'dist'/'assets'),name='assets')
@app.get('/folio.svg')
def favicon():return FileResponse(ROOT/'public'/'folio.svg')
@app.get('/')
def home():
    if not (ROOT/'dist'/'index.html').exists():return Response('Frontend not built. Run npm run build.',status_code=503)
    return FileResponse(ROOT/'dist'/'index.html')

from .transfers import install as install_transfers
install_transfers(app, lambda: store, parsed)
