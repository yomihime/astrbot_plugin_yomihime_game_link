// Offline JSDOM/bridge contracts. Not Host/browser acceptance.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const runtimeDOM=new JSDOM('<html><body></body></html>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=runtimeDOM.window[name];
const {createManagementPage,validateCatalog,validateSnapshot,updateBody}=await import('./source-management.mjs');
import {catalog as moduleCatalog,snapshot as moduleSnapshot,context,credentialCatalog,credentialStatus,masterKeyReady} from './fixtures.mjs';
// Ordinary lifecycle assertions now exercise the Core-owned form. Module UI lives behind public services.
function catalog(){const value=moduleCatalog();value.fields=value.fields.map(field=>({...field,module_id:'game_link/core'}));return value;}
function snapshot(revision=1){const value=moduleSnapshot(revision);value['game_link/core'].fields={...value['game_link/core'].fields,...value['ff14/ff14'].fields};delete value['ff14/ff14'];return value;}
const html=await readFile(new URL('./embedded-fixture.html',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(initial=context,resolveReady=true,options={}){
  const dom=new JSDOM(html,{pretendToBeVisual:true,url:'http://localhost/management/'}),calls=[],ready=deferred();let callback=null;
  // Ordinary request counts remain scoped to ordinary endpoints;
  // independent credential tests capture their own requests and assertions.
  const bridge={onContext(fn){callback=fn;if(initial)fn(initial);return()=>{callback=null;};},ready:()=>ready.promise,apiPost(endpoint,body){if(endpoint==='admin/master-key-status')return Promise.resolve(masterKeyReady());if(endpoint==='admin/credential-readiness')return Promise.resolve({ready:true,state:'ready',reason_code:'ready',diagnostics:{key_state:'valid_format',codec_state:'available'}});if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());const d=deferred(),call={endpoint,body,settled:false,promise:d.promise,resolve(value){this.settled=true;d.resolve(value);},reject(value){this.settled=true;d.reject(value);}};calls.push(call);return d.promise;}};
  const app=createManagementPage(dom.window.document,bridge,dom.window,options);app.start();if(resolveReady)ready.resolve(initial||context);
  return {dom,app,calls,ready,context(value){callback?.(value);},get document(){return dom.window.document;},close(){app.close();dom.window.close();}};
}
const pending=(f,endpoint)=>{const call=f.calls.find(c=>c.endpoint===`admin/${endpoint}`&&!c.settled);assert.ok(call,'pending '+endpoint);return call;};
async function load(f,declarations=catalog(),values=snapshot()){pending(f,'catalog').resolve(declarations);await flush();pending(f,'read').resolve(values);await flush();}
const field=(f,name)=>f.document.querySelector(`[data-field="${name}"]`);
const mode=(f,name,value)=>{const node=field(f,name).querySelector('[data-role=mode]');node.value=value;node.dispatchEvent(new f.dom.window.Event('change'));};
const input=(f,name)=>field(f,name).querySelector('[data-role=value]');
const save=(f,target)=>f.document.querySelector(`section[data-owner="${target}"] [data-action=save]`);
const status=f=>f.document.getElementById('status').textContent;
const click=(f,id)=>f.document.getElementById(id).click();
const region='default_region',zone='ff14_calendar_default_timezone',days='ff14_calendar_default_days';

test('semantic draft notifications cover edit/default/discard/save while initialization and theme stay silent',async()=>{
 let edits=0,saved=0;const f=fixture(context,true,{onDraftMutation(){edits++;},onConfigurationSaved(){saved++;}});
 try{
  await load(f);assert.equal(edits,0);f.context({...context,isDark:true,locale:'en-US'});await flush();assert.equal(edits,0);
  const node=input(f,zone);node.value='Europe/London';node.dispatchEvent(new f.dom.window.Event('input'));node.dispatchEvent(new f.dom.window.Event('change'));assert.equal(edits,1);
  field(f,zone).querySelector('[data-action="default"]').click();assert.equal(edits,2);field(f,zone).querySelector('[data-action="default"]').click();assert.equal(edits,2);
  f.document.querySelector('section[data-owner="game_link/core"] [data-action="discard"]').click();assert.equal(edits,3);
  f.document.querySelector('section[data-owner="game_link/core"] [data-action="discard"]').click();assert.equal(edits,3);
  field(f,zone).querySelector('[data-action="default"]').click();save(f,'game_link/core').click();pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();assert.equal(saved,1);assert.equal(edits,4);await load(f,catalog(),snapshot(2));assert.equal(edits,5);assert.equal(f.app.dirty(),false);
 }finally{f.close();}
});

test('validated ordinary ACK notifies before failed readback; conflict and stale ACK do not notify',async()=>{
 for(const outcome of ['read-failure','conflict','stale','malformed']){
  let saved=0;const f=fixture(context,true,{onConfigurationSaved(){saved++;}});
  try{
   await load(f);field(f,zone).querySelector('[data-action="default"]').click();save(f,'game_link/core').click();const update=pending(f,'update');
   if(outcome==='conflict')update.reject(Error('revision_conflict'));
   else if(outcome==='stale'){f.context({...context,session:'new'});update.resolve({module_id:'game_link/core',revision:2});}
   else if(outcome==='malformed')update.resolve({module_id:'game_link/core',revision:1});
   else{update.resolve({module_id:'game_link/core',revision:2});await flush();assert.equal(saved,1);pending(f,'catalog').reject(Error('controlled_read_failure'));}
   await flush();assert.equal(saved,outcome==='read-failure'?1:0);
  }finally{f.close();}
 }
});

test('retired global credential form is absent; Module secrets stay behind public services',async()=>{
 let edits=0,saved=0;const f=fixture(context,true,{onDraftMutation(){edits++;},onConfigurationSaved(){saved++;}});
 try{
  await load(f);assert.equal(edits,0);assert.equal(f.document.querySelector('[data-credential]'),null);assert.equal(saved,0);assert.equal(f.calls.some(call=>call.endpoint==='admin/credential-update'),false);
 }finally{f.close();}
});

test('ordinary current values edit directly; discard is local and hidden modes are not menus',async()=>{
 const f=fixture();try{await load(f);const node=input(f,zone),baseline=node.value,control=field(f,zone);assert.equal(control.querySelector('[data-role="mode"]').type,'hidden');assert.equal(node.disabled,false);node.value='Europe/London';node.dispatchEvent(new f.dom.window.Event('input'));assert.equal(control.querySelector('[data-role="mode"]').value,'replace');const writes=f.calls.filter(c=>c.endpoint==='admin/update').length;f.document.querySelector('section[data-owner="game_link/core"] [data-action="discard"]').click();await flush();assert.equal(node.value,baseline);assert.equal(status(f),'\u5df2\u653e\u5f03\u672a\u4fdd\u5b58\u7684\u666e\u901a\u8bbe\u7f6e\u3002');assert.equal(control.querySelector('[data-role="mode"]').value,'keep');assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,writes);}finally{f.close();}
});

