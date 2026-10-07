// Synthetic declaration/value fixtures. No storage or real Host identity.
export const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'management',session:'mock-one'};
export function catalog(){
  const field=(module_id,name,description,value_schema,defaultValue,group=null)=>({module_id,name,description,group,value_schema,default:defaultValue,required:false,readable:true,editable:true,blocked_reason:null});
  return {schema_version:1,fields:[
    {...field('example/demo','sample','第二模块声明字段',{type:'string'},''),readable:false,editable:false,blocked_reason:'not_granted'},
    field('ff14/ff14','ff14_calendar_default_days','日历默认天数',{type:'integer',minimum:1,maximum:30},7,'calendar'),
    field('ff14/ff14','ff14_calendar_default_delivery_time','日历默认投递时间',{type:'string',minLength:5,maxLength:5},'08:00','calendar'),
    field('ff14/ff14','ff14_calendar_default_timezone','日历默认时区',{type:'string',minLength:1,maxLength:128},'Asia/Shanghai','calendar'),
    field('game_link/core','default_region','默认查询区域',{type:'string',enum:['cn','global']},'cn'),
  ]};
}
export function snapshot(revision=1){
  const field=value=>({value,state:'valid',present:false,source:'default'});
  return {'game_link/core':{revision,fields:{default_region:field('cn')}},'ff14/ff14':{revision,fields:{ff14_calendar_default_days:field(7),ff14_calendar_default_timezone:field('Asia/Shanghai'),ff14_calendar_default_delivery_time:field('08:00')}}};
}
export function credentialCatalog(){return {schema_version:1,fields:['cn','global'].map(realm=>({module_id:'ff14/ff14',name:'credential_fflogs_'+realm,group:'fflogs',description:realm==='cn'?'FFLogs 国服来源凭据':'FFLogs 国际服来源凭据',value_schema:{type:'object',properties:{client_id:{type:'string',minLength:1,maxLength:512},client_secret:{type:'string',minLength:1,maxLength:512}},required:['client_id','client_secret'],additionalProperties:false}}))};}
export function credentialStatus(revision=1){return {'ff14/ff14':{revision,fields:{credential_fflogs_cn:'unset',credential_fflogs_global:'configured'}}};}
