import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NCard, NButton, NAlert, NConfigProvider, darkTheme, zhCN, enUS} from 'naive-ui';
import {contextBoundary} from './lifecycle';
import {createInlineConfirmation, type InlineConfirmation} from './confirmation';
import type {Bridge} from './contracts';

type Status={module_id:string;enabled:boolean;lifecycle:string;health:string;epoch:number;registry_revision:number;reason_code:string|null};
export type ModuleOperation={owner:string;action:'enable'|'disable'|'unload';revision:number;isCurrent():boolean;canRestoreFocus():boolean};
export function createModuleManagement(container:HTMLElement,bridge:Bridge,window:Window,expectedPage:string,onChanged:()=>void,beforeOperate:(operation:ModuleOperation)=>boolean|Promise<boolean>=()=>true,sharedConfirmation?:InlineConfirmation,interactionKey:()=>string=()=>'' ) {
  const rows=shallowRef<Status[]>([]),message=shallowRef('尚未读取模块管理状态。'),busy=shallowRef(false),stale=shallowRef(true);
  const confirmation=sharedConfirmation??createInlineConfirmation(container.ownerDocument),waiting=shallowRef<number|null>(null);
  const presentation=shallowRef<Record<string,unknown>>({});
  let closed=false,ready=false,generation=0,boundary='',registryRevision=0,operationSerial=0,off:(()=>void)|undefined;
  const valid=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const current=(version:number)=>!closed&&ready&&version===generation;
  function decode(value:unknown) {
    if(!valid(value)||Object.keys(value).sort().join(',')!=='modules,registry_revision'||!Number.isSafeInteger(value.registry_revision)||!Array.isArray(value.modules)||value.modules.length>128)throw Error('invalid_modules');
    const seen=new Set<string>();
    for(const row of value.modules) {
      if(!valid(row)||Object.keys(row).sort().join(',')!=='enabled,epoch,health,lifecycle,module_id,reason_code,registry_revision'||typeof row.module_id!=='string'||!/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(row.module_id)||seen.has(row.module_id)||typeof row.enabled!=='boolean'||!Number.isSafeInteger(row.epoch)||!Number.isSafeInteger(row.registry_revision)||typeof row.lifecycle!=='string'||typeof row.health!=='string'||!(row.reason_code===null||typeof row.reason_code==='string'))throw Error('invalid_modules');
      seen.add(row.module_id);
    }
    return value as unknown as {registry_revision:number;modules:Status[]};
  }
  async function refresh() {
    if(!ready||closed||busy.value)return;
    confirmation.cancel();waiting.value=null;const version=generation;busy.value=true;stale.value=true;
    try {const result=decode(await bridge.apiPost('admin/modules',{}));if(!current(version))return;rows.value=result.modules;registryRevision=result.registry_revision;stale.value=false;message.value='禁用保留设置；解除挂载保留配置、凭据、缓存及业务数据。';}
    catch {if(current(version)){stale.value=true;message.value='模块管理权限或状态无法确认，请重新打开页面后刷新。';}}
    finally {if(current(version))busy.value=false;}
  }
  async function operate(row:Status,action:'enable'|'disable'|'unload') {
    if(!current(generation)||busy.value||stale.value||waiting.value||!rows.value.includes(row))return;
    const version=generation,revision=registryRevision,interaction=interactionKey(),ticket=++operationSerial;waiting.value=ticket;
    const canRestoreFocus=()=>current(version)&&!busy.value&&!stale.value&&registryRevision===revision&&interactionKey()===interaction&&rows.value.includes(row);
    const canProceed=()=>waiting.value===ticket&&canRestoreFocus();
    try {
      if(!await beforeOperate({owner:row.module_id,action,revision,isCurrent:canProceed,canRestoreFocus})||!canProceed())return;
      if(action==='unload'&&!await confirmation.ask({kind:'unload',owner:row.module_id,title:`确认解除挂载 ${row.module_id}`,message:`配置、凭据、缓存及业务数据全部保留。注册版本 ${revision}；确认后需从宿主插件详情正式重开页面。`,confirmLabel:'确认解除挂载（保留数据）',isCurrent:canProceed,canRestoreFocus}))return;
      if(!canProceed())return;
    }finally{if(waiting.value===ticket)waiting.value=null;}
    if(!current(version)||busy.value||stale.value||registryRevision!==revision||interactionKey()!==interaction||!rows.value.includes(row))return;
    busy.value=true;stale.value=true;
    try {
      const body={module_id:row.module_id,expected_registry_revision:revision,...(action==='unload'?{}:{enabled:action==='enable'})};
      const result=await bridge.apiPost(action==='unload'?'admin/module-unload':'admin/module-enabled',body);
      if(!current(version))return;
      if(!valid(result)||result.module_id!==row.module_id||!Number.isSafeInteger(result.registry_revision)||(action==='unload'&&(result.state!=='unloaded'||result.data_retained!==true||result.reopen_required!==true)))throw Error('operation_unavailable');
      busy.value=false;await refresh();if(!current(version))return;onChanged();
      if(current(version))message.value=action==='enable'?'已恢复模块。请从宿主插件详情重新打开页面，以取得新的模块资源授权。':action==='unload'?'模块已解除挂载，持久数据保留。恢复后请从宿主插件详情正式重开页面。':'模块已禁用，设置与持久数据保留。';
    } catch(error) {if(current(version)){message.value=(error as Error)?.message==='revision_conflict'?'模块状态已变化，请刷新后重新核对。':'未确认清理完成；可能仍在排空或清理。请刷新核对，并重试解除挂载。不会创建替代实例。';stale.value=true;}}
    finally {if(current(version))busy.value=false;}
  }
  const app=createApp(defineComponent({setup(){return()=>h(NConfigProvider,{theme:presentation.value.isDark===true||presentation.value.theme==='dark'?darkTheme:null,locale:presentation.value.locale==='en-US'?enUS:zhCN,styleMountTarget:container},{default:()=>h(NCard,{title:'模块生命周期',class:'module-management'}, {default:()=>[
    h(NAlert,{type:stale.value?'warning':'info',showIcon:false},{default:()=>message.value}),
    h(NButton,{disabled:busy.value||!ready,onClick:refresh},()=>busy.value?'正在处理…':'刷新模块管理状态'),
    ...rows.value.map(row=>h('section',{class:'module-management-row',key:row.module_id,'data-module-owner':row.module_id},[
      h('h3',row.module_id),h('p',`${row.enabled?'已启用':'已禁用'} · ${row.lifecycle}${row.reason_code?' · '+row.reason_code:''} · 注册版本 ${registryRevision}`),
      ...(['enable','disable','unload'] as const).map(action=>h(NButton,{disabled:busy.value||waiting.value!==null||stale.value||!ready||(action==='enable'&&row.enabled)||(action==='disable'&&!row.enabled),onClick:()=>operate(row,action)},()=>({enable:'启用 / 恢复',disable:'禁用',unload:'解除挂载（保留数据）'})[action])),
    ])),!sharedConfirmation?confirmation.render():null,h('p',{'data-reopen-guidance':''},'请在宿主左侧点击“插件”，找到 astrbot_plugin_yomihime_game_link，在插件详情中重新打开 monitor 页面，以取得当前资源授权。'),
  ]})});}}));app.mount(container);
  function accept(context:Record<string,unknown>) {
    if(closed)return;presentation.value=valid(context)?context:{};let next='';try {if(!valid(context)||context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==expectedPage)throw Error();next=contextBoundary(context);}catch {confirmation.cancel();waiting.value=null;ready=false;generation++;rows.value=[];busy.value=false;stale.value=true;message.value='管理上下文无效，请正式重开页面。';return;}
    if(ready&&next===boundary)return;confirmation.cancel();waiting.value=null;generation++;boundary=next;ready=true;rows.value=[];busy.value=false;void refresh();
  }
  return {start(){off=bridge.onContext(accept);Promise.resolve(bridge.ready()).then(accept).catch(()=>{message.value='宿主会话不可用，请正式重开页面。';});},refresh,cancelConfirmation(){confirmation.cancel();waiting.value=null;},dispose(){confirmation.cancel();waiting.value=null;if(!sharedConfirmation)confirmation.dispose();closed=true;generation++;ready=false;off?.();app.unmount();}};
}
