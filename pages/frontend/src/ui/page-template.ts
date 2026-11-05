import {defineComponent, h, mergeProps, onBeforeUnmount, type PropType, type SlotsType, type VNodeChild} from 'vue';
import type {PageAppearance, PageContext} from '../../../public-contract';
import {ActionBar, FormField, PageHeader, SettingsGroup, StatusHint, type FormFieldSlot} from './components';
import {SettingsLayout, UiProvider, type UiPresentationContext} from './shared-components';
import {uiText} from './page-copy';

export interface TemplateChoice {
  value: string;
  label: string;
  disabled?: boolean;
}
export interface TemplateField {
  id: string;
  label: string;
  hint?: string;
  width?: 'short' | 'medium' | 'long';
  control: 'text' | 'number' | 'time' | 'select' | 'checkbox' | 'custom';
  placeholder?: string;
  min?: number;
  max?: number;
  step?: number;
  maxLength?: number;
  choices?: readonly TemplateChoice[];
}
export interface TemplateSection {
  id: string;
  title: string;
  description?: string;
  fields?: readonly TemplateField[];
}
const actionKeys = [
  'save', 'discard', 'refresh', 'query', 'cancelWait', 'resetDefault',
  'saveCredential', 'discardCredential', 'clearCredential',
] as const;
export type TemplateActionKey = typeof actionKeys[number];
export interface TemplateAction {
  id: TemplateActionKey;
  label?: string;
  type?: 'button' | 'submit';
  disabled?: boolean;
  busy?: boolean;
}
export interface TemplateContent {
  title: string;
  eyebrow?: string;
  intro?: string;
  sections?: readonly TemplateSection[];
  notes?: readonly string[];
  actions?: readonly TemplateAction[];
}
export type TemplateValues = Readonly<Record<string, string | boolean>>;
export interface TemplateNotice {
  key: 'loading' | 'saved' | 'refreshed' | 'dirty' | 'conflict' | 'pendingUnverified' | 'unavailable' | 'custom';
  text?: string;
  tone?: 'neutral' | 'success' | 'warning' | 'error';
  role?: 'status' | 'alert' | 'none';
}
export interface TemplateState {
  values?: TemplateValues;
  errors?: Readonly<Record<string, string>>;
  disabled?: boolean;
  readonly?: boolean;
  pending?: boolean;
  notice?: TemplateNotice;
}
export interface PageTemplateProps {
  content: TemplateContent;
  state?: TemplateState;
  appearance?: PageAppearance;
  context: Readonly<Pick<PageContext, 'theme' | 'locale'>>;
  styleMountTarget: HTMLElement;
  headingLevel?: 1 | 2;
  idPrefix: string;
}
type PresentationSlot = {presentation: UiPresentationContext};
export type PageTemplateSlots = {
  logo?: (slot: PresentationSlot) => VNodeChild;
  headerActions?: (slot: PresentationSlot) => VNodeChild;
  aside?: (slot: PresentationSlot) => VNodeChild;
  results?: (slot: PresentationSlot) => VNodeChild;
  footer?: (slot: PresentationSlot) => VNodeChild;
  sectionExtra?: (slot: PresentationSlot & {section: TemplateSection}) => VNodeChild;
  control?: (slot: PresentationSlot & FormFieldSlot & {
    field: TemplateField;
    value: string | boolean;
    disabled: boolean;
    readonly: boolean;
    onChange: (value: string | boolean) => void;
  }) => VNodeChild;
};
export interface PageTemplateEvents {
  fieldChange: (change: {id: string; value: string | boolean}) => void;
  action: (id: TemplateActionKey) => void;
}

const controls = ['text', 'number', 'time', 'select', 'checkbox', 'custom'];
const widths = ['short', 'medium', 'long'];
const noticeKeys = ['loading', 'saved', 'refreshed', 'dirty', 'conflict', 'pendingUnverified', 'unavailable', 'custom'];

function nonempty(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}
function optionalText(value: unknown): boolean {
  return value === undefined || typeof value === 'string';
}

function optionalBoolean(value: unknown): boolean {
  return value === undefined || typeof value === 'boolean';
}

function ownValue<T>(values: Readonly<Record<string, T>> | undefined, id: string): T | undefined {
  return values && Object.hasOwn(values, id) ? values[id] : undefined;
}