test('restore default is one exact field clear draft and writes only after explicit CAS save',async()=>{
 const f=fixture();try{await load(f);field(f,zone).querySelector('[data-action="default"]').click();assert.equal(field(f,zone).querySelector('[data-role="mode"]').value,'clear');assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,0);save(f,'game_link/core').click();assert.deepEqual(pending(f,'update').body,{module_id:'game_link/core',expected_revision:1,updates:[{field:zone,mode:'clear'}]});}finally{f.close();}
});

test('suspension fences old controls and resume preserves the Core ordinary draft',async()=>{
 const f=fixture();try{await load(f);const node=input(f,zone);node.value='Europe/London';node.dispatchEvent(new f.dom.window.Event('input'));const old=save(f,'game_link/core');f.app.suspend();await flush();assert.equal(old.getAttribute('aria-disabled'),'true');old.dispatchEvent(new f.dom.window.Event('click'));assert.equal(f.calls.filter(c=>c.endpoint==='admin/update').length,0);f.app.resume();await load(f);assert.equal(node.value,'Europe/London');assert.equal(field(f,zone).querySelector('[data-role="mode"]').value,'replace');assert.equal(node.disabled,false);}finally{f.close();}
});

test('production status has direct theme colors with no empty alert wrapper or color transition',async()=>{
 const f=fixture();try{
  await load(f);const node=f.document.getElementById('status');assert.equal(node.textContent,'');assert.equal(node.closest('.n-alert'),null);
  const stylesheet=f.document.createElement('style');stylesheet.textContent=await readFile(new URL('../../../pages/frontend/src/ui/tokens.css',import.meta.url),'utf8');f.document.head.append(stylesheet);
  const rules=[...stylesheet.sheet.cssRules],matches=property=>rules.flatMap(rule=>rule.selectorText?.split(',').filter(selector=>node.matches(selector)).map(selector=>({selector,value:rule.style.getPropertyValue(property)}))||[]).filter(rule=>rule.value);
  assert.ok(matches('color').some(rule=>rule.selector.includes('#status')&&rule.value==='var(--text)'));assert.ok(matches('transition').some(rule=>rule.value==='none'));
  for(const isDark of [true,false]){f.context({...context,isDark});await flush();assert.equal(f.document.getElementById('status'),node);assert.equal(node.closest('.n-alert'),null);assert.equal(f.document.documentElement.dataset.theme,isDark?'dark':'light');assert.equal(node.getAttribute('aria-live'),'polite');assert.equal(node.getAttribute('role'),'status');}
 }finally{f.close();}
});

