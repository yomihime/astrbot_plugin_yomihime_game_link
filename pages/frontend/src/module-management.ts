import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NButton, NAlert} from 'naive-ui';
import {UiProvider} from '@game-link/ui';
import {contextBoundary} from './lifecycle';
import {createInlineConfirmation, type InlineConfirmation} from './confirmation';
import {createNotice} from './ui/notice';
import {moduleGroups} from './catalog';
import {createInstallationManagement} from './installation-management';
import type {ModuleDescriptor} from './contracts';
import type {Bridge} from './contracts';
import {coreText} from './i18n/messages';
import type {UiParams} from './i18n/locale';

const lifecycles = new Set(['active', 'stopped', 'failed', 'unloaded', 'discovered', 'starting', 'stopping']);
type Status={module_id:string;enabled:boolean;lifecycle:string;health:string;epoch:number;registry_revision:number;reason_code:string|null};
export type ModuleOperation={owner:string;action:'enable'|'disable'|'unload';revision:number;isCurrent():boolean;canRestoreFocus():boolean};
export function createModuleManagement(container:HTMLElement,bridge:Bridge,window:Window,expectedPage:string,onChanged:()=>void,beforeOperate:(operation:ModuleOperation)=>boolean|Promise<boolean>=()=>true,sharedConfirmation?:InlineConfirmation,interactionKey:()=>string=()=>'', home?:{hasSettings?(owner:string):boolean;onSettings(owner:string):void;labels():Map<string,string>;catalog(value:unknown):{fields:{module_id:string;name:string;readable:boolean;editable:boolean}[]};snapshot(value:unknown,catalog:unknown):Record<string,{fields:Record<string,{state:string}>}>} ) {
  const descriptors=shallowRef<ModuleDescriptor[]>([]);
  const localeOverride=shallowRef<string|null>(null);
  const locale=()=>localeOverride.value ?? (typeof presentation.value.locale==='string'?presentation.value.locale:'zh-CN');
  const text = (key: string, params?: UiParams) => coreText(locale(), key, params);
  const rowGroups=()=>moduleGroups(rows.value.filter(row=>selectedOwner.value===null||row.module_id===selectedOwner.value).map(row=>({...descriptors.value.find(module=>module.module_id===row.module_id),module_id:row.module_id,row})),locale());
  const rows=shallowRef<Status[]>([]),message=shallowRef(''),tone=shallowRef('neutral'),busy=shallowRef(false),stale=shallowRef(true);
  const notice=createNotice(window,(text,value)=>{message.value=text;tone.value=value;});
  const settingOwners=shallowRef<Set<string>>(new Set()),renderVisible=shallowRef(true);
  const confirmation=sharedConfirmation??createInlineConfirmation(container.ownerDocument,()=>text('modules.confirm.cancel')),waiting=shallowRef<number|null>(null);
  const presentation=shallowRef<Record<string,unknown>>({}),labels=shallowRef<Map<string,string>>(new Map()),configuration=shallowRef<Record<string,string>>({}),selectedOwner=shallowRef<string|null>(null);
  let closed=false,ready=false,generation=0,boundary='',registryRevision=0,operationSerial=0,off:(()=>void)|undefined;
  let configurationVersion=0,configurationDirty=true,configurationAttempted=false,homeVisible=false,configurationFlight:Promise<void>|null=null;
  const valid=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const current=(version:number)=>!closed&&ready&&version===generation;
  const installation=createInstallationManagement(bridge,confirmation,()=>!closed&&ready&&renderVisible.value&&selectedOwner.value===null,()=>generation,locale);
  function invalidateConfiguration(refreshVisible=true){configurationVersion++;configurationDirty=true;configurationAttempted=false;configuration.value={};if(refreshVisible&&homeVisible)void refreshConfiguration();}
  function refreshConfiguration(allowHidden=false):Promise<void>{
    if(configurationFlight)return configurationFlight;
    if(!home||!ready||closed||!configurationDirty||configurationAttempted||!allowHidden&&(!homeVisible||busy.value))return Promise.resolve();
    const version=generation,ticket=configurationVersion;configurationAttempted=true;
    const work=async()=>{
      try{
        const declarations=home.catalog(await bridge.apiPost('admin/catalog',{}));
        if(!current(version)||ticket!==configurationVersion)return;
        settingOwners.value=new Set(declarations.fields.map(field=>field.module_id));
        const snapshot=home.snapshot(await bridge.apiPost('admin/read',{}),declarations);
        if(!current(version)||ticket!==configurationVersion)return;
        const values:Record<string,string>={};
        for(const row of rows.value){const fields=declarations.fields.filter(field=>field.module_id===row.module_id),readable=fields.filter(field=>field.readable),stored=snapshot[row.module_id];values[row.module_id]=!fields.length?'modules.configuration.none':!readable.length?'modules.configuration.denied':readable.some(field=>stored?.fields[field.name]?.state==='invalid')?'modules.configuration.invalid':!stored||readable.some(field=>stored.fields[field.name]?.state!=='valid')?'modules.configuration.unverified':fields.some(field=>!field.readable)?'modules.configuration.partial':readable.some(field=>!field.editable)?'modules.configuration.readonly':'modules.configuration.valid';}
        configuration.value=values;configurationDirty=false;
      }catch{if(current(version)&&ticket===configurationVersion)configuration.value={};}
    };
    configurationFlight=work().finally(()=>{configurationFlight=null;if(homeVisible&&configurationDirty&&!configurationAttempted)void refreshConfiguration();});
    return configurationFlight;
  }
  function decode(value:unknown) {
    if(!valid(value)||Object.keys(value).sort().join(',')!=='modules,registry_revision'||!Number.isSafeInteger(value.registry_revision)||!Array.isArray(value.modules)||value.modules.length>128)throw Error('invalid_modules');
    const seen=new Set<string>();
    for(const row of value.modules) {
      if(!valid(row)||Object.keys(row).sort().join(',')!=='enabled,epoch,health,lifecycle,module_id,reason_code,registry_revision'||typeof row.module_id!=='string'||!/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(row.module_id)||seen.has(row.module_id)||typeof row.enabled!=='boolean'||!Number.isSafeInteger(row.epoch)||!Number.isSafeInteger(row.registry_revision)||typeof row.lifecycle!=='string'||typeof row.health!=='string'||!(row.reason_code===null||typeof row.reason_code==='string'))throw Error('invalid_modules');
      seen.add(row.module_id);
    }
    return value as unknown as {registry_revision:number;modules:Status[]};
  }
  async function refresh(feedback=false) {
    if(!ready||closed||busy.value)return;
    confirmation.cancel();waiting.value=null;const version=generation;busy.value=true;stale.value=true;invalidateConfiguration(false);
    try {const result=decode(await bridge.apiPost('admin/modules',{}));if(!current(version))return false;rows.value=result.modules;registryRevision=result.registry_revision;stale.value=false;
      if(home){await refreshConfiguration(true);if(!current(version))return false;labels.value=home.labels();}if(feedback)notice.show('modules.notice.refreshed','success');else if(tone.value==='error')notice.clear();return true;}
    catch {if(current(version)){stale.value=true;notice.show('modules.notice.readFailed');}return false;}
    finally {if(current(version)){busy.value=false;if(homeVisible&&configurationDirty&&!configurationAttempted)void refreshConfiguration();}}
  }
  async function operate(row:Status,action:'enable'|'disable'|'unload') {
    if(!current(generation)||busy.value||stale.value||waiting.value||!rows.value.includes(row))return;
    const version=generation,revision=registryRevision,interaction=interactionKey(),ticket=++operationSerial;waiting.value=ticket;
    const canRestoreFocus=()=>current(version)&&!busy.value&&!stale.value&&registryRevision===revision&&interactionKey()===interaction&&rows.value.includes(row);
    const canProceed=()=>waiting.value===ticket&&canRestoreFocus();
    try {
      if(!await beforeOperate({owner:row.module_id,action,revision,isCurrent:canProceed,canRestoreFocus})||!canProceed())return;
      if(action==='unload'&&!await confirmation.ask({kind:'unload',owner:row.module_id,title:()=>text('modules.confirm.unload',{owner:labels.value.get(row.module_id)||text('modules.confirm.unknownOwner')}),message:()=>text('modules.confirm.unloadEffect',{revision}),confirmLabel:()=>text('modules.confirm.unloadButton'),isCurrent:canProceed,canRestoreFocus}))return;
      if(!canProceed())return;
    }finally{if(waiting.value===ticket)waiting.value=null;}
    if(!current(version)||busy.value||stale.value||registryRevision!==revision||interactionKey()!==interaction||!rows.value.includes(row))return;
    busy.value=true;stale.value=true;
    try {
      const body={module_id:row.module_id,expected_registry_revision:revision,...(action==='unload'?{}:{enabled:action==='enable'})};
      const result=await bridge.apiPost(action==='unload'?'admin/module-unload':'admin/module-enabled',body);
      if(!current(version))return;
      if(!valid(result)||result.module_id!==row.module_id||!Number.isSafeInteger(result.registry_revision)||(action==='unload'&&(result.state!=='unloaded'||result.data_retained!==true||result.reopen_required!==true)))throw Error('operation_unavailable');
      busy.value=false;const verified=await refresh();if(!current(version))return;if(!verified){notice.show('modules.notice.unverified','warning');return;}onChanged();
      if(current(version))notice.show(action==='enable'?'modules.notice.enabled':action==='unload'?'modules.notice.unloaded':'modules.notice.disabled','warning');
    } catch(error) {if(current(version)){notice.show((error as Error)?.message==='revision_conflict'?'modules.notice.conflict':action==='enable'?'modules.notice.enableUnknown':action==='disable'?'modules.notice.disableUnknown':'modules.notice.unloadUnknown');stale.value=true;}}
    finally {if(current(version))busy.value=false;}
  }
  const app=createApp(defineComponent({setup(){return()=>h(UiProvider,{theme:typeof presentation.value.isDark==='boolean'?(presentation.value.isDark?'dark':'light'):presentation.value.theme==='dark'?'dark':'light',locale:locale(),styleMountTarget:container},{default:()=>renderVisible.value?h('section',{class:'module-management','aria-label':text('modules.title')}, [
    message.value?h(NAlert,{class:'module-management-notice',type:tone.value==='error'?'error':tone.value==='warning'?'warning':tone.value==='success'?'success':'info',showIcon:false},{default:()=>text(message.value)}):null,
    h('div',{class:'module-management-toolbar'},[h(NButton,{disabled:busy.value||!ready,onClick:()=>refresh(true)},()=>busy.value?text('modules.busy'):text('modules.refresh'))]),
    selectedOwner.value===null?installation.render():null,
    !rows.value.length&&!stale.value?h('p',text('modules.empty')):null,...rowGroups().map(group=>h('section',{class:'module-management-group',key:group.key,'aria-label':group.title},[selectedOwner.value?null:h('h2',{class:'module-management-group-title'},group.title),...group.modules.map(({row})=>h('section',{class:'module-management-row',key:row.module_id,'data-module-owner':row.module_id},[
      h('div',{class:'module-management-info'},[h('div',{class:'module-management-identity'},[selectedOwner.value?null:h('h3',labels.value.get(row.module_id)||row.module_id),h('p',{class:'module-status'},`${text(row.enabled?'modules.enabled':'modules.disabled')} · ${text(`modules.lifecycle.${lifecycles.has(row.lifecycle)?row.lifecycle:'unknown'}`)}`)]),h('div',{class:'module-management-metadata'},[home?h('p',{class:'config-readiness'},text(configuration.value[row.module_id]||'modules.configuration.unverified')):null,row.reason_code?h('p',{class:'module-management-warning',role:'status'},row.reason_code==='health_unknown'?text('modules.healthUnknown'):text('modules.healthCheck')):null])]),h('div',{class:'module-management-actions'},[home&&(settingOwners.value.has(row.module_id)||home.hasSettings?.(row.module_id))?h(NButton,{onClick:()=>home.onSettings(row.module_id)},()=> text('modules.openSettings')):null,
      ...(['enable','disable','unload'] as const).map(action=>h(NButton,{disabled:busy.value||waiting.value!==null||stale.value||!ready||(action==='enable'&&row.enabled)||(action==='disable'&&!row.enabled),onClick:()=>operate(row,action)},()=>text(`modules.action.${action}`))),
      ]),
    ]))])),!sharedConfirmation?confirmation.render():null,h('details',{class:'technical-details'},[h('summary',text('modules.advanced')),h('p',text('modules.advancedEffect')),...rows.value.map(row=>h('p',text('modules.technicalRow',{owner:row.module_id,lifecycle:row.lifecycle,health:row.health,revision:registryRevision}))),h('p',{'data-reopen-guidance':''},text('modules.reopen'))]),
  ]):null});}}));app.mount(container);
  function accept(context:Record<string,unknown>) {
    if(closed)return;presentation.value=valid(context)?context:{};let next='';try {if(!valid(context)||context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==expectedPage)throw Error();next=contextBoundary(context);}catch {installation.invalidate();confirmation.cancel();waiting.value=null;ready=false;generation++;rows.value=[];busy.value=false;stale.value=true;notice.show('modules.notice.invalidContext');return;}
    if(ready&&next===boundary)return;installation.invalidate();notice.clear();confirmation.cancel();waiting.value=null;generation++;boundary=next;ready=true;rows.value=[];busy.value=false;void refresh();
  }
  return {start(){off=bridge.onContext(accept);Promise.resolve(bridge.ready()).then(accept).catch(()=>{if(!closed)notice.show('modules.notice.hostUnavailable');});},refresh,invalidateConfiguration,setRenderVisible(value:boolean){if(renderVisible.value!==value){installation.invalidate();confirmation.cancel();}renderVisible.value=value;},setHomeVisible(value:boolean){const opening=value&&!homeVisible;homeVisible=value;if(opening)void refreshConfiguration();},selectOwner(value:string|null){if(selectedOwner.value!==value)installation.invalidate();selectedOwner.value=value;},presentationCatalog(value:ModuleDescriptor[]){descriptors.value=value;},presentationLabels(value:Map<string,string>){labels.value=value;},setLocale(value:string){localeOverride.value=value;},cancelConfirmation(){confirmation.cancel();waiting.value=null;},dispose(){installation.dispose();notice.cancel();confirmation.cancel();waiting.value=null;if(!sharedConfirmation)confirmation.dispose();closed=true;generation++;ready=false;off?.();app.unmount();}};
}
