import {h,shallowRef} from 'vue';
import {NButton} from 'naive-ui';
import {FormField,FormGrid} from '@game-link/ui';
import type {Bridge} from './contracts';
import type {InlineConfirmation} from './confirmation';
import {coreText} from './i18n/messages';
import type {UiParams} from './i18n/locale';

type Row={package_id:string;plan_id?:string;installed_version?:string;running_version?:string;installed_digest?:string;running_digest?:string;state:string;reason_code?:string;grant_revision?:number;artifact_revision?:number;signature_state?:string;permissions_delta?:unknown};
type Publisher={key_id:string;public_key_base64:string;channels:string[];revision:number;revoked:boolean};
type Status={core_epoch:number;core:Record<string,unknown>;packages:Row[];publishers:Publisher[]};
const safeCode=(value:unknown)=>typeof value==='string'&&/^[a-z][a-z0-9_]{0,63}$/.test(value)?value:'hot_update_failed';
const safeFailure=(error:unknown)=>{const value=error instanceof Error?error.message:'';return new Set(['invalid_response','stale_generation','admin_authorization_denied','revision_conflict','plan_changed','hot_update_unavailable']).has(value)?value:'hot_update_failed';};
const object=(value:unknown):value is Record<string,any>=>!!value&&typeof value==='object'&&!Array.isArray(value);
/** All plans are inert; every write uses a reviewed plan and exact server CAS. */
export function createInstallationManagement(bridge:Bridge,confirmation:InlineConfirmation,current:()=>boolean,version:()=>number,locale:()=>string){
 const text=(key:string,params?:UiParams)=>coreText(locale(),key,params);
 const status=shallowRef<Status|null>(null),plan=shallowRef<Row|null>(null),pkg=shallowRef(''),key=shallowRef(''),channels=shallowRef('preview'),busy=shallowRef(false),message=shallowRef(''),code=shallowRef('');let serial=0,closed=false;
 const valid=(ticket:number,generation:number)=>!closed&&current()&&serial===ticket&&generation===version();
 function invalidate(){serial++;busy.value=false;plan.value=null;status.value=null;message.value='';code.value='';confirmation.cancel();}
 async function request(action:string,data:Record<string,unknown>,epoch:number|null){
  const value=await bridge.apiPost('admin/module-hot-update-'+action,epoch===null?data:{...data,core_epoch:epoch});
  if(!object(value))throw Error('invalid_response');
  if(value.ok!==true)throw Object.assign(Error(safeCode(value.error?.code??value.reason_code)),{controllerCode:true});
  if(!Number.isSafeInteger(value.core_epoch)||value.core_epoch<1||(epoch!==null&&value.core_epoch!==epoch))throw Error('stale_generation');
  return value;
 }
 async function refresh(){
  if(!current()||busy.value)return;confirmation.cancel();const ticket=++serial,generation=version();busy.value=true;plan.value=null;status.value=null;message.value='';code.value='';
  try{const value=await request('bootstrap',{},null);if(!valid(ticket,generation))return;
   if(!object(value.core)||!Array.isArray(value.packages)||!Array.isArray(value.publishers)||!value.packages.every((row:unknown)=>object(row)&&typeof row.package_id==='string'&&typeof row.state==='string'))throw Error('invalid_response');
   status.value=value as unknown as Status;return ticket;
  }catch(error){if(valid(ticket,generation)){code.value=object(error)&&error.controllerCode===true?safeCode(error.message):safeFailure(error);message.value='hotUpdate.failed';}}
  finally{if(valid(ticket,generation))busy.value=false;}
 }
 async function inspect(packageId=pkg.value.trim()){
  if(!current()||busy.value||!status.value||!packageId)return;
  confirmation.cancel();const ticket=++serial,generation=version(),epoch=status.value.core_epoch;busy.value=true;plan.value=null;message.value='';code.value='';
  try{const value=await request('inspect',{package_id:packageId},epoch);if(!valid(ticket,generation))return;
   if(value.package_id!==packageId||typeof value.plan_id!=='string'||!/^[0-9a-f]{48}$/.test(value.plan_id)||!Number.isSafeInteger(value.grant_revision)||!Number.isSafeInteger(value.artifact_revision)||!object(value.permissions_delta))throw Error('invalid_response');
   plan.value=Object.freeze(value) as unknown as Row;return ticket;
  }catch(error){if(valid(ticket,generation)){code.value=object(error)&&error.controllerCode===true?safeCode(error.message):safeFailure(error);message.value='hotUpdate.failed';if(code.value==='stale_generation')status.value=null;}}
  finally{if(valid(ticket,generation))busy.value=false;}
 }
 async function mutate(action:string,data:Record<string,unknown>,review:unknown){
  if(!current()||busy.value||!status.value)return;
  const ticket=serial,generation=version(),epoch=status.value.core_epoch,selected=plan.value,still=()=>valid(ticket,generation)&&status.value?.core_epoch===epoch&&plan.value===selected&&!busy.value;
  if(!await confirmation.ask({kind:'extension-install',title:()=>text('hotUpdate.confirm'),message:()=>text('hotUpdate.confirmReview',{action:text('hotUpdate.'+action),review:JSON.stringify(review)}),confirmLabel:()=>text('hotUpdate.'+action),isCurrent:still})||!still())return;
  busy.value=true;plan.value=null;message.value='';code.value='';
  try{
   await request(action,data,epoch);if(!valid(ticket,generation))return;busy.value=false;
   let continuation=await refresh();
   if(continuation===undefined||!valid(continuation,generation)||status.value?.core_epoch!==epoch)return;
   if('package_id' in data){
    continuation=await inspect(data.package_id as string);
    if(continuation===undefined||!valid(continuation,generation)||status.value?.core_epoch!==epoch)return;
   }
   message.value='hotUpdate.updated';
  }
  catch(error){if(valid(ticket,generation)){status.value=null;code.value=object(error)&&error.controllerCode===true?safeCode(error.message):safeFailure(error);message.value='hotUpdate.failed';}}
  finally{if(valid(ticket,generation))busy.value=false;}
 }
 function approve(action:'package-approve'|'artifact-approve'){const row=plan.value;if(!row?.plan_id)return;void mutate(action,{package_id:row.package_id,plan_id:row.plan_id,expected_revision:action==='package-approve'?row.grant_revision:row.artifact_revision},row);}
 const button=(action:string,onClick:()=>void,disabled=false)=>h(NButton,{
  disabled:!current()||disabled,'aria-disabled':String(busy.value||!current()||disabled),'aria-busy':String(busy.value),
  onClick:()=>{if(!busy.value&&current()&&!disabled)onClick();},
 },()=>text('hotUpdate.'+action));
 const displayFact=(name:string,value:unknown)=>value===null||value===undefined?text('hotUpdate.unknown'):name==='ready'?text(value===true?'hotUpdate.readyYes':'hotUpdate.readyNo'):['state','phase'].includes(name)&&['active','installed','fallback','cleanup_pending','approval_required','rejected','failed','preparing','stopped'].includes(String(value))?text('hotUpdate.state.'+value):String(value);
 const facts=(row:Row|Record<string,unknown>)=>h('dl',{class:'hot-update-facts'},['installed_version','installed_digest','running_version','running_digest',...('phase' in row?['phase','ready']:row.signature_state==='host_install'?['state','reason_code','operation_id']:['state','reason_code','grant_revision','artifact_revision','publisher_key_id','operation_id'])].map(name=>h('div',{key:name},[h('dt',text('hotUpdate.'+name)),h('dd',{style:{overflowWrap:'anywhere',marginInlineStart:'1rem'}},displayFact(name,row[name as keyof typeof row]))])));
 const input=(id:string,label:string,value:string,change:(value:string)=>void)=>h(FormField,{controlId:id,label:text(label)},{default:({controlAttrs}:any)=>h('input',{...controlAttrs,value,disabled:busy.value,maxlength:1024,onInput:(event:Event)=>{serial++;plan.value=null;confirmation.cancel();change((event.target as HTMLInputElement).value);}})});
 return {invalidate,dispose(){invalidate();closed=true;},refresh,inspect,mutate,render(){return h('details',{class:'extension-installation-management',onToggle:(event:Event)=>{if((event.currentTarget as HTMLDetailsElement).open&&!status.value&&!busy.value)void refresh();}},[
  h('summary',text('hotUpdate.heading')),h('p',text('hotUpdate.help')),button('refresh',()=>void refresh()),
  message.value?h('p',{role:'status'},[text(message.value),code.value?' · '+code.value:'']):null,
  status.value?h('section',{'aria-label':'Core'},[h('h3','Core'),facts(status.value.core)]):null,
  ...(status.value?.packages||[]).map(row=>h('section',{key:row.package_id,'data-package-id':row.package_id},[h('h3',row.package_id),facts(row),row.state==='fallback'||row.state==='cleanup_pending'?h('p',text(row.state==='fallback'?'hotUpdate.fallbackHelp':'hotUpdate.cleanupHelp')):null,button('inspect',()=>void inspect(row.package_id))])),
  h(FormGrid,null,{default:()=>[input('hot-update-package','installation.packageId',pkg.value,value=>pkg.value=value)]}),button('inspect',()=>void inspect(),!status.value||!pkg.value.trim()),
  plan.value?h('section',{'aria-label':text('hotUpdate.review')},[h('h3',text('hotUpdate.review')+' · '+plan.value.package_id),facts(plan.value),h('p',plan.value.signature_state==='host_install'?text('hotUpdate.hostInstall'):text('hotUpdate.signature')+' · '+plan.value.signature_state),h('pre',{style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'28rem',overflow:'auto'}},JSON.stringify(plan.value.permissions_delta,null,2)),h('div',{class:'module-management-actions'},[
   ...(plan.value.signature_state==='host_install'?[]:[button('package-approve',()=>approve('package-approve'),!['unsigned','verified'].includes(plan.value.signature_state||'')),
   button('artifact-approve',()=>approve('artifact-approve'),plan.value.signature_state!=='unsigned'||!plan.value.grant_revision),
   button('package-revoke',()=>void mutate('package-revoke',{package_id:plan.value!.package_id,expected_revision:plan.value!.grant_revision},plan.value),!plan.value.grant_revision),
   button('artifact-revoke',()=>void mutate('artifact-revoke',{package_id:plan.value!.package_id,artifact_digest:plan.value!.installed_digest,expected_revision:plan.value!.artifact_revision},plan.value),!plan.value.artifact_revision)]),
   button('retry',()=>void mutate('retry',{package_id:plan.value!.package_id},plan.value)),
  ])]):null,
  h('details',{class:'legacy-publisher-management'},[h('summary',text('hotUpdate.legacy')),h('h3',text('hotUpdate.publishers')),h('p',text('hotUpdate.publisherHelp')),
  h(FormGrid,null,{default:()=>[input('hot-update-public-key','hotUpdate.publicKey',key.value,value=>key.value=value),input('hot-update-channels','hotUpdate.channels',channels.value,value=>channels.value=value)]}),
  button('publisher-enroll',()=>{const existing=status.value?.publishers.find(p=>p.public_key_base64===key.value.trim());void mutate('publisher-enroll',{public_key_base64:key.value.trim(),channels:channels.value.split(',').map(s=>s.trim()).filter(Boolean),expected_revision:existing?.revision??0},{public_key_base64:key.value.trim(),channels:channels.value});},!status.value||!key.value.trim()||!channels.value.trim()),
  ...(status.value?.publishers||[]).map(p=>h('section',{key:p.key_id},[h('p',{style:{overflowWrap:'anywhere'}},p.key_id+' · '+p.public_key_base64+' · '+p.channels.join(', ')+' · '+text(p.revoked?'hotUpdate.revoked':'hotUpdate.enrolled')),button('publisher-revoke',()=>void mutate('publisher-revoke',{key_id:p.key_id,expected_revision:p.revision},p),p.revoked)])),
  ]),
 ]);}};
}
