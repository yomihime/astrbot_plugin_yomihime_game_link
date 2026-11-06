import {queryInput, validateQueryResult} from './query-contract.js';
import type {PageOptions, PageContext} from '../../../../pages/public-contract';
import {ff14Text} from './i18n';
type QueryInputError = {key: string; limit?: number};
export const fields: Record<string, {name:string; labelKey:string; placeholderKey?:string; limit?:number; options?:{labelKey:string;value:string}[]}[]> = {
  items: [{name:'query',labelKey:'query.field.item',placeholderKey:'query.field.itemExample',limit:120}],
  logs: [{name:'region',labelKey:'query.field.region',options:[{labelKey:'region.cn',value:'cn'},{labelKey:'region.global',value:'global'}]}, {name:'server',labelKey:'query.field.server',limit:100},{name:'character',labelKey:'query.field.character',limit:120}],
  calendar: [{name:'region',labelKey:'query.field.region',options:[{labelKey:'region.cn',value:'cn'},{labelKey:'region.global',value:'global'}]},{name:'days',labelKey:'query.field.days',limit:2},{name:'timezone',labelKey:'query.field.timezone',placeholderKey:'query.field.timezoneExample',limit:128}],
  market: [{name:'query',labelKey:'query.field.item',placeholderKey:'query.field.itemExample',limit:120},
    {name:'server',labelKey:'query.field.optionalServer',placeholderKey:'query.field.serverExample',limit:100},
    {name:'dc',labelKey:'query.field.optionalDc',placeholderKey:'query.field.dcExample',limit:100},
    {name:'region',labelKey:'query.field.optionalRegion',options:[{labelKey:'query.field.defaultScope',value:''},{labelKey:'region.cn',value:'cn'},{labelKey:'region.global',value:'global'}]},
    {name:'quality',labelKey:'query.field.quality',options:[{labelKey:'query.quality.all',value:'all'},{labelKey:'query.quality.nq',value:'nq'},{labelKey:'query.quality.hq',value:'hq'}]},
    {name:'intent',labelKey:'query.field.intent',options:[{labelKey:'query.intent.overview',value:'overview'},{labelKey:'query.intent.min',value:'min'},{labelKey:'query.intent.listings',value:'listings'}]}],
};
const capabilities: Record<string,string> = {items:'item.lookup',market:'ff14.market.query',logs:'ff14.logs.character',calendar:'ff14.calendar.query'};
export function capabilityParameters(route:string,body:any){if(route==='market')return {input:JSON.stringify(body)};if(route==='logs')return {realm:body.region,server:body.server,character:body.character,metric:'rdps'};return body;}
const drafts = new Map<string,Record<string,string>>();
// Calendar's reviewed Core budget is 45s; allow 5s for bridge/publication/rendering.
export function queryTimeoutMs(route:string) {return route==='calendar'?50000:35000;}
export function createQueryController(options: PageOptions, changed: () => void, timeoutMs=35000) {
  const route = options.routeId; if (!fields[route]) throw Error('invalid_query_route');
  const key = JSON.stringify([options.context.owner,route,options.context.boundary,options.context.runtimeId,options.context.epoch]);
  const draft = {...(drafts.get(key) || Object.fromEntries(fields[route].map(f=>[f.name,f.name==='quality'?'all':f.name==='intent'?'overview':f.name==='region'&&route!=='market'?'cn':f.name==='days'?'7':f.name==='timezone'?'Asia/Shanghai':''])))};
  let messageKey: string | null = 'query.status.idle';
  const state = {draft,errors:{} as Record<string, QueryInputError>,result:null as ReturnType<typeof validateQueryResult>|null, phase:'idle',get message():string{return messageKey ? ff14Text(this.context.locale, messageKey, {seconds: timeoutMs / 1000}) : '';},context:options.context};
  let disposed=false, bindingRevoked=false, sequence=0, timer:ReturnType<typeof setTimeout>|null=null;
  function clearTimer() {if (timer) clearTimeout(timer); timer=null;}
  const current = (version:number) => !disposed && !bindingRevoked && options.scope.isCurrent() && version===sequence;
  function invalidate(clearResult=true) {sequence++; clearTimer(); state.phase='idle'; if (clearResult) state.result=null;}
  function edit(name:string,value:string) {if (!(name in state.draft) || disposed || bindingRevoked) return; state.draft[name]=value; drafts.set(key,{...state.draft}); invalidate(); state.errors={}; messageKey='query.status.edited'; changed();}
  async function submit(selection?:{batch_id:string;generation:string;item_id:number}) {
    if (disposed || bindingRevoked || !options.scope.isCurrent() || !state.context.available || state.phase==='loading') return;
    const clean = selection && route==='market' ? {body:{selection},errors:{}} : queryInput(route,state.draft);
    const errors = clean.errors as Record<string, QueryInputError>; invalidate(); state.errors=errors;
    if (Object.keys(errors).length) {messageKey='query.status.invalid'; changed(); return;}
    const version=sequence; state.phase='loading'; messageKey='query.status.loading'; changed();
    timer=setTimeout(()=>{if (current(version)) {sequence++; timer=null; state.phase='timeout'; messageKey='query.status.timeout'; changed();}},timeoutMs);
    try {const response=await options.services.invoke(capabilities[route],capabilityParameters(route,clean.body)); if (!current(version)) return; const result=validateQueryResult(response); clearTimer(); state.result=result; state.phase='result'; messageKey=null; changed();}
    catch {if (!current(version)) return; clearTimer(); state.phase='error'; messageKey='query.status.requestFailed'; changed();}
  }
  function choose(item_id:number, result=state.result) {const selection = result?.model_facts?.selection; if (result!==state.result || !selection || state.phase==='loading' || !selection.candidates.some((c:{item_id:number})=>c.item_id===item_id)) return; return submit({batch_id:selection.batch_id,generation:selection.generation,item_id});}
  function dispose() {if (disposed) return; if(!bindingRevoked) drafts.set(key,{...state.draft}); disposed=true; invalidate();}
  options.scope.onDispose(dispose);
  return {state,edit,submit,choose,stop(){if (state.phase!=='loading') return; invalidate(); messageKey='query.status.stopped'; changed();},
    update(context:Readonly<PageContext>){if (disposed) return; if (context.owner!==state.context.owner || context.runtimeId!==state.context.runtimeId || context.epoch!==state.context.epoch || context.boundary!==state.context.boundary) {drafts.set(key,{...state.draft});bindingRevoked=true;invalidate();for(const name of Object.keys(state.draft))state.draft[name]='';messageKey='query.status.bindingLost';} else if (state.context.available && !context.available && state.phase==='loading') {invalidate(); messageKey='query.status.refreshStopped';} state.context=bindingRevoked?{...context,available:false}:context; changed();},dispose};
}
