import {createApp,defineComponent,h,ref} from 'vue';
import {UiProvider,LogoMark,SettingsLayout,SettingsGroup,SettingsRow,Surface,ActionBar,StatusHint} from '@game-link/ui';
import type {PageOptions,MountedPage} from '../../../../pages/public-contract';
import {createSettingsController,ordinaryFields,credentialFields} from './settings-controller';

export function mountSettings(container:HTMLElement,options:PageOptions):MountedPage {
  const revision=ref(0),activeRegion=ref('credential_fflogs_cn');
  const controller=createSettingsController(options,()=>{revision.value++;options.onNavigationStateChange?.();});
  const app=createApp(defineComponent({setup(){return()=>{
    revision.value;const s=controller.state,ordinaryDisabled=!s.draftEditable||!s.context.available,disabled=s.pending||!s.ready||!s.context.available;
    const button=(label:string,props:Record<string,unknown>={})=>h('button',{type:'button',class:'ui-button',...props},label);
    const row=(f:typeof ordinaryFields[number])=>h(SettingsRow,{key:f.name},{default:()=>[
      h('label',{for:`ff14-config-${f.name}`},f.label),
      f.name==='reply_theme'?h('select',{class:'ui-control',id:'ff14-config-reply_theme',value:s.draft.reply_theme,disabled:ordinaryDisabled,onChange:(e:Event)=>controller.edit(f.name,(e.target as HTMLSelectElement).value)},[
        h('option',{value:''},'跟随全局设置'),...s.themeChoices.map(choice=>h('option',{value:choice.value},choice.label)),
        s.draft.reply_theme&&!s.themeChoices.some(choice=>choice.value===s.draft.reply_theme)?h('option',{value:s.draft.reply_theme,disabled:true},'当前主题不可用，请重新选择'):null,
      ]):'choices' in f?h('select',{class:'ui-control',id:`ff14-config-${f.name}`,value:s.draft[f.name],disabled:ordinaryDisabled,onChange:(e:Event)=>controller.edit(f.name,(e.target as HTMLSelectElement).value)},f.choices.map(([value,label])=>h('option',{value},label))):
        h('input',{class:'ui-control',id:`ff14-config-${f.name}`,value:s.draft[f.name]||'',type:f.name==='ff14_calendar_default_delivery_time'?'time':f.name==='ff14_calendar_default_days'?'number':'text',min:f.name==='ff14_calendar_default_days'?1:undefined,max:f.name==='ff14_calendar_default_days'?30:undefined,disabled:ordinaryDisabled,onInput:(e:Event)=>controller.edit(f.name,(e.target as HTMLInputElement).value)}),
    ]});
    const fieldGroup=(title:string,number:string,names:readonly string[])=>h(SettingsGroup,{title,number},{default:()=>names.map(name=>ordinaryFields.find(f=>f.name===name)!).filter(f=>f.name!=='reply_theme'||s.themeAvailable).map(row)});
    return h(UiProvider,{theme:s.context.theme,locale:s.context.locale,styleMountTarget:container},{default:()=>h('section',{class:'ff14-page ff14-settings','data-theme':s.context.theme},
      options.routeId==='home'?[h(Surface,{title:'最终幻想 XIV',eyebrow:'FINAL FANTASY XIV / MODULE'},{default:()=>[
        // Reviewed static local artwork, never remote or user-supplied markup.
        h(LogoMark,{class:'ff14-module-mark','aria-hidden':'true'}),
        h('p','物品、市场、角色 Logs 与活动日历。请从模块导航选择查询，或进入业务配置维护默认值和来源凭据。'),
        h(StatusHint,{},()=> '查询只在手动提交后执行；缺失、失败与覆盖限制以本次来源结果为准。'),
        h(StatusHint,{},()=> '主密钥、存储恢复、全局主题和字体由全局设置管理。'),
      ]})]:[
        h(SettingsLayout,{}, {default:()=>h('form',{class:'ff14-form',onSubmit:(e:Event)=>{e.preventDefault();void controller.save();}},[
          fieldGroup('查询与回复','01',['ff14_default_region','reply_theme']),
          fieldGroup('日历与订阅','02',['ff14_calendar_default_days','ff14_calendar_default_timezone','ff14_calendar_default_delivery_time','ff14_calendar_sync_interval_seconds','ff14_subscriptions_enabled']),
          h(ActionBar,{},()=>[button('保存配置',{type:'submit','data-variant':'primary',disabled:disabled||!s.dirty}),button('放弃修改',{disabled:s.pending||ordinaryDisabled||!s.dirty,onClick:controller.discard})]),
          h(StatusHint,{},()=> '修改默认值不会重写已有订阅；提醒按既有调度检查，不承诺准点送达。'),
        ]),aside:()=>h(Surface,{title:'FFLogs 来源凭据',eyebrow:'SOURCE ACCESS'},{default:()=>[
          h(StatusHint,{},()=> '国服与国际服独立配置。保存与上游验证相互独立。'),
          h('div',{class:'ui-segmented','aria-label':'凭据区域'},credentialFields.map(f=>button(f.label,{'aria-pressed':activeRegion.value===f.name,onClick:()=>{activeRegion.value=f.name;}}))),
          ...credentialFields.map(f=>{const status=s.credentials?.fields[f.name];return h('form',{key:f.name,class:'ff14-credential',hidden:activeRegion.value!==f.name,'aria-label':`${f.label} FFLogs 凭据`,onSubmit:(e:Event)=>{e.preventDefault();void controller.replace(f.name);}},[
            h('p',{class:'ui-badge'},!status?'状态未读取':!status.configured?'未配置':status.decryptable?'已配置 · 可解密 · 上游未验证':'已配置 · 当前不可解密'),
            ...(['client_id','client_secret'] as const).map(name=>h(SettingsRow,{key:name},{default:()=>[
              h('label',{for:`${f.name}-${name}`},name==='client_id'?'Client ID':'Client Secret'),
              h('input',{class:'ui-control',value:s.pairs[f.name][name],type:'password',disabled,maxlength:512,id:`${f.name}-${name}`,autocomplete:'off',onInput:(e:Event)=>controller.editPair(f.name,name,(e.target as HTMLInputElement).value)}),
            ]})),
            h(ActionBar,{},()=>[button('保存凭据',{type:'submit','data-variant':'primary',disabled}),button('放弃输入',{disabled:s.pending,onClick:()=>controller.discardPair(f.name)}),button('清除凭据',{disabled:disabled||!status?.configured,onClick:()=>void controller.clear(f.name)})]),
          ]);}),
          h(StatusHint,{},()=> '凭据不回显。加密存储由全局设置统一管理。'),
        ]})}),
        h(ActionBar,{},()=>[button('重新读取状态',{disabled:s.pending||!s.context.available,onClick:()=>void controller.load()}),s.pending?button('停止等待',{onClick:controller.cancel}):null]),
        h('p',{class:s.error?'ff14-error':'ff14-hint',role:s.error?'alert':'status','aria-live':'polite'},s.message),
      ])});
  };}}));
  app.mount(container);if(options.routeId==='config')void controller.load();
  let disposed=false;const dispose=()=>{if(disposed)return;disposed=true;controller.dispose();app.unmount();container.replaceChildren();};options.scope.onDispose(dispose);
  return {update:controller.update,dispose,navigationImpact:controller.navigationImpact};
}
