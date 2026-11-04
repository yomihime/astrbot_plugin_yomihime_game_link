import {sourceBundle} from './source-build.mjs';
// Production ESM runs in a browser-like VM realm with no Node globals.
// This is an offline build contract, not browser/Host acceptance.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog,success} from './fixtures.mjs';
const data=catalog();
const dom=new JSDOM('<div id="app"></div><template id="module-style-assets"></template>',{pretendToBeVisual:true,url:'http://localhost/#/module/ff14%2Fff14/items'});
const {window}=dom, calls=[];
// JSDOM does not fetch CSS here. Model an explicit successful resource event;
// the shell must not equate a cloned link with a loaded stylesheet.
const append=window.document.head.append.bind(window.document.head);
window.document.head.append=(...nodes)=>{append(...nodes);for(const node of nodes)if(node.matches?.('link[data-module-style]'))queueMicrotask(()=>node.dispatchEvent(new window.Event('load')));};
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const realm=vm.createContext({window,document:window.document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,
  setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),
  requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),
  record:(kind,endpoint,body)=>calls.push({kind,endpoint,body})});
for(const key of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent']) realm[key]=window[key];
assert.equal(vm.runInContext('[typeof process, typeof global, typeof require, typeof Buffer].join(",")',realm),'undefined,undefined,undefined,undefined');
vm.runInContext(`window.AstrBotPluginView={ready:()=>Promise.resolve({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link'}),onContext(){return()=>{}},apiGet(endpoint){record('get',endpoint);return Promise.resolve(${JSON.stringify(data)})},apiPost(endpoint,body){record('post',endpoint,body);if(endpoint==='admin/module-page-session')return Promise.resolve({page_session:'synthetic-proof',expires_at:Date.now()/1000+30});if(endpoint==='admin/module-page-session-close')return Promise.resolve({closed:true});return Promise.resolve(${JSON.stringify(success)})}}`,realm);
for(const path of data.modules[0].pages[0].styles){const link=window.document.createElement('link');link.rel='stylesheet';link.dataset.resource=path;link.href='./'+path;window.document.getElementById('module-style-assets').content.append(link);}
const sources=new Map([
  ['/runtime.js',await readFile(root+'pages/00-game-link/runtime.js','utf8')],
  ['/app.js',await readFile(root+'pages/00-game-link/app.js','utf8')],
  ['/module-assets/ff14/ff14/pages/dist/entry.js',await readFile(root+'modules/ff14/pages/dist/entry.js','utf8')],
  ['/module-loader.js',`export const sharedRuntimeUrl='http://localhost/runtime.js';export function loadPage(entry){if(entry==='module-assets/ff14/ff14/pages/dist/entry.js')return import('./module-assets/ff14/ff14/pages/dist/entry.js');throw Error('unknown_page')}`],
]);
const cache=new Map();
function getModule(path){
  if(cache.has(path))return cache.get(path);
  if(!sources.has(path))throw Error('Unexpected production dependency: '+path);
  const module=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path,
    importModuleDynamically:async(specifier,referencing)=>{const target=getModule(new URL(specifier,referencing.identifier).pathname);if(target.status==='unlinked')await target.link(link);if(target.status==='linked')await target.evaluate();return target;}});
  cache.set(path,module);return module;
}
const link=(specifier,referencing)=>getModule(new URL(specifier,referencing.identifier).pathname);
try{
  const app=getModule('/app.js');await app.link(link);await app.evaluate();
  const runtime=getModule('/runtime.js').namespace;
  for(let i=0;i<12;i++){await new Promise(resolve=>setTimeout(resolve,0));await runtime.nextTick();}
  assert.ok(window.document.querySelector('#page-root'),'production shell rendered');
  assert.equal(calls.filter(c=>c.kind==='get').length,1);
  const input=window.document.querySelector('#ff14-items-query');assert.ok(input,'literal loader mounted independent module');
  input.value='44091';input.dispatchEvent(new window.InputEvent('input',{bubbles:true}));await runtime.nextTick();
  window.document.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  for(let i=0;i<12;i++){await Promise.resolve();await runtime.nextTick();}
  const queries=calls.filter(c=>c.kind==='post'&&c.endpoint==='admin/module-invoke');
  assert.equal(queries.length,1);assert.ok(window.document.querySelector('.ff14-result'));
  assert.equal(calls.filter(c=>c.kind==='post'&&['invoke','invoke-images','preview'].includes(c.endpoint)).length,0);
  assert.deepEqual(Object.keys(queries[0].body).sort(),['capability_id','module_epoch','owner','page','page_session','parameters','runtime_id']);
  assert.deepEqual(JSON.parse(JSON.stringify(queries[0].body)),{capability_id:'item.lookup',parameters:{query:'44091'},owner:'ff14/ff14',page:'items',runtime_id:'runtime-1',module_epoch:1,page_session:'synthetic-proof'});
  assert.ok(calls.filter(c=>c.kind==='post').every(c=>['admin/catalog','admin/modules','admin/read','admin/master-key-status','admin/credential-catalog','admin/credential-status','admin/credential-readiness','admin/module-page-session','admin/module-page-session-close','admin/module-invoke'].includes(c.endpoint)));
  window.dispatchEvent(new window.Event('pagehide'));await runtime.nextTick();
  console.log('production shell/runtime/module: cold evaluation, mount, query, disposal without Node globals passed');
}finally{dom.window.close();}
