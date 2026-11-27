// Auto-navigation uses only the answer's server-verified citations and current files.
export function firstEvidence(message,documents) {
 if(message?.error||message?.scope==='general')return null;
 const available=new Set(documents.map(doc=>doc.id));
 return (message?.citations||[]).find(source=>available.has(source.document_id)&&typeof source.locator==='string'&&source.hash&&Number.isInteger(source.revision))||null;
}

// Turn numbered references into internal links; leave code and existing links alone.
export function remarkEvidence({count=0}={}) {
 return tree=>{
  function walk(parent){
   if(!parent.children||['link','linkReference','code','inlineCode','html'].includes(parent.type))return;
   parent.children=parent.children.flatMap(node=>{
    if(node.type!=='text'){walk(node);return [node]}
    const pieces=[];let cursor=0;
    for(const match of node.value.matchAll(/\[(\d+)\]/g)){
     const number=Number(match[1]);if(number<1||number>count)continue;
     if(match.index>cursor)pieces.push({type:'text',value:node.value.slice(cursor,match.index)});
     pieces.push({type:'link',url:`#folio-evidence-${number}`,children:[{type:'text',value:String(number)}]});
     cursor=match.index+match[0].length;
    }
    if(!pieces.length)return [node];
    if(cursor<node.value.length)pieces.push({type:'text',value:node.value.slice(cursor)});
    return pieces;
   });
  }
  walk(tree);
 };
}
