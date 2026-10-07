import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NConfigProvider, NButton, NTag, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
import {loadPage} from './module-loader.js';
import {createShell} from './controller';
import type {Bridge} from './contracts';
import './shell.css';
import {skipToMain} from './navigation';
import {createManagementPage} from './management.js';
import {createModuleManagement} from './module-management';
declare global {interface Window {AstrBotPluginPage: Bridge}}
const App = defineComponent({setup() {
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null), settingsRoot=ref<HTMLElement|null>(null), modulesRoot=ref<HTMLElement|null>(null);
  let shell: ReturnType<typeof createShell> | null = null, management:ReturnType<typeof createManagementPage>|null=null, modulesView:ReturnType<typeof createModuleManagement>|null=null, wasSettings=false,lastSettingsOwner:string|null=null;
  const pageName=document.documentElement.dataset.pageName||'shell';
  const navigate=(action:()=>void)=>{if(shell?.state.settings&&management?.dirty()&&!window.confirm('保留普通配置草稿并离开设置？取消可继续编辑。秘密输入将清空。'))return;action();};
  let lastHash=window.location.hash;
  const guardHash=(event:HashChangeEvent)=>{if(wasSettings&&!window.location.hash.startsWith('#/settings')&&management?.dirty()&&!window.confirm('保留普通配置草稿并离开设置？取消可继续编辑。秘密输入将清空。')){event.stopImmediatePropagation();window.location.hash=lastHash;return;}lastHash=window.location.hash;};
  function changed(){revision.value++;const settings=shell?.state.settings||false;
    if(settings&&!management&&settingsRoot.value&&modulesRoot.value){management=createManagementPage(document,window.AstrBotPluginPage,window,{expectedPage:pageName,container:settingsRoot.value});management.start();modulesView=createModuleManagement(modulesRoot.value,window.AstrBotPluginPage,window,pageName,()=>{void shell?.refresh();management?.resume();},()=>!management?.dirty()||window.confirm('存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。'));modulesView.start();}
    else if(settings&&!wasSettings){management?.resume();void modulesView?.refresh();}
    else if(settings&&wasSettings&&lastSettingsOwner!==shell?.state.settingsOwner){management?.suspend();management?.resume();}
    else if(!settings&&wasSettings)management?.suspend();lastSettingsOwner=shell?.state.settingsOwner||null;if(settings)management?.selectOwner(shell?.state.settingsOwner||'game_link/core');wasSettings=settings;
  }
  onMounted(() => {window.addEventListener('hashchange',guardHash);shell = createShell({bridge: window.AstrBotPluginPage, container: root.value!, window, loadPage, expectedPage: document.documentElement.dataset.pageName || 'shell', changed}); shell.start();});
  onBeforeUnmount(() => {window.removeEventListener('hashchange',guardHash);management?.dispose();modulesView?.dispose();shell?.dispose();});
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = state?.catalog?.modules || [];
    const pages = modules.flatMap(module => module.pages.map(page => ({module,page})));
    const busy = state?.busy || false;
    const currentModule=modules.find(m=>m.module_id===(state?.settingsOwner||state?.selected?.owner));
    const selectModule = (event: Event) => {const module = modules.find(m => m.module_id === (event.target as HTMLSelectElement).value); if(module)navigate(()=>state?.settings||!module.pages[0]?shell!.selectSettings(module.module_id):shell!.select(module.module_id,module.pages[0].route_id));};
    const moduleSelect = (id: string) => h('div', {class: 'shell-field'}, [h('label', {for:id},'模块'), h('select',{id,value: currentModule?.module_id || '',disabled: !modules.length,onChange:selectModule},[!currentModule ? h('option',{value:''},'请选择模块') : null,...modules.map(m=>h('option',{value:m.module_id},m.module_id))])]);
    const pageSelect = h('div',{class:'shell-field'},[h('label',{for:'page-select'},'页面'),h('select',{id:'page-select',value:state?.settings?'':state?.selected?.route || '',disabled:!currentModule?.pages.length,onChange:(e:Event)=>{if(currentModule)navigate(()=>shell!.select(currentModule.module_id,(e.target as HTMLSelectElement).value));}},[!selection?h('option',{value:''},state?.settings?'模块设置 · 选择页面返回查询':'请先选择模块'):null,...(currentModule?.pages.map(p=>h('option',{value:p.route_id},p.title))||[])])]);
    return h(NConfigProvider,{theme: state?.theme === 'dark' ? darkTheme : null,locale:state?.locale.startsWith('en')?enUS:zhCN,dateLocale:state?.locale.startsWith('en')?dateEnUS:dateZhCN,inlineThemeDisabled:true}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},'跳至正文'),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':'模块导航'},[h('p',{class:'shell-brand'},'如月怜的游戏连结'),h('p',{class:'shell-eyebrow'},'注册目录 · 公开查询'),moduleSelect('module-select'),h('nav',{'aria-label':'模块页面'},pages.map(({module,page})=>h('a',{href:`#/module/${encodeURIComponent(module.module_id)}/${page.route_id}`,onClick:(event:Event)=>{event.preventDefault();navigate(()=>shell!.select(module.module_id,page.route_id));},'aria-current':state?.selected?.owner===module.module_id && state.selected.route===page.route_id?'page':undefined},page.title))),h('nav',{'aria-label':'模块设置'},modules.map(module=>h('a',{href:'#/settings/module/'+encodeURIComponent(module.module_id),onClick:(event:Event)=>{event.preventDefault();navigate(()=>shell!.selectSettings(module.module_id));},'aria-current':state?.settingsOwner===module.module_id?'page':undefined},module.module_id+' · 模块设置'))),h('div',{class:'shell-settings-slot'},[h('button',{class:'shell-settings',type:'button','aria-current':state?.settings?'page':undefined,onClick:()=>shell?.selectSettings()},'全局设置'),h('p',{class:'shell-note'},'设置使用独立管理鉴权。模块声明不授予读写权限。')])]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},'游戏连结'),moduleSelect('module-select-mobile'),pageSelect,currentModule?h(NButton,{onClick:()=>navigate(()=>shell?.selectSettings(currentModule.module_id))},()=> '模块设置'):null,h(NButton,{onClick:()=>shell?.selectSettings()},()=> '全局设置')]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h('header',{class:'shell-heading'},[h('div',[h('p',{class:'shell-eyebrow'},state?.settings?'Core 管理':selection?.module.module_id || '模块目录'),h('h1',{id:'page-title'},state?.settings?(state.settingsOwner?state.settingsOwner+' · 模块设置':'全局设置'):selection?.page.title || '游戏连结')]),h(NButton,{onClick:()=>shell?.refresh(),disabled:busy,'aria-label':'刷新模块状态'},()=>busy?'正在刷新…':'刷新状态')]),
            h('div',{class:'shell-status',role:'status','aria-live':'polite'},[h(NTag,{type:state?.stale?'warning':'default',size:'small'},()=>state?.stale?'旧状态':busy?'读取中':'只读'),h('span',state?.message || '正在准备页面。')]),
            state?.cleanupPending ? h(NButton,{onClick:()=>shell?.retryCleanup()},()=> '重试页面清理') : null,
            h('div',{ref:root,id:'module-container',hidden:state?.settings}),
            h('div',{class:'settings-container',hidden:!state?.settings},[h('div',{ref:settingsRoot,id:'settings-container'}),h('details',{class:'module-maintenance'},[h('summary','模块管理（启用、禁用与保留数据解除挂载）'),h('div',{ref:modulesRoot,id:'module-management-container'})])]),
            !state?.settings&&!state?.mounted ? h('p',{class:'shell-empty'},state?.cleanupPending?'页面清理未完成，请重新打开页面。':selection?'模块页面正在准备。':'此路由当前没有可用的注册页面。') : null,
          ]),
          h('footer','只读目录与手动查询 · 来源与缺项以本次结果为准'),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