test('synchronous onContext/ready issue one catalog, then one read; tiny metadata needs no root branch',async()=>{
  const f=fixture();try{await flush();assert.equal(f.calls.length,1);assert.equal(f.calls[0].endpoint,'admin/catalog');await load(f);
    assert.equal(f.document.querySelectorAll('#fields > section').length,1);assert.match(field(f,'sample').textContent,/无读取或修改权限/);assert.equal(input(f,'sample').value,'');assert.equal(input(f,'sample').disabled,true);assert.equal(f.document.querySelector('section[data-owner="example/demo"]'),null);
    assert.equal(input(f,region).tagName,'SELECT');assert.equal(input(f,days).type,'number');assert.equal(input(f,zone).type,'text');
    f.context({...context});f.context({...context,theme:'dark',locale:'en-US'});await flush();assert.equal(f.calls.length,2);assert.equal(f.document.documentElement.dataset.theme,'dark');
  }finally{f.close();}
});
test('same/presentation context preserves dirty input, focus, selection and pending read without extra requests',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='Europe/London';node.focus();node.setSelectionRange(2,8);f.document.documentElement.scrollTop=91;
    f.context({...context,isDark:true,locale:'en-US',i18n:{label:'x'}});await flush();assert.equal(input(f,zone),node);assert.equal(node.value,'Europe/London');assert.equal(f.document.activeElement,node);assert.equal(node.selectionStart,2);assert.equal(f.calls.length,2);
    click(f,'refresh');await flush();assert.equal(input(f,zone),node);assert.equal(f.document.activeElement,node);assert.equal(save(f,'game_link/core').disabled,true);
    pending(f,'catalog').resolve(catalog());await flush();f.context({...context,isDark:false});assert.equal(f.calls.length,4);pending(f,'read').resolve(snapshot(2));await flush();
    assert.equal(input(f,zone),node);assert.equal(node.value,'Europe/London');assert.equal(node.selectionEnd,8);assert.equal(f.document.documentElement.scrollTop,91);assert.equal(save(f,'game_link/core').disabled,false);
  }finally{f.close();}
});
test('presentation during initial catalog/read retains the in-flight request',async()=>{
  const f=fixture();try{f.context({...context,locale:'en-US'});await flush();assert.equal(f.calls.length,1);pending(f,'catalog').resolve(catalog());await flush();f.context({...context,theme:'dark'});pending(f,'read').resolve(snapshot());await flush();assert.equal(f.calls.length,2);assert.ok(input(f,region));}finally{f.close();}
});
test('refresh failure retains drafts and nodes, gates writes until explicit recovery',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='UTC';node.focus();click(f,'refresh');pending(f,'catalog').reject(Error('PRIVATE backend error'));await flush();
    assert.equal(input(f,zone),node);assert.equal(node.value,'UTC');assert.equal(f.document.activeElement,node);assert.equal(save(f,'game_link/core').disabled,true);assert.match(status(f),/未取得成功确认/);assert.ok(!status(f).includes('PRIVATE'));
    click(f,'refresh');await load(f,catalog(),snapshot(2));assert.equal(node.value,'UTC');assert.equal(save(f,'game_link/core').disabled,false);
  }finally{f.close();}
});
test('real unknown boundary change fences late read and clears drafts across identities',async()=>{
  const f=fixture();try{pending(f,'catalog').resolve(catalog());await flush();const old=pending(f,'read');f.context({...context,session:'mock-two',instance:{revision:2}});await flush();assert.equal(f.calls.length,3);old.resolve(snapshot(99));await flush();assert.equal(f.document.querySelectorAll('.config-field').length,0);
    await load(f,catalog(),snapshot(2));mode(f,region,'clear');save(f,'game_link/core').click();assert.equal(pending(f,'update').body.expected_revision,2);pending(f,'update').reject(Error('revision_conflict'));await flush();assert.ok(!f.document.querySelector('#fields').textContent.includes('99'));
    mode(f,zone,'replace');input(f,zone).value='DIRTY-OLD';f.context({...context,session:'mock-three'});await load(f,catalog(),snapshot(3));assert.equal(input(f,zone).value,'Asia/Shanghai');
  }finally{f.close();}
});
test('late update after boundary change cannot refresh or claim success in the new context',async()=>{
  const f=fixture();try{await load(f);mode(f,region,'clear');save(f,'game_link/core').click();const old=pending(f,'update');f.context({...context,session:'next'});old.resolve({module_id:'game_link/core',revision:9});await flush();assert.equal(f.calls.length,4);await load(f,catalog(),snapshot(2));assert.ok(!status(f).includes('已保存'));assert.equal(f.calls.filter(c=>c.endpoint==='admin/read').length,2);}finally{f.close();}
});
for(const entry of ['ready','onContext'])test(`${entry} missing/wrong plugin/page binding fails closed`,async()=>{
  for(const bad of [null,{}, {pluginName:context.pluginName},{pageName:'00-game-link'},{...context,pageName:'shell'},{...context,pageName:'management'},{...context,pluginName:'other'}]){
    const f=fixture(null,false);try{if(entry==='ready')f.ready.resolve(bad);else f.context(bad);await flush();assert.equal(f.calls.length,0);assert.equal(f.document.getElementById('refresh').disabled,true);}finally{f.close();}
  }
});
test('invalid binding event prevents late ready from resurrecting a management session',async()=>{
  const f=fixture(null);try{f.context({pluginName:context.pluginName});f.ready.resolve(context);await flush();assert.equal(f.calls.length,0);}finally{f.close();}
});
test('unrepresentable unknown lifecycle fields reject instead of collapsing the boundary',async()=>{
  for(const value of [undefined,()=>{},NaN,-0,new Date(),[,1]]){const f=fixture();try{f.context({...context,lifecycle:value});await flush();assert.equal(f.document.getElementById('refresh').disabled,true);assert.equal(f.calls.length,1);pending(f,'catalog').resolve(catalog());await flush();assert.equal(f.calls.length,1);}finally{f.close();}}
});
test('exact update/CAS body and explicit clear, then catalog/read refresh; no implicit recover',async()=>{
  const f=fixture();try{await load(f);mode(f,region,'clear');save(f,'game_link/core').click();assert.deepEqual(pending(f,'update').body,{module_id:'game_link/core',expected_revision:1,updates:[{field:region,mode:'clear'}]});
    pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.match(status(f),/已保存/);assert.equal(f.calls.filter(c=>c.endpoint==='admin/recover').length,0);assert.equal(field(f,region).querySelector('[data-role=mode]').value,'keep');
  }finally{f.close();}
});
test('invalid stored values are hidden and repair uses schema conversion with no fabricated value',async()=>{
  const f=fixture(),values=snapshot();values['game_link/core'].fields[days]={value:null,state:'invalid',present:true,source:'sqlite'};
  try{await load(f,catalog(),values);assert.equal(input(f,days).value,'');assert.match(field(f,days).textContent,/当前值无效/);mode(f,days,'replace');input(f,days).value='12';save(f,'game_link/core').click();assert.deepEqual(pending(f,'update').body.updates,[{field:days,mode:'replace',value:12}]);pending(f,'update').reject(Error('revision_conflict'));await flush();assert.match(status(f),/配置已变化/);assert.equal(input(f,days).value,'12');assert.equal(save(f,'game_link/core').disabled,true);}finally{f.close();}
});
test('client input failure posts nothing; server authorization/validation failures never show success',async()=>{
  const f=fixture();try{await load(f);mode(f,days,'replace');input(f,days).value='31';save(f,'game_link/core').click();await flush();assert.equal(f.calls.length,2);assert.match(status(f),/未通过校验/);
    input(f,days).value='10';save(f,'game_link/core').click();pending(f,'update').reject(Error('admin_authorization_denied'));await flush();assert.match(status(f),/管理授权不可用/);assert.equal(save(f,'game_link/core').disabled,true);
  }finally{f.close();}
});
test('missing semantic validator blocks replace/clear/recovery and preserves readable values',async()=>{
  const f=fixture(),declarations=catalog();Object.assign(declarations.fields.find(f=>f.name===zone),{editable:false,blocked_reason:'semantic_validator_unavailable'});
  try{await load(f,declarations);assert.equal(input(f,zone).value,'Asia/Shanghai');assert.equal(field(f,zone).querySelector('[data-role=mode]').disabled,true);assert.equal(f.document.getElementById('recover'),null);assert.match(field(f,zone).textContent,/当前只读/);assert.throws(()=>updateBody('game_link/core',1,{[zone]:{mode:'clear'}},validateCatalog(declarations)));assert.equal(f.calls.length,2);const m=fixture(context,true,{scope:'maintenance',beforeMaintenance:async()=>true});try{await load(m,declarations);assert.equal(m.document.getElementById('recover').disabled,true);}finally{m.close();}}finally{f.close();}
});
test('unsupported object schema is explicitly readonly and an added declaration needs no app edit',async()=>{
  const f=fixture(),declarations=catalog();declarations.fields.push({...declarations.fields[0],name:'new_number',description:'新增数字',value_schema:{type:'integer',minimum:0},default:2});
  declarations.fields[0].value_schema={type:'object',properties:{label:{type:'string'}},required:['label'],additionalProperties:false};declarations.fields[0].default={label:'example'};
  try{await load(f,declarations);assert.match(field(f,'sample').textContent,/无读取或修改权限|此类型暂不支持编辑/);assert.equal(input(f,'sample').disabled,true);assert.equal(input(f,'new_number').type,'number');assert.equal(input(f,'new_number').value,'');assert.equal(f.document.querySelector('section[data-owner="example/demo"]'),null);}finally{f.close();}
});
test('current-state recovery sends exact revision fences and only exact successful confirmations claim success',async()=>{
  const f=fixture(context,true,{scope:'maintenance',beforeMaintenance:async()=>true});try{await load(f);click(f,'recover');await flush();assert.deepEqual(pending(f,'recover').body,{expected_revisions:{'game_link/core':1,'game_link/core':1}});pending(f,'recover').resolve({recovered:true});await flush();await load(f,catalog(),snapshot(2));assert.match(status(f),/业务运行已恢复/);
  }finally{f.close();}
});
test('malformed update/recover/rollback and read envelopes never become successful',async()=>{
  for(const endpoint of ['update','recover']){const f=fixture(context,true,{scope:endpoint==='update'?'settings':'maintenance',beforeMaintenance:async()=>true});try{await load(f);if(endpoint==='update'){mode(f,region,'clear');save(f,'game_link/core').click();}else {click(f,endpoint);await flush();}pending(f,endpoint).resolve(endpoint==='update'?{module_id:'other/module',revision:2}:endpoint==='recover'?{recovered:false}:{rolled_back:true,extra:'PRIVATE'});await flush();assert.match(status(f),/未取得成功确认/);assert.equal(f.calls.length,3);}finally{f.close();}}
  const f=fixture();try{pending(f,'catalog').resolve(catalog());await flush();pending(f,'read').resolve({status:'ok',data:snapshot()});await flush();assert.match(status(f),/未通过校验/);assert.equal(f.document.querySelectorAll('.config-field').length,0);}finally{f.close();}
});
test('a newer draft during save survives response and refresh',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');input(f,zone).value='UTC';save(f,'game_link/core').click();input(f,zone).value='Europe/London';pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.equal(input(f,zone).value,'Europe/London');assert.equal(field(f,zone).querySelector('[data-role=mode]').value,'replace');}finally{f.close();}
});
test('closing fences pending responses and removes the context listener',async()=>{
  const f=fixture();try{const old=pending(f,'catalog');f.app.close();old.resolve(catalog());f.context(context);await flush();assert.equal(f.calls.length,1);assert.equal(f.document.getElementById('refresh').disabled,true);}finally{f.close();}
});
test('newer input event returning to the sent value remains a draft after old save confirmation',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='UTC';save(f,'game_link/core').click();node.value='Europe/London';node.dispatchEvent(new f.dom.window.Event('input'));node.value='UTC';node.dispatchEvent(new f.dom.window.Event('input'));pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.equal(node.value,'UTC');assert.equal(field(f,zone).querySelector('[data-role=mode]').value,'replace');}finally{f.close();}
});
test('focused refresh retains its node and focus while aria-disabled prevents duplicate activation',async()=>{
  const f=fixture();try{await load(f);const button=f.document.getElementById('refresh');button.focus();button.click();await flush();assert.equal(f.document.activeElement,button);assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.disabled,false);button.click();await flush();assert.equal(f.calls.length,3);await load(f,catalog(),snapshot(2));assert.equal(f.document.activeElement,button);assert.equal(button.getAttribute('aria-disabled'),'false');assert.equal(f.document.getElementById('refresh'),button);}finally{f.close();}
});
test('blocked writes and recover cannot be posted by forced events after refresh failure or validator loss',async()=>{
  const f=fixture();try{await load(f);mode(f,region,'clear');const button=save(f,'game_link/core');button.focus();click(f,'refresh');pending(f,'catalog').reject(Error('operation_unavailable'));await flush();assert.equal(button.getAttribute('aria-disabled'),'true');button.dispatchEvent(new f.dom.window.Event('click'));assert.equal(f.document.getElementById('recover'),null);await flush();assert.equal(f.calls.length,3);
    click(f,'refresh');const declarations=catalog();Object.assign(declarations.fields.find(f=>f.name===zone),{editable:false,blocked_reason:'semantic_validator_unavailable'});await load(f,declarations);assert.equal(f.document.getElementById('recover'),null);await flush();assert.equal(f.calls.length,5);
  }finally{f.close();}
});
test('invalid catalog fails before any value read and missing schema stays visibly readonly',async()=>{
  const f=fixture();try{pending(f,'catalog').resolve({schema_version:1,fields:[],extra:true});await flush();assert.equal(f.calls.length,1);assert.equal(f.document.querySelectorAll('.config-field').length,0);click(f,'refresh');const declarations=catalog();declarations.fields[0].value_schema=null;await load(f,declarations);assert.equal(input(f,'sample').disabled,true);assert.match(field(f,'sample').textContent,/无读取或修改权限|此类型暂不支持编辑/);}finally{f.close();}
});
test('ungranted metadata beside an authorized field is never read or submitted and does not break save confirmation',async()=>{
  const f=fixture(),declarations=catalog();declarations.fields.push({...declarations.fields[0],module_id:'game_link/core',name:'extra_declared'});
  try{await load(f,declarations);assert.equal(input(f,'extra_declared').value,'');assert.equal(input(f,'extra_declared').disabled,true);mode(f,region,'clear');save(f,'game_link/core').click();assert.deepEqual(pending(f,'update').body.updates,[{field:region,mode:'clear'}]);pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,declarations,snapshot(2));assert.match(status(f),/已保存/);assert.equal(input(f,'extra_declared').value,'');}finally{f.close();}
});

