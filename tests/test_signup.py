from synthetic_identity import synthetic_password
from synthetic_identity import synthetic_email
"""Account lifecycle and cross-account authorization regressions; AWS transport is mocked."""
import copy
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
import backend.app as module
from backend import accounts,security
from backend.storage import Store, workspace_scope

PASSWORD=synthetic_password()

def aws_error(code):
    return ClientError({'Error':{'Code':code,'Message':'Private AWS payload must not be exposed'}},'TestOperation')

class FakeCognito:
    exceptions=SimpleNamespace(NotAuthorizedException=ClientError,InvalidParameterException=ClientError)
    def __init__(self):self.users={};self.tokens={};self.sent=[]
    def sign_up(self,**kw):
        username=kw['Username']
        if username in self.users:raise aws_error('UsernameExistsException')
        assert kw['ClientId']=='signup-client'
        self.users[username]={'password':kw['Password'],'confirmed':False,'verified':False,'sub':'sub-'+username,'attributes':kw['UserAttributes']}
        self.sent.append(('signup',username))
        return {'UserConfirmed':False,'UserSub':'sub-'+username}
    def confirm_sign_up(self,**kw):
        assert kw['ForceAliasCreation'] is False
        if kw['ConfirmationCode']!='123456':raise aws_error('CodeMismatchException')
        self.users[kw['Username']].update(confirmed=True,verified=True)
        return {}
    def initiate_auth(self,**kw):
        username=kw['AuthParameters']['USERNAME']
        user=self.users.get(username)
        if not user or user['password']!=kw['AuthParameters']['PASSWORD']:raise aws_error('NotAuthorizedException')
        if not user['confirmed']:raise aws_error('UserNotConfirmedException')
        token='token-'+username
        self.tokens[token]=username
        return {'AuthenticationResult':{'AccessToken':token,'ExpiresIn':3600}}
    def get_user(self,**kw):
        username=self.tokens.get(kw['AccessToken'])
        if not username:raise aws_error('NotAuthorizedException')
        user=self.users[username]
        return {'Username':username,'UserAttributes':[{'Name':'sub','Value':user['sub']},{'Name':'email_verified','Value':str(user['verified']).lower()}]}
    def resend_confirmation_code(self,**kw):
        if kw['Username'] not in self.users:raise aws_error('UserNotFoundException')
        self.sent.append(('resend',kw['Username']));return {}
    def forgot_password(self,**kw):
        if kw['Username'] not in self.users:raise aws_error('UserNotFoundException')
        self.sent.append(('forgot',kw['Username']));return {}
    def confirm_forgot_password(self,**kw):
        if kw['ConfirmationCode']!='654321':raise aws_error('CodeMismatchException')
        self.users[kw['Username']]['password']=kw['Password'];return {}
    def global_sign_out(self,**kw):self.tokens.pop(kw['AccessToken'],None)

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.delenv('FOLIO_S3_BUCKET',raising=False)
    monkeypatch.delenv('FOLIO_SHARED_ACCOUNT_SUBS',raising=False)
    monkeypatch.setenv('COGNITO_CLIENT_ID','signup-client')
    monkeypatch.setattr(module,'store',Store(tmp_path))
    fake=FakeCognito();monkeypatch.setattr(accounts,'cognito',lambda:fake)
    def client():return TestClient(module.app,base_url='https://testserver',headers={'Origin':'https://testserver'})
    return fake,client

def trusted_user(fake,client,username):
    fake.users[username]={'password':PASSWORD,'confirmed':True,'verified':True,'sub':'sub-'+username}
    api=client();assert api.post('/api/auth/login',json={'username':username,'password':PASSWORD}).status_code==200
    return api

