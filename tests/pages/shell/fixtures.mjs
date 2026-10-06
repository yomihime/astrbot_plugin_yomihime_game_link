export const sha='a'.repeat(64);
export function catalog(owner='ff14/ff14',epoch=1,runtime='runtime-1') {
  const prefix=`module-assets/${owner}/pages/dist/`;
  return {schema_version:1,catalog_revision:1,runtime:{state:'ready'},modules:[{module_id:owner,route:owner.split('/')[1],category:'game',version:'1',enabled:true,lifecycle:'active',state:'loaded',reason:null,module_epoch:epoch,runtime_id:runtime,asset_version:sha,
    pages:[{route_id:'items',title:'物品查询',order:0,access:'public_web',capability_id:'item.lookup',entry:prefix+'entry.js',styles:[prefix+'styles.css']},{route_id:'market',title:'市场查询',order:1,access:'public_web',capability_id:'ff14.market.query',entry:prefix+'entry.js',styles:[prefix+'styles.css']}],resources:[{path:prefix+'entry.js',sha256:sha},{path:prefix+'styles.css',sha256:sha}],capabilities:[],config_fields:[]}]};
}
export const success={schema_version:1,status:'partial_success',privacy:'public',document:{title:'物品测试',subject:'44091',blocks:[{kind:'text',text:'部分公开内容；缺项保持可见。'},{kind:'fields',fields:{物品ID:44091}},{kind:'table',columns:['来源','价格'],rows:[['fixture',{value:123,currency:'Gil'}]]},{kind:'links',links:[{label:'来源',url:'https://example.com/item'}]}],sources:['测试来源'],timestamps:[{value:'2026-10-06T01:00:00Z',timezone:'UTC'}]},error:null,model_facts:null,provenance:['公开口径'],timestamps:[],warnings:['关联详情缺项']};
export const selection={...success,status:'needs_selection',model_facts:{selection:{kind:'item',batch_id:'batch-1',generation:'generation-1',candidates:[{item_id:44091,name:'犎牛牛排'},{item_id:8,name:'牛排'}],truncated:false}}};
export const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
export const flush=async()=>{for(let i=0;i<24;i++) await Promise.resolve();};
