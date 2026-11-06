import {createManagementRequests} from './management-requests.js';
import {createFontSettings} from './font-settings';
import {createManagementView} from "./management-view";
import {nextTick} from "vue";
import {isCoreThemeField, followsCoreTheme, settingValues} from "./setting-choices";
import {createNotice} from './ui/notice';
import {coreText} from './i18n/messages';
import {normalizeUiLocale} from './i18n/locale';
import {record, exact, fail, key, ownedFields, supported, jsonBudget, validateCatalog, validateSnapshot, updateBody, validateCredentialCatalog, validateCredentialStatus, credentialUpdateBody, validateMasterKeyStatus} from './management-contract.js';
export {validateCatalog,validateSnapshot,updateBody,validateCredentialCatalog,validateCredentialStatus,credentialUpdateBody,validateMasterKeyStatus} from './management-contract.js';
// Exact bridge codes only; free-form messages never establish authorization.
const managementAccessLost=error=>[error?.code,error?.error_code,error?.message].some(code=>['admin_authorization_denied','stale_context','page_revoked','asset_revoked'].includes(code))||[error?.status,error?.response?.status].some(status=>status===401||status===403);
const knownMasterKeyRejections=new Map([
  [
    "原密钥格式无效，请输入标准 Base64 编码的 32 字节密钥；旧凭据未更改。",
    "manage.invalidOriginal"
  ],
  [
    "所填密钥与当前密钥不一致；旧凭据未更改，请核对后重试。",
    "manage.originalMismatch"
  ],
  [
    "所填密钥未能解开现有凭据；旧凭据未被替换，请核对原密钥后重试。",
    "manage.originalDecrypt"
  ]
]);
// Matching names bind the bridge surface; the backend owns authorization.
const presentation=new Set(['theme','isDark','locale','i18n','displayName','pageTitle']);
function contextBoundary(context,expectedPage){
  if(!record(context)||!Object.hasOwn(context,'pluginName')||!Object.hasOwn(context,'pageName')||context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==expectedPage)fail('invalid_context');
  const boundary=Object.fromEntries(Object.entries(context).filter(([name])=>!presentation.has(name)));jsonBudget(boundary);
  const representable=v=>{if(typeof v==='number'&&Object.is(v,-0))fail('invalid_context');if(v&&typeof v==='object')for(const child of Object.values(v))representable(child);};representable(boundary);
  const stable=v=>Array.isArray(v)?v.map(stable):record(v)?Object.fromEntries(Object.keys(v).sort().map(name=>[name,stable(v[name])])):v;return JSON.stringify(stable(boundary));
}
export function createManagementPage(document,bridge,window,{expectedPage='00-game-link',container=document.getElementById('management-root'),scope='settings',initiallySuspended=false,beforeMaintenance=async _operation=>false,onDraftMutation=()=>{},onConfigurationSaved=()=>{},onConfigurationRead=_payload=>{},onCapabilities=(/** @type {string[]|null} */ _owners, _complete=false)=>{}}={}){
  if(!container)fail('invalid_container'); const element=id=>Array.from(container.querySelectorAll('[id]')).find(node=>node.id===id)||null;
  let catalog=null,snapshot=null,masterKeyStatus=null,migrationPlan=null,ready=false,closed=false,stale=true,generation=0,boundary=null,contextSeen=false,off=null,active=null;
  const view=createManagementView(document,container,scope),cards=new Map(),controls=new Map(),status=element('status');
  let suspended=initiallySuspended;let legacyPolicies=[],legacyGeneration=0;const legacyPlans=new Map();
  let savedFocus=null,selectedOwner='game_link/core',localeOverride=null,hostLocale;
  const locale=()=>localeOverride??normalizeUiLocale(hostLocale)??'zh-CN';
  const t=(key)=>coreText(locale(),key);
  const fonts=createFontSettings(bridge,window,panel=>view.font(panel),()=>loseAccess(),()=>{view.cancelConfirmation();clearMasterDraft();},locale);
  const emptyDraft='[[],[]]';let draftState=emptyDraft;
  let noticeKey=null;
  const statusNotice=createNotice(window,(message,tone)=>{if(!message)noticeKey=null;status.textContent=message;status.dataset.tone=tone;});
  const notice=(key,tone='error')=>{noticeKey=key;statusNotice.show(t(key),tone);};
  const relabelNotice=()=>{if(noticeKey)status.textContent=t(noticeKey);};
  function draftChanged(){
    const next=JSON.stringify([
      [...controls].filter(([,c])=>c.mode&&c.mode.value!=='keep').map(([key,c])=>[key,c.mode.value,c.input?.value]),

    ]);
    if(next!==draftState){draftState=next;onDraftMutation();}view.drafts();buttons();
  }
  const current=attempt=>!closed&&ready&&generation===attempt,writable=f=>f.readable&&f.editable&&supported(f);
  const blocked=button=>!button||button.disabled||button.getAttribute('aria-disabled')==='true';
  function clearMasterDraft(){const input=element('master-key-restore-input');if(input)input.value='';view.resetConfirmation();}
  function showMasterStatus(){
    if(scope!=='settings')return;
    view.masterKey(masterKeyStatus?{
      storageSecurity:masterKeyStatus.storage_security||'unknown',environmentPresent:masterKeyStatus.environment_present===true,environmentMatches:masterKeyStatus.environment_matches===true,recoveryAction:masterKeyStatus.recovery_action||null,inventoryKnown:masterKeyStatus.unknown_count===0&&masterKeyStatus.inventory_digest.length===64,migrationPlan,onAdopt:()=>changeMasterKey('adopt-environment'),onPlanMigration:planMigration,
      state:masterKeyStatus.state,reason:masterKeyStatus.reason_code,source:masterKeyStatus.source,
      generation:masterKeyStatus.generation,unknown:masterKeyStatus.unknown_count,impact:masterKeyStatus.impact,
      enabled:!closed&&ready&&!suspended&&!stale&&masterKeyStatus.storage_security!=='blocked'&&masterKeyStatus.inventory_digest.length===64&&masterKeyStatus.unknown_count===0,
      busy:active!==null,onDraft:()=>onDraftMutation(),onRetry:()=>changeMasterKey('retry'),onRestore:()=>changeMasterKey('restore'),onReset:()=>changeMasterKey('reset'),onCancelReset:()=>{view.resetConfirmation();onDraftMutation();notice('manage.resetCancelled','success');},
    }:null);
  }
  function setButton(button,denied){if(!button)return;button.setAttribute('aria-disabled',String(denied));button.disabled=denied&&(closed||!ready||document.activeElement!==button);}
  function buttons(){
    showMasterStatus();
    view.legacy(legacyPolicies.map(policy=>({...policy,status:legacyPlans.get(policy.policy_id)?.status||null,enabled:!closed&&ready&&!suspended&&!stale,busy:active!==null,plan:()=>planLegacy(policy),execute:()=>executeLegacy(policy)})));
    const fontVisible=scope==='settings'&&(selectedOwner===null||selectedOwner==='game_link/core')&&Boolean(catalog?.fields.some(f=>f.module_id==='game_link/core'&&f.group==='reply_style'&&f.readable));
    fonts.sync(fontVisible,!closed&&ready&&!suspended&&!stale);
    setButton(element('refresh'),closed||!ready||active!==null);
    const usable=!closed&&ready&&!suspended&&!stale&&snapshot!==null&&Object.keys(snapshot).length>0&&active===null;
    setButton(element('recover'),!usable||catalog.fields.some(f=>f.readable&&!f.editable));
    for(const [target,card]of cards){const selected=selectedOwner===null||selectedOwner===target;setButton(card.save,!usable||!selected||!catalog||!ownedFields(catalog,target).some(writable));if(card.discard)setButton(card.discard,!ready||closed||active!==null||!selected);}
    // Preserve focused editable drafts while reading; submit buttons fence writes.
    for(const control of controls.values()){const allowed=!closed&&ready&&!suspended&&(selectedOwner===null||selectedOwner===control.field.module_id)&&writable(control.field);control.mode.disabled=!allowed;control.input.disabled=!allowed;if(control.reset)control.reset.disabled=!allowed;}

  }
  const post=createManagementRequests(bridge,current);
  function inputValue(f,value){const choices=settingValues(f);if(value===null&&!followsCoreTheme(f))return '';return choices?String(choices.findIndex(v=>v===value)):String(value);}
  function makeControl(f){
    return {field:f,signature:JSON.stringify(f),draftVersion:0,mode:null,input:null};
  }
  function saveTarget(target){
    const button=cards.get(target).save;if(blocked(button))return;
    run(async attempt=>{
      const selected=Object.fromEntries(ownedFields(catalog,target).map(f=>{const c=controls.get(key(f));return [f.name,{mode:c.mode.value,value:c.input.value,draftVersion:c.draftVersion}];}));
      const body=updateBody(target,snapshot[target].revision,selected,catalog),submitted=body.updates.map(update=>controls.get(JSON.stringify([target,update.field])));
      // An inherited selection made after submission is a new clear intent,
      // even while the last verified baseline still says inherited absence.
      for(const c of submitted){c.writePending=true;c.baselineUnverified=true;}
      try{
      await post('update',body,attempt);onConfigurationSaved();
      try{await refresh(attempt);}catch(error){if(!current(attempt))throw error;stale=true;fail(managementAccessLost(error)?'write_ack_authorization_denied':'write_ack_readback_unverified');}
      for(const [name,sent]of Object.entries(selected)){if(sent.mode==='keep')continue;const c=controls.get(JSON.stringify([target,name])),row=snapshot[target]?.fields[name];if(c&&row&&c.draftVersion===sent.draftVersion&&c.mode.value===sent.mode&&c.input.value===sent.value){c.mode.value='keep';c.input.value=row.state==='valid'?inputValue(c.field,row.value):'';}}
      draftChanged();notice('manage.saved','success');
      }finally{for(const c of submitted)c.writePending=false;}
    });
  }
  function discardTarget(target){
    if(closed||!ready||active!==null||!snapshot?.[target])return;
    for(const f of ownedFields(catalog,target)){const c=controls.get(key(f)),row=snapshot[target].fields[f.name];if(!c||!c.mode||!c.input||!row)continue;c.draftVersion++;c.mode.value='keep';c.input.value=row.state==='valid'?inputValue(f,row.value):'';}
    draftChanged();notice('manage.discarded','success');
  }
  function loseAccess(message='manage.accessLost',tone='error'){
    // Revoke every sensitive owner draft and fence concurrent responses while
    // retaining ordinary local edits for an explicit authorized refresh.
    view.cancelConfirmation();migrationPlan=null;generation++;active=null;stale=true;masterKeyStatus=null;
    fonts.invalidate();clearMasterDraft();onCapabilities(null);buttons();notice(message,tone);
  }
  function changeMasterKey(action){
    if(suspended||!['retry','restore','reset','adopt-environment'].includes(action)||!masterKeyStatus||stale||active)return;
    const source=masterKeyStatus,restore=element('master-key-restore-input'),reset=element('master-key-reset-consent');
    const value=action==='restore'?restore?.value:null;
    if(action==='restore'&&(typeof value!=='string'||!/^[A-Za-z0-9+/]{43}=$/.test(value))){notice('manage.enterOriginal');return;}
    if(action==='reset'&&!reset?.checked){notice('manage.confirmImpact');return;}
    const body={expected_revision:source.revision,expected_generation:source.generation,inventory_digest:source.inventory_digest};
    if(action==='restore')body.encoded_key=value;
    if(action==='reset')body.confirmation='RESET ENCRYPTED CREDENTIALS';
    clearMasterDraft();
    run(async attempt=>{
      if(!current(attempt)||masterKeyStatus!==source)fail('stale_context');
      if(action!=='retry'&&!await view.confirm({kind:action==='reset'?'key-reset':action==='restore'?'key-restore':'key-adopt',title:action==='reset'?()=>t('manage.confirmReset'):action==='restore'?()=>t('manage.confirmRestore'):()=>t('manage.confirmAdopt'),message:action==='reset'?()=>t('manage.resetEffect'):action==='restore'?()=>t('manage.restoreEffect'):()=>t('manage.adoptEffect'),confirmLabel:()=>t('manage.confirm'),isCurrent:()=>current(attempt)&&masterKeyStatus===source&&!suspended}))return;
      if(!current(attempt)||masterKeyStatus!==source||suspended)fail('stale_context');
      let result;
      try{result=await post('master-key-'+action,body,attempt);}
      catch(error){if(!current(attempt))throw error;if(action==='restore'&&knownMasterKeyRejections.has(error?.message)){notice(knownMasterKeyRejections.get(error.message));return;}throw error;}
      try{await refresh(attempt);}catch(error){if(!current(attempt))throw error;stale=true;fail(managementAccessLost(error)?'write_ack_authorization_denied':'write_ack_readback_unverified');}
      if(!masterKeyStatus||masterKeyStatus.revision!==result.revision||masterKeyStatus.generation!==result.generation){stale=true;fail('write_ack_readback_unverified');}
      notice(result.ready?'manage.keyReady':'manage.keyRecorded',result.ready?'success':'warning');
    });
  }
  function planLegacy(policy){
    if(suspended||active||stale||!legacyPolicies.includes(policy))return;
    const version=legacyGeneration;
    run(async attempt=>{const plan=await post('legacy-config-plan',{policy_id:policy.policy_id},attempt);if(version!==legacyGeneration)return;legacyPlans.set(policy.policy_id,plan);});
  }
  function executeLegacy(policy){
    const plan=legacyPlans.get(policy.policy_id),version=legacyGeneration;
    if(suspended||active||stale||!plan||plan.status!=='migration_pending'||!legacyPolicies.includes(policy))return;
    run(async attempt=>{
      const valid=()=>current(attempt)&&!suspended&&legacyGeneration===version&&legacyPlans.get(policy.policy_id)===plan;
      if(!await view.confirm({kind:'legacy-config',owner:policy.target_owner,title:()=>t('manage.confirmLegacyCopy'),message:()=>t('manage.legacyCopyEffect'),confirmLabel:()=>t('manage.confirmCopy'),isCurrent:valid})||!valid())return;
      const result=await post('legacy-config-execute',{policy_id:policy.policy_id,source_revision:plan.source_revision,target_revision:plan.target_revision,confirmed:true},attempt);
      if(!valid())return;legacyPlans.set(policy.policy_id,result);onConfigurationSaved();
    });
  }
  function setLegacyPolicies(policies){
    if(!Array.isArray(policies))return;
    const next=policies.map(policy=>({policy_id:policy.policy_id,target_owner:policy.target_owner}));
    if(JSON.stringify(next)===JSON.stringify(legacyPolicies))return;
    legacyGeneration++;legacyPlans.clear();view.cancelConfirmation();legacyPolicies=next;buttons();
  }
  function planMigration(){
    if(suspended||active||stale||masterKeyStatus?.storage_security!=='legacy_review_required')return;
    run(async attempt=>{migrationPlan=await post('master-key-migration-plan',{},attempt);notice('manage.planRead','warning');});
  }
  async function render(){
    const owners=[...new Set(catalog.fields.filter(f=>f.module_id==='game_link/core').map(f=>f.module_id))],wanted=new Set(catalog.fields.map(key));
    for(const k of controls.keys())if(!wanted.has(k))controls.delete(k);
    for(const target of cards.keys())if(!owners.includes(target))cards.delete(target);
    const reset=[];
    for(const target of owners){
      let card=cards.get(target);if(!card){card={target,controls:[],save:null,discard:null,onSave:()=>saveTarget(target),onDiscard:()=>discardTarget(target)};cards.set(target,card);}
      card.controls=[];
      for(const f of ownedFields(catalog,target)){
        const k=key(f),signature=JSON.stringify(f);let c=controls.get(k);if(!c||c.signature!==signature){c=makeControl(f);controls.set(k,c);}
        // Only a current verified read can restore trust in the baseline. A failed
        // write/readback must retain this fence after the operation has finished.
        c.baselineUnverified=false;
        const row=f.readable?snapshot[target].fields[f.name]:null;c.inheritBaseline=Boolean(followsCoreTheme(f)&&row?.state==='valid'&&row.value===null&&row.present===false&&row.source==='default');c.problem=!f.readable?'manage.noAccess':!f.editable?'manage.readonly':!supported(f)?'manage.unsupported':row?.state==='invalid'?'manage.invalidValue':'';
        if(!supported(f))reset.push([c,row?.state==='valid'?JSON.stringify(row.value):'',c.mode?.value,c.input?.value,c.draftVersion]);else if(!c.mode||c.mode.value==='keep')reset.push([c,row?.state==='valid'?inputValue(f,row.value):'',c.mode?.value,c.input?.value,c.draftVersion]);
        card.controls.push(c);
      }
    }
    view.fields([...cards.values()],draftChanged);await nextTick();
    for(const [c,value,mode,input,draftVersion]of reset)if(c.input&&c.draftVersion===draftVersion&&(mode===undefined||c.mode.value===mode&&c.input.value===input))c.input.value=value;
    buttons();
  }
  async function refresh(attempt){migrationPlan=null;legacyPlans.clear();view.cancelConfirmation();view.resetConfirmation();stale=true;buttons();const declarations=await post('catalog',{},attempt),values=validateSnapshot(await post('read',{},attempt),declarations);if(!current(attempt))fail('stale_context');catalog=declarations;snapshot=values;onConfigurationRead(values);
    if(scope==='settings'){try{masterKeyStatus=await post('master-key-status',{},attempt);}catch(error){if(current(attempt))masterKeyStatus=null;throw error;}}
    stale=false;if(scope==='settings')await render();buttons();if(!current(attempt))fail('stale_context');onCapabilities([...new Set(catalog.fields.map(f=>f.module_id))],true);return {};
  }
  async function run(action){
    if(active||closed||!ready)return;const operation={generation};active=operation;buttons();
    try{await action(operation.generation);}catch(error){if(!current(operation.generation))return;
      // Host bridge retains only the controlled message, not HTTP status or code.
      if(error?.message==='revision_conflict'||error?.message==='配置版本冲突，请刷新并重新核对后修改。'){stale=true;notice('manage.revisionChanged');}
      else if(error?.message==='select_changes')notice('manage.selectChanges');
      else if(error?.message==='confirm_clear')notice('manage.confirmClear');
      else if(error?.message==='write_ack_readback_unverified'){clearMasterDraft();notice('manage.unverified','warning');}
      else if(error?.message==='write_ack_authorization_denied')loseAccess('manage.unverifiedAuth','warning');
      else if(['requires_stopped_host','source_exclusivity_unproven','offline_supervisor_unavailable'].includes(error?.code)||['requires_stopped_host','source_exclusivity_unproven','offline_supervisor_unavailable'].includes(error?.message)){migrationPlan=null;notice('manage.migrationUnsupported','warning');}
      else if(error?.message==='invalid_config')notice('manage.invalidInput');
      else if(managementAccessLost(error))loseAccess();
      else {clearMasterDraft();stale=true;notice('manage.failure');}
    }finally{if(active===operation){active=null;buttons();}}
  }
  const revisions=()=>Object.fromEntries(Object.entries(snapshot).map(([target,row])=>[target,row.revision]));
  element('refresh').addEventListener('click',()=>!blocked(element('refresh'))&&run(async attempt=>{await refresh(attempt);notice('manage.refreshed','success');}));
  for(const action of ['recover'])element(action)?.addEventListener('click',()=>!blocked(element(action))&&run(async attempt=>{
    if(!await beforeMaintenance({action,isCurrent:()=>current(attempt)})||!current(attempt))return;
    await post(action,{expected_revisions:revisions()},attempt);
    try{await refresh(attempt);}catch(error){if(!current(attempt))throw error;stale=true;fail(managementAccessLost(error)?'write_ack_authorization_denied':'write_ack_readback_unverified');}
    onConfigurationSaved();notice('manage.recovered','success');
  }));
  function accept(context){
    if(closed)return;let next;
    try{next=contextBoundary(context,expectedPage);}catch{view.cancelConfirmation();migrationPlan=null;legacyPlans.clear();fonts.invalidate();onCapabilities(null);clearMasterDraft();contextSeen=true;generation++;ready=false;stale=true;active=null;boundary=null;catalog=null;snapshot=null;masterKeyStatus=null;view.fields([],draftChanged);view.masterKey(null);cards.clear();controls.clear();draftState=emptyDraft;buttons();notice('manage.contextInvalid');return;}
    hostLocale=context.locale;view.context(context);
    document.documentElement.dataset.theme=context.isDark===true||context.theme==='dark'?'dark':'light';
    if(localeOverride===null)document.documentElement.lang=locale();relabelNotice();
    contextSeen=true;if(ready&&next===boundary)return;view.cancelConfirmation();migrationPlan=null;legacyPlans.clear();fonts.invalidate();statusNotice.clear();onCapabilities(null);clearMasterDraft();generation++;boundary=next;ready=true;stale=true;active=null;catalog=null;snapshot=null;masterKeyStatus=null;view.fields([],draftChanged);view.masterKey(null);cards.clear();controls.clear();draftState=emptyDraft;buttons();
    run(async attempt=>{await refresh(attempt);});
  }
  const masterDraft=()=>Boolean(element('master-key-restore-input')?.value);
  const dirty=()=>[...controls.values()].some(c=>c.mode?.value!=='keep')||masterDraft();
  // Navigation preserves ordinary drafts but suspend clears credentials across
  // all owners. Keep this scope separate from global module-operation guards.
  function navigationImpact(){
    const ordinaryOwners=[...new Set([...controls.values()].filter(c=>(selectedOwner===null||c.field.module_id===selectedOwner)&&['replace','clear'].includes(c.mode?.value)).map(c=>c.field.module_id))];
    const credentialOwners=[];
    return {ordinaryOwners,credentialOwners,requiresConfirmation:ordinaryOwners.length>0||credentialOwners.length>0||masterDraft()};
  }
  function suspend(){legacyPlans.clear();view.cancelConfirmation();migrationPlan=null;suspended=true;fonts.invalidate();if(status.dataset.tone==='success')statusNotice.clear();else statusNotice.cancel();const node=document.activeElement;savedFocus=container.contains(node)&&node?.id?{id:node.id,start:node.selectionStart,end:node.selectionEnd}:null;clearMasterDraft();generation++;active=null;stale=true;buttons();}
  function resume(){if(closed||!ready)return;suspended=false;run(async attempt=>{await refresh(attempt);if(savedFocus){const node=element(savedFocus.id);node?.focus({preventScroll:true});if(node&&savedFocus.start!==null&&node.setSelectionRange)try{node.setSelectionRange(savedFocus.start,savedFocus.end);}catch{}savedFocus=null;}});}
  function close(){if(closed)return;view.cancelConfirmation();migrationPlan=null;fonts.dispose();statusNotice.cancel();onCapabilities(null);clearMasterDraft();closed=true;generation++;ready=false;active=null;catalog=null;snapshot=null;masterKeyStatus=null;off?.();buttons();}
  return {start(){
    if(!bridge||typeof bridge.apiPost!=='function'||typeof bridge.ready!=='function'||typeof bridge.onContext!=='function'){notice('manage.openFromHost');return;}
    off=bridge.onContext(accept);Promise.resolve(bridge.ready()).then(context=>{if(!contextSeen)accept(context);}).catch(()=>{if(!contextSeen&&!closed){ready=false;notice('manage.sessionUnavailable');buttons();}});window?.addEventListener('pagehide',close,{once:true});
  },setLocale(value){if(localeOverride!==value){localeOverride=value;view.setLocale(value);relabelNotice();}},setModuleLabels(labels){view.labels(labels);},setLegacyPolicies,close,dirty,navigationImpact,suspend,resume,selectOwner(){selectedOwner='game_link/core';view.owner(selectedOwner);buttons();},dispose(){close();window?.removeEventListener('pagehide',close);view.dispose();}};
}
