import hashlib
from synthetic_identity import synthetic_password
import io
import time
import pytest
from fastapi.testclient import TestClient
import backend.app as module
from backend import accounts,cloud,security
from backend.storage import Store

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.delenv('COGNITO_CLIENT_ID',raising=False)
    monkeypatch.delenv('WORKSPACE_PASSWORD',raising=False)
    monkeypatch.setattr(module,'store',Store(tmp_path))
    return TestClient(module.app,base_url='https://testserver')

def test_cognito_identity_and_revocation(env,monkeypatch):
    monkeypatch.setenv('COGNITO_CLIENT_ID','test-client')
    class Denied(Exception):pass
    class Cognito:
        class exceptions:
            NotAuthorizedException=Denied
            InvalidParameterException=Denied
        revoked=False
        def initiate_auth(self,**kw):
            assert kw['ClientId']=='test-client' and kw['AuthParameters']['USERNAME']=='alice'
            return {'AuthenticationResult':{'AccessToken':'aws-signed-token','ExpiresIn':3600}}
        def get_user(self,**kw):
            if self.revoked:raise Denied()
            return {'Username':'alice','UserAttributes':[{'Name':'sub','Value':'alice-sub'},{'Name':'email_verified','Value':'true'}]}
        def global_sign_out(self,**kw):self.revoked=True
    c=Cognito();monkeypatch.setattr(accounts,'cognito',lambda:c)
    assert env.get('/api/clients').status_code==401
    r=env.post('/api/auth/login',json={'username':'alice','password':synthetic_password()},headers={'Origin':'https://testserver'})
    assert r.status_code==200 and 'max-age=3600' in r.headers['set-cookie'].lower()
    assert 'aws-signed-token' not in str(module.store.auth_state())
    assert env.get('/api/clients').status_code==200
    env.headers['Origin']='https://testserver'
    assert env.post('/api/auth/logout').status_code==200 and c.revoked
    env.cookies.set(security.COOKIE,'aws-signed-token');assert env.get('/api/clients').status_code==401

def test_hosted_shared_password_cannot_bypass_cognito(env,monkeypatch):
    monkeypatch.setenv('VERCEL','1');monkeypatch.setenv('WORKSPACE_PASSWORD',synthetic_password())
    assert env.get('/api/clients').status_code==401

def test_direct_upload_limits_client_scope_and_replay(env,monkeypatch):
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    class S3:
        def generate_presigned_post(self,**kw):
            self.policy=kw;return {'url':'https://test-bucket.s3.us-east-1.amazonaws.com','fields':{'key':kw['Key']}}
        def head_object(self,**kw):return {'ContentLength':5,'VersionId':'v1'}
        def copy_object(self,**kw):self.copy=kw
    s3=S3();monkeypatch.setattr(cloud,'client',lambda service:s3);monkeypatch.setattr(cloud,'read',lambda key,version=None:b'hello')
    r=env.post('/api/clients/northwind/transfers',json={'name':'sample.bin','size':5});assert r.status_code==200
    ident=r.json()['id'];assert ['content-length-range',5,5] in s3.policy['Conditions']
    assert env.post(f'/api/clients/ostara/transfers/{ident}/complete').status_code==404
    url=f'/api/clients/northwind/transfers/{ident}/complete'
    assert env.post(url).status_code==200
    assert env.post(url).status_code==200
    assert s3.copy['CopySource']['VersionId']=='v1'
    ws=env.get('/api/clients/northwind').json();assert len(ws['documents'])==8 and 'transfers' not in ws
    assert env.post('/api/clients/northwind/transfers',json={'name':'../bad','size':5}).status_code==400
    assert env.post('/api/clients/northwind/transfers',json={'name':'huge.bin','size':cloud.MAX_FILE+1}).status_code==422

def test_reservation_quota_and_voice_limit(env,monkeypatch):
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    class S3:
        def generate_presigned_post(self,**kw):return {'url':'https://example.invalid','fields':{}}
    monkeypatch.setattr(cloud,'client',lambda service:S3())
    for i in range(10):assert env.post('/api/clients/northwind/transfers',json={'name':f'{i}.bin','size':1}).status_code==200
    assert env.post('/api/clients/northwind/transfers',json={'name':'extra.bin','size':1}).status_code==400
    assert env.post('/api/clients/ostara/transfers',json={'name':'audio.webm','size':9*1024*1024,'purpose':'voice'}).status_code==400