def test_signup_verification_signin_recovery_lifecycle(setup,monkeypatch):
    fake,client=setup;api=client()
    monkeypatch.setenv('COGNITO_CLIENT_ID','signup-client\r\n')
    r=api.post('/api/auth/signup',json={'name':'Test Person','email':synthetic_email('test').upper(),'password':PASSWORD})
    assert r.status_code==200 and r.json()['username']==synthetic_email('test')
    assert 'set-cookie' not in r.headers and api.get('/api/clients').status_code==401
    r=api.post('/api/auth/login',json={'username':synthetic_email('test'),'password':PASSWORD})
    assert r.status_code==403 and r.json()['detail']['code']=='EMAIL_NOT_VERIFIED'
    assert api.post('/api/auth/confirm',json={'username':synthetic_email('test'),'code':'999999'}).status_code==400
    assert api.post('/api/auth/confirm',json={'username':synthetic_email('test'),'code':'123456'}).json()=={'confirmed':True}
    assert api.post('/api/auth/login',json={'username':synthetic_email('test'),'password':PASSWORD}).status_code==200
    assert len(api.get('/api/clients').json())==3
    assert api.post('/api/auth/logout').status_code==200
    module.store.auth_state(lambda s:s.update(email_attempts={}))
    assert api.post('/api/auth/forgot',json={'username':synthetic_email('test')}).status_code==200
    r=api.post('/api/auth/reset',json={'username':synthetic_email('test'),'code':'999999','password':PASSWORD+'new'})
    assert r.status_code==400 and 'Private AWS' not in r.text
    assert api.post('/api/auth/reset',json={'username':synthetic_email('test'),'code':'654321','password':PASSWORD+'new'}).status_code==200
    assert api.post('/api/auth/login',json={'username':synthetic_email('test'),'password':PASSWORD}).status_code==401
    assert api.post('/api/auth/login',json={'username':synthetic_email('test'),'password':PASSWORD+'new'}).status_code==200

def test_signup_recovery_dont_enumerate_existing_accounts(setup):
    fake,client=setup;api=client()
    body={'name':'Test Person','email':synthetic_email('test'),'password':PASSWORD}
    first=api.post('/api/auth/signup',json=body)
    module.store.auth_state(lambda s:s.update(email_attempts={}))
    second=api.post('/api/auth/signup',json=body)
    assert first.json()==second.json()
    module.store.auth_state(lambda s:s.update(email_attempts={}))
    known=api.post('/api/auth/forgot',json={'username':synthetic_email('test')})
    unknown=api.post('/api/auth/forgot',json={'username':synthetic_email('missing')})
    assert known.json()==unknown.json() and known.status_code==unknown.status_code==200

def test_account_actions_reject_cross_origin_invalid_codes_and_email_spam(setup):
    fake,client=setup;api=client()
    for path,body in [('signup',{'name':'Test','email':synthetic_email('a'),'password':PASSWORD}),('forgot',{'username':synthetic_email('a')}),('confirm',{'username':synthetic_email('a'),'code':'123456'})]:
        assert api.post('/api/auth/'+path,json=body,headers={'Origin':'https://attacker.invalid'}).status_code==403
    assert not fake.users and not fake.sent
    assert api.post('/api/auth/signup',json={'name':'Test','email':'invalid email','password':PASSWORD}).status_code==400
    assert api.post('/api/auth/confirm',json={'username':synthetic_email('a'),'code':'1234567'}).status_code==422
    assert api.post('/api/auth/forgot',json={'username':synthetic_email('a')}).status_code==200
    assert api.post('/api/auth/resend',json={'username':synthetic_email('a')}).status_code==429

def test_unverified_identity_cannot_reach_data_even_with_registered_token(setup):
    fake,client=setup;api=trusted_user(fake,client,synthetic_email('unverified'))
    fake.users[synthetic_email('unverified')]['verified']=False
    assert api.get('/api/clients').status_code==401
    assert api.get('/api/auth/session').json()['authenticated'] is False

