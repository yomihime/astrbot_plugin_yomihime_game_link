import {h,shallowRef} from 'vue';
import {NButton} from 'naive-ui';
import {FormField,FormGrid} from '@game-link/ui';
import type {Bridge} from './contracts';
import type {InlineConfirmation} from './confirmation';

type Plan={plan_id:string;expected_revision:number;plugin_name:string;package_id:string;permission_digest:string;permissions:unknown;source_policy:unknown;restart_required:boolean};
/** Management-only proposed permissions. No page or package approves itself. */
export function createInstallationManagement(bridge:Bridge,confirmation:InlineConfirmation,current:()=>boolean,version:()=>number){
  const plugin=shallowRef(''),pkg=shallowRef(''),plan=shallowRef<Plan|null>(null),busy=shallowRef(false),message=shallowRef('');let serial=0,closed=false;
  const valid=(ticket:number,generation:number)=>!closed&&current()&&serial===ticket&&generation===version();
  function invalidate(){serial++;busy.value=false;plan.value=null;message.value='';}
  function edit(target:'plugin'|'package',value:string){invalidate();confirmation.cancel();(target==='plugin'?plugin:pkg).value=value;}
  async function inspect(){
    if(!current()||busy.value||!plugin.value.trim()||!pkg.value.trim())return;
    const ticket=++serial,generation=version(),pluginName=plugin.value.trim(),packageId=pkg.value.trim();busy.value=true;plan.value=null;message.value='';
    try{
      const value=await bridge.apiPost('admin/module-install-plan',{plugin_name:pluginName,package_id:packageId}) as Plan;
      if(!valid(ticket,generation))return;
      if(!value||!/^[0-9a-f]{64}$/.test(value.plan_id)||!Number.isSafeInteger(value.expected_revision)||value.expected_revision<0||value.plugin_name!==pluginName||value.package_id!==packageId||!/^[0-9a-f]{64}$/.test(value.permission_digest)||JSON.stringify(value).length>1048576)throw Error();
      plan.value=Object.freeze(value);message.value='请核对下方来源、凭据注入、作业和兼容迁移权限。';
    }catch{if(valid(ticket,generation))message.value='无法核实已安装扩展或管理权限；请核对名称并重新读取计划。';}
    finally{if(valid(ticket,generation))busy.value=false;}
  }
  async function approve(){
    const selected=plan.value;if(!selected||busy.value||!current())return;
    const ticket=serial,generation=version(),still=()=>valid(ticket,generation)&&plan.value===selected;
    const confirmed=await confirmation.ask({kind:'extension-install',title:'批准这份扩展安装计划？',message:`${selected.plugin_name} / ${selected.package_id}。批准仅保存当前计划的权限，之后需正常重启同一宿主。`,confirmLabel:'批准此计划',isCurrent:still});
    if(!confirmed||!still())return;busy.value=true;
    try{
      const result=await bridge.apiPost('admin/module-install-approve',{plugin_name:selected.plugin_name,package_id:selected.package_id,plan_id:selected.plan_id,expected_revision:selected.expected_revision}) as {revision:number;restart_required:boolean};
      if(!still())return;
      if(!result||!Number.isSafeInteger(result.revision)||result.revision<=selected.expected_revision||result.restart_required!==true)throw Error();
      plan.value=null;message.value='安装授权已保存。请正常重启同一宿主，再启用扩展并重新打开页面。';
    }catch{if(still()){plan.value=null;message.value='未取得保存确认。请重新读取计划核对；不会自动重试批准。';}}
    finally{if(valid(ticket,generation))busy.value=false;}
  }
  return {invalidate,dispose(){invalidate();closed=true;},render(){return h('details',{class:'extension-installation-management'},[
    h('summary','独立扩展安装授权'),
    h('p','为已在宿主安装的扩展核对来源与敏感权限。普通版本和页面更新沿用已有授权；新增权限需批准新计划。此处不填写 Key 或 Token。'),
    h(FormGrid,null,{default:()=>[
      h(FormField,{controlId:'extension-plugin-name',label:'宿主插件名'},{default:({controlAttrs}:any)=>h('input',{...controlAttrs,value:plugin.value,disabled:busy.value,maxlength:128,onInput:(e:Event)=>edit('plugin',(e.target as HTMLInputElement).value)})}),
      h(FormField,{controlId:'extension-package-id',label:'扩展包 ID'},{default:({controlAttrs}:any)=>h('input',{...controlAttrs,value:pkg.value,disabled:busy.value,maxlength:128,onInput:(e:Event)=>edit('package',(e.target as HTMLInputElement).value)})}),
    ]}),
    h(NButton,{disabled:busy.value||!current()||!plugin.value.trim()||!pkg.value.trim(),onClick:inspect},()=>busy.value?'正在核对…':'读取授权计划'),
    message.value?h('p',{role:'status'},message.value):null,
    plan.value?h('section',{'aria-label':'待批准的扩展权限'},[
      h('pre',{style:{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'28rem',overflow:'auto'}},JSON.stringify({permissions:plan.value.permissions,source_policy:plan.value.source_policy},null,2)),
      h(NButton,{disabled:busy.value||!current(),onClick:approve},()=> '批准此计划'),
    ]):null,
  ]);}};
}
