import {test,after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const dom=new JSDOM('<body></body>',{pretendToBeVisual:true,url:'http://localhost/'}),window=dom.window;
for(const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent','Image'])globalThis[key]=key==='window'?window:key==='document'?window.document:window[key];
globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
globalThis.getComputedStyle=window.getComputedStyle.bind(window);window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const url=source=>'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
const controller=url(stripTypeScriptTypes(await readFile('modules/ff14/pages/src/settings-controller.ts','utf8'),{mode:'strip'}));
const source=stripTypeScriptTypes(await readFile('modules/ff14/pages/src/settings-page.ts','utf8'),{mode:'strip'})
 .replace("from 'vue'",`from ${JSON.stringify(pathToFileURL(resolve('pages/frontend/node_modules/vue/index.mjs')).href)}`)
 .replace("from 'naive-ui'",`from ${JSON.stringify(pathToFileURL(resolve('pages/frontend/node_modules/naive-ui/es/index.mjs')).href)}`)
 .replace("from './settings-controller'",`from ${JSON.stringify(controller)}`);
const {mountSettings}=await import(url(source));
const {nextTick}=await import(pathToFileURL(resolve('pages/frontend/node_modules/vue/index.mjs')));
async function flush(){for(let i=0;i<20;i++)await Promise.resolve();await nextTick();}
after(()=>window.close());
test('actual source settings UI preserves mask/choices and editable fenced drafts after CAS/UNKNOWN',async()=>{
 let revision=4,confirmation=0,storedTheme=null,writeError='',heldWrite=false,releaseWrite;const calls=[],callbacks=[],container=window.document.createElement('main');window.document.body.append(container);
 const context={owner:'ff14/ff14',runtimeId:'source-ui',epoch:1,boundary:'source-ui',available:true,theme:'light',locale:'zh-CN'};
 const page=mountSettings(container,{routeId:'config',context,scope:{isCurrent:()=>true,onDispose:f=>callbacks.push(f)},services:{config:{current:async()=>({revision,fields:{reply_theme:{value:storedTheme,state:'valid',present:storedTheme!==null,source:storedTheme===null?'default':'sqlite'}},descriptors:{reply_theme:{description:'Theme',default:null,value_schema:{},choices:[{value:'host-choice',label:'Host provided label'}]}}}),save:async(r,v)=>{calls.push([r,v]);if(heldWrite)await new Promise(resolve=>releaseWrite=resolve);if(writeError)throw Error(writeError);storedTheme=v.reply_theme;return{revision:++revision};}},credentials:{status:async()=>({revision,fields:{credential_fflogs_cn:{configured:true,decryptable:true,upstream:'unverified'}}}),replace:async(r,f,p)=>{calls.push([r,f,p]);return{revision:++revision};},clear:async()=>{confirmation++;throw Error('operation_cancelled');}}}});
 try{
  await flush();const theme=container.querySelector('#ff14-config-reply_theme');assert.deepEqual([...theme.options].map(o=>o.textContent),['跟随全局设置','Host provided label']);
  const inputs=[...container.querySelectorAll('input')].filter(i=>i.id.includes('credential_fflogs'));assert.equal(inputs.length,4);assert.ok(inputs.every(i=>i.type==='password'&&i.maxLength===512));
  const edit=(id,value)=>{const input=container.querySelector('#'+id);input.value=value;input.dispatchEvent(new window.Event('input',{bubbles:true}));};
  edit('credential_fflogs_global-client_id','synthetic');edit('credential_fflogs_global-client_secret','synthetic-secret');await flush();
  const clear=[...container.querySelectorAll('button')].find(b=>b.textContent.includes('清除凭据'));clear.click();await flush();assert.equal(confirmation,1);assert.equal(container.querySelector('#credential_fflogs_global-client_secret').value,'synthetic-secret');
  theme.value='host-choice';theme.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(calls[0][1].reply_theme,'host-choice');
  theme.value='';theme.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(calls[1][1].reply_theme,null);
  for(const error of ['revision_conflict','network_lost']){
   theme.value='host-choice';theme.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();heldWrite=true;writeError=error;
   container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();
   assert.equal(theme.disabled,false);theme.value='';theme.dispatchEvent(new window.Event('change',{bubbles:true}));releaseWrite();await flush();
   assert.equal(theme.disabled,false);assert.equal(container.querySelector('button[type=submit]').disabled,true);
   theme.value='host-choice';theme.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();theme.value='';theme.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();
   const before=calls.length;container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(calls.length,before);
   storedTheme='host-choice';revision++;heldWrite=false;writeError='';[...container.querySelectorAll('button')].find(b=>b.textContent.includes('重新读取状态')).click();await flush();assert.equal(theme.value,'');assert.equal(container.querySelector('button[type=submit]').disabled,false);
   container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(calls.at(-1)[1].reply_theme,null);
  }
  page.update({...context,owner:'other/demo'});await flush();assert.equal(theme.disabled,true);const before=calls.length;theme.value='host-choice';theme.dispatchEvent(new window.Event('change',{bubbles:true}));container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();assert.equal(calls.length,before);
 }finally{page.dispose();callbacks.forEach(f=>f());container.remove();}
});

test('ordinary discard follows actual auth classification and recovers without writes',async()=>{
 let denied=false,writes=0;const callbacks=[],container=window.document.createElement('main');window.document.body.append(container);
 const context={owner:'ff14/ff14',runtimeId:'discard-auth-ui',epoch:1,boundary:'discard-auth-ui',available:true,theme:'light',locale:'zh-CN'};
 const page=mountSettings(container,{routeId:'config',context,scope:{isCurrent:()=>true,onDispose:f=>callbacks.push(f)},services:{
  config:{current:async()=>{if(denied)throw Error('admin_authorization_denied');return{revision:9,fields:{ff14_calendar_default_days:{value:7,state:'valid',present:true,source:'sqlite'}}};},save:async()=>{writes++;return{revision:10};}},
  credentials:{status:async()=>({revision:9,fields:{}}),replace:async()=>{writes++;return{revision:10};},clear:async()=>{writes++;return{revision:10};}},
 }});
 const button=text=>[...container.querySelectorAll('button')].find(b=>b.textContent.includes(text));
 try{
  await flush();const days=container.querySelector('#ff14-config-ff14_calendar_default_days');assert.ok(days);assert.equal(days.value,'7');
  days.value='17';days.dispatchEvent(new window.Event('input',{bubbles:true}));await flush();assert.equal(button('放弃修改').disabled,false);
  denied=true;button('重新读取状态').click();await flush();assert.match(container.textContent,/管理授权或模块绑定已失效/);assert.equal(days.value,'17');assert.equal(days.disabled,true);
  assert.equal(button('放弃修改').disabled,true);button('放弃修改').click();await flush();assert.equal(days.value,'17');assert.equal(writes,0);
  denied=false;button('重新读取状态').click();await flush();assert.equal(days.value,'17');assert.equal(days.disabled,false);assert.equal(button('放弃修改').disabled,false);
  button('放弃修改').click();await flush();assert.equal(days.value,'7');assert.equal(button('放弃修改').disabled,true);assert.equal(writes,0);
 }finally{page.dispose();callbacks.forEach(f=>f());container.remove();}
});
