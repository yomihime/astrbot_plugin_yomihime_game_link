import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';
const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:'http://localhost/#/'}),window=dom.window,calls=[],pending=[];
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
let nativeCalls=0;window.confirm=()=>{nativeCalls++;return false;};
let values=snapshot(1),holdRead=false,holdUpdate=false,rejectUpdate=false,rejectRead=false;
const status={registry_revision:5,modules:[{module_id:'ff14/ff14',enabled:false,lifecycle:'stopped',health:'ready',epoch:2,registry_revision:5,reason_code:null}]};
function post(endpoint,body){
  calls.push({endpoint,body:JSON.parse(JSON.stringify(body))});
  if(endpoint==='admin/update'&&rejectUpdate)return Promise.reject(Error('revision_conflict'));
  if(endpoint==='admin/read'&&rejectRead)return Promise.reject(Error('controlled_read_failure'));
  if(endpoint==='admin/read'&&holdRead||endpoint==='admin/update'&&holdUpdate)return new Promise((resolve,reject)=>pending.push({endpoint,resolve,reject}));
  const responses={'admin/catalog':catalog(),'admin/read':values,'admin/credential-catalog':credentialCatalog(),'admin/credential-status':credentialStatus(),'admin/credential-readiness':{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable'},'admin/modules':status,'admin/module-unload':{module_id:'ff14/ff14',registry_revision:6,state:'unloaded',data_retained:true,reopen_required:true}};
  assert.ok(endpoint in responses,endpoint);return Promise.resolve(responses[endpoint]);
}
const realm=vm.createContext({window,document:window.document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),post,shellData:shellCatalog()});
for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
vm.runInContext(`const handlers=new Set(),context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'one'};window.AstrBotPluginPage={onContext(fn){handlers.add(fn);fn(context);return()=>handlers.delete(fn)},ready:()=>Promise.resolve(context),apiGet:()=>Promise.resolve(JSON.parse(JSON.stringify(shellData))),apiPost:(endpoint,body)=>post(endpoint,body).then(value=>JSON.parse(JSON.stringify(value)),error=>{throw Error(error.message)})};globalThis.emitContext=value=>handlers.forEach(fn=>fn(value));`,realm);
const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in management revisions')} "]]),cache=new Map();
function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path));const value=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,value);return value;}
const link=(specifier,referencing)=>module(new URL(specifier,referencing.identifier).pathname);
const flush=async()=>{for(let i=0;i<12;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
const click=node=>{assert.ok(node);node.focus();node.click();};
const find=text=>[...window.document.querySelectorAll('button')].find(node=>node.textContent===text);
const action=name=>window.document.querySelector('[data-action="'+name+'"]');
const section=()=>window.document.querySelector('section[data-owner="ff14/ff14"]');
const field=()=>window.document.querySelector('[data-field="ff14_calendar_default_timezone"]');
const goHome=()=>click(window.document.querySelector('nav[aria-label="主要导航"] a'));
const goSettings=()=>click(window.document.querySelector('nav[aria-label="模块设置"] a'));
const count=endpoint=>calls.filter(call=>call.endpoint===endpoint).length;
const invalidSnapshot=revision=>{const value=snapshot(revision);Object.assign(value['ff14/ff14'].fields.ff14_calendar_default_timezone,{state:'invalid',value:null});return value;};
const settle=(endpoint,value,position=0)=>{const call=pending.filter(call=>call.endpoint===endpoint)[position];assert.ok(call,'pending '+endpoint);pending.splice(pending.indexOf(call),1);call.resolve(value);};
try{
  const app=module('/app.js');await app.link(link);await app.evaluate();await flush();
  assert.match(window.document.querySelector('.config-readiness').textContent,/已校验/);
  goSettings();await flush();
  if(process.argv[2]==='draft'){
    click(find('解除挂载（保留数据）'));await flush();const old=action('confirm-unload');assert.ok(old);
    click(field().querySelector('[data-action="default"]'));await flush();
    assert.equal(action('confirm-unload'),null,'restore default must revoke pristine unload consent');
    old.click();await flush();assert.equal(count('admin/module-unload'),0);assert.equal(field().querySelector('[data-role="mode"]').value,'clear');
    click(find('解除挂载（保留数据）'));await flush();assert.ok(action('confirm-module-draft'));
    click(action('confirm-module-draft'));await flush();const afterDraft=action('confirm-unload');assert.ok(afterDraft);
    click(section().querySelector('[data-action="discard"]'));await flush();assert.equal(action('confirm-unload'),null);afterDraft.click();await flush();assert.equal(count('admin/module-unload'),0);
    const input=field().querySelector('[data-role="value"]');input.value='Europe/London';input.dispatchEvent(new window.Event('input',{bubbles:true}));goHome();await flush();const oldNavigation=action('confirm-navigation');assert.ok(oldNavigation);
    click(section().querySelector('[data-action="discard"]'));await flush();assert.equal(action('confirm-navigation'),null);oldNavigation.click();await flush();assert.match(window.location.hash,/settings/);
    click(find('解除挂载（保留数据）'));await flush();vm.runInContext("emitContext({...context,isDark:true,locale:'en-US'})",realm);await flush();assert.ok(action('confirm-unload'));click(action('confirm-unload'));await flush();assert.equal(count('admin/module-unload'),1);
  }else if(process.argv[2]==='configuration-success'){
    click(field().querySelector('[data-action="default"]'));holdUpdate=true;click(section().querySelector('[data-action="save"]'));await flush();
    const life=count('admin/modules');settle('admin/update',{module_id:'ff14/ff14',revision:2});await flush();assert.equal(action('confirm-navigation'),null);
    holdRead=true;const reads=count('admin/read');goHome();await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/未核对/);assert.equal(count('admin/read'),reads+1);assert.equal(count('admin/modules'),life);
    goHome();await flush();assert.equal(count('admin/read'),reads+1);settle('admin/read',invalidSnapshot(2));await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/待处理/);
    holdRead=false;goSettings();await flush();goHome();await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/待处理/);assert.equal(count('admin/read'),reads+2);assert.equal(count('admin/modules'),life);
  }else{
    // A validated ACK dirties home immediately, even if settings readback fails.
    click(field().querySelector('[data-action="default"]'));holdUpdate=true;click(section().querySelector('[data-action="save"]'));await flush();
    const modulesBefore=count('admin/modules');rejectRead=true;settle('admin/update',{module_id:'ff14/ff14',revision:2});await flush();
    goHome();await flush();if(action('confirm-navigation')){click(action('confirm-navigation'));await flush();}
    assert.match(window.document.querySelector('.config-readiness').textContent,/未核对/,'ACK with failed readback must invalidate home');
    assert.equal(count('admin/modules'),modulesBefore,'save must not refresh module lifecycle');
    const readsAfterFailure=count('admin/read');goSettings();await flush();goHome();await flush();if(action('confirm-navigation')){click(action('confirm-navigation'));await flush();}
    assert.equal(count('admin/read'),readsAfterFailure+1,'failed home refresh must not loop on every navigation');
    // Explicit refresh recovers; a conflicting write keeps the valid home snapshot.
    rejectRead=false;click(find('刷新状态'));await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/已校验/);
    goSettings();await flush();rejectUpdate=true;click(field().querySelector('[data-action="default"]'));click(section().querySelector('[data-action="save"]'));await flush();click(section().querySelector('[data-action="discard"]'));goHome();await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/已校验/);
    // One home read stays in flight across rapid switches and ignores an old snapshot.
    rejectUpdate=false;holdRead=false;goSettings();await flush();click(field().querySelector('[data-action="default"]'));click(section().querySelector('[data-action="save"]'));settle('admin/update',{module_id:'ff14/ff14',revision:3});await flush();
    holdRead=true;const before=count('admin/read'),life=count('admin/modules');goHome();await flush();assert.equal(count('admin/read'),before+1);
    goSettings();await flush();goHome();await flush();assert.equal(count('admin/modules'),life);assert.equal(count('admin/read'),before+2); // settings' own one read plus coalesced home read
    goSettings();await flush();settle('admin/read',snapshot(3),1);await flush(); // older settings read was fenced by suspend
    settle('admin/read',snapshot(3),1);await flush(); // current settings read, home read is still oldest
    click(field().querySelector('[data-action="default"]'));click(section().querySelector('[data-action="save"]'));settle('admin/update',{module_id:'ff14/ff14',revision:4});await flush();settle('admin/read',snapshot(4),1);await flush();
    goHome();await flush();const oldValues=invalidSnapshot(2);settle('admin/read',oldValues);await flush();assert.doesNotMatch(window.document.querySelector('.config-readiness').textContent,/待处理/);assert.equal(count('admin/modules'),life);
    settle('admin/read',snapshot(4));await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/已校验/);
    // A context boundary fences another late snapshot independently of the ACK ticket.
    click(find('刷新状态'));await flush();vm.runInContext("emitContext({...context,session:'two'})",realm);await flush();settle('admin/read',oldValues);await flush();assert.doesNotMatch(window.document.querySelector('.config-readiness').textContent,/待处理/);holdRead=false;while(pending.some(call=>call.endpoint==='admin/read'))settle('admin/read',snapshot(5));await flush();assert.match(window.document.querySelector('.config-readiness').textContent,/已校验/);
  }
  assert.equal(nativeCalls,0);assert.equal(count('invoke'),0);window.dispatchEvent(new window.Event('pagehide'));await flush();console.log(process.argv[2]+' built management revisions passed');
}finally{dom.window.close();}
