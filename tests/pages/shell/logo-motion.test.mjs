import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceRuntimeURL} from './source-build.mjs';
const read=path=>readFile(new URL('../../../'+path,import.meta.url),'utf8');

test('logos are opaque by default and only builtin FF14 opts into motion',async()=>{
 const css=await read('pages/frontend/src/ui/tokens.css');
 assert.match(css,/\.ui-logo-rebound\{[^}]*background:var\(--ui-gold\)/);
 assert.doesNotMatch(css,/ui-logo-reveal|@keyframes ui-logo-beam/);
 const shell=await read('pages/frontend/src/main.ts');
 assert.match(shell,/module\.module_id==='ff14\/ff14'&&module\.source\?\.kind==='builtin'\?'rebound':'none'/);
 const ff14=await read('modules/ff14/pages/src/settings-page.ts');
 assert.match(ff14,/h\(LogoMark,\{effect:'rebound'/);
});

test('optional sweep plays once, respects reduced motion and cleans up',async()=>{
 const dom=new JSDOM('<main></main>',{pretendToBeVisual:true,url:'http://localhost/'});
 const previous=new Map();
 for(const key of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver']){
  previous.set(key,Object.getOwnPropertyDescriptor(globalThis,key));
  Object.defineProperty(globalThis,key,{configurable:true,writable:true,value:key==='window'?dom.window:key==='document'?dom.window.document:dom.window[key]});
 }
 let reduced=false,listener=null,added=0,removed=0,calls=[];
 dom.window.matchMedia=()=>({get matches(){return reduced;},addEventListener(_name,fn){listener=fn;added++;},removeEventListener(_name,fn){if(listener===fn)listener=null;removed++;}});
 dom.window.HTMLElement.prototype.animate=function(frames,options){const a={id:'',cancelled:false,cancel(){this.cancelled=true;}};calls.push({target:this,frames,options,a});return a;};
 let app;
 try{
  const {createApp,h,ref,LogoMark,nextTick}=await import(await sourceRuntimeURL());
  const host=dom.window.document.querySelector('main'),tick=ref(0);
  app=createApp({setup:()=>()=>h('div',[h('p',String(tick.value)),h(LogoMark,{effect:'rebound'})])});app.mount(host);
  const root=host.querySelector('.ui-logo-rebound');assert.equal(root.getAttribute('aria-hidden'),'true');
  assert.equal(calls.length,1);assert.equal(calls[0].target.className,'ui-logo-sweep');assert.notEqual(calls[0].target,root);assert.equal(calls[0].options.iterations,1);assert.equal(calls[0].options.fill,'forwards');assert.equal(calls[0].frames.at(-1).opacity,0);
  tick.value++;await nextTick();assert.equal(calls.length,1);assert.equal(host.querySelector('.ui-logo-rebound'),root);
  reduced=true;listener({matches:true});assert.equal(calls[0].a.cancelled,true);reduced=false;listener({matches:false});assert.equal(calls.length,1);
  app.unmount();app=null;assert.equal(added,removed);
  app=createApp({render:()=>h(LogoMark,{effect:'rebound'})});app.mount(host);assert.equal(calls.length,2);app.unmount();app=null;assert.equal(calls[1].a.cancelled,true);
  app=createApp({render:()=>h(LogoMark)});app.mount(host);assert.equal(calls.length,2);app.unmount();app=null;
  reduced=true;app=createApp({render:()=>h(LogoMark,{effect:'rebound'})});app.mount(host);assert.equal(calls.length,2);app.unmount();app=null;
  reduced=false;delete dom.window.HTMLElement.prototype.animate;app=createApp({render:()=>h(LogoMark,{effect:'rebound'})});app.mount(host);assert.equal(calls.length,2);app.unmount();app=null;
  dom.window.HTMLElement.prototype.animate=()=>{throw Error('unsupported');};app=createApp({render:()=>h(LogoMark,{effect:'rebound'})});app.mount(host);app.unmount();app=null;
  delete dom.window.matchMedia;app=createApp({render:()=>h(LogoMark,{effect:'rebound'})});app.mount(host);
 }finally{app?.unmount();dom.window.close();for(const [key,descriptor] of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
});
