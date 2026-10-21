// Synthetic admin DTOs only. Runtime choices and labels always come from Core.
import {catalog as ordinaryCatalog,snapshot as ordinarySnapshot} from '../management/fixtures.mjs';
export const themeChoices=[{value:'warm_paper',label:'暖桃纸'},{value:'cool_white',label:'冷白'},{value:'deep_indigo',label:'深靛'}];
export function themeCatalog(){
 const declarations=ordinaryCatalog();
 const theme=(module_id)=>({module_id,name:module_id==='game_link/core'?'image_theme':'reply_theme',description:'图片主题',group:'reply_style',value_schema:{type:'string',enum:themeChoices.map(choice=>choice.value)},default:module_id==='game_link/core'?'warm_paper':null,required:false,readable:true,editable:true,blocked_reason:null,choices:module_id==='game_link/core'?structuredClone(themeChoices):[{value:null,label:'跟随核心'},...structuredClone(themeChoices)]});
 declarations.fields.push(...['game_link/core','ff14/ff14','example/demo'].map(theme));return declarations;
}
export function themeSnapshot(revision=7){
 const values=ordinarySnapshot(revision),value=(value,present=false)=>({value,state:'valid',present,source:present?'sqlite':'default'});
 values['game_link/core'].fields.image_theme=value('warm_paper');values['ff14/ff14'].fields.reply_theme=value(null);
 values['example/demo']={revision,fields:{reply_theme:value('deep_indigo',true)}};return values;
}
