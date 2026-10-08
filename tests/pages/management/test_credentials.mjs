// Synthetic credential inputs only; these tests do not prove Host authorization.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const host=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=host.window[name];
const {createManagementPage,validateCredentialCatalog,validateCredentialStatus,credentialUpdateBody}=await import('../../../pages/management/app.js');
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from './fixtures.mjs';
const flush=async()=>{for(let i=0;i<60;i++)await Promise.resolve();};
test('closed metadata contracts and pair inputs never allow ordinary fields or readback',()=>{
  const c=validateCredentialCatalog(credentialCatalog());
  assert.ok(validateCredentialStatus(credentialStatus(),c));
  assert.throws(()=>validateCredentialStatus({'ff14/ff14':{revision:1,fields:{credential_fflogs_cn:'private'}}},c));
  const pair={client_id:'synthetic-id',client_secret:'synthetic-secret'};
  assert.deepEqual(credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','replace',pair,c).updates,[{field:'credential_fflogs_cn',mode:'replace',value:pair}]);
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','replace',{...pair,token:'x'},c));
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'default_region','clear',null,c));
  assert.throws(()=>credentialUpdateBody('ff14/ff14',1,'credential_fflogs_cn','keep',null,c));
  const extra=credentialCatalog();extra.fields[0].value='private';assert.throws(()=>validateCredentialCatalog(extra));
});
test('masked empty inputs, keep zero write, theme draft, lifecycle clear and late rejection',async()=>{
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];let accept,release;
  const bridge={ready:()=>Promise.resolve(context),onContext(fn){accept=fn;return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());
    if(endpoint==='admin/credential-readiness')return Promise.resolve({ready:true,state:'ready',reason_code:'ready'});if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());
    return new Promise(resolve=>{release=resolve;});}};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
  const card=dom.window.document.querySelector('[data-credential="credential_fflogs_cn"]');assert.ok(card);
  const inputs=[...card.querySelectorAll('input[type=password]')],mode=card.querySelector('select'),save=card.querySelector('button');
  assert.equal(inputs.length,2);assert.ok(inputs.every(node=>node.value===''));save.click();await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,0);
  mode.value='replace';mode.dispatchEvent(new dom.window.Event('change'));inputs[0].value='synthetic-id';inputs[1].value='synthetic-secret';inputs[1].dispatchEvent(new dom.window.Event('input'));inputs[1].focus();inputs[1].setSelectionRange(1,4);
  accept({...context,isDark:true,locale:'en-US'});await flush();assert.equal(card.querySelectorAll('input[type=password]')[1],inputs[1]);assert.equal(inputs[1].value,'synthetic-secret');assert.equal(dom.window.document.activeElement,inputs[1]);assert.equal(inputs[1].selectionEnd,4);
  save.click();await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);
  accept(null);await flush();assert.ok(inputs.every(node=>node.value===''));release({module_id:'ff14/ff14',revision:2});await flush();assert.doesNotMatch(dom.window.document.getElementById('status').textContent,/已加密保存/);
  page.close();dom.window.close();
});
test('clear requires confirmation, CAS refuses blind retry and save-time edits remain drafts',async()=>{
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];let revision=1,release,reject;
  const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot(revision));
    if(endpoint==='admin/credential-readiness')return Promise.resolve({ready:true,state:'ready',reason_code:'ready'});if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus(revision));
    return new Promise((resolve,fail)=>{release=value=>{revision=value.revision;resolve(value);};reject=fail;});}};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
  const card=dom.window.document.querySelector('[data-credential="credential_fflogs_global"]'),mode=card.querySelector('select'),inputs=[...card.querySelectorAll('input[type=password]')],confirm=card.querySelector('input[type=checkbox]'),save=card.querySelector('button');
  const setMode=value=>{mode.value=value;mode.dispatchEvent(new dom.window.Event('change'));};
  try{setMode('clear');save.click();await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,0);
    confirm.checked=true;confirm.dispatchEvent(new dom.window.Event('change'));save.click();await flush();const sent=calls.find(c=>c.endpoint==='admin/credential-update').body;assert.deepEqual(sent.updates,[{field:'credential_fflogs_global',mode:'clear'}]);reject(Error('revision_conflict'));await flush();assert.equal(save.disabled,true);save.click();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);
    dom.window.document.getElementById('refresh').click();await flush();setMode('replace');inputs[0].value='synthetic-id';inputs[1].value='synthetic-old';inputs[1].dispatchEvent(new dom.window.Event('input'));
    // Ordinary dirty drafts coexist with a credential revision refresh.
    const ordinary=dom.window.document.querySelector('[data-field="ff14_calendar_default_timezone"]'),normalMode=ordinary.querySelector('select'),normalInput=ordinary.querySelector('input');normalMode.value='replace';normalMode.dispatchEvent(new dom.window.Event('change'));normalInput.value='Europe/London';normalInput.dispatchEvent(new dom.window.Event('input'));
    save.click();await flush();inputs[1].value='synthetic-new';inputs[1].dispatchEvent(new dom.window.Event('input'));release({module_id:'ff14/ff14',revision:2});await flush();assert.equal(inputs[1].value,'synthetic-new');assert.equal(mode.value,'replace');assert.equal(normalInput.value,'Europe/London');assert.equal(normalMode.value,'replace');assert.match(dom.window.document.getElementById('status').textContent,/尚未验证/);
    setMode('clear');assert.ok(inputs.every(input=>input.value===''));confirm.checked=true;confirm.dispatchEvent(new dom.window.Event('change'));save.click();await flush();release({module_id:'ff14/ff14',revision:3});await flush();assert.equal(mode.value,'keep');assert.match(dom.window.document.getElementById('status').textContent,/已清除/);
  }finally{page.close();dom.window.close();}
});
test('credential read failure keeps ordinary four-field management available',async()=>{
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');const dom=new JSDOM(html,{pretendToBeVisual:true});
  const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint){if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());return Promise.reject(Error('admin_authorization_denied'));}};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();try{assert.equal(dom.window.document.querySelector('#fields [data-action=save]').disabled,true);const card=dom.window.document.querySelector('#fields [data-owner="ff14/ff14"] [data-action=save]');assert.equal(card.disabled,false);assert.match(dom.window.document.getElementById('credential-status').textContent,/授权不可用/);}finally{page.close();dom.window.close();}
});
test('pending save new focused draft remains editable through held automatic refresh with writes fenced',async()=>{
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];let revision=1,saveReply,readReply,holdRead=false;
  const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return Promise.resolve(catalog());
    if(endpoint==='admin/read')return holdRead?new Promise(resolve=>{readReply=()=>resolve(snapshot(revision));}):Promise.resolve(snapshot(revision));
    if(endpoint==='admin/credential-readiness')return Promise.resolve({ready:true,state:'ready',reason_code:'ready'});if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus(revision));
    return new Promise(resolve=>{saveReply=()=>{revision=2;resolve({module_id:'ff14/ff14',revision});};});}};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
  const card=dom.window.document.querySelector('[data-credential="credential_fflogs_cn"]'),mode=card.querySelector('select'),secret=card.querySelector('[data-role=client_secret]'),client=card.querySelector('[data-role=client_id]'),save=card.querySelector('button');
  try{mode.value='replace';mode.dispatchEvent(new dom.window.Event('change'));client.value='synthetic-id';secret.value='synthetic-old';secret.dispatchEvent(new dom.window.Event('input'));save.click();await flush();
    secret.value='synthetic-new';secret.dispatchEvent(new dom.window.Event('input'));secret.focus();secret.setSelectionRange(1,4);dom.window.document.documentElement.scrollTop=91;
    holdRead=true;saveReply();await flush();assert.equal(typeof readReply,'function');
    assert.equal(card.querySelector('[data-role=client_secret]'),secret);assert.equal(secret.disabled,false);assert.equal(dom.window.document.activeElement,secret);assert.equal(secret.value,'synthetic-new');assert.equal(secret.selectionEnd,4);assert.equal(dom.window.document.documentElement.scrollTop,91);
    secret.value+='-typed';secret.dispatchEvent(new dom.window.Event('input'));save.click();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);assert.equal(save.getAttribute('aria-disabled'),'true');
    readReply();await flush();assert.equal(dom.window.document.activeElement,secret);assert.equal(secret.value,'synthetic-new-typed');assert.equal(secret.disabled,false);assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);
  }finally{page.close();dom.window.close();}
});
test('credential authorization failure clears secrets and denies forced writes',async()=>{
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];
  const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-readiness')return Promise.resolve({ready:true,state:'ready',reason_code:'ready'});if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());return Promise.reject(Error('admin_authorization_denied'));}};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();const card=dom.window.document.querySelector('[data-credential="credential_fflogs_cn"]'),mode=card.querySelector('select'),inputs=[...card.querySelectorAll('input[type=password]')],save=card.querySelector('button');
  try{mode.value='replace';mode.dispatchEvent(new dom.window.Event('change'));inputs[0].value='synthetic-id';inputs[1].value='synthetic-secret';inputs[1].dispatchEvent(new dom.window.Event('input'));save.click();await flush();assert.ok(inputs.every(input=>input.value===''&&input.disabled));assert.equal(mode.disabled,true);assert.equal(save.getAttribute('aria-disabled'),'true');save.dispatchEvent(new dom.window.Event('click'));assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);}finally{page.close();dom.window.close();}
});

