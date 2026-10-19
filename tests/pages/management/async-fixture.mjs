// Isolated in-memory bridge for production-page race tests. Synthetic values only.
import {themeCatalog,themeSnapshot} from '../shell/theme-settings-fixtures.mjs';
import {catalog as shellCatalog} from '../shell/fixtures.mjs';
import {credentialCatalog,credentialStatus,masterKeyReady} from './fixtures.mjs';
export function installAsyncFixture(window,pageName='management'){
 const clone=value=>JSON.parse(JSON.stringify(value)),calls=[],held=new Set(),pending=[],failures=new Map(),handlers=new Set();
 let context={pluginName:'astrbot_plugin_yomihime_game_link',pageName,session:'async-one'};
 const declarations=themeCatalog(),values=themeSnapshot(),registry=shellCatalog();registry.modules.push(...shellCatalog('example/demo').modules);
 const forms=credentialCatalog(),states=credentialStatus(7);
 forms.fields.push({...clone(forms.fields[0]),module_id:'example/demo'});states['example/demo']={revision:7,fields:{credential_fflogs_cn:'unset'}};
 const font={revision:0,state:'ready',source:'canonical',error:null,filename:'NotoSansCJKsc-Regular.otf',size:16437364,sha256:'a'.repeat(64),download_url:'https://example.invalid/not-clickable.otf'};
 function response(endpoint,body){
  if(endpoint==='admin/update'){
   const row=values[body.module_id];if(row.revision!==body.expected_revision)throw Error('revision_conflict');
   for(const update of body.updates){const field=declarations.fields.find(f=>f.module_id===body.module_id&&f.name===update.field);if(!field?.editable)throw Error('admin_authorization_denied');row.fields[update.field]={value:update.mode==='clear'?field.default:update.value,state:'valid',present:update.mode==='replace',source:update.mode==='replace'?'sqlite':'default'};}
   row.revision++;if(states[body.module_id])states[body.module_id].revision=row.revision;return {module_id:body.module_id,revision:row.revision};
  }
  const responses={'admin/catalog':declarations,'admin/read':values,'admin/master-key-status':masterKeyReady(),'admin/credential-catalog':forms,'admin/credential-status':states,'admin/credential-readiness':{ready:true,state:'ready',reason_code:'ready',diagnostics:{key_state:'valid_format',codec_state:'available'}},'admin/font-status':font,'admin/font-retry':font,'admin/modules':{registry_revision:5,modules:registry.modules.map(m=>({module_id:m.module_id,enabled:true,lifecycle:'active',health:'ready',epoch:1,registry_revision:5,reason_code:null}))}};
  if(!(endpoint in responses))throw Error('unexpected_fixture_endpoint');return responses[endpoint];
 }
 const bridge={onContext(fn){handlers.add(fn);return()=>handlers.delete(fn);},ready:()=>Promise.resolve(clone(context)),apiGet:()=>Promise.resolve(clone(registry)),apiPost(endpoint,body){
  // Never keep credential pairs in the inspectable call history.
  calls.push({endpoint,body:endpoint==='admin/credential-update'?{module_id:body.module_id}:clone(body)});
  let result,error;try{if(failures.has(endpoint)){const code=failures.get(endpoint);failures.delete(endpoint);throw Error(code);}result=clone(response(endpoint,body));}catch(e){error=e;}
  if(!held.has(endpoint))return error?Promise.reject(error):Promise.resolve(result);
  return new Promise((resolve,reject)=>pending.push({endpoint,resolve,reject,result,error}));
 }};
 window.AstrBotPluginView=bridge;
 const control={calls,values,hold(endpoint){held.add(endpoint);},failNext(endpoint,code){failures.set(endpoint,code);},release(endpoint,code){held.delete(endpoint);for(const item of pending.filter(item=>item.endpoint===endpoint)){pending.splice(pending.indexOf(item),1);if(code||item.error)item.reject(code?Error(code):item.error);else item.resolve(item.result);}},context(change){context={...context,...change};for(const fn of handlers)fn(clone(context));},pending:()=>pending.map(item=>item.endpoint)};
 window.asyncFixture=control;return control;
}
