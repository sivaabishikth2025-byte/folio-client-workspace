import React, {useEffect, useState} from 'react';
import {Layers3, LockKeyhole, ArrowRight, ArrowLeft, Loader2, ShieldCheck, MailCheck, Eye, EyeOff} from 'lucide-react';

const headings={signin:'Welcome to Folio.',signup:'Make room for clarity.',verify:'Check your inbox.',forgot:'Let’s get you back in.',reset:'Choose a fresh password.',challenge:'Make this account yours.'};
const descriptions={signin:'Sign in to your client workspace.',signup:'Create your account and start your own private workspace.',verify:'Enter the six-digit code sent to your email to verify your account.',forgot:'We’ll send a reset code to your verified email address.',reset:'Enter your email code and a new password.',challenge:'Choose a new password to finish signing in.'};
const labels={signin:'Sign in',signup:'Create account',verify:'Verify email',forgot:'Send reset code',reset:'Reset password',challenge:'Set password & sign in'};
const passwordHint='At least 12 characters, with uppercase and lowercase letters, a number and a symbol.';

export function AuthGate({children}) {
 const [mode,setMode]=useState('signin'),[username,setUsername]=useState(''),[name,setName]=useState('');
 const [session,setSession]=useState(null),[password,setPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[code,setCode]=useState(''),[challenge,setChallenge]=useState('');
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState(''),[visible,setVisible]=useState(false),[cooldown,setCooldown]=useState(0);
 useEffect(()=>{
  fetch('/api/auth/session').then(r=>{if(!r.ok)throw new Error('Workspace is temporarily unavailable. Please reload.');return r.json()}).then(setSession).catch(e=>setError(e.message));
  const expire=()=>{setSession({required:true,authenticated:false,mode:'cognito',signup:true});setMode('signin');setPassword('');setConfirmation('');setChallenge('');setCode('');setNotice('');setError('')};
  window.addEventListener('folio-auth-required',expire);return()=>window.removeEventListener('folio-auth-required',expire);
 },[]);
 useEffect(()=>{if(!cooldown)return;const timer=setTimeout(()=>setCooldown(n=>Math.max(0,n-1)),1000);return()=>clearTimeout(timer)},[cooldown]);
 function navigate(next){setMode(next);setPassword('');setConfirmation('');setCode('');setChallenge('');setError('');setNotice('');setVisible(false)}
 async function request(path,body){
  const r=await fetch('/api/auth/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await r.json();
  if(!r.ok){const detail=data.detail;const e=new Error(typeof detail==='string'?detail:detail?.message||'Check the form details and try again.');e.code=detail?.code;throw e}
  return data;
 }
 async function submit(e){
  e.preventDefault();setError('');setNotice('');
  if(['signup','reset','challenge'].includes(mode)&&password!==confirmation){setError('The passwords don’t match.');return}
  setBusy(true);
  try{
   if(mode==='signup'){
    const data=await request('signup',{name,email:username,password});
    setUsername(data.username);setPassword('');setConfirmation('');setMode(data.confirmation_required?'verify':'signin');setCooldown(60);
    setNotice(data.confirmation_required?'Check your inbox and spam folder. Already have an account? Return to sign in.':'Account created. You can sign in now.');
   }else if(mode==='verify'){
    await request('confirm',{username,code});navigate('signin');setNotice('Email verified. Your account is ready—sign in to get started.');
   }else if(mode==='forgot'){
    const data=await request('forgot',{username});navigate('reset');setNotice(data.message);setCooldown(60);
   }else if(mode==='reset'){
    await request('reset',{username,code,password});navigate('signin');setNotice('Password updated. Sign in with your new password.');
   }else{
    const data=await request('login',{username,password:mode==='challenge'?'':password,challenge_session:challenge,new_password:mode==='challenge'?password:''});
    if(data.challenge){setChallenge(data.session);setPassword('');setConfirmation('');setMode('challenge');return}
    setPassword('');setConfirmation('');setChallenge('');setSession({required:true,authenticated:true});
   }
  }catch(e){
   if(e.code==='EMAIL_NOT_VERIFIED'){setMode('verify');setPassword('');setConfirmation('');setNotice('Enter the code from your email, or request a new code below.')}
   else setError(e.message);
  }finally{setBusy(false)}
 }
 async function resend(){
  setBusy(true);setError('');setNotice('');
  try{const data=await request(mode==='verify'?'resend':'forgot',{username});setNotice(data.message);setCooldown(60)}catch(e){setError(e.message)}finally{setBusy(false)}
 }
 if(session?.authenticated)return children;
 if(!session)return <main className="auth-pending" aria-live="polite"><div className="auth-brand"><Layers3 size={28}/><span>folio / workspace</span></div>{error?<><p role="alert">{error}</p><button className="primary" onClick={()=>window.location.reload()}>Try again</button></>:<p><Loader2 size={17} className="spin"/>Opening your workspace…</p>}</main>;
 const newPassword=['signup','reset','challenge'].includes(mode);
 const hasPassword=newPassword||mode==='signin';
 const hasUsername=['signin','signup','forgot'].includes(mode);
 const cognito=session?.mode==='cognito';
 return <main className="auth-page">
  <section className="auth-story"><div className="auth-brand"><Layers3 size={30}/><span>folio / workspace</span></div>
   <div><span className="section-eyebrow">A LITTLE LESS COMPLEXITY</span><h1>Your clients.<br/>Your confidence.</h1><p>A private place for the files, answers, and decisions that matter.</p><div className="auth-story-points"><span>All the context, together.</span><span>Answers you can trace.</span><span>Every change, your call.</span></div></div>
   <span className="auth-seal"><ShieldCheck size={18}/>Private by default. Built for thoughtful work.</span>
  </section>
  <section className="auth-entry"><div className="auth-card">
   <span className="auth-lock">{mode==='verify'?<MailCheck size={25}/>:<LockKeyhole size={25}/>}</span>
   <span className="section-eyebrow">{mode==='signup'?'YOUR NEXT CHAPTER STARTS HERE':'YOUR PRIVATE WORKSPACE'}</span>
   <h2>{headings[mode]}</h2><p>{descriptions[mode]}</p>
   {['verify','reset','challenge'].includes(mode)&&<p className="auth-recipient">{username}</p>}
   <form onSubmit={submit}>
    {mode==='signup'&&<><label htmlFor="account-name">Full name</label><input id="account-name" autoComplete="name" value={name} onChange={e=>setName(e.target.value)} required maxLength={100}/></>}
    {hasUsername&&(cognito||mode!=='signin')&&<><label htmlFor="username">{mode==='signup'?'Email address':'Email or username'}</label><input id="username" type={mode==='signup'?'email':'text'} autoComplete={mode==='signup'?'email':'username'} autoCapitalize="none" spellCheck={false} value={username} onChange={e=>setUsername(e.target.value)} required maxLength={128}/></>}
    {['verify','reset'].includes(mode)&&<><label htmlFor="verification-code">Verification code</label><input id="verification-code" className="auth-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={e=>setCode(e.target.value.replace(/\D/g,''))} required/></>}
    {hasPassword&&<><div className="auth-label-row"><label htmlFor="workspace-password">{newPassword?'New password':'Password'}</label>{mode==='signin'&&cognito&&<button type="button" className="auth-text-button" disabled={busy} onClick={()=>navigate('forgot')}>Forgot password?</button>}</div>
     <div className="auth-password-field"><input id="workspace-password" type={visible?'text':'password'} autoComplete={newPassword?'new-password':'current-password'} value={password} onChange={e=>setPassword(e.target.value)} required minLength={newPassword?12:1} maxLength={256} aria-describedby={newPassword?'password-hint':undefined}/><button type="button" aria-label={visible?'Hide password':'Show password'} onClick={()=>setVisible(!visible)}>{visible?<EyeOff size={18}/>:<Eye size={18}/>}</button></div>
    </>}
    {newPassword&&<><p id="password-hint" className="auth-password-hint">{passwordHint}</p><label htmlFor="confirm-password">Confirm password</label><input id="confirm-password" type={visible?'text':'password'} autoComplete="new-password" value={confirmation} onChange={e=>setConfirmation(e.target.value)} required minLength={12} maxLength={256}/></>}
    {notice&&<p className="auth-notice" role="status">{notice}</p>}{error&&<p className="auth-error" role="alert">{error}</p>}
    <button className="primary" disabled={busy||!session}>{busy?<><Loader2 size={18} className="spin"/>Please wait…</>:<>{labels[mode]}<ArrowRight size={18}/></>}</button>
   </form>
   {['verify','reset'].includes(mode)&&<div className="auth-resend"><span>Didn’t receive a code?</span><button className="auth-text-button" disabled={busy||cooldown>0} onClick={resend}>{cooldown>0?`Resend in ${cooldown}s`:'Resend code'}</button></div>}
   {mode==='signin'&&cognito&&<div className="auth-switch"><span>New to Folio?</span><button type="button" className="auth-create" disabled={busy} onClick={()=>navigate('signup')}>Create account<ArrowRight size={16}/></button></div>}
   {mode!=='signin'&&<button type="button" className="auth-back" disabled={busy} onClick={()=>navigate('signin')}><ArrowLeft size={15}/>{mode==='signup'?'Already have an account? Sign in':'Back to sign in'}</button>}
   <p className="auth-note"><ShieldCheck size={14}/>Secured by Amazon Cognito.{mode==='signup'&&' Your account includes a private copy of the sample client folders.'}</p>
  </div></section>
 </main>;
}

// No remote images, raw HTML or clickable generated URLs: file citations are server-validated cards.
export const safeMarkdown = {
 img:({alt})=><span className="blocked-image">[External image omitted{alt?`: ${alt}`:''}]</span>,
 a:({children})=><span>{children}</span>
};
