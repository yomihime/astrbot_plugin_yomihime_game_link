import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceRuntimeURL} from './source-build.mjs';
const dom=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?dom.window:name==='document'?dom.window.document:dom.window[name];
const ui=await import(await sourceRuntimeURL());
test('public fields preserve stable control identity and accessible hint/error relationships',async()=>{
 const root=document.createElement('div');document.body.append(root);const error=ui.ref(''),draft=ui.ref('draft');
 const app=ui.createApp({render:()=>ui.h(ui.FormGrid,{columns:2},()=>ui.h(ui.FormField,{controlId:'test-field',label:'Public field',hint:'Help',error:error.value},{default:({controlAttrs})=>ui.h('input',{...controlAttrs,value:draft.value,onInput:e=>draft.value=e.target.value})}))});
 app.mount(root);try{
  const input=root.querySelector('input');assert.equal(root.querySelector('label').htmlFor,input.id);
  assert.equal(input.getAttribute('aria-describedby'),'test-field-hint');assert.equal(input.getAttribute('aria-invalid'),'false');
  input.focus();error.value='Invalid value';await ui.nextTick();
  assert.equal(root.querySelector('input'),input);assert.equal(document.activeElement,input);assert.equal(input.value,'draft');
  assert.equal(input.getAttribute('aria-describedby'),'test-field-hint test-field-error');assert.equal(input.getAttribute('aria-invalid'),'true');
  assert.equal(root.querySelector('#test-field-error').hidden,false);assert.equal(root.querySelector('[role=alert]'),null);
  error.value='';await ui.nextTick();assert.equal(root.querySelector('#test-field-error').hidden,true);assert.equal(root.querySelector('#test-field-error').textContent,'');
 }finally{app.unmount();root.remove();}
});
test('feedback keeps polite compatibility and only announces alerts explicitly',()=>{
 const root=document.createElement('div'),app=ui.createApp({render:()=>ui.h('div',[ui.h(ui.StatusHint,{},()=> 'ordinary'),ui.h(ui.StatusHint,{tone:'error',role:'alert'},()=> 'failed'),ui.h(ui.StatusHint,{role:'none'},()=> 'static')])});app.mount(root);
 try{const rows=root.querySelectorAll('p');assert.equal(rows[0].getAttribute('role'),'status');assert.equal(rows[0].getAttribute('aria-live'),'polite');assert.equal(rows[1].getAttribute('role'),'alert');assert.equal(rows[1].getAttribute('aria-live'),'assertive');assert.equal(rows[2].getAttribute('role'),null);assert.equal(rows[2].getAttribute('aria-live'),null);}finally{app.unmount();}
});
