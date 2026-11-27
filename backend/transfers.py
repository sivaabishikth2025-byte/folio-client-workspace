import io
import wave
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from . import cloud
from .storage import uid, now, digest, public_doc, storage_usage

class Begin(BaseModel):
    name: str = Field(min_length=1,max_length=200)
    size: int = Field(gt=0,le=cloud.MAX_FILE)
    purpose: Literal['file','voice'] = 'file'

class Speech(BaseModel):
    text: str = Field(min_length=1,max_length=3000)

def install(app, get_store, parsed):
    class Proxy:
        def __getattr__(self,name):return getattr(get_store(),name)
    store=Proxy()
    def transfer(client, ident):
        state,_=store.read(client)
        item=next((t for t in state.get('transfers',[]) if t['id']==ident),None)
        if not item or item['expires']<time.time():raise HTTPException(404,'Upload expired or not in this client.')
        return item

    def speech_limit(client):
        def change(s):
            recent=[t for t in s.get('speech_usage',[]) if t>time.time()-86400]
            if len(recent)>=60:raise ValueError('Daily voice limit reached for this client.')
            s['speech_usage']=recent+[time.time()]
        store.mutate(client,change)

    @app.post('/api/clients/{client}/transfers')
    def begin(client:str, body:Begin):
        if not cloud.bucket():raise ValueError('Direct uploads are not configured.')
        name=body.name
        if name in ('.','..') or re.search(r'[\\/\x00-\x1f\x7f]',name):raise ValueError('Use a plain filename.')
        if body.purpose=='voice':
            if Path(name).suffix!='.wav' or body.size>8*1024*1024:raise ValueError('Record up to two minutes, at most 8 MB.')
            speech_limit(client)
        ident=uid();prefix='audio' if body.purpose=='voice' else 'incoming'
        item={'id':ident,'key':f'{prefix}/{store.scoped_key(client)}/{ident}','name':name,'size':body.size,'purpose':body.purpose,'expires':time.time()+3600,'status':'pending'}
        def reserve(s):
            s['transfers']=[t for t in s.get('transfers',[]) if t['expires']>time.time()]
            pending=[t for t in s['transfers'] if t['status']=='pending']
            if len(pending)>=10:raise ValueError('Ten uploads are already reserved. Retry after they finish or expire.')
            count,size=storage_usage(s)
            if count+len(pending)>=100 or size+sum(t['size'] for t in pending)+body.size>1024*1024*1024:raise ValueError('Client folder limit: 100 files or 1 GB.')
            s['transfers'].append(item)
        store.mutate(client,reserve)
        post=cloud.client('s3').generate_presigned_post(Bucket=cloud.bucket(),Key=item['key'],
            Fields={'Content-Type':'application/octet-stream'},Conditions=[['content-length-range',body.size,body.size],{'Content-Type':'application/octet-stream'}],ExpiresIn=600)
        return {'id':ident,**post}

    @app.post('/api/clients/{client}/transfers/{ident}/complete')
    def complete(client:str,ident:str):
        item=transfer(client,ident)
        if item['status']!='pending':return item.get('result',{'status':item['status']})
        s3=cloud.client('s3');head=s3.head_object(Bucket=cloud.bucket(),Key=item['key'])
        if head['ContentLength']!=item['size']:raise ValueError('Uploaded length differs from reservation.')
        version=head.get('VersionId')
        if not version:raise ValueError('Storage versioning must be enabled before accepting uploads.')
        # Copy a specific version into an immutable key; replaying the upload grant
        # cannot change the committed document or the audio being transcribed.
        key=('audio-ready' if item['purpose']=='voice' else 'files')+f'/{store.scoped_key(client)}/{ident}/{uid()}'
        if item['purpose']=='voice':
            audio=cloud.read(item['key'],version)
            try:
                with wave.open(io.BytesIO(audio),'rb') as wav:
                    frames=wav.getnframes();rate=wav.getframerate()
                    if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or rate!=16000 or frames>120*rate or frames<rate//4:raise ValueError('Use a mono 16 kHz recording between 0.25 and 120 seconds.')
                    if len(wav.readframes(frames+1))!=frames*2:raise ValueError('Incomplete recording.')
            except (wave.Error,EOFError):raise ValueError('Upload a valid WAV voice recording.')
        if item['purpose']=='file':
            raw=cloud.read(item['key'],version)
            if item['name'].lower().endswith(('.docx','.xlsx')):
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    if len(z.infolist())>10000 or sum(i.file_size for i in z.infolist())>100*1024*1024:raise ValueError('Office expansion exceeds supported size.')
            try:parsed(raw,item['name'])
            except Exception as exc:raise ValueError('Could not read this document: '+type(exc).__name__)
        s3.copy_object(Bucket=cloud.bucket(),Key=key,CopySource={'Bucket':cloud.bucket(),'Key':item['key'],'VersionId':version})
        if item['purpose']=='voice':
            job='folio-'+ident
            transcribe=cloud.client('transcribe')
            try:
                transcribe.start_transcription_job(TranscriptionJobName=job,LanguageCode='en-US',
                    MediaFormat=Path(item['name']).suffix[1:],Media={'MediaFileUri':f's3://{cloud.bucket()}/{key}'},
                    OutputBucketName=cloud.bucket(),OutputKey=f'audio-output/{store.scoped_key(client)}/{ident}.json')
            except transcribe.exceptions.ConflictException:
                pass # Retrying completion reuses the same server-created job.
            result={'status':'transcribing'}
        else:
            doc={'id':ident[:20],'name':item['name'],'folder':'Inbox','ext':Path(item['name']).suffix.lower()[1:],'size':len(raw),'revision':1,'hash':digest(raw),'blob':'s3:'+key,'updated':now(),'history':[]}
            result={'status':'complete','document':public_doc(doc)}
        def commit(s):
            t=next(t for t in s['transfers'] if t['id']==ident)
            if t['status']!='pending':return t['result']
            if item['purpose']=='file':
                count,size=storage_usage(s)
                if size+doc['size']>1024*1024*1024 or count>=100:raise ValueError('Client folder quota exceeded.')
                s['documents'].append(doc)
            t.update(status=result['status'],result=result)
            return result
        return store.mutate(client,commit)

    @app.get('/api/clients/{client}/transfers/{ident}/transcript')
    def transcript(client:str,ident:str):
        item=transfer(client,ident)
        if item['purpose']!='voice' or item['status']=='pending':raise HTTPException(404,'No voice recording found.')
        if item['status']=='complete':return item['result']
        transcribe=cloud.client('transcribe');job=transcribe.get_transcription_job(TranscriptionJobName='folio-'+ident)['TranscriptionJob']
        if job['TranscriptionJobStatus']=='FAILED':raise ValueError('The recording could not be transcribed. Try a clearer recording.')
        if job['TranscriptionJobStatus']!='COMPLETED':return {'status':'transcribing'}
        result={'status':'complete','text':json.loads(cloud.read(f'audio-output/{store.scoped_key(client)}/{ident}.json'))['results']['transcripts'][0]['transcript']}
        def finish(s):
            t=next(t for t in s['transfers'] if t['id']==ident);t.update(status='complete',result=result)
        store.mutate(client,finish)
        try:transcribe.delete_transcription_job(TranscriptionJobName='folio-'+ident)
        except transcribe.exceptions.BadRequestException:pass # A concurrent completion may have cleaned it up.
        return result

    @app.post('/api/clients/{client}/speech')
    def speech(client:str,body:Speech):
        if not cloud.bucket():raise ValueError('Voice services are not configured.')
        speech_limit(client)
        result=cloud.client('polly').synthesize_speech(Text=body.text,TextType='text',VoiceId='Joanna',Engine='neural',OutputFormat='mp3')
        with result['AudioStream'] as stream:data=stream.read()
        return Response(data,media_type='audio/mpeg',headers={'Cache-Control':'no-store'})
