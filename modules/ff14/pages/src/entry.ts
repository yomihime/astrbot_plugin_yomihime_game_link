import {createApp, defineComponent, h, withDirectives, ref} from 'vue';
import {NButton,NInput,NSelect} from 'naive-ui';
import {PageTemplate,Surface,FormField,FormGrid} from '@game-link/ui';
import type {FormFieldSlot,TemplateActionKey} from '@game-link/ui';
import type {PageOptions, MountedPage} from '../../../../pages/public-contract';
import {mountSettings} from './settings-page';
import {createQueryController,fields,queryTimeoutMs} from './query-controller';
import './styles.css';
import {createSelectA11y} from './select-a11y';
import {ff14Text} from './i18n';

type Text = (key: string, params?: Readonly<Record<string, string | number>>) => string;
function resultValue(value:any): string {if (value===null) return '—'; if (value && typeof value==='object' && !Array.isArray(value) && Object.hasOwn(value,'value')) return [String(value.value),value.currency||value.unit||value.timezone].filter(Boolean).join(' '); return value && typeof value==='object'?JSON.stringify(value):String(value);}
const knownErrors = new Set(['parameter_error', 'auth_required', 'auth_expired', 'rate_limited',
  'module_unavailable', 'upstream_error', 'not_found', 'no_records', 'not_public']);
function blocks(result:any, text: Text) {
  const doc=result.document, nodes:any[]=doc?[h('h3',doc.title),h('p',{class:'ff14-hint'},doc.subject)]:[];
  for (const block of doc?.blocks || []) {
    if (block.kind==='text') nodes.push(h('p',{class:'ff14-result-text'},block.text));
    else if (['fields','metrics'].includes(block.kind)) nodes.push(h('dl',{class:'ff14-fields'},Object.entries(block[block.kind]).flatMap(([k,v])=>[h('dt',k),h('dd',resultValue(v))])));
    else if (block.kind==='table') {
      nodes.push(h('div',{class:'ff14-table-desktop',tabindex:0,'aria-label':text('query.result.table')},[h('table',[h('thead',[h('tr',block.columns.map((column:string)=>h('th',{scope:'col'},column)))]),h('tbody',block.rows.map((row:any[])=>h('tr',row.map(value=>h('td',resultValue(value))))))])]),h('div',{class:'ff14-table-mobile'},block.rows.map((row:any[])=>h('dl',{class:'ff14-fields'},row.flatMap((value,index)=>[h('dt',block.columns[index]),h('dd',resultValue(value))])))));
      if (!block.rows.length) nodes.push(h('p',{class:'ff14-warning'},text('query.result.emptyTable')));
    } else nodes.push(h('ul',block.links.map((link:{label:string;url:string})=>h('li',[h('a',{href:link.url,target:'_blank',rel:'noopener noreferrer'},link.label)]))));
  }
  if (doc && !doc.blocks.length) nodes.push(h('p',{class:'ff14-warning'},text('query.result.emptyDocument')));
  for (const warning of result.warnings) nodes.push(h('p',{class:'ff14-warning'},warning));
  const sources=[...(doc?.sources || []),...result.provenance]; if (sources.length) nodes.push(h('p',{class:'ff14-hint'},text('query.result.source', {sources: sources.join(text('list.separator'))})));
  const times=[...(doc?.timestamps || []),...result.timestamps]; if (times.length) nodes.push(h('p',{class:'ff14-hint'},text('query.result.timestamps', {times: times.map(resultValue).join(text('list.separator'))})));
  return nodes;
}
function resultStatus(result: NonNullable<ReturnType<typeof createQueryController>['state']['result']>, text: Text): string {
  switch (result.status) {
    case 'success': return text('query.result.success');
    case 'partial_success': return text('query.result.partial');
    case 'needs_selection': return text('query.result.select');
    default: {
      const code = result.error?.code ?? '';
      const title = code === 'not_found' ? 'noMatch' : code === 'no_records' ? 'noRecords' : 'failed';
      return `${text(`query.result.${title}`)} · ${text(`query.error.${knownErrors.has(code) ? code : 'fallback'}`)}`;
    }
  }
}

type MarketCoverage = {
  region: string;
  target: string | number | null;
  state: string;
  cached: boolean | null;
  fetched_at: string | null;
  reason: string | null;
};

function marketContext(market: {
  scope: {kind: string; target: string | number | null; regions: string[]; source: string};
  quality: string;
  intent: string;
  coverage: MarketCoverage[];
  truncated: boolean;
}, text: Text) {
  const scope = market.scope;
  return h('div', {class: 'ff14-market-context'}, [
    h('dl', {class: 'ff14-fields'}, [
      h('dt', text('market.scope')), h('dd', text('market.scopeValue', {
        kind: text(`market.scopeKind.${scope.kind}`), target: scope.target ?? '—', regions: scope.regions.join('、'),
        source: text(`market.scopeSource.${scope.source}`),
      })),
      h('dt', text('query.field.quality')), h('dd', text(`query.quality.${market.quality}`)),
      h('dt', text('market.intent')), h('dd', text(`query.intent.${market.intent}`)),
    ]),
    ...market.coverage.map(coverage => h('p', {class: 'ff14-hint'}, text('market.coverage', {
      region: coverage.region, target: coverage.target ?? '—', status: text(`market.coverageState.${coverage.state}`),
      cache: text(`market.coverageCache.${coverage.cached === true ? 'cached' : coverage.cached === false ? 'fresh' : 'unknown'}`),
      time: coverage.fetched_at ?? text('market.unknownTime'),
      reason: coverage.reason ? text('market.coverageReason', {reason: coverage.reason}) : '',
    }))),
    market.truncated ? h('p', {class: 'ff14-warning'}, text('market.truncated')) : null,
  ]);
}

