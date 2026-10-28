import type {PageOptions, PageContext, ConfigSnapshot, CredentialSnapshot, JsonValue} from '../../../../pages/public-contract';

export const ordinaryFields = [
  {name:'reply_theme',label:'回复图片主题',value:null},
  {name:'ff14_subscriptions_enabled',label:'日历订阅',value:true,choices:[['true','允许订阅'],['false','暂停订阅']]},
  {name:'ff14_default_region',label:'默认区域',value:'cn',choices:[['cn','国服'],['global','国际服']]},
  {name:'ff14_calendar_default_days',label:'日历默认天数',value:7},
  {name:'ff14_calendar_default_timezone',label:'日历默认时区',value:'Asia/Shanghai'},
  {name:'ff14_calendar_default_delivery_time',label:'日历默认投递时间',value:'08:00'},
  {name:'ff14_calendar_sync_interval_seconds',label:'日历同步周期',value:21600,choices:[['21600','6 小时'],['43200','12 小时'],['86400','24 小时']]},
] as const;
export const credentialFields = [{name:'credential_fflogs_cn',label:'国服'},{name:'credential_fflogs_global',label:'国际服'}] as const;
const drafts = new Map<string,Record<string,string>>();
const authorityErrors=new Set(['admin_authorization_denied','access_denied','auth_expired','page_revoked','page_unavailable','stale_context','module_unavailable']);
function errorCode(error:unknown):string {if(error&&typeof error==='object'){const e=error as {code?:unknown;message?:unknown};return typeof e.code==='string'?e.code:typeof e.message==='string'?e.message:'';}return '';}
export function createSettingsController(options:PageOptions,onChanged:()=>void) {
  let navigationVersion=0;
  const changed=()=>{navigationVersion++;onChanged();};
  const identity=(c:Readonly<PageContext>)=>JSON.stringify([c.owner,c.runtimeId,c.epoch,c.boundary]);
  const key=identity(options.context);
  const state={context:options.context,revision:0,get credentialRevision():number{return this.revision;},ready:false,draftEditable:false,baselineUnverified:false,pending:false,message:'正在读取模块配置…',error:false,
    draft:{} as Record<string,string>,pairs:Object.fromEntries(credentialFields.map(f=>[f.name,{client_id:'',client_secret:''}])),
    credentials:null as CredentialSnapshot|null,themeChoices:[] as {value:string;label:string}[],themeAvailable:false,dirty:false};
  let disposed=false,bindingRevoked=false,sequence=0,editVersion=0,minimumRevision=0,baseline:Record<string,string>={};
  let acknowledgementPending=false,acknowledgedSubmission:{values:Record<string,string>;version:number}|null=null;
  const editedAt:Record<string,number>={};
  const explicitIntent=new Set<string>();
  const current=(n:number)=>!disposed&&!bindingRevoked&&options.scope.isCurrent()&&state.context.available&&identity(state.context)===key&&sequence===n;
  const scrub=(field?:string)=>{for(const [name,pair] of Object.entries(state.pairs))if(!field||name===field){pair.client_id='';pair.client_secret='';}};
  const notify=(message:string,error=false)=>{state.message=message;state.error=error;changed();};
  function syncDrafts(){const patch=Object.fromEntries(Object.entries(state.draft).filter(([name,value])=>explicitIntent.has(name)||value!==baseline[name]));state.dirty=Object.keys(patch).length>0;if(state.dirty)drafts.set(key,patch);else drafts.delete(key);}
  function accept(config:ConfigSnapshot,credentials:CredentialSnapshot,startedAt:number) {
    const patch={...drafts.get(key)};
    if(acknowledgedSubmission)for(const name of Object.keys(acknowledgedSubmission.values))if((editedAt[name]??0)<=acknowledgedSubmission.version){delete patch[name];explicitIntent.delete(name);}
    for(const [name,version] of Object.entries(editedAt))if(version>startedAt)patch[name]=state.draft[name];
    state.revision=config.revision;state.credentials=credentials;minimumRevision=Math.max(minimumRevision,config.revision);
    state.themeAvailable=Boolean(config.descriptors?.reply_theme);
    state.themeChoices=(config.descriptors?.reply_theme?.choices||[]).flatMap(choice=>typeof choice.value==='string'&&choice.value?[{value:choice.value,label:choice.label}]:[]);
    baseline=Object.fromEntries(ordinaryFields.filter(f=>f.name!=='reply_theme'||state.themeAvailable).map(f=>{
      const stored=config.fields[f.name];return [f.name,stored?.state==='invalid'?(f.name==='reply_theme'?'!invalid':''):String(stored?.value??f.value??'')];
    }));
    state.draft={...baseline,...patch};acknowledgementPending=false;acknowledgedSubmission=null;state.baselineUnverified=false;syncDrafts();state.ready=true;state.draftEditable=true;
  }
  async function snapshots(n:number,minRevision=minimumRevision):Promise<[ConfigSnapshot,CredentialSnapshot]|null>{
    // Retry only an inconsistent read pair, never a mutation.
    for(let attempt=0;attempt<2;attempt++){
      const pair=await Promise.all([options.services.config.current(),options.services.credentials.status()]);
      if(!current(n))return null;if(pair[0].revision===pair[1].revision&&pair[0].revision>=minRevision)return pair;
    }
    throw Error('revision_conflict');
  }
  function failure(error:unknown,field?:string,committed=false,reading=false){
    // A failed management read cannot establish authority. Scrub this current
    // context's secrets without pretending an unknown failure is an auth denial.
    if(reading)scrub();
    const code=errorCode(error);
    if(authorityErrors.has(code)){state.ready=false;state.baselineUnverified=true;state.draftEditable=false;scrub();notify('管理授权或模块绑定已失效，请重新打开页面。',true);return;}
    if(committed){state.ready=false;state.baselineUnverified=true;if(field)scrub(field);notify('已提交，状态暂未核实，请刷新确认。',true);return;}
    if(code==='operation_cancelled'){notify('已取消，未修改凭据。');return;}
    if(code==='invalid_config'){notify('输入未被接受，请检查字段和凭据格式。',true);return;}
    if(code==='revision_conflict'){state.ready=false;state.baselineUnverified=true;notify('配置已在其他操作中变化；可继续修改草稿，重新读取后才能保存。',true);return;}
    state.ready=false;state.baselineUnverified=true;
    if(field)scrub(field);
    notify('操作未确认。请重新读取状态后再决定；不会自动重试。',true);
  }
  async function load(){
    if(state.pending||!current(sequence))return;const n=++sequence,startedAt=editVersion;state.pending=true;changed();
    try{const pair=await snapshots(n);if(!pair)return;accept(...pair,startedAt);const invalid=Object.values(pair[0].fields).some(f=>f.state==='invalid');notify(invalid?'存在无效配置，原值已保留；请修正后保存。':'配置已读取。凭据保存后，上游验证状态仍为待验证。',invalid);}
    catch(error){if(current(n))failure(error,undefined,acknowledgementPending,true);}
    finally{if(current(n)){state.pending=false;changed();}}
  }
  async function mutate(action:(revision:number)=>Promise<{revision:number}>,submitted?:Record<string,string>,field?:string){
    if(!state.ready||state.pending||!current(sequence))return;
    const n=++sequence,startedAt=editVersion,revision=state.revision;state.pending=true;changed();let committed=false;
    try{const receipt=await action(revision);if(!current(n))return;committed=true;acknowledgementPending=true;minimumRevision=Math.max(minimumRevision,receipt.revision);state.ready=false;state.baselineUnverified=true;
      if(submitted)acknowledgedSubmission={values:submitted,version:startedAt};
      if(field)scrub(field);const pair=await snapshots(n);if(!pair)return;accept(...pair,startedAt);
      notify(state.dirty?'已保存本次提交；仍有更新的未保存修改。':'已保存；上游状态仍需实际查询确认。');
    }catch(error){if(current(n))failure(error,field,committed,committed);}
    finally{if(current(n)){state.pending=false;changed();}}
  }
  function cancel(){sequence++;state.pending=false;scrub();state.ready=false;state.baselineUnverified=true;notify('已停止等待；操作可能已经保存，请重新读取状态。');}
  function dispose(){if(disposed)return;sequence++;disposed=true;scrub();state.pending=false;state.draftEditable=false;}
  options.scope.onDispose(dispose);
  return {state,load,cancel,dispose,
    navigationImpact(){return Object.freeze({ordinary:state.dirty,credentials:Object.values(state.pairs).some(pair=>Boolean(pair.client_id||pair.client_secret)),pending:state.pending,version:navigationVersion});},
    edit(name:string,value:string){if(!ordinaryFields.some(f=>f.name===name)||!state.draftEditable||!current(sequence)||name==='reply_theme'&&!state.themeAvailable)return;state.draft[name]=value;editedAt[name]=++editVersion;if(state.pending||state.baselineUnverified)explicitIntent.add(name);syncDrafts();changed();},
    editPair(field:string,name:'client_id'|'client_secret',value:string){if(state.pending||!current(sequence)||!state.pairs[field])return;state.pairs[field][name]=value;changed();},
    discard(){if(state.pending||!current(sequence)||!state.draftEditable)return;state.draft={...baseline};explicitIntent.clear();drafts.delete(key);state.dirty=false;scrub();notify(state.ready?'已放弃未保存的输入。':'已放弃本地修改；仍需重新读取才能保存。');},
    discardPair(field:string){if(state.pending||!current(sequence)||!state.pairs[field])return;scrub(field);notify('已放弃未保存的凭据输入。');},
    save(){const submitted={...state.draft},values:Record<string,JsonValue>={};for(const field of ordinaryFields){if(field.name==='reply_theme'&&!state.themeAvailable)continue;const raw=submitted[field.name];values[field.name]=field.name==='reply_theme'?(raw===''?null:raw):typeof field.value==='number'?Number(raw):typeof field.value==='boolean'?raw==='true':raw;}
      if(state.themeAvailable&&submitted.reply_theme!==''&&!state.themeChoices.some(choice=>choice.value===submitted.reply_theme)){notify('请选择当前可用的回复主题，或跟随全局设置。',true);return Promise.resolve();}
      if(!['true','false'].includes(submitted.ff14_subscriptions_enabled)||!['cn','global'].includes(String(values.ff14_default_region))||!Number.isInteger(values.ff14_calendar_default_days)||Number(values.ff14_calendar_default_days)<1||Number(values.ff14_calendar_default_days)>30||![21600,43200,86400].includes(Number(values.ff14_calendar_sync_interval_seconds))||!/^([01]\d|2[0-3]):[0-5]\d$/.test(String(values.ff14_calendar_default_delivery_time))){notify('请检查订阅开关、区域、天数、投递时间和同步周期。',true);return Promise.resolve();}
      try{new Intl.DateTimeFormat('en',{timeZone:String(values.ff14_calendar_default_timezone)});}catch{notify('请输入有效的 IANA 时区。',true);return Promise.resolve();}
      return mutate(revision=>options.services.config.save(revision,values),submitted);},
    replace(field:string){const pair=state.pairs[field];if(!pair||Object.values(pair).some(value=>!value.trim()||value.length>512||/[\x00-\x1f\x7f-\x9f]/u.test(value))){notify('请填写完整凭据对，每项最多 512 字符且不含控制字符。',true);return Promise.resolve();}const submitted={...pair};return mutate(revision=>options.services.credentials.replace(revision,field,submitted),undefined,field);},
    clear(field:string){if(!state.pairs[field])return Promise.resolve();return mutate(revision=>options.services.credentials.clear(revision,field),undefined,field);},
    update(context:Readonly<PageContext>){if(disposed)return;if(identity(context)!==key||!context.available){if(identity(context)!==key)bindingRevoked=true;sequence++;state.pending=false;state.ready=false;state.draftEditable=false;scrub();state.draft={};state.dirty=false;state.message='模块或授权已变化，请重新打开页面。';}state.context=context;changed();},
  };
}
