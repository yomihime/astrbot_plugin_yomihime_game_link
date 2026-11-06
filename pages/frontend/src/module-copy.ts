import {UI_LOCALES, type UiCatalogs, type UiLocale} from './i18n/locale';

export const MODULE_COPY_MARKER = '// ygl-copy-v1 ';
const maxHeaderBytes = 16 * 1024;

export type ModuleCopy = UiCatalogs;
export const MODULE_SUMMARY_KEY = 'catalog.summary';
export const moduleRouteTitleKey = (route: string): string => 'catalog.route.' + route;

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function plain(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && [...value].length <= maxLength &&
    !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
}

/** Read the optional, owner-scoped first-line declaration as data. */
export function parseModuleCopy(bytes: Uint8Array, routes: ReadonlySet<string>): ModuleCopy | null {
  const prefix = bytes.subarray(0, maxHeaderBytes + 1);
  const newline = prefix.indexOf(10);
  const length = newline === -1 ? bytes.length : newline;
  if (length > maxHeaderBytes) return null;
  try {
    const line = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(prefix.subarray(0, length));
    if (!line.startsWith(MODULE_COPY_MARKER)) return null;
    const data: unknown = JSON.parse(line.slice(MODULE_COPY_MARKER.length));
    if (!record(data)) return null;
    const copy: Partial<Record<UiLocale, Record<string, string>>> = {};
    for (const [key, value] of Object.entries(data)) {
      const locale = UI_LOCALES.find(locale => locale === key);
      if (!locale || !record(value) || Object.keys(value).some(key => key !== 'summary' && key !== 'titles')) return null;
      const summary = value.summary;
      if (summary !== undefined && !plain(summary, 160)) return null;
      const messages: Record<string, string> = {};
      if (summary !== undefined) messages[MODULE_SUMMARY_KEY] = summary;
      if (value.titles !== undefined) {
        if (!record(value.titles)) return null;
        for (const [route, title] of Object.entries(value.titles)) {
          if (!routes.has(route) || !plain(title, 256)) return null;
          messages[moduleRouteTitleKey(route)] = title;
        }
      }
      if (!Object.keys(messages).length) return null;
      copy[locale] = messages;
    }
    return Object.keys(copy).length ? copy : null;
  } catch {
    return null;
  }
}
