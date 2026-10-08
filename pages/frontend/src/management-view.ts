import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NConfigProvider, NCard, NAlert, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
type Field = {module_id:string; name:string; description:string; group?:string|null; value_schema: null | {type:string; enum?: unknown[]; minimum?:number; maximum?:number; maxLength?:number}};
type Control = {field:Field; signature:string; detail:string; problem?:string; draftVersion:number; mode:HTMLInputElement|HTMLSelectElement|null; input:HTMLInputElement|HTMLSelectElement|null; reset?:HTMLButtonElement|null};
type Card = {target:string; revision:string; controls:Control[]; save:HTMLButtonElement|null; discard?:HTMLButtonElement|null; onSave():void; onDiscard?():void};
type Credential = {field:{module_id:string;name:string;description:string};signature:string;detail:string;draftVersion:number;mode:HTMLSelectElement|null;clientId:HTMLInputElement|null;clientSecret:HTMLInputElement|null;confirm:HTMLInputElement|null;save:HTMLButtonElement|null;onSave():void};
function groupControls(controls:Control[]) {
  const groups=new Map<string,Control[]>();
  for(const control of controls){const group=control.field.group||'';if(!groups.has(group))groups.set(group,[]);groups.get(group)!.push(control);}
  return [...groups].map(([group,controls],index)=>({group,controls,title:groups.size===1?'普通设置':`设置组 ${index+1}`}));
}
export function createManagementView(document:Document,container:HTMLElement=document.getElementById('management-root')!) {
  const cards=shallowRef<Card[]>([]),context=shallowRef<Record<string,unknown>>({});let changed=()=>{};
  const credentials=shallowRef<Credential[]>([]);
  const credentialNotice=shallowRef('');
  const owner=shallowRef<string|null>(null);
  const button=(id:string,text:string)=>h('button',{id,type:'button',disabled:true},text);
  const App=defineComponent({setup(){return()=>{
    const dark=context.value.isDark===true||context.value.theme==='dark',english=String(context.value.locale||'').startsWith('en');
    return h(NConfigProvider,{preflightStyleDisabled:true,theme:dark?darkTheme:null,locale:english?enUS:zhCN,dateLocale:english?dateEnUS:dateZhCN,inlineThemeDisabled:true},{default:()=>h('section',{class:'management'},[
      container.id==='management-root'?h('h1','设置'):null,
      h(NAlert,{type:'info',showIcon:false},{default:()=>h('p',{id:'status',role:'status','aria-live':'polite'},'正在连接宿主管理页面。')}),button('refresh','刷新配置与版本'),
      h('section',{id:'fields','aria-label':'普通配置'},cards.value.map(card=>h('section',{class:'card',key:card.target,'data-owner':card.target,hidden:owner.value!==null&&owner.value!==card.target},[
        h(NCard,{},{default:()=>[
          ...groupControls(card.controls).map(group=>h('fieldset',{class:'config-group','data-group':group.group||'general',key:group.group,style:{border:'0',padding:'0',minWidth:'0',font:'inherit',margin:'0 0 24px'}},[h('legend',{style:{fontSize:'16px',fontWeight:'600',padding:'0',marginBottom:'8px'}},group.title),...group.controls.map(c=>{
            const f=c.field,s=f.value_schema,id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,choices=s?.enum||(s?.type==='boolean'?[false,true]:null);
            return h('div',{class:'config-field',key:c.signature,'data-field':f.name,'data-owner':f.module_id,'data-group':f.group||'general'},[
              h('label',{for:id},f.description||f.name),
              h('input',{type:'hidden','data-role':'mode',ref:(node:unknown)=>{c.mode=node as HTMLInputElement;if(c.mode&&!c.mode.value)c.mode.value='keep';},onChange:()=>{c.draftVersion++;changed();}}),
              h(choices?'select':'input',{id,'data-role':'value','aria-describedby':c.problem?id+'-detail':undefined,onInput:()=>{if(c.mode)c.mode.value='replace';c.draftVersion++;changed();},onChange:()=>{if(c.mode)c.mode.value='replace';c.draftVersion++;changed();},ref:(node:unknown)=>{c.input=node as HTMLInputElement;},...(!choices?{type:['integer','number'].includes(s?.type||'')?'number':'text',min:s?.minimum,max:s?.maximum,step:s?.type==='integer'?'1':'any',maxlength:s?.maxLength}:{})},choices?[h('option',{value:'',disabled:true},'请选择有效值'),...choices.map((value,index)=>h('option',{value:String(index)},String(value)))]:undefined),
              c.problem?h('p',{class:'field-detail',id:id+'-detail',role:'status'},c.problem):null,
              h('button',{type:'button','data-action':'default',ref:(node:unknown)=>{c.reset=node as HTMLButtonElement;},onClick:()=>{if(!c.mode||!c.input||c.reset?.disabled)return;c.mode.value='clear';c.draftVersion++;const value=(f as Field&{default:unknown}).default;c.input.value=choices?String(choices.findIndex(choice=>choice===value)):value===null?'':String(value);changed();}},'恢复默认'),
            ]);
          })])),h('div',{class:'form-actions'},[h('button',{type:'button','data-action':'save',disabled:true,ref:(node:unknown)=>{card.save=node as HTMLButtonElement;},onClick:card.onSave},'保存'),h('button',{type:'button','data-action':'discard',ref:(node:unknown)=>{card.discard=node as HTMLButtonElement;},onClick:()=>card.onDiscard?.()},'放弃修改')]),h('details',{class:'technical-details'},[h('summary','配置详细信息'),h('p',card.revision),...card.controls.map(c=>h('p',c.detail))]),
        ]}),
      ]))),
      h('p',{id:'credential-status',role:'status','aria-live':'polite'},credentialNotice.value),
      h('section',{id:'credentials','aria-label':'来源凭据'},credentials.value.map(c=>{
        const id=`credential-${encodeURIComponent(c.field.module_id)}-${encodeURIComponent(c.field.name)}`;
        const edit=()=>{c.draftVersion++;};
        return h(NCard,{class:'card',key:c.signature,title:c.field.description.split('；')[0],'data-credential':c.field.name,'data-owner':c.field.module_id,hidden:owner.value!==null&&owner.value!==c.field.module_id},{default:()=>[
          h('p',c.detail),
          h('label',{for:id+'-mode'},'本次操作'),h('select',{id:id+'-mode','data-role':'credential-mode',ref:(node:unknown)=>{c.mode=node as HTMLSelectElement;},onChange:()=>{edit();if(c.mode?.value!=='replace'){if(c.clientId)c.clientId.value='';if(c.clientSecret)c.clientSecret.value='';}if(c.confirm)c.confirm.checked=false;changed();}},[['keep','保持（不写入）'],['replace','替换完整凭据对'],['clear','清除已保存凭据']].map(([value,text])=>h('option',{value},text))),
          ...(['client_id','client_secret'] as const).map(name=>h('div',{class:'credential-field',key:name},[
            h('label',{for:id+'-'+name},name==='client_id'?'Client ID':'Client Secret'),h('input',{id:id+'-'+name,type:'password',autocomplete:'off',maxlength:512,'data-role':name,ref:(node:unknown)=>{if(name==='client_id')c.clientId=node as HTMLInputElement;else c.clientSecret=node as HTMLInputElement;},onInput:()=>{edit();changed();},onChange:()=>{edit();changed();}}),
          ])),h('label',[h('input',{type:'checkbox','data-role':'credential-clear-confirm',ref:(node:unknown)=>{c.confirm=node as HTMLInputElement;},onChange:()=>{edit();changed();}}),'确认清除本组已保存凭据']),
          h('button',{type:'button','data-action':'credential-save',disabled:true,ref:(node:unknown)=>{c.save=node as HTMLButtonElement;},onClick:c.onSave},'提交本组操作'),
        ]});
      })),
      h('details',{class:'advanced-maintenance'},[h('summary','高级维护与启动修复'),h(NCard,{class:'card',title:'限定恢复'},{default:()=>[
        h('p','恢复默认仅作用于指定配置字段。缓存清理和持久业务数据删除尚无通用范围合同，当前不提供执行入口。解除挂载始终保留数据。'),
        h('p','回退仅处理受审普通配置迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。'),button('rollback','回退受审迁移'),
        h('p',h('label',[h('input',{id:'replacement',type:'checkbox'}),'以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）'])),button('recover','重新校验并恢复运行'),
      ]})]),
    ])});
  };}});
  const app=createApp(App);app.mount(container);
  return {owner(value:string|null){owner.value=value;},fields(value:Card[],onChange:()=>void){changed=onChange;cards.value=value;},credentials(value:Credential[],onChange:()=>void,notice=''){changed=onChange;credentials.value=value;credentialNotice.value=notice;},context(value:Record<string,unknown>){context.value=value||{};},dispose(){app.unmount();}};
}
