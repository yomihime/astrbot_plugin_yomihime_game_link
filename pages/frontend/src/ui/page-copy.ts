import {DEFAULT_UI_LOCALE, type UiParams} from '../i18n/locale';
import {coreText, type CoreTextKey} from '../i18n/messages';

export type UiTextKey =
  'save' | 'discard' | 'refresh' | 'query' | 'cancelWait' | 'resetDefault' |
  'saveCredential' | 'discardCredential' | 'clearCredential' | 'saved' | 'refreshed' |
  'loading' | 'dirty' | 'conflict' | 'pendingUnverified' | 'unavailable' | 'custom';

export function uiText(key: UiTextKey, locale: string, params?: UiParams): string {
  return coreText(locale, key, params);
}

export function uiThemeLabel(value: string | null, fallback: string, locale: string): string {
  const keys: Readonly<Record<string, CoreTextKey>> = {
    warm_paper: 'settings.warmPaper', cool_white: 'settings.coolWhite', deep_indigo: 'settings.deepIndigo',
  };
  if (value === null) return fallback === coreText(DEFAULT_UI_LOCALE, 'settings.followTheme')
    ? coreText(locale, 'settings.followTheme') : fallback;
  return Object.hasOwn(keys, value) ? coreText(locale, keys[value]) : fallback;
}
