import {createApp, defineComponent, h, shallowRef} from 'vue';
import {UiProvider, ActionBar, SettingsGroup, SettingsRow, FormField, StatusHint, uiText, uiThemeLabel} from '@game-link/ui';
import type {FormFieldSlot} from '@game-link/ui';
import {fieldPresentation, groupTitle} from './ui/presentation';
import {createInlineConfirmation} from './confirmation';
import {fontStatusText, fontDownloadUrl, type FontPanel} from './font-settings';
import {followsCoreTheme, isCoreThemeField, settingValues, type SettingChoice} from './setting-choices';
import {coreText, type CoreTextKey} from './i18n/messages';
import {normalizeUiLocale, type UiParams} from './i18n/locale';
type Field={module_id:string;name:string;description:string;group?:string|null;editable?:boolean;default?:unknown;choices?:SettingChoice[];value_schema:null|{type:string;enum?:unknown[];minimum?:number;maximum?:number;maxLength?:number}};
type Control={field:Field;signature:string;problem?:CoreTextKey|'';inheritBaseline?:boolean;writePending?:boolean;baselineUnverified?:boolean;draftVersion:number;mode:HTMLInputElement|HTMLSelectElement|null;input:HTMLInputElement|HTMLSelectElement|null;reset?:HTMLButtonElement|null};
type Card={target:string;controls:Control[];save:HTMLButtonElement|null;discard?:HTMLButtonElement|null;onSave():void;onDiscard?():void};
type LegacyPanel={policy_id:string;target_owner:string;status:string|null;busy:boolean;enabled:boolean;plan():void;execute():void};
type MasterKeyPanel={storageSecurity:string;environmentPresent:boolean;environmentMatches:boolean;recoveryAction:string|null;migrationPlan:{file_count:number;source_retained:boolean;requires_stopped_host:boolean}|null;onAdopt():void;onPlanMigration():void;state:string;reason:string;source:string;generation:number;unknown:number;inventoryKnown:boolean;impact:{module_id:string;config_count:number;grant_count:number}[];enabled:boolean;busy:boolean;onDraft():void;onRetry():void;onRestore():void;onReset():void;onCancelReset():void};
const masterReason:Record<string,CoreTextKey>={key_missing:'keys.missing',key_unselected:'keys.unselected',environment_key_missing:'keys.envMissing',environment_key_invalid:'keys.envInvalid',environment_key_conflict:'keys.envConflict',managed_key_missing:'keys.managedMissing',managed_key_unreadable:'keys.managedUnreadable',managed_key_conflict:'keys.managedConflict',inventory_decryption_failed:'keys.decryptFailed',inventory_unknown:'keys.inventoryUnknown',operation_incomplete:'keys.incomplete',inventory_unavailable:'keys.inventoryUnavailable',manager_unavailable:'keys.storageUnavailable'};
masterReason.inventory_unknown='keys.inventoryRetry';
masterReason.encryption_component_unavailable='keys.encryptionUnavailable';
masterReason.unsafe_storage_layout='keys.unsafeStorage';
function groups(controls:Control[]){const map=new Map<string,Control[]>();for(const c of controls){const key=c.field.group||'';if(!map.has(key))map.set(key,[]);map.get(key)!.push(c);}return [...map].map(([group,items])=>({group,items:items.sort((a,b)=>(fieldPresentation(a.field).order||0)-(fieldPresentation(b.field).order||0))}));}
export function createManagementView(document:Document,container:HTMLElement=document.getElementById('management-root')!,scope:'settings'|'maintenance'='settings'){
 const localeOverride=shallowRef<string|null>(null);
 const labels=shallowRef<Map<string,string>>(new Map());

 const font=shallowRef<FontPanel|null>(null);
 const legacy=shallowRef<LegacyPanel[]>([]);
 const cards=shallowRef<Card[]>([]),context=shallowRef<Record<string,unknown>>({}),masterKey=shallowRef<MasterKeyPanel|null>(null),resetConsent=shallowRef(false),owner=shallowRef<string|null>(null),draftRevision=shallowRef(0);let changed=()=>{};
 const locale=()=>localeOverride.value ?? normalizeUiLocale(context.value.locale) ?? 'zh-CN';
 const t=(key:CoreTextKey, params?:UiParams)=>coreText(locale(),key,params);
 const ownerLabel=(id:string)=>id==='game_link/core'?t('shell.global'):labels.value.get(id)||t('settings.moduleFallback',{id});
 const confirmation=createInlineConfirmation(document,()=>t('common.cancel'));
 const mutate=()=>{draftRevision.value++;changed();},button=(id:string,text:string)=>h('button',{id,type:'button',disabled:true},text);
 const dirty=(c:Control)=>Boolean(c.mode&&c.mode.value!=='keep');
 function field(c:Control){const f=c.field,s=f.value_schema,p=fieldPresentation(f,locale()),id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,choices=settingValues(f),inheritMode=()=>c.inheritBaseline&&!c.writePending&&!c.baselineUnverified?'keep':'clear',edit=()=>{if(c.mode)c.mode.value=followsCoreTheme(f)&&c.input?.value!==''&&choices?.[Number(c.input?.value)]===null?inheritMode():'replace';c.draftVersion++;mutate();};
  return h(FormField,{controlId:id,label:p.label||f.description||f.name,layout:'row',hint:c.problem?undefined:p.hint,error:c.problem?t(c.problem):undefined,hintId:id+'-hint',errorId:id+'-detail',class:'config-field',key:c.signature,'data-field':f.name,'data-owner':f.module_id,'data-group':f.group||'general','data-width':p.width||(['integer','number','boolean'].includes(s?.type||'')?'short':'long')},{default:({controlAttrs}:FormFieldSlot)=>[
   h('input',{type:'hidden','data-role':'mode',ref:(node:unknown)=>{c.mode=node as HTMLInputElement;if(c.mode&&!c.mode.value)c.mode.value='keep';},onChange:()=>{c.draftVersion++;mutate();}}),
   h(choices?'select':'input',{...controlAttrs,'data-role':'value',onInput:edit,onChange:edit,ref:(node:unknown)=>{c.input=node as HTMLInputElement;},...(!choices?{type:p.input||(['integer','number'].includes(s?.type||'')?'number':'text'),min:s?.minimum,max:s?.maximum,step:p.input==='time'?60:s?.type==='integer'?'1':'any',maxlength:s?.maxLength}:{})},choices?[h('option',{value:'',disabled:true},t('settings.placeholder')),...choices.map((value,index)=>h('option',{value:String(index)},isCoreThemeField(f)?uiThemeLabel(value as string|null,f.choices?.[index]?.label||String(value),locale()):p.optionLabels?.[String(value)]||f.choices?.[index]?.label||(p.booleanLabels&&typeof value==='boolean'?p.booleanLabels[String(value) as 'true'|'false']:String(value))))]:undefined),
   h('button',{type:'button','data-action':'default',ref:(node:unknown)=>{c.reset=node as HTMLButtonElement;},onClick:()=>{if(!c.mode||!c.input||c.reset?.disabled)return;c.mode.value=p.reset==='replace-default'?'replace':inheritMode();c.draftVersion++;const value=(f as Field&{default:unknown}).default;c.input.value=choices?String(choices.findIndex(choice=>choice===value)):value===null?'':String(value);mutate();}},uiText('resetDefault',locale())),
  ]});
 }
 function fontControls(){const f=font.value;if(!f?.visible)return null;const disabled=!f.enabled||f.busy||f.status?.state==='downloading'||f.status?.state==='checking';return h('section',{class:'global-font-panel','data-font-settings':'','aria-label':t('settings.font'),'aria-busy':String(f.busy)},[
  h('h2',t('settings.font')),
  h(StatusHint,{id:'font-status'},{default:()=>f.progress||f.message||fontStatusText(f.status,locale())}),
  h('p',{class:'field-hint',id:'font-help'},t('settings.fontHelp')),
  h(ActionBar,{}, {default:()=>[
   h('button',{type:'button','data-action':'font-refresh',disabled:!f.enabled||f.busy,onClick:f.refresh},t('settings.fontRefresh')),
   h('button',{type:'button','data-action':'font-retry',disabled:disabled||!f.status,onClick:f.retry},t('settings.fontRetry')),
   h('label',{for:'font-upload'},t('settings.fontUpload')),
   h('input',{id:'font-upload',type:'file',accept:'.otf',disabled:disabled||!f.status||!f.uploadAvailable,'aria-describedby':'font-help font-status',onChange:(event:Event)=>{const input=event.target as HTMLInputElement;const file=input.files?.[0]||null;input.value='';f.upload(file);}}),
  ]}),
  f.status?h('p',{class:'field-hint'},[t('settings.fontFile')+f.status.filename+' ',fontDownloadUrl(f.status)?h('a',{href:fontDownloadUrl(f.status)!,target:'_blank',rel:'noopener noreferrer'},t('settings.fontDownload')):null]):null,
 ]);}
 const App=defineComponent({setup(){return()=>{draftRevision.value;const dark=typeof context.value.isDark==='boolean'?context.value.isDark:context.value.theme==='dark';
  return h(UiProvider,{theme:dark?'dark':'light',locale:locale(),styleMountTarget:container},{default:()=>h('section',{class:['management',scope==='settings'&&(owner.value===null||owner.value==='game_link/core')?'management-global':'']},[
   container.id==='management-root'?h('h1',scope==='maintenance'?t('shell.maintenance'):t('settings.heading')):null,h('div',{class:'management-toolbar'},[h(StatusHint,{id:'status'}),button('refresh',t('settings.refreshRevision'))]),
   scope==='settings'?h('section',{id:'fields','aria-label':t('settings.ordinaryConfig')},cards.value.map(card=>{const advanced=card.controls.filter(c=>fieldPresentation(c.field).advanced||c.field.editable===false),normal=card.controls.filter(c=>!advanced.includes(c));return h('section',{class:'card',key:card.target,'data-owner':card.target,hidden:owner.value!==null&&owner.value!==card.target},[
    ...groups(normal).map(group=>h(SettingsGroup,{title:groupTitle(group.group,locale()),fieldset:true,class:'config-group','data-group':group.group||'general',key:group.group},{default:()=>group.items.map(field)})),
    advanced.length?h('details',{class:'advanced-config'},[h('summary',t('settings.advanced')),...groups(advanced).map(group=>h(SettingsGroup,{title:groupTitle(group.group,locale()),key:group.group},{default:()=>group.items.map(field)}))]):null,
    h(ActionBar,{class:'ordinary-actions'}, {default:()=>[h('button',{type:'button','data-action':'save',disabled:true,ref:(node:unknown)=>{card.save=node as HTMLButtonElement;},onClick:card.onSave},card.target==='game_link/core'?t('settings.saveGlobal'):t('settings.saveOrdinary')),h('button',{type:'button','data-action':'discard',ref:(node:unknown)=>{card.discard=node as HTMLButtonElement;},onClick:()=>card.onDiscard?.()},card.target==='game_link/core'?t('settings.discardGlobal'):t('settings.discardModule')),card.controls.some(dirty)?h('span',{class:'dirty-indicator',role:'status'},t('settings.ordinaryDirty')):null]}),
    card.target==='game_link/core'?fontControls():null,
   ]);})) : null,
   scope==='settings'?h(SettingsGroup,{title:t('keys.storage'),class:'master-key-panel',hidden:owner.value!==null&&owner.value!=='game_link/core'},{default:()=>{const panel=masterKey.value,canReset=Boolean(panel?.enabled&&!panel.busy&&panel.state!=='unknown'&&panel.reason!=='encryption_component_unavailable'&&panel.source!=='environment');if(!panel)return [h(StatusHint,{id:'master-key-status',tone:'warning'},{default:()=> t('keys.unread')})];
    const recovery=()=>[
      panel.inventoryKnown?h('p',{class:'field-hint',id:'master-key-reset-impact'},t('keys.impact',{config:panel.impact.reduce((sum,item)=>sum+item.config_count,0),grants:panel.impact.reduce((sum,item)=>sum+item.grant_count,0)})):h('p',{class:'field-hint',id:'master-key-reset-impact'},t('keys.impactUnknown')),
      ...(panel.inventoryKnown?panel.impact:[]).map(item=>h('p',{class:'field-hint',key:item.module_id},t('keys.ownerImpact',{owner:ownerLabel(item.module_id),config:item.config_count,grants:item.grant_count}))),
      h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-retry',type:'button',disabled:!panel.enabled||panel.busy,onClick:panel.onRetry},t('keys.recheck'))]}),
      h(SettingsRow,{}, {default:()=>[h('label',{for:'master-key-restore-input'},t('keys.restoreInput')),h('input',{id:'master-key-restore-input',type:'password',autocomplete:'off',maxlength:128,disabled:!panel.enabled||panel.busy,'data-role':'master-key-restore',onInput:panel.onDraft}),h('button',{id:'master-key-restore',type:'button',disabled:!panel.enabled||panel.busy||panel.state==='unknown'||panel.reason==='encryption_component_unavailable',onClick:panel.onRestore},t('keys.restore'))]}),
      h('p',{class:'field-hint'},t('keys.resetWarning')),
      h('label',{class:'ui-check-label',for:'master-key-reset-consent'},[h('input',{id:'master-key-reset-consent',type:'checkbox',checked:resetConsent.value,disabled:!canReset,'aria-describedby':'master-key-reset-impact',onChange:(event:Event)=>{resetConsent.value=(event.currentTarget as HTMLInputElement).checked;panel.onDraft();}}),h('span',t('keys.resetConsent'))]),
      h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-reset-cancel',type:'button',disabled:!resetConsent.value||panel.busy,onClick:panel.onCancelReset},t('keys.cancelReset')),h('button',{id:'master-key-reset',type:'button',disabled:!canReset||!resetConsent.value,onClick:panel.onReset},t('keys.reset'))]}),
    ];
    return [h(StatusHint,{id:'master-key-status',tone:panel.state==='ready'?'success':'warning'},{default:()=>panel.state==='ready'?t('keys.ready'):t('keys.actionNeeded',{reason:t(masterReason[panel.reason]||'keys.unknown')})}),
      h(StatusHint,{id:'storage-security',tone:panel.storageSecurity==='private'?'success':'warning'},{default:()=>({private:t('keys.securityPassed'),legacy_review_required:t('keys.securityLegacy'),blocked:t('keys.securityBlocked')}[panel.storageSecurity]||t('keys.securityUnknown'))}),
      h('p',{class:'field-hint'},t('keys.intro')),
      panel.source==='environment'?h(ActionBar,{}, {default:()=>[h('button',{id:'master-key-adopt-environment',type:'button',disabled:!panel.enabled||panel.busy||!panel.environmentMatches,onClick:panel.onAdopt},t('keys.adopt'))]}):null,
      panel.source==='managed'&&panel.environmentPresent?h(StatusHint,{tone:panel.environmentMatches?'neutral':'warning'},{default:()=>panel.environmentMatches?t('keys.managedSaved'):t('keys.envConflictRestart')}):null,
      panel.storageSecurity==='legacy_review_required'?h('section',{'aria-label':t('keys.migrateLegacy')},[
        h('p',{class:'field-hint'},t('keys.migrationIntro')),
        h('button',{id:'storage-migration-plan',type:'button',disabled:!panel.enabled||panel.busy,onClick:panel.onPlanMigration},t('keys.readPlan')),
        panel.migrationPlan?h('p',t('keys.planFiles',{count:panel.migrationPlan.file_count})):null,
      ]):null,
      panel.state==='ready'?h('details',{class:'advanced-config'},[h('summary',t('keys.recovery')),...recovery()]):recovery(),
      panel.state==='ready'?null:h('details',{class:'advanced-config'},[h('summary',t('keys.details')),h('p',{class:'field-hint'},t('keys.statusDetails',{source:panel.source,generation:panel.generation,unknown:panel.unknown,reason:panel.reason}))]),
    ];}}):null,
   scope==='settings'&&legacy.value.length?h(SettingsGroup,{title:t('settings.legacy')},{default:()=>[
    h('p',{class:'field-hint'},t('settings.legacyIntro')),
    ...legacy.value.map(item=>h('section',{'data-legacy-policy':item.policy_id,key:item.policy_id},[
      h('p',ownerLabel(item.target_owner)+' · '+({migration_pending:t('settings.legacyPending'),already_configured:t('settings.legacyConfigured'),not_applicable:t('settings.legacyNotApplicable'),copied:t('settings.legacyCopied')}[item.status||'']||t('settings.legacyUnread'))),
      h(ActionBar,{}, {default:()=>[h('button',{type:'button','data-action':'legacy-plan',disabled:!item.enabled||item.busy,onClick:item.plan},t('settings.legacyInspect')),item.status==='migration_pending'?h('button',{type:'button','data-action':'legacy-execute',disabled:!item.enabled||item.busy,onClick:item.execute},t('settings.legacyCopy')):null]}),
    ])),
   ]}):null,
   confirmation.render(),
   scope==='maintenance'?h(SettingsGroup,{title:t('settings.restrictedRecovery'),class:'maintenance-panel'},{default:()=>[
    h('p',t('settings.recoveryIntro')),button('recover',t('settings.recoveryRun')),
   ]}):null,
  ])});
 };}});const app=createApp(App);app.mount(container);
 return {labels(value:Map<string,string>){labels.value=new Map(value);},legacy(value:LegacyPanel[]){legacy.value=value;},confirm:confirmation.ask,cancelConfirmation:()=>confirmation.cancel(),font(value:FontPanel){font.value=value;},owner(value:string|null){owner.value=value;},drafts(){draftRevision.value++;},resetConfirmation(){resetConsent.value=false;},masterKey(value:MasterKeyPanel|null){masterKey.value=value;},fields(value:Card[],onChange:()=>void){changed=onChange;cards.value=value;},context(value:Record<string,unknown>){context.value=value||{};},setLocale(value:string){localeOverride.value=value;},dispose(){confirmation.dispose();app.unmount();}};
}
