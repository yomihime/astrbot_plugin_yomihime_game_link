import {createApp, defineComponent, h, shallowRef} from 'vue';
import {NConfigProvider, darkTheme, zhCN, enUS, dateZhCN, dateEnUS} from 'naive-ui';
import {ActionBar, SettingsGroup, SettingsRow, StatusHint} from './ui/components';
import {fieldPresentation, groupTitle, ownerPresentation} from './ui/presentation';
import './ui/tokens.css';
import {createInlineConfirmation} from './confirmation';
import {fontStatusText, fontDownloadUrl, type FontPanel} from './font-settings';
import {followsCoreTheme, settingValues, type SettingChoice} from './setting-choices';
type Field={module_id:string;name:string;description:string;group?:string|null;editable?:boolean;default?:unknown;choices?:SettingChoice[];value_schema:null|{type:string;enum?:unknown[];minimum?:number;maximum?:number;maxLength?:number}};
type Control={field:Field;signature:string;problem?:string;inheritBaseline?:boolean;writePending?:boolean;baselineUnverified?:boolean;draftVersion:number;mode:HTMLInputElement|HTMLSelectElement|null;input:HTMLInputElement|HTMLSelectElement|null;reset?:HTMLButtonElement|null};
type Card={target:string;controls:Control[];save:HTMLButtonElement|null;discard?:HTMLButtonElement|null;onSave():void;onDiscard?():void};
type LegacyPanel={policy_id:string;target_owner:string;status:string|null;busy:boolean;enabled:boolean;plan():void;execute():void};
type MasterKeyPanel={storageSecurity:string;environmentPresent:boolean;environmentMatches:boolean;recoveryAction:string|null;migrationPlan:{file_count:number;source_retained:boolean;requires_stopped_host:boolean}|null;onAdopt():void;onPlanMigration():void;state:string;reason:string;source:string;generation:number;unknown:number;inventoryKnown:boolean;impact:{module_id:string;config_count:number;grant_count:number}[];enabled:boolean;busy:boolean;onDraft():void;onRetry():void;onRestore():void;onReset():void;onCancelReset():void};
const masterReason:Record<string,string>={key_missing:'原密钥缺失',key_unselected:'检测到旧数据，需要恢复原密钥',environment_key_missing:'部署环境密钥缺失',environment_key_invalid:'部署环境密钥格式无效',environment_key_conflict:'部署环境密钥与已选密钥冲突',managed_key_missing:'托管密钥文件缺失',managed_key_unreadable:'托管密钥文件无法安全读取',managed_key_conflict:'托管密钥文件与记录不匹配',inventory_decryption_failed:'原密钥无法解开全部旧凭据',inventory_unknown:'凭据库存无法安全确认',operation_incomplete:'上次操作未完成，可重试或按提示恢复',inventory_unavailable:'凭据库存暂不可用',manager_unavailable:'凭据存储暂不可用'};
masterReason.inventory_unknown='凭据库存无法安全确认，请检查存储后重试';
masterReason.encryption_component_unavailable='加密组件暂不可用，请检查宿主环境后重试';
masterReason.unsafe_storage_layout='凭据存储无法安全访问。请部署者检查运行身份与存储权限，修复后正常重启同一宿主；保留数据库、selector 和原密钥，不要清库或重填凭据。';
function groups(controls:Control[]){const map=new Map<string,Control[]>();for(const c of controls){const key=c.field.group||'';if(!map.has(key))map.set(key,[]);map.get(key)!.push(c);}return [...map].map(([group,items])=>({group,items:items.sort((a,b)=>(fieldPresentation(a.field).order||0)-(fieldPresentation(b.field).order||0))}));}
export function createManagementView(document:Document,container:HTMLElement=document.getElementById('management-root')!,scope:'settings'|'maintenance'='settings'){
 const confirmation=createInlineConfirmation(document);
 const font=shallowRef<FontPanel|null>(null);
 const legacy=shallowRef<LegacyPanel[]>([]);
 const cards=shallowRef<Card[]>([]),context=shallowRef<Record<string,unknown>>({}),masterKey=shallowRef<MasterKeyPanel|null>(null),resetConsent=shallowRef(false),owner=shallowRef<string|null>(null),draftRevision=shallowRef(0);let changed=()=>{};
 const mutate=()=>{draftRevision.value++;changed();},button=(id:string,text:string)=>h('button',{id,type:'button',disabled:true},text);
 const dirty=(c:Control)=>Boolean(c.mode&&c.mode.value!=='keep');
 function field(c:Control){const f=c.field,s=f.value_schema,p=fieldPresentation(f),id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,choices=settingValues(f),inheritMode=()=>c.inheritBaseline&&!c.writePending&&!c.baselineUnverified?'keep':'clear',edit=()=>{if(c.mode)c.mode.value=followsCoreTheme(f)&&c.input?.value!==''&&choices?.[Number(c.input?.value)]===null?inheritMode():'replace';c.draftVersion++;mutate();};
  return h(SettingsRow,{class:'config-field',key:c.signature,'data-field':f.name,'data-owner':f.module_id,'data-group':f.group||'general','data-width':p.width||(['integer','number','boolean'].includes(s?.type||'')?'short':'long')},{default:()=>[
   h('label',{for:id},p.label||f.description||f.name),h('input',{type:'hidden','data-role':'mode',ref:(node:unknown)=>{c.mode=node as HTMLInputElement;if(c.mode&&!c.mode.value)c.mode.value='keep';},onChange:()=>{c.draftVersion++;mutate();}}),
   h(choices?'select':'input',{id,'data-role':'value','aria-describedby':c.problem?id+'-detail':p.hint?id+'-hint':undefined,onInput:edit,onChange:edit,ref:(node:unknown)=>{c.input=node as HTMLInputElement;},...(!choices?{type:p.input||(['integer','number'].includes(s?.type||'')?'number':'text'),min:s?.minimum,max:s?.maximum,step:p.input==='time'?60:s?.type==='integer'?'1':'any',maxlength:s?.maxLength}:{})},choices?[h('option',{value:'',disabled:true},'请选择有效值'),...choices.map((value,index)=>h('option',{value:String(index)},f.choices?.[index]?.label||p.optionLabels?.[String(value)]||(p.booleanLabels&&typeof value==='boolean'?p.booleanLabels[String(value) as 'true'|'false']:String(value))))]:undefined),
   h('button',{type:'button','data-action':'default',ref:(node:unknown)=>{c.reset=node as HTMLButtonElement;},onClick:()=>{if(!c.mode||!c.input||c.reset?.disabled)return;c.mode.value=p.reset==='replace-default'?'replace':inheritMode();c.draftVersion++;const value=(f as Field&{default:unknown}).default;c.input.value=choices?String(choices.findIndex(choice=>choice===value)):value===null?'':String(value);mutate();}},'恢复默认'),c.problem?h('p',{class:'field-detail',id:id+'-detail',role:'status'},c.problem):p.hint?h('p',{class:'field-hint',id:id+'-hint'},p.hint):null,
  ]});
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
 const App=defineComponent({setup(){return()=>{draftRevision.value;const dark=context.value.isDark===true||context.value.theme==='dark',english=String(context.value.locale||'').startsWith('en');
  return h(NConfigProvider,{preflightStyleDisabled:true,theme:dark?darkTheme:null,locale:english?enUS:zhCN,dateLocale:english?dateEnUS:dateZhCN,inlineThemeDisabled:true},{default:()=>h('section',{class:'management'},[
   container.id==='management-root'?h('h1',scope==='maintenance'?'配置维护':'设置'):null,h('div',{class:'management-toolbar'},[h(StatusHint,{id:'status'}),button('refresh','刷新配置与版本')]),
   scope==='settings'?h('section',{id:'fields','aria-label':'普通配置'},cards.value.map(card=>{const advanced=card.controls.filter(c=>fieldPresentation(c.field).advanced||c.field.editable===false),normal=card.controls.filter(c=>!advanced.includes(c));return h('section',{class:'card',key:card.target,'data-owner':card.target,hidden:owner.value!==null&&owner.value!==card.target},[
    ...groups(normal).map(group=>h(SettingsGroup,{title:groupTitle(card.target,group.group),fieldset:true,class:'config-group','data-group':group.group||'general',key:group.group},{default:()=>[...group.items.map(field),card.target==='game_link/core'&&group.group==='reply_style'?fontControls():null]})),
    advanced.length?h('details',{class:'advanced-config'},[h('summary','只读与高级设置'),...groups(advanced).map(group=>h(SettingsGroup,{title:groupTitle(card.target,group.group),key:group.group},{default:()=>group.items.map(field)}))]):null,
    h(ActionBar,{class:'ordinary-actions'}, {default:()=>[h('button',{type:'button','data-action':'save',disabled:true,ref:(node:unknown)=>{card.save=node as HTMLButtonElement;},onClick:card.onSave},ownerPresentation(card.target)?.saveLabel||'保存普通设置'),h('button',{type:'button','data-action':'discard',ref:(node:unknown)=>{card.discard=node as HTMLButtonElement;},onClick:()=>card.onDiscard?.()},'放弃本模块修改'),card.controls.some(dirty)?h('span',{class:'dirty-indicator',role:'status'},'普通设置有未保存修改'):null]}),
   ]);})) : null,
   scope==='settings'?h(SettingsGroup,{title:'凭据存储',class:'master-key-panel',hidden:owner.value!==null&&owner.value!=='game_link/core'},{default:()=>{const panel=masterKey.value,canReset=Boolean(panel?.enabled&&!panel.busy&&panel.state!=='unknown'&&panel.reason!=='encryption_component_unavailable'&&panel.source!=='environment');if(!panel)return [h(StatusHint,{id:'master-key-status',tone:'warning'},{default:()=> '尚未读取凭据存储状态。'})];
    const recovery=()=>[
      panel.inventoryKnown?h('p',{class:'field-hint',id:'master-key-reset-impact'},`全域影响：配置凭据 ${panel.impact.reduce((sum,item)=>sum+item.config_count,0)} 项，授权凭据 ${panel.impact.reduce((sum,item)=>sum+item.grant_count,0)} 项。`):h('p',{class:'field-hint',id:'master-key-reset-impact'},'影响范围暂不可确认，请保留原数据。'),
      ...(panel.inventoryKnown?panel.impact:[]).map(item=>h('p',{class:'field-hint',key:item.module_id},`${item.module_id}：配置凭据 ${item.config_count} 项，授权凭据 ${item.grant_count} 项`)),
      h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-retry',type:'button',disabled:!panel.enabled||panel.busy,onClick:panel.onRetry},'重新核对存储')]}),
      h(SettingsRow,{}, {default:()=>[h('label',{for:'master-key-restore-input'},'恢复原密钥（标准 Base64，32 字节）'),h('input',{id:'master-key-restore-input',type:'password',autocomplete:'off',maxlength:128,disabled:!panel.enabled||panel.busy,'data-role':'master-key-restore',onInput:panel.onDraft}),h('button',{id:'master-key-restore',type:'button',disabled:!panel.enabled||panel.busy||panel.state==='unknown'||panel.reason==='encryption_component_unavailable',onClick:panel.onRestore},'验证并恢复原密钥')]}),
      h('p',{class:'field-hint'},'全域重配会使已保存的加密凭据逻辑失效，需重新配置凭据与授权。普通配置保留；这不代表密码学擦除。'),
      h('label',{class:'ui-check-label',for:'master-key-reset-consent'},[h('input',{id:'master-key-reset-consent',type:'checkbox',checked:resetConsent.value,disabled:!canReset,'aria-describedby':'master-key-reset-impact',onChange:(event:Event)=>{resetConsent.value=(event.currentTarget as HTMLInputElement).checked;panel.onDraft();}}),h('span','我了解所有模块旧凭据将失效，需重新配置。')]),
      h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-reset-cancel',type:'button',disabled:!resetConsent.value||panel.busy,onClick:panel.onCancelReset},'取消重配'),h('button',{id:'master-key-reset',type:'button',disabled:!canReset||!resetConsent.value,onClick:panel.onReset},'全域重配')]}),
    ];
    return [h(StatusHint,{id:'master-key-status',tone:panel.state==='ready'?'success':'warning'},{default:()=>panel.state==='ready'?'加密存储已就绪。':`加密存储需要处理：${masterReason[panel.reason]||'无法安全确认状态'}。`}),
      h(StatusHint,{id:'storage-security',tone:panel.storageSecurity==='private'?'success':'warning'},{default:()=>({private:'存储安全：私密边界已通过检查。',legacy_review_required:'存储安全：旧布局需复核，原数据保留。',blocked:'存储安全：检查未通过，禁止秘密写入。'}[panel.storageSecurity]||'存储安全：尚未确认。')}),
      h('p',{class:'field-hint'},'这里管理所有模块的加密存储。业务凭据在各模块配置；本地已配置、可解密均不代表上游验证成功。'),
      panel.source==='environment'?h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-adopt-environment',type:'button',disabled:!panel.enabled||panel.busy||!panel.environmentMatches,onClick:panel.onAdopt},'将当前环境密钥转为托管')]}):null,
      panel.source==='managed'&&panel.environmentPresent?h(StatusHint,{tone:panel.environmentMatches?'neutral':'warning'},{default:()=>panel.environmentMatches?'托管密钥已保存；可由部署者移除环境密钥配置后冷重启同一宿主。刷新不会修改进程环境。':'部署环境密钥冲突，请核对后冷重启；页面不会静默换钥。'}):null,
      panel.storageSecurity==='legacy_review_required'?h('section',{'aria-label':'旧存储迁移'},[
        h('p',{class:'field-hint'},'当前版本仅支持读取计划，没有旧部署执行适配器。请保留原数据；停止宿主或刷新页面不会使迁移可用。'),
        h('button',{id:'storage-migration-plan',type:'button',disabled:!panel.enabled||panel.busy,onClick:panel.onPlanMigration},'读取迁移计划'),
        panel.migrationPlan?h('p',`计划包含 ${panel.migrationPlan.file_count} 个文件；源保留。尚未迁移。`):null,
      ]):null,
      panel.state==='ready'?h('details',{class:'advanced-config'},[h('summary','恢复与全域重配'),...recovery()]):recovery(),
      panel.state==='ready'?null:h('details',{class:'advanced-config'},[h('summary','状态详情'),h('p',{class:'field-hint'},`来源：${panel.source}；代际：${panel.generation}；未知项：${panel.unknown}；原因代码：${panel.reason}`)]),
    ];}}):null,
   scope==='settings'&&legacy.value.length?h(SettingsGroup,{title:'旧普通配置迁移'},{default:()=>[
    h('p',{class:'field-hint'},'按已注册政策检查旧普通配置。仅在模块字段尚未配置时复制；源值保留，不涉及凭据。'),
    ...legacy.value.map(item=>h('section',{'data-legacy-policy':item.policy_id,key:item.policy_id},[
      h('p',item.target_owner+' · '+({migration_pending:'待显式迁移',already_configured:'模块已配置，不覆盖',not_applicable:'没有适用的旧配置',copied:'已复制'}[item.status||'']||'尚未读取计划')),
      h(ActionBar,{}, {default:()=>[h('button',{type:'button','data-action':'legacy-plan',disabled:!item.enabled||item.busy,onClick:item.plan},'检查迁移计划'),item.status==='migration_pending'?h('button',{type:'button','data-action':'legacy-execute',disabled:!item.enabled||item.busy,onClick:item.execute},'复制旧普通配置'):null]}),
    ])),
   ]}):null,
   confirmation.render(),
   scope==='maintenance'?h(SettingsGroup,{title:'限定恢复',class:'maintenance-panel'},{default:()=>[
    h('p','修正无效普通设置后重新校验并恢复运行。已有订阅及其它持久数据保留。'),button('recover','重新校验并恢复运行'),
   ]}):null,
  ])});
 };}});const app=createApp(App);app.mount(container);
 return {legacy(value:LegacyPanel[]){legacy.value=value;},confirm:confirmation.ask,cancelConfirmation:()=>confirmation.cancel(),font(value:FontPanel){font.value={...value};},owner(value:string|null){owner.value=value;},drafts(){draftRevision.value++;},resetConfirmation(){resetConsent.value=false;},masterKey(value:MasterKeyPanel|null){masterKey.value=value;},fields(value:Card[],onChange:()=>void){changed=onChange;cards.value=value;},context(value:Record<string,unknown>){context.value=value||{};},dispose(){confirmation.dispose();app.unmount();}};
}