for(const readiness of [{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'},null,{ready:true,state:'ready',reason_code:'unknown'}])test('unready or unknown codec blocks typing and forced credential submit '+JSON.stringify(readiness),async()=>{
 const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8'),dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];
 const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());if(endpoint==='admin/credential-readiness')return Promise.resolve(readiness);throw Error('unexpected_write');}};
 const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();try{const card=dom.window.document.querySelector('[data-credential]'),mode=card.querySelector('select'),inputs=[...card.querySelectorAll('input[type=password]')];assert.ok(inputs.every(n=>n.disabled&&n.value===''));assert.equal(mode.disabled,false);assert.equal(mode.querySelector('option[value=replace]').disabled,true);assert.match(dom.window.document.getElementById('credential-status').textContent,/禁止填写和替换/);mode.value='replace';mode.dispatchEvent(new dom.window.Event('change'));inputs[0].value='synthetic-id';inputs[1].value='synthetic-secret';card.querySelector('button').dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,0);assert.equal(dom.window.document.querySelector('#fields [data-owner="ff14/ff14"] [data-action=save]').disabled,false);}finally{page.close();dom.window.close();}
});

for(const readiness of [{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'},null])test('unready or unknown codec allows only scoped confirmed clear with current CAS '+JSON.stringify(readiness),async()=>{
 const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8'),dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];let revision=1,accept;
 const bridge={ready:()=>Promise.resolve(context),onContext(fn){accept=fn;return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus(revision));if(endpoint==='admin/credential-readiness')return Promise.resolve(readiness);if(endpoint==='admin/credential-update'){assert.equal(body.expected_revision,revision);return Promise.resolve({module_id:body.module_id,revision:++revision});}throw Error('unexpected_write');}};
 const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
 try{const card=dom.window.document.querySelector('[data-credential="credential_fflogs_global"]'),mode=card.querySelector('select'),confirm=card.querySelector('input[type=checkbox]'),save=card.querySelector('button'),writes=()=>calls.filter(c=>c.endpoint==='admin/credential-update');
  save.click();await flush();assert.equal(writes().length,0);mode.value='clear';mode.dispatchEvent(new dom.window.Event('change'));assert.equal(confirm.disabled,false);save.click();await flush();assert.equal(writes().length,0);assert.match(dom.window.document.getElementById('status').textContent,/勾选清除确认/);
  page.selectOwner('game_link/core');confirm.checked=true;save.dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(writes().length,0);assert.equal(mode.disabled,true);
  page.selectOwner('ff14/ff14');confirm.checked=true;confirm.dispatchEvent(new dom.window.Event('change'));save.click();await flush();assert.deepEqual(writes()[0].body,{module_id:'ff14/ff14',expected_revision:1,updates:[{field:'credential_fflogs_global',mode:'clear'}]});assert.equal(mode.value,'keep');assert.equal(writes().length,1);
  mode.value='clear';mode.dispatchEvent(new dom.window.Event('change'));confirm.checked=true;page.suspend();save.dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(writes().length,1);
  page.resume();await flush();mode.value='clear';mode.dispatchEvent(new dom.window.Event('change'));confirm.checked=true;accept({...context,pageName:'wrong'});save.dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(writes().length,1);
 }finally{page.close();dom.window.close();}
});

for(const failure of ['revision_conflict','admin_authorization_denied'])test('unready confirmed clear preserves conflict and authorization fences '+failure,async()=>{
 const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8'),dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];let release;
 const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},apiPost(endpoint,body){calls.push({endpoint,body});if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());if(endpoint==='admin/credential-readiness')return Promise.resolve(null);if(endpoint==='admin/credential-update')return new Promise((_,reject)=>{release=()=>reject(Error(failure));});throw Error('unexpected_write');}};
 const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
 try{const card=dom.window.document.querySelector('[data-credential="credential_fflogs_global"]'),mode=card.querySelector('select'),confirm=card.querySelector('input[type=checkbox]'),save=card.querySelector('button');mode.value='clear';mode.dispatchEvent(new dom.window.Event('change'));confirm.checked=true;save.click();await flush();save.dispatchEvent(new dom.window.Event('click'));assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);release();await flush();save.dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/credential-update').length,1);assert.equal(save.getAttribute('aria-disabled'),'true');if(failure==='admin_authorization_denied'){assert.equal(mode.disabled,true);assert.equal(confirm.checked,false);}else assert.match(dom.window.document.getElementById('status').textContent,/配置已变化/);
 }finally{page.close();dom.window.close();}
});
