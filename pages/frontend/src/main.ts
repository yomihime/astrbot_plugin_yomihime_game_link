import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NConfigProvider, NButton, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
import {PageHeader, SettingsGroup, StatusHint} from './ui/components';
import {loadPage} from './module-loader.js';
import {createShell} from './controller';
import type {Bridge} from './contracts';
import './shell.css';
import {skipToMain} from './navigation';
import {createManagementPage,validateCatalog as configurationCatalog,validateSnapshot as configurationSnapshot} from './management.js';
import {createModuleManagement} from './module-management';
import {createInlineConfirmation} from './confirmation';
import {contextBoundary} from './lifecycle';
import {moduleLabels} from './catalog';
declare global {interface Window {AstrBotPluginPage: Bridge}}
const App = defineComponent({setup() {
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null), settingsRoot=ref<HTMLElement|null>(null), modulesRoot=ref<HTMLElement|null>(null),maintenanceRoot=ref<HTMLElement|null>(null),maintenanceOpen=ref(false);
  let maintenance:ReturnType<typeof createManagementPage>|null=null;
  let shell: ReturnType<typeof createShell> | null = null, management:ReturnType<typeof createManagementPage>|null=null, modulesView:ReturnType<typeof createModuleManagement>|null=null, wasSettings=false,lastSettingsOwner:string|null=null;
  const pageName=document.documentElement.dataset.pageName||'shell';
  const configurationOwners=ref<string[]|null>(null),configurationComplete=ref(false);
  const confirmation=createInlineConfirmation(document);
  let lastHash=window.location.hash,approvedHash='',contextGeneration=0,contextKey='',contextValid=false,draftGeneration=0,navigationSerial=0,closed=false,offContext:(()=>void)|undefined;
  const interactionKey=()=>JSON.stringify([contextGeneration,contextKey,contextValid,draftGeneration,maintenanceOpen.value,shell?.state.home,shell?.state.settings,shell?.state.settingsOwner,shell?.state.selected,shell?.state.catalog?.catalog_revision,shell?.state.busy,shell?.state.stale,window.location.hash]);
  const closeMaintenance=()=>{if(!maintenance&&!maintenanceOpen.value)return;confirmation.cancel();maintenance?.dispose();maintenance=null;maintenanceOpen.value=false;};
  const openMaintenance=()=>{if(maintenance||!maintenanceRoot.value||!contextValid)return;maintenanceOpen.value=true;maintenance=createManagementPage(document,window.AstrBotPluginPage,window,{expectedPage:pageName,container:maintenanceRoot.value,scope:'maintenance',onDraftMutation:draftEdited,onConfigurationSaved:()=>{management?.resume();modulesView?.invalidateConfiguration();},beforeMaintenance:operation=>{const snapshot=interactionKey();return confirmation.ask({kind:'maintenance',title:operation.action==='rollback'?'确认回退受审迁移？':'确认重新校验并恢复？',message:(management?.dirty()?'存在未保存草稿；维护后仍需重新核对。':'')+'仅处理受审普通配置，其他持久数据保留。',confirmLabel:'确认维护',isCurrent:()=>contextValid&&!closed&&maintenanceOpen.value&&snapshot===interactionKey()&&operation.isCurrent()});}});maintenance.start();};
  const navigate=async(action:()=>void)=>{
    const serial=++navigationSerial,snapshot=interactionKey();confirmation.cancel();
    const current=()=>!closed&&contextValid&&serial===navigationSerial&&snapshot===interactionKey();
    const impact=shell?.state.settings?management?.navigationImpact():null;
    if(impact?.requiresConfirmation){
      const labels=moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN');
      const owners=(ids:string[])=>ids.map(owner=>owner==='game_link/core'?'全局设置':labels.get(owner)||owner).join('、');
      const message=[impact.ordinaryOwners.length?`${owners(impact.ordinaryOwners)}的普通设置草稿将保留。`:'',impact.credentialOwners.length?`${owners(impact.credentialOwners)}的凭据输入及操作草稿将清空。`:''].join('');
      if(!await confirmation.ask({kind:'navigation',title:'离开设置？',message,confirmLabel:'确认离开',isCurrent:current}))return;
    }
    if(!current())return;closeMaintenance();action();lastHash=window.location.hash;
  };
  const guardHash=(event:HashChangeEvent)=>{
    const requested=window.location.hash;if(requested===approvedHash){approvedHash='';lastHash=requested;return;}if(requested===lastHash)return;
    if(wasSettings&&management?.navigationImpact().requiresConfirmation){
      event.stopImmediatePropagation();window.history.replaceState(null,'',lastHash||'#/');
      void navigate(()=>{approvedHash=requested;window.location.hash=requested;});return;
    }confirmation.cancel();closeMaintenance();lastHash=requested;
  };
  const draftEdited=()=>{draftGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();};
  function changed(){confirmation.invalidate();revision.value++;const settings=shell?.state.settings||false;
    const showModules=Boolean(shell?.state.home||shell?.state.moduleSection==='home');
    if(!showModules)closeMaintenance();
    if(shell?.state.catalog&&!modulesView&&modulesRoot.value){modulesView=createModuleManagement(modulesRoot.value,window.AstrBotPluginPage,window,pageName,()=>{void shell?.refresh();management?.resume();},operation=>{const snapshot=interactionKey();return !management?.dirty()||confirmation.ask({kind:'module-draft',owner:operation.owner,title:'确认模块操作 '+operation.owner,message:`存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。注册版本 ${operation.revision}。`,confirmLabel:'保留草稿并继续核对',isCurrent:()=>snapshot===interactionKey()&&operation.isCurrent(),canRestoreFocus:()=>snapshot===interactionKey()&&operation.canRestoreFocus()});},confirmation,interactionKey,{hasSettings:owner=>configurationOwners.value?.includes(owner)||false,onSettings:owner=>void navigate(()=>shell?.selectSettings(owner)),labels:()=>moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'),catalog:configurationCatalog,snapshot:configurationSnapshot});modulesView.start();}
    modulesView?.setRenderVisible(showModules);
    modulesView?.presentationLabels(moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'));
    modulesView?.setHomeVisible(Boolean(shell?.state.home||shell?.state.moduleSection==='home'));
    modulesView?.selectOwner(shell?.state.moduleOwner||null);
    if(shell?.state.catalog&&(settings||shell.state.moduleOwner)&&!management&&settingsRoot.value&&modulesRoot.value){management=createManagementPage(document,window.AstrBotPluginPage,window,{expectedPage:pageName,container:settingsRoot.value,onDraftMutation:draftEdited,onConfigurationSaved:()=>modulesView?.invalidateConfiguration(),onCapabilities:(owners:string[]|null,complete=false)=>{configurationOwners.value=owners;configurationComplete.value=complete;}});management.start();}
    else if(settings&&!wasSettings){management?.resume();}
    else if(settings&&wasSettings&&lastSettingsOwner!==shell?.state.settingsOwner){management?.suspend();management?.resume();}
    else if(!settings&&wasSettings)management?.suspend();lastSettingsOwner=shell?.state.settingsOwner||null;if(settings)management?.selectOwner(shell?.state.settingsOwner||'game_link/core');wasSettings=settings;
  }
  onMounted(() => {offContext=window.AstrBotPluginPage?.onContext(context=>{let next='';try{if(context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==pageName)throw Error();next=contextBoundary(context);}catch{contextValid=false;contextGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();return;}if(next!==contextKey){contextGeneration++;contextKey=next;confirmation.cancel();modulesView?.cancelConfirmation();}contextValid=true;});window.addEventListener('hashchange',guardHash);shell = createShell({bridge: window.AstrBotPluginPage, container: root.value!, window, loadPage, expectedPage: document.documentElement.dataset.pageName || 'shell', changed}); shell.start();});
  onBeforeUnmount(() => {closed=true;contextGeneration++;closeMaintenance();confirmation.dispose();offContext?.();window.removeEventListener('hashchange',guardHash);management?.dispose();modulesView?.dispose();shell?.dispose();});
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = state?.catalog?.modules || [];
    const labels=moduleLabels(modules,state?.locale||'zh-CN'),label=(owner:string)=>labels.get(owner)||owner;
    const busy = state?.busy || false,home=state?.home??true;
    const currentModule=modules.find(m=>m.module_id===state?.moduleOwner),moduleHome=Boolean(currentModule&&state?.moduleSection==='home'),queries=Boolean(currentModule&&(state?.moduleSection==='queries'||selection));
    const go=(action:()=>void)=>(event:Event)=>{event.preventDefault();void navigate(action);};
    const link=(text:string,href:string,current:boolean,action:()=>void)=>h('a',{href,'aria-current':current?'page':undefined,onClick:go(action)},text);
    const moduleSelect = (id:string)=>h('div',{class:'shell-field'},[h('label',{for:id},'模块'),h('select',{id,value:currentModule?.module_id||'',disabled:!modules.length,onChange:(event:Event)=>{const owner=(event.target as HTMLSelectElement).value;if(owner)void navigate(()=>shell?.selectModule(owner));}},[h('option',{value:''},'选择模块'),...modules.map(module=>h('option',{value:module.module_id},label(module.module_id)))])]);
    return h(NConfigProvider,{preflightStyleDisabled:true,theme: state?.theme === 'dark' ? darkTheme : null,locale:state?.locale.startsWith('en')?enUS:zhCN,dateLocale:state?.locale.startsWith('en')?dateEnUS:dateZhCN,inlineThemeDisabled:true}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},'跳至正文'),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':'模块导航'},[
          h('p',{class:'shell-brand'},'如月怜的游戏连结'),
          h('nav',{'aria-label':'主要导航'},[link('首页','#/',home,()=>shell?.selectHome())]),
          h('nav',{'aria-label':'已挂载模块'},modules.map(module=>link(label(module.module_id),'#/module/'+encodeURIComponent(module.module_id),state?.moduleOwner===module.module_id,()=>shell?.selectModule(module.module_id)))),
          h('div',{class:'shell-settings-slot'},[h('button',{class:'shell-settings',type:'button','aria-current':state?.settings&&state.settingsOwner===null?'page':undefined,onClick:()=>void navigate(()=>shell?.selectSettings())},'全局设置')]),
        ]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},'如月怜的游戏连结'),h('nav',{'aria-label':'移动导航'},[link('首页','#/',home,()=>shell?.selectHome()),link('全局设置','#/settings',Boolean(state?.settings&&state.settingsOwner===null),()=>shell?.selectSettings())]),moduleSelect('module-select-mobile')]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h(PageHeader,{class:'shell-heading',titleId:'page-title',title:currentModule?label(currentModule.module_id):state?.settings&&state.settingsOwner===null?'全局设置':home?'首页':'页面不可用'},{default:()=>h(NButton,{onClick:()=>{if(!shell?.state.busy){void shell?.refresh();if(home||moduleHome)void modulesView?.refresh(true);}},'aria-disabled':String(busy),'aria-busy':String(busy),'aria-label':'刷新模块状态'},()=>busy?'正在刷新…':'刷新模块状态')}),
            currentModule?h('nav',{class:'module-tabs','aria-label':'模块内分页'},[
              link('主页','#/module/'+encodeURIComponent(currentModule.module_id),moduleHome,()=>shell?.selectModule(currentModule.module_id)),
              configurationOwners.value?.includes(currentModule.module_id)?link('配置','#/settings/module/'+encodeURIComponent(currentModule.module_id),Boolean(state?.settings),()=>shell?.selectSettings(currentModule.module_id)):null,
              currentModule.pages.length?link('辅助查询','#/module/'+encodeURIComponent(currentModule.module_id)+'/-/queries',queries,()=>shell?.selectModule(currentModule.module_id,'queries')):null,
            ]):null,
            h(StatusHint,{class:'shell-status',hidden:!busy&&!state?.stale&&state?.message==='状态已更新；查询仅在手动提交时执行。',tone:state?.stale?'warning':'neutral'},{default:()=>state?.message||'正在准备页面。'}),
            currentModule&&!configurationComplete.value&&!configurationOwners.value?.includes(currentModule.module_id)?h(StatusHint,{}, {default:()=> ['配置能力尚未核对。',h('button',{type:'button',onClick:()=>management?.resume()},'重试读取配置能力')]}):null,
            state?.cleanupPending ? h(NButton,{onClick:()=>shell?.retryCleanup()},()=> '重试页面清理') : null,
            confirmation.render(),
            queries?h('nav',{class:'query-links','aria-label':'辅助查询入口'},currentModule!.pages.map(page=>link(page.title,`#/module/${encodeURIComponent(currentModule!.module_id)}/${page.route_id}`,state?.selected?.route===page.route_id,()=>shell?.select(currentModule!.module_id,page.route_id)))):null,
            moduleHome&&currentModule!.pages.length?h(SettingsGroup,{title:'可用功能'},{default:()=>h('div',{class:'module-capabilities'},currentModule!.pages.map(page=>h('span',{key:page.route_id},page.title)))}):null,
            queries&&!selection?h('p',{class:'shell-empty'},'选择一项辅助查询；仅在手动提交时请求数据。'):null,
            h('div',{ref:root,id:'module-container',hidden:!selection}),
            h('div',{class:'settings-container',hidden:!state?.settings||Boolean(state.settingsOwner&&!currentModule)},[h('div',{ref:settingsRoot,id:'settings-container'})]),
            h('div',{class:'module-dashboard',hidden:!home&&!moduleHome},[h('div',{ref:modulesRoot,id:'module-management-container',hidden:maintenanceOpen.value}),home||moduleHome?h(NButton,{size:'large',onClick:maintenanceOpen.value?closeMaintenance:openMaintenance},()=>maintenanceOpen.value?'关闭配置维护':'配置维护'):null]),
            h('div',{ref:maintenanceRoot,id:'maintenance-container',hidden:!maintenanceOpen.value}),
            !home&&!state?.settings&&!moduleHome&&!queries&&!state?.mounted||Boolean(state?.settingsOwner&&!currentModule)?h('p',{class:'shell-empty'},state?.cleanupPending?'页面清理未完成，请重新打开页面。':selection?'模块页面正在准备。':'此路由当前没有可用的注册页面。恢复模块后请从宿主插件详情正式重开。'):null,
          ]),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
