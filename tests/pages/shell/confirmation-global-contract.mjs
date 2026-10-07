// Production shell/confirmation in an offline browser-like VM; not live Host acceptance.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';
const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:'http://localhost/#/settings'}),window=dom.window,calls=[];
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
let nativeCalls=0;window.confirm=()=>{nativeCalls++;return false;};
const realm=vm.createContext({window,document:window.document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),record:(endpoint,body)=>calls.push({endpoint,body})});
for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
const responses={'admin/catalog':catalog(),'admin/read':snapshot(12),'admin/credential-catalog':credentialCatalog(),'admin/credential-status':credentialStatus(),'admin/credential-readiness':{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'},'admin/modules':{registry_revision:5,modules:[{module_id:'ff14/ff14',enabled:false,lifecycle:'stopped',health:'ready',epoch:2,registry_revision:5,reason_code:null}]}};
vm.runInContext(`const handlers=new Set(),context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'one'},responses=${JSON.stringify(responses)};window.AstrBotPluginPage={onContext(fn){handlers.add(fn);fn(context);return()=>handlers.delete(fn)},ready:()=>Promise.resolve(context),apiGet(){return Promise.resolve(${JSON.stringify(shellCatalog())})},apiPost(endpoint,body){record(endpoint,body);if(!(endpoint in responses))return new Promise(()=>{});return Promise.resolve(responses[endpoint]);}};globalThis.emitContext=value=>handlers.forEach(fn=>fn(value));`,realm);
const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in this confirmation test')}"]]),cache=new Map();
function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path));const value=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,value);return value;}
const link=(specifier,referencing)=>module(new URL(specifier,referencing.identifier).pathname);
const flush=async()=>{for(let i=0;i<20;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
const click=node=>{assert.ok(node);node.focus();node.click();};
const action=name=>window.document.querySelector('[data-action="'+name+'"]');
try{
 const app=module('/app.js');await app.link(link);await app.evaluate();await flush();
 const field=window.document.querySelector('[data-field="default_region"]'),mode=field.querySelector('[data-role="mode"]'),input=field.querySelector('[data-role="value"]');mode.value='replace';mode.dispatchEvent(new window.Event('change',{bubbles:true}));input.value='0';input.dispatchEvent(new window.Event('input',{bubbles:true}));
 const navigate=()=>click([...window.document.querySelectorAll('nav[aria-label="模块页面"] a')][0]);navigate();await flush();assert.ok(action('confirm-navigation'));click(action('cancel-navigation'));await flush();assert.equal(window.location.hash,'#/settings');assert.equal(mode.value,'replace');assert.equal(input.value,'0');
 navigate();await flush();const old=action('confirm-navigation');input.value='1';input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush();assert.equal(action('confirm-navigation'),null);old.dispatchEvent(new window.Event('click'));await flush();assert.equal(window.location.hash,'#/settings');assert.equal(input.value,'1');
 const find=text=>[...window.document.querySelectorAll('button')].find(node=>node.textContent===text);
 click(find('解除挂载（保留数据）'));await flush();assert.ok(action('confirm-module-draft'));click(action('cancel-module-draft'));await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/module-unload').length,0);assert.equal(window.document.activeElement,find('解除挂载（保留数据）'));
 mode.value='keep';mode.dispatchEvent(new window.Event('change',{bubbles:true}));click(find('解除挂载（保留数据）'));await flush();assert.ok(action('confirm-unload'));action('cancel-unload').dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await flush();assert.equal(window.document.activeElement,find('解除挂载（保留数据）'));assert.equal(find('解除挂载（保留数据）').disabled,false);assert.equal(calls.filter(c=>c.endpoint==='admin/module-unload').length,0);mode.value='replace';mode.dispatchEvent(new window.Event('change',{bubbles:true}));

 click(find('解除挂载（保留数据）'));await flush();click(action('confirm-module-draft'));await flush();assert.ok(action('confirm-unload'));input.value='0';input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush();assert.equal(action('confirm-unload'),null);assert.equal(calls.filter(c=>c.endpoint==='admin/module-unload').length,0);
 window.location.hash='#/outside';await flush();assert.equal(window.location.hash,'#/settings');assert.ok(action('confirm-navigation'));click(action('cancel-navigation'));await flush();assert.equal(window.location.hash,'#/settings');assert.equal(mode.value,'replace');
 window.location.hash='#/outside';await flush();assert.ok(action('confirm-navigation'));vm.runInContext("emitContext({...context,isDark:true,locale:'en-US'})",realm);await flush();assert.ok(action('confirm-navigation'));click(action('confirm-navigation'));await flush();assert.equal(window.location.hash,'#/outside');assert.equal(nativeCalls,0);assert.equal(calls.filter(c=>['admin/update','admin/credential-update','admin/module-unload','admin/module-enabled'].includes(c.endpoint)).length,0);
 window.dispatchEvent(new window.Event('pagehide'));await flush();console.log('production shell dirty navigation/hash/module confirmation: cancellation, draft edit, theme, exact guards and zero native modals passed');
}finally{dom.window.close();}
