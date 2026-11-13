import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceApp,sourceRuntimeURL} from './source-build.mjs';
const dom=new JSDOM('<body></body>',{pretendToBeVisual:true,url:'http://localhost/'});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?dom.window:name==='document'?dom.window.document:dom.window[name];
const runtime=await import(await sourceRuntimeURL());
const {createInstallationManagement}=await sourceApp('pages/frontend/src/installation-management.ts');
const flush=async()=>{for(let i=0;i<12;i++){await Promise.resolve();await runtime.nextTick();}};
const plan=(grant=0)=>({ok:true,core_epoch:7,package_id:'arbitrary/package',plan_id:'a'.repeat(48),installed_digest:'d'.repeat(64),state:'approval_required',signature_state:'unsigned',grant_revision:grant,artifact_revision:0,permissions_delta:{added:[{path:'module/config/key',requested:{description:'<script>unsafe()</script>'}}],removed:[],changed:[]}});
const status=()=>({ok:true,core_epoch:7,core:{installed_version:'0.2.0',running_version:'0.2.0',installed_digest:'a'.repeat(64),running_digest:'a'.repeat(64),phase:'active',ready:true},packages:[plan()],publishers:[]});
function fixture(){const calls=[];let generation=1,live=true,confirmation;
 const bridge={apiPost(endpoint,body){return new Promise((resolve,reject)=>calls.push({endpoint,body,resolve,reject}));}};
 const view=createInstallationManagement(bridge,{cancel(){},async ask(value){confirmation=value;return value.isCurrent();}},()=>live,()=>generation,()=> 'en-US');
 const container=document.createElement('div');document.body.append(container);const app=runtime.createApp({render:()=>view.render()});app.mount(container);
 return {view,calls,container,get confirmation(){return confirmation;},invalidate(){generation++;view.invalidate();},close(){live=false;view.dispose();app.unmount();container.remove();}};
}
const click=async(f,text)=>{const btn=[...f.container.querySelectorAll('button')].find(b=>b.textContent===text);assert.ok(btn,text);assert.equal(btn.disabled,false);btn.click();await flush();};
test('unsigned approvals bind exact plan/CAS, fresh inspect, and render declaration text safely',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;await flush();
 work=f.view.inspect('arbitrary/package');assert.deepEqual(f.calls.at(-1).body,{package_id:'arbitrary/package',core_epoch:7});f.calls.at(-1).resolve(plan());await work;await flush();
 assert.equal(f.container.querySelector('script'),null);assert.match(f.container.textContent,/<script>unsafe/);
 assert.equal([...f.container.querySelectorAll('button')].find(b=>b.textContent==='Approve exact artifact').disabled,true);
 await click(f,'Approve package permissions');assert.match(f.confirmation.message(),/permissions_delta/);assert.deepEqual(f.calls.at(-1).body,{package_id:'arbitrary/package',plan_id:'a'.repeat(48),expected_revision:0,core_epoch:7});
 f.calls.at(-1).resolve({ok:true,core_epoch:7,revision:1});await flush();assert.match(f.calls.at(-1).endpoint,/bootstrap$/);f.calls.at(-1).resolve(status());await flush();assert.match(f.calls.at(-1).endpoint,/inspect$/);f.calls.at(-1).resolve(plan(1));await flush();
 await click(f,'Approve exact artifact');assert.deepEqual(f.calls.at(-1).body,{package_id:'arbitrary/package',plan_id:'a'.repeat(48),expected_revision:0,core_epoch:7});
 }finally{f.close();}});
test('stale epoch invalidates writes; explicit refresh learns a new epoch; old requests cannot restore plans',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 work=f.view.inspect('arbitrary/package');const stale=f.calls.at(-1);f.invalidate();stale.resolve(plan(1));await work;await flush();assert.doesNotMatch(f.container.textContent,/Permission changes/);
 work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 work=f.view.inspect('arbitrary/package');f.calls.at(-1).resolve({ok:false,error:{code:'stale_generation'}});await work;await flush();assert.match(f.container.textContent,/stale_generation/);const count=f.calls.length;await f.view.mutate('retry',{package_id:'arbitrary/package'},{});assert.equal(f.calls.length,count);
 }finally{f.close();}});
test('signed packages require explicit publisher key/channels then fresh inspection; revocation binds inventory revision',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;await flush();work=f.view.inspect('arbitrary/package');f.calls.at(-1).resolve({...plan(),signature_state:'publisher_untrusted'});await work;await flush();
 assert.equal([...f.container.querySelectorAll('button')].find(b=>b.textContent==='Approve package permissions').disabled,true);
 for(const [id,value] of [['hot-update-public-key','A'.repeat(43)+'='],['hot-update-channels','preview, stable']]){const input=f.container.querySelector('#'+id);input.value=value;input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));}await flush();
 await click(f,'Enroll publisher');assert.deepEqual(f.calls.at(-1).body,{public_key_base64:'A'.repeat(43)+'=',channels:['preview','stable'],expected_revision:0,core_epoch:7});
 f.calls.at(-1).resolve({ok:true,core_epoch:7,revision:3});await flush();const publisher={key_id:'f'.repeat(64),public_key_base64:'A'.repeat(43)+'=',channels:['preview','stable'],revision:3,revoked:false};f.calls.at(-1).resolve({...status(),publishers:[publisher]});await flush();assert.equal(f.container.querySelector('[aria-label="Permission changes (added / removed / changed)"]'),null);
 work=f.view.inspect('arbitrary/package');f.calls.at(-1).resolve({...plan(),signature_state:'verified'});await work;await flush();assert.equal([...f.container.querySelectorAll('button')].find(b=>b.textContent==='Approve package permissions').disabled,false);
 await click(f,'Revoke publisher');assert.deepEqual(f.calls.at(-1).body,{key_id:'f'.repeat(64),expected_revision:3,core_epoch:7});
 }finally{f.close();}});
