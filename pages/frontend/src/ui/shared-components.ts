import {defineComponent, h, type PropType, type SlotsType, type VNodeChild} from 'vue';
import {NConfigProvider, darkTheme, zhCN, zhTW, jaJP, enUS, dateZhCN, dateZhTW, dateJaJP, dateEnUS} from 'naive-ui';
import type {PageAppearance, ResolvedPageAppearance} from '../../../public-contract';
import {resolvePageAppearance} from './page-appearance';
import {DEFAULT_UI_LOCALE, normalizeUiLocale} from '../i18n/locale';

const componentLocales = {
  'zh-CN': {locale: zhCN, dateLocale: dateZhCN},
  'zh-TW': {locale: zhTW, dateLocale: dateZhTW},
  'ja-JP': {locale: jaJP, dateLocale: dateJaJP},
  'en-US': {locale: enUS, dateLocale: dateEnUS},
};

export interface UiPresentationContext {
  readonly theme: 'light' | 'dark';
  readonly locale: string;
  readonly appearance: ResolvedPageAppearance;
}
export interface UiProviderProps {
  theme?: 'light' | 'dark';
  locale?: string;
  styleMountTarget: HTMLElement;
  appearance?: PageAppearance;
}
export type UiProviderSlots = {default?: (slot: {presentation: UiPresentationContext}) => VNodeChild};

/** Theme values are normalized by the shell; modules never read the Host bridge. */
export const UiProvider = defineComponent({
  props: {
    theme: {type: String as PropType<'light' | 'dark'>, default: 'light'},
    locale: {type: String, default: 'zh-CN'},
    styleMountTarget: {type: Object as PropType<HTMLElement>, required: true},
    appearance: {type: Object as PropType<PageAppearance>},
  },
  slots: Object as SlotsType<UiProviderSlots>,
  setup(props, {slots}) {
    return () => {
      const {theme, locale} = props;
      const appearance = resolvePageAppearance(theme, props.appearance);
      const presentation = Object.freeze({theme, locale, appearance});
      return h(NConfigProvider, {
        theme: theme === 'dark' ? darkTheme : null,
        ...componentLocales[normalizeUiLocale(locale) ?? DEFAULT_UI_LOCALE],
        styleMountTarget: props.styleMountTarget, preflightStyleDisabled: true,
        inlineThemeDisabled: true, themeOverrides: appearance.naiveOverrides,
      }, {
        default: () => h('div', {class: 'ui-page', 'data-theme': theme, style: appearance.cssVars},
          [slots.default?.({presentation})]),
      });
    };
  },
});

export const SettingsLayout=defineComponent({setup(_,{slots}){return()=>h('div',{class:'ui-settings-layout','data-has-aside':Boolean(slots.aside)},[h('div',{class:'ui-settings-main'},slots.default?.()),slots.aside?h('aside',{class:'ui-settings-aside'},slots.aside()):null]);}});
export const Surface=defineComponent({props:{title:String,eyebrow:String},setup(props,{slots}){return()=>h('section',{class:'ui-surface'},[props.eyebrow?h('p',{class:'ui-eyebrow'},props.eyebrow):null,props.title?h('h2',props.title):null,slots.default?.()]);}});

/** Decorative only. Callers supply a trusted local resource mask via CSS/style. */
export const LogoMark = defineComponent({
  // Kept as an inert prop for already compiled consumers.
  props: {effect: {type: String as PropType<'none' | 'rebound'>, default: 'none'}},
  setup() {
    return () => h('span', {class: 'ui-logo-rebound ui-logo-mark', 'aria-hidden': 'true'});
  },
});
