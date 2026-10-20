import {isCoreThemeField, followsCoreTheme, settingValues} from './setting-choices';
export const record=v=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&[Object.prototype,null].includes(Object.getPrototypeOf(v));
export const exact=(v,keys)=>record(v)&&Object.keys(v).sort().join('\0')===[...keys].sort().join('\0');
const identifier=/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/;
export const fail=(code='invalid_config')=>{throw Error(code);};
export const key=f=>JSON.stringify([f.module_id,f.name]);
export const ownedFields=(catalog,target)=>catalog.fields.filter(f=>f.module_id===target);
const textLength=value=>[...value].length;
export function jsonBudget(value,maxDepth=32,maxNodes=65536){
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
    if(!record(f)||!exact(f,Object.hasOwn(f,'choices')?[...keys,'choices']:keys)||typeof f.module_id!=='string'||f.module_id.split('/').length!==2||!f.module_id.split('/').every(p=>identifier.test(p))||typeof f.name!=='string'||!f.name||textLength(f.name)>128||/[\s\x00-\x1f]/u.test(f.name)||typeof f.description!=='string'||!(f.group===null||typeof f.group==='string'&&identifier.test(f.group))||![f.required,f.readable,f.editable].every(v=>typeof v==='boolean'))fail();
    if(f.blocked_reason===null?!f.readable||!f.editable:f.blocked_reason==='not_granted'?f.readable||f.editable:f.blocked_reason==='semantic_validator_unavailable'?!f.readable||f.editable:true)fail();
    if(seen.has(key(f)))fail();seen.add(key(f));
    if(f.value_schema!==null){jsonBudget(f.value_schema,16,2048);schemaValid(f.value_schema);if(f.default!==null&&!valueValid(f.value_schema,f.default))fail();}
    if(Object.hasOwn(f,'choices')){
      if(!isCoreThemeField(f)||f.value_schema?.type!=='string'||!Array.isArray(f.value_schema.enum)||!Array.isArray(f.choices))fail();
      const inherited=f.module_id!=='game_link/core',values=inherited?[null,...f.value_schema.enum]:f.value_schema.enum;
      if(inherited?f.default!==null:!valueValid(f.value_schema,f.default))fail();
      if(f.choices.length!==values.length||!f.choices.every((choice,index)=>exact(choice,['value','label'])&&choice.value===values[index]&&typeof choice.label==='string'&&choice.label.trim().length>0&&textLength(choice.label)<=128&&!/[\p{Cc}\p{Cf}\p{Cs}]/u.test(choice.label)))fail();
    }
  }
  const owned=JSON.parse(JSON.stringify(input));const freeze=v=>{if(v&&typeof v==='object'){for(const child of Object.values(v))freeze(child);Object.freeze(v);}return v;};return freeze(owned);
}
export function validateSnapshot(data,catalog){
  if(!catalog)fail();jsonBudget(data);const targets=[...new Set(catalog.fields.filter(f=>f.readable).map(f=>f.module_id))];if(!exact(data,targets))fail();
  for(const target of targets){const row=data[target],declared=ownedFields(catalog,target).filter(f=>f.readable);
    if(!exact(row,['revision','fields'])||!Number.isSafeInteger(row.revision)||row.revision<1||!exact(row.fields,declared.map(f=>f.name)))fail();
    for(const f of declared){const value=row.fields[f.name];if(!exact(value,['value','state','present','source'])||!['valid','invalid'].includes(value.state)||typeof value.present!=='boolean'||!['sqlite','default'].includes(value.source)||(value.state==='invalid'?value.value!==null:!(value.value===null&&followsCoreTheme(f)&&value.present===false&&value.source==='default')&&!valueValid(f.value_schema,value.value)))fail();}
  }return data;
}
export const supported=f=>f.value_schema!==null&&['string','integer','number','boolean'].includes(f.value_schema.type);
function convert(f,raw){
  const schema=f.value_schema;let value=raw;
  if(schema.enum||schema.type==='boolean'){const choices=settingValues(f);if(!/^\d+$/.test(raw)||!Object.hasOwn(choices,Number(raw)))fail();value=choices[Number(raw)];}
  else if(['integer','number'].includes(schema.type)){if(typeof raw!=='string'||!raw.trim()||! /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(raw.trim()))fail();value=Number(raw);}
  if(!(value===null&&followsCoreTheme(f))&&!valueValid(schema,value))fail();return value;
}
export function updateBody(target,revision,controls,catalog){
  if(!catalog||!record(controls)||!Number.isSafeInteger(revision)||revision<1)fail();const declared=ownedFields(catalog,target),updates=[];
  if(!declared.length||Object.keys(controls).some(name=>!declared.some(f=>f.name===name)))fail();
  for(const [name,control] of Object.entries(controls)){const f=declared.find(item=>item.name===name);if(!record(control)||!['keep','replace','clear'].includes(control.mode))fail();if(control.mode==='keep')continue;
    if(!f.readable||!f.editable||!supported(f))fail('admin_authorization_denied');const value=control.mode==='clear'?null:convert(f,control.value);updates.push(control.mode==='clear'||value===null&&followsCoreTheme(f)?{field:name,mode:'clear'}:{field:name,mode:'replace',value});
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
export function validateMasterKeyStatus(value){
  jsonBudget(value);
  const keys=['state','reason_code','ready','source','generation','revision','inventory_digest','unknown_count','impact'];
  const extended=['storage_security','environment_present','environment_matches','recovery_action'];
  const hasExtended=Object.hasOwn(value||{},'storage_security');
  if(!exact(value,hasExtended?[...keys,...extended]:keys)||
    !['ready','needs_restore','source_conflict','initialization_failed','unknown'].includes(value.state)||
    typeof value.reason_code!=='string'||!/^[a-z_]{1,64}$/.test(value.reason_code)||
    typeof value.ready!=='boolean'||value.ready!==(value.state==='ready')||
    !['unselected','environment','managed'].includes(value.source)||
    !Number.isSafeInteger(value.generation)||value.generation<0||!Number.isSafeInteger(value.revision)||value.revision<0||
    typeof value.inventory_digest!=='string'||!(/^[0-9a-f]{64}$/.test(value.inventory_digest)||value.inventory_digest==='')||
    !Number.isSafeInteger(value.unknown_count)||value.unknown_count<0||!Array.isArray(value.impact)||value.impact.length>128)fail('operation_unavailable');
  for(const row of value.impact)if(!exact(row,['module_id','config_count','grant_count'])||typeof row.module_id!=='string'||row.module_id.length>128||!row.module_id.split('/').every(part=>identifier.test(part))||!Number.isSafeInteger(row.config_count)||row.config_count<0||!Number.isSafeInteger(row.grant_count)||row.grant_count<0)fail('operation_unavailable');
  if(hasExtended&&(!['private','legacy_review_required','blocked'].includes(value.storage_security)||typeof value.environment_present!=='boolean'||typeof value.environment_matches!=='boolean'||![null,'adopt_environment','resume_migration','retry','restore'].includes(value.recovery_action)))fail('operation_unavailable');
  return value;
}