test('bridge exception details are never rendered',async()=>{const f=fixture();try{const work=f.view.refresh();f.calls.at(-1).reject(Error('/private/owner-token=secret-value'));await work;await flush();assert.doesNotMatch(f.container.textContent,/private|owner-token|secret-value/);assert.match(f.container.textContent,/hot_update_failed/);}finally{f.close();}});
test('ordinary Host-installed package needs no approval and keeps inspection and retry available',async()=>{const f=fixture();try{
 const ordinary={...plan(),state:'active',signature_state:'host_install'};
 let work=f.view.refresh();f.calls.at(-1).resolve({...status(),packages:[ordinary]});await work;
 work=f.view.inspect('arbitrary/package');f.calls.at(-1).resolve(ordinary);await work;await flush();
 assert.match(f.container.textContent,/Installed and enabled by AstrBot; no additional approval is needed/);
 const labels=[...f.container.querySelectorAll('button')].map(b=>b.textContent);
 for(const label of ['Approve package permissions','Approve exact artifact','Revoke package approval','Revoke artifact approval'])assert.equal(labels.includes(label),false,label);
 assert.equal(f.container.querySelector('.legacy-publisher-management').open,false);
 assert.doesNotMatch(f.container.querySelector('[data-package-id]').textContent,/Package approval revision|Artifact approval revision/);
 await click(f,'Retry activation');assert.match(f.calls.at(-1).endpoint,/retry$/);assert.deepEqual(f.calls.at(-1).body,{package_id:'arbitrary/package',core_epoch:7});
 f.calls.at(-1).resolve({ok:true,core_epoch:7});await flush();f.calls.at(-1).resolve({...status(),packages:[ordinary]});await flush();f.calls.at(-1).resolve(ordinary);await flush();
 assert.equal(f.calls.some(c=>/approve|enroll|revoke/.test(c.endpoint)),false);
 }finally{f.close();}});
test('confirmed mutation followed by bootstrap failure preserves failure and never claims refreshed status',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 work=f.view.mutate('retry',{package_id:'arbitrary/package'},{});await flush();f.calls.at(-1).resolve({ok:true,core_epoch:7,state:'active'});await flush();assert.match(f.calls.at(-1).endpoint,/bootstrap$/);const count=f.calls.length;
 f.calls.at(-1).reject(Error('transport failed'));await work;await flush();assert.equal(f.calls.length,count);assert.match(f.container.textContent,/The operation was not confirmed/);assert.match(f.container.textContent,/hot_update_failed/);assert.doesNotMatch(f.container.textContent,/Status refreshed/);
 }finally{f.close();}});
test('confirmed mutation followed by fresh inspection failure preserves the exact failure notice',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 work=f.view.mutate('retry',{package_id:'arbitrary/package'},{});await flush();f.calls.at(-1).resolve({ok:true,core_epoch:7,state:'active'});await flush();f.calls.at(-1).resolve(status());await flush();assert.match(f.calls.at(-1).endpoint,/inspect$/);
 f.calls.at(-1).resolve({ok:false,error:{code:'plan_changed'}});await work;await flush();assert.match(f.container.textContent,/The operation was not confirmed/);assert.match(f.container.textContent,/plan_changed/);assert.doesNotMatch(f.container.textContent,/Status refreshed/);
 }finally{f.close();}});
test('navigation during post-mutation refresh cannot inspect an old package or overwrite the newer view notice',async()=>{const f=fixture();try{
 let work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 const mutation=f.view.mutate('retry',{package_id:'arbitrary/package'},{});await flush();f.calls.at(-1).resolve({ok:true,core_epoch:7,state:'active'});await flush();const oldRefresh=f.calls.at(-1);assert.match(oldRefresh.endpoint,/bootstrap$/);
 f.invalidate();work=f.view.refresh();f.calls.at(-1).resolve(status());await work;
 work=f.view.inspect('new/context');f.calls.at(-1).resolve({ok:false,error:{code:'package_unavailable'}});await work;await flush();const count=f.calls.length,notice=f.container.querySelector('[role="status"]').textContent;
 oldRefresh.resolve(status());await mutation;await flush();assert.equal(f.calls.length,count);assert.equal(f.container.querySelector('[role="status"]').textContent,notice);assert.match(notice,/package_unavailable/);assert.doesNotMatch(f.container.textContent,/Status refreshed/);
 }finally{f.close();}});