def test_read_aloud_returns_audio_only_after_auth(env,monkeypatch):
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    class Polly:
        def synthesize_speech(self,**kw):
            assert kw['TextType']=='text' and kw['Engine']=='neural';return {'AudioStream':io.BytesIO(b'mp3')}
    monkeypatch.setattr(cloud,'client',lambda service:Polly())
    r=env.post('/api/clients/northwind/speech',json={'text':'Hello'});assert r.status_code==200 and r.content==b'mp3'
    assert env.post('/api/clients/not-a-client/speech',json={'text':'Hello'}).status_code==404


def test_concurrent_completion_cannot_replace_committed_upload_bytes(env,monkeypatch):
    """A replayed grant can upload another version while two completions race."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    heads=Barrier(2);copies=Barrier(2);lock=Lock()
    versions={'v1':b'first','v2':b'other'}
    class S3:
        counter=0
        objects={}
        def generate_presigned_post(self,**kw):return {'url':'https://example.invalid','fields':{}}
        def head_object(self,**kw):
            with lock:
                self.counter+=1;version='v'+str(self.counter)
            heads.wait(timeout=10)
            return {'ContentLength':5,'VersionId':version}
        def copy_object(self,**kw):
            with lock:self.objects[kw['Key']]=versions[kw['CopySource']['VersionId']]
            copies.wait(timeout=10)
    s3=S3();monkeypatch.setattr(cloud,'client',lambda service:s3)
    monkeypatch.setattr(cloud,'read',lambda key,version=None:versions[version])
    ident=env.post('/api/clients/northwind/transfers',json={'name':'race.bin','size':5}).json()['id']
    url=f'/api/clients/northwind/transfers/{ident}/complete'
    def complete():
        with TestClient(module.app,base_url='https://testserver') as api:return api.post(url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:complete(),range(2)))
    assert all(r.status_code==200 for r in results)
    assert results[0].json()==results[1].json()
    state,_=module.store.read('northwind')
    documents=[d for d in state['documents'] if d['name']=='race.bin']
    assert len(documents)==1
    committed=documents[0]
    assert len(s3.objects)==2, 'Each attempt must copy to a separate immutable key.'
    assert hashlib.sha256(s3.objects[committed['blob'][3:]]).hexdigest()==committed['hash']


def test_voice_duration_is_rejected_before_copy_or_transcription(env,monkeypatch):
    import wave
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    audio=io.BytesIO()
    with wave.open(audio,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000)
        wav.writeframes(b'\0\0'*(121*16000))
    raw=audio.getvalue()
    class S3:
        def generate_presigned_post(self,**kw):return {'url':'https://example.invalid','fields':{}}
        def head_object(self,**kw):return {'ContentLength':len(raw),'VersionId':'v1'}
        def copy_object(self,**kw):pytest.fail('Overlong audio must not be copied to processing storage.')
    def aws_client(service):
        assert service=='s3', 'No billed Transcribe call is permitted for rejected audio.'
        return S3()
    monkeypatch.setattr(cloud,'client',aws_client)
    monkeypatch.setattr(cloud,'read',lambda key,version=None:raw)
    r=env.post('/api/clients/northwind/transfers',json={'name':'too-long.wav','size':len(raw),'purpose':'voice'})
    assert r.status_code==200 # Within byte limit, but above duration limit.
    ident=r.json()['id']
    result=env.post(f'/api/clients/northwind/transfers/{ident}/complete')
    assert result.status_code==400 and '120 seconds' in result.json()['detail']
    state,_=module.store.read('northwind')
    assert len(state['documents'])==7
    assert next(t for t in state['transfers'] if t['id']==ident)['status']=='pending'


def test_large_saved_versions_use_s3_and_redirect_download(env,monkeypatch):
    monkeypatch.setenv('FOLIO_S3_BUCKET','test-bucket')
    objects={}
    class S3:
        def put_object(self,**kw):
            assert kw['Bucket']=='test-bucket' and kw['ContentType']=='application/octet-stream'
            objects[kw['Key']]=kw['Body']
    monkeypatch.setattr(cloud,'client',lambda service:S3())
    raw=b'x'*(5*1024*1024) # Larger than the hosted proxy's response allowance.
    key=module.store.save_bytes('northwind',raw)
    assert key.startswith('s3:files/northwind/')
    assert objects[key[3:]]==raw
    module.store.mutate('northwind',lambda s:s['documents'].append({
        'id':'saved-large-version','name':'Large edited file.bin','folder':'Inbox','ext':'bin',
        'size':len(raw),'revision':2,'hash':hashlib.sha256(raw).hexdigest(),'blob':key,'updated':None,'history':[]}))
    observed={}
    def signed_download(object_key,name):
        observed.update(key=object_key,name=name)
        return 'https://test-bucket.s3.us-east-1.amazonaws.com/private-download'
    monkeypatch.setattr(cloud,'download_url',signed_download)
    r=env.get('/api/clients/northwind/documents/saved-large-version/download',follow_redirects=False)
    assert r.status_code==303
    assert observed=={'key':key[3:],'name':'Large edited file.bin'}
    assert len(r.content)<1024 # Never proxy the large file through the serverless response.


def test_large_document_parsing_does_not_retain_raw_bytes_in_cache(monkeypatch):
    module.parsed_small.cache_clear()
    calls=[]
    def inspect(raw,name):calls.append((len(raw),name));return {'name':name}
    monkeypatch.setattr(module,'inspect_document',inspect)
    large=b'x'*(2*1024*1024+1)
    try:
        module.parsed(large,'large.bin');module.parsed(large,'large.bin')
        assert len(calls)==2 and module.parsed_small.cache_info().currsize==0
        module.parsed(b'small','small.bin');module.parsed(b'small','small.bin')
        assert len(calls)==3 and module.parsed_small.cache_info().currsize==1
    finally:module.parsed_small.cache_clear()


def test_archive_requires_exact_client_and_retains_original_file(env,monkeypatch):
    monkeypatch.delenv('FOLIO_S3_BUCKET',raising=False)
    assert env.post('/api/clients/northwind/archive',json={'name':'Northwind Trading'}).status_code==400
    assert env.post('/api/clients/missing-client/archive',json={'name':'Missing client'}).status_code==404
    created=env.post('/api/clients',json={'name':'Temporary validation client'}).json()
    client=created['id'];url=f'/api/clients/{client}/archive'
    raw=b'Original assessment fixture bytes'
    upload=env.post(f'/api/clients/{client}/upload',files={'files':('fixture.bin',raw)})
    assert upload.status_code==200
    doc_id=upload.json()[0]['id']
    before,_=module.store.read(client)
    assert env.post(url,json={'name':'Wrong client name'}).status_code==404
    assert env.get(f'/api/clients/{client}').status_code==200
    result=env.post(url,json={'name':created['name']})
    assert result.status_code==200 and result.json()=={'archived':True,'files_preserved':True}
    assert client not in {c['id'] for c in env.get('/api/clients').json()}
    assert env.get(f'/api/clients/{client}').status_code==404
    assert env.get(f'/api/clients/{client}/documents/{doc_id}/download').status_code==404
    assert env.post(url,json={'name':created['name']}).status_code==404
    preserved,_=module.store.read_record(client,lambda:None)
    assert preserved==before
    assert module.store.content(preserved['documents'][0])==raw
    registry,_=module.store.read_record('_clients',lambda:None)
    archived=next(c for c in registry['archived'] if c['id']==client)
    assert archived['name']==created['name'] and archived['archived_at']
    monkeypatch.setenv('WORKSPACE_PASSWORD',synthetic_password())
    assert env.post('/api/clients/northwind/archive',json={'name':'Northwind Trading'}).status_code==401


def test_obsolete_session_cookie_is_rejected_without_server_error(env,monkeypatch):
    monkeypatch.setenv('COGNITO_CLIENT_ID','test-client')
    class InvalidToken(Exception):pass
    class Denied(Exception):pass
    class Cognito:
        class exceptions:
            InvalidParameterException=InvalidToken
            NotAuthorizedException=Denied
        def get_user(self,**kw):raise InvalidToken('Old session is not a Cognito token.')
    monkeypatch.setattr(accounts,'cognito',lambda:Cognito())
    token='legacy-session-not-a-jwt'
    module.store.auth_state(lambda s:s['sessions'].update({hashlib.sha256(token.encode()).hexdigest():time.time()+3600}))
    env.cookies.set(security.COOKIE,token)
    assert env.get('/api/clients').status_code==401
    session=env.get('/api/auth/session')
    assert session.status_code==200 and session.json()['authenticated'] is False
