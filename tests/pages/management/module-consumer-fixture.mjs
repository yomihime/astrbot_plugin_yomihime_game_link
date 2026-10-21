import {sourceModule,root} from '../shell/compile.mjs';
export const {createSettingsController}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
const {createModuleServices}=await sourceModule(root+'pages/frontend/src/module-services.ts');
export const flush=async()=>{for(let i=0;i<60;i++)await Promise.resolve();};
let serial=0;
export async function consumer({owner='ff14/ff14',failure='',error=null,configured=true,decryptable=true,hold='',context:patch={}}={}){
 const callbacks=[],calls=[],pending=[];let active=true,proof=0;
 const context={owner,runtimeId:'synthetic',epoch:1,boundary:'consumer-'+(++serial),available:true,theme:'light',locale:'zh-CN',...patch};
 const state={failure,error,configured,decryptable,hold,revision:4,fields:{reply_theme:{value:null,state:'valid',present:false,source:'default'}},descriptors:{reply_theme:{description:'Reply theme',value_schema:{type:'string',enum:['one','two']},default:null,choices:[{value:null,label:'Follow Core'},{value:'one',label:'Server one'},{value:'two',label:'Server two'}]}}};
 const pairState=()=>({revision:state.revision,fields:Object.fromEntries(['cn','global'].map(region=>['credential_fflogs_'+region,{configured:state.configured,decryptable:state.decryptable,upstream:'unverified'}]))});
 const bridge={async apiPost(action,body){calls.push({action,body});
  if(action==='admin/module-page-session')return {page_session:'synthetic-'+(++proof),expires_at:Date.now()/1000+30};
  if(action==='admin/module-page-session-close')return {closed:true};
  if(action===state.hold)await new Promise(resolve=>pending.push(resolve));
  if(action===state.failure)throw state.error||Error('admin_authorization_denied');
  if(action==='admin/module-config-current')return {revision:state.revision,fields:structuredClone(state.fields),descriptors:structuredClone(state.descriptors)};
  if(action==='admin/module-credential-status')return pairState();
  if(body.expected_revision!==state.revision)throw Error('revision_conflict');
  if(action==='admin/module-credential-replace'&&!state.decryptable)throw Error('secret_encryption_unavailable');
  if(action==='admin/module-credential-clear')state.configured=false;
  if(action==='admin/module-config-save')for(const [name,value]of Object.entries(body.values))state.fields[name]={value,state:'valid',present:value!==null,source:value===null?'default':'sqlite'};
  return {revision:++state.revision};
 }};
 let confirm=true;const scope={signal:new AbortController().signal,isCurrent:()=>active,onDispose(callback){callbacks.push(callback);}};
 const services=await createModuleServices({bridge,binding:{owner,page:'config',runtime_id:context.runtimeId,module_epoch:context.epoch},capabilityId:null,isCurrent:()=>active&&context.available,onDispose:scope.onDispose,confirmClear:async()=>confirm});
 const controller=createSettingsController({routeId:'config',context,scope,services},()=>{});await controller.load();
 return {controller,state,context,calls,services,scope,confirm(value){confirm=value;},editPair(field='credential_fflogs_cn'){controller.editPair(field,'client_id','synthetic-id');controller.editPair(field,'client_secret','synthetic-secret');},release(){pending.shift()?.();},invalidate(){active=false;controller.update({...context,available:false});},dispose(){active=false;callbacks.forEach(callback=>callback());},writes:()=>calls.filter(call=>/module-(?:config-save|config-clear|credential-replace|credential-clear)$/.test(call.action))};
}
