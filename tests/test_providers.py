import asyncio
import json
import pytest
from fastapi.testclient import TestClient
import backend.app as app_module
import backend.assistant as assistant
from backend.storage import Store
from backend.providers import catalog


@pytest.mark.parametrize('entry',['answer_stream','_answer_once'])
def test_removed_anthropic_path_cannot_invoke_a_provider(monkeypatch,entry):
    monkeypatch.setenv('ANTHROPIC_API_KEY','obsolete-key-test-only')
    def forbidden(*args):raise AssertionError('Unsupported provider reached AWS')
    monkeypatch.setattr(assistant,'bedrock_client',forbidden)
    async def run():return [event async for event in getattr(assistant,entry)('Test','Hello',[],[],[],[],'anthropic')]
    with pytest.raises(ValueError,match='Only AWS Claude'):asyncio.run(run())


def test_aws_only_default_status_and_credentials_never_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'store', Store(tmp_path))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret-anthropic-test')
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK', 'secret-aws-test')
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.delenv('WORKSPACE_PASSWORD', raising=False)
    seen = []
    async def reply(name, question, context, images, history, memory, provider, token):
        seen.append(provider)
        assert all('Ostara' not in json.dumps(x) for x in context)
        yield {'type': 'result', 'result': {'answer': 'General answer.', 'scope': 'general', 'citations': [], 'proposals': []}}
    monkeypatch.setattr(app_module, 'answer_stream', reply)
    api = TestClient(app_module.app)
    for selection in [{}, {'provider':'bedrock'}]:
        r = api.post('/api/clients/northwind/chat', json={'message': 'Explain gross margin.', **selection})
        assert r.status_code == 200 and '"type": "done"' in r.text
    assert seen == ['bedrock', 'bedrock']
    state = api.get('/api/clients/northwind').json()
    assert {t['messages'][-1]['provider'] for t in state['chats']} == {'bedrock'}
    status=api.get('/api/status').json()
    assert [p['id'] for p in status['providers']]==['bedrock'] and status['ai_ready']
    assert status['model']==catalog()[0]['model']
    assert api.post('/api/clients/northwind/chat',json={'message':'Hello','provider':'anthropic'}).status_code==422
    assert 'secret-' not in json.dumps(state) and 'secret-' not in json.dumps(catalog())
    assert api.post('/api/clients/northwind/chat', json={'message': 'hi', 'provider': 'untrusted-url'}).status_code == 422
    monkeypatch.delenv('AWS_BEARER_TOKEN_BEDROCK', raising=False)
    monkeypatch.delenv('FOLIO_AWS_ROLE_ARN', raising=False)
    monkeypatch.delenv('AWS_BEDROCK_ENABLED', raising=False)
    assert api.post('/api/clients/northwind/chat', json={'message': 'hi', 'provider': 'bedrock'}).status_code == 503
    assert not api.get('/api/status').json()['ai_ready']
    assert seen == ['bedrock', 'bedrock']  # An obsolete Anthropic key cannot act as fallback.


def test_bedrock_stream_translates_images_and_streams_one_constrained_generation(monkeypatch):
    output = {'answer': 'A quoted "answer" from the scan.', 'scope': 'client', 'citations': ['doc::page1:image'], 'proposals': []}
    payload = json.dumps(output)
    captured = {};calls=[]
    class Stream:
        closed = False
        def __iter__(self):
            for n in range(0, len(payload), 7):
                yield {'contentBlockDelta': {'contentBlockIndex': 0, 'delta': {'text': payload[n:n+7]}}}
            yield {'messageStop': {'stopReason': 'end_turn'}}
        def close(self): self.closed = True
    stream = Stream()
    class Client:
        def converse_stream(self, **kwargs):
            captured.update(kwargs)
            calls.append(kwargs)
            return {'stream': stream}
    monkeypatch.setattr(assistant, 'bedrock_client', lambda token: Client())
    async def run():
        return [e async for e in assistant.answer_stream('Northwind', 'Read this.', [],
            [{'source_id': 'doc::page1:image', 'type': 'image/png', 'data': b'png-bytes'}], [], [], 'bedrock')]
    events = asyncio.run(run())
    assert events[-1] == {'type': 'result', 'result': output}
    assert any(e['type'] == 'text' for e in events)
    assert captured['inferenceConfig']['maxTokens'] == 6000
    assert len(calls)==1 and 'toolConfig' not in captured
    assert captured['outputConfig']['textFormat']['type']=='json_schema'
    assert json.loads(captured['outputConfig']['textFormat']['structure']['jsonSchema']['schema'])['additionalProperties'] is False
    assert any(b.get('image', {}).get('source', {}).get('bytes') == b'png-bytes' for b in captured['messages'][0]['content'])
    assert stream.closed


