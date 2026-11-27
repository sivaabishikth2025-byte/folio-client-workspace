import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {safeMarkdown} from './auth.jsx';
import {remarkEvidence} from './evidence.mjs';
import './evidence.css';

export function EvidenceMarkdown({text,citations=[],activeSource,onSource}) {
 return <ReactMarkdown skipHtml remarkPlugins={[remarkGfm,[remarkEvidence,{count:citations.length}]]} components={{...safeMarkdown,a:({href,children})=>{
  const match=href?.match(/^#folio-evidence-(\d+)$/);
  const source=match&&citations[Number(match[1])-1];
  return source?<button type="button" className="inline-citation" aria-label={`Open source ${match[1]}: ${source.name}`} aria-pressed={activeSource===source.id} title={`${source.name} · ${source.locator}`} onClick={()=>onSource(source)}>{children}</button>:<span>{children}</span>;
 }}}>{text}</ReactMarkdown>;
}
