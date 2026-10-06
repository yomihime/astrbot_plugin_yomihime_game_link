import {createApp, defineComponent, h, ref, onMounted, onBeforeUnmount} from 'vue';
import {NConfigProvider, NButton, NTag, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
import {loadPage} from './module-loader.js';
import {createShell} from './controller';
import type {Bridge} from './contracts';
import './shell.css';
import {skipToMain} from './navigation';
declare global {interface Window {AstrBotPluginPage: Bridge}}
const App = defineComponent({setup() {
  const revision = ref(0), root = ref<HTMLElement|null>(null), main = ref<HTMLElement|null>(null);
  let shell: ReturnType<typeof createShell> | null = null;
  onMounted(() => {shell = createShell({bridge: window.AstrBotPluginPage, container: root.value!, window, loadPage, expectedPage: document.documentElement.dataset.pageName || 'shell', changed: () => {revision.value++;}}); shell.start();});
  onBeforeUnmount(() => shell?.dispose());
  return () => {
    revision.value;
    const state = shell?.state, selection = shell?.selected(), modules = state?.catalog?.modules || [];
    const pages = modules.flatMap(module => module.pages.map(page => ({module,page})));
    const busy = state?.busy || false;
    const selectModule = (event: Event) => {const module = modules.find(m => m.module_id === (event.target as HTMLSelectElement).value); if (module?.pages[0]) shell!.select(module.module_id,module.pages[0].route_id);};
    const moduleSelect = (id: string) => h('div', {class: 'shell-field'}, [h('label', {for:id},'模块'), h('select',{id,value: state?.selected?.owner || '',disabled: !modules.some(m=>m.pages.length),onChange:selectModule},[!state?.selected ? h('option',{value:''},'请选择模块') : null,...modules.map(m=>h('option',{value:m.module_id,disabled:!m.pages.length},m.module_id))])]);
    const pageSelect = h('div',{class:'shell-field'},[h('label',{for:'page-select'},'页面'),h('select',{id:'page-select',value:state?.selected?.route || '',disabled:!selection,onChange:(e:Event)=>shell!.select(state!.selected!.owner,(e.target as HTMLSelectElement).value)},selection?.module.pages.map(p=>h('option',{value:p.route_id},p.title)) || [h('option',{value:''},'请先选择模块')])]);
    return h(NConfigProvider,{theme: state?.theme === 'dark' ? darkTheme : null,locale:state?.locale.startsWith('en')?enUS:zhCN,dateLocale:state?.locale.startsWith('en')?dateEnUS:dateZhCN,inlineThemeDisabled:true}, {default:()=>[
      h('button',{class:'skip-button',onClick:()=>skipToMain(main.value)},'跳至正文'),
      h('div',{class:'game-shell','data-theme':state?.theme || 'light'},[
        h('aside',{class:'shell-sidebar','aria-label':'模块导航'},[h('p',{class:'shell-brand'},'如月怜的游戏连结'),h('p',{class:'shell-eyebrow'},'注册目录 · 公开查询'),moduleSelect('module-select'),h('nav',{'aria-label':'模块页面'},pages.map(({module,page})=>h('a',{href:`#/module/${encodeURIComponent(module.module_id)}/${page.route_id}`,'aria-current':state?.selected?.owner===module.module_id && state.selected.route===page.route_id?'page':undefined},page.title))),h('p',{class:'shell-note'},'此处为公开查询入口。配置管理使用独立授权页面。')]),
        h('div',{class:'shell-content'},[
          h('div',{class:'shell-mobile'},[h('p',{class:'shell-brand'},'游戏连结'),moduleSelect('module-select-mobile'),pageSelect]),
          h('main',{ref:main,id:'page-root',tabindex:-1,'aria-labelledby':'page-title'},[
            h('header',{class:'shell-heading'},[h('div',[h('p',{class:'shell-eyebrow'},selection?.module.module_id || '模块目录'),h('h1',{id:'page-title'},selection?.page.title || '游戏连结')]),h(NButton,{onClick:()=>shell?.refresh(),disabled:busy,'aria-label':'刷新模块状态'},()=>busy?'正在刷新…':'刷新状态')]),
            h('div',{class:'shell-status',role:'status','aria-live':'polite'},[h(NTag,{type:state?.stale?'warning':'default',size:'small'},()=>state?.stale?'旧状态':busy?'读取中':'只读'),h('span',state?.message || '正在准备页面。')]),
            state?.cleanupPending ? h(NButton,{onClick:()=>shell?.retryCleanup()},()=> '重试页面清理') : null,
            h('div',{ref:root,id:'module-container'}),
            !state?.mounted ? h('p',{class:'shell-empty'},state?.cleanupPending?'页面清理未完成，请重新打开页面。':selection?'模块页面正在准备。':'此路由当前没有可用的注册页面。') : null,
          ]),
          h('footer','只读目录与手动查询 · 来源与缺项以本次结果为准'),
        ]),
      ]),
    ]});
  };
}});
const app = createApp(App); app.mount('#app'); window.addEventListener('pagehide',()=>app.unmount(),{once:true});
