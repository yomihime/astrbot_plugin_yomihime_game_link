import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {mount} from '../../../examples/modules/minimal/pages/entry.js';
function harness(){
 const dom=new JSDOM('<main></main>'),container=dom.window.document.querySelector('main'),cleanups=[];
 const calls=[];let active=true;
 const context={owner:'minimal/demo',runtimeId:'test',epoch:1,boundary:'page',available:true,theme:'light',locale:'en'};
 const page=mount(container,{routeId:'home',context,scope:{isCurrent:()=>active,onDispose:f=>cleanups.push(f)},services:{invoke(...args){let resolve,reject;const result=new Promise((a,b)=>{resolve=a;reject=b;});calls.push({args,resolve,reject});return result;}}});
 return {container,page,calls,context,dispose(){active=false;cleanups.forEach(f=>f());dom.window.close();}};
}
test('public-contract page invokes exact capability once and renders safe facts',async()=>{
 const h=harness();const button=h.container.querySelector('button');button.click();button.click();
 assert.equal(h.calls.length,1);assert.deepEqual(h.calls[0].args,['status',{}]);
 h.calls[0].resolve({status:'success',model_facts:{ready:true}});await Promise.resolve();await Promise.resolve();
 assert.match(h.container.textContent,/Minimal module is ready/);h.dispose();assert.equal(h.container.children.length,0);
});
test('owner change/unload ignores late result and disables expired binding',async()=>{
 const h=harness();h.container.querySelector('button').click();h.page.update({...h.context,owner:'other/demo'});
 h.calls[0].resolve({status:'success',model_facts:{ready:true}});await Promise.resolve();await Promise.resolve();
 assert.match(h.container.textContent,/binding expired/);assert.equal(h.container.querySelector('button').disabled,true);
 h.dispose();assert.equal(h.container.textContent,'');
});
