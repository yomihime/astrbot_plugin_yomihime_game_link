const plain=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&[Object.prototype,null].includes(Object.getPrototypeOf(value));
const reject=():never=>{throw Error('operation_unavailable');};
export function assertConfigBudget(value:unknown){
  let nodes=0;const seen=new Set<object>();
  const walk=(node:unknown,depth=0)=>{if(++nodes>4096||depth>16)reject();if(node===null||typeof node==='boolean'||typeof node==='number'&&Number.isFinite(node))return;if(typeof node==='string'){if([...node].length>4096)reject();return;}if(!node||typeof node!=='object'||!Array.isArray(node)&&!plain(node)||seen.has(node))reject();seen.add(node as object);for(const [name,child]of Object.entries(node as object)){if(name.length>128)reject();walk(child,depth+1);}seen.delete(node as object);};
  walk(value);if(new TextEncoder().encode(JSON.stringify(value)).length>262144)reject();
}
function schema(value:unknown,depth=0):void{
 if(!plain(value)||depth>8||!['string','integer','number','boolean','object','array'].includes(String(value.type)))reject();
 const row=value as Record<string,unknown>,allowed=['type','description'];
 if('description'in row&&(typeof row.description!=='string'||row.description.length>512))reject();
 if(row.type==='object'){
  allowed.push('properties','required','additionalProperties');if(row.additionalProperties!==undefined&&row.additionalProperties!==false||row.properties!==undefined&&!plain(row.properties))reject();
  const properties=row.properties as Record<string,unknown>|undefined;
  for(const [name,child]of Object.entries(properties||{})){if(!name||name.length>128)reject();schema(child,depth+1);}
  if(row.required!==undefined&&(!Array.isArray(row.required)||new Set(row.required).size!==row.required.length||row.required.some(name=>typeof name!=='string'||!Object.hasOwn(properties||{},name))))reject();
 }else if(row.type==='array'){allowed.push('items');schema(row.items,depth+1);}
 else {
  allowed.push('enum');if(row.enum!==undefined&&(!Array.isArray(row.enum)||!row.enum.length||row.enum.length>64||row.enum.some(item=>item!==null&&!['string','number','boolean'].includes(typeof item))||new Set(row.enum.map(item=>JSON.stringify(item))).size!==row.enum.length))reject();
  const bounds=row.type==='string'?['minLength','maxLength']:['integer','number'].includes(String(row.type))?['minimum','maximum']:[];allowed.push(...bounds);
  for(const key of bounds)if(key in row&&(typeof row[key]!=='number'||!Number.isFinite(row[key])||row.type!=='number'&&!Number.isSafeInteger(row[key])||row.type==='string'&&Number(row[key])<0))reject();
  if(bounds.length&&bounds.every(key=>key in row)&&Number(row[bounds[0]])>Number(row[bounds[1]]))reject();
 }
 if(Object.keys(row).some(key=>!allowed.includes(key)))reject();
}
export function assertDescriptors(descriptors:unknown,fields:Record<string,unknown>){
 if(!plain(descriptors)||Object.keys(descriptors).some(name=>!Object.hasOwn(fields,name)))reject();
 for(const row of Object.values(descriptors as Record<string,unknown>)){
  if(!plain(row)||!['default,description,value_schema','choices,default,description,value_schema'].includes(Object.keys(row).sort().join())||typeof row.description!=='string'||row.description.length>512)reject();
  const descriptor=row as Record<string,unknown>;if(descriptor.value_schema!==null)schema(descriptor.value_schema);
  if(Object.hasOwn(descriptor,'choices')){
   const choices=descriptor.choices;if(!Array.isArray(choices)||choices.length>64||choices.some(choice=>!plain(choice)||Object.keys(choice).sort().join()!=='label,value'||typeof choice.label!=='string'||!choice.label.trim()||choice.label.length>128)||new Set(choices.map(choice=>JSON.stringify(choice.value))).size!==choices.length)reject();
  }
 }
}