def test_private_workspaces_isolate_registry_uploads_memory_and_parallel_requests(setup):
    fake,client=setup;a=trusted_user(fake,client,synthetic_email('a'));b=trusted_user(fake,client,synthetic_email('b'))
    a.post('/api/clients/northwind/memory',json={'text':'Only account A should see this'})
    extra=a.post('/api/clients',json={'name':'Private customer'}).json()
    added=a.post('/api/clients/northwind/upload',files={'files':('private.txt',b'Private account A bytes','text/plain')})
    assert added.status_code==200
    doc=added.json()[0]
    assert b.get('/api/clients/'+extra['id']).status_code==404
    assert b.get('/api/clients/northwind/documents/'+doc['id']+'/download').status_code==404
    assert b.get('/api/clients/northwind').json()['memory']==[]
    def read(api):return api.get('/api/clients/northwind').json()
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(read,[a,b,a,b]))
    assert [len(x['memory']) for x in results]==[1,0,1,0]
    assert [len(x['documents']) for x in results]==[8,7,8,7]
    assert a.get('/api/clients/northwind/documents/'+doc['id']+'/download').content==b'Private account A bytes'
    assert workspace_scope.get()=='' and module.store.read('northwind')[0]['memory']==[]

def test_streamed_chat_stays_in_its_account_and_forged_chat_is_rejected(setup,monkeypatch):
    fake,client=setup;a=trusted_user(fake,client,synthetic_email('a'));b=trusted_user(fake,client,synthetic_email('b'))
    monkeypatch.setattr(module,'catalog',lambda:[{'id':'bedrock','configured':True,'model':'test','name':'test'}])
    monkeypatch.setattr(module,'source_map',lambda state:([],{},[]))
    async def answer(*args,**kw):
        yield {'type':'text','text':'Account-scoped answer'}
        yield {'type':'result','result':{'answer':'Account-scoped answer','scope':'general','citations':[],'proposals':[],'memory':None}}
    monkeypatch.setattr(module,'answer_stream',answer)
    r=a.post('/api/clients/northwind/chat',json={'provider':'bedrock','message':'Hello'})
    assert r.status_code==200 and 'Account-scoped answer' in r.text
    chat=a.get('/api/clients/northwind').json()['chats'][0]
    assert len(chat['messages'])==2 and b.get('/api/clients/northwind').json()['chats']==[]
    assert b.post('/api/clients/northwind/chat',json={'provider':'bedrock','message':'Hello','chat_id':chat['id']}).status_code==404

def test_only_configured_immutable_sub_can_access_existing_assessment(setup,monkeypatch):
    fake,client=setup
    module.store.mutate('northwind',lambda s:s['memory'].append({'text':'Assessment only'}))
    monkeypatch.setenv('FOLIO_SHARED_ACCOUNT_SUBS','sub-reviewer')
    reviewer=trusted_user(fake,client,'reviewer')
    assert reviewer.get('/api/clients/northwind').json()['memory'][0]['text']=='Assessment only'
    attacker=trusted_user(fake,client,synthetic_email('reviewer'))
    assert attacker.get('/api/clients/northwind').json()['memory']==[]
    # A display name or requested workspace header never selects the shared data.
    assert attacker.get('/api/clients/northwind',headers={'X-Workspace':'','X-User':'reviewer'}).json()['memory']==[]

def test_scoped_cloud_record_paths_keep_auth_global(setup,monkeypatch):
    fake,client=setup;store=module.store;store.cloud=True;paths=[]
    class Missing:
        status_code=404
    monkeypatch.setattr(store,'cloud_get',lambda key,**kw:(paths.append(key) or Missing()))
    token=workspace_scope.set('account-hash')
    try:
        store.read('northwind');store.auth_state()
    finally:workspace_scope.reset(token)
    auth_key=hashlib.sha256(b'signup-client').hexdigest()[:16]
    assert paths==['folio/state/accounts/account-hash/northwind.json',f'folio/state/_auth_{auth_key}.json']

def test_new_app_client_sessions_cannot_authorize_old_deployments(setup,monkeypatch):
    fake,client=setup;api=trusted_user(fake,client,synthetic_email('a'))
    current=module.store.auth_state()
    assert current['sessions']
    monkeypatch.setenv('COGNITO_CLIENT_ID','obsolete-client')
    assert module.store.auth_state()['sessions']=={}
    assert api.get('/api/clients').status_code==401
    monkeypatch.delenv('COGNITO_CLIENT_ID')
    assert module.store.auth_state()['sessions']=={}
