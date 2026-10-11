import {defineComponent, h} from 'vue';

/** Small presentation components. Authorization and form state belong to callers. */
export const PageHeader = defineComponent({
  props: {title: {type: String, required: true}, titleId: String},
  setup(props, {slots}) {return () => h('header', {class: 'ui-page-header'}, [h('h1', {id: props.titleId}, props.title), slots.default?.()]);},
});
export const SettingsGroup = defineComponent({
  props: {title: {type: String, required: true}, fieldset: Boolean},
  setup(props, {slots}) {return () => h(props.fieldset?'fieldset':'section', {class: 'ui-settings-group'}, [props.fieldset?h('legend',{class:'ui-group-legend'},props.title):h('header', {class: 'ui-group-heading'}, [h('h2', props.title), slots.heading?.()]), slots.default?.()]);},
});
export const SettingsRow = defineComponent({
  setup(_, {slots}) {return () => h('div', {class: 'ui-settings-row'}, slots.default?.());},
});
export const ActionBar = defineComponent({
  setup(_, {slots}) {return () => h('div', {class: 'ui-action-bar'}, slots.default?.());},
});
export const StatusHint = defineComponent({
  props: {tone: {type: String, default: 'neutral'}},
  setup(props, {slots}) {return () => h('p', {class: 'ui-status-hint', 'data-tone': props.tone, role: 'status', 'aria-live': 'polite'}, slots.default?.());},
});