test('embedded shell management keeps exact context and scoped container; resume clears pending status',async()=>{
 const dom=new JSDOM('<div id="management-root">outside</div><div id="embedded"></div>',{pretendToBeVisual:true}),calls=[];const binding={...context,pageName:'00-game-link'};
 const bridge={onContext(fn){fn(binding);return()=>{};},ready:()=>Promise.resolve(binding),apiPost(endpoint){calls.push(endpoint);return Promise.resolve(endpoint==='admin/catalog'?catalog():endpoint==='admin/read'?snapshot():endpoint==='admin/master-key-status'?masterKeyReady():endpoint==='admin/credential-catalog'?credentialCatalog():endpoint==='admin/credential-status'?credentialStatus():{ready:false,state:'unavailable',reason_code:'secret_encryption_unavailable',diagnostics:{key_state:'not_provided',codec_state:'not_run'}});}};
 const container=dom.window.document.getElementById('embedded'),page=createManagementPage(dom.window.document,bridge,dom.window,{expectedPage:'00-game-link',container});page.start();await flush();try{assert.equal(dom.window.document.getElementById('management-root').textContent,'outside');assert.ok(container.querySelector('[data-owner="game_link/core"]'));page.selectOwner('game_link/core');await flush();assert.equal(container.querySelector('#fields [data-owner="game_link/core"]').hidden,false);page.suspend();page.resume();await flush();assert.doesNotMatch(container.querySelector('#status').textContent,/正在执行/);assert.equal(container.querySelector('#status').textContent,'');assert.equal(calls.filter(x=>x==='admin/catalog').length,2);}finally{page.dispose();dom.window.close();}
});

