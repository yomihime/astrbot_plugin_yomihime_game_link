import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NButton} from 'naive-ui';
import {UiProvider,LogoMark} from '@game-link/ui';
import {PageHeader, SettingsGroup, StatusHint} from '@game-link/ui';
import * as moduleLoader from './module-loader.js';
import {createShell} from './controller';
import type {Bridge, ModuleDescriptor, PageDescriptor} from './contracts';
import {assetIdentity, createModuleAssets, presentationPaths} from './module-assets';
import {MODULE_SUMMARY_KEY, moduleRouteTitleKey, type ModuleCopy} from './module-copy';
import {createDirectoryPresentation} from './module-presentation';
import './shell.css';
// Core owns public presentation CSS independently of the management view.
import './ui/tokens.css';
import {skipToMain} from './navigation';
import {createManagementPage,validateCatalog as configurationCatalog,validateSnapshot as configurationSnapshot} from './management.js';
import {createModuleManagement} from './module-management';
import {createInlineConfirmation} from './confirmation';
import {contextBoundary} from './lifecycle';
import {moduleLabels,moduleGroups} from './catalog';
import {coreText, type CoreTextKey} from './i18n/messages';
import {findUiMessage, type UiParams} from './i18n/locale';
const {loadPage, sharedRuntimeUrl} = moduleLoader;
const loadPresentation = Reflect.get(moduleLoader, 'loadPresentation') as typeof moduleLoader.loadPresentation;
declare global {interface Window {AstrBotPluginView: Bridge}}
const App = defineComponent({setup() {
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null), settingsRoot=ref<HTMLElement|null>(null), modulesRoot=ref<HTMLElement|null>(null),maintenanceRoot=ref<HTMLElement|null>(null),maintenanceOpen=ref(false);
  let maintenance:ReturnType<typeof createManagementPage>|null=null;
  let shell: ReturnType<typeof createShell> | null = null, management:ReturnType<typeof createManagementPage>|null=null, modulesView:ReturnType<typeof createModuleManagement>|null=null, wasSettings=false,lastSettingsOwner:string|null=null;
  const pageName=document.documentElement.dataset.pageName||'00-game-link';

  const locale=()=>shell?.state.locale ?? 'zh-CN';
  const t=(key:CoreTextKey, params?:UiParams)=>coreText(locale(),key,params);
  const confirmation=createInlineConfirmation(document,()=>t('common.cancel'));
  let lastHash=window.location.hash,approvedHash='',contextGeneration=0,contextKey='',contextValid=false,draftGeneration=0,navigationSerial=0,closed=false,offContext:(()=>void)|undefined;
  const assets = createModuleAssets(document, loadPage, 8000, {
    bridge: window.AstrBotPluginView, runtimeUrl: sharedRuntimeUrl,
  }, loadPresentation);
  const directory = createDirectoryPresentation(assets, () => { revision.value++; },
    module => shell?.canExecuteModule(module) === true);
  type CopyRead = {identity: string; cancel: AbortController; value: ModuleCopy | null};
  const copies = new Map<string, CopyRead>();
  let logoBoundaryRevoked = false;
  const canReadCopy = (module: ModuleDescriptor) =>
    !closed && contextValid && !logoBoundaryRevoked && shell?.canExecuteModule(module) === true;
  function localizedCopy(module: ModuleDescriptor, key: string) {
    const copy = copies.get(module.module_id);
    if (copy?.value && canReadCopy(module) && copy.identity === assetIdentity(module, contextKey)) {
      return findUiMessage(copy.value, locale(), key);
    }
  }
  function moduleSummary(module: ModuleDescriptor) {
    const summary = localizedCopy(module, MODULE_SUMMARY_KEY) ?? module.display?.summary;
    return summary ? h('p', {class: 'workspace-module-summary'}, summary) : null;
  }
  const pageTitle = (module: ModuleDescriptor, page: PageDescriptor) =>
    localizedCopy(module, moduleRouteTitleKey(page.route_id)) ?? page.title;
  function syncModuleCopy() {
    const state = shell?.state;
    const modules = state?.catalog?.modules || [];
    for (const [owner, copy] of copies) {
      const module = modules.find(module => module.module_id === owner);
      if (!module || !canReadCopy(module) || copy.identity !== assetIdentity(module, contextKey)) {
        copy.cancel.abort();
        copies.delete(owner);
      }
    }
    if (!state || state.busy || state.stale) return;
    for (const module of modules) {
      if ((!state.home && state.moduleOwner !== module.module_id) || !canReadCopy(module) ||
          copies.has(module.module_id) || !presentationPaths(module)) continue;
      const copy: CopyRead = {identity: assetIdentity(module, contextKey), cancel: new AbortController(), value: null};
      copies.set(module.module_id, copy);
      void assets.moduleCopy(module, copy.cancel.signal).then(value => {
        if (!value || copies.get(module.module_id) !== copy || !canReadCopy(module) ||
            copy.identity !== assetIdentity(module, contextKey)) return;
        copy.value = value;
        revision.value++;
      });
    }
  }
  function syncDirectory() {
    const state = shell?.state;
    directory.sync(logoBoundaryRevoked || !contextValid ? [] : state?.catalog?.modules || [], {
      theme: state?.theme || 'light', locale: state?.locale || 'zh-CN',
      available: contextValid && !state?.busy && !state?.stale, boundary: contextKey,
    }, Boolean(state?.home && contextValid && !logoBoundaryRevoked));
  }
  function moduleEmblem(module: ModuleDescriptor) {
    const view = directory.view(module.module_id);
    return h('span', {class: 'workspace-emblem workspace-emblem-frame', style: view?.appearance.cssVars, 'aria-hidden': 'true'}, [
      view?.url
        ? h(LogoMark, {class: 'workspace-emblem-resource', hidden: view.shown, style: {maskImage: `url("${view.url}")`}})
        : h('span', '✦'),
      h('span', {class: 'workspace-emblem-effect', ref: view?.bind, hidden: !view?.shown}),
    ]);
  }
  const ownerLabel=(owner:string)=>owner==='game_link/core'?t('shell.global'):moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN').get(owner)||t('shell.currentModule');
  const interactionKey=()=>JSON.stringify([contextGeneration,contextKey,contextValid,draftGeneration,maintenanceOpen.value,shell?.state.home,shell?.state.settings,shell?.state.settingsOwner,shell?.state.selected,shell?.state.catalog?.catalog_revision,shell?.state.busy,shell?.state.stale,shell?.navigationImpact(),window.location.hash]);
  const navigationImpact=()=>{const global=shell?.state.settings?management?.navigationImpact():null,local=shell?.navigationImpact();return {ordinaryOwners:[...(global?.ordinaryOwners||[]),...(local?.ordinary?[local.owner]:[])],credentialOwners:[...(global?.credentialOwners||[]),...(local?.credentials?[local.owner]:[])],pending:local?.pending||false,blocked:local?.blocked||false,requiresConfirmation:Boolean(global?.requiresConfirmation||local?.ordinary||local?.credentials||local?.pending)};};
  const closeMaintenance=()=>{if(!maintenance&&!maintenanceOpen.value)return;confirmation.cancel();maintenance?.dispose();maintenance=null;maintenanceOpen.value=false;};
  const openMaintenance=()=>{if(maintenance||!maintenanceRoot.value||!contextValid)return;maintenanceOpen.value=true;maintenance=createManagementPage(document,window.AstrBotPluginView,window,{expectedPage:pageName,container:maintenanceRoot.value,scope:'maintenance',onDraftMutation:draftEdited,onConfigurationSaved:()=>{management?.resume();modulesView?.invalidateConfiguration();},beforeMaintenance:operation=>{const snapshot=interactionKey();return confirmation.ask({kind:'maintenance',title:()=>t('shell.confirmMaintenance'),message:()=>(management?.dirty()?t('shell.maintenanceDraft'):'')+t('shell.maintenanceScope'),confirmLabel:()=>t('shell.maintenanceAccept'),isCurrent:()=>contextValid&&!closed&&maintenanceOpen.value&&snapshot===interactionKey()&&operation.isCurrent()});}});maintenance.setLocale(locale());maintenance.start();};
  const navigate=async(action:()=>void)=>{
    const serial=++navigationSerial,snapshot=interactionKey();confirmation.cancel();
    const current=()=>!closed&&contextValid&&serial===navigationSerial&&snapshot===interactionKey();
    const impact=navigationImpact();if(impact.blocked){shell?.refuseNavigation();return;}
    if(impact.requiresConfirmation){
      const owners=(ids:string[])=>{const labels=moduleLabels(shell?.state.catalog?.modules||[],locale());return ids.map(owner=>owner==='game_link/core'?t('shell.global'):labels.get(owner)||owner).join(locale()==='en-US'?', ':'、');};
      const message=()=>[impact.ordinaryOwners.length?t('shell.keepOrdinary',{owners:owners(impact.ordinaryOwners)}):'',impact.credentialOwners.length?t('shell.clearCredentialDraft',{owners:owners(impact.credentialOwners)}):'',impact.pending?t('shell.leavePending'):''].join('');
      if(!await confirmation.ask({kind:'navigation',title:()=>t('shell.confirmLeave'),message,confirmLabel:()=>t('shell.leaveAccept'),isCurrent:current}))return;
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
  function changed(){syncDirectory();syncModuleCopy();confirmation.invalidate();revision.value++;const settings=shell?.state.settings||false;
    const showModules=Boolean(shell?.state.modules);
    if(!showModules)closeMaintenance();
    if(shell?.state.catalog&&!modulesView&&modulesRoot.value){modulesView=createModuleManagement(modulesRoot.value,window.AstrBotPluginView,window,pageName,()=>{void shell?.refresh();management?.resume();},operation=>{const snapshot=interactionKey(),impact=navigationImpact();if(impact.blocked){shell?.refuseNavigation();return false;}return !management?.dirty()&&!impact.requiresConfirmation||confirmation.ask({kind:'module-draft',owner:operation.owner,title:()=>t('shell.confirmModuleOperation',{module:ownerLabel(operation.owner)}),message:()=>t('shell.moduleDraft',{credentials:impact.credentialOwners.length?t('shell.moduleCredentialDraft'):'',pending:impact.pending?t('shell.modulePending'):'',revision:operation.revision}),confirmLabel:()=>t('shell.keepDraft'),isCurrent:()=>snapshot===interactionKey()&&operation.isCurrent(),canRestoreFocus:()=>snapshot===interactionKey()&&operation.canRestoreFocus()});},confirmation,interactionKey,{hasSettings:owner=>Boolean(shell?.state.catalog?.modules?.find(module=>module.module_id===owner)?.pages.some(page=>page.role==='config')),onSettings:owner=>void navigate(()=>shell?.selectSettings(owner)),labels:()=>moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'),catalog:configurationCatalog,snapshot:configurationSnapshot});modulesView.start();}
    modulesView?.setLocale(locale());
    management?.setLocale(locale());
    maintenance?.setLocale(locale());
    modulesView?.setRenderVisible(showModules);
    modulesView?.presentationCatalog(shell?.state.catalog?.modules||[]);
    modulesView?.presentationLabels(moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'));
    modulesView?.setHomeVisible(Boolean(shell?.state.modules));
    modulesView?.selectOwner(shell?.state.moduleOwner||null);
    if(shell?.state.catalog&&settings&&!management&&settingsRoot.value&&modulesRoot.value){management=createManagementPage(document,window.AstrBotPluginView,window,{expectedPage:pageName,container:settingsRoot.value,initiallySuspended:!settings,onDraftMutation:draftEdited,onConfigurationSaved:()=>modulesView?.invalidateConfiguration(),onConfigurationRead:payload=>shell?.applyLanguageSnapshot(payload)});management.setLocale(locale());management.start();}
    else if(settings&&!wasSettings){management?.resume();}
    else if(settings&&wasSettings&&lastSettingsOwner!==shell?.state.settingsOwner){management?.suspend();management?.resume();}
    else if(!settings&&wasSettings)management?.suspend();lastSettingsOwner=shell?.state.settingsOwner||null;if(settings)management?.selectOwner();management?.setModuleLabels(moduleLabels(shell?.state.catalog?.modules||[],shell?.state.locale||'zh-CN'));management?.setLegacyPolicies(shell?.state.catalog?.legacy_config_policies||[]);wasSettings=settings;
  }
  onMounted(() => {offContext=window.AstrBotPluginView?.onContext(context=>{let next='';try{if(context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!==pageName)throw Error();next=contextBoundary(context);}catch{contextValid=false;contextGeneration++;logoBoundaryRevoked=true;directory.revoke();revision.value++;confirmation.cancel();modulesView?.cancelConfirmation();return;}if(next!==contextKey){if(contextKey){logoBoundaryRevoked=true;directory.revoke();revision.value++;}contextGeneration++;contextKey=next;confirmation.cancel();modulesView?.cancelConfirmation();}contextValid=true;});window.addEventListener('hashchange',guardHash);shell = createShell({bridge: window.AstrBotPluginView, container: root.value!, window, loadPage, runtimeUrl:sharedRuntimeUrl, expectedPage: '00-game-link', confirmClear:(owner,field,revision,isCurrent)=>confirmation.ask({kind:'credential-clear',owner,title:()=>t('shell.confirmCredentialClear'),message:()=>t('shell.clearCredentialWarning',{owner:ownerLabel(owner),revision}),confirmLabel:()=>t('shell.clearAccept'),isCurrent}), changed}); shell.start();});
  onBeforeUnmount(() => {closed=true;contextGeneration++;for(const copy of copies.values())copy.cancel.abort();copies.clear();directory.close();closeMaintenance();confirmation.dispose();offContext?.();window.removeEventListener('hashchange',guardHash);management?.dispose();modulesView?.dispose();shell?.dispose();});
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = moduleGroups(state?.catalog?.modules || [],state?.locale||'zh-CN').flatMap(group=>group.modules);
    const labels=moduleLabels(modules,state?.locale||'zh-CN'),label=(owner:string)=>labels.get(owner)||t('shell.currentModule');
    const busy = state?.busy || false,home=state?.home??true;
    const currentModule=modules.find(m=>m.module_id===state?.moduleOwner),moduleHome=Boolean(currentModule&&state?.moduleSection==='home'),queries=Boolean(currentModule&&state?.moduleSection==='queries');
    const go=(action:()=>void)=>(event:Event)=>{event.preventDefault();void navigate(action);};
    const link=(text:string,href:string,current:boolean,action:()=>void,description?:string)=>h('a',{href,'aria-current':current?'page':undefined,'aria-describedby':description,onClick:go(action)},text);
    const title=currentModule?(state?.moduleSection==='config'?t('shell.businessSettings'):label(currentModule.module_id)):state?.settings?t('shell.global'):state?.modules?t('shell.moduleManagement'):home?t('shell.workspace'):t('shell.pageUnavailable');
    const icon=(kind:'home'|'grid'|'game'|'settings'|'arrow'|'page')=>h('svg',{class:'shell-icon',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.5,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},[h('path',{d:{home:'M3 10 12 3l9 7v11h-6v-8H9v8H3Z',grid:'M3 3h7v7H3Zm11 0h7v7h-7ZM3 14h7v7H3Zm11 0h7v7h-7Z',game:'M7 7h10c3 0 5 10 3 11-2 1-4-3-5-3H9c-1 0-3 4-5 3-2-1 0-11 3-11Zm0 3v4m-2-2h4m7-1h.01m2 3h.01',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Zm3 5a4 4 0 1 0 0 8 4 4 0 0 0 0-8',arrow:'M5 12h14m-5-5 5 5-5 5',page:'M5 3h10l4 4v14H5Zm9 0v5h5M8 12h8m-8 4h6'}[kind]})]);
    const railLink=(text:string,href:string,current:boolean,action:()=>void,kind:'home'|'grid'|'game')=>h('a',{href,'aria-current':current?'page':undefined,onClick:go(action)},[icon(kind),h('span',text)]);
    const workspaceStatus=(owner:string,state:string)=>h('span',{class:'workspace-status-icon','data-state':state},[
      h('svg',{viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':1.7,'stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'},state==='unavailable'?[h('path',{d:'m12 3 10 18H2Z'}),h('path',{d:'M12 9v5m0 3h.01'})]:[h('circle',{cx:12,cy:12,r:9}),h('path',{d:state==='loaded'?'m8 12 3 3 5-6':'M9 8v8m6-8v8'})]),
      h('span',{class:'workspace-status-tooltip',id:'workspace-status-'+encodeURIComponent(owner),role:'tooltip'},state==='loaded'?t('shell.loaded'):state==='disabled'?t('shell.disabled'):t('shell.unavailable')),
    ]);
    const workspaceLink=(text:string,href:string,action:()=>void,kind:'settings'|'grid')=>h('a',{class:'workspace-management-link',href,onClick:go(action)},[h('span',{class:'workspace-management-icon'},[icon(kind)]),h('span',[h('strong',text)]),icon('arrow')]);
    const moduleSelect = (id:string)=>h('div',{class:'shell-field'},[h('label',{for:id},t('shell.module')),h('select',{id,value:currentModule?.module_id||'',disabled:!modules.length,onChange:(event:Event)=>{const owner=(event.target as HTMLSelectElement).value;if(owner)void navigate(()=>shell?.selectModule(owner));}},[h('option',{value:''},t('shell.chooseModule')),...modules.map(module=>h('option',{value:module.module_id},label(module.module_id)))])]);
    return h(UiProvider,{theme:state?.theme||'light',locale:state?.locale||'zh-CN',styleMountTarget:document.head}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},t('shell.skipMain')),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':t('shell.moduleNavigation')},[
          h('p',{class:'shell-brand'},[h('span',{class:'shell-brand-mark','aria-hidden':'true'},'☾'),h('span',[h('span',{class:'shell-brand-line'},t('shell.brandOwner')),h('span',{class:'shell-brand-line'},t('shell.brandShort'))])]),
          h('nav',{'aria-label':t('shell.mainNavigation')},[railLink(t('shell.home'),'#/',home,()=>shell?.selectHome(),'home'),railLink(t('shell.moduleManagement'),'#/modules',Boolean(state?.modules),()=>shell?.selectModules(),'grid')]),
          h('p',{class:'shell-nav-label'},t('shell.gameModules')),
          h('nav',{'aria-label':t('shell.mountedModules')},modules.map(module=>railLink(label(module.module_id),'#/module/'+encodeURIComponent(module.module_id),state?.moduleOwner===module.module_id,()=>shell?.selectModule(module.module_id),'game'))),
          h('div',{class:'shell-settings-slot'},[h('button',{class:'shell-settings',type:'button','aria-current':state?.settings&&state.settingsOwner===null?'page':undefined,onClick:()=>void navigate(()=>shell?.selectSettings())},[icon('settings'),h('span',t('shell.global'))]),h('p',{class:'shell-note'},['GAME LINK',h('br'),t('shell.tagline')])]),
        ]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-topbar'},[h('span',t('shell.brandShort')),h('span',{'aria-hidden':'true'},'/'),h('span',title)]),
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},t('shell.brand')),h('nav',{'aria-label':t('shell.mobileNavigation')},[link(t('shell.home'),'#/',home,()=>shell?.selectHome()),link(t('shell.moduleManagement'),'#/modules',Boolean(state?.modules),()=>shell?.selectModules()),link(t('shell.global'),'#/settings',Boolean(state?.settings&&state.settingsOwner===null),()=>shell?.selectSettings())]),moduleSelect('module-select-mobile')]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h(PageHeader,{class:'shell-heading',titleId:'page-title',title,eyebrow:home?undefined:currentModule?'GAME LINK / MODULE':'GAME LINK / SETTINGS',subtitle:home||currentModule?undefined:state?.modules?t('shell.manageModulesIntro'):t('shell.settingsIntro')},{default:()=>h(NButton,{onClick:()=>{if(!shell?.state.busy){void shell?.refresh();if(state?.modules)void modulesView?.refresh(true);}},'aria-disabled':String(busy),'aria-busy':String(busy),'aria-label':t('shell.refreshModules')},()=>busy?t('shell.refreshing'):t('shell.refreshModules'))}),
            currentModule?h('nav',{class:'module-tabs','aria-label':t('shell.moduleTabs')},[
              link(t('shell.moduleHome'),'#/module/'+encodeURIComponent(currentModule.module_id),moduleHome,()=>shell?.selectModule(currentModule.module_id)),
              currentModule.pages.some(page=>page.role==='config')?link(t('shell.businessSettings'),'#/module/'+encodeURIComponent(currentModule.module_id)+'/'+currentModule.pages.find(page=>page.role==='config')!.route_id,state?.moduleSection==='config',()=>shell?.selectSettings(currentModule.module_id)):null,
              currentModule.pages.some(page=>page.role==='aux')?link(t('shell.auxQueries'),'#/module/'+encodeURIComponent(currentModule.module_id)+'/-/queries',queries,()=>shell?.selectModule(currentModule.module_id,'queries')):null,
            ]):null,
            h(StatusHint,{class:'shell-status',hidden:!busy&&!state?.stale&&state?.messageKey==='shell.catalogReady',tone:state?.stale?'warning':'neutral'},{default:()=>state?.message||t('shell.preparing')}),
            state?.cleanupPending || directory.cleanupPending ? h(NButton,{onClick:()=>{directory.retryCleanup();shell?.retryCleanup();syncDirectory();revision.value++;}},()=> t('shell.retryCleanup')) : null,
            confirmation.render(),
            queries?h('nav',{class:'query-links','aria-label':t('shell.queryEntries')},currentModule!.pages.filter(page=>page.role==='aux').map(page=>link(pageTitle(currentModule!,page),`#/module/${encodeURIComponent(currentModule!.module_id)}/${page.route_id}`,state?.selected?.route===page.route_id,()=>shell?.select(currentModule!.module_id,page.route_id)))):null,
            moduleHome&&!selection&&currentModule!.pages.length?h(SettingsGroup,{title:t('shell.capabilities')},{default:()=>h('div',{class:'module-capabilities'},currentModule!.pages.map(page=>h('span',{key:page.route_id},pageTitle(currentModule!,page))))}):null,
            queries&&!selection?h('p',{class:'shell-empty'},t('shell.chooseQuery')):null,
            home?h('section',{class:'workspace-index','aria-label':t('shell.brandShort')},[
              h('div',{class:'workspace-catalog'},[
                h('div',{class:'workspace-index-heading'},[h('h2',{class:'shell-eyebrow'},t('shell.gameCatalog')),h('span',{class:'workspace-count'},t('shell.moduleCount',{count:modules.length}))]),
                ...moduleGroups(modules,state?.locale||'zh-CN').map(group=>h('section',{class:'workspace-package',key:group.key,'aria-label':group.title},[h('h3',{class:'workspace-package-title'},group.title),h('div',{class:'workspace-package-grid'},group.modules.map(module=>h('article',{class:'workspace-module',key:module.module_id},[
                  h('div',{class:'workspace-module-heading'},[h('div',[h('div',{class:'workspace-module-title'},[h('h2',{'aria-level':4},[link(label(module.module_id),'#/module/'+encodeURIComponent(module.module_id),false,()=>shell?.selectModule(module.module_id),'workspace-status-'+encodeURIComponent(module.module_id))]),workspaceStatus(module.module_id,module.state)]),moduleSummary(module),!module.pages.length?h('p',{class:'workspace-module-description'},t('shell.noFeatures')):null]),moduleEmblem(module)]),
                ])))])),
                !modules.length?h('p',{class:'shell-empty'},t('shell.emptyCatalog')):null,
              ]),
              h('aside',{class:'workspace-aside','aria-label':t('shell.workspaceManagement')},[h('h2',{class:'shell-eyebrow'},t('shell.workspaceManagement')),workspaceLink(t('shell.global'),'#/settings',()=>shell?.selectSettings(),'settings'),workspaceLink(t('shell.moduleManagement'),'#/modules',()=>shell?.selectModules(),'grid')]),
            ]):null,
            h('div',{ref:root,id:'module-container',hidden:!selection}),
            h('div',{class:'settings-container',hidden:!state?.settings||Boolean(state.settingsOwner&&!currentModule)},[h('div',{ref:settingsRoot,id:'settings-container'})]),
            h('div',{class:'module-dashboard',hidden:!state?.modules},[h('div',{ref:modulesRoot,id:'module-management-container',hidden:maintenanceOpen.value}),state?.modules?h(NButton,{size:'large',onClick:maintenanceOpen.value?closeMaintenance:openMaintenance},()=>maintenanceOpen.value?t('shell.closeMaintenance'):t('shell.maintenance')):null]),
            h('div',{ref:maintenanceRoot,id:'maintenance-container',hidden:!maintenanceOpen.value}),
            !home&&!state?.modules&&!state?.settings&&!moduleHome&&!queries&&!state?.mounted||Boolean(state?.settingsOwner&&!currentModule)?h('p',{class:'shell-empty'},state?.cleanupPending?t('shell.cleanupReopen'):selection?t('shell.modulePreparing'):t('shell.routeMissing')):null,
          ]),
          h('footer',{class:'shell-footer'},[h('a',{class:'shell-project-link',href:'https://github.com/yomihime/astrbot_plugin_yomihime_game_link',target:'_blank',rel:'noopener noreferrer'},[h('span',t('shell.brand')),h('span',{class:'shell-project-english'},[locale()==='en-US'?' ':' / Yomihime Game Link ',h('span',{class:'shell-project-spark','aria-hidden':'true'},'✧')])])]),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
