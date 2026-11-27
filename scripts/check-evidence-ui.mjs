// Deterministic browser regression against the isolated fixture-model server.
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const base=process.env.EVIDENCE_TEST_URL||'http://127.0.0.1:8008';assert.equal(new URL(base).hostname,'127.0.0.1');
const browser=await chromium.launch({headless:true,channel:process.env.PLAYWRIGHT_CHANNEL||'chrome'});
const page=await browser.newPage({viewport:{width:1440,height:1000}}),checks=[],errors=[];
await page.addInitScript(()=>localStorage.setItem('folio-provider','anthropic'));
page.on('pageerror',e=>errors.push(e.message));
const api=page.request;const uploads=[];
async function json(r){assert(r.ok(),`HTTP ${r.status()}`);return r.json()}
async function start(message){
 await page.getByRole('button',{name:/^New conversation/}).first().click();
 await page.getByRole('textbox',{name:'Message Folio',exact:true}).fill(message);
 await page.getByRole('button',{name:'Send message',exact:true}).click();
}
async function complete(){await expect(page.locator('.working-label')).toHaveCount(0,{timeout:15000});await expect(page.locator('.assistant-message')).toHaveCount(1)}
async function highlighted(locator){
 await expect(page.locator('.evidence-highlight')).toHaveCount(1);
 await expect(page.locator('.evidence-highlight')).toHaveAttribute('data-locator',locator);
 await expect.poll(async()=>page.locator('.evidence-highlight').evaluate(el=>{
  const b=el.getBoundingClientRect(),v=el.closest('.document-scroll').getBoundingClientRect();
  return b.top<v.bottom&&b.bottom>v.top&&b.left<v.right&&b.right>v.left;
 })).toBe(true);
}
try{
 await page.goto(base);await expect(page.locator('.client-button')).toContainText('Northwind');
 await expect(page.getByRole('status',{name:'Current model',exact:true})).toContainText('Claude Sonnet 4.6');
 assert.equal(await page.evaluate(()=>localStorage.getItem('folio-provider')),null);
 assert.equal(await page.getByRole('button',{name:'Choose model',exact:true}).count(),0);
 await page.getByRole('button',{name:'Open workspace settings',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('Claude Sonnet 4.6');
 assert.equal(await page.locator('.api-key-guide').count(),0);assert.equal(await page.getByText('Claude Sonnet 5.5',{exact:true}).count(),0);
 await expect(page.getByRole('group',{name:'Appearance',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Close dialog',exact:true}).click();
 checks.push('AWS Claude is the only assistant; old Anthropic preference is cleared; model picker/key guide removed while appearance settings remain');
 await start('Word fixture');await complete();await highlighted('p2');
 await expect(page.locator('.document-top')).toContainText('Company details.docx');
 await expect(page.locator('.inline-citation')).toHaveCount(1);
 assert.equal(await page.locator('.markdown a,.markdown img').count(),0);
 await page.getByRole('button',{name:'Close document',exact:true}).click();await expect(page.locator('.document-panel')).toHaveCount(0);
 await page.getByRole('button',{name:'Open source 1: Company details.docx',exact:true}).click();await highlighted('p2');
 checks.push('Submitted question automatically opens the cited Word paragraph; inline citation reopens it; external Markdown links/images stay disabled');
 await page.locator('.chat-options').first().click();await page.getByRole('menuitem',{name:'Delete chat',exact:true}).waitFor();await page.keyboard.press('Escape');
 checks.push('Per-chat options still open and dismiss after removing the provider menu');
 await start('Sheet fixture');await complete();await highlighted('Income statement!B8');
 await expect(page.locator('.formula-bar>span')).toHaveText('B8');
 await page.getByRole('button',{name:'Open source 2: Management accounts Aug 2026.xlsx',exact:true}).click();await highlighted('Debtors!C10');
 await expect(page.locator('.formula-bar>span')).toHaveText('C10');
 await page.screenshot({path:'test-results/evidence-sheet.png'});
 checks.push('Spreadsheet source auto-opens with correct sheet, selected cell and formula bar; second inline citation switches sheets and highlights');
 const [pdf]=await json(await api.post(base+'/api/clients/northwind/upload',{multipart:{folder:'Inbox',files:{name:'Evidence multipage.pdf',mimeType:'application/pdf',buffer:await readFile('test-results/evidence-multipage.pdf')}}}));uploads.push(pdf);
 let releaseImage;const imageGate=new Promise(resolve=>{releaseImage=resolve});
 await page.route('**/page/2?*',async route=>{await imageGate;await route.continue()});
 await start('PDF fixture');await complete();await highlighted('page2:b0');
 const image=page.locator('.pdf-page>img').nth(1);assert((await image.boundingBox()).height>100);
 releaseImage();await page.unrouteAll({behavior:'wait'});
 await expect.poll(()=>image.evaluate(img=>img.complete&&img.naturalWidth>0)).toBe(true);
 await page.screenshot({path:'test-results/evidence-pdf.png'});
 checks.push('Multi-page PDF auto-opens and scrolls to the cited rectangle even before page image loading completes');
 await start('Scan fixture');await complete();await expect(page.locator('.evidence-highlight')).toHaveCount(1);
 await expect(page.locator('.evidence-banner')).toContainText('Page or image used in the answer');
 checks.push('Image-only scan highlights the whole source and labels its granularity honestly');
 const png=await readFile('test-results/evidence-pixel.png');
 const [uploaded]=await json(await api.post(base+'/api/clients/northwind/upload',{multipart:{folder:'Inbox',files:{name:'Evidence pixel.png',mimeType:'image/png',buffer:png}}}));uploads.push(uploaded);
 await start('Image fixture');await complete();await highlighted('image');
 await expect(page.locator('.evidence-banner')).toContainText('WHOLE SOURCE');
 checks.push('Uploaded image citation opens the image with a whole-source outline');
 await start('General fixture');await complete();await expect(page.locator('.document-panel')).toHaveCount(0);
 await expect(page.locator('.answer-scope')).toHaveText('General knowledge');
 checks.push('A general-knowledge question clears the old preview and does not open unrelated evidence');
 await start('Slow Word manual hide fixture');await page.getByRole('button',{name:'Show files',exact:true}).click();
 await page.getByRole('button',{name:'Hide panel',exact:true}).click();await complete();
 await expect(page.locator('.document-panel')).toHaveCount(0);
 checks.push('Manually hiding the panel while an answer is pending prevents automatic reopening');
 await start('Slow Word client switch fixture');
 await page.locator('.client-button').click();await page.locator('.client-menu button').filter({hasText:'Ostara Health'}).click();
 await page.waitForTimeout(3500);await expect(page.locator('.document-panel')).toHaveCount(0);await expect(page.locator('.assistant-message')).toHaveCount(0);
 await start('Word other client fixture');await complete();await highlighted('p2');await expect(page.locator('.word-page')).toContainText('Ostara');
 checks.push('Switching clients rejects the old response; the next question opens only the new client’s source');
 await start('Slow Word new chat fixture');await page.getByRole('button',{name:/^New conversation/}).first().click();await page.waitForTimeout(3500);
 await expect(page.locator('.document-panel')).toHaveCount(0);await expect(page.locator('.assistant-message')).toHaveCount(0);
 checks.push('Starting a new conversation while answering prevents the abandoned source from reopening');
 await start('Propose Word fixture');await complete();await page.locator('.proposal-link').click();
 await expect(page.locator('.proposal-review')).toContainText('Fixture review');await expect(page.locator('.document-top')).toContainText('Company details.docx');
 await expect(page.locator('.word-page')).not.toContainText('Fixture-only proposed text');
 checks.push('Reviewing an edit opens its target on desktop without applying the proposal');
 await page.setViewportSize({width:390,height:844});await page.reload();
 await page.getByRole('textbox',{name:'Message Folio',exact:true}).fill('Word mobile fixture');await page.getByRole('button',{name:'Send message',exact:true}).click();
 await expect(page.getByRole('button',{name:'Back to answer',exact:true})).toBeVisible();await highlighted('p2');
 await page.emulateMedia({colorScheme:'dark'});await page.waitForTimeout(250);await page.screenshot({path:'test-results/evidence-mobile-dark.png'});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.getByRole('button',{name:'Back to answer',exact:true}).click();await expect(page.locator('.assistant-message')).toBeVisible();
 await page.getByRole('button',{name:'Open source 1: Company details.docx',exact:true}).click();await highlighted('p2');
 checks.push('Mobile auto-opens evidence; Back to answer and inline reopening work in dark theme without page overflow');
 assert.deepEqual(errors,[]);
 await writeFile('test-results/evidence-browser-checks.json',JSON.stringify({tested_at:new Date().toISOString(),environment:'isolated localhost, fixture model; real document parsers',checks,console_errors:errors},null,2)+'\n');
 console.log(JSON.stringify({passed:checks.length,checks}));
}finally{
 for(const uploaded of uploads)await api.delete(base+'/api/clients/northwind/documents/'+uploaded.id,{data:{revision:uploaded.revision,lifecycle_version:uploaded.lifecycle_version||0}});
 await browser.close();
}