let nextQuery = 0;

export function mount(container:HTMLElement,options:PageOptions):MountedPage {
  if (options.routeId==='home' || options.routeId==='config') return mountSettings(container,options);
  const route = options.routeId, idPrefix = `ff14-query-${++nextQuery}`;
  const headStyles = new Set(Array.from(container.ownerDocument.head.querySelectorAll('style')));
  const selectA11y=createSelectA11y(container), expanded=ref<Record<string,boolean>>({});
  const revision=ref(0), controller=createQueryController(options,()=>{revision.value++;},queryTimeoutMs(options.routeId));
  const app=createApp(defineComponent({setup(){return ()=>{
    revision.value; const state=controller.state, pending=state.phase==='loading', result=state.result, context=state.context;
    const text: Text = (key, params) => ff14Text(context.locale, key, params);
    const candidates=result?.model_facts?.selection;
    const focusInput = () => container.querySelector('input')?.focus();
    async function onAction(action: TemplateActionKey) {
      if (action === 'cancelWait') {
        controller.stop();
        focusInput();
      } else if (action === 'query') {
        await controller.submit();
        const name = Object.keys(state.errors)[0];
        if (name) container.querySelector<HTMLElement>(`#ff14-${route}-${name}`)?.focus();
      }
    }
    const market=result?.model_facts?.market;
    return h(PageTemplate, {
      idPrefix, class: 'ff14-page', context, styleMountTarget: container, onAction,
      content: {
        title: text(`query.${route}.title`), intro: text(`query.${route}.hint`), sections: [{id:'criteria', title:text('query.criteria')}],
        actions: [
          {id:'query', type:'submit', label:text(`query.${route}.submit`), disabled:!context.available},
          ...(pending ? [{id:'cancelWait', label:text('query.cancelDisplay')} as const] : []),
        ],
      },
      state: {pending, notice: {key:'custom', tone:context.available?'neutral':'warning', text:context.available
        ? text('query.ready')
        : text('query.unavailable')}},
    }, {
      sectionExtra: () => h(FormGrid, {}, {default: () =>
          fields[route].map(field=>{const error=state.errors[field.name];return h(FormField,{controlId:`ff14-${route}-${field.name}`,label:text(field.labelKey),labelId:`label-${route}-${field.name}`,errorId:`error-${field.name}`,error:error ? text(error.key, error.limit === undefined ? undefined : {limit: error.limit}) : undefined,class:field.name==='query'?'ui-field-wide':undefined},{default:({controlAttrs}:FormFieldSlot)=>[
            field.options?withDirectives(h(NSelect,{value:state.draft[field.name],options:field.options.map(({value,labelKey})=>({value,label:text(labelKey)})),to:container,
              menuProps:{id:`menu-${route}-${field.name}`,role:'listbox','aria-labelledby':`label-${route}-${field.name}`},
              nodeProps:(option:any)=>({id:`option-${route}-${field.name}-${option.value}`,role:'option','aria-selected':state.draft[field.name]===option.value}),
              'onUpdate:show':(show:boolean)=>{expanded.value={...expanded.value,[field.name]:show};},
              'onUpdate:value':(value:string)=>controller.edit(field.name,value)}),[[selectA11y,{id:`ff14-${route}-${field.name}`,labelId:`label-${route}-${field.name}`,menuId:`menu-${route}-${field.name}`,expanded:Boolean(expanded.value[field.name]),invalid:Boolean(state.errors[field.name]),errorId:`error-${field.name}`}]] )
              :h(NInput,{value:state.draft[field.name],placeholder:field.placeholderKey ? text(field.placeholderKey) : undefined,maxlength:field.limit,inputProps:controlAttrs,'onUpdate:value':(value:string)=>controller.edit(field.name,value)}),
          ]});})
      }),
      results: () => h(Surface,{title:text('query.result.title'),class:'ff14-result','aria-live':'polite','data-result-status':result?.status||state.phase}, {default:()=>[
        result?h('p',{class:result.status==='error'?'ff14-error':result.status==='success'?'ff14-success':'ff14-warning',role:result.status==='error'?'alert':'status'},resultStatus(result, text)):h('p',{role:['error','timeout'].includes(state.phase)?'alert':'status'},state.message),
        pending?h('p',{class:'ff14-hint'},text('query.result.stopNote')):null,
        ...(result?blocks(result, text):[]),
        market?marketContext(market, text):null,
        candidates?h('div',{class:'ff14-candidates','aria-label':text('query.result.candidates')},[h('p',{class:'ff14-hint'},text('query.result.pickCandidate')),...candidates.candidates.map((c:any)=>h(NButton,{disabled:pending||!context.available,'data-item-id':c.item_id,onClick:()=>{void controller.choose(c.item_id,result);container.querySelector<HTMLInputElement>('input')?.focus();}},()=>`${c.name} · ID ${c.item_id}`)),candidates.truncated?h('p',{class:'ff14-warning'},text('query.result.truncatedCandidates')):null]):null,
      ]}),
    });
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