// Host 4.28.2 PluginPagePage forwards response.data.message for HTTP errors;
// plugin_page_bridge.js then rejects Error(message.error), without code/status.
test('Host bridge conflict text preserves the Core draft and requires a fresh revision before retry',async()=>{
  const f=fixture();try{
    await load(f,catalog(),snapshot(8));mode(f,region,'replace');const node=input(f,region);node.value='0';save(f,'game_link/core').click();
    const rejected=pending(f,'update');assert.deepEqual(rejected.body,{module_id:'game_link/core',expected_revision:8,updates:[{field:region,mode:'replace',value:'cn'}]});
    const error=Error('配置版本冲突，请刷新并重新核对后修改。');assert.equal(error.code,undefined);assert.equal(error.response,undefined);rejected.reject(error);await flush();
    assert.match(status(f),/配置已变化/);assert.equal(input(f,region),node);assert.equal(node.value,'0');assert.equal(field(f,region).querySelector('[data-role=mode]').value,'replace');assert.equal(f.calls.find(c=>c.endpoint==='admin/update').body.expected_revision,8);assert.equal(save(f,'game_link/core').getAttribute('aria-disabled'),'true');assert.equal(f.calls.length,3);
    save(f,'game_link/core').dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.length,3);
    click(f,'refresh');const latest=snapshot(9);latest['game_link/core'].fields.default_region.value='global';await load(f,catalog(),latest);assert.equal(input(f,region),node);assert.equal(node.value,'0');assert.equal(save(f,'game_link/core').getAttribute('aria-disabled'),'false');
    save(f,'game_link/core').click();assert.equal(pending(f,'update').body.expected_revision,9);pending(f,'update').resolve({module_id:'game_link/core',revision:10});await flush();await load(f,catalog(),snapshot(10));assert.match(status(f),/已保存/);assert.equal(field(f,region).querySelector('[data-role=mode]').value,'keep');
  }finally{f.close();}
});
test('unknown bridge errors and decorated conflict text stay redacted and gate Core writes',async()=>{
  for(const error of [Error('Request failed with status code 409'),Error('PRIVATE 配置版本冲突，请刷新并重新核对后修改。'),Error('配置版本冲突，请刷新并重新核对后修改。 PRIVATE'),{message:'PRIVATE',code:'revision_conflict'},{response:{status:409,data:{code:'revision_conflict',message:'PRIVATE'}}}]){
    const f=fixture();try{await load(f,catalog(),snapshot(8));mode(f,region,'replace');const node=input(f,region);node.value='0';save(f,'game_link/core').click();pending(f,'update').reject(error);await flush();assert.match(status(f),/未取得成功确认/);assert.doesNotMatch(status(f),/PRIVATE|配置已变化|配置已保存/);assert.equal(input(f,region),node);assert.equal(node.value,'0');assert.equal(save(f,'game_link/core').getAttribute('aria-disabled'),'true');assert.equal(f.calls.length,3);}finally{f.close();}
  }
});


