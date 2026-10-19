// Production shell in an offline VM. No live bridge, backend, or secret capture.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus,masterKeyReady} from '../management/fixtures.mjs';
const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in navigation test')}"]]);
async function fixture(hash='#/settings',denied=''){
 const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:'http://localhost/'+hash}),window=dom.window,calls=[];
 window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 let nativeCalls=0;window.confirm=()=>{nativeCalls++;return false;};
 const realm=vm.createContext({window,document:window.document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),record:endpoint=>calls.push(endpoint)});
 for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
 const responses={'admin/catalog':catalog(),'admin/read':snapshot(12),'admin/master-key-status':masterKeyReady(),'admin/credential-catalog':credentialCatalog(),'admin/credential-status':credentialStatus(),'admin/credential-readiness':{ready:true,state:'ready',reason_code:'ready',diagnostics:{key_state:'valid_format',codec_state:'available'}},'admin/modules':{registry_revision:5,modules:[{module_id:'ff14/ff14',enabled:true,lifecycle:'ready',health:'ready',epoch:2,registry_revision:5,reason_code:null}]}};
 const registry=shellCatalog();registry.modules[0].display={default_name:'最终幻想 XIV'};
 vm.runInContext(`const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'navigation'},responses=${JSON.stringify(responses)};window.AstrBotPluginView={onContext(fn){fn(context);return()=>{}},ready:()=>Promise.resolve(context),apiGet:()=>Promise.resolve(${JSON.stringify(registry)}),apiPost(endpoint){record(endpoint);if(endpoint===${JSON.stringify(denied)})return Promise.reject(Error('admin_authorization_denied'));if(endpoint in responses)return Promise.resolve(responses[endpoint]);return new Promise(()=>{});}};`,realm);
 const cache=new Map();function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path));const value=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,value);return value;}
 const flush=async()=>{for(let i=0;i<15;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
 const app=module('/app.js');await app.link((specifier,referencing)=>module(new URL(specifier,referencing.identifier).pathname));await app.evaluate();await flush();
 const document=window.document,action=name=>document.querySelector(`[data-action="${name}"]`),click=node=>{assert.ok(node);node.focus();node.click();};
 return {window,document,flush,action,click,find:text=>[...document.querySelectorAll('button')].find(node=>node.textContent===text),link:href=>document.querySelector(`a[href="${href}"]`),field:name=>document.querySelector(`[data-field="${name}"]`),credential:name=>document.querySelector(`[data-credential="${name}"]`),edit(node,value,event='change'){if(node.type==='checkbox')node.checked=value;else node.value=value;node.dispatchEvent(new window.Event(event,{bubbles:true}));},async close(){assert.equal(nativeCalls,0);assert.equal(calls.some(endpoint=>['admin/update','admin/credential-update','admin/module-unload','admin/module-enabled','admin/recover','admin/rollback'].includes(endpoint)),false);window.dispatchEvent(new window.Event('pagehide'));await flush();dom.window.close();}};
}
const ffSettings='#/settings/module/ff14%2Fff14',ffHome='#/module/ff14%2Fff14';
async function go(f,href){f.click(f.link(href));await f.flush();}
async function confirm(f){f.click(f.action('confirm-navigation'));await f.flush();}
const prompt=f=>f.document.querySelector('[data-confirmation=navigation]')?.textContent||'';
for(const denied of ['', 'admin/catalog']){
 const f=await fixture('#/settings',denied);try{
  if(denied)assert.match(f.document.querySelector('#status').textContent,/授权不可用/);
  await go(f,'#/');assert.equal(f.action('confirm-navigation'),null);assert.equal(f.window.location.hash,'#/');
 }finally{await f.close();}
}
{
 const f=await fixture();try{
  const core=f.field('default_region').querySelector('[data-role=mode]');f.edit(core,'replace');await go(f,ffHome);
  assert.match(prompt(f),/全局设置的普通设置草稿将保留/);assert.doesNotMatch(prompt(f),/凭据/);
  f.click(f.action('cancel-navigation'));await f.flush();assert.equal(f.window.location.hash,'#/settings');assert.equal(core.value,'replace');
  await go(f,ffHome);await confirm(f);await go(f,ffSettings);assert.equal(f.window.location.hash,ffSettings);
  // Current FF14 is clean, Core history remains dirty. Module operations still
  // guard that global draft even though navigation no longer blocks on it.
  await go(f,ffHome);assert.equal(f.action('confirm-navigation'),null);assert.equal(f.window.location.hash,ffHome);assert.equal(core.value,'replace');
  for(const label of ['禁用','解除挂载（保留数据）']){
   f.click(f.find(label));await f.flush();assert.ok(f.action('confirm-module-draft'));f.click(f.action('cancel-module-draft'));await f.flush();
  }
  f.click(f.find('全局设置'));await f.flush();assert.equal(core.value,'replace');
  // Current clear mode, history hash, cancellation and reentry preserve drafts.
  f.edit(core,'clear');f.window.location.hash=ffSettings;await f.flush();assert.equal(f.window.location.hash,'#/settings');assert.ok(f.action('confirm-navigation'));
  f.click(f.action('cancel-navigation'));await f.flush();assert.equal(core.value,'clear');
  f.window.location.hash=ffSettings;await f.flush();await confirm(f);assert.equal(f.window.location.hash,ffSettings);
  await go(f,ffHome);assert.equal(f.action('confirm-navigation'),null);assert.equal(core.value,'clear');
 }finally{await f.close();}
}
{
 const f=await fixture();try{
  const core=f.field('default_region').querySelector('[data-role=mode]');f.edit(core,'replace');await go(f,ffHome);await confirm(f);
  f.click(f.find('配置维护'));await f.flush();const panel=f.document.querySelector('#maintenance-container');assert.equal(panel.querySelector('#fields,#credentials'),null);assert.ok(panel.querySelector('#recover'));assert.equal(f.field('default_region').querySelector('[data-role=mode]'),core);
  f.click(panel.querySelector('#recover'));await f.flush();assert.match(f.document.querySelector('[data-confirmation=maintenance]').textContent,/存在未保存草稿/);f.click(f.action('cancel-maintenance'));await f.flush();assert.equal(core.value,'replace');
  f.click(panel.querySelector('#recover'));await f.flush();const old=f.action('confirm-maintenance');f.edit(core,'clear');await f.flush();assert.equal(f.action('confirm-maintenance'),null);old.click();await f.flush();assert.equal(f.window.location.hash,ffHome);
  f.click(panel.querySelector('#recover'));await f.flush();const closeConsent=f.action('confirm-maintenance'),oldRecover=panel.querySelector('#recover');f.click(f.find('关闭配置维护'));await f.flush();assert.equal(panel.children.length,0);assert.equal(f.action('confirm-maintenance'),null);closeConsent.click();oldRecover.dispatchEvent(new f.window.Event('click'));await f.flush();
  await go(f,ffSettings);assert.equal(f.document.querySelector('#module-management-container .module-management'),null);assert.equal(f.document.querySelector('#settings-container #recover,#settings-container .technical-details'),null);
 }finally{await f.close();}
}
for(const start of ['#/settings',ffSettings])for(const region of ['cn','global'])for(const kind of ['client_secret','replace','clear','confirm']){
 const f=await fixture(start);try{
  const card=f.credential('credential_fflogs_'+region),mode=card.querySelector('select'),secret=card.querySelector('[data-role=client_secret]'),check=card.querySelector('input[type=checkbox]');
  // Cross-owner and hidden-region fixtures intentionally populate retained
  // DOM state directly; no ordinary route claims secrets survive suspension.
  if(kind==='client_secret')f.edit(secret,'synthetic-navigation-input','input');else if(kind==='confirm')f.edit(check,true);else f.edit(mode,kind);
  await go(f,ffHome);assert.match(prompt(f),/最终幻想 XIV的凭据输入及操作草稿将清空/);assert.doesNotMatch(prompt(f),/普通设置草稿/);
  f.click(f.action('cancel-navigation'));await f.flush();assert.equal(f.window.location.hash,start);
  if(kind==='client_secret')assert.ok(Boolean(secret.value));else if(kind==='confirm')assert.equal(check.checked,true);else assert.equal(mode.value,kind);
  await go(f,ffHome);const old=f.action('confirm-navigation');assert.ok(old);
  // Every affected draft edit invalidates old consent, even a hidden region.
  f.edit(secret,'synthetic-navigation-changed','input');await f.flush();assert.equal(f.action('confirm-navigation'),null);old.dispatchEvent(new f.window.Event('click'));await f.flush();assert.equal(f.window.location.hash,start);
  await go(f,ffHome);await confirm(f);assert.equal(f.window.location.hash,ffHome);
  assert.ok([...f.document.querySelectorAll('[data-role=client_id],[data-role=client_secret]')].every(node=>node.value===''));
  assert.ok([...f.document.querySelectorAll('[data-credential] select')].every(node=>node.value==='keep'));assert.equal(check.checked,false);
 }finally{await f.close();}
}
{
 const f=await fixture(ffSettings);try{
  const mode=f.field('ff14_calendar_default_days').querySelector('[data-role=mode]');f.edit(mode,'replace');
  await go(f,ffHome);await confirm(f);f.window.history.back();await f.flush();assert.equal(f.window.location.hash,ffSettings);assert.equal(mode.value,'replace');
  f.window.history.forward();await f.flush();assert.equal(f.window.location.hash,ffSettings);assert.ok(f.action('confirm-navigation'));
  f.click(f.action('cancel-navigation'));await f.flush();assert.equal(f.window.location.hash,ffSettings);
  // New module-tab navigation uses the same predicate after history cancel.
  await go(f,ffHome);await confirm(f);f.window.history.back();await f.flush();assert.equal(f.window.location.hash,ffSettings);
  f.window.history.forward();await f.flush();await confirm(f);assert.equal(f.window.location.hash,ffHome);
 }finally{await f.close();}
}
console.log('navigation impact production shell: fresh/auth, owner scope, credential modes/regions, cancel/clear, draft invalidation, module guard and hash/history passed');
