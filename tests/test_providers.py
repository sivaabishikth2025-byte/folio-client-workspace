import asyncio
import json
import pytest
from fastapi.testclient import TestClient
import backend.app as app_module
import backend.assistant as assistant
from backend.storage import Store
from backend.providers import catalog


@pytest.mark.parametrize('entry',['answer_stream','_answer_once'])
def test_unknown_provider_cannot_invoke_a_model(monkeypatch,entry):
    def forbidden(*args):raise AssertionError('Unsupported provider reached AWS')
    monkeypatch.setattr(assistant,'bedrock_client',forbidden)
    async def run():return [event async for event in getattr(assistant,entry)('Test','Hello',[],[],[],[],'untrusted-url')]
    with pytest.raises(ValueError,match='Unknown Claude provider'):asyncio.run(run())


def _local(tmp_path,monkeypatch):
    monkeypatch.setattr(app_module, 'store', Store(tmp_path))
    for key in ['VERCEL','WORKSPACE_PASSWORD','COGNITO_CLIENT_ID','AWS_BEARER_TOKEN_BEDROCK','FOLIO_AWS_ROLE_ARN','AWS_BEDROCK_ENABLED','ANTHROPIC_API_KEY','ANTHROPIC_MODEL']:
        monkeypatch.delenv(key, raising=False)
    return TestClient(app_module.app)


def test_provider_is_chosen_by_the_server_and_credentials_never_persist(tmp_path, monkeypatch):
    api=_local(tmp_path,monkeypatch)
    seen = []
    async def reply(name, question, context, images, history, memory, provider, token, edit_log=None):
        seen.append(provider)
        assert all('Ostara' not in json.dumps(x) for x in context)
        yield {'type': 'result', 'result': {'answer': 'General answer.', 'scope': 'general', 'citations': [], 'proposals': []}}
    monkeypatch.setattr(app_module, 'answer_stream', reply)
    # Nothing configured: a clear error, no model call.
    assert api.post('/api/clients/northwind/chat', json={'message': 'hi'}).status_code == 503
    assert not api.get('/api/status').json()['ai_ready']
    # AWS only: Bedrock.
    monkeypatch.setenv('AWS_BEARER_TOKEN_BEDROCK', 'secret-aws-test')
    assert api.post('/api/clients/northwind/chat', json={'message': 'Explain gross margin.'}).status_code==200
    status=api.get('/api/status').json()
    assert [p['id'] for p in status['providers']]==['bedrock'] and status['ai_ready']
    # Anthropic key set: Anthropic wins, even if the browser asks for Bedrock.
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'secret-anthropic-test')
    for selection in [{}, {'provider':'bedrock'}, {'provider':'anthropic'}]:
        r = api.post('/api/clients/northwind/chat', json={'message': 'Explain gross margin.', **selection})
        assert r.status_code == 200 and '"type": "done"' in r.text
    assert seen == ['bedrock', 'anthropic', 'anthropic', 'anthropic']
    status=api.get('/api/status').json()
    assert status['providers'][0]['id']=='anthropic' and status['model']=='claude-sonnet-5-5' and status['ai_ready']
    monkeypatch.setenv('ANTHROPIC_MODEL','claude-opus-5-5')
    assert api.get('/api/status').json()['model']=='claude-opus-5-5'
    state = api.get('/api/clients/northwind').json()
    assert 'secret-' not in json.dumps(state) and 'secret-' not in json.dumps(catalog()) and 'secret-' not in json.dumps(status)
    assert api.post('/api/clients/northwind/chat', json={'message': 'hi', 'provider': 'untrusted-url'}).status_code == 422


