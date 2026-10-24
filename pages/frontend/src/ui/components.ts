import {defineComponent, h, type PropType, type SlotsType, type VNodeChild} from 'vue';

/** Small presentation components. Authorization and form state belong to callers. */
export const PageHeader = defineComponent({
  props: {title: {type: String, required: true}, titleId: String, eyebrow: String, subtitle: String},
  setup(props, {slots}) {return () => h('header', {class: 'ui-page-header'}, [h('div',[props.eyebrow?h('p',{class:'ui-eyebrow'},props.eyebrow):null,h('h1', {id: props.titleId}, props.title),props.subtitle?h('p',{class:'ui-subtitle'},props.subtitle):null]), slots.default?.()]);},
});
export const SettingsGroup = defineComponent({
  props: {title: {type: String, required: true}, fieldset: Boolean, number: String},
  setup(props, {slots}) {return () => h(props.fieldset?'fieldset':'section', {class: 'ui-settings-group'}, [props.fieldset?h('legend',{class:'ui-group-legend'},props.title):h('header', {class: 'ui-group-heading'}, [h('h2',[props.number?h('span',{class:'ui-section-number','aria-hidden':'true'},props.number):null,props.title]), slots.heading?.()]), slots.default?.()]);},
});
export const SettingsRow = defineComponent({
  setup(_, {slots}) {return () => h('div', {class: 'ui-settings-row'}, slots.default?.());},
});
export const ActionBar = defineComponent({
  setup(_, {slots}) {return () => h('div', {class: 'ui-action-bar'}, slots.default?.());},
});
export const StatusHint = defineComponent({
  props: {tone: {type: String as PropType<'neutral'|'success'|'warning'|'error'>, default: 'neutral'}, role: {type:String as PropType<'status'|'alert'|'none'>,default:'status'}},
  setup(props, {slots}) {return () => h('p', {class: 'ui-status-hint', 'data-tone': props.tone, role: props.role==='none'?undefined:props.role, 'aria-live': props.role==='none'?undefined:props.role==='alert'?'assertive':'polite'}, slots.default?.());},
});

export interface FormFieldSlot {controlAttrs:{id:string;'aria-labelledby':string;'aria-describedby':string|undefined;'aria-invalid':boolean}}
/** Layout only; callers retain control values, validation, requests and handlers. */
export const FormField=defineComponent({
  props:{controlId:{type:String,required:true},label:{type:String,required:true},labelId:String,hint:String,error:String,hintId:String,errorId:String,layout:{type:String as PropType<'stacked'|'row'>,default:'stacked'}},
  slots:Object as SlotsType<{default:(props:FormFieldSlot)=>VNodeChild;actions?:()=>VNodeChild}>,
  setup(props,{slots}){return()=>{
    const labelId=props.labelId||props.controlId+'-label',hintId=props.hintId||props.controlId+'-hint',errorId=props.errorId||props.controlId+'-error';
    const description=[props.hint?hintId:null,props.error?errorId:null].filter(Boolean).join(' ')||undefined;
    return h('div',{class:['ui-form-field',props.layout==='row'?'ui-settings-row':'ui-form-field-stacked']},[
      h('label',{id:labelId,for:props.controlId,onClick:(event:MouseEvent)=>(event.currentTarget as HTMLElement).ownerDocument.getElementById(props.controlId)?.focus()},props.label),
      slots.default?.({controlAttrs:{id:props.controlId,'aria-labelledby':labelId,'aria-describedby':description,'aria-invalid':Boolean(props.error)}}),
      slots.actions?.(),props.hint?h('p',{id:hintId,class:'ui-field-hint field-hint'},props.hint):null,
      h('p',{id:errorId,class:'ui-field-error field-detail',hidden:!props.error},props.error||''),
    ]);
  }},
});
export const FormGrid=defineComponent({
  props:{columns:{type:Number as PropType<1|2>,default:2}},
  setup(props,{slots}){return()=>h('div',{class:'ui-form-grid','data-columns':props.columns},slots.default?.());},
});
