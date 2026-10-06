const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&[Object.prototype,null].includes(Object.getPrototypeOf(v));
const exact=(v,keys)=>record(v)&&Object.keys(v).sort().join('\0')===[...keys].sort().join('\0');
const identifier=/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/;
const fail=(code='invalid_config')=>{throw Error(code);};
const key=f=>JSON.stringify([f.module_id,f.name]);
const ownedFields=(catalog,target)=>catalog.fields.filter(f=>f.module_id===target);
const textLength=value=>[...value].length;
function jsonBudget(value,maxDepth=32,maxNodes=65536){
  const seen=new Set();let nodes=0;
  function walk(v,depth){
    if(++nodes>maxNodes||depth>maxDepth)fail();
    if(typeof v==='string'){if(textLength(v)>4096)fail();return;}
    if(v===null||typeof v==='boolean'||typeof v==='number'&&Number.isFinite(v))return;
    if((!record(v)&&!Array.isArray(v))||seen.has(v))fail();seen.add(v);
    if(Array.isArray(v)){if(Object.keys(v).length!==v.length)fail();for(const child of v)walk(child,depth+1);}
    else for(const [name,child] of Object.entries(v)){walk(name,depth+1);walk(child,depth+1);}seen.delete(v);
  }
  walk(value,0);if(new TextEncoder().encode(JSON.stringify(value)).length>262144)fail();
}
function valueValid(schema,value){
  if(schema===null)return true;if(schema.enum&&!schema.enum.includes(value))return false;
  if(schema.type==='string')return typeof value==='string'&&(schema.minLength===undefined||textLength(value)>=schema.minLength)&&(schema.maxLength===undefined||textLength(value)<=schema.maxLength);
  if(['integer','number'].includes(schema.type))return (schema.type==='integer'?Number.isSafeInteger(value):typeof value==='number'&&Number.isFinite(value))&&(schema.minimum===undefined||value>=schema.minimum)&&(schema.maximum===undefined||value<=schema.maximum);
  if(schema.type==='boolean')return typeof value==='boolean';
  if(schema.type==='array')return Array.isArray(value)&&value.every(v=>valueValid(schema.items,v));
  if(schema.type==='object')return record(value)&&(schema.required||[]).every(name=>Object.hasOwn(value,name))&&Object.entries(value).every(([name,v])=>Object.hasOwn(schema.properties||{},name)&&valueValid(schema.properties[name],v));
  return false;
}
function schemaValid(schema,depth=0){
  if(!record(schema)||depth>16||!['string','integer','number','boolean','object','array'].includes(schema.type))fail();
  const allowed=['type','description'];if('description'in schema&&(typeof schema.description!=='string'||!schema.description.trim()))fail();
  if(schema.type==='object'){
    allowed.push('properties','required','additionalProperties');
    if('properties'in schema&&!record(schema.properties)||'additionalProperties'in schema&&schema.additionalProperties!==false)fail();
    const properties=schema.properties||{};for(const [name,child] of Object.entries(properties)){if(!name.trim())fail();schemaValid(child,depth+1);}
    if('required'in schema&&(!Array.isArray(schema.required)||new Set(schema.required).size!==schema.required.length||!schema.required.every(name=>typeof name==='string'&&Object.hasOwn(properties,name))))fail();
  }else if(schema.type==='array'){allowed.push('items');schemaValid(schema.items,depth+1);}
  else{
    allowed.push('enum');const bounds=schema.type==='string'?['minLength','maxLength']:['integer','number'].includes(schema.type)?['minimum','maximum']:[];allowed.push(...bounds);
    for(const name of bounds)if(name in schema&&(!Number.isFinite(schema[name])||schema.type!=='number'&&!Number.isSafeInteger(schema[name])||schema.type==='string'&&schema[name]<0))fail();
    if(bounds.length&&bounds.every(name=>name in schema)&&schema[bounds[0]]>schema[bounds[1]])fail();
    if('enum'in schema&&(!Array.isArray(schema.enum)||!schema.enum.length||new Set(schema.enum).size!==schema.enum.length||!schema.enum.every(v=>valueValid({...schema,enum:undefined},v))))fail();
  }
  if(Object.keys(schema).some(name=>!allowed.includes(name)))fail();
}
export function validateCatalog(input){
  jsonBudget(input);if(!exact(input,['schema_version','fields'])||input.schema_version!==1||!Array.isArray(input.fields)||input.fields.length>128)fail();
  const keys=['module_id','name','description','group','value_schema','default','required','readable','editable','blocked_reason'],seen=new Set();
  for(const f of input.fields){
    if(!exact(f,keys)||typeof f.module_id!=='string'||f.module_id.split('/').length!==2||!f.module_id.split('/').every(p=>identifier.test(p))||typeof f.name!=='string'||!f.name||textLength(f.name)>128||/[\s\x00-\x1f]/u.test(f.name)||typeof f.description!=='string'||!(f.group===null||typeof f.group==='string'&&identifier.test(f.group))||![f.required,f.readable,f.editable].every(v=>typeof v==='boolean'))fail();
    if(f.blocked_reason===null?!f.readable||!f.editable:f.blocked_reason==='not_granted'?f.readable||f.editable:f.blocked_reason==='semantic_validator_unavailable'?!f.readable||f.editable:true)fail();
    if(seen.has(key(f)))fail();seen.add(key(f));
    if(f.value_schema!==null){jsonBudget(f.value_schema,16,2048);schemaValid(f.value_schema);if(f.default!==null&&!valueValid(f.value_schema,f.default))fail();}
  }
  const owned=JSON.parse(JSON.stringify(input));const freeze=v=>{if(v&&typeof v==='object'){for(const child of Object.values(v))freeze(child);Object.freeze(v);}return v;};return freeze(owned);
}
export function validateSnapshot(data,catalog){
  if(!catalog)fail();jsonBudget(data);const targets=[...new Set(catalog.fields.filter(f=>f.readable).map(f=>f.module_id))];if(!exact(data,targets))fail();
  for(const target of targets){const row=data[target],declared=ownedFields(catalog,target).filter(f=>f.readable);
    if(!exact(row,['revision','fields'])||!Number.isSafeInteger(row.revision)||row.revision<1||!exact(row.fields,declared.map(f=>f.name)))fail();
    for(const f of declared){const value=row.fields[f.name];if(!exact(value,['value','state','present','source'])||!['valid','invalid'].includes(value.state)||typeof value.present!=='boolean'||!['sqlite','default'].includes(value.source)||(value.state==='invalid'?value.value!==null:!valueValid(f.value_schema,value.value)))fail();}
  }return data;
}
const supported=f=>f.value_schema!==null&&['string','integer','number','boolean'].includes(f.value_schema.type);
function convert(f,raw){
  const schema=f.value_schema;let value=raw;
  if(schema.enum||schema.type==='boolean'){const choices=schema.enum||[false,true];if(!/^\d+$/.test(raw)||!Object.hasOwn(choices,Number(raw)))fail();value=choices[Number(raw)];}
  else if(['integer','number'].includes(schema.type)){if(typeof raw!=='string'||!raw.trim()||! /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(raw.trim()))fail();value=Number(raw);}
  if(!valueValid(schema,value))fail();return value;
}
export function updateBody(target,revision,controls,catalog){
  if(!catalog||!record(controls)||!Number.isSafeInteger(revision)||revision<1)fail();const declared=ownedFields(catalog,target),updates=[];
  if(!declared.length||Object.keys(controls).some(name=>!declared.some(f=>f.name===name)))fail();
  for(const [name,control] of Object.entries(controls)){const f=declared.find(item=>item.name===name);if(!record(control)||!['keep','replace','clear'].includes(control.mode))fail();if(control.mode==='keep')continue;
    if(!f.readable||!f.editable||!supported(f))fail('admin_authorization_denied');updates.push(control.mode==='clear'?{field:name,mode:'clear'}:{field:name,mode:'replace',value:convert(f,control.value)});
  }if(!updates.length)fail('select_changes');return {module_id:target,expected_revision:revision,updates};
}
// Matching names bind the bridge surface; the backend owns authorization.
const presentation=new Set(['theme','isDark','locale','i18n','displayName','pageTitle']);
function contextBoundary(context){
  if(!record(context)||!Object.hasOwn(context,'pluginName')||!Object.hasOwn(context,'pageName')||context.pluginName!=='astrbot_plugin_yomihime_game_link'||context.pageName!=='management')fail('invalid_context');
  const boundary=Object.fromEntries(Object.entries(context).filter(([name])=>!presentation.has(name)));jsonBudget(boundary);
  const representable=v=>{if(typeof v==='number'&&Object.is(v,-0))fail('invalid_context');if(v&&typeof v==='object')for(const child of Object.values(v))representable(child);};representable(boundary);
  const stable=v=>Array.isArray(v)?v.map(stable):record(v)?Object.fromEntries(Object.keys(v).sort().map(name=>[name,stable(v[name])])):v;return JSON.stringify(stable(boundary));
}
export function createManagementPage(document,bridge,window){
  let catalog=null,snapshot=null,ready=false,closed=false,stale=true,generation=0,boundary=null,contextSeen=false,off=null,active=null;
  const cards=new Map(),controls=new Map(),status=document.getElementById('status'),fields=document.getElementById('fields');
  const element=(tag,text)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n;};
  const current=attempt=>!closed&&ready&&generation===attempt,writable=f=>f.readable&&f.editable&&supported(f);
  const blocked=button=>button.disabled||button.getAttribute('aria-disabled')==='true';
  function setButton(button,denied){button.setAttribute('aria-disabled',String(denied));button.disabled=denied&&(closed||!ready||document.activeElement!==button);}
  function buttons(){
    setButton(document.getElementById('refresh'),closed||!ready||active!==null);
    const usable=!closed&&ready&&!stale&&snapshot!==null&&Object.keys(snapshot).length>0&&active===null;
    setButton(document.getElementById('rollback'),!usable);setButton(document.getElementById('recover'),!usable||catalog.fields.some(f=>f.readable&&!f.editable));
    document.getElementById('replacement').disabled=blocked(document.getElementById('recover'));
    for(const [target,card]of cards)setButton(card.save,!usable||!catalog||!ownedFields(catalog,target).some(writable));
    // Preserve focused editable drafts while reading; submit buttons fence writes.
    for(const control of controls.values()){const allowed=!closed&&ready&&writable(control.field);control.mode.disabled=!allowed;control.input.disabled=!allowed||control.mode.value!=='replace';}
  }
  async function post(endpoint,body,attempt){
    if(!current(attempt))fail('stale_context');const response=await bridge.apiPost(`admin/${endpoint}`,body);if(!current(attempt))fail('stale_context');
    if(endpoint==='catalog')return validateCatalog(response);if(endpoint==='read')return response;
    if(endpoint==='update'&&(!exact(response,['module_id','revision'])||response.module_id!==body.module_id||!Number.isSafeInteger(response.revision)||response.revision<=body.expected_revision))fail('operation_unavailable');
    if(endpoint==='rollback'&&(!exact(response,['rolled_back'])||response.rolled_back!==true))fail('operation_unavailable');
    if(endpoint==='recover'&&(!exact(response,['recovered'])||response.recovered!==true))fail('operation_unavailable');return response;
  }
  function inputValue(f,value){if(value===null)return '';const choices=f.value_schema?.enum||(f.value_schema?.type==='boolean'?[false,true]:null);return choices?String(choices.findIndex(v=>v===value)):String(value);}
  function makeControl(f){
    const node=element('div');node.className='config-field';node.dataset.field=f.name;node.dataset.owner=f.module_id;
    const id=`config-${encodeURIComponent(f.module_id)}-${encodeURIComponent(f.name)}`,label=element('label',f.description||f.name);label.htmlFor=id;
    const detail=element('p');detail.className='field-detail';detail.id=id+'-detail';
    const mode=element('select');mode.dataset.role='mode';mode.setAttribute('aria-label',`${f.description||f.name}修改方式`);
    for(const [value,text]of [['keep','保持'],['replace','替换 / 修复'],['clear','清除显式值']]){const option=element('option',text);option.value=value;mode.append(option);}
    const schema=f.value_schema,choices=schema?.enum||(schema?.type==='boolean'?[false,true]:null),input=element(choices?'select':'input');input.id=id;input.dataset.role='value';input.setAttribute('aria-describedby',detail.id);
    if(choices){const option=element('option','请选择有效值');option.value='';option.disabled=true;input.append(option);choices.forEach((value,index)=>{const option=element('option',String(value));option.value=String(index);input.append(option);});}
    else {input.type=['integer','number'].includes(schema?.type)?'number':'text';if(schema){if(schema.minimum!==undefined)input.min=String(schema.minimum);if(schema.maximum!==undefined)input.max=String(schema.maximum);input.step=schema.type==='integer'?'1':'any';if(schema.maxLength!==undefined)input.maxLength=schema.maxLength;}}
    mode.addEventListener('change',buttons);node.append(label,detail,mode,input);return {node,detail,mode,input,field:f,signature:JSON.stringify(f)};
  }
  function render(){
    const owners=[...new Set(catalog.fields.map(f=>f.module_id))],wanted=new Set(catalog.fields.map(key));
    for(const [k,c]of controls)if(!wanted.has(k)){c.node.remove();controls.delete(k);}for(const [target,card]of cards)if(!owners.includes(target)){card.node.remove();cards.delete(target);}
    for(const target of owners){
      let card=cards.get(target);if(!card){const node=element('section');node.className='card';node.dataset.owner=target;
        const revision=element('p'),content=element('div'),save=element('button','保存本组修改');save.type='button';save.dataset.action='save';node.append(element('h2',target),revision,content,save);fields.append(node);card={node,revision,content,save};cards.set(target,card);
        save.addEventListener('click',()=>!blocked(save)&&run(async attempt=>{
          const selected=Object.fromEntries(ownedFields(catalog,target).map(f=>{const c=controls.get(key(f));return [f.name,{mode:c.mode.value,value:c.input.value}];}));
          await post('update',updateBody(target,snapshot[target].revision,selected,catalog),attempt);await refresh(attempt);
          // A newer draft typed during the pending save survives its response.
          for(const [name,sent]of Object.entries(selected)){if(sent.mode==='keep')continue;const c=controls.get(JSON.stringify([target,name])),row=snapshot[target]?.fields[name];if(c&&row&&c.mode.value===sent.mode&&c.input.value===sent.value){c.mode.value='keep';c.input.value=inputValue(c.field,row.value);}}
          status.textContent='配置已保存；需要恢复时请单独执行恢复运行。';
        }));
      }
      card.revision.textContent=snapshot[target]?`配置版本：${snapshot[target].revision}`:'仅声明目录；未读取该模块存量值。';
      for(const f of ownedFields(catalog,target)){
        const k=key(f),signature=JSON.stringify(f);let c=controls.get(k);if(!c||c.signature!==signature){const next=makeControl(f);if(c)c.node.replaceWith(next.node);else card.content.append(next.node);controls.set(k,next);c=next;}
        const row=f.readable?snapshot[target].fields[f.name]:null,detail=[`字段：${f.name}`,f.group?`分组：${f.group}`:'未分组',f.required?'必需配置':'可选配置',`声明默认值：${JSON.stringify(f.default)}`];
        if(row)detail.push(row.state==='valid'?'有效':'无效存量，原值不显示',row.present?'已有显式值':'未设置',row.source==='sqlite'?'Core 配置':'默认值');else detail.push('无存量读取与修改授权');
        if(f.blocked_reason==='semantic_validator_unavailable')detail.push('模块语义校验器不可用，禁止修改与清除');if(!supported(f))detail.push('此 schema 暂不支持编辑，仅显示声明与已授权值');c.detail.textContent=detail.join(' · ');
        if(!supported(f))c.input.value=row?.state==='valid'?JSON.stringify(row.value):'';else if(c.mode.value==='keep')c.input.value=row?inputValue(f,row.value):'';
      }
    }buttons();
  }
  async function refresh(attempt){stale=true;buttons();const declarations=await post('catalog',{},attempt),values=validateSnapshot(await post('read',{},attempt),declarations);if(!current(attempt))fail('stale_context');catalog=declarations;snapshot=values;stale=false;render();}
  async function run(action){
    if(active||closed||!ready)return;const operation={generation};active=operation;buttons();status.textContent='管理操作正在执行。';
    try{await action(operation.generation);}catch(error){if(!current(operation.generation))return;
      if(error?.message==='revision_conflict'){stale=true;status.textContent='配置已变化，请刷新并重新核对后修改。';}
      else if(error?.message==='select_changes')status.textContent='请选择需要修改的字段。';
      else if(error?.message==='invalid_config')status.textContent='配置或输入未通过校验，请核对字段要求后刷新重试。';
      else if(error?.message==='admin_authorization_denied'){stale=true;status.textContent='管理授权不可用，请重新打开宿主管理页面后手动刷新。';}
      else {stale=true;status.textContent='未取得成功确认，请刷新核对配置与版本后重试。';}
    }finally{if(active===operation){active=null;buttons();}}
  }
  const revisions=()=>Object.fromEntries(Object.entries(snapshot).map(([target,row])=>[target,row.revision]));
  document.getElementById('refresh').addEventListener('click',()=>!blocked(document.getElementById('refresh'))&&run(async attempt=>{await refresh(attempt);status.textContent='已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。';}));
  document.getElementById('rollback').addEventListener('click',()=>!blocked(document.getElementById('rollback'))&&run(async attempt=>{await post('rollback',{expected_revisions:revisions()},attempt);await refresh(attempt);status.textContent='四字段迁移已限定回退，其他数据保留。';}));
  document.getElementById('recover').addEventListener('click',()=>!blocked(document.getElementById('recover'))&&run(async attempt=>{await post('recover',{expected_revisions:revisions(),complete_from_current:document.getElementById('replacement').checked},attempt);await refresh(attempt);status.textContent='配置已重新校验，业务运行已恢复。';}));
  function accept(context){
    if(closed)return;document.documentElement.dataset.theme=context?.isDark===true||context?.theme==='dark'?'dark':'light';document.documentElement.lang=typeof context?.locale==='string'?context.locale:'zh-CN';let next;
    try{next=contextBoundary(context);}catch{contextSeen=true;generation++;ready=false;stale=true;active=null;boundary=null;catalog=null;snapshot=null;fields.replaceChildren();cards.clear();controls.clear();buttons();status.textContent='宿主管理上下文无效，请重新打开页面。';return;}
    contextSeen=true;if(ready&&next===boundary)return;generation++;boundary=next;ready=true;stale=true;active=null;catalog=null;snapshot=null;fields.replaceChildren();cards.clear();controls.clear();buttons();
    run(async attempt=>{await refresh(attempt);status.textContent='已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。';});
  }
  function close(){if(closed)return;closed=true;generation++;ready=false;active=null;catalog=null;snapshot=null;off?.();buttons();}
  return {start(){
    if(!bridge||typeof bridge.apiPost!=='function'||typeof bridge.ready!=='function'||typeof bridge.onContext!=='function'){status.textContent='请从宿主插件管理页面打开此页。';return;}
    off=bridge.onContext(accept);Promise.resolve(bridge.ready()).then(context=>{if(!contextSeen)accept(context);}).catch(()=>{if(!contextSeen&&!closed){ready=false;status.textContent='宿主管理会话不可用。';buttons();}});window?.addEventListener('pagehide',close,{once:true});
  },close};
}
if(typeof document!=='undefined')createManagementPage(document,window.AstrBotPluginPage,window).start();