def _fake_anthropic(monkeypatch, result, captured, status=200, error=None, stop='end_turn'):
    """Stand-in for api.anthropic.com that streams Server-Sent Events like the real Messages API."""
    import httpx
    def handler(request):
        captured.append({'url':str(request.url),'headers':dict(request.headers),'body':json.loads(request.content)})
        if status!=200:
            return httpx.Response(status, json={'type':'error','error':{'type':'invalid_request_error','message':error}})
        payload=json.dumps(result)
        events=[{'type':'message_start','message':{'id':'msg_test','usage':{'input_tokens':10}}},
                {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}]
        events+=[{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':payload[n:n+9]}} for n in range(0,len(payload),9)]
        events+=[{'type':'content_block_stop','index':0},{'type':'message_delta','delta':{'stop_reason':stop},'usage':{'output_tokens':50}},{'type':'message_stop'}]
        body=''.join(f'event: {e["type"]}\ndata: {json.dumps(e)}\n\n' for e in events)
        return httpx.Response(200, text=body, headers={'content-type':'text/event-stream'})
    real=httpx.AsyncClient
    monkeypatch.setattr(assistant.httpx,'AsyncClient',lambda **kw: real(transport=httpx.MockTransport(handler),**kw))


def test_local_run_with_only_anthropic_key_answers_a_question_with_citations(tmp_path, monkeypatch):
    api=_local(tmp_path,monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-test-local-only')
    def forbidden(*args):raise AssertionError('Bedrock must not be used when ANTHROPIC_API_KEY is set')
    monkeypatch.setattr(assistant,'bedrock_client',forbidden)
    # Find the real gross-margin cell id from the seeded Northwind workbook.
    context,sources,_=app_module.source_map(api.get('/api/clients/northwind').json() and app_module.store.read('northwind')[0])
    margin=next(i for i in sources if i.endswith('Income statement!B8'))
    captured=[]
    _fake_anthropic(monkeypatch,{'answer':'Gross margin is 35.0% [1].','scope':'client','citations':[margin],'proposals':[]},captured)
    r=api.post('/api/clients/northwind/chat',json={'message':'What is the gross margin?'})
    assert r.status_code==200 and '"type": "done"' in r.text, r.text
    done=json.loads([l for l in r.text.split('\n') if '"type": "done"' in l][0][6:])
    assert done['message']['provider']=='anthropic' and done['message']['model']=='claude-sonnet-5-5'
    assert done['message']['citations'][0]['locator']=='Income statement!B8'
    assert '"type": "text"' in r.text  # streamed before completion
    sent=captured[0]
    assert sent['url']=='https://api.anthropic.com/v1/messages'
    assert sent['headers']['x-api-key']=='sk-test-local-only' and sent['headers']['anthropic-version']=='2023-06-01'
    body=sent['body']
    assert body['model']=='claude-sonnet-5-5' and body['stream'] is True and body['max_tokens']==6000
    assert body['output_config']['format']['type']=='json_schema'
    assert body['output_config']['format']['schema']['additionalProperties'] is False
    assert margin in body['output_config']['format']['schema']['properties']['citations']['items']['enum']
    content=body['messages'][0]['content']
    marked=[i for i,b in enumerate(content) if b.get('cache_control')=={'type':'ephemeral'}]
    question=next(i for i,b in enumerate(content) if 'CURRENT USER REQUEST' in b.get('text',''))
    assert len(marked)==1 and marked[0]<question  # evidence is cached, the question is not
    assert not any(b.get('type')=='cache_point' for b in content)
    assert 'Ostara' not in json.dumps(content)  # only the selected client's files are sent
    state=api.get('/api/clients/northwind').json()
    assert 'sk-test' not in json.dumps(state)


@pytest.mark.parametrize('status,error,expected',[
    (400,'Your credit balance is too low to access the Anthropic API.','out of API credit'),
    (401,'invalid x-api-key','key was rejected'),
    (404,'model: not-a-model','model was not found'),
    (429,'rate limited','rate limiting'),
    (529,'Overloaded','temporarily unavailable'),
])
def test_anthropic_errors_are_explained_and_never_save_an_answer(tmp_path,monkeypatch,status,error,expected):
    api=_local(tmp_path,monkeypatch)
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-test-local-only')
    _fake_anthropic(monkeypatch,None,[],status=status,error=error)
    r=api.post('/api/clients/northwind/chat',json={'message':'What is the gross margin?'})
    assert expected in r.text and '"type": "done"' not in r.text
    assert 'sk-test' not in r.text


@pytest.mark.parametrize('stop',['max_tokens','refusal'])
def test_anthropic_incomplete_answers_never_commit(monkeypatch,stop):
    monkeypatch.setenv('ANTHROPIC_API_KEY','sk-test-local-only')
    _fake_anthropic(monkeypatch,{'answer':'x','scope':'general','citations':[],'proposals':[]},[],stop=stop)
    async def run():return [e async for e in assistant.answer_stream('Test','Hi',[],[],[],[],'anthropic')]
    with pytest.raises(ValueError):asyncio.run(run())


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
