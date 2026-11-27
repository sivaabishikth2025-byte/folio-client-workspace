import test from 'node:test';
import assert from 'node:assert/strict';
import {firstEvidence,remarkEvidence} from '../src/evidence.mjs';

const source={id:'one::p2',document_id:'one',locator:'p2',hash:'abc',revision:1};
test('auto-opening uses the first available current-client source in citation order',()=>{
 const second={...source,id:'two::p3',document_id:'two',locator:'p3'};
 assert.equal(firstEvidence({scope:'client',citations:[second,source]},[{id:'one'}]),source);
 assert.equal(firstEvidence({scope:'mixed',citations:[second,source]},[{id:'one'},{id:'two'}]),second);
});
test('general, error and source-free answers never auto-open a document',()=>{
 for(const message of [{scope:'general',citations:[source]},{error:true,citations:[source]},{scope:'client',citations:[]}])assert.equal(firstEvidence(message,[{id:'one'}]),null);
});
test('absent files and incomplete references cannot trigger navigation',()=>{
 assert.equal(firstEvidence({citations:[source]},[]),null);
 for(const missing of ['locator','hash','revision']){
  const bad={...source};delete bad[missing];assert.equal(firstEvidence({citations:[bad]},[{id:'one'}]),null);
 }
});
const text=value=>({type:'text',value});
test('numbered citations become links only within the verified citation range',()=>{
 const paragraph={type:'paragraph',children:[text('See [1], [2], [0] and [8].')]};
 remarkEvidence({count:2})({type:'root',children:[paragraph]});
 assert.deepEqual(paragraph.children,[text('See '),{type:'link',url:'#folio-evidence-1',children:[text('1')]},text(', '),{type:'link',url:'#folio-evidence-2',children:[text('2')]},text(', [0] and [8].')]);
});
test('citations in formatting are linked while code and existing links stay untouched',()=>{
 const protectedNodes=[{type:'inlineCode',value:'[1]'},{type:'code',value:'[1]'},{type:'link',url:'https://example.com',children:[text('[1]')]},{type:'linkReference',identifier:'one',children:[text('[1]')]}];
 const original=structuredClone(protectedNodes),bold={type:'strong',children:[text('[1]')]};
 remarkEvidence({count:1})({type:'root',children:[...protectedNodes,bold]});
 assert.deepEqual(protectedNodes,original);assert.equal(bold.children[0].url,'#folio-evidence-1');
});
test('a response without verified citations leaves Markdown text unchanged',()=>{
 const tree={type:'root',children:[{type:'paragraph',children:[text('[1] remains plain text.')]}]},before=structuredClone(tree);
 remarkEvidence()(tree);assert.deepEqual(tree,before);
});
