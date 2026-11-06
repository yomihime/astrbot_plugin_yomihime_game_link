export const UI_LOCALES = ['zh-CN', 'zh-TW', 'ja-JP', 'en-US'] as const;
export type UiLocale = (typeof UI_LOCALES)[number];
export type UiLanguage = 'auto' | UiLocale;

export const DEFAULT_UI_LOCALE: UiLocale = 'zh-CN';

export function normalizeUiLocale(value: unknown): UiLocale | null {
  if (typeof value !== 'string' || value.length > 64) return null;
  try {
    const {language, script, region} = new Intl.Locale(value.trim().replaceAll('_', '-'));
    switch (language) {
      case 'en': return 'en-US';
      case 'ja': return 'ja-JP';
      case 'zh':
        return script === 'Hant' || (!script && ['TW', 'HK', 'MO'].includes(region ?? ''))
          ? 'zh-TW' : 'zh-CN';
      default: return null;
    }
  } catch {
    return null;
  }
}

export function normalizeUiLanguage(value: unknown): UiLanguage {
  return UI_LOCALES.find(locale => locale === value) ?? 'auto';
}

export function effectiveUiLocale(preference: UiLanguage, hostLocale: unknown): UiLocale {
  return preference === 'auto' ? normalizeUiLocale(hostLocale) ?? DEFAULT_UI_LOCALE : preference;
}

type Messages = Readonly<Record<string, string>>;
export type UiCatalogs = Readonly<Partial<Record<UiLocale, Messages>>>;
export type UiParams = Readonly<Record<string, string | number>>;

function ownMessage(catalogs: UiCatalogs, locale: UiLocale, key: string): string | undefined {
  const messages = Object.hasOwn(catalogs, locale) ? catalogs[locale] : undefined;
  const message = messages && Object.hasOwn(messages, key) ? messages[key] : undefined;
  return typeof message === 'string' ? message : undefined;
}

const placeholder = /\{([a-zA-Z][a-zA-Z0-9_]*)\}/g;

function placeholderSignature(message: string): string {
  return [...message.matchAll(placeholder)].map(match => match[1]).sort().join(',');
}

export function findUiMessage(catalogs: UiCatalogs, locale: unknown, key: string): string | undefined {
  const language = normalizeUiLocale(locale) ?? DEFAULT_UI_LOCALE;
  const fallback = ownMessage(catalogs, DEFAULT_UI_LOCALE, key);
  const localized = ownMessage(catalogs, language, key);
  if (localized === undefined) return fallback;
  if (fallback === undefined || localized === fallback) return localized;
  // A translated message may reorder slots, but must keep their names and counts.
  return placeholderSignature(localized) === placeholderSignature(fallback) ? localized : fallback;
}

/** Catalogs ship with their owner. Format named placeholders as plain text. */
export function translateUi(catalogs: UiCatalogs, locale: unknown, key: string, params?: UiParams): string {
  const message = findUiMessage(catalogs, locale, key) ?? key;
  return params ? message.replace(placeholder,
    (token, name) => Object.hasOwn(params, name) ? String(params[name]) : token) : message;
}
