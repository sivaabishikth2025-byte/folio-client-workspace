from synthetic_identity import synthetic_email
import copy
import json

import pytest
import backend.app as module
from backend.storage import Store, now
from test_file_trash import local
from test_signup import setup, trusted_user


def thread(ident='target'):
    return {'id':ident,'title':'Conversation '+ident,'time':now(),'messages':[
        {'id':ident+'-user','role':'user','text':'Private question','time':now()},
        {'id':ident+'-answer','role':'assistant','text':'Private answer','time':now(),'proposal_ids':['suggestion-'+ident]}]}


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


async def answer(*args):
    yield {'type':'result','result':{'answer':'Fixture answer','scope':'general','citations':[],'proposals':[]}}


def test_delete_only_target_chat_and_its_suggestions_preserves_files_memory_and_restart(local,monkeypatch):
    api,store=local
    def seed(s):
        s['chats']=[thread(),thread('other')]
        s['memory']=[{'id':'saved','text':'Accepted memory'}]
        s['proposals']=[{'id':'suggestion-target','status':'pending'},
                        {'id':'linked-target','chat_id':'target','status':'accepted'},
                        {'id':'manual','status':'pending'},
                        {'id':'suggestion-other','status':'pending'}]
    store.mutate('northwind',seed);before=store.read('northwind')[0]
    result=api.delete('/api/clients/northwind/chats/target');assert result.status_code==200
    assert result.json()['chat_count']==1
    assert set(result.json()['deleted_proposal_ids'])=={'suggestion-target','linked-target'}
    after=Store(store.directory).read('northwind')[0]
    assert after['chats']==[thread_copy for thread_copy in before['chats'] if thread_copy['id']=='other']
    assert after['documents']==before['documents'] and after['memory']==before['memory']
    assert [p['id'] for p in after['proposals']]==['manual','suggestion-other']
    assert all(d['blob'] and store.content(d) for d in after['documents'])
    assert api.delete('/api/clients/northwind/chats/target').status_code==404
    assert 'chat_usage' not in api.get('/api/clients/northwind').json()
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    assert api.post('/api/clients/northwind/chat',json={'chat_id':'target','message':'Continue'}).status_code==404


def test_delete_enforces_auth_account_client_and_csrf(setup,monkeypatch):
    fake,client=setup;a=trusted_user(fake,client,synthetic_email('a'));b=trusted_user(fake,client,synthetic_email('b'))
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1');monkeypatch.setattr(module,'answer_stream',answer)
    result=events(a.post('/api/clients/northwind/chat',json={'message':'Temporary question'}));ident=result[0]['chat_id']
    url='/api/clients/northwind/chats/'+ident
    assert client().delete(url).status_code==401
    assert b.delete(url).status_code==404
    assert a.delete(url.replace('northwind','ostara')).status_code==404
    assert a.delete(url,headers={'Origin':'https://evil.example'}).status_code==403
    assert len(a.get('/api/clients/northwind').json()['chats'])==1
    assert a.delete(url).status_code==200
    assert b.get('/api/clients/northwind').json()['chats']==[]


@pytest.mark.parametrize('provider_fails',[False,True])
def test_deleted_stream_never_recreates_chat_or_proposals(local,monkeypatch,provider_fails):
    api,store=local;monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    async def racing_answer(*args):
        ident=store.read('northwind')[0]['chats'][0]['id']
        module.delete_chat('northwind',ident)
        if provider_fails:raise ValueError('Simulated interrupted answer')
        yield {'type':'result','result':{'answer':'Must not save this','scope':'general','citations':[],'proposals':[],'memory':'Must not save this either'}}
    monkeypatch.setattr(module,'answer_stream',racing_answer)
    result=events(api.post('/api/clients/northwind/chat',json={'message':'Temporary question'}))
    assert result[-1]['type']=='error'
    state=store.read('northwind')[0]
    assert not state['chats'] and not state['proposals'] and not state['memory']
    assert state['chat_usage'][now()[:10]]==1


