import {assertConfigBudget,assertDescriptors} from './config-descriptors';
import type {Bridge} from './contracts';
import type {ModulePageServices, ConfigSnapshot, CredentialSnapshot, MutationReceipt, JsonValue, CredentialPair} from '../../public-contract';

export interface ModuleBinding {readonly owner: string; readonly page: string; readonly runtime_id: string; readonly module_epoch: number}
const plain=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
function immutable<T>(value:unknown):T {
  const copy=JSON.parse(JSON.stringify(value));
  const freeze=(node:unknown):unknown=>{if(node&&typeof node==='object'){Object.values(node).forEach(freeze);Object.freeze(node);}return node;};
  return freeze(copy) as T;
}
const revision=(value:unknown)=>Number.isSafeInteger(value)&&Number(value)>0;
const denied=()=>{throw Error('page_unavailable');};
function publicError(error:unknown):Error{
  const row=error as {code?:unknown;error_code?:unknown;message?:unknown;status?:unknown;response?:{status?:unknown}}|null;
  if([row?.status,row?.response?.status].some(status=>status===401||status===403))return Error('admin_authorization_denied');
  const codes=['admin_authorization_denied','page_revoked','page_unavailable','asset_revoked','stale_context','revision_conflict','invalid_config','secret_encryption_unavailable','operation_cancelled','operation_in_progress'];
  for(const code of [row?.code,row?.error_code,row?.message])if(typeof code==='string'&&codes.includes(code))return Error(code==='asset_revoked'?'page_revoked':code);
  if(row?.message==='配置版本冲突，请刷新并重新核对后修改。')return Error('revision_conflict');
  return Error('operation_unavailable');
}
/** All authority is rechecked by the backend; this closure prevents accidental coupling. */
export async function createModuleServices({bridge,binding,capabilityId,isCurrent,confirmClear,onDispose}:{bridge:Bridge;binding:ModuleBinding;capabilityId:string|null;isCurrent():boolean;onDispose(callback:()=>void):unknown;confirmClear(field:string,revision:number,isCurrent:()=>boolean):Promise<boolean>}):Promise<ModulePageServices> {
  const identity=Object.freeze({...binding});let writing=false,closed=false,proof='',expiresAt=0,opening:Promise<string>|null=null;
  const closeProof=(page_session:string)=>{if(page_session)void Promise.resolve().then(()=>bridge.apiPost('admin/module-page-session-close',{page_session})).catch(()=>{});};
  onDispose(()=>{closed=true;closeProof(proof);proof='';expiresAt=0;});
  const check=(late=false)=>{if(!isCurrent())throw Error(late?'page_revoked':'page_unavailable');};
  async function pageSession():Promise<string>{
    check();if(closed)throw Error('page_revoked');
    // Rotate while the current proof still has enough life for the next poll.
    // A 2s UI poll can otherwise land exactly on the 30s expiry boundary.
    if(proof&&Date.now()/1000<expiresAt-10)return proof;
    if(!opening)opening=(async()=>{
      // An expired proof cannot authorize rotation: the registry already prunes it.
      // A fresh bootstrap still rechecks the ordinary Host session and page grants.
      const previous=Date.now()/1000<expiresAt?proof:'';
      let value:unknown;try{value=await bridge.apiPost('admin/module-page-session',{...identity,...(previous?{previous_session:previous}:{})});}catch(error){throw publicError(error);}
      if(!plain(value)||Object.keys(value).sort().join()!=='expires_at,page_session'||typeof value.page_session!=='string'||!value.page_session||value.page_session.length>512||typeof value.expires_at!=='number'||!Number.isFinite(value.expires_at)||value.expires_at<=Date.now()/1000||value.expires_at>Date.now()/1000+31)throw Error('operation_unavailable');
      if(closed||!isCurrent()){closeProof(value.page_session);throw Error('page_revoked');}
      proof=value.page_session;expiresAt=value.expires_at;return proof;
    })().finally(()=>{opening=null;});
    return opening;
  }
  await pageSession();
  async function request(action:string,payload:Record<string,unknown>={}) {
    check();const page_session=await pageSession();check();let result:unknown;try{result=await bridge.apiPost('admin/module-'+action,{...payload,...identity,page_session});}catch(error){check(true);throw publicError(error);}check(true);return result;
  }
  async function mutation(action:string,expectedRevision:number,payload:Record<string,unknown>) {
    check();if(writing)throw Error('operation_in_progress');if(!revision(expectedRevision))throw Error('invalid_config');writing=true;
    try {const value=await request(action,{...payload,expected_revision:expectedRevision});
      if(!plain(value)||Object.keys(value).join()!=='revision'||!revision(value.revision)||Number(value.revision)<=expectedRevision)throw Error('operation_unavailable');
      return immutable<MutationReceipt>(value);
    } finally {writing=false;}
  }
  return Object.freeze({
    jobs:Object.freeze({
      submit(jobRequest:{handler_id:string;parameters:Readonly<Record<string,JsonValue>>;idempotency_key?:string|null}) {return request('job-submit',{request:jobRequest});},
      status(jobId:string) {return request('job-status',{job_id:jobId});},
      result(jobId:string,options:{cursor?:string|null;page_size?:number}={}) {return request('job-result',{job_id:jobId,...options});},
      cancel(jobId:string,expectedRevision:number) {if(!revision(expectedRevision))return Promise.reject(Error('invalid_config'));return request('job-cancel',{job_id:jobId,expected_revision:expectedRevision});},
      retry(jobId:string,expectedRevision:number) {if(!revision(expectedRevision))return Promise.reject(Error('invalid_config'));return request('job-retry',{job_id:jobId,expected_revision:expectedRevision});},
      async resource(jobId:string,assetId:string) {
        const value=await request('job-resource',{job_id:jobId,asset_id:assetId});
        if(!plain(value)||value.media_type!=='image/png'||typeof value.base64!=='string'||value.base64.length>5592416)throw Error('operation_unavailable');
        const raw=atob(value.base64),bytes=Uint8Array.from(raw,c=>c.charCodeAt(0));check(true);
        return new Blob([bytes],{type:value.media_type});
      },
    }),
    async invoke(id:string,parameters:unknown) {check();if(id!==capabilityId||!id)denied();const result=await bridge.apiPost('invoke',{owner:identity.owner,page:identity.page,capability_id:id,parameters});check(true);return result;},
    async preview(id:string,parameters:unknown) {check();if(id!==capabilityId||!id)denied();const result=await bridge.apiPost('invoke-images',{owner:identity.owner,page:identity.page,capability_id:id,parameters});check(true);return result;},
    config:Object.freeze({
      async current() {const value=await request('config-current');assertConfigBudget(value);if(!plain(value)||!revision(value.revision)||!plain(value.fields)||!['fields,revision','descriptors,fields,revision'].includes(Object.keys(value).sort().join()))throw Error('operation_unavailable');
        for(const row of Object.values(value.fields))if(!plain(row)||Object.keys(row).sort().join()!==(row.source==='legacy'?'migration_pending,present,source,state,value':'present,source,state,value')||!['valid','invalid'].includes(String(row.state))||typeof row.present!=='boolean'||!['sqlite','default','legacy'].includes(String(row.source))||row.source==='legacy'&&(row.migration_pending!==true||row.present!==false||row.state!=='valid'))throw Error('operation_unavailable');
        if(Object.hasOwn(value,'descriptors'))assertDescriptors(value.descriptors,value.fields);
        return immutable<ConfigSnapshot>(value);},
      save(expectedRevision:number,values:Readonly<Record<string,JsonValue>>) {if(!plain(values)||!Object.keys(values).length)return Promise.reject(Error('invalid_config'));return mutation('config-save',expectedRevision,{values});},
      clear(expectedRevision:number,fields:readonly string[]) {if(!Array.isArray(fields)||!fields.length||!fields.every(field=>typeof field==='string'&&field.length>0)||new Set(fields).size!==fields.length)return Promise.reject(Error('invalid_config'));return mutation('config-clear',expectedRevision,{fields});},
    }),
    credentials:Object.freeze({
      async status() {const value=await request('credential-status');if(!plain(value)||!revision(value.revision)||!plain(value.fields)||Object.keys(value).sort().join()!=='fields,revision')throw Error('operation_unavailable');
        for(const row of Object.values(value.fields))if(!plain(row)||Object.keys(row).sort().join()!=='configured,decryptable,upstream'||typeof row.configured!=='boolean'||typeof row.decryptable!=='boolean'||row.upstream!=='unverified')throw Error('operation_unavailable');
        return immutable<CredentialSnapshot>(value);},
      replace(expectedRevision:number,field:string,pair:CredentialPair) {if(typeof field!=='string'||!field||!plain(pair)||!['client_id,client_secret','token','subject,token'].includes(Object.keys(pair).sort().join())||Object.values(pair).some(value=>typeof value!=='string'||!value.length||value.length>('token' in pair?4096:512)||/[\x00-\x1f\x7f-\x9f]/u.test(value)))return Promise.reject(Error('invalid_config'));return mutation('credential-replace',expectedRevision,{field,pair:{...pair}});},
      async authorize(expectedRevision:number,flowId:string,step:'begin'|'poll'|'cancel'|'qr'|'revoke',sessionId='') {
        check();if(writing)throw Error('operation_in_progress');
        if(!revision(expectedRevision)||typeof flowId!=='string'||!flowId||flowId.length>64||!['begin','poll','cancel','qr','revoke'].includes(step)||typeof sessionId!=='string'||sessionId.length>256)throw Error('invalid_config');
        writing=true;try {const value=await request('credential-authorize',{expected_revision:expectedRevision,flow_id:flowId,step,session_id:sessionId});if(!plain(value))throw Error('operation_unavailable');return immutable(value);}finally{writing=false;}
      },
      async clear(expectedRevision:number,field:string) {check();if(writing)throw Error('operation_in_progress');if(!revision(expectedRevision)||typeof field!=='string'||!field)throw Error('invalid_config');writing=true;
        let confirmed=false;try {confirmed=await confirmClear(field,expectedRevision,isCurrent);check();}finally{writing=false;}
        if(!confirmed)throw Error('operation_cancelled');return mutation('credential-clear',expectedRevision,{field,confirmation:'CLEAR MODULE CREDENTIAL'});},
    }),
  });
}
