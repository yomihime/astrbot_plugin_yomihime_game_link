import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NButton} from 'naive-ui';
import {UiProvider,LogoMark} from '@game-link/ui';
import {PageHeader, SettingsGroup, StatusHint} from '@game-link/ui';
import {loadPage} from './module-loader.js';
import {createShell} from './controller';
import type {Bridge} from './contracts';
import './shell.css';
import {skipToMain} from './navigation';
import {createManagementPage,validateCatalog as configurationCatalog,validateSnapshot as configurationSnapshot} from './management.js';
import {createModuleManagement} from './module-management';
import {createInlineConfirmation} from './confirmation';
import {contextBoundary} from './lifecycle';
import {moduleLabels,moduleGroups} from './catalog';
declare global {interface Window {AstrBotPluginView: Bridge}}
const App = defineComponent({setup() {
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null), settingsRoot=ref<HTMLElement|null>(null), modulesRoot=ref<HTMLElement|null>(null),maintenanceRoot=ref<HTMLElement|null>(null),maintenanceOpen=ref(false);
  let maintenance:ReturnType<typeof createManagementPage>|null=null;
  let shell: ReturnType<typeof createShell> | null = null, management:ReturnType<typeof createManagementPage>|null=null, modulesView:ReturnType<typeof createModuleManagement>|null=null, wasSettings=false,lastSettingsOwner:string|null=null;
  const pageName=document.documentElement.dataset.pageName||'00-game-link';

  const confirmation=createInlineConfirmation(document);
  let lastHash=window.location.hash,approvedHash='',contextGeneration=0,contextKey='',contextValid=false,draftGeneration=0,navigationSerial=0,closed=false,offContext:(()=>void)|undefined;
  const interactionKey=()=>JSON.stringify([contextGeneration,contextKey,contextValid,draftGeneration,maintenanceOpen.value,shell?.state.home,shell?.state.settings,shell?.state.settingsOwner,shell?.state.selected,shell?.state.catalog?.catalog_revision,shell?.state.busy,shell?.state.stale,shell?.navigationImpact(),window.location.hash]);
  const navigationImpact=()=>{const global=shell?.state.settings?management?.navigationImpact():null,local=shell?.navigationImpact();return {ordinaryOwners:[...(global?.ordinaryOwners||[]),...(local?.ordinary?[local.owner]:[])],credentialOwners:[...(global?.credentialOwners||[]),...(local?.credentials?[local.owner]:[])],pending:local?.pending||false,blocked:local?.blocked||false,requiresConfirmation:Boolean(global?.requiresConfirmation||local?.ordinary||local?.credentials||local?.pending)};};
  const closeMaintenance=()=>{if(!maintenance&&!maintenanceOpen.value)return;confirmation.cancel();maintenance?.dispose();maintenance=null;maintenanceOpen.value=false;};
  const openMaintenance=()=>{if(maintenance||!maintenanceRoot.value||!contextValid)return;maintenanceOpen.value=true;maintenance=createManagementPage(document,window.AstrBotPluginView,window,{expectedPage:pageName,container:maintenanceRoot.value,scope:'maintenance',onDraftMutation:draftEdited,onConfigurationSaved:()=>{management?.resume();modulesView?.invalidateConfiguration();},beforeMaintenance:operation=>{const snapshot=interactionKey();return confirmation.ask({kind:'maintenance',title:'确认重新校验并恢复？',message:(management?.dirty()?'存在未保存草稿；维护后仍需重新核对。':'')+'仅处理受审普通配置，其他持久数据保留。',confirmLabel:'确认维护',isCurrent:()=>contextValid&&!closed&&maintenanceOpen.value&&snapshot===interactionKey()&&operation.isCurrent()});}});maintenance.start();};
  const navigate=async(action:()=>void)=>{
    const serial=++navigationSerial,snapshot=interactionKey();confirmation.cancel();
    const current=()=>!closed&&contextValid&&serial===navigationSerial&&snapshot===interactionKey();
    const impact=navigationImpact();if(impact.blocked){shell?.refuseNavigation();return;}
    if(impact.requiresConfirmation){
      const labels=moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN');
      const owners=(ids:string[])=>ids.map(owner=>owner==='game_link/core'?'全局设置':labels.get(owner)||owner).join('、');
      const message=[impact.ordinaryOwners.length?`${owners(impact.ordinaryOwners)}的普通设置草稿将保留。`:'',impact.credentialOwners.length?`${owners(impact.credentialOwners)}的凭据输入及操作草稿将清空。`:'',impact.pending?'有操作仍在等待；离开不能撤回已发出的请求，结果未确认时请重新读取，不会自动重试。':''].join('');
      if(!await confirmation.ask({kind:'navigation',title:'离开设置？',message,confirmLabel:'确认离开',isCurrent:current}))return;
    }
    if(!current())return;closeMaintenance();action();lastHash=window.location.hash;
  };
  const guardHash=(event:HashChangeEvent)=>{
    const requested=window.location.hash;if(requested===approvedHash){approvedHash='';lastHash=requested;return;}if(requested===lastHash)return;
    const impact=navigationImpact();
    if(impact.blocked){event.stopImmediatePropagation();window.history.replaceState(null,'',lastHash||'#/');confirmation.cancel();shell?.refuseNavigation();return;}
    if(impact.requiresConfirmation){
      event.stopImmediatePropagation();window.history.replaceState(null,'',lastHash||'#/');
      void navigate(()=>{approvedHash=requested;window.location.hash=requested;});return;
    }confirmation.cancel();closeMaintenance();lastHash=requested;
  };
  const draftEdited=()=>{draftGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();};
  function changed(){confirmation.invalidate();revision.value++;const settings=shell?.state.settings||false;
    const showModules=Boolean(shell?.state.modules);
    if(!showModules)closeMaintenance();
    if(shell?.state.catalog&&!modulesView&&modulesRoot.value){modulesView=createModuleManagement(modulesRoot.value,window.AstrBotPluginView,window,pageName,()=>{void shell?.refresh();management?.resume();},operation=>{const snapshot=interactionKey(),impact=navigationImpact();if(impact.blocked){shell?.refuseNavigation();return false;}return !management?.dirty()&&!impact.requiresConfirmation||confirmation.ask({kind:'module-draft',owner:operation.owner,title:'确认模块操作 '+operation.owner,message:`存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。${impact.credentialOwners.length?'当前页面凭据输入将在解除绑定时清空。':''}${impact.pending?'等待中的请求不能撤回，结果未确认时请重新读取，不会自动重试。':''}注册版本 ${operation.revision}。`,confirmLabel:'保留草稿并继续核对',isCurrent:()=>snapshot===interactionKey()&&operation.isCurrent(),canRestoreFocus:()=>snapshot===interactionKey()&&operation.canRestoreFocus()});},confirmation,interactionKey,{hasSettings:owner=>Boolean(shell?.state.catalog?.modules?.find(module=>module.module_id===owner)?.pages.some(page=>page.role==='config')),onSettings:owner=>void navigate(()=>shell?.selectSettings(owner)),labels:()=>moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'),catalog:configurationCatalog,snapshot:configurationSnapshot});modulesView.start();}
    modulesView?.setRenderVisible(showModules);
    modulesView?.presentationLabels(moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'));
    modulesView?.setHomeVisible(Boolean(shell?.state.modules));
    modulesView?.selectOwner(shell?.state.moduleOwner||null);
    if(shell?.state.catalog&&settings&&!management&&settingsRoot.value&&modulesRoot.value){management=createManagementPage(document,window.AstrBotPluginView,window,{expectedPage:pageName,container:settingsRoot.value,initiallySuspended:!settings,onDraftMutation:draftEdited,onConfigurationSaved:()=>modulesView?.invalidateConfiguration()});management.start();}
    else if(settings&&!wasSettings){management?.resume();}
    else if(settings&&wasSettings&&lastSettingsOwner!==shell?.state.settingsOwner){management?.suspend();management?.resume();}
    else if(!settings&&wasSettings)management?.suspend();lastSettingsOwner=shell?.state.settingsOwner||null;if(settings)management?.selectOwner();management?.setLegacyPolicies(shell?.state.catalog?.legacy_config_policies||[]);wasSettings=settings;
  }
  onMounted(() => {offContext=window.AstrBotPluginView?.onContext(context=>{let next='';try{if(context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==pageName)throw Error();next=contextBoundary(context);}catch{contextValid=false;contextGeneration++;confirmation.cancel();modulesView?.cancelConfirmation();return;}if(next!==contextKey){contextGeneration++;contextKey=next;confirmation.cancel();modulesView?.cancelConfirmation();}contextValid=true;});window.addEventListener('hashchange',guardHash);shell = createShell({bridge: window.AstrBotPluginView, container: root.value!, window, loadPage, expectedPage: '00-game-link', confirmClear:(owner,field,revision,isCurrent)=>confirmation.ask({kind:'credential-clear',owner,title:'清除模块凭据？',message:owner+' 的 '+field+' 已保存凭据将失效；普通配置保留。版本 '+revision+'。',confirmLabel:'确认清除',isCurrent}), changed}); shell.start();});
  onBeforeUnmount(() => {closed=true;contextGeneration++;closeMaintenance();confirmation.dispose();offContext?.();window.removeEventListener('hashchange',guardHash);management?.dispose();modulesView?.dispose();shell?.dispose();});
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = state?.catalog?.modules || [];
    const labels=moduleLabels(modules,state?.locale||'zh-CN'),label=(owner:string)=>labels.get(owner)||owner;
    const busy = state?.busy || false,home=state?.home??true;
    const currentModule=modules.find(m=>m.module_id===state?.moduleOwner),moduleHome=Boolean(currentModule&&state?.moduleSection==='home'),queries=Boolean(currentModule&&state?.moduleSection==='queries');
    const go=(action:()=>void)=>(event:Event)=>{event.preventDefault();void navigate(action);};
    const link=(text:string,href:string,current:boolean,action:()=>void,description?:string)=>h('a',{href,'aria-current':current?'page':undefined,'aria-describedby':description,onClick:go(action)},text);
    const title=currentModule?(state?.moduleSection==='config'?'业务配置':label(currentModule.module_id)):state?.settings?'全局设置':state?.modules?'模块管理':home?'游戏工作台':'页面不可用';
    const icon=(kind:'home'|'grid'|'game'|'settings'|'arrow'|'page')=>h('svg',{class:'shell-icon',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.5,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},[h('path',{d:{home:'M3 10 12 3l9 7v11h-6v-8H9v8H3Z',grid:'M3 3h7v7H3Zm11 0h7v7h-7ZM3 14h7v7H3Zm11 0h7v7h-7Z',game:'M7 7h10c3 0 5 10 3 11-2 1-4-3-5-3H9c-1 0-3 4-5 3-2-1 0-11 3-11Zm0 3v4m-2-2h4m7-1h.01m2 3h.01',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8',arrow:'M5 12h14m-5-5 5 5-5 5',page:'M5 3h10l4 4v14H5Zm9 0v5h5M8 12h8m-8 4h6'}[kind]})]);
    const railLink=(text:string,href:string,current:boolean,action:()=>void,kind:'home'|'grid'|'game')=>h('a',{href,'aria-current':current?'page':undefined,onClick:go(action)},[icon(kind),h('span',text)]);
    const workspaceStatus=(owner:string,state:string)=>h('span',{class:'workspace-status-icon','data-state':state},[
      h('svg',{viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.7,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},state==='unavailable'?[h('path',{d:'m12 3 10 18H2Z'}),h('path',{d:'M12 9v5m0 3h.01'})]:[h('circle',{cx:12,cy:12,r:9}),h('path',{d:state==='loaded'?'m8 12 3 3 5-6':'M9 8v8m6-8v8'})]),
      h('span',{class:'workspace-status-tooltip',id:'workspace-status-'+encodeURIComponent(owner),role:'tooltip'},state==='loaded'?'已加载':state==='disabled'?'已停用':'不可用'),
    ]);
    const workspaceLink=(text:string,href:string,action:()=>void,kind:'settings'|'grid')=>h('a',{class:'workspace-management-link',href,onClick:go(action)},[h('span',{class:'workspace-management-icon'},[icon(kind)]),h('span',[h('strong',text)]),icon('arrow')]);
    const moduleSelect = (id:string)=>h('div',{class:'shell-field'},[h('label',{for:id},'模块'),h('select',{id,value:currentModule?.module_id||'',disabled:!modules.length,onChange:(event:Event)=>{const owner=(event.target as HTMLSelectElement).value;if(owner)void navigate(()=>shell?.selectModule(owner));}},[h('option',{value:''},'选择模块'),...modules.map(module=>h('option',{value:module.module_id},label(module.module_id)))])]);
    return h(UiProvider,{theme:state?.theme||'light',locale:state?.locale||'zh-CN',styleMountTarget:document.head}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},'跳至正文'),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':'模块导航'},[
          h('p',{class:'shell-brand'},[h('span',{class:'shell-brand-mark','aria-hidden':'true'},'☾'),h('span',[h('span',{class:'shell-brand-line'},'如月怜的'),h('span',{class:'shell-brand-line'},'游戏连结')])]),
          h('nav',{'aria-label':'主要导航'},[railLink('首页','#/',home,()=>shell?.selectHome(),'home'),railLink('模块管理','#/modules',Boolean(state?.modules),()=>shell?.selectModules(),'grid')]),
          h('p',{class:'shell-nav-label'},'游戏模块'),
          h('nav',{'aria-label':'已挂载模块'},modules.map(module=>railLink(label(module.module_id),'#/module/'+encodeURIComponent(module.module_id),state?.moduleOwner===module.module_id,()=>shell?.selectModule(module.module_id),'game'))),
          h('div',{class:'shell-settings-slot'},[h('button',{class:'shell-settings',type:'button','aria-current':state?.settings&&state.settingsOwner===null?'page':undefined,onClick:()=>void navigate(()=>shell?.selectSettings())},[icon('settings'),h('span','全局设置')]),h('p',{class:'shell-note'},['GAME LINK',h('br'),'如月之下，游戏之间。'])]),
        ]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-topbar'},[h('span','游戏连结'),h('span',{'aria-hidden':'true'},'/'),h('span',title)]),
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},'如月怜的游戏连结'),h('nav',{'aria-label':'移动导航'},[link('首页','#/',home,()=>shell?.selectHome()),link('模块管理','#/modules',Boolean(state?.modules),()=>shell?.selectModules()),link('全局设置','#/settings',Boolean(state?.settings&&state.settingsOwner===null),()=>shell?.selectSettings())]),moduleSelect('module-select-mobile')]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h(PageHeader,{class:'shell-heading',titleId:'page-title',title,eyebrow:home?undefined:currentModule?'GAME LINK / MODULE':'GAME LINK / SETTINGS',subtitle:home||currentModule?undefined:state?.modules?'查看模块状态，管理加载与恢复。':'管理工作区的通用偏好。'},{default:()=>h(NButton,{onClick:()=>{if(!shell?.state.busy){void shell?.refresh();if(state?.modules)void modulesView?.refresh(true);}},'aria-disabled':String(busy),'aria-busy':String(busy),'aria-label':'刷新模块状态'},()=>busy?'正在刷新…':'刷新模块状态')}),
            currentModule?h('nav',{class:'module-tabs','aria-label':'模块内分页'},[
              link('模块主页','#/module/'+encodeURIComponent(currentModule.module_id),moduleHome,()=>shell?.selectModule(currentModule.module_id)),
              currentModule.pages.some(page=>page.role==='config')?link('业务配置','#/module/'+encodeURIComponent(currentModule.module_id)+'/'+currentModule.pages.find(page=>page.role==='config')!.route_id,state?.moduleSection==='config',()=>shell?.selectSettings(currentModule.module_id)):null,
              currentModule.pages.some(page=>page.role==='aux')?link('辅助查询','#/module/'+encodeURIComponent(currentModule.module_id)+'/-/queries',queries,()=>shell?.selectModule(currentModule.module_id,'queries')):null,
            ]):null,
            h(StatusHint,{class:'shell-status',hidden:!busy&&!state?.stale&&state?.message==='状态已更新；查询仅在手动提交时执行。',tone:state?.stale?'warning':'neutral'},{default:()=>state?.message||'正在准备页面。'}),
            state?.cleanupPending ? h(NButton,{onClick:()=>shell?.retryCleanup()},()=> '重试页面清理') : null,
            confirmation.render(),
            queries?h('nav',{class:'query-links','aria-label':'辅助查询入口'},currentModule!.pages.filter(page=>page.role==='aux').map(page=>link(page.title,`#/module/${encodeURIComponent(currentModule!.module_id)}/${page.route_id}`,state?.selected?.route===page.route_id,()=>shell?.select(currentModule!.module_id,page.route_id)))):null,
            moduleHome&&!selection&&currentModule!.pages.length?h(SettingsGroup,{title:'可用功能'},{default:()=>h('div',{class:'module-capabilities'},currentModule!.pages.map(page=>h('span',{key:page.route_id},page.title)))}):null,
            queries&&!selection?h('p',{class:'shell-empty'},'选择一项辅助查询；仅在手动提交时请求数据。'):null,
            home?h('section',{class:'workspace-index','aria-label':'游戏连结'},[
              h('div',{class:'workspace-catalog'},[
                h('div',{class:'workspace-index-heading'},[h('h2',{class:'shell-eyebrow'},'游戏目录'),h('span',{class:'workspace-count'},modules.length+' 个模块')]),
                ...moduleGroups(modules).map(group=>h('section',{class:'workspace-package',key:group.key,'aria-label':group.title},[h('h3',{class:'workspace-package-title'},group.title),h('div',{class:'workspace-package-grid'},group.modules.map(module=>h('article',{class:'workspace-module',key:module.module_id},[
                  h('div',{class:'workspace-module-heading'},[h('div',[h('div',{class:'workspace-module-title'},[h('h2',{'aria-level':4},[link(label(module.module_id)+' →','#/module/'+encodeURIComponent(module.module_id),false,()=>shell?.selectModule(module.module_id),'workspace-status-'+encodeURIComponent(module.module_id))]),workspaceStatus(module.module_id,module.state)]),module.display?.summary?h('p',{class:'workspace-module-summary'},module.display.summary):null,!module.pages.length?h('p',{class:'workspace-module-description'},'暂无可用功能，请在模块管理中核对状态。'):null]),module.display?.logo_resource?h(LogoMark,{class:'workspace-emblem workspace-emblem-resource',style:{maskImage:`url("./${module.display.logo_resource}")`}}):h('span',{class:'workspace-emblem','aria-hidden':'true'},'✦')]),
                ])))])),
                !modules.length?h('p',{class:'shell-empty'},'暂无可用模块。可前往模块管理核对状态。'):null,
              ]),
              h('aside',{class:'workspace-aside','aria-label':'工作区管理'},[h('h2',{class:'shell-eyebrow'},'工作区管理'),workspaceLink('全局设置','#/settings',()=>shell?.selectSettings(),'settings'),workspaceLink('模块管理','#/modules',()=>shell?.selectModules(),'grid')]),
            ]):null,
            h('div',{ref:root,id:'module-container',hidden:!selection}),
            h('div',{class:'settings-container',hidden:!state?.settings||Boolean(state.settingsOwner&&!currentModule)},[h('div',{ref:settingsRoot,id:'settings-container'})]),
            h('div',{class:'module-dashboard',hidden:!state?.modules},[h('div',{ref:modulesRoot,id:'module-management-container',hidden:maintenanceOpen.value}),state?.modules?h(NButton,{size:'large',onClick:maintenanceOpen.value?closeMaintenance:openMaintenance},()=>maintenanceOpen.value?'关闭配置维护':'配置维护'):null]),
            h('div',{ref:maintenanceRoot,id:'maintenance-container',hidden:!maintenanceOpen.value}),
            !home&&!state?.modules&&!state?.settings&&!moduleHome&&!queries&&!state?.mounted||Boolean(state?.settingsOwner&&!currentModule)?h('p',{class:'shell-empty'},state?.cleanupPending?'页面清理未完成，请重新打开页面。':selection?'模块页面正在准备。':'此路由当前没有可用的注册页面。恢复模块后请从宿主插件详情正式重开。'):null,
          ]),
          h('footer',{class:'shell-footer'},'如月怜的游戏连结'),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
