import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NConfigProvider, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
import {ActionBar, SettingsGroup, SettingsRow, StatusHint} from './ui/components';
import {credentialLabel, fieldPresentation, groupTitle, ownerPresentation} from './ui/presentation';
import './ui/tokens.css';
import {fontStatusText, fontDownloadUrl, type FontPanel} from './font-settings';
import {followsCoreTheme, settingValues, type SettingChoice} from './setting-choices';
type Field={module_id:string;name:string;description:string;group?:string|null;editable?:boolean;default?:unknown;choices?:SettingChoice[];value_schema:null|{type:string;enum?:unknown[];minimum?:number;maximum?:number;maxLength?:number}};
type Control={field:Field;signature:string;problem?:string;inheritBaseline?:boolean;writePending?:boolean;baselineUnverified?:boolean;draftVersion:number;mode:HTMLInputElement|HTMLSelectElement|null;input:HTMLInputElement|HTMLSelectElement|null;reset?:HTMLButtonElement|null};
type Card={target:string;controls:Control[];save:HTMLButtonElement|null;discard?:HTMLButtonElement|null;onSave():void;onDiscard?():void};
type Credential={field:{module_id:string;name:string;description:string;group?:string};signature:string;detail:string;draftVersion:number;mode:HTMLSelectElement|null;clientId:HTMLInputElement|null;clientSecret:HTMLInputElement|null;confirm:HTMLInputElement|null;save:HTMLButtonElement|null;onSave():void};
function groups(controls:Control[]){const map=new Map<string,Control[]>();for(const c of controls){const key=c.field.group||'';if(!map.has(key))map.set(key,[]);map.get(key)!.push(c);}return [...map].map(([group,items])=>({group,items:items.sort((a,b)=>(fieldPresentation(a.field).order||0)-(fieldPresentation(b.field).order||0))}));}
export function createManagementView(document:Document,container:HTMLElement=document.getElementById('management-root')!,scope:'settings'|'maintenance'='settings'){
 const font=shallowRef<FontPanel|null>(null);
 const cards=shallowRef<Card[]>([]),context=shallowRef<Record<string,unknown>>({}),credentials=shallowRef<Credential[]>([]),credentialNotice=shallowRef(''),owner=shallowRef<string|null>(null),draftRevision=shallowRef(0),regions=shallowRef<Record<string,string>>({}),revealed=shallowRef<Record<string,boolean>>({});let changed=()=>{};
 const mutate=()=>{draftRevision.value++;changed();},button=(id:string,text:string)=>h('button',{id,type:'button',disabled:true},text);
 const dirty=(c:Control)=>Boolean(c.mode&&c.mode.value!=='keep'),credentialDirty=(c:Credential)=>Boolean(c.mode&&c.mode.value!=='keep'||c.clientId?.value||c.clientSecret?.value);
 function field(c:Control){const f=c.field,s=f.value_schema,p=fieldPresentation(f),id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,choices=settingValues(f),inheritMode=()=>c.inheritBaseline&&!c.writePending&&!c.baselineUnverified?'keep':'clear',edit=()=>{if(c.mode)c.mode.value=followsCoreTheme(f)&&c.input?.value!==''&&choices?.[Number(c.input?.value)]===null?inheritMode():'replace';c.draftVersion++;mutate();};
  return h(SettingsRow,{class:'config-field',key:c.signature,'data-field':f.name,'data-owner':f.module_id,'data-group':f.group||'general','data-width':p.width||(['integer','number','boolean'].includes(s?.type||'')?'short':'long')},{default:()=>[
   h('label',{for:id},p.label||f.description||f.name),h('input',{type:'hidden','data-role':'mode',ref:(node:unknown)=>{c.mode=node as HTMLInputElement;if(c.mode&&!c.mode.value)c.mode.value='keep';},onChange:()=>{c.draftVersion++;mutate();}}),
   h(choices?'select':'input',{id,'data-role':'value','aria-describedby':c.problem?id+'-detail':p.hint?id+'-hint':undefined,onInput:edit,onChange:edit,ref:(node:unknown)=>{c.input=node as HTMLInputElement;},...(!choices?{type:p.input||(['integer','number'].includes(s?.type||'')?'number':'text'),min:s?.minimum,max:s?.maximum,step:p.input==='time'?60:s?.type==='integer'?'1':'any',maxlength:s?.maxLength}:{})},choices?[h('option',{value:'',disabled:true},'请选择有效值'),...choices.map((value,index)=>h('option',{value:String(index)},f.choices?.[index]?.label||(p.booleanLabels&&typeof value==='boolean'?p.booleanLabels[String(value) as 'true'|'false']:String(value))))]:undefined),
   h('button',{type:'button','data-action':'default',ref:(node:unknown)=>{c.reset=node as HTMLButtonElement;},onClick:()=>{if(!c.mode||!c.input||c.reset?.disabled)return;c.mode.value=p.reset==='replace-default'?'replace':inheritMode();c.draftVersion++;const value=(f as Field&{default:unknown}).default;c.input.value=choices?String(choices.findIndex(choice=>choice===value)):value===null?'':String(value);mutate();}},'恢复默认'),c.problem?h('p',{class:'field-detail',id:id+'-detail',role:'status'},c.problem):p.hint?h('p',{class:'field-hint',id:id+'-hint'},p.hint):null,
  ]});
 }
 function credential(c:Credential,active:boolean){const id=`credential-${encodeURIComponent(c.field.module_id)}-${encodeURIComponent(c.field.name)}`,label=credentialLabel(c.field),edit=()=>{c.draftVersion++;};
  return h('section',{class:'card credential-card',key:c.signature,'data-credential':c.field.name,'data-owner':c.field.module_id,hidden:!active,'aria-label':label},[
   h(StatusHint,{class:'credential-detail'},{default:()=>c.detail}),
   h(SettingsRow,{'data-width':'medium'},{default:()=>[h('label',{for:id+'-mode'},'本次操作'),h('select',{id:id+'-mode','data-role':'credential-mode',ref:(node:unknown)=>{c.mode=node as HTMLSelectElement;},onChange:()=>{edit();if(c.mode?.value!=='replace'){if(c.clientId)c.clientId.value='';if(c.clientSecret)c.clientSecret.value='';}if(c.confirm)c.confirm.checked=false;revealed.value={...revealed.value,[id+'-client_id']:false,[id+'-client_secret']:false};mutate();}},[['keep','保持（不写入）'],['replace','替换完整凭据对'],['clear','清除已保存凭据']].map(([value,text])=>h('option',{value},text)))]}),
   ...(['client_id','client_secret'] as const).map(name=>h(SettingsRow,{class:'credential-field',key:name},{default:()=>[h('label',{for:id+'-'+name},name==='client_id'?'Client ID':'Client Secret'),h('div',{class:'credential-input ui-control'},[
    h('input',{id:id+'-'+name,type:revealed.value[id+'-'+name]?'text':'password',autocomplete:'off',maxlength:512,'data-role':name,ref:(node:unknown)=>{if(name==='client_id')c.clientId=node as HTMLInputElement;else c.clientSecret=node as HTMLInputElement;},onInput:()=>{edit();if(c.confirm)c.confirm.checked=false;mutate();},onChange:()=>{edit();if(c.confirm)c.confirm.checked=false;mutate();}}),
    h('button',{type:'button','data-action':'reveal','aria-label':(revealed.value[id+'-'+name]?'隐藏':'显示')+'本次输入 '+(name==='client_id'?'Client ID':'Client Secret'),'aria-pressed':Boolean(revealed.value[id+'-'+name]),onClick:()=>{revealed.value={...revealed.value,[id+'-'+name]:!revealed.value[id+'-'+name]};}},revealed.value[id+'-'+name]?'隐藏':'显示'),
   ])]})),
   h('label',{class:'ui-check-label credential-confirm',hidden:c.mode?.value!=='clear'},[h('input',{type:'checkbox','data-role':'credential-clear-confirm',ref:(node:unknown)=>{c.confirm=node as HTMLInputElement;},onChange:()=>{edit();mutate();}}),h('span','确认清除本组已保存凭据')]),
   h(ActionBar,{}, {default:()=>[h('button',{type:'button','data-action':'credential-save',disabled:true,ref:(node:unknown)=>{c.save=node as HTMLButtonElement;},onClick:c.onSave},'保存'+label+'凭据'),credentialDirty(c)?h('span',{class:'dirty-indicator',role:'status'},label+'有未保存修改'):null]}),
  ]);
 }
 function fontControls(){const f=font.value;if(!f?.visible)return null;const disabled=!f.enabled||f.busy||f.status?.state==='downloading'||f.status?.state==='checking';return h('section',{'data-font-settings':'','aria-label':'图片字体','aria-busy':String(f.busy)},[
  h(StatusHint,{id:'font-status'},{default:()=>f.progress||f.message||fontStatusText(f.status)}),
  h('p',{class:'field-hint',id:'font-help'},'启动时自动获取标准字体；下载失败时优先使用系统字体；缺字时回复文字。上传仅接受指定版本并校验文件。'),
  h(ActionBar,{}, {default:()=>[
   h('button',{type:'button','data-action':'font-refresh',disabled:!f.enabled||f.busy,onClick:f.refresh},'刷新字体状态'),
   h('button',{type:'button','data-action':'font-retry',disabled:disabled||!f.status,onClick:f.retry},'重试下载'),
   h('label',{for:'font-upload'},'上传标准字体'),
   h('input',{id:'font-upload',type:'file',accept:'.otf',disabled:disabled||!f.status||!f.uploadAvailable,'aria-describedby':'font-help font-status',onChange:(event:Event)=>{const input=event.target as HTMLInputElement;const file=input.files?.[0]||null;input.value='';f.upload(file);}}),
  ]}),
  f.status?h('p',{class:'field-hint'},['指定文件：'+f.status.filename+' ',fontDownloadUrl(f.status)?h('a',{href:fontDownloadUrl(f.status)!,target:'_blank',rel:'noopener noreferrer'},'下载指定字体'):null]):null,
 ]);}
 const App=defineComponent({setup(){return()=>{draftRevision.value;const dark=context.value.isDark===true||context.value.theme==='dark',english=String(context.value.locale||'').startsWith('en'),credentialGroups=new Map<string,Credential[]>();for(const c of credentials.value){const key=JSON.stringify([c.field.module_id,c.field.group||'']);if(!credentialGroups.has(key))credentialGroups.set(key,[]);credentialGroups.get(key)!.push(c);}
  return h(NConfigProvider,{preflightStyleDisabled:true,theme:dark?darkTheme:null,locale:english?enUS:zhCN,dateLocale:english?dateEnUS:dateZhCN,inlineThemeDisabled:true},{default:()=>h('section',{class:'management'},[
   container.id==='management-root'?h('h1',scope==='maintenance'?'配置维护':'设置'):null,h('div',{class:'management-toolbar'},[h(StatusHint,{id:'status'}),button('refresh','刷新配置与版本')]),
   scope==='settings'?h('section',{id:'fields','aria-label':'普通配置'},cards.value.map(card=>{const advanced=card.controls.filter(c=>fieldPresentation(c.field).advanced||c.field.editable===false),normal=card.controls.filter(c=>!advanced.includes(c));return h('section',{class:'card',key:card.target,'data-owner':card.target,hidden:owner.value!==null&&owner.value!==card.target},[
    ...groups(normal).map(group=>h(SettingsGroup,{title:groupTitle(card.target,group.group),fieldset:true,class:'config-group','data-group':group.group||'general',key:group.group},{default:()=>[...group.items.map(field),card.target==='game_link/core'&&group.group==='reply_style'?fontControls():null]})),
    advanced.length?h('details',{class:'advanced-config'},[h('summary','只读与高级设置'),...groups(advanced).map(group=>h(SettingsGroup,{title:groupTitle(card.target,group.group),key:group.group},{default:()=>group.items.map(field)}))]):null,
    h(ActionBar,{class:'ordinary-actions'}, {default:()=>[h('button',{type:'button','data-action':'save',disabled:true,ref:(node:unknown)=>{card.save=node as HTMLButtonElement;},onClick:card.onSave},ownerPresentation(card.target)?.saveLabel||'保存普通设置'),h('button',{type:'button','data-action':'discard',ref:(node:unknown)=>{card.discard=node as HTMLButtonElement;},onClick:()=>card.onDiscard?.()},'放弃本模块修改'),card.controls.some(dirty)?h('span',{class:'dirty-indicator',role:'status'},'普通设置有未保存修改'):null]}),
   ]);})) : null,
   scope==='settings'?h(StatusHint,{id:'credential-status',tone:'warning',hidden:owner.value!==null&&credentials.value.length>0&&!credentials.value.some(c=>c.field.module_id===owner.value)},{default:()=>credentialNotice.value}):null,scope==='settings'?h('section',{id:'credentials','aria-label':'来源凭据'},[...credentialGroups].map(([key,items])=>{const current=items.some(c=>c.field.name===regions.value[key])?regions.value[key]:items[0].field.name;return h(SettingsGroup,{title:groupTitle(items[0].field.module_id,items[0].field.group||''),key,hidden:owner.value!==null&&owner.value!==items[0].field.module_id},{default:()=>[
    items.length>1?h('nav',{class:'credential-tabs','aria-label':'凭据来源'},items.map(c=>h('button',{type:'button','aria-pressed':current===c.field.name,onClick:()=>{regions.value={...regions.value,[key]:c.field.name};}},credentialLabel(c.field)+(credentialDirty(c)?' · 未保存':'')))):null,...items.map(c=>credential(c,current===c.field.name)),
   ]});})):null,
   scope==='maintenance'?h(SettingsGroup,{title:'限定恢复',class:'maintenance-panel'},{default:()=>[
    h('p','仅处理受审普通配置迁移；后续修改会阻止回退。订阅及其它持久数据保留。'),button('rollback','回退受审迁移'),h('p',h('label',{class:'ui-check-label'},[h('input',{id:'replacement',type:'checkbox',onChange:mutate}),h('span','以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）')])),button('recover','重新校验并恢复运行'),
   ]}):null,
  ])});
 };}});const app=createApp(App);app.mount(container);
 return {font(value:FontPanel){font.value={...value};},owner(value:string|null){owner.value=value;},drafts(){draftRevision.value++;},maskSecrets(){revealed.value={};},fields(value:Card[],onChange:()=>void){changed=onChange;cards.value=value;},credentials(value:Credential[],onChange:()=>void,notice=''){changed=onChange;credentials.value=value;credentialNotice.value=notice;},context(value:Record<string,unknown>){context.value=value||{};},dispose(){app.unmount();}};
}
