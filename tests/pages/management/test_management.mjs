// Offline JSDOM/bridge contracts. Not Host/browser acceptance.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const runtimeDOM=new JSDOM('<html><body></body></html>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=runtimeDOM.window[name];
const {createManagementPage,validateCatalog,validateSnapshot,updateBody}=await import('../../../pages/management/app.js');
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from './fixtures.mjs';
const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
const flush=async()=>{for(let i=0;i<35;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture(initial=context,resolveReady=true){
  const dom=new JSDOM(html,{pretendToBeVisual:true,url:'http://localhost/management/'}),calls=[],ready=deferred();let callback=null;
  // Ordinary request counts remain scoped to ordinary endpoints;
  // independent credential tests capture their own requests and assertions.
  const bridge={onContext(fn){callback=fn;if(initial)fn(initial);return()=>{callback=null;};},ready:()=>ready.promise,apiPost(endpoint,body){if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());const d=deferred(),call={endpoint,body,settled:false,promise:d.promise,resolve(value){this.settled=true;d.resolve(value);},reject(value){this.settled=true;d.reject(value);}};calls.push(call);return d.promise;}};
  const app=createManagementPage(dom.window.document,bridge,dom.window);app.start();if(resolveReady)ready.resolve(initial||context);
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

test('production status and actual Naive content node explicitly override their own color transitions',async()=>{
  const f=fixture();try{
    await load(f);const node=f.document.getElementById('status'),content=node.closest('.n-alert-body__content'),alert=node.closest('.n-alert');assert.ok(content);assert.ok(alert);
    const stylesheet=f.document.createElement('style');stylesheet.textContent=await readFile(new URL('../../../pages/management/styles.css',import.meta.url),'utf8');f.document.head.append(stylesheet);
    const rules=[...stylesheet.sheet.cssRules],matches=(element,property)=>rules.flatMap(rule=>rule.selectorText?.split(',').filter(selector=>element.matches(selector)).map(selector=>({selector,value:rule.style.getPropertyValue(property)}))||[]).filter(rule=>rule.value);
    const foreground=matches(content,'color').find(rule=>rule.value==='var(--text)'),transition=matches(content,'transition').find(rule=>rule.value==='none'),background=matches(alert,'background-color').find(rule=>rule.value==='var(--surface)');assert.ok(foreground);assert.ok(transition);assert.ok(background);
    // The paragraph must have an explicit declaration; parent inheritance alone
    // does not override Naive's content-level color and 0.3s transition.
    assert.ok(matches(node,'color').some(rule=>rule.selector.includes('#status')&&rule.value==='var(--text)'));assert.ok(matches(node,'transition').some(rule=>rule.selector.includes('#status')&&rule.value==='none'));
    const classes=selector=>(selector.match(/\.[\w-]+/g)||[]).length;
    const native=runtimeDOM.window.document.querySelector('style[cssr-id="n-alert"]');assert.ok(native);let nativeContentTransitions=0,nativeContentColors=0;
    for(const rule of native.sheet.cssRules)for(const selector of rule.selectorText?.split(',')||[])if(content.matches(selector)){
      if(rule.style.getPropertyValue('color')){nativeContentColors++;assert.ok(classes(foreground.selector)>classes(selector));}
      if(rule.style.getPropertyValue('transition').includes('color')){nativeContentTransitions++;assert.ok(classes(transition.selector)>classes(selector));}
    }assert.ok(nativeContentTransitions>0);assert.ok(nativeContentColors>0);
    for(const isDark of [true,false]){f.context({...context,isDark});await flush();assert.equal(f.document.getElementById('status'),node);assert.equal(node.closest('.n-alert-body__content'),content);assert.equal(f.document.documentElement.dataset.theme,isDark?'dark':'light');assert.equal(node.getAttribute('aria-live'),'polite');assert.equal(node.getAttribute('role'),'status');}
  }finally{f.close();}
});

test('synchronous onContext/ready issue one catalog, then one read; tiny metadata needs no root branch',async()=>{
  const f=fixture();try{await flush();assert.equal(f.calls.length,1);assert.equal(f.calls[0].endpoint,'admin/catalog');await load(f);
    assert.equal(f.document.querySelectorAll('#fields > section').length,3);assert.match(field(f,'sample').textContent,/无存量读取与修改授权/);assert.equal(input(f,'sample').value,'');assert.equal(input(f,'sample').disabled,true);assert.equal(save(f,'example/demo').disabled,true);
    assert.equal(input(f,region).tagName,'SELECT');assert.equal(input(f,days).type,'number');assert.equal(input(f,zone).type,'text');
    f.context({...context});f.context({...context,theme:'dark',locale:'en-US'});await flush();assert.equal(f.calls.length,2);assert.equal(f.document.documentElement.dataset.theme,'dark');
  }finally{f.close();}
});
test('same/presentation context preserves dirty input, focus, selection and pending read without extra requests',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='Europe/London';node.focus();node.setSelectionRange(2,8);f.document.documentElement.scrollTop=91;
    f.context({...context,isDark:true,locale:'en-US',i18n:{label:'x'}});await flush();assert.equal(input(f,zone),node);assert.equal(node.value,'Europe/London');assert.equal(f.document.activeElement,node);assert.equal(node.selectionStart,2);assert.equal(f.calls.length,2);
    click(f,'refresh');await flush();assert.equal(input(f,zone),node);assert.equal(f.document.activeElement,node);assert.equal(save(f,'ff14/ff14').disabled,true);
    pending(f,'catalog').resolve(catalog());await flush();f.context({...context,isDark:false});assert.equal(f.calls.length,4);pending(f,'read').resolve(snapshot(2));await flush();
    assert.equal(input(f,zone),node);assert.equal(node.value,'Europe/London');assert.equal(node.selectionEnd,8);assert.equal(f.document.documentElement.scrollTop,91);assert.equal(save(f,'ff14/ff14').disabled,false);
  }finally{f.close();}
});
test('presentation during initial catalog/read retains the in-flight request',async()=>{
  const f=fixture();try{f.context({...context,locale:'en-US'});await flush();assert.equal(f.calls.length,1);pending(f,'catalog').resolve(catalog());await flush();f.context({...context,theme:'dark'});pending(f,'read').resolve(snapshot());await flush();assert.equal(f.calls.length,2);assert.ok(input(f,region));}finally{f.close();}
});
test('refresh failure retains drafts and nodes, gates writes until explicit recovery',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='UTC';node.focus();click(f,'refresh');pending(f,'catalog').reject(Error('PRIVATE backend error'));await flush();
    assert.equal(input(f,zone),node);assert.equal(node.value,'UTC');assert.equal(f.document.activeElement,node);assert.equal(save(f,'ff14/ff14').disabled,true);assert.match(status(f),/未取得成功确认/);assert.ok(!status(f).includes('PRIVATE'));
    click(f,'refresh');await load(f,catalog(),snapshot(2));assert.equal(node.value,'UTC');assert.equal(save(f,'ff14/ff14').disabled,false);
  }finally{f.close();}
});
test('real unknown boundary change fences late read and clears drafts across identities',async()=>{
  const f=fixture();try{pending(f,'catalog').resolve(catalog());await flush();const old=pending(f,'read');f.context({...context,session:'mock-two',instance:{revision:2}});await flush();assert.equal(f.calls.length,3);old.resolve(snapshot(99));await flush();assert.equal(f.document.querySelectorAll('.config-field').length,0);
    await load(f,catalog(),snapshot(2));assert.match(f.document.querySelector('section[data-owner="game_link/core"]').textContent,/配置版本：2/);assert.ok(!f.document.querySelector('#fields').textContent.includes('99'));
    mode(f,zone,'replace');input(f,zone).value='DIRTY-OLD';f.context({...context,session:'mock-three'});await load(f,catalog(),snapshot(3));assert.equal(input(f,zone).value,'Asia/Shanghai');
  }finally{f.close();}
});
test('late update after boundary change cannot refresh or claim success in the new context',async()=>{
  const f=fixture();try{await load(f);mode(f,region,'clear');save(f,'game_link/core').click();const old=pending(f,'update');f.context({...context,session:'next'});old.resolve({module_id:'game_link/core',revision:9});await flush();assert.equal(f.calls.length,4);await load(f,catalog(),snapshot(2));assert.ok(!status(f).includes('已保存'));assert.equal(f.calls.filter(c=>c.endpoint==='admin/read').length,2);}finally{f.close();}
});
for(const entry of ['ready','onContext'])test(`${entry} missing/wrong plugin/page binding fails closed`,async()=>{
  for(const bad of [null,{}, {pluginName:context.pluginName},{pageName:'management'},{...context,pageName:'shell'},{...context,pluginName:'other'}]){
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
    pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.match(status(f),/配置已保存/);assert.equal(f.calls.filter(c=>c.endpoint==='admin/recover').length,0);assert.equal(field(f,region).querySelector('[data-role=mode]').value,'keep');
  }finally{f.close();}
});
test('invalid stored values are hidden and repair uses schema conversion with no fabricated value',async()=>{
  const f=fixture(),values=snapshot();values['ff14/ff14'].fields[days]={value:null,state:'invalid',present:true,source:'sqlite'};
  try{await load(f,catalog(),values);assert.equal(input(f,days).value,'');assert.match(field(f,days).textContent,/无效存量/);mode(f,days,'replace');input(f,days).value='12';save(f,'ff14/ff14').click();assert.deepEqual(pending(f,'update').body.updates,[{field:days,mode:'replace',value:12}]);pending(f,'update').reject(Error('revision_conflict'));await flush();assert.match(status(f),/配置已变化/);assert.equal(input(f,days).value,'12');assert.equal(save(f,'ff14/ff14').disabled,true);}finally{f.close();}
});
test('client input failure posts nothing; server authorization/validation failures never show success',async()=>{
  const f=fixture();try{await load(f);mode(f,days,'replace');input(f,days).value='31';save(f,'ff14/ff14').click();await flush();assert.equal(f.calls.length,2);assert.match(status(f),/未通过校验/);
    input(f,days).value='10';save(f,'ff14/ff14').click();pending(f,'update').reject(Error('admin_authorization_denied'));await flush();assert.match(status(f),/管理授权不可用/);assert.equal(save(f,'ff14/ff14').disabled,true);
  }finally{f.close();}
});
test('missing semantic validator blocks replace/clear/recovery and preserves readable values',async()=>{
  const f=fixture(),declarations=catalog();Object.assign(declarations.fields.find(f=>f.name===zone),{editable:false,blocked_reason:'semantic_validator_unavailable'});
  try{await load(f,declarations);assert.equal(input(f,zone).value,'Asia/Shanghai');assert.equal(field(f,zone).querySelector('[data-role=mode]').disabled,true);assert.equal(f.document.getElementById('recover').disabled,true);assert.match(field(f,zone).textContent,/语义校验器不可用/);assert.throws(()=>updateBody('ff14/ff14',1,{[zone]:{mode:'clear'}},validateCatalog(declarations)));assert.equal(f.calls.length,2);}finally{f.close();}
});
test('unsupported object schema is explicitly readonly and an added declaration needs no app edit',async()=>{
  const f=fixture(),declarations=catalog();declarations.fields.push({...declarations.fields[0],name:'new_number',description:'新增数字',value_schema:{type:'integer',minimum:0},default:2});
  declarations.fields[0].value_schema={type:'object',properties:{label:{type:'string'}},required:['label'],additionalProperties:false};declarations.fields[0].default={label:'example'};
  try{await load(f,declarations);assert.match(field(f,'sample').textContent,/schema 暂不支持编辑/);assert.equal(input(f,'sample').disabled,true);assert.equal(input(f,'new_number').type,'number');assert.equal(input(f,'new_number').value,'');assert.equal(save(f,'example/demo').disabled,true);}finally{f.close();}
});
test('recover/rollback retain old exact bodies and only exact successful confirmations claim success',async()=>{
  const f=fixture();try{await load(f);f.document.getElementById('replacement').checked=true;click(f,'recover');assert.deepEqual(pending(f,'recover').body,{expected_revisions:{'game_link/core':1,'ff14/ff14':1},complete_from_current:true});pending(f,'recover').resolve({recovered:true});await flush();await load(f,catalog(),snapshot(2));assert.match(status(f),/业务运行已恢复/);
    click(f,'rollback');assert.deepEqual(pending(f,'rollback').body,{expected_revisions:{'game_link/core':2,'ff14/ff14':2}});pending(f,'rollback').resolve({rolled_back:true});await flush();await load(f,catalog(),snapshot(3));assert.match(status(f),/限定回退/);
  }finally{f.close();}
});
test('malformed update/recover/rollback and read envelopes never become successful',async()=>{
  for(const endpoint of ['update','recover','rollback']){const f=fixture();try{await load(f);if(endpoint==='update'){mode(f,region,'clear');save(f,'game_link/core').click();}else click(f,endpoint);pending(f,endpoint).resolve(endpoint==='update'?{module_id:'other/module',revision:2}:endpoint==='recover'?{recovered:false}:{rolled_back:true,extra:'PRIVATE'});await flush();assert.match(status(f),/未取得成功确认/);assert.equal(f.calls.length,3);}finally{f.close();}}
  const f=fixture();try{pending(f,'catalog').resolve(catalog());await flush();pending(f,'read').resolve({status:'ok',data:snapshot()});await flush();assert.match(status(f),/未通过校验/);assert.equal(f.document.querySelectorAll('.config-field').length,0);}finally{f.close();}
});
test('a newer draft during save survives response and refresh',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');input(f,zone).value='UTC';save(f,'ff14/ff14').click();input(f,zone).value='Europe/London';pending(f,'update').resolve({module_id:'ff14/ff14',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.equal(input(f,zone).value,'Europe/London');assert.equal(field(f,zone).querySelector('[data-role=mode]').value,'replace');}finally{f.close();}
});
test('closing fences pending responses and removes the context listener',async()=>{
  const f=fixture();try{const old=pending(f,'catalog');f.app.close();old.resolve(catalog());f.context(context);await flush();assert.equal(f.calls.length,1);assert.equal(f.document.getElementById('refresh').disabled,true);}finally{f.close();}
});
test('newer input event returning to the sent value remains a draft after old save confirmation',async()=>{
  const f=fixture();try{await load(f);mode(f,zone,'replace');const node=input(f,zone);node.value='UTC';save(f,'ff14/ff14').click();node.value='Europe/London';node.dispatchEvent(new f.dom.window.Event('input'));node.value='UTC';node.dispatchEvent(new f.dom.window.Event('input'));pending(f,'update').resolve({module_id:'ff14/ff14',revision:2});await flush();await load(f,catalog(),snapshot(2));assert.equal(node.value,'UTC');assert.equal(field(f,zone).querySelector('[data-role=mode]').value,'replace');}finally{f.close();}
});
test('focused refresh retains its node and focus while aria-disabled prevents duplicate activation',async()=>{
  const f=fixture();try{await load(f);const button=f.document.getElementById('refresh');button.focus();button.click();await flush();assert.equal(f.document.activeElement,button);assert.equal(button.getAttribute('aria-disabled'),'true');assert.equal(button.disabled,false);button.click();await flush();assert.equal(f.calls.length,3);await load(f,catalog(),snapshot(2));assert.equal(f.document.activeElement,button);assert.equal(button.getAttribute('aria-disabled'),'false');assert.equal(f.document.getElementById('refresh'),button);}finally{f.close();}
});
test('blocked writes and recover cannot be posted by forced events after refresh failure or validator loss',async()=>{
  const f=fixture();try{await load(f);mode(f,region,'clear');const button=save(f,'game_link/core');button.focus();click(f,'refresh');pending(f,'catalog').reject(Error('operation_unavailable'));await flush();assert.equal(button.getAttribute('aria-disabled'),'true');button.dispatchEvent(new f.dom.window.Event('click'));f.document.getElementById('recover').dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.length,3);
    click(f,'refresh');const declarations=catalog();Object.assign(declarations.fields.find(f=>f.name===zone),{editable:false,blocked_reason:'semantic_validator_unavailable'});await load(f,declarations);f.document.getElementById('recover').dispatchEvent(new f.dom.window.Event('click'));await flush();assert.equal(f.calls.length,5);
  }finally{f.close();}
});
test('invalid catalog fails before any value read and missing schema stays visibly readonly',async()=>{
  const f=fixture();try{pending(f,'catalog').resolve({schema_version:1,fields:[],extra:true});await flush();assert.equal(f.calls.length,1);assert.equal(f.document.querySelectorAll('.config-field').length,0);click(f,'refresh');const declarations=catalog();declarations.fields[0].value_schema=null;await load(f,declarations);assert.equal(input(f,'sample').disabled,true);assert.match(field(f,'sample').textContent,/schema 暂不支持编辑/);}finally{f.close();}
});
test('ungranted metadata beside an authorized field is never read or submitted and does not break save confirmation',async()=>{
  const f=fixture(),declarations=catalog();declarations.fields.push({...declarations.fields[0],module_id:'game_link/core',name:'extra_declared'});
  try{await load(f,declarations);assert.equal(input(f,'extra_declared').value,'');assert.equal(input(f,'extra_declared').disabled,true);mode(f,region,'clear');save(f,'game_link/core').click();assert.deepEqual(pending(f,'update').body.updates,[{field:region,mode:'clear'}]);pending(f,'update').resolve({module_id:'game_link/core',revision:2});await flush();await load(f,declarations,snapshot(2));assert.match(status(f),/配置已保存/);assert.equal(input(f,'extra_declared').value,'');}finally{f.close();}
});
