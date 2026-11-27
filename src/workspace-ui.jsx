import React, {useEffect, useRef, useState} from 'react';
import {RefreshCw, ShieldCheck, Sparkles} from 'lucide-react';

export function modelLabel(model){
 const value=String(model||'');
 const current=value.match(/claude[-.](sonnet|opus|haiku)[-.](\d+)(?:[-.](\d+))?/i);
 if(current)return `Claude ${current[1][0].toUpperCase()+current[1].slice(1)} ${current[2]}${current[3]?'.'+current[3]:''}`;
 const legacy=value.match(/claude[-.](\d+)[-.](\d+)[-.](sonnet|opus|haiku)/i);
 if(legacy)return `Claude ${legacy[3][0].toUpperCase()+legacy[3].slice(1)} ${legacy[1]}.${legacy[2]}`;
 return value||'Claude';
}
const clamp=(value,min,max)=>Math.min(max,Math.max(min,value));
function storedWidth(key,fallback){try{const value=Number(localStorage.getItem(key));return Number.isFinite(value)&&value>0?value:fallback}catch{return fallback}}
export function useWorkspaceLayout(sidebar){
 const [viewport,setViewport]=useState(window.innerWidth);
 const [nav,setNav]=useState(()=>storedWidth('folio-sidebar-width',280));
 const [detail,setDetail]=useState(()=>storedWidth('folio-detail-width',480));
 useEffect(()=>{const resize=()=>setViewport(window.innerWidth);window.addEventListener('resize',resize);return()=>window.removeEventListener('resize',resize)},[]);
 useEffect(()=>{try{localStorage.setItem('folio-sidebar-width',String(nav))}catch{}},[nav]);
 useEffect(()=>{try{localStorage.setItem('folio-detail-width',String(detail))}catch{}},[detail]);
 const navMax=Math.max(240,Math.min(440,viewport-560));
 const navWidth=clamp(nav,240,navMax);
 const detailMax=Math.max(320,Math.min(760,viewport-(sidebar?navWidth+8:0)-408));
 return {navWidth,navMax,setNav,detailWidth:clamp(detail,320,detailMax),detailMax,setDetail};
}
export function ResizeHandle({kind,value,min,max,onChange,defaultValue,reverse=false}){
 const drag=useRef(null);
 useEffect(()=>()=>document.documentElement.classList.remove('is-resizing'),[]);
 const change=next=>onChange(clamp(next,min,max));
 function finish(e){drag.current=null;document.documentElement.classList.remove('is-resizing');if(e.currentTarget.hasPointerCapture(e.pointerId))e.currentTarget.releasePointerCapture(e.pointerId)}
 return <div className={'resize-handle resize-'+kind} role="separator" aria-orientation="vertical" aria-label={kind==='sidebar'?'Resize sidebar':'Resize document panel'} aria-controls={kind==='sidebar'?'workspace-sidebar':'workspace-detail'} aria-valuenow={Math.round(value)} aria-valuemin={min} aria-valuemax={max} tabIndex={0} title="Drag to resize. Arrow keys adjust width; double-click resets."
  onPointerDown={e=>{if(e.button!==0)return;e.preventDefault();drag.current={x:e.clientX,value};e.currentTarget.setPointerCapture(e.pointerId);document.documentElement.classList.add('is-resizing')}}
  onPointerMove={e=>{if(drag.current)change(drag.current.value+(e.clientX-drag.current.x)*(reverse?-1:1))}}
  onPointerUp={finish} onPointerCancel={finish} onLostPointerCapture={()=>{drag.current=null;document.documentElement.classList.remove('is-resizing')}}
  onDoubleClick={()=>change(defaultValue)} onKeyDown={e=>{const step=e.shiftKey?40:16;const actions={ArrowLeft:value+(reverse?step:-step),ArrowRight:value+(reverse?-step:step),Home:min,End:max};if(e.key in actions){e.preventDefault();change(actions[e.key])}}}><span/></div>;
}

export function WorkspaceSettings({status,connection,appearance,setAppearance,currentPassword,setCurrentPassword,nextPassword,setNextPassword,changePassword,refresh}){
 return <>
  <span className="section-eyebrow">YOUR WORKSPACE</span><h2 id="modal-title">Workspace settings</h2><p className="modal-intro">Set your appearance and account preferences.</p>

  <section className="settings-section"><div className="settings-section-heading"><h3>Assistant</h3><button className="icon" aria-label="Refresh assistant availability" onClick={refresh}><RefreshCw size={16}/></button></div><div className="model-settings-list"><div className="model-setting"><Sparkles size={19}/><span><strong>{modelLabel(connection?.model)}</strong><small>Included with your workspace</small></span><span className={'status-pill '+(connection?.configured?'connected':'')}>{connection?.configured?'Ready':'Unavailable'}</span></div></div></section>
  {status?.account_mode==='cognito'&&<details className="account-password settings-section"><summary>Change account password</summary><input type="password" autoComplete="current-password" aria-label="Current password" placeholder="Current password" value={currentPassword} onChange={e=>setCurrentPassword(e.target.value)}/><input type="password" autoComplete="new-password" aria-label="New account password" placeholder="New password (12+ characters)" value={nextPassword} onChange={e=>setNextPassword(e.target.value)}/><button className="secondary" disabled={!currentPassword||nextPassword.length<12} onClick={changePassword}>Update password</button></details>}
  <p className="settings-note"><ShieldCheck size={16}/>Changes are saved automatically. Voice uses AWS to transcribe recordings and read answers aloud.</p>
 </>;
}
