// Offline Vue/JSDOM test: a sandboxed Host has no native modal permission.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {stripTypeScriptTypes} from 'node:module';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const root=new URL('../../../',import.meta.url);
const dom=new JSDOM('<html><body></body></html>',{pretendToBeVisual:true,url:'http://localhost/'});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?dom.window:name==='document'?dom.window.document:dom.window[name];
const runtimeURL=new URL('pages/shell/runtime.js',root).href;
const runtime=await import(runtimeURL);
let confirmationSource=stripTypeScriptTypes(await readFile(new URL('pages/frontend/src/confirmation.ts',root),'utf8'),{mode:'strip'}).replace("from 'vue'",'from '+JSON.stringify(runtimeURL)).replace("from 'naive-ui'",'from '+JSON.stringify(runtimeURL));
const confirmationURL='data:text/javascript;base64,'+Buffer.from(confirmationSource).toString('base64');
const {createInlineConfirmation}=await import(confirmationURL);
let source=stripTypeScriptTypes(await readFile(new URL('pages/frontend/src/module-management.ts',root),'utf8'),{mode:'strip'});
source=source.replace("from 'vue'",'from '+JSON.stringify(runtimeURL)).replace("from 'naive-ui'",'from '+JSON.stringify(runtimeURL)).replace("from './lifecycle'",'from '+JSON.stringify(new URL('pages/frontend/src/lifecycle.ts',root).href)).replace("from './confirmation'",'from '+JSON.stringify(confirmationURL));
const {createModuleManagement}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const {validateCatalog:configurationCatalog,validateSnapshot:configurationSnapshot}=await import(new URL('pages/management/app.js',root));
const configurationFixture=await import('../management/fixtures.mjs');
const flush=async()=>{for(let i=0;i<20;i++){await Promise.resolve();await runtime.nextTick();}};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'one'};
const status=(revision=5,epoch=2)=>({registry_revision:revision,modules:[{module_id:'ff14/ff14',enabled:false,lifecycle:'stopped',health:'ready',epoch,registry_revision:revision,reason_code:null}]});
function fixture(beforeOperate,interactionKey,home){
 const container=dom.window.document.createElement('div');dom.window.document.body.append(container);const calls=[];let emit,changed=0,confirmCalls=0;
 const window={...dom.window,confirm(){confirmCalls++;return false;}};
 const bridge={onContext(fn){emit=fn;fn(context);return()=>{emit=null;};},ready:()=>Promise.resolve(context),apiPost(endpoint,body){const d=deferred();calls.push({endpoint,body,...d});return d.promise;}};
 const view=createModuleManagement(container,bridge,window,'00-game-link',()=>{changed++;},beforeOperate,undefined,interactionKey,home);view.start();
 return {container,calls,view,emit(value){emit?.(value);},get changed(){return changed;},get confirmCalls(){return confirmCalls;},close(){view.dispose();container.remove();}};
}
const button=(f,text)=>{const node=[...f.container.querySelectorAll('button')].find(n=>n.textContent===text);assert.ok(node,'button '+text);return node;};
const pending=(f,endpoint)=>{const call=f.calls.filter(c=>c.endpoint===endpoint).at(-1);assert.ok(call);return call;};
async function load(f,value=status()){pending(f,'admin/modules').resolve(value);await flush();}
test('home ordinary settings status uses authorized catalog/read and never claims overall or upstream readiness',async()=>{
 const options={onSettings(){},labels:()=>new Map([['ff14/ff14','最终幻想 XIV']]),catalog:configurationCatalog,snapshot:configurationSnapshot},f=fixture(undefined,undefined,options);
 try{await load(f);assert.match(f.container.textContent,/普通设置未核对/);pending(f,'admin/catalog').resolve(configurationFixture.catalog());await flush();pending(f,'admin/read').resolve(configurationFixture.snapshot());await flush();assert.match(f.container.textContent,/普通设置已校验/);assert.match(f.container.textContent,/最终幻想 XIV/);assert.doesNotMatch(f.container.textContent,/上游可用|配置已就绪/);assert.deepEqual(f.calls.map(call=>call.endpoint),['admin/modules','admin/catalog','admin/read']);const refresh=f.view.refresh();await load(f);pending(f,'admin/catalog').reject(Error('admin_authorization_denied'));await refresh;await flush();assert.match(f.container.textContent,/普通设置未核对/);assert.doesNotMatch(f.container.textContent,/普通设置已校验/);assert.equal(f.calls.filter(call=>['admin/module-enabled','admin/module-unload','admin/update','admin/credential-update'].includes(call.endpoint)).length,0);}finally{f.close();}
});
const open=async f=>{button(f,'解除挂载（保留数据）').click();await flush();};
const confirm=f=>f.container.querySelector('[data-action="confirm-unload"]');
const cancel=f=>f.container.querySelector('[data-action="cancel-unload"]');
test('sandboxed unload uses accessible in-page confirmation, cancellation and one exact CAS request',async()=>{
 const f=fixture();try{await load(f);const trigger=button(f,'解除挂载（保留数据）');trigger.focus();await open(f);assert.equal(f.confirmCalls,0);const group=f.container.querySelector('[data-unload-confirm]');assert.ok(group);assert.equal(group.getAttribute('role'),'group');assert.match(group.getAttribute('aria-label'),/ff14\/ff14/);assert.match(group.textContent,/配置、凭据、缓存及业务数据全部保留/);assert.match(group.textContent,/注册版本 5/);assert.equal(f.calls.length,1);assert.equal(dom.window.document.activeElement,cancel(f));cancel(f).click();await flush();assert.equal(f.container.querySelector('[data-unload-confirm]'),null);assert.equal(dom.window.document.activeElement,trigger);assert.equal(trigger.disabled,false);assert.equal(f.calls.length,1);
 await open(f);const oldConfirm=confirm(f);oldConfirm.click();oldConfirm.click();await flush();assert.equal(f.calls.length,2);assert.deepEqual(pending(f,'admin/module-unload').body,{module_id:'ff14/ff14',expected_registry_revision:5});assert.equal(f.container.querySelector('[data-unload-confirm]'),null);pending(f,'admin/module-unload').resolve({module_id:'ff14/ff14',registry_revision:6,state:'unloaded',data_retained:true,reopen_required:true});await flush();await load(f,{registry_revision:6,modules:[]});assert.equal(f.changed,1);assert.match(f.container.textContent,/模块已解除挂载，持久数据保留/);
 }finally{f.close();}
});
test('refresh revokes the pending confirmation and old click; reopened confirmation binds latest CAS',async()=>{
 const f=fixture();try{await load(f);await open(f);const old=confirm(f);assert.ok(old);const refresh=f.view.refresh();await flush();assert.equal(f.container.querySelector('[data-unload-confirm]'),null);old.dispatchEvent(new dom.window.Event('click'));assert.equal(f.calls.length,2);await load(f,status(7,3));await refresh;await open(f);assert.match(f.container.querySelector('[data-unload-confirm]').textContent,/注册版本 7/);confirm(f).click();await flush();assert.deepEqual(pending(f,'admin/module-unload').body,{module_id:'ff14/ff14',expected_registry_revision:7});
 }finally{f.close();}
});
test('presentation keeps confirmation and cancel focus; Escape cancels without request',async()=>{
 const f=fixture();try{await load(f);const trigger=button(f,'解除挂载（保留数据）');trigger.focus();await open(f);const node=cancel(f);f.emit({...context,isDark:true,locale:'en-US'});await flush();assert.equal(cancel(f),node);assert.equal(dom.window.document.activeElement,node);f.container.querySelector('[data-unload-confirm]').dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await flush();assert.equal(f.container.querySelector('[data-unload-confirm]'),null);assert.equal(dom.window.document.activeElement,trigger);assert.equal(trigger.disabled,false);assert.equal(f.calls.length,1);
 }finally{f.close();}
});
test('new or invalid context closes confirmation and fences detached old action and late results',async()=>{
 for(const next of [{...context,session:'two'},{...context,pageName:'wrong'}]){const f=fixture();try{await load(f);await open(f);const old=confirm(f);assert.ok(old);f.emit(next);await flush();assert.equal(f.container.querySelector('[data-unload-confirm]'),null);old.dispatchEvent(new dom.window.Event('click'));await flush();assert.equal(f.calls.filter(c=>c.endpoint==='admin/module-unload').length,0);}finally{f.close();}}
 const f=fixture();try{await load(f);await open(f);confirm(f).click();await flush();const old=pending(f,'admin/module-unload');f.emit({...context,session:'two'});await flush();old.resolve({module_id:'ff14/ff14',registry_revision:6,state:'unloaded',data_retained:true,reopen_required:true});await flush();assert.equal(f.changed,0);assert.doesNotMatch(f.container.textContent,/模块已解除挂载/);assert.equal(f.calls.length,3);}finally{f.close();}
});
test('dispose closes confirmation and detached old controls cannot request; dirty guard denial is zero mutation',async()=>{
 const f=fixture();try{await load(f);await open(f);const old=confirm(f);assert.ok(old);f.view.dispose();old.dispatchEvent(new dom.window.Event('click'));assert.equal(f.calls.length,1);}finally{f.close();}
 const denied=fixture(()=>false);try{await load(denied);await open(denied);assert.equal(denied.calls.length,1);assert.equal(denied.container.querySelector('[data-unload-confirm]'),null);}finally{denied.close();}
});