function validContent(content: TemplateContent, hasCustomSlot: boolean): boolean {
  if (!content || typeof content.title !== 'string' || !optionalText(content.eyebrow) || !optionalText(content.intro)) return false;
  if (content.notes !== undefined &&
      (!Array.isArray(content.notes) || !content.notes.every(note => typeof note === 'string'))) return false;

  const {sections = [], actions = []} = content;
  if (!Array.isArray(sections) || !Array.isArray(actions)) return false;
  const sectionIds = new Set<string>();
  const fieldIds = new Set<string>();
  for (const section of sections as readonly TemplateSection[]) {
    if (!section || !nonempty(section.id) || sectionIds.has(section.id) ||
        typeof section.title !== 'string' || !optionalText(section.description)) return false;
    sectionIds.add(section.id);
    const {fields = []} = section;
    if (!Array.isArray(fields)) return false;
    for (const field of fields as readonly TemplateField[]) {
      if (!field || !nonempty(field.id) || fieldIds.has(field.id) ||
          typeof field.label !== 'string' || !optionalText(field.hint) || !optionalText(field.placeholder) ||
          !controls.includes(field.control) || (field.control === 'custom' && !hasCustomSlot) ||
          (field.width !== undefined && !widths.includes(field.width))) return false;
      if (![field.min, field.max, field.step].every(value => value === undefined || Number.isFinite(value)) ||
          (field.maxLength !== undefined && (!Number.isSafeInteger(field.maxLength) || field.maxLength < 0))) return false;
      if (field.choices !== undefined && (!Array.isArray(field.choices) || !field.choices.every(choice =>
        choice && typeof choice.value === 'string' && typeof choice.label === 'string' &&
        optionalBoolean(choice.disabled),
      ))) return false;
      fieldIds.add(field.id);
    }
  }
  const actionIds = new Set<TemplateActionKey>();
  let submitCount = 0;
  for (const action of actions as readonly TemplateAction[]) {
    if (!action || !actionKeys.includes(action.id) || actionIds.has(action.id) ||
        !optionalText(action.label) || !optionalBoolean(action.disabled) || !optionalBoolean(action.busy) ||
        (action.type !== undefined && action.type !== 'button' && action.type !== 'submit')) return false;
    actionIds.add(action.id);
    if (action.type === 'submit') submitCount++;
  }
  return submitCount <= 1;
}

function validNotice(notice: TemplateNotice | undefined): boolean {
  return notice === undefined || Boolean(notice && noticeKeys.includes(notice.key) &&
    optionalText(notice.text) && (notice.key !== 'custom' || typeof notice.text === 'string') &&
    (notice.tone === undefined || ['neutral', 'success', 'warning', 'error'].includes(notice.tone)) &&
    (notice.role === undefined || ['status', 'alert', 'none'].includes(notice.role)));
}

function idPart(value: string): string {
  return value.split('').map(unit => unit.charCodeAt(0).toString(16).padStart(4, '0')).join('');
}

