import {createApp, defineComponent, h, withDirectives, ref} from 'vue';
import {NConfigProvider,NButton,NInput,NSelect,NCard,NTag,darkTheme,zhCN,enUS,dateZhCN,dateEnUS} from 'naive-ui';
import type {PageOptions, MountedPage} from '../../../../pages/frontend/src/contracts';
import {createQueryController,fields} from './query-controller';
import './styles.css';
import {createSelectA11y} from './select-a11y';
function resultValue(value:any): string {if (value===null) return '—'; if (value && typeof value==='object' && !Array.isArray(value) && Object.hasOwn(value,'value')) return [String(value.value),value.currency||value.unit||value.timezone].filter(Boolean).join(' '); return value && typeof value==='object'?JSON.stringify(value):String(value);}
const titles:Record<string,string>={items:'查找物品与公开详情',market:'按物品查询市场',logs:'查询角色 Logs',calendar:'查询活动日历'};
const hints:Record<string,string>={items:'名称或 ID 查询；缺项与来源说明保留在结果中。',market:'范围可留空，由服务端解析默认区域的全服范围；名称与范围别名也由服务端判定。',logs:'选择对应区域和服务器，输入角色名；来源凭据由管理员维护，不在公开页面填写。',calendar:'仅手动查询选定窗口；来源失败和覆盖限制以本次结果为准。'};
const errors:Record<string,string>={parameter_error:'参数未被来源接受；范围含糊时使用明确 World ID 或规范数据中心名。',auth_required:'来源凭据不可用，请联系管理员配置相应区域的来源凭据。',auth_expired:'授权已过期，请重新登录 Dashboard。',rate_limited:'查询受到限流，请稍后手动重试。',module_unavailable:'模块或查询期限不可用，请刷新状态后手动重试。',upstream_error:'来源暂时不可用，请稍后手动重试。',not_found:'来源未找到匹配内容，请检查名称或 ID。',no_records:'来源未返回记录；这不表示查询窗口已被完整覆盖。',not_public:'来源内容未公开。'};
function blocks(result:any) {
  const doc=result.document, nodes:any[]=doc?[h('h3',doc.title),h('p',{class:'ff14-hint'},doc.subject)]:[];
  for (const block of doc?.blocks || []) {
    if (block.kind==='text') nodes.push(h('p',{class:'ff14-result-text'},block.text));
    else if (['fields','metrics'].includes(block.kind)) nodes.push(h('dl',{class:'ff14-fields'},Object.entries(block[block.kind]).flatMap(([k,v])=>[h('dt',k),h('dd',resultValue(v))])));
    else if (block.kind==='table') {
      nodes.push(h('div',{class:'ff14-table-desktop',tabindex:0,'aria-label':'结果表格'},[h('table',[h('thead',[h('tr',block.columns.map((column:string)=>h('th',{scope:'col'},column)))]),h('tbody',block.rows.map((row:any[])=>h('tr',row.map(value=>h('td',resultValue(value))))))])]),h('div',{class:'ff14-table-mobile'},block.rows.map((row:any[])=>h('dl',{class:'ff14-fields'},row.flatMap((value,index)=>[h('dt',block.columns[index]),h('dd',resultValue(value))])))));
      if (!block.rows.length) nodes.push(h('p',{class:'ff14-warning'},'来源返回的表格内容为空。'));
    } else nodes.push(h('ul',block.links.map((link:{label:string;url:string})=>h('li',[h('a',{href:link.url,target:'_blank',rel:'noopener noreferrer'},link.label)]))));
  }
  if (doc && !doc.blocks.length) nodes.push(h('p',{class:'ff14-warning'},'来源未返回可展示内容。'));
  for (const warning of result.warnings) nodes.push(h('p',{class:'ff14-warning'},warning));
  const sources=[...(doc?.sources || []),...result.provenance]; if (sources.length) nodes.push(h('p',{class:'ff14-hint'},`来源 / 口径：${sources.join('；')}`));
  const times=[...(doc?.timestamps || []),...result.timestamps]; if (times.length) nodes.push(h('p',{class:'ff14-hint'},`返回时间：${times.map(resultValue).join('；')}`));
  return nodes;
}
export function mount(container:HTMLElement,options:PageOptions):MountedPage {
  const headStyles = new Set(Array.from(container.ownerDocument.head.querySelectorAll('style')));
  const selectA11y=createSelectA11y(container), expanded=ref<Record<string,boolean>>({});
  const revision=ref(0), controller=createQueryController(options,()=>{revision.value++;});
  const app=createApp(defineComponent({setup(){return ()=>{
    revision.value; const state=controller.state, pending=state.phase==='loading', result=state.result, context=state.context;
    const candidates=result?.model_facts?.selection;
    const submit=async(event?:Event)=>{event?.preventDefault(); await controller.submit(); const name=Object.keys(state.errors)[0]; if (name) container.querySelector<HTMLInputElement>(`#ff14-${options.routeId}-${name}`)?.focus();};
    const market=result?.model_facts?.market;
    return h(NConfigProvider,{theme:context.theme==='dark'?darkTheme:null,locale:context.locale.startsWith('en')?enUS:zhCN,dateLocale:context.locale.startsWith('en')?dateEnUS:dateZhCN,styleMountTarget:container,preflightStyleDisabled:true,inlineThemeDisabled:true}, {default:()=>h('section',{class:'ff14-page','data-theme':context.theme},[
      h(NCard,{title:titles[options.routeId],bordered:false}, {default:()=>[
        h('p',{class:'ff14-hint'},hints[options.routeId]),
        h('form',{class:'ff14-form','aria-busy':pending,onSubmit:submit},[
          ...fields[options.routeId].map(field=>h('div',{class:`ff14-field ${field.name==='query'?'ff14-query':''}`},[
            h('label',{id:`label-${options.routeId}-${field.name}`,for:`ff14-${options.routeId}-${field.name}`,onClick:()=>container.querySelector<HTMLElement>(`#ff14-${options.routeId}-${field.name}`)?.focus()},field.label),
            field.options?withDirectives(h(NSelect,{value:state.draft[field.name],options:field.options,to:container,
              menuProps:{id:`menu-${options.routeId}-${field.name}`,role:'listbox','aria-labelledby':`label-${options.routeId}-${field.name}`},
              nodeProps:(option:any)=>({id:`option-${options.routeId}-${field.name}-${option.value}`,role:'option','aria-selected':state.draft[field.name]===option.value}),
              'onUpdate:show':(show:boolean)=>{expanded.value={...expanded.value,[field.name]:show};},
              'onUpdate:value':(value:string)=>controller.edit(field.name,value)}),[[selectA11y,{id:`ff14-${options.routeId}-${field.name}`,labelId:`label-${options.routeId}-${field.name}`,menuId:`menu-${options.routeId}-${field.name}`,expanded:Boolean(expanded.value[field.name]),invalid:Boolean(state.errors[field.name]),errorId:`error-${field.name}`}]] )
              :h(NInput,{value:state.draft[field.name],placeholder:field.placeholder,maxlength:field.limit,inputProps:{id:`ff14-${options.routeId}-${field.name}`,'aria-label':field.label,'aria-invalid':Boolean(state.errors[field.name]),'aria-describedby':`error-${field.name}`},'onUpdate:value':(value:string)=>controller.edit(field.name,value)}),
            h('span',{class:'ff14-field-error',id:`error-${field.name}`},state.errors[field.name]||''),
          ])),
          h('div',{class:'ff14-actions'},[h(NButton,{type:'primary',attrType:'submit',disabled:pending||!context.available},()=>({market:'查询市场',items:'查询物品',logs:'查询 Logs',calendar:'查询日历'} as Record<string,string>)[options.routeId]),pending?h(NButton,{onClick:()=>{controller.stop();container.querySelector<HTMLInputElement>('input')?.focus();}},()=> '停止展示'):null]),
        ]),
        h('p',{class:context.available?'ff14-hint':'ff14-warning',role:'status'},context.available?'模块目录已加载；实际查询仍由后端逐请求鉴权，仅手动提交。':'当前入口状态尚未确认，请刷新状态；仍失败时请重新登录 Dashboard 或重新打开本页。'),
      ]}),
      h(NCard,{title:'本次结果',bordered:false,class:'ff14-result','aria-live':'polite','data-result-status':result?.status||state.phase}, {default:()=>[
        result?h('p',{class:result.status==='error'?'ff14-error':result.status==='success'?'ff14-success':'ff14-warning',role:result.status==='error'?'alert':'status'},result.status==='success'?'查询完成':result.status==='partial_success'?'部分结果 · 保留来源说明':result.status==='needs_selection'?'需要选择物品 · 请确认本次候选':`${result.error?.code==='not_found'?'无匹配结果':result.error?.code==='no_records'?'没有可展示记录':'查询失败'} · ${errors[result.error?.code||'']||'查询未完成，请检查输入与来源后手动重试。'}`):h('p',{role:['error','timeout'].includes(state.phase)?'alert':'status'},state.message),
        pending?h('p',{class:'ff14-hint'},'仅停止展示，本次请求可能仍在后台处理。'):null,
        ...(result?blocks(result):[]),
        market?h('div',{class:'ff14-market-context'},[h('dl',{class:'ff14-fields'},[h('dt','当前解析范围'),h('dd',`${{world:'服务器',dc:'数据中心',region:'区域全服'}[market.scope.kind as string]} / ${market.scope.target??'—'} / ${market.scope.regions.join('、')}（${market.scope.source==='default'?'服务端默认':'显式指定'}）`),h('dt','品质'),h('dd',({all:'全部品质',nq:'NQ',hq:'HQ'} as Record<string,string>)[market.quality]),h('dt','意图'),h('dd',({overview:'概览',min:'最低价',listings:'在售列表'} as Record<string,string>)[market.intent])]),...market.coverage.map((c:any)=>h('p',{class:'ff14-hint'},`来源覆盖：${c.region} / ${c.target??'—'} · ${{available:'已取得记录',empty:'来源返回空记录',failed:'获取失败'}[c.state as string]}；${c.cached===true?'缓存结果':c.cached===false?'本次获取':'缓存状态未知'}；来源获取时间：${c.fetched_at??'未知'}${c.reason?`；原因：${c.reason}`:''}。`)),market.truncated?h('p',{class:'ff14-warning'},'结果已截断；只展示本次返回的有限记录，不代表全部市场。'):null]):null,
        candidates?h('div',{class:'ff14-candidates','aria-label':'物品候选'},[h('p',{class:'ff14-hint'},'请选择本次返回的物品候选；修改输入或重新提交后旧候选失效。'),...candidates.candidates.map((c:any)=>h(NButton,{disabled:pending||!context.available,'data-item-id':c.item_id,onClick:()=>{void controller.choose(c.item_id,result);container.querySelector<HTMLInputElement>('input')?.focus();}},()=>`${c.name} · ID ${c.item_id}`)),candidates.truncated?h('p',{class:'ff14-warning'},'候选已截断，请进一步明确名称后手动查询。'):null]):null,
      ]}),
    ])});
  };}}));
  app.mount(container); let revoked=false, cleanupComplete=false;
  // vueuc mounts binder / virtual-list CSS directly in head, outside
  // Naive UI's styleMountTarget. Track only styles created by this mount.
  const moduleStyleIds = new Set(['vueuc/binder', 'vueuc/virtual-list']);
  const ownedHeadStyles = () => Array.from(container.ownerDocument.head.querySelectorAll<HTMLStyleElement>('style[cssr-id]')).filter(style => moduleStyleIds.has(style.getAttribute('cssr-id')!) && !headStyles.has(style));
  let cleanupTasks: Set<() => void> | null = null;
  const dispose=()=>{
    if (cleanupComplete) return;
    // Revoke the view immediately, but retain every unfinished cleanup action.
    revoked=true;
    if (!cleanupTasks) cleanupTasks=new Set([
      () => controller.dispose(), () => app.unmount(),
      ...ownedHeadStyles().map(style => () => style.remove()),
      () => container.replaceChildren(),
    ]);
    const errors: unknown[]=[];
    for (const task of cleanupTasks) {try {task(); cleanupTasks.delete(task);} catch (error) {errors.push(error);}}
    cleanupComplete=cleanupTasks.size===0;
    if (errors.length) throw new AggregateError(errors,'cleanup_pending');
  };
  options.scope.onDispose(dispose);
  return {update:context=>{if (!revoked) controller.update(context);},dispose};
}
