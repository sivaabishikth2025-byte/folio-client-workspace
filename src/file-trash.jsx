import React, {useEffect, useRef, useState} from 'react';
import {Trash2, RotateCcw, X, FileText, Loader2} from 'lucide-react';
import './file-trash.css';

export function FileTrashDialog({clientName, file, files, cleanupPending=0, busy, onClose, onDelete, onRestore, onEmpty, onRetryCleanup}) {
 const dialog=useRef(null);
 const [emptyFiles,setEmptyFiles]=useState(null);
 const confirming=!!emptyFiles;
 const size=items=>{const bytes=items.reduce((sum,d)=>sum+d.size,0);return bytes<1048576?`${Math.max(1,Math.round(bytes/1024))} KB`:`${(bytes/1048576).toFixed(1)} MB`};
 const close=()=>{if(confirming)setEmptyFiles(null);else onClose()};
 useEffect(()=>{
  const previous=document.activeElement;
  dialog.current?.querySelector('button')?.focus();
  return ()=>{if(previous?.isConnected)previous.focus()};
 },[]);
 useEffect(()=>{
  if(!busy&&(!dialog.current?.contains(document.activeElement)||document.activeElement.disabled))dialog.current?.querySelector('button')?.focus();
 },[busy,file,files.length,confirming]);
 function keys(event){
  if(event.key==='Escape'){event.stopPropagation();if(!busy)close()}
  if(event.key!=='Tab')return;
  const buttons=[...dialog.current.querySelectorAll('button:not(:disabled)')];
  if(!buttons.length){event.preventDefault();return}
  const first=buttons[0],last=buttons.at(-1);
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
 }
 return <div className="modal-backdrop" onClick={()=>{if(!busy)close()}}>
  <section ref={dialog} className="modal file-trash-dialog" role={file||confirming?'alertdialog':'dialog'} aria-modal="true" aria-labelledby="trash-title" aria-describedby="trash-description" aria-busy={busy} onKeyDown={keys} onClick={e=>e.stopPropagation()}>
   <button className="icon modal-close" aria-label="Close file dialog" disabled={busy} onClick={close}><X size={19}/></button>
   <span className="section-eyebrow">{clientName}</span>
   <div className="trash-emblem"><Trash2 size={23} strokeWidth={1.5}/></div>
   <h2 id="trash-title">{file?'Move this file to Trash?':confirming?'Empty Trash permanently?':'Trash'}</h2>
   <p id="trash-description" className="modal-intro">{file?'This removes the file from your workspace and future source lookups. Pending edits will be dismissed. You can restore the file and its saved versions until you empty Trash.':confirming?`Permanently delete ${emptyFiles.length} file${emptyFiles.length===1?'':'s'} and their saved versions from ${clientName}? This cannot be undone.`:'Deleted files stay here with their saved versions. Restore a file to make it available in this client workspace again.'}</p>
   {confirming?<>
    <div className="trash-purge-files">{emptyFiles.map(d=><div key={d.id}><FileText size={16}/><span>{d.name}<small>{d.folder}</small></span></div>)}</div>
    <p className="trash-note">Active files are kept. Previous chat messages and saved client memory are kept.</p>
    <div className="trash-actions"><button className="secondary" disabled={busy} onClick={()=>setEmptyFiles(null)}>Cancel</button><button className="danger-button" disabled={busy} onClick={async()=>{await onEmpty(emptyFiles);setEmptyFiles(null)}}>{busy?<Loader2 className="spin" size={15}/>:<Trash2 size={15}/>}Delete permanently</button></div>
   </>:file?<>
    <div className="trash-file-summary"><FileText size={21}/><div><strong>{file.name}</strong><small>{file.folder} · Version {file.revision}</small></div></div>
    <p className="trash-note">Previous chat messages are kept. Any unsaved changes in the editor will be discarded.</p>
    <div className="trash-actions"><button className="secondary" disabled={busy} onClick={onClose}>Keep file</button><button className="danger-button" disabled={busy} onClick={()=>onDelete(file)}>{busy?<Loader2 className="spin" size={15}/>:<Trash2 size={15}/>}Move to Trash</button></div>
   </>:<>
    <div className="trash-list">{files.length?files.map(d=><div className="trash-item" key={d.id}><FileText size={19}/><div><strong>{d.name}</strong><small>{d.folder} · Deleted {new Date(d.deleted_at).toLocaleDateString(undefined,{month:'short',day:'numeric'})}</small></div><button className="secondary" disabled={busy} aria-label={`Restore ${d.name}`} onClick={()=>onRestore(d)}><RotateCcw size={14}/>Restore</button></div>):<div className="trash-empty"><CheckEmpty/><strong>No deleted files</strong><p>Files you delete from {clientName} will appear here.</p></div>}</div>
    <p className="trash-note">Files in Trash still count toward the 100-file / 1 GB client storage limit. Previous chat messages are kept.</p>
    {cleanupPending>0&&<div className="trash-cleanup" role="status"><p>{cleanupPending} permanently deleted file{cleanupPending===1?'':'s'} still need storage cleanup. Their space will be released when cleanup finishes.</p><button className="secondary" disabled={busy} onClick={onRetryCleanup}>{busy?<Loader2 className="spin" size={14}/>:<RotateCcw size={14}/>}Retry cleanup</button></div>}
    <div className="trash-empty-footer"><span>{files.length?`${files.length} file${files.length===1?'':'s'} · ${size(files)}`:'Trash is empty'}</span><button className="danger-button" disabled={busy||!files.length} onClick={()=>setEmptyFiles(files.map(d=>({...d})))}><Trash2 size={15}/>Empty Trash</button></div>
   </>}
  </section>
 </div>;
}
function CheckEmpty(){return <RotateCcw size={25} strokeWidth={1.4}/>}
