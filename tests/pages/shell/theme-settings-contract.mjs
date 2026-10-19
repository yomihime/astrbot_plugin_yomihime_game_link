// Built production shell with an offline bridge, never live Host credentials.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {themeCatalog,themeSnapshot} from './theme-settings-fixtures.mjs';
import {masterKeyReady} from '../management/fixtures.mjs';
const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:'http://localhost/#/settings'}),window=dom.window,document=window.document,calls=[];
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
let nativeCalls=0,conflict=false;window.confirm=()=>{nativeCalls++;return false;};
const values=themeSnapshot(),declarations=themeCatalog(),registry=shellCatalog();registry.modules.push(...shellCatalog('example/demo').modules);
// A new Core label is rendered directly, with no frontend theme-label registry.
for(const f of declarations.fields.filter(f=>f.choices))f.choices.find(c=>c.value==='cool_white').label='目录提供的冷白 <b>原文</b>';
const status={registry_revision:5,modules:registry.modules.map(m=>({module_id:m.module_id,enabled:true,lifecycle:'active',health:'ready',epoch:1,registry_revision:5,reason_code:null}))};
function post(endpoint,body){
 calls.push({endpoint,body:structuredClone(body)});
 if(endpoint==='admin/update'){
  if(conflict)return Promise.reject(Error('revision_conflict'));
  const row=values[body.module_id];assert.equal(body.expected_revision,row.revision);
  for(const update of body.updates){const field=declarations.fields.find(f=>f.module_id===body.module_id&&f.name===update.field);assert.ok(field);
   row.fields[update.field]={value:update.mode==='clear'?field.default:update.value,state:'valid',present:update.mode!=='clear',source:update.mode==='clear'?'default':'sqlite'};
  }row.revision++;return Promise.resolve({module_id:body.module_id,revision:row.revision});
 }
 const responses={'admin/catalog':declarations,'admin/read':values,'admin/master-key-status':masterKeyReady(),'admin/credential-catalog':{schema_version:1,fields:[]},'admin/credential-status':{},'admin/credential-readiness':{ready:true,state:'ready',reason_code:'ready',diagnostics:{key_state:'valid_format',codec_state:'available'}},'admin/modules':status};
 assert.ok(endpoint in responses,endpoint);return Promise.resolve(responses[endpoint]);
}
const realm=vm.createContext({window,document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),post,registry});
for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
vm.runInContext(`const handlers=new Set(),context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'theme-settings'};window.AstrBotPluginView={onContext(fn){handlers.add(fn);fn(context);return()=>handlers.delete(fn)},ready:()=>Promise.resolve(context),apiGet:()=>Promise.resolve(JSON.parse(JSON.stringify(registry))),apiPost:(endpoint,body)=>post(endpoint,body).then(value=>JSON.parse(JSON.stringify(value)),error=>{throw Error(error.message)})};globalThis.emitContext=value=>handlers.forEach(fn=>fn(value));`,realm);
const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in theme settings test')}"]]),cache=new Map();
function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path));const mod=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,mod);return mod;}
const flush=async()=>{for(let i=0;i<12;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
const click=node=>{assert.ok(node);node.focus();node.click();};
const field=(owner,name=owner==='game_link/core'?'image_theme':'reply_theme')=>document.querySelector(`[data-owner="${owner}"][data-field="${name}"]`);
const input=owner=>field(owner).querySelector('[data-role="value"]'),mode=owner=>field(owner).querySelector('[data-role="mode"]').value;
const save=owner=>document.querySelector(`section[data-owner="${owner}"] [data-action="save"]`);
const action=name=>document.querySelector(`[data-action="${name}"]`);
const edit=async(owner,value)=>{input(owner).value=value;input(owner).dispatchEvent(new window.Event('change',{bubbles:true}));await flush();};
const go=async hash=>{window.location.hash=hash;await flush();};
const writes=()=>calls.filter(call=>call.endpoint==='admin/update'),last=()=>writes().at(-1).body;
try{
 const app=module('/app.js');await app.link((specifier,reference)=>module(new URL(specifier,reference.identifier).pathname));await app.evaluate();await flush();
 for(const owner of ['game_link/core','ff14/ff14','example/demo']){
  assert.equal(field(owner).querySelector('label').textContent,'图片主题');assert.equal(field(owner).closest('fieldset').querySelector('legend').textContent,'回复样式');
  assert.ok([...input(owner).options].some(option=>option.textContent==='目录提供的冷白 <b>原文</b>'));assert.equal(input(owner).querySelector('b'),null);
 }
 assert.deepEqual([...input('game_link/core').options].slice(1).map(option=>option.textContent),['暖桃纸','目录提供的冷白 <b>原文</b>','深靛']);
 assert.equal(input('game_link/core').value,'0');assert.equal(input('ff14/ff14').value,'0');assert.equal(input('ff14/ff14').selectedOptions[0].textContent,'跟随核心');assert.equal(input('example/demo').value,'3');assert.equal(writes().length,0);
 // Opening inherited settings is pristine; choosing a theme then returning to
 // inherited absence restores pristine state and does not prompt on navigation.
 await go('#/settings/module/ff14%2Fff14');assert.equal(action('confirm-navigation'),null);assert.equal(mode('ff14/ff14'),'keep');
 await go('#/module/ff14%2Fff14');assert.equal(action('confirm-navigation'),null);assert.equal(window.location.hash,'#/module/ff14%2Fff14');
 await go('#/settings/module/ff14%2Fff14');await edit('ff14/ff14','2');assert.equal(mode('ff14/ff14'),'replace');await edit('ff14/ff14','0');assert.equal(mode('ff14/ff14'),'keep');
 await go('#/module/ff14%2Fff14');assert.equal(action('confirm-navigation'),null);assert.equal(window.location.hash,'#/module/ff14%2Fff14');assert.equal(writes().length,0);await go('#/settings');
 // Core draft survives canceled and accepted navigation, without leaking into a module write.
 await edit('game_link/core','2');const coreNode=input('game_link/core');await go('#/settings/module/ff14%2Fff14');assert.ok(action('confirm-navigation'));assert.equal(window.location.hash,'#/settings');
 click(action('cancel-navigation'));await flush();assert.equal(coreNode.value,'2');await go('#/settings/module/ff14%2Fff14');click(action('confirm-navigation'));await flush();
 assert.equal(input('game_link/core'),coreNode);assert.equal(coreNode.value,'2');assert.equal(mode('game_link/core'),'replace');assert.equal(save('game_link/core').getAttribute('aria-disabled'),'true');
 save('game_link/core').dispatchEvent(new window.Event('click'));await flush();assert.equal(writes().length,0);
 await edit('ff14/ff14','2');assert.equal(mode('ff14/ff14'),'replace');click(save('ff14/ff14'));await flush();
 assert.deepEqual(last(),{module_id:'ff14/ff14',expected_revision:7,updates:[{field:'reply_theme',mode:'replace',value:'cool_white'}]});assert.equal(mode('ff14/ff14'),'keep');
 assert.equal(values['game_link/core'].fields.image_theme.value,'warm_paper');assert.equal(values['example/demo'].fields.reply_theme.value,'deep_indigo');assert.equal(coreNode.value,'2');
 // Explicit Follow Core is a deletion, never replace:null, and acknowledged readback selects it.
 await edit('ff14/ff14','0');assert.equal(mode('ff14/ff14'),'clear');click(save('ff14/ff14'));await flush();assert.deepEqual(last().updates,[{field:'reply_theme',mode:'clear'}]);assert.equal(last().expected_revision,8);assert.equal(input('ff14/ff14').value,'0');assert.equal(mode('ff14/ff14'),'keep');
 assert.deepEqual(values['ff14/ff14'].fields.reply_theme,{value:null,state:'valid',present:false,source:'default'});
 // A normal default reset follows the same draft path and writes only after Save.
 await edit('ff14/ff14','3');click(field('ff14/ff14').querySelector('[data-action="default"]'));await flush();assert.equal(mode('ff14/ff14'),'keep');assert.equal(input('ff14/ff14').value,'0');
 await edit('ff14/ff14','3');click(save('ff14/ff14'));await flush();const count=writes().length;click(field('ff14/ff14').querySelector('[data-action="default"]'));await flush();assert.equal(mode('ff14/ff14'),'clear');assert.equal(input('ff14/ff14').value,'0');assert.equal(writes().length,count);click(save('ff14/ff14'));await flush();assert.deepEqual(last().updates,[{field:'reply_theme',mode:'clear'}]);
 await go('#/settings');assert.equal(action('confirm-navigation'),null);assert.equal(input('game_link/core'),coreNode);assert.equal(coreNode.value,'2');click(save('game_link/core'));await flush();assert.deepEqual(last(),{module_id:'game_link/core',expected_revision:7,updates:[{field:'image_theme',mode:'replace',value:'deep_indigo'}]});assert.equal(input('ff14/ff14').value,'0');
 click(field('game_link/core').querySelector('[data-action="default"]'));await flush();assert.equal(input('game_link/core').value,'0');assert.equal(mode('game_link/core'),'clear');click(save('game_link/core'));await flush();assert.deepEqual(last().updates,[{field:'image_theme',mode:'clear'}]);assert.equal(values['game_link/core'].fields.image_theme.value,'warm_paper');
 // Conflict preserves the theme draft and fences writes until refresh supplies a revision.
 await go('#/settings/module/ff14%2Fff14');await edit('ff14/ff14','1');conflict=true;click(save('ff14/ff14'));await flush();assert.match(document.querySelector('#status').textContent,/配置已变化/);assert.equal(input('ff14/ff14').value,'1');assert.equal(save('ff14/ff14').getAttribute('aria-disabled'),'true');
 const rejected=writes().length;save('ff14/ff14').dispatchEvent(new window.Event('click'));await flush();assert.equal(writes().length,rejected);conflict=false;values['ff14/ff14'].revision=20;click(document.querySelector('#settings-container #refresh'));await flush();assert.equal(input('ff14/ff14').value,'1');click(save('ff14/ff14'));await flush();assert.equal(last().expected_revision,20);
 // Invalid stored null stays visibly invalid, never masquerades as inherited default.
 Object.assign(values['ff14/ff14'].fields.reply_theme,{value:null,state:'invalid',present:true,source:'sqlite'});click(document.querySelector('#settings-container #refresh'));await flush();assert.equal(input('ff14/ff14').value,'');assert.match(field('ff14/ff14').textContent,/当前值无效/);
 const card=document.querySelector('section[data-owner="ff14/ff14"]');click(card.querySelector('[data-action="discard"]'));await flush();assert.equal(input('ff14/ff14').value,'');click(field('ff14/ff14').querySelector('[data-action="default"]'));click(save('ff14/ff14'));await flush();assert.equal(input('ff14/ff14').value,'0');
 const counts=calls.length;vm.runInContext('emitContext({...context,isDark:true,locale:"en-US"})',realm);await flush();assert.equal(calls.length,counts);assert.equal(input('game_link/core'),coreNode);
 assert.equal(nativeCalls,0);assert.equal(calls.some(call=>call.endpoint==='invoke'||call.endpoint==='admin/credential-update'),false);window.dispatchEvent(new window.Event('pagehide'));await flush();console.log('production theme settings passed');
}finally{dom.window.close();}