def test_bedrock_cache_excludes_questions_and_tracks_selected_client_and_memory(monkeypatch):
    calls=[]
    async def capture(content,token,tool):
        calls.append((content,tool))
        yield {'type':'result','result':{'answer':'General answer','scope':'general','citations':[],'proposals':[]}}
    monkeypatch.setattr(assistant,'bedrock_stream',capture)
    async def run():
        for name,doc,question,history,memory in [
            ('Northwind','doc-one','Question A',[],[]),
            ('Northwind','doc-one','Question B',[{'text':'Previous chat'}],[]),
            ('Ostara','doc-two','Question B',[],[]),
            ('Northwind','doc-one','Question B',[],[{'text':'Updated contact'}]),
        ]:
            context=[{'document_id':doc,'blocks':[{'id':doc+'::p1','locator':'p1','text':name}]}]
            async for _ in assistant.answer_stream(name,question,context,[],history,memory,'bedrock'):pass
    asyncio.run(run())
    assert calls[0][1]==calls[1][1]
    assert calls[0][1]['input_schema']['properties']['citations']['items']['enum']==['doc-one::p1']
    assert calls[2][1]['input_schema']['properties']['citations']['items']['enum']==['doc-two::p1']
    def prefix(content):return content[:next(i for i,b in enumerate(content) if b['type']=='cache_point')]
    assert prefix(calls[0][0])==prefix(calls[1][0])
    assert prefix(calls[0][0])!=prefix(calls[2][0])!=prefix(calls[3][0])
    assert 'Question' not in json.dumps(prefix(calls[0][0]))
    assert 'Previous chat' not in json.dumps(prefix(calls[1][0]))
    assert 'Updated contact' in json.dumps(prefix(calls[3][0]))


def test_bedrock_stream_is_visible_before_completion_and_closes_on_cancel(monkeypatch):
    class Stream:
        closed=False;completed=False
        def __iter__(self):
            yield {'contentBlockDelta':{'delta':{'text':'{"answer":"Visible early'}}}
            self.completed=True
            yield {'contentBlockDelta':{'delta':{'text':'","scope":"general","citations":[],"proposals":[]}'}}}
            yield {'messageStop':{'stopReason':'end_turn'}}
        def close(self):self.closed=True
    stream=Stream()
    class Client:
        def converse_stream(self,**kwargs):return {'stream':stream}
    monkeypatch.setattr(assistant,'bedrock_client',lambda token:Client())
    async def run():
        response=assistant.answer_stream('Test','Hi',[],[],[],[],'bedrock')
        first=await anext(response)
        assert first=={'type':'text','text':'Visible early'} and not stream.completed
        await response.aclose()
        assert stream.closed and not stream.completed
    asyncio.run(run())


def test_bedrock_edits_keep_review_and_only_publish_the_final_proposal(monkeypatch):
    draft={'answer':'Draft proposal','scope':'client','citations':['doc::p1'],
           'proposals':[{'document_id':'doc','title':'Draft','reason':'Test','changes':[{'paragraph':'p1','value':'draft','source_ids':['doc::p1']}]}]}
    reviewed={**draft,'answer':'Checked proposal','proposals':[{**draft['proposals'][0],'title':'Checked'}]}
    questions=[]
    async def once(name,question,*args):
        questions.append(question)
        output=draft if len(questions)==1 else reviewed
        yield {'type':'text','text':output['answer']}
        yield {'type':'result','result':output}
    monkeypatch.setattr(assistant,'_answer_once',once)
    async def run():return [event async for event in assistant.answer_stream('Test','Edit the document',[],[],[],[],'bedrock')]
    events=asyncio.run(run())
    assert len(questions)==2 and 'Edit the document' in questions[1]
    assert [e for e in events if e['type']=='result']==[{'type':'result','result':reviewed}]
    assert {'type':'status','message':'Checking proposed changes'} in events


