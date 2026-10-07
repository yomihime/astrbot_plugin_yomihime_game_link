// Private preview controller: synthetic data in memory; no browser storage/JWT.
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from './fixtures.mjs';
const clone=value=>JSON.parse(JSON.stringify(value));
let declarations=catalog(),values=snapshot(),binding={...context},listener=null;
let forms=credentialCatalog(),credentialStates=credentialStatus();
const held=new Set(),waiting=[],failures=new Map(),calls=[];
const normalized=endpoint=>endpoint.startsWith('admin/')?endpoint:'admin/'+endpoint;
function response(endpoint,body){
  if(endpoint==='admin/catalog')return clone(declarations);
  if(endpoint==='admin/read')return clone(values);
  if(endpoint==='admin/credential-catalog')return clone(forms);
  if(endpoint==='admin/credential-status'){credentialStates['ff14/ff14'].revision=values['ff14/ff14'].revision;return clone(credentialStates);}
  if(endpoint==='admin/credential-update'){
    const row=values[body.module_id],state=credentialStates[body.module_id];if(!row||!state||row.revision!==body.expected_revision)throw Error('revision_conflict');
    for(const update of body.updates){if(!forms.fields.some(f=>f.module_id===body.module_id&&f.name===update.field))throw Error('admin_authorization_denied');state.fields[update.field]=update.mode==='clear'?'unset':'configured';}
    row.revision++;state.revision=row.revision;return {module_id:body.module_id,revision:row.revision};
  }
  if(endpoint==='admin/update'){
    const row=values[body.module_id];if(!row||row.revision!==body.expected_revision)throw Error('revision_conflict');
    for(const update of body.updates){const field=declarations.fields.find(f=>f.module_id===body.module_id&&f.name===update.field);if(!field?.editable)throw Error('admin_authorization_denied');row.fields[update.field]={value:update.mode==='clear'?clone(field.default):clone(update.value),state:'valid',present:update.mode==='replace',source:update.mode==='replace'?'sqlite':'default'};}
    row.revision++;return {module_id:body.module_id,revision:row.revision};
  }
  if(endpoint==='admin/recover')return {recovered:true};
  if(endpoint==='admin/rollback')return {rolled_back:true};
  throw Error('operation_unavailable');
}
window.AstrBotPluginPage={ready:()=>Promise.resolve(clone(binding)),onContext(fn){listener=fn;return()=>{listener=null;};},apiPost(endpoint,body){
  // Do not retain input pairs in the inspectable mock history.
  calls.push({endpoint,body:endpoint==='admin/credential-update'?{module_id:body.module_id,expected_revision:body.expected_revision,updates:body.updates.map(u=>({field:u.field,mode:u.mode}))}:clone(body)});let value,error;
  try{const failure=failures.get(endpoint);if(failure){failures.delete(endpoint);throw Error(failure);}value=response(endpoint,body);}catch(e){error=e;}
  const reply=()=>error?Promise.reject(error):Promise.resolve(value);
  if(!held.has(endpoint))return reply();return new Promise((resolve,reject)=>waiting.push({endpoint,resolve,reject,value,error}));
}};
window.mockManagementControl=Object.freeze({kind:'private mock management; not Host authorization',calls,
  context(change){binding={...binding,...change};listener?.(clone(binding));},replaceContext(value){binding=value;listener?.(value);},
  setCatalog(value){declarations=clone(value);},setSnapshot(value){values=clone(value);},
  setCredentialCatalog(value){forms=clone(value);},setCredentialStatus(value){credentialStates=clone(value);},
  hold(endpoint){held.add(normalized(endpoint));},release(endpoint){const name=normalized(endpoint);held.delete(name);for(let i=waiting.length-1;i>=0;i--)if(waiting[i].endpoint===name){const item=waiting.splice(i,1)[0];item.error?item.reject(item.error):item.resolve(item.value);}},
  failNext(endpoint,code='operation_unavailable'){failures.set(normalized(endpoint),code);},
});
document.body.insertAdjacentHTML('afterbegin','<p class="mock-banner" role="note">隔离 Mock 管理预览 · 内存数据 · 无真实宿主授权或写入</p>');
await import('../../../pages/management/app.js');
