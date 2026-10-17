// Actual production bundles in JSDOM; browser verification is a separate layer.
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {installAsyncFixture} from '../management/async-fixture.mjs';
import {root} from './compile.mjs';
const [surface,scenario]=process.argv.slice(2),shell=surface==='shell';
const fontScenario=scenario.startsWith('font-');
const dom=new JSDOM(shell?'<div id="app"></div>':'<div id="management-root"></div>',{pretendToBeVisual:true,url:'http://localhost/'+(shell&&!fontScenario?'#/settings/module/ff14%2Fff14':'#/settings')}),window=dom.window,document=window.document;
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const fixture=installAsyncFixture(window,surface),realm=vm.createContext({window,document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window)});
for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
// Cross-realm DTOs must have the same plain-object prototype as bridge JSON.
realm.hostBridge=window.AstrBotPluginPage;
vm.runInContext(`const raw=hostBridge;window.AstrBotPluginPage={ready:()=>raw.ready().then(v=>JSON.parse(JSON.stringify(v))),onContext:fn=>raw.onContext(v=>fn(JSON.parse(JSON.stringify(v)))),apiGet:(...args)=>raw.apiGet(...args).then(v=>JSON.parse(JSON.stringify(v))),apiPost:(...args)=>raw.apiPost(...args).then(v=>JSON.parse(JSON.stringify(v)),e=>{throw Error(e.message)})};`,realm);
const sources=new Map([['/runtime.js',await readFile(root+`pages/${surface}/runtime.js`,'utf8')],['/app.js',await readFile(root+`pages/${surface}/app.js`,'utf8')],['/module-loader.js',"export function loadPage(){throw Error('Business loading forbidden in fixture')} "]]),cache=new Map();
function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path),path);const mod=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,mod);return mod;}
const flush=async()=>{for(let i=0;i<10;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
const field=(name='reply_theme',owner='ff14/ff14')=>document.querySelector(`[data-field="${name}"][data-owner="${owner}"]`),input=()=>field().querySelector('[data-role=value]'),mode=()=>field().querySelector('[data-role=mode]').value;
const save=()=>document.querySelector(`section[data-owner="${fontScenario?'game_link/core':'ff14/ff14'}"] [data-action=save]`),refresh=()=>document.querySelector((shell?'#settings-container ':'')+'#refresh'),action=name=>document.querySelector(`[data-action="${name}"]`);
const edit=(node,value,event='input')=>{assert.ok(node);node.value=value;node.dispatchEvent(new window.Event(event,{bubbles:true}));},click=node=>{assert.ok(node);node.click();};
const writes=()=>fixture.calls.filter(c=>c.endpoint==='admin/update');
function secrets(){return [...document.querySelectorAll('[data-role=client_id],[data-role=client_secret]')];}
async function draftSecrets(){for(const card of document.querySelectorAll('[data-credential]')){edit(card.querySelector('select'),'replace','change');for(const node of card.querySelectorAll('[data-role=client_id],[data-role=client_secret]'))edit(node,'synthetic-test-pair');for(const node of card.querySelectorAll('[data-action=reveal]'))click(node);}edit(field('default_region','game_link/core').querySelector('[data-role=value]'),'1');await flush();assert.ok(secrets().every(n=>n.value&&n.type==='text'));}
try{
 const app=module('/app.js');await app.link((specifier,ref)=>module(new URL(specifier,ref.identifier).pathname));await app.evaluate();await flush();assert.ok(input());
 if(['inherit','default','unchanged','conflict','read-failure'].includes(scenario)){
  edit(input(),'2','change');if(scenario==='conflict')fixture.failNext('admin/update','revision_conflict');fixture.hold('admin/update');click(save());await flush();assert.equal(writes().length,1);
  if(scenario!=='unchanged'){if(scenario==='default')click(field().querySelector('[data-action=default]'));else edit(input(),'0','change');}
  if(scenario==='read-failure')fixture.failNext('admin/read','fixture_network_failure');fixture.release('admin/update');await flush();
  assert.equal(input().value,scenario==='unchanged'?'2':'0','late readback must preserve post-submit inheritance/default');
  if(scenario==='unchanged')assert.equal(mode(),'keep');
  else if(scenario==='conflict'||scenario==='read-failure'){
   assert.equal(save().getAttribute('aria-disabled'),'true');click(refresh());await flush();assert.equal(input().value,'0');
   click(document.querySelector('section[data-owner="ff14/ff14"] [data-action=discard]'));await flush();assert.equal(mode(),'keep');assert.equal(input().value,scenario==='conflict'?'0':'2');
  }else{assert.equal(mode(),'clear');click(save());await flush();assert.deepEqual(writes().at(-1).body,{module_id:'ff14/ff14',expected_revision:8,updates:[{field:'reply_theme',mode:'clear'}]});assert.equal(input().value,'0');assert.equal(mode(),'keep');}
 }else{
  await draftSecrets();const old=action('font-refresh');
  if(scenario==='font-late'){
   fixture.hold('admin/font-status');fixture.failNext('admin/font-status','admin_authorization_denied');click(old);await flush();fixture.context({session:'async-two'});await flush();await draftSecrets();fixture.release('admin/font-status');await flush();assert.ok(secrets().every(n=>n.value==='synthetic-test-pair'&&n.type==='text'),'obsolete font denial must not clear new owner drafts');
  }else{
   if(scenario==='font-fence'){fixture.hold('admin/update');click(save());await flush();}
   fixture.failNext('admin/font-status',scenario==='font-network'?'fixture_network_failure':'admin_authorization_denied');click(old);await flush();
   if(scenario==='font-network'){assert.ok(secrets().every(n=>n.value==='synthetic-test-pair'&&n.type==='text'));assert.equal(save().getAttribute('aria-disabled'),'false');}
   else{
    assert.ok(secrets().every(n=>n.value===''&&n.type==='password'),'font access loss must erase and mask every owner secret');
    assert.ok([...document.querySelectorAll('[data-role=credential-mode]')].every(n=>n.value==='keep'&&n.disabled));assert.ok([...document.querySelectorAll('[data-role=credential-clear-confirm]')].every(n=>!n.checked));
    assert.equal(field('default_region','game_link/core').querySelector('[data-role=value]').value,'1');assert.equal(save().getAttribute('aria-disabled'),'true');assert.match(document.querySelector((shell?'#settings-container ':'')+'#status').textContent,/管理授权/);
    if(scenario==='font-fence'){fixture.release('admin/update');await flush();assert.equal(save().getAttribute('aria-disabled'),'true');assert.ok(secrets().every(n=>n.value===''));}
    fixture.failNext('admin/credential-readiness','admin_authorization_denied');click(refresh());await flush();assert.equal(save().getAttribute('aria-disabled'),'true');assert.ok(secrets().every(n=>n.value===''));
    click(refresh());await flush();assert.equal(save().getAttribute('aria-disabled'),'false');assert.ok(secrets().every(n=>n.value===''));
   }
  }
  assert.equal(fixture.calls.some(c=>c.endpoint==='admin/credential-update'),false);
 }
 window.dispatchEvent(new window.Event('pagehide'));await flush();console.log(`${surface} ${scenario} passed`);
}finally{window.dispatchEvent(new window.Event('pagehide'));dom.window.close();}
