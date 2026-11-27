// Creates and removes only disposable fixtures on an isolated local server.
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
const base=process.env.TRASH_TEST_URL||'http://127.0.0.1:8008';
assert.equal(new URL(base).hostname,'127.0.0.1');
const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const api=page.request,checks=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
let fixture;
async function json(r){assert(r.ok(),`HTTP ${r.status()}`);return r.json()}
try {
 fixture=await json(await api.post(base+'/api/clients',{data:{name:'Empty Trash QA '+Date.now()}}));
 const cp=base+'/api/clients/'+fixture.id;
 const docs=[];
 for(const name of ['Delete one.txt','Delete two.txt','Keep active.txt']){
  const [d]=await json(await api.post(cp+'/upload',{multipart:{files:{name,mimeType:'text/plain',buffer:Buffer.from(name)}}}));docs.push(d);
 }
 for(const d of docs.slice(0,2))await json(await api.delete(cp+'/documents/'+d.id,{data:{revision:1,lifecycle_version:0}}));
 await page.goto(base);await page.locator('.client-button').click();await page.locator('.client-menu button').filter({hasText:fixture.name}).click();
 await page.getByRole('button',{name:'Trash 2',exact:true}).click();
 await page.getByRole('button',{name:'Empty Trash',exact:true}).click();
 await page.getByRole('alertdialog').waitFor();
 assert((await page.getByRole('alertdialog').innerText()).includes('This cannot be undone'));
 await page.getByRole('button',{name:'Cancel',exact:true}).click();
 assert.equal((await json(await api.get(cp))).trash.length,2);
 checks.push('Confirmation names client and files; Cancel keeps Trash unchanged');
 await page.getByRole('button',{name:'Empty Trash',exact:true}).click();
 // Simulate a second tab restoring a file after this confirmation was opened.
 await json(await api.post(cp+'/documents/'+docs[0].id+'/restore',{data:{revision:1,lifecycle_version:1}}));
 await page.getByRole('button',{name:'Delete permanently',exact:true}).click();
 await page.getByText('Trash changed since you opened this confirmation. Review the current files and try again.',{exact:true}).waitFor();
 assert.equal((await json(await api.get(cp))).trash.length,1);
 checks.push('Stale confirmation stops without removing any other file');
 await page.getByRole('button',{name:'Empty Trash',exact:true}).click();
 await page.setViewportSize({width:390,height:844});
 await page.locator('.file-trash-dialog').screenshot({path:'test-results/empty-trash-mobile.png'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.emulateMedia({colorScheme:'dark'});
 await page.locator('.file-trash-dialog').screenshot({path:'test-results/empty-trash-dark.png'});
 await page.keyboard.press('Escape');assert.equal(await page.getByRole('alertdialog').count(),0);
 checks.push('Mobile and dark confirmation fit; Escape returns to Trash');
 await page.getByRole('button',{name:'Empty Trash',exact:true}).click();
 await page.getByRole('button',{name:'Delete permanently',exact:true}).click();
 await page.getByText('No deleted files',{exact:true}).waitFor();
 assert(await page.getByRole('button',{name:'Empty Trash',exact:true}).isDisabled());
 const state=await json(await api.get(cp));assert.equal(state.documents.length,2);assert.equal(state.cleanup_pending,0);
 assert.equal((await api.get(cp+'/documents/'+docs[1].id+'/download')).status(),404);
 assert.equal((await api.post(cp+'/documents/'+docs[1].id+'/restore',{data:{revision:1,lifecycle_version:1}})).status(),404);
 assert.equal((await api.get(cp+'/documents/'+docs[2].id+'/download')).status(),200);
 checks.push('Permanent deletion removes only confirmed Trash, disables empty action, preserves active/restored files');
 await page.setViewportSize({width:1440,height:1000});await page.reload();await page.locator('.client-button').click();await page.locator('.client-menu button').filter({hasText:fixture.name}).click();
 await page.getByRole('button',{name:'Trash 0',exact:true}).click();await page.getByText('No deleted files',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);checks.push('Empty state persists through reload with no browser errors');
 await writeFile('test-results/empty-trash-browser-checks.json',JSON.stringify({tested_at:new Date().toISOString(),environment:'isolated local fixtures',checks,console_errors:errors},null,2)+'\n');
 console.log(JSON.stringify({passed:checks.length,checks}));
} finally {
 if(fixture)await api.post(base+'/api/clients/'+fixture.id+'/archive',{data:{name:fixture.name}});
 await browser.close();
}
