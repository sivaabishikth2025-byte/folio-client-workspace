import React,{useEffect,useRef,useState} from 'react';
import {Mic,Square,Loader2,Volume2} from 'lucide-react';

export async function directUpload(api,client,file,purpose='file'){
 const intent=await api(`/clients/${client}/transfers`,{method:'POST',body:JSON.stringify({name:file.name,size:file.size,purpose})});
 const form=new FormData();for(const [k,v] of Object.entries(intent.fields))form.append(k,v);form.append('file',file);
 const response=await fetch(intent.url,{method:'POST',body:form});if(!response.ok)throw new Error('Upload did not finish. Please retry.');
 const result=await api(`/clients/${client}/transfers/${intent.id}/complete`,{method:'POST'});return {...result,id:intent.id};
}

export function VoiceInput({api,client,onText,onError,disabled}){
 const [phase,setPhase]=useState('idle');const recorder=useRef(),active=useRef(true),timer=useRef(),stream=useRef();
 useEffect(()=>()=>{active.current=false;clearTimeout(timer.current);if(recorder.current?.state==='recording')recorder.current.stop();stream.current?.getTracks().forEach(t=>t.stop())},[]);
 async function start(){
  try{
   if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('Recording is unavailable in this browser. Use a current Chrome, Edge or Safari browser.');
   stream.current=await navigator.mediaDevices.getUserMedia({audio:true});
   if(!active.current){stream.current.getTracks().forEach(t=>t.stop());return}
   const mime=['audio/webm','audio/mp4','audio/ogg'].find(m=>MediaRecorder.isTypeSupported(m));
   if(!mime)throw new Error('This browser has no supported audio recording format.');
   const r=new MediaRecorder(stream.current,{mimeType:mime});recorder.current=r;const chunks=[];
   r.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};
   r.onstop=async()=>{
    clearTimeout(timer.current);stream.current.getTracks().forEach(t=>t.stop());if(!active.current)return;setPhase('transcribing');
    try{
     const file=await speechWav(new Blob(chunks,{type:mime}));
     if(file.size>8*1024*1024)throw new Error('Recording exceeds 8 MB. Please use a shorter clip.');
     const result=await directUpload(api,client,file,'voice');
     for(let i=0;i<90&&active.current;i++){
      const response=await api(`/clients/${client}/transfers/${result.id}/transcript`);
      if(response.status==='complete'){if(active.current){onText(response.text);setPhase('idle')}return}
      await new Promise(resolve=>setTimeout(resolve,2000));
     }
     if(active.current)throw new Error('Transcription is taking longer than expected. Please try again.');
    }catch(e){if(active.current){onError(e.message);setPhase('idle')}}
   };
   r.start();setPhase('recording');timer.current=setTimeout(()=>{if(r.state==='recording')r.stop()},120000);
  }catch(e){stream.current?.getTracks().forEach(t=>t.stop());onError(e.name==='NotAllowedError'?'Microphone access was declined. You can still type your message.':e.message);setPhase('idle')}
 }
 return <div className="voice-input"><button type="button" className={'voice-button '+phase} disabled={disabled||phase==='transcribing'} aria-label={phase==='recording'?'Stop recording':phase==='transcribing'?'Transcribing with AWS':'Dictate a message'} onClick={()=>phase==='recording'?recorder.current.stop():start()}>{phase==='transcribing'?<Loader2 size={17} className="spin"/>:phase==='recording'?<Square size={17}/>:<Mic size={17}/>}<span>{phase==='recording'?'Stop':phase==='transcribing'?'Transcribing…':'Dictate'}</span></button>{phase==='recording'&&<span className="voice-status">Recording · max 2 min</span>}</div>
}

export function ReadAloud({client,text}){
 const [state,setState]=useState('idle');const audio=useRef(),url=useRef(),controller=useRef();
 useEffect(()=>()=>{controller.current?.abort();audio.current?.pause();if(url.current)URL.revokeObjectURL(url.current)},[]);
 async function read(){
  if(state==='playing'){audio.current.pause();setState('idle');return}
  setState('loading');controller.current=new AbortController();
  try{
   const plain=text.replace(/\[\d+\]/g,'').replace(/[*#`|]/g,'').slice(0,3000);
   const r=await fetch(`/api/clients/${client}/speech`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:plain}),signal:controller.current.signal});
   if(!r.ok)throw new Error('Speech unavailable. Try again.');
   if(url.current)URL.revokeObjectURL(url.current);url.current=URL.createObjectURL(await r.blob());audio.current=new Audio(url.current);audio.current.onended=()=>setState('idle');await audio.current.play();setState('playing');
  }catch(e){if(e.name!=='AbortError')setState('error')}
 }
 return <button className="read-aloud" onClick={read} disabled={state==='loading'}>{state==='loading'?<Loader2 className="spin" size={14}/>:state==='playing'?<Square size={14}/>:<Volume2 size={14}/>} {state==='playing'?'Stop audio':state==='error'?'Retry audio':text.length>3000?'Read first part':'Read aloud'}</button>
}

// Normalize to bounded PCM so the server can verify duration before billing speech.
async function speechWav(blob){
 const context=new AudioContext();let audio;try{audio=await context.decodeAudioData(await blob.arrayBuffer())}finally{await context.close()}
 const seconds=Math.min(audio.duration,120),count=Math.floor(seconds*16000),buffer=new ArrayBuffer(44+count*2),view=new DataView(buffer);
 function text(at,value){for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i))}
 text(0,'RIFF');view.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,count*2,true);
 const channels=Array.from({length:audio.numberOfChannels},(_,i)=>audio.getChannelData(i));
 for(let i=0;i<count;i++){const at=Math.min(Math.floor(i*audio.sampleRate/16000),audio.length-1);const sample=channels.reduce((sum,c)=>sum+c[at],0)/channels.length;view.setInt16(44+i*2,Math.max(-1,Math.min(1,sample))*32767,true)}
 return new File([buffer],'Recording.wav',{type:'audio/wav'});
}
