import {createManagementView} from "./management-view";
import {nextTick} from "vue";
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
export function validateCredentialCatalog(input){
  jsonBudget(input);if(!exact(input,['schema_version','fields'])||input.schema_version!==1||!Array.isArray(input.fields)||input.fields.length>32)fail();
  const seen=new Set(),schema={type:'object',properties:{client_id:{type:'string',minLength:1,maxLength:512},client_secret:{type:'string',minLength:1,maxLength:512}},required:['client_id','client_secret'],additionalProperties:false};
  for(const f of input.fields){if(!exact(f,['module_id','name','group','description','value_schema'])||typeof f.module_id!=='string'||f.module_id.split('/').length!==2||!f.module_id.split('/').every(p=>identifier.test(p))||typeof f.name!=='string'||!identifier.test(f.name)||typeof f.group!=='string'||!identifier.test(f.group)||typeof f.description!=='string'||!f.description||seen.has(key(f)))fail();seen.add(key(f));
    if(!exact(f.value_schema,['type','properties','required','additionalProperties'])||f.value_schema.type!=='object'||f.value_schema.additionalProperties!==false||!exact(f.value_schema.properties,['client_id','client_secret'])||JSON.stringify(f.value_schema.required)!==JSON.stringify(schema.required))fail();
    for(const name of schema.required){const s=f.value_schema.properties[name];if(!exact(s,['type','minLength','maxLength'])||s.type!=='string'||s.minLength!==1||s.maxLength!==512)fail();}}
  return JSON.parse(JSON.stringify(input));
}
export function validateCredentialStatus(data,catalog){
  jsonBudget(data);const targets=[...new Set(catalog.fields.map(f=>f.module_id))];if(!exact(data,targets))fail();
  for(const target of targets){const row=data[target];if(!exact(row,['revision','fields'])||!Number.isSafeInteger(row.revision)||row.revision<1||!exact(row.fields,ownedFields(catalog,target).map(f=>f.name))||Object.values(row.fields).some(value=>!['unset','configured','unusable'].includes(value)))fail();}return data;
}
export function credentialUpdateBody(target,revision,name,mode,value,catalog){
  if(!Number.isSafeInteger(revision)||revision<1||!catalog.fields.some(f=>f.module_id===target&&f.name===name)||!['replace','clear'].includes(mode))fail();
  if(mode==='replace'&&(!exact(value,['client_id','client_secret'])||Object.values(value).some(v=>typeof v!=='string'||textLength(v)<1||textLength(v)>512||/[\x00-\x1f\x7f-\x9f]/u.test(v))))fail();
  return {module_id:target,expected_revision:revision,updates:[mode==='replace'?{field:name,mode,value}:{field:name,mode}]};
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
  let catalog=null,snapshot=null,credentialCatalog=null,credentialSnapshot=null,ready=false,closed=false,stale=true,generation=0,boundary=null,contextSeen=false,off=null,active=null;
  const view=createManagementView(document),cards=new Map(),controls=new Map(),status=document.getElementById('status');
  const credentialControls=new Map();
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
    for(const c of credentialControls.values()){
      // A validated form keeps local drafts editable while refreshing;
      // writes still require a fresh revision and no active operation.
      const allowed=!closed&&ready&&credentialSnapshot!==null&&credentialCatalog!==null&&credentialCatalog.fields.some(f=>key(f)===key(c.field)&&JSON.stringify(f)===c.signature);
      if(c.save)setButton(c.save,!allowed||stale||active!==null);
      if(c.mode)c.mode.disabled=!allowed;
      for(const input of [c.clientId,c.clientSecret])if(input)input.disabled=!allowed||c.mode?.value!=='replace';
      if(c.confirm)c.confirm.disabled=!allowed||c.mode?.value!=='clear';
    }
  }
  async function post(endpoint,body,attempt){
    if(!current(attempt))fail('stale_context');const response=await bridge.apiPost(`admin/${endpoint}`,body);if(!current(attempt))fail('stale_context');
    if(endpoint==='catalog')return validateCatalog(response);if(endpoint==='read')return response;
    if(endpoint==='credential-catalog')return validateCredentialCatalog(response);
    if((endpoint==='update'||endpoint==='credential-update')&&(!exact(response,['module_id','revision'])||response.module_id!==body.module_id||!Number.isSafeInteger(response.revision)||response.revision<=body.expected_revision))fail('operation_unavailable');
    if(endpoint==='rollback'&&(!exact(response,['rolled_back'])||response.rolled_back!==true))fail('operation_unavailable');
    if(endpoint==='recover'&&(!exact(response,['recovered'])||response.recovered!==true))fail('operation_unavailable');return response;
  }
  function inputValue(f,value){if(value===null)return '';const choices=f.value_schema?.enum||(f.value_schema?.type==='boolean'?[false,true]:null);return choices?String(choices.findIndex(v=>v===value)):String(value);}
  function makeControl(f){
    return {field:f,signature:JSON.stringify(f),detail:'',draftVersion:0,mode:null,input:null};
  }
  function saveTarget(target){
    const button=cards.get(target).save;if(blocked(button))return;
    run(async attempt=>{
      const selected=Object.fromEntries(ownedFields(catalog,target).map(f=>{const c=controls.get(key(f));return [f.name,{mode:c.mode.value,value:c.input.value,draftVersion:c.draftVersion}];}));
      await post('update',updateBody(target,snapshot[target].revision,selected,catalog),attempt);await refresh(attempt);
      for(const [name,sent]of Object.entries(selected)){if(sent.mode==='keep')continue;const c=controls.get(JSON.stringify([target,name])),row=snapshot[target]?.fields[name];if(c&&row&&c.draftVersion===sent.draftVersion&&c.mode.value===sent.mode&&c.input.value===sent.value){c.mode.value='keep';c.input.value=inputValue(c.field,row.value);}}
      status.textContent='配置已保存；需要恢复时请单独执行恢复运行。';
    });
  }
  function clearSecrets(){for(const c of credentialControls.values()){for(const node of [c.clientId,c.clientSecret])if(node)node.value='';if(c.confirm)c.confirm.checked=false;if(c.mode)c.mode.value='keep';c.draftVersion++;}}
  function saveCredential(c){
    if(blocked(c.save))return;
    run(async attempt=>{
      const mode=c.mode.value;if(mode==='keep')fail('select_changes');if(mode==='clear'&&!c.confirm.checked)fail('confirm_clear');
      const version=c.draftVersion,value={client_id:c.clientId.value,client_secret:c.clientSecret.value};
      const body=credentialUpdateBody(c.field.module_id,credentialSnapshot[c.field.module_id].revision,c.field.name,mode,value,credentialCatalog);
      await post('credential-update',body,attempt);await refresh(attempt);
      if(c.draftVersion===version&&c.mode.value===mode&&c.clientId.value===value.client_id&&c.clientSecret.value===value.client_secret){c.clientId.value='';c.clientSecret.value='';c.mode.value='keep';c.confirm.checked=false;}
      buttons();status.textContent=mode==='clear'?'已清除所选来源凭据；公开查询需要重新配置该组凭据。':'来源凭据已加密保存；尚未验证 FFLogs 上游。';
    });
  }
  async function renderCredentials(){
    const wanted=new Set(credentialCatalog.fields.map(key));for(const [k,c]of credentialControls)if(!wanted.has(k)){for(const node of [c.clientId,c.clientSecret])if(node)node.value='';credentialControls.delete(k);}
    for(const f of credentialCatalog.fields){const k=key(f),signature=JSON.stringify(f);let c=credentialControls.get(k);
      if(!c||c.signature!==signature){if(c)for(const node of [c.clientId,c.clientSecret])if(node)node.value='';c={field:f,signature,draftVersion:0,mode:null,clientId:null,clientSecret:null,confirm:null,save:null,detail:'',onSave:null};c.onSave=()=>saveCredential(c);credentialControls.set(k,c);}
      const state=credentialSnapshot[f.module_id].fields[f.name];c.detail=(state==='unset'?'未配置':state==='configured'?'已保存 · 尚未验证上游':'已配置但暂不可用')+` · 配置版本：${credentialSnapshot[f.module_id].revision}`;
    }view.credentials([...credentialControls.values()],buttons);await nextTick();buttons();
  }
  async function render(){
    const owners=[...new Set(catalog.fields.map(f=>f.module_id))],wanted=new Set(catalog.fields.map(key));
    for(const k of controls.keys())if(!wanted.has(k))controls.delete(k);
    for(const target of cards.keys())if(!owners.includes(target))cards.delete(target);
    const reset=[];
    for(const target of owners){
      let card=cards.get(target);if(!card){card={target,revision:'',controls:[],save:null,onSave:()=>saveTarget(target)};cards.set(target,card);}
      card.revision=snapshot[target]?`配置版本：${snapshot[target].revision}`:'仅声明目录；未读取该模块存量值。';card.controls=[];
      for(const f of ownedFields(catalog,target)){
        const k=key(f),signature=JSON.stringify(f);let c=controls.get(k);if(!c||c.signature!==signature){c=makeControl(f);controls.set(k,c);}
        const row=f.readable?snapshot[target].fields[f.name]:null,detail=[`字段：${f.name}`,f.group?`分组：${f.group}`:'未分组',f.required?'必需配置':'可选配置',`声明默认值：${JSON.stringify(f.default)}`];
        if(row)detail.push(row.state==='valid'?'有效':'无效存量，原值不显示',row.present?'已有显式值':'未设置',row.source==='sqlite'?'Core 配置':'默认值');else detail.push('无存量读取与修改授权');
        if(f.blocked_reason==='semantic_validator_unavailable')detail.push('模块语义校验器不可用，禁止修改与清除');if(!supported(f))detail.push('此 schema 暂不支持编辑，仅显示声明与已授权值');c.detail=detail.join(' · ');
        if(!supported(f))reset.push([c,row?.state==='valid'?JSON.stringify(row.value):'',c.mode?.value,c.input?.value,c.draftVersion]);else if(!c.mode||c.mode.value==='keep')reset.push([c,row?inputValue(f,row.value):'',c.mode?.value,c.input?.value,c.draftVersion]);
        card.controls.push(c);
      }
    }
    view.fields([...cards.values()],buttons);await nextTick();
    for(const [c,value,mode,input,draftVersion]of reset)if(c.input&&c.draftVersion===draftVersion&&(mode===undefined||c.mode.value===mode&&c.input.value===input))c.input.value=value;
    buttons();
  }
  async function refresh(attempt){stale=true;buttons();const declarations=await post('catalog',{},attempt),values=validateSnapshot(await post('read',{},attempt),declarations);if(!current(attempt))fail('stale_context');catalog=declarations;snapshot=values;
    let credentialNotice='';try{const forms=await post('credential-catalog',{},attempt),states=validateCredentialStatus(await post('credential-status',{},attempt),forms);if(!current(attempt))fail('stale_context');credentialCatalog=forms;credentialSnapshot=states;}
    catch(error){if(!current(attempt))throw error;if(error?.message==='admin_authorization_denied')clearSecrets();credentialCatalog=null;credentialSnapshot=null;credentialNotice=error?.message==='admin_authorization_denied'?'来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。':'来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。';}
    stale=false;await render();if(credentialCatalog)await renderCredentials();else view.credentials([...credentialControls.values()],buttons,credentialNotice);if(!current(attempt))fail('stale_context');}
  async function run(action){
    if(active||closed||!ready)return;const operation={generation};active=operation;buttons();status.textContent='管理操作正在执行。';
    try{await action(operation.generation);}catch(error){if(!current(operation.generation))return;
      if(error?.message==='revision_conflict'){stale=true;status.textContent='配置已变化，请刷新并重新核对后修改。';}
      else if(error?.message==='select_changes')status.textContent='请选择需要修改的字段。';
      else if(error?.message==='confirm_clear')status.textContent='请勾选清除确认后再提交；保持选项不会写入。';
      else if(error?.message==='secret_encryption_unavailable')status.textContent='未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。';
      else if(error?.message==='invalid_config')status.textContent='配置或输入未通过校验，请核对字段要求后刷新重试。';
      else if(error?.message==='admin_authorization_denied'){clearSecrets();credentialCatalog=null;credentialSnapshot=null;stale=true;status.textContent='管理授权不可用，请重新打开宿主管理页面后手动刷新。';}
      else {stale=true;status.textContent='未取得成功确认，请刷新核对配置与版本后重试。';}
    }finally{if(active===operation){active=null;buttons();}}
  }
  const revisions=()=>Object.fromEntries(Object.entries(snapshot).map(([target,row])=>[target,row.revision]));
  document.getElementById('refresh').addEventListener('click',()=>!blocked(document.getElementById('refresh'))&&run(async attempt=>{await refresh(attempt);status.textContent='已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。';}));
  document.getElementById('rollback').addEventListener('click',()=>!blocked(document.getElementById('rollback'))&&run(async attempt=>{await post('rollback',{expected_revisions:revisions()},attempt);await refresh(attempt);status.textContent='四字段迁移已限定回退，其他数据保留。';}));
  document.getElementById('recover').addEventListener('click',()=>!blocked(document.getElementById('recover'))&&run(async attempt=>{await post('recover',{expected_revisions:revisions(),complete_from_current:document.getElementById('replacement').checked},attempt);await refresh(attempt);status.textContent='配置已重新校验，业务运行已恢复。';}));
  function accept(context){
    if(closed)return;view.context(context);document.documentElement.dataset.theme=context?.isDark===true||context?.theme==='dark'?'dark':'light';document.documentElement.lang=typeof context?.locale==='string'?context.locale:'zh-CN';let next;
    try{next=contextBoundary(context);}catch{clearSecrets();contextSeen=true;generation++;ready=false;stale=true;active=null;boundary=null;catalog=null;snapshot=null;credentialCatalog=null;credentialSnapshot=null;view.fields([],buttons);view.credentials([],buttons);cards.clear();controls.clear();credentialControls.clear();buttons();status.textContent='宿主管理上下文无效，请重新打开页面。';return;}
    contextSeen=true;if(ready&&next===boundary)return;clearSecrets();generation++;boundary=next;ready=true;stale=true;active=null;catalog=null;snapshot=null;credentialCatalog=null;credentialSnapshot=null;view.fields([],buttons);view.credentials([],buttons);cards.clear();controls.clear();credentialControls.clear();buttons();
    run(async attempt=>{await refresh(attempt);status.textContent='已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。';});
  }
  function close(){if(closed)return;clearSecrets();closed=true;generation++;ready=false;active=null;catalog=null;snapshot=null;credentialCatalog=null;credentialSnapshot=null;off?.();buttons();}
  return {start(){
    if(!bridge||typeof bridge.apiPost!=='function'||typeof bridge.ready!=='function'||typeof bridge.onContext!=='function'){status.textContent='请从宿主插件管理页面打开此页。';return;}
    off=bridge.onContext(accept);Promise.resolve(bridge.ready()).then(context=>{if(!contextSeen)accept(context);}).catch(()=>{if(!contextSeen&&!closed){ready=false;status.textContent='宿主管理会话不可用。';buttons();}});window?.addEventListener('pagehide',close,{once:true});
  },close};
}


if(typeof document!=='undefined' && document.getElementById('management-root'))createManagementPage(document,window.AstrBotPluginPage,window).start();