def test_delete_between_context_read_and_request_commit_does_not_recreate(local,monkeypatch):
    api,store=local;store.mutate('northwind',lambda s:s['chats'].append(thread()))
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1');original=module.source_map
    def source(state):
        module.delete_chat('northwind','target')
        return original(state)
    monkeypatch.setattr(module,'source_map',source)
    assert api.post('/api/clients/northwind/chat',json={'chat_id':'target','message':'Continue'}).status_code==404
    assert store.read('northwind')[0]['chats']==[]


def test_deleting_chats_cannot_reset_daily_model_usage_limit(local,monkeypatch):
    api,store=local;store.mutate('northwind',lambda s:s['chats'].append(thread()))
    monkeypatch.setenv('DAILY_CHAT_LIMIT','2');monkeypatch.setenv('AWS_BEDROCK_ENABLED','1');monkeypatch.setattr(module,'answer_stream',answer)
    assert api.delete('/api/clients/northwind/chats/target').status_code==200
    result=events(api.post('/api/clients/northwind/chat',json={'message':'One more allowed'}));assert result[-1]['type']=='done'
    assert api.delete('/api/clients/northwind/chats/'+result[0]['chat_id']).status_code==200
    response=api.post('/api/clients/northwind/chat',json={'message':'Must remain limited'})
    assert response.status_code==400 and 'daily chat limit' in response.text
    assert store.read('northwind')[0]['chats']==[]


def test_revert_request_sees_the_value_before_the_last_accepted_edit(local,monkeypatch):
    api,store=local
    doc=next(d for d in api.get('/api/clients/northwind').json()['documents'] if d['name']=='Management accounts Aug 2026.xlsx')
    proposal=api.post('/api/clients/northwind/proposals',json={'document_id':doc['id'],'revision':doc['revision'],'changes':[{'sheet':'Income statement','cell':'B3','value':4455901}]}).json()
    assert api.post(f"/api/clients/northwind/proposals/{proposal['id']}/accept").status_code==200
    seen={}
    async def capture(*args):
        seen['edit_log']=args[8]
        yield {'type':'result','result':{'answer':'Fixture answer','scope':'general','citations':[],'proposals':[]}}
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1');monkeypatch.setattr(module,'answer_stream',capture)
    assert events(api.post('/api/clients/northwind/chat',json={'message':'revert'}))[-1]['type']=='done'
    latest=seen['edit_log'][0]
    assert latest['file']=='Management accounts Aug 2026.xlsx'
    change=latest['changes'][0]
    assert 'B3' in change['location'] and str(change['before']).replace(',','').startswith('4812300') and str(change['after']).replace(',','').startswith('4455901')


def test_assistant_edits_never_replace_formulas_with_typed_values(local):
    api,store=local
    state=store.read('northwind')[0]
    doc=next(d for d in state['documents'] if d['name']=='Management accounts Aug 2026.xlsx')
    p=module.prepare_proposal('northwind',state,doc['id'],[{'sheet':'Income statement','cell':'B3','value':5000000},{'sheet':'Income statement','cell':'B5','value':6206450}],'Edit','Test','assistant',{})
    assert [d['location'] for d in p['diff']]==['Income statement!B3']
    assert any(i['location']=='Income statement!B5' and i['formula'] for i in p['impacts'])
    with pytest.raises(ValueError,match='formulas'):
        module.prepare_proposal('northwind',state,doc['id'],[{'sheet':'Income statement','cell':'B5','value':1}],'Edit','Test','assistant',{})
    restored=module.prepare_proposal('northwind',state,doc['id'],[{'sheet':'Income statement','cell':'B5','value':'=SUM(B3:B4)'}],'Edit','Test','assistant',{})
    assert restored['diff'][0]['after']=='=SUM(B3:B4)'