test('neutral owner groups form separate labeled fieldsets and share one exact CAS save',async()=>{
 const f=fixture(),declarations=catalog(),prototype=declarations.fields.find(field=>field.name==='sample');
 const a={...prototype,module_id:'game_link/core',name:'frequency',description:'同步频率',group:'alpha',readable:true,editable:true,blocked_reason:null},b={...a,name:'appearance',description:'展示方式',group:'beta'};
 declarations.fields=[b,a];
 const values={};values['game_link/core']={revision:7,fields:{frequency:{value:'daily',state:'valid',present:true,source:'sqlite'},appearance:{value:'compact',state:'valid',present:true,source:'sqlite'}}};
 try{await load(f,declarations,values);const card=f.document.querySelector('section.card[data-owner="game_link/core"]'),groups=[...card.querySelectorAll('fieldset.config-group')];
  assert.equal(groups.length,2);assert.deepEqual(groups.map(group=>group.dataset.group),['beta','alpha']);assert.deepEqual(groups.map(group=>group.querySelector('legend').textContent),['beta','alpha']);assert.ok(groups.every(group=>group.querySelector('legend')?.textContent));assert.deepEqual(groups.map(group=>[...group.querySelectorAll('[data-field]')].map(field=>field.dataset.field)),[['appearance'],['frequency']]);
  for(const [name,value] of [['frequency','weekly'],['appearance','expanded']]){const node=input(f,name);node.value=value;node.dispatchEvent(new f.dom.window.Event('input'));}
  assert.equal(card.querySelectorAll('[data-action=save]').length,1);save(f,'game_link/core').click();assert.equal(f.calls.filter(call=>call.endpoint==='admin/update').length,1);assert.deepEqual(pending(f,'update').body,{module_id:'game_link/core',expected_revision:7,updates:[{field:'appearance',mode:'replace',value:'expanded'},{field:'frequency',mode:'replace',value:'weekly'}]});
 }finally{f.close();}
});