test('a background draft or owner change cancels old unload consent before any write',async()=>{
 let interaction='owner-one/draft-1';const f=fixture(undefined,()=>interaction);try{await load(f);await open(f);const old=confirm(f);assert.ok(old);interaction='owner-one/draft-2';old.click();await flush();assert.equal(f.calls.length,1);assert.equal(f.container.querySelector('[data-unload-confirm]'),null);await open(f);const oldOwner=confirm(f);interaction='owner-two/draft-2';oldOwner.click();await flush();assert.equal(f.calls.length,1);}finally{f.close();}
});
test('async dirty guard cancellation or draft change fences enable/disable/unload without native modals',async()=>{
 for(const action of ['enable','unload']){let interaction='draft-1';const guard=deferred(),f=fixture(()=>guard.promise,()=>interaction);try{await load(f);button(f,action==='enable'?'启用 / 恢复':'解除挂载（保留数据）').click();await flush();interaction='draft-2';guard.resolve(true);await flush();assert.equal(f.calls.length,1);assert.equal(f.container.querySelector('[data-unload-confirm]'),null);}finally{f.close();}}
 const guard=deferred(),f=fixture(()=>guard.promise);try{await load(f);button(f,'启用 / 恢复').click();await flush();const refresh=f.view.refresh();await flush();guard.resolve(true);await flush();assert.equal(f.calls.filter(c=>c.endpoint==='admin/module-enabled').length,0);await load(f,status(7));await refresh;}finally{f.close();}
});
test('confirmation replacement, invalidation, Escape and disposal resolve only the current request',async()=>{
 const container=dom.window.document.createElement('div');dom.window.document.body.append(container);const confirmation=createInlineConfirmation(dom.window.document);let version=1;
 const app=runtime.createApp({render:()=>confirmation.render()});app.mount(container);
 const request=(kind='navigation')=>confirmation.ask({kind,title:'离开当前设置？',message:'保留普通配置草稿并离开设置；秘密输入将清空。',confirmLabel:'继续',isCurrent:()=>version===1});
 try{const old=request();await flush();const oldButton=container.querySelector('[data-action="confirm-navigation"]');const next=request('module-draft');assert.equal(await old,false);await flush();oldButton.dispatchEvent(new dom.window.Event('click'));version=2;confirmation.invalidate();assert.equal(await next,false);await flush();assert.equal(container.children.length,0);version=1;const escape=request();await flush();container.querySelector('[data-confirmation]').dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(await escape,false);const closed=request();confirmation.dispose();assert.equal(await closed,false);assert.equal(await request(),false);}finally{confirmation.dispose();app.unmount();container.remove();}
});
test('supported shell and lifecycle sources contain no native confirm calls',async()=>{
 for(const path of ['pages/frontend/src/main.ts','pages/frontend/src/module-management.ts'])assert.doesNotMatch(await readFile(new URL(path,root),'utf8'),/\.confirm\s*\(/);
});

test('production shell dirty navigation and lifecycle guards use owned confirmations without native modals',()=>{
 const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./confirmation-global-contract.mjs',import.meta.url))],{encoding:'utf8'});assert.equal(result.status,0,result.stdout+'\n'+result.stderr);assert.match(result.stdout,/zero native modals passed/);
});

test('context change during post-unload refresh cannot notify or report old success',async()=>{
 const f=fixture();try{await load(f);await open(f);confirm(f).click();await flush();pending(f,'admin/module-unload').resolve({module_id:'ff14/ff14',registry_revision:6,state:'unloaded',data_retained:true,reopen_required:true});await flush();const oldRefresh=pending(f,'admin/modules');f.emit({...context,session:'two'});await flush();oldRefresh.resolve({registry_revision:6,modules:[]});await flush();assert.equal(f.changed,0);assert.doesNotMatch(f.container.textContent,/模块已解除挂载/);}finally{f.close();}
});

test('deferred cancel restoration never steals a newer focus or restores invalidated consent',async()=>{
 const container=dom.window.document.createElement('div'),trigger=dom.window.document.createElement('button'),elsewhere=dom.window.document.createElement('input');dom.window.document.body.append(trigger,elsewhere,container);const confirmation=createInlineConfirmation(dom.window.document);let current=true;
 const app=runtime.createApp({render:()=>confirmation.render()});app.mount(container);
 const request=()=>confirmation.ask({kind:'navigation',title:'焦点恢复',message:'仅当前确认可以恢复焦点。',confirmLabel:'继续',trigger,isCurrent:()=>current});
 try{
   trigger.focus();let result=request();await flush();container.querySelector('[data-confirm-cancel]').click();elsewhere.focus();await flush();assert.equal(await result,false);assert.equal(dom.window.document.activeElement,elsewhere);
   trigger.focus();result=request();await flush();container.querySelector('[data-confirm-cancel]').click();current=false;await flush();assert.equal(await result,false);assert.notEqual(dom.window.document.activeElement,trigger);
   current=true;trigger.focus();result=request();await flush();container.querySelector('[data-confirm-cancel]').click();const replacement=request();await flush();assert.equal(await result,false);assert.equal(dom.window.document.activeElement,container.querySelector('[data-confirm-cancel]'));confirmation.cancel();assert.equal(await replacement,false);
 }finally{confirmation.dispose();app.unmount();trigger.remove();elsewhere.remove();container.remove();}
});
test('cancelled unload cannot restore focus after context loss or user movement',async()=>{
 for(const loseContext of [false,true]){const f=fixture();const elsewhere=dom.window.document.createElement('input');dom.window.document.body.append(elsewhere);try{await load(f);const trigger=button(f,'解除挂载（保留数据）');trigger.focus();await open(f);cancel(f).click();if(loseContext)f.emit({...context,pageName:'wrong'});else elsewhere.focus();await flush();assert.notEqual(dom.window.document.activeElement,trigger);if(!loseContext)assert.equal(dom.window.document.activeElement,elsewhere);assert.equal(f.calls.filter(c=>c.endpoint==='admin/module-unload').length,0);}finally{f.close();elsewhere.remove();}}
});
