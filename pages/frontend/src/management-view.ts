import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NConfigProvider, NCard, NAlert, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
type Field = {module_id:string; name:string; description:string; value_schema: null | {type:string; enum?: unknown[]; minimum?:number; maximum?:number; maxLength?:number}};
type Control = {field:Field; signature:string; detail:string; draftVersion:number; mode:HTMLSelectElement|null; input:HTMLInputElement|HTMLSelectElement|null};
type Card = {target:string; revision:string; controls:Control[]; save:HTMLButtonElement|null; onSave():void};
export function createManagementView(document:Document) {
  const cards=shallowRef<Card[]>([]),context=shallowRef<Record<string,unknown>>({});let changed=()=>{};
  const button=(id:string,text:string)=>h('button',{id,type:'button',disabled:true},text);
  const App=defineComponent({setup(){return()=>{
    const dark=context.value.isDark===true||context.value.theme==='dark',english=String(context.value.locale||'').startsWith('en');
    return h(NConfigProvider,{theme:dark?darkTheme:null,locale:english?enUS:zhCN,dateLocale:english?dateEnUS:dateZhCN,inlineThemeDisabled:true},{default:()=>h('main',{class:'management'},[
      h('p',{class:'brand'},'如月怜的游戏连结'),h('h1','配置目录与管理'),
      h('p','目录来自可信 Core 与模块声明。当前只开放四字段管理范围；字段注册不授予存量读取或修改权限，写操作仍由后端逐请求鉴权。'),
      h('p','保存只修改所选配置。客户端检查用于辅助输入，模块语义校验与配置版本以服务端为准。启动失败时，修复后需单独点击恢复运行。'),
      h(NAlert,{type:'info',showIcon:false},{default:()=>h('p',{id:'status',role:'status','aria-live':'polite'},'正在连接宿主管理页面。')}),button('refresh','刷新配置与版本'),
      h('section',{id:'fields','aria-label':'普通配置'},cards.value.map(card=>h('section',{class:'card',key:card.target,'data-owner':card.target},[
        h(NCard,{title:card.target},{default:()=>[
          h('p',card.revision),h('div',card.controls.map(c=>{
            const f=c.field,s=f.value_schema,id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,choices=s?.enum||(s?.type==='boolean'?[false,true]:null);
            return h('div',{class:'config-field',key:c.signature,'data-field':f.name,'data-owner':f.module_id},[
              h('label',{for:id},f.description||f.name),h('p',{class:'field-detail',id:id+'-detail'},c.detail),
              h('select',{'data-role':'mode','aria-label':`${f.description||f.name}修改方式`,ref:(node:unknown)=>{c.mode=node as HTMLSelectElement;},onChange:()=>{c.draftVersion++;changed();}},[['keep','保持'],['replace','替换 / 修复'],['clear','清除显式值']].map(([value,text])=>h('option',{value},text))),
              h(choices?'select':'input',{id,'data-role':'value','aria-describedby':id+'-detail',onInput:()=>{c.draftVersion++;},onChange:()=>{c.draftVersion++;},ref:(node:unknown)=>{c.input=node as HTMLInputElement;},...(!choices?{type:['integer','number'].includes(s?.type||'')?'number':'text',min:s?.minimum,max:s?.maximum,step:s?.type==='integer'?'1':'any',maxlength:s?.maxLength}:{})},choices?[h('option',{value:'',disabled:true},'请选择有效值'),...choices.map((value,index)=>h('option',{value:String(index)},String(value)))]:undefined),
            ]);
          })),h('button',{type:'button','data-action':'save',disabled:true,ref:(node:unknown)=>{card.save=node as HTMLButtonElement;},onClick:card.onSave},'保存本组修改'),
        ]}),
      ]))),
      h(NCard,{class:'card',title:'限定恢复'},{default:()=>[
        h('p','回退仅处理本次四字段迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。'),button('rollback','回退四字段迁移'),
        h('p',h('label',[h('input',{id:'replacement',type:'checkbox'}),'以四个已显式保存的有效新值完成恢复（缺少旧准备材料时）'])),button('recover','重新校验并恢复运行'),
      ]}),
    ])});
  };}});
  const app=createApp(App);app.mount(document.getElementById('management-root')!);
  return {fields(value:Card[],onChange:()=>void){changed=onChange;cards.value=value;},context(value:Record<string,unknown>){context.value=value||{};},dispose(){app.unmount();}};
}
