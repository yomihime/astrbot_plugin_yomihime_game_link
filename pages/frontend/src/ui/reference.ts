import {createApp, defineComponent, h, ref} from 'vue';
import {ActionBar, PageHeader, SettingsGroup, SettingsRow, StatusHint} from './components';
import './tokens.css';

createApp(defineComponent({setup(){
  const dark=ref(false),state=ref('dirty'),days=ref('7');
  const messages:Record<string,string>={loading:'正在读取设置…',dirty:'有未保存修改。',error:'保存失败，请核对输入后重试。',conflict:'配置已变化，请刷新核对后重新保存。',readonly:'当前字段不可修改。',success:'本组已保存。'};
  return()=>h('main',{class:'management',style:{maxWidth:'1000px',margin:'32px auto',padding:'0 24px'}},[
    h(PageHeader,{title:'Yomihime UI 组件参考'},{default:()=>h('button',{type:'button',onClick:()=>{dark.value=!dark.value;document.documentElement.dataset.theme=dark.value?'dark':'light';document.body.style.background='var(--ui-background)';}},dark.value?'浅色主题':'深色主题')}),
    h('p','仅展示本地交互状态，无宿主连接、凭据或业务请求。'),
    h('label',{for:'demo-state'},'示例状态 '),h('select',{id:'demo-state',value:state.value,onChange:(event:Event)=>{state.value=(event.target as HTMLSelectElement).value;}},Object.keys(messages).map(value=>h('option',{value},value))),
    h(SettingsGroup,{title:'常规设置'},{default:()=>[
      h(SettingsRow,{'data-width':'short'},{default:()=>[h('label',{for:'demo-days'},'默认天数'),h('input',{id:'demo-days',type:'number',value:days.value,disabled:state.value==='readonly',onInput:(event:Event)=>{days.value=(event.target as HTMLInputElement).value;state.value='dirty';}}),h('button',{type:'button',disabled:state.value==='readonly',onClick:()=>{days.value='7';state.value='dirty';}},'恢复默认')]}),
      h(SettingsRow,{'data-width':'medium'},{default:()=>[h('label',{for:'demo-zone'},'时区'),h('input',{id:'demo-zone',value:'Asia/Shanghai',readonly:true}),h('span','只读示例')]}),
      h(StatusHint,{tone:['error','conflict'].includes(state.value)?'error':state.value==='dirty'?'warning':state.value==='success'?'success':'neutral'},{default:()=>messages[state.value]}),
      h(ActionBar,{}, {default:()=>[h('button',{type:'button','data-action':'save',disabled:['loading','readonly','conflict'].includes(state.value),onClick:()=>{state.value='success';}},'保存本组'),h('button',{type:'button',onClick:()=>{days.value='7';state.value='readonly';}},'放弃修改')]}),
    ]}),
  ]);
}})).mount('#reference');
