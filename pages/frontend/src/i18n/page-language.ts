import {normalizeUiLanguage, type UiLanguage} from './locale';

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** Parse the ordinary settings row shared by the narrow and full reads. */
export function readPageLanguage(input: unknown): {revision: number; preference: UiLanguage} | null {
  if (!record(input) || !record(input['game_link/core'])) return null;
  const owner = input['game_link/core'];
  if (typeof owner.revision !== 'number' || !Number.isSafeInteger(owner.revision) || owner.revision < 0 ||
      !record(owner.fields) || !record(owner.fields.page_locale)) return null;
  const field = owner.fields.page_locale;
  if (typeof field.present !== 'boolean' || field.source !== (field.present ? 'sqlite' : 'default')) return null;
  if (field.state === 'invalid' && field.value === null && field.present) return {revision: owner.revision, preference: 'auto'};
  if (field.state !== 'valid' || typeof field.value !== 'string') return null;
  return {revision: owner.revision, preference: normalizeUiLanguage(field.value)};
}
