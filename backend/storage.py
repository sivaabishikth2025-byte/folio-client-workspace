import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parent.parent
load_dotenv(ROOT/'.env')
CLIENTS=[{'id':'northwind','name':'Northwind Trading','legal':'Northwind Trading (Pty) Ltd','initials':'NT','industry':'Wholesale & distribution','color':'sage'},
         {'id':'ostara','name':'Ostara Health','legal':'Ostara Health (Pty) Ltd','initials':'OH','industry':'Healthcare','color':'lavender'},
         {'id':'corviq','name':'Corviq Systems','legal':'Corviq Systems (Pty) Ltd','initials':'CS','industry':'Technology','color':'blue'}]

def now():return datetime.now(timezone.utc).isoformat()
def digest(data):return hashlib.sha256(data).hexdigest()
def uid():return uuid.uuid4().hex
class Conflict(Exception):pass

# Set only from Cognito's validated identity by the request boundary, never a URL/header.
workspace_scope = ContextVar('folio_workspace', default='')


class Store:
    def __init__(self, directory=None):
        self.cloud=bool(os.getenv('BLOB_READ_WRITE_TOKEN')) and directory is None
        self.directory=Path(directory or ('/tmp/folio' if os.getenv('VERCEL') and not self.cloud else os.getenv('DATA_DIR',str(ROOT/'storage'))))
        self.lock=threading.RLock()
        if not self.cloud:
            self.directory.mkdir(parents=True,exist_ok=True)
            with self.db() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS state (client TEXT PRIMARY KEY, value TEXT, version INTEGER)')

    def db(self):
        conn=sqlite3.connect(self.directory/'folio.db',timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        return conn

    def scoped_key(self, key):
        # Sessions from the self-service app client must never authorize old deployments.
        if key=='_auth' and os.getenv('COGNITO_CLIENT_ID'):
            return '_auth_'+digest(os.environ['COGNITO_CLIENT_ID'].strip().encode())[:16]
        scope=workspace_scope.get()
        return f'accounts/{scope}/{key}' if scope and key!='_auth' else key

    def initial(self,client):
        spec=next((x for x in CLIENTS if x['id']==client),None) or next((x for x in self.clients() if x['id']==client),None)
        if not spec:raise KeyError('Unknown client')
        docs=[]
        for path in sorted((ROOT/'seed'/'clients'/spec['name']).rglob('*')) if any(c['id']==client for c in CLIENTS) else []:
            if not path.is_file():continue
            rel=path.relative_to(ROOT/'seed').as_posix();raw=path.read_bytes()
            docs.append({'id':hashlib.sha256(rel.encode()).hexdigest()[:20], 'name':path.name,'folder':path.parent.name,'ext':path.suffix.lower()[1:],'size':len(raw),'revision':1,'hash':digest(raw),'blob':'seed:'+rel,'updated':None,'history':[]})
        return {'documents':docs,'chats':[],'memory':[],'proposals':[],'activity':[]}

    def read(self,client):
        if not any(c['id']==client for c in CLIENTS) and not any(c['id']==client for c in self.clients()):raise KeyError('Unknown client')
        return self.read_record(client, lambda: self.initial(client))

    def clients(self):
        extra,_=self.read_record('_clients',lambda:{'clients':[]})
        return copy.deepcopy(CLIENTS)+extra['clients']

    def create_client(self,name):
        name=' '.join(name.split())
        if not 2<=len(name)<=80 or any(ord(c)<32 for c in name):raise ValueError('Use a client name between 2 and 80 characters.')
        with self.lock:
            for attempt in range(4):
                state,version=self.read_record('_clients',lambda:{'clients':[]})
                if any(c['name'].casefold()==name.casefold() for c in CLIENTS+state['clients']):raise ValueError('A client with that name already exists.')
                if len(state['clients'])>=17:raise ValueError('This demonstration supports up to 20 client workspaces.')
                item={'id':'client_'+uid()[:16],'name':name,'legal':name,'initials':''.join(w[0] for w in name.split()[:2]).upper(),'industry':'Client workspace','color':'blue'}
                state['clients'].append(item)
                try:self.write('_clients',state,version);return item
                except Conflict:
                    if attempt==3:raise

    def read_record(self,client,default):
        client=self.scoped_key(client)
        if self.cloud:
            # Official useCache:false protocol: private reads with cache=0 go to origin.
            response=self.cloud_get(f'folio/state/{client}.json',consistent=True)
            if response.status_code==404:return default(),None
            response.raise_for_status()
            version=response.headers.get('etag')
            if not version:raise RuntimeError('Storage did not provide an ETag')
            return response.json(),version
        with self.db() as conn:
            row=conn.execute('SELECT value,version FROM state WHERE client=?',(client,)).fetchone()
        return (json.loads(row[0]),row[1]) if row else (default(),None)

    def archive_client(self,client,name):
        if any(c['id']==client for c in CLIENTS):raise ValueError('The supplied assessment clients cannot be archived.')
        with self.lock:
            for attempt in range(4):
                state,version=self.read_record('_clients',lambda:{'clients':[]})
                item=next((c for c in state['clients'] if c['id']==client and c['name']==name),None)
                if not item:raise KeyError('Client name and identifier do not match.')
                state['clients']=[c for c in state['clients'] if c['id']!=client]
                state.setdefault('archived',[]).append({**item,'archived_at':now()})
                try:self.write('_clients',state,version);return {'archived':True,'files_preserved':True}
                except Conflict:
                    if attempt==3:raise

    def auth_state(self,fn=None):
        with self.lock:
            for attempt in range(4):
                state,version=self.read_record('_auth',lambda:{'sessions':{},'attempts':{}})
                if fn is None:return state
                result=fn(state)
                try:self.write('_auth',state,version);return result
                except Conflict:
                    if attempt==3:raise

    def write(self,client,state,version):
        client=self.scoped_key(client)
        data=json.dumps(state,ensure_ascii=False,allow_nan=False).encode()
        if self.cloud:
            # Conditional PUT protocol from Vercel's official storage SDK. Python SDK lacks if_match.
            headers={'Authorization':'Bearer '+os.environ['BLOB_READ_WRITE_TOKEN'],'x-api-version':'13','x-vercel-blob-access':'private','x-content-type':'application/json','x-add-random-suffix':'0','x-allow-overwrite':'1' if version else '0','x-cache-control-max-age':'60'}
            if version:headers['x-if-match']=version
            response=httpx.put('https://vercel.com/api/blob/',params={'pathname':f'folio/state/{client}.json'},content=data,headers=headers,timeout=30)
            if response.status_code in (409,412):raise Conflict('Workspace changed. Please retry.')
            if response.status_code==400 and 'already exists' in response.text.lower():raise Conflict('Workspace changed. Please retry.')
            response.raise_for_status()
            return
        with self.db() as conn:
            if version is None:
                try:conn.execute('INSERT INTO state VALUES(?,?,1)',(client,data.decode()))
                except sqlite3.IntegrityError:raise Conflict('Workspace changed. Please retry.')
            else:
                n=conn.execute('UPDATE state SET value=?,version=version+1 WHERE client=? AND version=?',(data.decode(),client,version)).rowcount
                if n!=1:raise Conflict('Workspace changed. Please retry.')

    def mutate(self,client,fn):
        with self.lock:
            for attempt in range(4):
                state,version=self.read(client)
                result=fn(state)
                try:self.write(client,state,version);return result
                except Conflict:
                    if attempt==3:raise

    def content(self,doc):
        key=doc['blob']
        if key.startswith('s3:'):
            from . import cloud
            return cloud.read(key[3:])
        if key.startswith('seed:'):
            p=(ROOT/'seed'/key[5:]).resolve()
            if not p.is_relative_to((ROOT/'seed').resolve()):raise ValueError('Invalid path')
            return p.read_bytes()
        if self.cloud:
            response=self.cloud_get(key);response.raise_for_status();return response.content
        p=(self.directory/key).resolve()
        if not p.is_relative_to(self.directory.resolve()):raise ValueError('Invalid path')
        return p.read_bytes()

    def save_bytes(self,client,data):
        from . import cloud
        client=self.scoped_key(client)
        if cloud.bucket():
            key=f'files/{client}/{uid()}'
            cloud.client('s3').put_object(Bucket=cloud.bucket(),Key=key,Body=data,ContentType='application/octet-stream')
            return 's3:'+key
        key=f'folio/files/{client}/{uid()}'
        if self.cloud:
            response=httpx.put('https://vercel.com/api/blob/',params={'pathname':key},content=data,headers={'Authorization':'Bearer '+os.environ['BLOB_READ_WRITE_TOKEN'],'x-api-version':'13','x-vercel-blob-access':'private','x-content-type':'application/octet-stream','x-add-random-suffix':'0','x-allow-overwrite':'0'},timeout=45)
            response.raise_for_status();return response.json()['pathname']
        p=self.directory/key;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        return key

    def cloud_get(self,key,consistent=False):
        token=os.environ['BLOB_READ_WRITE_TOKEN']
        parts=token.split('_')
        store_id=parts[3] if len(parts)>4 else ''
        if not re.fullmatch(r'[a-zA-Z0-9]+',store_id):raise ValueError('Invalid private Blob storage configuration.')
        # Compression changes Vercel's strong origin ETag into a weak W/ tag,
        # which cannot satisfy a conditional write. Request the original bytes.
        return httpx.get(f'https://{store_id.lower()}.private.blob.vercel-storage.com/{key}',params={'cache':'0'} if consistent else None,headers={'Authorization':'Bearer '+token,'Accept-Encoding':'identity'},timeout=30)


def active_documents(state):
    return [d for d in state['documents'] if not d.get('deleted_at')]


def storage_usage(state):
    jobs=state.get('purge_jobs',[])
    return (len(state['documents'])+sum(j['count'] for j in jobs),
            sum(d['size'] for d in state['documents'])+sum(j['size'] for j in jobs))


def document(state,doc_id,include_deleted=False):
    found=next((d for d in state['documents'] if d['id']==doc_id),None)
    if not found:raise KeyError('Document not found in this client')
    if found.get('deleted_at') and not include_deleted:raise KeyError('This file is in Trash. Restore it to open it again.')
    return found

def public_doc(doc):return {k:v for k,v in doc.items() if k not in ('blob','history')}
