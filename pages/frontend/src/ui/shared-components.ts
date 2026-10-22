import {defineComponent,h,type PropType} from 'vue';
import {NConfigProvider,darkTheme,zhCN,enUS,dateZhCN,dateEnUS} from 'naive-ui';

/** Theme values are normalized by the shell; modules never read the Host bridge. */
export const UiProvider=defineComponent({
  props:{theme:{type:String as PropType<'light'|'dark'>,default:'light'},locale:{type:String,default:'zh-CN'},styleMountTarget:{type:Object as PropType<HTMLElement>,required:true}},
  setup(props,{slots}){return()=>{
    const dark=props.theme==='dark',english=props.locale.startsWith('en');
    return h(NConfigProvider,{theme:dark?darkTheme:null,locale:english?enUS:zhCN,dateLocale:english?dateEnUS:dateZhCN,styleMountTarget:props.styleMountTarget,preflightStyleDisabled:true,inlineThemeDisabled:true,themeOverrides:{common:{heightMedium:'40px',borderRadius:'2px',primaryColor:dark?'#c4ad78':'#655071',primaryColorHover:dark?'#d7c497':'#796087',primaryColorPressed:dark?'#ae9460':'#503d5c',bodyColor:dark?'#211e26':'#f7f4ed',cardColor:dark?'#2a2630':'#efebe4',textColorBase:dark?'#eae6df':'#302c30'}}},{default:()=>h('div',{class:'ui-page','data-theme':props.theme},slots.default?.())});
  };},
});
export const SettingsLayout=defineComponent({setup(_,{slots}){return()=>h('div',{class:'ui-settings-layout','data-has-aside':Boolean(slots.aside)},[h('div',{class:'ui-settings-main'},slots.default?.()),slots.aside?h('aside',{class:'ui-settings-aside'},slots.aside()):null]);}});
export const Surface=defineComponent({props:{title:String,eyebrow:String},setup(props,{slots}){return()=>h('section',{class:'ui-surface'},[props.eyebrow?h('p',{class:'ui-eyebrow'},props.eyebrow):null,props.title?h('h2',props.title):null,slots.default?.()]);}});

/** Decorative only. Callers supply a trusted local resource mask via CSS/style. */
export const LogoMark=defineComponent({setup(){return()=>h('span',{class:'ui-logo-rebound','aria-hidden':'true'});}});
