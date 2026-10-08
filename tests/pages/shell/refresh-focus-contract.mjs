// Production app in an offline JSDOM VM. Native keyboard activation and browser
// blur behavior still require real-browser acceptance; click is its activation.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';

async function scenario(kind){
 const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:'http://localhost/#/settings/module/ff14%2Fff14'}),window=dom.window;
 window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 let pending=null,gets=0,binding;const posts=[],handlers=new Set();
 const responses={'admin/catalog':catalog(),'admin/read':snapshot(12),'admin/credential-catalog':credentialCatalog(),'admin/credential-status':credentialStatus(),'admin/credential-readiness':{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'},'admin/modules':{registry_revision:5,modules:[]}};
 window.AstrBotPluginPage={onContext(fn){handlers.add(fn);fn(binding);return()=>handlers.delete(fn)},ready:()=>Promise.resolve(binding),apiGet(endpoint){assert.equal(endpoint,'catalog');gets++;return pending?.promise||Promise.resolve(shellCatalog());},apiPost(endpoint,body){posts.push({endpoint,body});assert.ok(endpoint in responses,endpoint);return Promise.resolve(vm.runInContext('('+JSON.stringify(responses[endpoint])+')',realm));}};
 const realm=vm.createContext({window,document:window.document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window)});
 binding=vm.runInContext('({pluginName:"astrbot_plugin_yomihime_game_link",pageName:"shell",session:"one"})',realm);
 for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
 const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in refresh test')}"]]),cache=new Map();
 const module=p=>{if(cache.has(p))return cache.get(p);assert.ok(sources.has(p),p);const value=new vm.SourceTextModule(sources.get(p),{context:realm,identifier:'http://localhost'+p});cache.set(p,value);return value;};
 const flush=async()=>{for(let i=0;i<12;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
 const frames=async()=>{for(let i=0;i<2;i++)await new Promise(resolve=>window.requestAnimationFrame(resolve));};
 try{
  const app=module('/app.js');await app.link((s,m)=>module(new URL(s,m.identifier).pathname));await app.evaluate();await flush();
  const field=window.document.querySelector('[data-field="ff14_calendar_default_timezone"]');assert.ok(field);const input=field.querySelector('[data-role="value"]');assert.ok(input);if(kind!=='page'){const mode=field.querySelector('[data-role="mode"]');mode.value='replace';mode.dispatchEvent(new window.Event('change',{bubbles:true}));}input.value='draft';input.setSelectionRange(1,3);
  const scrollRoot=window.document.documentElement;scrollRoot.scrollTop=190.5;
  const button=window.document.querySelector('button[aria-label="刷新模块状态"]');assert.ok(button);button.focus();
  let resolve,reject;pending={promise:new Promise((a,b)=>{resolve=a;reject=b;})};const initialGets=gets;
  // Enter/Space activation in the native button dispatches click in a browser.
  button.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));button.click();await flush();
  assert.equal(gets,initialGets+1);assert.equal(button.disabled,false,'busy must not natively disable and blur the trigger');assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.getAttribute('aria-busy'),'true');assert.equal(window.document.activeElement,button);assert.equal(window.document.querySelector('button[aria-label="刷新模块状态"]'),button);
  button.click();button.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));button.dispatchEvent(new window.KeyboardEvent('keydown',{key:' ',bubbles:true}));button.click();await flush();assert.equal(gets,initialGets+1,'busy keyboard/pointer activations must not repeat catalog requests');
  let focusCalls=0;button.focus=()=>{focusCalls++;};let expectedFocus=button;
  if(kind==='move'||kind==='dispose'){input.focus();expectedFocus=input;}
  if(kind==='context'||kind==='invalid'){const skip=window.document.querySelector('.skip-button');skip.focus();expectedFocus=skip;}
  if(kind==='page'){const select=window.document.querySelector('#module-select-mobile');select.focus();expectedFocus=select;window.location.hash='#/settings';await flush();expectedFocus=window.document.activeElement;assert.notEqual(expectedFocus,button);}
  if(kind==='context'){handlers.forEach(fn=>fn(vm.runInContext('({pluginName:"astrbot_plugin_yomihime_game_link",pageName:"shell",session:"two"})',realm)));expectedFocus=window.document.activeElement;}
  if(kind==='invalid'){handlers.forEach(fn=>fn(vm.runInContext('({pluginName:"astrbot_plugin_yomihime_game_link",pageName:"wrong-page",session:"one"})',realm)));expectedFocus=window.document.activeElement;}
  if(kind==='dispose'){window.document.getElementById('app').__vue_app__.unmount();expectedFocus=window.document.body;}
  if(kind==='failure')reject(Error('fixture_catalog_failure'));else resolve(shellCatalog());pending=null;await flush();await frames();
  assert.equal(window.document.activeElement,expectedFocus,kind+' must retain the current user focus');assert.equal(focusCalls,0,kind+' must not restore focus asynchronously');
  if(['success','move','failure'].includes(kind)){
   assert.equal(window.document.querySelector('[data-field="ff14_calendar_default_timezone"] [data-role="value"]'),input);assert.deepEqual([input.selectionStart,input.selectionEnd],[1,3]);assert.equal(scrollRoot.scrollTop,190.5);
  }
  if(kind!=='dispose'){
   assert.equal(window.document.querySelector('button[aria-label="刷新模块状态"]'),button);assert.equal(button.getAttribute('aria-disabled'),'false');assert.equal(button.getAttribute('aria-busy'),'false');
  }
  if(kind==='failure'){button.click();await flush();assert.equal(gets,initialGets+2,'failed refresh must remain retryable');}
  if(kind==='context'||kind==='invalid'||kind==='dispose'){button.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));await flush();assert.equal(gets,initialGets+1,'revoked/disposed lifecycle must reject further refresh');}
  assert.equal(posts.filter(p=>['invoke','admin/update','admin/credential-update','admin/module-unload','admin/module-enabled'].includes(p.endpoint)).length,0);
 }finally{window.document.getElementById('app')?.__vue_app__?.unmount();dom.window.close();}
}
for(const kind of ['success','move','page','failure','context','invalid','dispose'])await scenario(kind);
console.log('refresh focus contract passed: same trigger, busy gate, drafts, selection, scroll, failure retry, user focus, navigation, context and dispose; zero business writes');
