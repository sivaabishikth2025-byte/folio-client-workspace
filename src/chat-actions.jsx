import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {MoreHorizontal, MessageSquare, Trash2, X, Loader2} from 'lucide-react';
import './chat-actions.css';

export function ChatRow({chat,selected,time,onOpen,onMenu,menuOpen}) {
 const menu=(event,pointer=false)=>{
  event.preventDefault();event.stopPropagation();
  const rect=event.currentTarget.getBoundingClientRect();
  onMenu({chat,x:pointer?event.clientX:rect.right-18,y:pointer?event.clientY:rect.bottom+4,trigger:event.target.closest('button')||event.currentTarget.querySelector('button')||event.currentTarget});
 };
 return <div className={`chat-row ${selected?'selected':''}`} onContextMenu={e=>menu(e,true)} onKeyDown={e=>{if(e.key==='ContextMenu'||(e.shiftKey&&e.key==='F10'))menu(e)}}>
  <button className={`chat-entry ${selected?'selected':''}`} onClick={onOpen} title={chat.title} aria-current={selected?'true':undefined}><MessageSquare size={15}/><span>{chat.title}<small>{time}</small></span></button>
  <button className="chat-options" aria-label={`Options for ${chat.title}`} title="Chat options" aria-haspopup="menu" aria-expanded={menuOpen} onClick={menu}><MoreHorizontal size={17}/></button>
 </div>;
}

export function ChatContextMenu({target,onClose,onDelete}) {
 const element=useRef(null),[position,setPosition]=useState({left:target.x,top:target.y});
 useLayoutEffect(()=>{
  const rect=element.current.getBoundingClientRect();
  setPosition({left:Math.max(8,Math.min(target.x,innerWidth-rect.width-8)),top:Math.max(8,Math.min(target.y,innerHeight-rect.height-8))});
  element.current.querySelector('button')?.focus({preventScroll:true});
 },[target]);
 useEffect(()=>{
  const outside=e=>{if(!element.current?.contains(e.target))onClose()};
  const close=()=>onClose();
  document.addEventListener('pointerdown',outside);window.addEventListener('resize',close);window.addEventListener('scroll',close,true);
  return()=>{document.removeEventListener('pointerdown',outside);window.removeEventListener('resize',close);window.removeEventListener('scroll',close,true)};
 },[onClose]);
 function closeWithFocus(){onClose();if(target.trigger?.isConnected)target.trigger.focus()}
 return <div ref={element} className="chat-context-menu" role="menu" aria-label="Chat options" style={position} onKeyDown={e=>{
  if(e.key==='Escape'){e.stopPropagation();closeWithFocus()}
  if(e.key==='Tab'){e.preventDefault();closeWithFocus()}
  if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();element.current.querySelector('button')?.focus()}
 }}>
  <span className="chat-menu-caption">THIS CONVERSATION</span>
  <button role="menuitem" onClick={()=>onDelete(target.chat)}><Trash2 size={16}/>Delete chat</button>
 </div>;
}

export function DeleteChatDialog({chat,clientName,busy,onClose,onDelete}) {
 const dialog=useRef(null);
 useEffect(()=>{const previous=document.activeElement;dialog.current.querySelector('button')?.focus();return()=>{if(previous?.isConnected)previous.focus()}},[]);
 useEffect(()=>{if(!busy&&!dialog.current.contains(document.activeElement))dialog.current.querySelector('button')?.focus()},[busy]);
 function keys(e){
  if(e.key==='Escape'){e.stopPropagation();if(!busy)onClose()}
  if(e.key!=='Tab')return;
  const buttons=[...dialog.current.querySelectorAll('button:not(:disabled)')];
  if(!buttons.length){e.preventDefault();return}
  if(e.shiftKey&&document.activeElement===buttons[0]){e.preventDefault();buttons.at(-1).focus()}
  else if(!e.shiftKey&&document.activeElement===buttons.at(-1)){e.preventDefault();buttons[0].focus()}
 }
 return <div className="modal-backdrop" onClick={()=>{if(!busy)onClose()}}><section ref={dialog} className="modal chat-delete-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-chat-title" aria-describedby="delete-chat-description" aria-busy={busy} onKeyDown={keys} onClick={e=>e.stopPropagation()}>
  <button className="icon modal-close" aria-label="Close delete chat dialog" disabled={busy} onClick={onClose}><X size={19}/></button>
  <span className="section-eyebrow">{clientName}</span><div className="trash-emblem"><MessageSquare size={23} strokeWidth={1.5}/></div>
  <h2 id="delete-chat-title">Delete this chat?</h2>
  <p id="delete-chat-description" className="modal-intro">This permanently removes the conversation and its suggestions. It cannot be undone.</p>
  <div className="chat-delete-title"><MessageSquare size={18}/><strong>{chat.title}</strong></div>
  <p className="trash-note">Files, accepted edits and saved client memory are kept. Any response still running in this chat will be stopped.</p>
  <div className="trash-actions"><button className="secondary" disabled={busy} onClick={onClose}>Cancel</button><button className="danger-button" disabled={busy} onClick={()=>onDelete(chat)}>{busy?<Loader2 size={15} className="spin"/>:<Trash2 size={15}/>}Delete chat</button></div>
 </section></div>;
}