// Success expires on the controlled clock; failures have no dismissal timer.
for(const action of ['recover'])for(const succeeds of [true,false])test(`${action} ${succeeds?'success expires after 3500ms':'failure remains an error without auto-dismiss'}`,async()=>{
 const f=fixture(context,true,{scope:'maintenance',beforeMaintenance:async()=>true}),timers=new Map();let timerId=0;
 f.dom.window.setTimeout=(callback,delay)=>{const id=++timerId;timers.set(id,{callback,delay});return id;};f.dom.window.clearTimeout=id=>timers.delete(id);
 try{
  await load(f);click(f,action);await flush();const call=pending(f,action);
  if(succeeds){call.resolve(action==='recover'?{recovered:true}:{rolled_back:true});await flush();await load(f,catalog(),snapshot(2));}
  else{call.reject(Error('operation_unavailable'));await flush();}
  const node=f.document.getElementById('status'),message=node.textContent;
  assert.equal(f.calls.filter(c=>c.endpoint==='admin/'+action).length,1);
  assert.equal(node.dataset.tone,succeeds?'success':'error');assert.ok(message.length>0);
  if(succeeds){
   assert.match(message,action==='recover'?/业务运行已恢复/:/限定回退/);
   assert.equal(timers.size,1);const [id,timer]=[...timers][0];assert.equal(timer.delay,3500);
   assert.equal(node.textContent,message,'success must remain before its deadline');timers.delete(id);timer.callback();assert.equal(node.textContent,'');
  }else{
   assert.match(message,/未取得成功确认/);assert.equal(timers.size,0,'failure must cancel any earlier success dismissal and schedule none');
   for(const timer of timers.values())timer.callback();assert.equal(node.textContent,message);assert.equal(node.dataset.tone,'error');
  }
 }finally{f.close();}
});
