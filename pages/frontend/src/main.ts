import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NConfigProvider, NButton, NTag, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
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
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null), settingsRoot=ref<HTMLElement|null>(null), modulesRoot=ref<HTMLElement|null>(null);
  let shell: ReturnType<typeof createShell> | null = null, management:ReturnType<typeof createManagementPage>|null=null, modulesView:ReturnType<typeof createModuleManagement>|null=null, wasSettings=false,lastSettingsOwner:string|null=null;
  const pageName=document.documentElement.dataset.pageName||'shell';
  const confirmation=createInlineConfirmation(document);
  let lastHash=window.location.hash,approvedHash='',contextGeneration=0,contextKey='',contextValid=false,draftGeneration=0,navigationSerial=0,closed=false,offContext:(()=>void)|undefined;
  const interactionKey=()=>JSON.stringify([contextGeneration,contextKey,contextValid,draftGeneration,shell?.state.home,shell?.state.settings,shell?.state.settingsOwner,shell?.state.selected,shell?.state.catalog?.catalog_revision,shell?.state.busy,shell?.state.stale,window.location.hash]);
  const navigate=async(action:()=>void)=>{
    const serial=++navigationSerial,snapshot=interactionKey();confirmation.cancel();
    const current=()=>!closed&&contextValid&&serial===navigationSerial&&snapshot===interactionKey();
    if(shell?.state.settings&&management?.dirty()&&!await confirmation.ask({kind:'navigation',title:'离开当前设置？',message:'保留普通配置草稿并离开设置？取消可继续编辑。秘密输入将清空。',confirmLabel:'保留普通草稿并离开',isCurrent:current}))return;
    if(!current())return;action();lastHash=window.location.hash;
  };
  const guardHash=(event:HashChangeEvent)=>{
    const requested=window.location.hash;if(requested===approvedHash){approvedHash='';lastHash=requested;return;}if(requested===lastHash)return;
    if(wasSettings&&management?.dirty()){
      event.stopImmediatePropagation();window.history.replaceState(null,'',lastHash||'#/');
      void navigate(()=>{approvedHash=requested;window.location.hash=requested;});return;
    }confirmation.cancel();lastHash=requested;
  };
  const draftEdited=()=>{draftGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();};
  function changed(){confirmation.invalidate();revision.value++;const settings=shell?.state.settings||false;
    if(shell?.state.catalog&&(settings||shell.state.home)&&!modulesView&&modulesRoot.value){modulesView=createModuleManagement(modulesRoot.value,window.AstrBotPluginPage,window,pageName,()=>{void shell?.refresh();management?.resume();},operation=>{const snapshot=interactionKey();return !management?.dirty()||confirmation.ask({kind:'module-draft',owner:operation.owner,title:'确认模块操作 '+operation.owner,message:`存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。注册版本 ${operation.revision}。`,confirmLabel:'保留草稿并继续核对',isCurrent:()=>snapshot===interactionKey()&&operation.isCurrent(),canRestoreFocus:()=>snapshot===interactionKey()&&operation.canRestoreFocus()});},confirmation,interactionKey,{onSettings:owner=>void navigate(()=>shell?.selectSettings(owner)),labels:()=>moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'),catalog:configurationCatalog,snapshot:configurationSnapshot});modulesView.start();}
    modulesView?.presentationLabels(moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'));
    modulesView?.setHomeVisible(Boolean(shell?.state.home));
    if(settings&&!management&&settingsRoot.value&&modulesRoot.value){management=createManagementPage(document,window.AstrBotPluginPage,window,{expectedPage:pageName,container:settingsRoot.value,onDraftMutation:draftEdited,onConfigurationSaved:()=>modulesView?.invalidateConfiguration()});management.start();}
    else if(settings&&!wasSettings){management?.resume();}
    else if(settings&&wasSettings&&lastSettingsOwner!==shell?.state.settingsOwner){management?.suspend();management?.resume();}
    else if(!settings&&wasSettings)management?.suspend();lastSettingsOwner=shell?.state.settingsOwner||null;if(settings)management?.selectOwner(shell?.state.settingsOwner||'game_link/core');wasSettings=settings;
  }
  onMounted(() => {offContext=window.AstrBotPluginPage?.onContext(context=>{let next='';try{if(context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==pageName)throw Error();next=contextBoundary(context);}catch{contextValid=false;contextGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();return;}if(next!==contextKey){contextGeneration++;contextKey=next;confirmation.cancel();modulesView?.cancelConfirmation();}contextValid=true;});window.addEventListener('hashchange',guardHash);shell = createShell({bridge: window.AstrBotPluginPage, container: root.value!, window, loadPage, expectedPage: document.documentElement.dataset.pageName || 'shell', changed}); shell.start();});
  onBeforeUnmount(() => {closed=true;contextGeneration++;confirmation.dispose();offContext?.();window.removeEventListener('hashchange',guardHash);management?.dispose();modulesView?.dispose();shell?.dispose();});
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = state?.catalog?.modules || [];
    const labels=moduleLabels(modules,state?.locale||'zh-CN'),label=(owner:string)=>labels.get(owner)||owner;
    const pages = modules.flatMap(module => module.pages.map(page => ({module,page})));
    const busy = state?.busy || false,home=state?.home??true;
    const currentModule=modules.find(m=>m.module_id===(state?.settingsOwner||state?.selected?.owner));
    const go=(action:()=>void)=>(event:Event)=>{event.preventDefault();void navigate(action);};
    const link=(text:string,href:string,current:boolean,action:()=>void)=>h('a',{href,'aria-current':current?'page':undefined,onClick:go(action)},text);
    const moduleSelect = (id:string)=>h('div',{class:'shell-field'},[h('label',{for:id},'模块'),h('select',{id,value:currentModule?.module_id||'',disabled:!modules.length,onChange:(event:Event)=>{const owner=(event.target as HTMLSelectElement).value;if(owner)void navigate(()=>shell?.selectSettings(owner));}},[h('option',{value:''},'选择模块'),...modules.map(module=>h('option',{value:module.module_id},label(module.module_id)))])]);
    return h(NConfigProvider,{preflightStyleDisabled:true,theme: state?.theme === 'dark' ? darkTheme : null,locale:state?.locale.startsWith('en')?enUS:zhCN,dateLocale:state?.locale.startsWith('en')?dateEnUS:dateZhCN,inlineThemeDisabled:true}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},'跳至正文'),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':'模块导航'},[
          h('p',{class:'shell-brand'},'游戏连结'),h('p',{class:'shell-eyebrow'},'如月怜'),
          h('nav',{'aria-label':'主要导航'},[link('首页','#/',home,()=>shell?.selectHome())]),
          h('p',{class:'nav-section-label'},'模块'),h('nav',{'aria-label':'模块设置'},modules.map(module=>link(label(module.module_id),'#/settings/module/'+encodeURIComponent(module.module_id),Boolean(state?.settings&&state.settingsOwner===module.module_id),()=>shell?.selectSettings(module.module_id)))),
          h('details',{class:'query-navigation'},[h('summary','辅助查询'),h('nav',{'aria-label':'模块页面'},pages.map(({module,page})=>link(label(module.module_id)+' · '+page.title,`#/module/${encodeURIComponent(module.module_id)}/${page.route_id}`,Boolean(state?.selected?.owner===module.module_id&&state.selected.route===page.route_id),()=>shell?.select(module.module_id,page.route_id))))]),
          h('div',{class:'shell-settings-slot'},[h('button',{class:'shell-settings',type:'button','aria-current':state?.settings&&state.settingsOwner===null?'page':undefined,onClick:()=>void navigate(()=>shell?.selectSettings())},'全局设置')]),
        ]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},'游戏连结'),h('nav',{'aria-label':'移动导航'},[link('首页','#/',home,()=>shell?.selectHome()),link('全局设置','#/settings',Boolean(state?.settings&&state.settingsOwner===null),()=>shell?.selectSettings())]),moduleSelect('module-select-mobile'),h('details',[h('summary','辅助查询'),...pages.map(({module,page})=>link(label(module.module_id)+' · '+page.title,`#/module/${encodeURIComponent(module.module_id)}/${page.route_id}`,Boolean(state?.selected?.owner===module.module_id&&state.selected.route===page.route_id),()=>shell?.select(module.module_id,page.route_id)))])]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h('header',{class:'shell-heading'},[h('div',[h('p',{class:'shell-eyebrow'},state?.settings?state.settingsOwner?label(state.settingsOwner):'全局':selection?label(selection.module.module_id):'管理概览'),h('h1',{id:'page-title'},state?.settings?'设置':home?'首页':selection?.page.title||'页面不可用')]),h(NButton,{onClick:()=>{if(!shell?.state.busy){void shell?.refresh();if(home)void modulesView?.refresh();}},'aria-disabled':String(busy),'aria-busy':String(busy),'aria-label':'刷新模块状态'},()=>busy?'正在刷新…':'刷新状态')]),
            h('div',{class:'shell-status',role:'status','aria-live':'polite'},[h(NTag,{type:state?.stale?'warning':'default',size:'small'},()=>state?.stale?'上次状态':busy?'读取中':'状态'),h('span',state?.message || '正在准备页面。')]),
            state?.cleanupPending ? h(NButton,{onClick:()=>shell?.retryCleanup()},()=> '重试页面清理') : null,
            confirmation.render(),h('div',{ref:root,id:'module-container',hidden:state?.settings||home}),
            h('div',{class:'settings-container',hidden:!state?.settings},[h('div',{ref:settingsRoot,id:'settings-container'})]),
            h('details',{class:'module-dashboard',open:home,hidden:!home&&!state?.settings},[h('summary',home?'模块':'高级模块管理'),h('div',{ref:modulesRoot,id:'module-management-container'})]),
            !home&&!state?.settings&&!state?.mounted ? h('p',{class:'shell-empty'},state?.cleanupPending?'页面清理未完成，请重新打开页面。':selection?'模块页面正在准备。':'此路由当前没有可用的注册页面。') : null,
          ]),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
