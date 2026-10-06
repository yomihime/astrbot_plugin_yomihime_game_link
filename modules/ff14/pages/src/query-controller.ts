import {queryInput, validateQueryResult} from './query-contract.js';
import type {PageOptions, PageContext} from '../../../../pages/frontend/src/contracts';
export const fields: Record<string, {name:string; label:string; placeholder?:string; limit?:number; options?:{label:string;value:string}[]}[]> = {
  items: [{name:'query',label:'物品名称或 ID',placeholder:'例如 犎牛牛排，或 44091',limit:120}],
  market: [{name:'query',label:'物品名称或 ID',placeholder:'例如 犎牛牛排，或 44091',limit:120},
    {name:'server',label:'服务器（可选）',placeholder:'World ID 或明确服务器名',limit:100},
    {name:'dc',label:'数据中心（可选）',placeholder:'规范数据中心名',limit:100},
    {name:'region',label:'区域（可选）',options:[{label:'服务端默认范围',value:''},{label:'国服',value:'cn'},{label:'国际服',value:'global'}]},
    {name:'quality',label:'品质',options:[{label:'全部品质',value:'all'},{label:'NQ',value:'nq'},{label:'HQ',value:'hq'}]},
    {name:'intent',label:'展示',options:[{label:'概览',value:'overview'},{label:'最低价',value:'min'},{label:'在售列表',value:'listings'}]}],
};
const capabilities: Record<string,string> = {items:'item.lookup',market:'ff14.market.query'};
const endpoints: Record<string,string> = {items:'queries/items',market:'queries/market'};
export const STOP_MESSAGE = '已停止展示。仅停止展示，本次请求可能仍在后台处理；不会自动重试。';
const drafts = new Map<string,Record<string,string>>();
export function createQueryController(options: PageOptions, changed: () => void, timeoutMs=35000) {
  const route = options.routeId; if (!fields[route]) throw Error('invalid_query_route');
  const key = JSON.stringify([options.context.owner,route,options.context.boundary,options.context.runtimeId,options.context.epoch]);
  const draft = {...(drafts.get(key) || Object.fromEntries(fields[route].map(f=>[f.name,f.name==='quality'?'all':f.name==='intent'?'overview':''])))};
  const state = {draft,errors:{} as Record<string,string>,result:null as ReturnType<typeof validateQueryResult>|null, phase:'idle',message:'尚未提交查询。',context:options.context};
  let disposed=false, sequence=0, timer:ReturnType<typeof setTimeout>|null=null;
  function clearTimer() {if (timer) clearTimeout(timer); timer=null;}
  const current = (version:number) => !disposed && options.scope.isCurrent() && version===sequence;
  function invalidate(clearResult=true) {sequence++; clearTimer(); state.phase='idle'; if (clearResult) state.result=null;}
  function edit(name:string,value:string) {if (!(name in state.draft) || disposed) return; state.draft[name]=value; drafts.set(key,{...state.draft}); invalidate(); state.errors={}; state.message='输入已修改，请手动提交查询。'; changed();}
  async function submit(selection?:{batch_id:string;generation:string;item_id:number}) {
    if (disposed || !options.scope.isCurrent() || !state.context.available || state.phase==='loading') return;
    const clean = selection && route==='market' ? {body:{selection},errors:{}} : queryInput(route,state.draft);
    const errors = clean.errors as Record<string,string>; invalidate(); state.errors=errors;
    if (Object.keys(errors).length) {state.message='请先修正表单中标出的输入。'; changed(); return;}
    const version=sequence; state.phase='loading'; state.message='正在查询…'; changed();
    timer=setTimeout(()=>{if (current(version)) {sequence++; timer=null; state.phase='timeout'; state.message='等待查询超过35秒，已停止展示。后台请求可能仍在处理，请检查会话与来源后手动重试。'; changed();}},timeoutMs);
    try {const response=await options.services.invoke(capabilities[route],clean.body,endpoints[route]); if (!current(version)) return; const result=validateQueryResult(response); clearTimer(); state.result=result; state.phase='result'; state.message=''; changed();}
    catch {if (!current(version)) return; clearTimer(); state.phase='error'; state.message='查询未完成，请检查登录、网页地址和输入后手动重试。可重新登录 Dashboard，检查插件配置齿轮中的网页地址，保存重载后重新打开本页。'; changed();}
  }
  function choose(item_id:number, result=state.result) {const selection = result?.model_facts?.selection; if (result!==state.result || !selection || state.phase==='loading' || !selection.candidates.some((c:{item_id:number})=>c.item_id===item_id)) return; return submit({batch_id:selection.batch_id,generation:selection.generation,item_id});}
  function dispose() {if (disposed) return; drafts.set(key,{...state.draft}); disposed=true; invalidate();}
  options.scope.onDispose(dispose);
  return {state,edit,submit,choose,stop(){if (state.phase!=='loading') return; invalidate(); state.message=STOP_MESSAGE; changed();},
    update(context:Readonly<PageContext>){if (disposed) return; if (context.owner!==state.context.owner || context.runtimeId!==state.context.runtimeId || context.epoch!==state.context.epoch || context.boundary!==state.context.boundary) {invalidate(); state.message='入口绑定已变化，请手动重新查询。';} else if (state.context.available && !context.available && state.phase==='loading') {invalidate(); state.message='状态刷新已停止待返回结果展示。本次请求可能仍在后台处理，请确认状态后手动重新查询。';} state.context=context; changed();},dispose};
}