export const PageTemplate = defineComponent({
  inheritAttrs: false,
  props: {
    content: {type: Object as PropType<TemplateContent>, required: true},
    state: {type: Object as PropType<TemplateState>},
    appearance: {type: Object as PropType<PageAppearance>},
    context: {type: Object as PropType<PageTemplateProps['context']>, required: true},
    styleMountTarget: {type: Object as PropType<HTMLElement>, required: true},
    headingLevel: {type: Number as PropType<1 | 2>, default: 2},
    idPrefix: {type: String, required: true},
  },
  slots: Object as SlotsType<PageTemplateSlots>,
  emits: {
    fieldChange: (_change: Parameters<PageTemplateEvents['fieldChange']>[0]) => true,
    action: (_id: TemplateActionKey) => true,
  },
  setup(props, {attrs, slots, emit}) {
    let submitButton: HTMLButtonElement | null = null;
    let mounted = true;
    let dispatching = false;
    onBeforeUnmount(() => { mounted = false; });

    const isValid = () => nonempty(props.idPrefix) && (props.headingLevel === 1 || props.headingLevel === 2) &&
      (props.context?.theme === 'light' || props.context?.theme === 'dark') &&
      typeof props.context.locale === 'string' && validContent(props.content, Boolean(slots.control)) &&
      validNotice(props.state?.notice);

    const disabled = (action: TemplateAction) => Boolean(props.state?.disabled || action.disabled);
    const busy = (action: TemplateAction) => Boolean(action.busy || (props.state?.pending && action.id !== 'cancelWait'));
    const accepts = (action: TemplateAction) => mounted && !dispatching && isValid() &&
      props.content.actions?.includes(action) && !disabled(action) && !busy(action);

    function dispatch(action: TemplateAction) {
      if (!accepts(action)) return;
      dispatching = true;
      try {
        emit('action', action.id);
      } finally {
        dispatching = false;
      }
    }

    function submit(event: SubmitEvent) {
      event.preventDefault();
      if (event.target !== event.currentTarget || (event.submitter && event.submitter !== submitButton)) return;
      const action = props.content.actions?.find(candidate => candidate.type === 'submit');
      if (action) dispatch(action);
    }

    function renderField(field: TemplateField, section: TemplateSection, presentation: UiPresentationContext) {
      const base = 'ygl-' + [props.idPrefix, section.id, field.id].map(idPart).join('-');
      const error = ownValue(props.state?.errors, field.id);
      return h(FormField, {
        key: field.id, controlId: base + '-control', label: field.label, labelId: base + '-label',
        hint: field.hint, hintId: base + '-hint', error: typeof error === 'string' ? error : undefined,
        errorId: base + '-error', class: 'ui-template-field', 'data-width': field.width,
      }, {
        default: ({controlAttrs}: FormFieldSlot) => {
          const readonly = Boolean(props.state?.readonly);
          const disabled = Boolean(props.state?.disabled);
          const raw = ownValue(props.state?.values, field.id);
          const value = typeof raw === 'string' || typeof raw === 'boolean' ? raw : field.control === 'checkbox' ? false : '';
          const onChange = (value: string | boolean) => {
            if (mounted && !props.state?.disabled && !props.state?.readonly && isValid() &&
                props.content.sections?.some(section => section.fields?.includes(field))) {
              emit('fieldChange', {id: field.id, value});
            }
          };
          if (field.control === 'custom') {
            return slots.control?.({field, value, controlAttrs, disabled, readonly, onChange, presentation});
          }
          const attrs = {...controlAttrs, name: field.id, class: 'ui-control'};
          if (field.control === 'select') {
            return h('select', {...attrs, value, disabled: disabled || readonly,
              onChange: (event: Event) => onChange((event.target as HTMLSelectElement).value),
            }, field.choices?.map(choice => h('option', {value: choice.value, disabled: choice.disabled}, choice.label)));
          }
          if (field.control === 'checkbox') {
            return h('input', {...attrs, type: 'checkbox', checked: value === true, disabled: disabled || readonly,
              onChange: (event: Event) => onChange((event.target as HTMLInputElement).checked),
            });
          }
          return h('input', {...attrs, type: field.control, value, disabled, readonly,
            placeholder: field.placeholder, min: field.min, max: field.max, step: field.step, maxlength: field.maxLength,
            onInput: (event: Event) => onChange((event.target as HTMLInputElement).value),
          });
        },
      });
    }

    function renderAction(action: TemplateAction, locale: string) {
      const type = action.type ?? 'button';
      const waiting = busy(action);
      return h('button', {
        key: action.id, type, class: 'ui-button', 'data-action': action.id,
        'data-variant': type === 'submit' ? 'primary' : undefined,
        disabled: disabled(action), 'aria-disabled': waiting || undefined, 'aria-busy': waiting || undefined,
        ref: type === 'submit' ? (el: unknown) => { submitButton = el as HTMLButtonElement | null; } : undefined,
        onClick: (event: MouseEvent) => {
          if (type === 'submit' && accepts(action)) return;
          event.preventDefault();
          if (type === 'button') dispatch(action);
        },
      }, action.label ?? uiText(action.id, locale));
    }

    return () => {
      if (!isValid()) {
        const locale = typeof props.context?.locale === 'string' ? props.context.locale : 'zh-CN';
        return h(StatusHint, {role: 'alert', 'data-error': 'template_invalid'}, {
          default: () => uiText('unavailable', locale),
        });
      }
      return h(UiProvider, {
        theme: props.context.theme, locale: props.context.locale,
        styleMountTarget: props.styleMountTarget, appearance: props.appearance,
      }, {
        default: ({presentation}: PresentationSlot) => {
          const {title, eyebrow, intro, sections = [], actions = [], notes = []} = props.content;
          const {notice} = props.state ?? {};
          const hasSubmit = actions.some(action => action.type === 'submit');
          const slot = {presentation};
          return h('div', mergeProps(attrs, {class: 'ui-page-template'}), [
            h(PageHeader, {title, eyebrow, subtitle: intro, headingLevel: props.headingLevel}, {
              default: () => [
                slots.logo?.(slot),
                slots.headerActions?.(slot),
              ],
            }),
            h(SettingsLayout, {}, {
              default: () => h(hasSubmit ? 'form' : 'div', hasSubmit ? {novalidate: true, onSubmit: submit} : null, [
                ...sections.map(section => h(SettingsGroup, {
                  key: section.id, title: section.title, headingLevel: props.headingLevel === 1 ? 2 : 3,
                }, {default: () => [
                  section.description ? h('p', {class: 'ui-subtitle'}, section.description) : null,
                  ...(section.fields ?? []).map(field => renderField(field, section, presentation)),
                  slots.sectionExtra?.({section, presentation}),
                ]})),
                actions.length ? h(ActionBar, {}, {default: () => actions.map(action => renderAction(action, presentation.locale))}) : null,
              ]),
              ...(slots.aside ? {aside: () => slots.aside?.(slot)} : {}),
            }),
            notice ? h(StatusHint, {key: 'notice', tone: notice.tone, role: notice.role}, {
              default: () => notice.text ?? uiText(notice.key, presentation.locale),
            }) : null,
            slots.results?.(slot),
            ...notes.map(note => h('p', {class: 'ui-subtitle'}, note)),
            slots.footer?.(slot),
          ]);
        },
      });
    };
  },
});
