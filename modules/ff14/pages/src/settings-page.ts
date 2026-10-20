import {createApp,defineComponent,h,ref} from 'vue';
import {NConfigProvider,NCard,NButton,NInput,darkTheme} from 'naive-ui';
import type {PageOptions,MountedPage} from '../../../../pages/public-contract';
import {createSettingsController,ordinaryFields,credentialFields} from './settings-controller';

export function mountSettings(container:HTMLElement,options:PageOptions):MountedPage {
  const revision=ref(0),controller=createSettingsController(options,()=>{revision.value++;options.onNavigationStateChange?.();});
  const app=createApp(defineComponent({setup(){return()=>{
    revision.value;const s=controller.state,ordinaryDisabled=!s.draftEditable||!s.context.available,disabled=s.pending||!s.ready||!s.context.available;
    return h(NConfigProvider,{theme:s.context.theme==='dark'?darkTheme:null,themeOverrides:{common:{primaryColor:'#8763c5',primaryColorHover:'#9b79d6',primaryColorPressed:'#6f4db1'}},styleMountTarget:container,preflightStyleDisabled:true,inlineThemeDisabled:true},{default:()=>h('section',{class:'ff14-page ff14-settings','data-theme':s.context.theme},
      options.routeId==='home'?[h(NCard,{title:'最终幻想 XIV',bordered:false},{default:()=>[
        h('p','物品、市场、角色 Logs 与活动日历。请从模块导航选择查询，或进入业务配置维护默认值和来源凭据。'),
        h('p',{class:'ff14-hint'},'查询只在手动提交后执行；缺失、失败与覆盖限制以本次来源结果为准。'),
        h('p',{class:'ff14-hint'},'主密钥、存储恢复、全局主题和字体由全局设置管理。'),
      ]})]:[
        h(NCard,{title:'FF14 业务配置',bordered:false},{default:()=>[
          h('form',{class:'ff14-form',onSubmit:(e:Event)=>{e.preventDefault();void controller.save();}},[
            ...ordinaryFields.filter(f=>f.name!=='reply_theme'||s.themeAvailable).map(f=>h('div',{class:'ff14-field'},[
              h('label',{for:`ff14-config-${f.name}`},f.label),
              f.name==='reply_theme'?h('select',{id:'ff14-config-reply_theme',value:s.draft.reply_theme,disabled:ordinaryDisabled,onChange:(e:Event)=>controller.edit(f.name,(e.target as HTMLSelectElement).value)},[
                h('option',{value:''},'跟随全局设置'),...s.themeChoices.map(choice=>h('option',{value:choice.value},choice.label)),
                s.draft.reply_theme&&!s.themeChoices.some(choice=>choice.value===s.draft.reply_theme)?h('option',{value:s.draft.reply_theme,disabled:true},'当前主题不可用，请重新选择'):null,
              ]):'choices' in f?h('select',{id:`ff14-config-${f.name}`,value:s.draft[f.name],disabled:ordinaryDisabled,onChange:(e:Event)=>controller.edit(f.name,(e.target as HTMLSelectElement).value)},f.choices.map(([value,label])=>h('option',{value},label))):
                h(NInput,{value:s.draft[f.name]||'',disabled:ordinaryDisabled,inputProps:{id:`ff14-config-${f.name}`},'onUpdate:value':(value:string)=>controller.edit(f.name,value)}),
            ])),
            h('div',{class:'ff14-actions'},[h(NButton,{type:'primary',attrType:'submit',disabled:disabled||!s.dirty},()=> '保存配置'),h(NButton,{disabled:s.pending||!s.dirty,onClick:controller.discard},()=> '放弃修改')]),
          ]),
        ]}),
        h(NCard,{title:'FFLogs 来源凭据',bordered:false},{default:()=>[
          h('p',{class:'ff14-hint'},'直接保存或替换完整凭据对。存储和密钥异常请到全局设置处理；这里不验证上游。'),
          ...credentialFields.map(f=>{const status=s.credentials?.fields[f.name];return h('form',{class:'ff14-credential',onSubmit:(e:Event)=>{e.preventDefault();void controller.replace(f.name);}},[
            h('h3',f.label),h('p',{class:'ff14-hint'},!status?'状态未读取':!status.configured?'未配置':status.decryptable?'已配置 · 可解密 · 上游未验证':'已配置 · 当前不可解密'),
            ...(['client_id','client_secret'] as const).map(name=>h('div',{class:'ff14-field'},[
              h('label',{for:`${f.name}-${name}`},name==='client_id'?'Client ID':'Client Secret'),
              h(NInput,{value:s.pairs[f.name][name],type:'password',disabled,maxlength:512,inputProps:{id:`${f.name}-${name}`,autocomplete:'off'},'onUpdate:value':(value:string)=>controller.editPair(f.name,name,value)}),
            ])),
            h('div',{class:'ff14-actions'},[h(NButton,{attrType:'submit',type:'primary',disabled},()=> '保存凭据'),h(NButton,{disabled:s.pending,onClick:()=>controller.discardPair(f.name)},()=> '放弃输入'),h(NButton,{disabled:disabled||!status?.configured,onClick:()=>void controller.clear(f.name)},()=> '清除凭据')]),
          ]);}),
        ]}),
        h('div',{class:'ff14-actions'},[h(NButton,{disabled:s.pending||!s.context.available,onClick:()=>void controller.load()},()=> '重新读取状态'),s.pending?h(NButton,{onClick:controller.cancel},()=> '停止等待'):null]),
        h('p',{class:s.error?'ff14-error':'ff14-hint',role:s.error?'alert':'status','aria-live':'polite'},s.message),
      ])});
  };}}));
  app.mount(container);if(options.routeId==='config')void controller.load();
  let disposed=false;const dispose=()=>{if(disposed)return;disposed=true;controller.dispose();app.unmount();container.replaceChildren();};options.scope.onDispose(dispose);
  return {update:controller.update,dispose,navigationImpact:controller.navigationImpact};
}
