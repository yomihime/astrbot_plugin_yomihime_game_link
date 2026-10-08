// Formal production Vue harness; old renderer cases are mapped in legacy-retirement.md.
import assert from 'node:assert/strict';
import {readFile,mkdir,copyFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule,root as repositoryRoot} from '../shell/compile.mjs';
import {queryInput,validateQueryResult} from '../../../modules/ff14/pages/src/query-contract.js';
const out=resolve(repositoryRoot,'.architecture-refactor/unified-settings/formal-vue-tests-'+process.pid);
await mkdir(resolve(out,'module-assets/ff14/ff14/pages/dist'),{recursive:true});
await copyFile(resolve(repositoryRoot,'pages/shell/runtime.js'),resolve(out,'runtime.js'));
await copyFile(resolve(repositoryRoot,'modules/ff14/pages/dist/entry.js'),resolve(out,'module-assets/ff14/ff14/pages/dist/entry.js'));
const dom=new JSDOM('<body></body>',{pretendToBeVisual:true,url:'http://localhost/'}),window=dom.window;
for(const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])globalThis[key]=key==='window'?window:key==='document'?window.document:window[key];
globalThis.getComputedStyle=window.getComputedStyle.bind(window);globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
window.Element.prototype.scrollTo=function(){};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const runtime=await import(pathToFileURL(resolve(out,'runtime.js')));
const {mount}=await import(pathToFileURL(resolve(out,'module-assets/ff14/ff14/pages/dist/entry.js')));
const {createScope}=await sourceModule(repositoryRoot+'pages/frontend/src/lifecycle.ts');
let run=0;
export async function flush(){for(let i=0;i<35;i++)await Promise.resolve();await runtime.nextTick();}
export async function queryHarness(route='items'){
 const container=window.document.createElement('div');window.document.body.append(container);const posts=[],scope=createScope(()=>true),timers=new Map(),nativeTimeout=globalThis.setTimeout,nativeClear=globalThis.clearTimeout;
 globalThis.setTimeout=(callback,delay,...args)=>{if(delay===35000){const id={};timers.set(id,callback);return id;}return nativeTimeout(callback,delay,...args);};globalThis.clearTimeout=id=>{if(timers.delete(id))return;nativeClear(id);};
 const page=mount(container,{routeId:route,context:{owner:'ff14/ff14',runtimeId:'formal-vue',epoch:1,boundary:'fixture-'+(++run),theme:'light',locale:'zh-CN',available:true},scope,services:{invoke(capability,parameters){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});posts.push({capability,parameters,resolve,reject});return promise;}}});
 container.all=predicate=>[...container.querySelectorAll('*')].filter(predicate);
 for(const type of ['HTMLButtonElement']){const proto=window[type].prototype;if(!proto.press)proto.press=async function(key){this.dispatchEvent(new window.KeyboardEvent('keydown',{key,bubbles:true}));if(key==='Enter'||key===' ')this.click();await flush();};}
 return {root:container,document:window.document,bridge:{posts},clock:{fire(){for(const callback of timers.values())callback();timers.clear();}},app:{destroy(){scope.dispose();container.remove();globalThis.setTimeout=nativeTimeout;globalThis.clearTimeout=nativeClear;}},update:page.update};
}
export async function edit(h,name,value){const input=h.root.querySelector('#ff14-'+(h.root.querySelector('form input')?.id.split('-')[1]||'items')+'-'+name);assert.ok(input);input.value=value;input.dispatchEvent(new window.Event('input',{bubbles:true}));await flush();}
export function submitFor(h){return h.root.querySelector('button[type=submit]');}
export async function runUiATests(){
 const h=await queryHarness('items');try{await edit(h,'query','');submitFor(h).click();await flush();assert.equal(h.bridge.posts.length,0);assert.equal(h.root.querySelector('input').getAttribute('aria-invalid'),'true');assert.equal(h.document.activeElement,h.root.querySelector('input'));}finally{h.app.destroy();}
 return {kind:'formal-vue-invalid-input',passed:1};
}
if(import.meta.url===pathToFileURL(process.argv[1]||'').href)console.log(JSON.stringify(await runUiATests()));
export {queryInput,validateQueryResult};
