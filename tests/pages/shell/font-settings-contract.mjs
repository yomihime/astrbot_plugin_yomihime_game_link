// Built production shell with an offline bridge, never live Host credentials.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {root} from './compile.mjs';
import {catalog as shellCatalog} from './fixtures.mjs';
import {themeCatalog,themeSnapshot} from './theme-settings-fixtures.mjs';
const dom=new JSDOM('<div id="app"></div>',{pretendToBeVisual:true,url:process.argv.includes('--cold-module')?'http://localhost/#/module/ff14%2Fff14':'http://localhost/#/settings'}),window=dom.window,document=window.document,calls=[];
window.Element.prototype.scrollTo=function(){};window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
window.confirm=()=>{throw Error('Unexpected native confirmation');};
let deferredRetry=null,deferredUpload=null;
const font={revision:0,state:'unavailable',source:'system',error:'download_failed',filename:'NotoSansCJKsc-Regular.otf',size:16437364,sha256:'2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b',download_url:'https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf'};
const values=themeSnapshot(),declarations=themeCatalog(),registry=shellCatalog();registry.modules.push(...shellCatalog('example/demo').modules);
const status={registry_revision:5,modules:registry.modules.map(m=>({module_id:m.module_id,enabled:true,lifecycle:'active',health:'ready',epoch:1,registry_revision:5,reason_code:null}))};
function post(endpoint,body){
 calls.push({endpoint,body:structuredClone(body)});
 if(endpoint==='admin/font-retry')return new Promise(resolve=>deferredRetry=resolve);
 if(endpoint==='admin/font-upload')return new Promise(resolve=>deferredUpload=resolve);
 const responses={'admin/catalog':declarations,'admin/read':values,'admin/credential-catalog':{schema_version:1,fields:[]},'admin/credential-status':{},'admin/credential-readiness':{ready:true,state:'ready',reason_code:'ready'},'admin/font-status':font,'admin/modules':status};
 assert.ok(endpoint in responses,endpoint);return Promise.resolve(responses[endpoint]);
}
const realm=vm.createContext({window,document,navigator:window.navigator,location:window.location,console,TextEncoder,TextDecoder,AbortController,URL,setTimeout,clearTimeout,setInterval,clearInterval,getComputedStyle:window.getComputedStyle.bind(window),requestAnimationFrame:window.requestAnimationFrame.bind(window),cancelAnimationFrame:window.cancelAnimationFrame.bind(window),post,registry});
for(const name of ['HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])realm[name]=window[name];
vm.runInContext(`const handlers=new Set(),context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'theme-settings'};window.AstrBotPluginPage={onContext(fn){handlers.add(fn);fn(context);return()=>handlers.delete(fn)},ready:()=>Promise.resolve(context),upload:(endpoint,file)=>post(endpoint,{name:file.name,size:file.size}).then(value=>JSON.parse(JSON.stringify(value))),apiGet:()=>Promise.resolve(JSON.parse(JSON.stringify(registry))),apiPost:(endpoint,body)=>post(endpoint,body).then(value=>JSON.parse(JSON.stringify(value)),error=>{throw Error(error.message)})};globalThis.emitContext=value=>handlers.forEach(fn=>fn(value));`,realm);
const sources=new Map([['/runtime.js',await readFile(root+'pages/shell/runtime.js','utf8')],['/app.js',await readFile(root+'pages/shell/app.js','utf8')],['/module-loader.js',"export function loadPage(){throw Error('No business load in theme settings test')}"]]),cache=new Map();
function module(path){if(cache.has(path))return cache.get(path);assert.ok(sources.has(path));const mod=new vm.SourceTextModule(sources.get(path),{context:realm,identifier:'http://localhost'+path});cache.set(path,mod);return mod;}
const flush=async()=>{for(let i=0;i<12;i++){await new Promise(resolve=>setTimeout(resolve,0));await cache.get('/runtime.js').namespace.nextTick();}};
const click=node=>{assert.ok(node);node.focus();node.click();};
const action=name=>document.querySelector(`[data-action="${name}"]`);
const go=async hash=>{window.location.hash=hash;await flush();};
try{
 const app=module('/app.js');await app.link((specifier,reference)=>module(new URL(specifier,reference.identifier).pathname));await app.evaluate();await flush();
 if(process.argv.includes('--cold-module')){assert.equal(calls.some(c=>c.endpoint==='admin/font-status'),false);await go('#/settings');}
 const panel=document.querySelector('[data-font-settings]');assert.ok(panel);assert.equal(document.querySelectorAll('[data-font-settings]').length,1);
 assert.equal(panel.closest('[data-owner]').dataset.owner,'game_link/core');assert.equal(panel.closest('fieldset').dataset.group,'reply_style');
 assert.equal(panel.querySelector('a').getAttribute('href'),font.download_url);assert.equal(panel.querySelector('a').getAttribute('rel'),'noopener noreferrer');
 assert.match(panel.textContent,/系统字体/);assert.match(panel.textContent,/NotoSansCJKsc-Regular.otf/);assert.equal(panel.querySelector('#font-upload').disabled,false);
 assert.equal(panel.querySelector('#font-upload').getAttribute('aria-describedby'),'font-help font-status');
 const count=()=>calls.filter(call=>call.endpoint==='admin/font-status').length;const first=count();assert.ok(first>=1);
 await go('#/settings/module/ff14%2Fff14');assert.equal(document.querySelector('[data-font-settings]'),null);await go('#/settings');assert.ok(count()>first);assert.ok(document.querySelector('[data-font-settings]'));
 const input=document.querySelector('#font-upload');Object.defineProperty(input,'files',{value:[{name:'wrong.otf',size:4}],configurable:true});input.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.match(document.querySelector('#font-status').textContent,/请选择指定版本/);
 Object.defineProperty(input,'files',{value:[],configurable:true});input.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(calls.some(call=>call.endpoint==='admin/update'),false);
 // Double retry is one request, and navigation drops a late response.
 click(action('font-retry'));click(action('font-retry'));await flush();assert.equal(calls.filter(c=>c.endpoint==='admin/font-retry').length,1);assert.equal(action('font-retry').disabled,true);assert.equal(document.querySelector('#font-upload').disabled,true);assert.match(document.querySelector('#font-status').textContent,/请求重新下载/);
 await go('#/settings/module/ff14%2Fff14');deferredRetry({...font,state:'ready',source:'canonical',error:null});await flush();assert.equal(document.querySelector('[data-font-settings]'),null);
 await go('#/settings');assert.match(document.querySelector('#font-status').textContent,/系统字体/);
 const validInput=document.querySelector('#font-upload');Object.defineProperty(validInput,'files',{value:[{name:font.filename,size:font.size}],configurable:true});validInput.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.match(document.querySelector('#font-status').textContent,/正在上传/);assert.equal(action('font-retry').disabled,true);assert.equal(calls.filter(c=>c.endpoint==='admin/font-upload').length,1);
 await go('#/');deferredUpload({...font,state:'ready',source:'canonical',error:null});await flush();assert.equal(window.location.hash,'#/');await go('#/settings');assert.match(document.querySelector('#font-status').textContent,/系统字体/);
 vm.runInContext('emitContext({...context,pageName:"wrong"})',realm);await flush();assert.equal(document.querySelector('[data-font-settings]'),null);
 window.dispatchEvent(new window.Event('pagehide'));await flush();console.log('production font settings passed');
}finally{dom.window.close();}