def test_bedrock_compact_context_keeps_all_source_content_and_original_map(monkeypatch):
    context=[{'document_id':'doc','name':'Evidence.xlsx','revision':2,'fields':[],
              'blocks':[{'id':'doc::Sheet!B8','document_id':'doc','name':'Evidence.xlsx','revision':2,
                         'hash':'file-version','rect':[0,0,1,1],'locator':'Sheet!B8','text':'Sheet!B8: 35% (formula =B7/B5)'}]}]
    original=json.dumps(context);seen=[]
    async def stream(content,token,tool):
        seen.append(content)
        yield {'type':'result','result':{'answer':'35% [1]','scope':'client','citations':['doc::Sheet!B8'],'proposals':[]}}
    monkeypatch.setattr(assistant,'bedrock_stream',stream)
    async def run():
        async for _ in assistant.answer_stream('Test','Margin?',context,[],[],[],'bedrock'):pass
    asyncio.run(run())
    projected=json.loads(seen[0][0]['text'].split('UNTRUSTED FILE EVIDENCE:\n')[1])
    assert projected[0]['blocks']==[{'id':'doc::Sheet!B8','locator':'Sheet!B8','text':'Sheet!B8: 35% (formula =B7/B5)'}]
    assert projected[0]['revision']==2 and projected[0]['name']=='Evidence.xlsx'
    assert json.dumps(context)==original


@pytest.mark.parametrize('payload,stop',[( '{"answer":"unfinished','end_turn'),(json.dumps({'answer':'draft','scope':'general','citations':[],'proposals':[]}), 'content_filtered')])
def test_bedrock_invalid_or_blocked_stream_never_commits_a_result(monkeypatch,payload,stop):
    class Stream:
        closed=False
        def __iter__(self):
            yield {'contentBlockDelta':{'delta':{'text':payload}}}
            yield {'messageStop':{'stopReason':stop}}
        def close(self):self.closed=True
    stream=Stream()
    class Client:
        def converse_stream(self,**kwargs):return {'stream':stream}
    monkeypatch.setattr(assistant,'bedrock_client',lambda token:Client())
    events=[]
    async def run():
        async for event in assistant.answer_stream('Test','Hi',[],[],[],[],'bedrock'):events.append(event)
    with pytest.raises(ValueError):asyncio.run(run())
    assert not any(e['type']=='result' for e in events) and stream.closed


def test_bedrock_streaming_still_rejects_forged_citations(tmp_path,monkeypatch):
    monkeypatch.setattr(app_module,'store',Store(tmp_path))
    monkeypatch.setenv('AWS_BEDROCK_ENABLED','1')
    for key in ['VERCEL','WORKSPACE_PASSWORD','COGNITO_CLIENT_ID']:monkeypatch.delenv(key,raising=False)
    result={'answer':'Untrusted answer','scope':'client','citations':['foreign-client::p1'],'proposals':[]}
    class Stream:
        def __iter__(self):
            yield {'contentBlockDelta':{'delta':{'text':json.dumps(result)}}}
            yield {'messageStop':{'stopReason':'end_turn'}}
        def close(self):pass
    class Client:
        def converse_stream(self,**kwargs):return {'stream':Stream()}
    monkeypatch.setattr(assistant,'bedrock_client',lambda token:Client())
    api=TestClient(app_module.app)
    response=api.post('/api/clients/northwind/chat',json={'provider':'bedrock','message':'Read a file'})
    assert 'could not be verified' in response.text and '"type": "done"' not in response.text
    state=api.get('/api/clients/northwind').json()
    assert not state['proposals'] and state['chats'][0]['messages'][-1]['error']


def test_bedrock_truncation_cannot_produce_an_edit(monkeypatch):
    class Stream:
        def __iter__(self):
            yield {'messageStop': {'stopReason': 'max_tokens'}}
        def close(self): pass
    class Client:
        def converse_stream(self, **kwargs): return {'stream': Stream()}
    monkeypatch.setattr(assistant, 'bedrock_client', lambda token: Client())
    async def run(): return [e async for e in assistant.bedrock_stream([{'type': 'text', 'text': 'hi'}], None)]
    with pytest.raises(ValueError, match='length limit'):
        asyncio.run(run())
