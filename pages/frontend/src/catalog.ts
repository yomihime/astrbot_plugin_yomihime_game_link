import type {Catalog, ModuleDescriptor, ModuleDisplay, PageDescriptor} from './contracts';
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown, n = 4096): v is string => typeof v === 'string' && v.length > 0 && v.length <= n;
const asset = (v: unknown): v is string => str(v) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\/(?:[a-zA-Z0-9_.-]+\/)*[a-zA-Z0-9_.-]+$/.test(v) && !v.split('/').some(p => p === '.' || p === '..');
function displayMetadata(value: unknown): ModuleDisplay | null {
  if (value === undefined || value === null) return null;
  const name = (v: unknown, limit: number): v is string => typeof v === 'string' && v.trim().length > 0 && [...v].length <= limit && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(v);
  const fail = (): never => {throw Error('invalid_catalog');};
  if (!record(value) || Object.keys(value).some(key => !['default_name','localized_names','short_name'].includes(key)) || !name(value.default_name,128)) fail();
  const raw=value as Record<string,unknown>;
  const names = Object.hasOwn(raw,'localized_names')?raw.localized_names:{}, short = raw.short_name ?? null;
  if (!record(names) || Object.keys(names).length > 16 || short !== null && !name(short,32)) fail();
  const translations=names as Record<string,unknown>;
  const folded = new Set<string>();
  for (const [locale, text] of Object.entries(translations)) {
    const key = locale.toLowerCase(), parts = key.split('-');
    if (!/^[a-z]{2,8}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3})){0,4}$/i.test(locale) || folded.has(key) || new Set(parts).size !== parts.length || !name(text,128)) fail();
    folded.add(key);
  }
  return {default_name:raw.default_name as string, localized_names:{...translations} as Record<string,string>, short_name:short as string|null};
}
export function moduleLabel(module: ModuleDescriptor, locale: string, compact = false): string {
  const display=module.display; if (!display) return module.module_id;
  if (compact && display.short_name) return display.short_name;
  const language=locale.toLowerCase(), names=Object.entries(display.localized_names);
  return names.find(([key])=>key.toLowerCase()===language)?.[1] ?? names.find(([key])=>key.toLowerCase()===language.split('-')[0])?.[1] ?? display.default_name ?? module.module_id;
}
export function moduleLabels(modules: ModuleDescriptor[], locale: string, compact = false): Map<string,string> {
  const names=modules.map(module=>moduleLabel(module,locale,compact));
  return new Map(modules.map((module,index)=>[module.module_id,names.filter(name=>name.normalize('NFC').toLowerCase()===names[index].normalize('NFC').toLowerCase()).length>1 ? names[index]+' · '+module.module_id : names[index]]));
}
export function validateCatalog(input: unknown): Catalog {
  const fail = (): never => {throw Error('invalid_catalog');};
  if (!record(input) || input.schema_version !== 1 || !record(input.runtime) || !['ready', 'not_ready', 'invalid_config', 'closing', 'closed'].includes(String(input.runtime.state)) || new TextEncoder().encode(JSON.stringify(input)).length > 262144) fail();
  const value = input as Record<string, any>;
  if (value.modules === null) {if (value.catalog_revision !== null || !['not_ready','invalid_config'].includes(value.runtime.state)) fail(); return {schema_version: 1, catalog_revision: null, runtime: {state: value.runtime.state}, modules: null};}
  if (!Number.isSafeInteger(value.catalog_revision) || value.catalog_revision < 0 || !Array.isArray(value.modules) || value.modules.length > 64) fail();
  const modules: ModuleDescriptor[] = value.modules.map((m: Record<string, any>) => {
    if (!record(m) || !str(m.module_id) || !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(m.module_id) || !str(m.route) || !['loaded','disabled','unavailable'].includes(String(m.state)) || !Number.isSafeInteger(m.module_epoch) || Number(m.module_epoch) < 0 || !Array.isArray(m.pages) || m.pages.length > 64 || !Array.isArray(m.resources) || m.resources.length > 128) fail();
    const raw = m as Record<string, any>;
    if (raw.state === 'loaded' ? !str(raw.runtime_id) || raw.enabled !== true || raw.lifecycle !== 'active' || value.runtime.state !== 'ready' : raw.runtime_id !== null || raw.pages.length || raw.resources.length) fail();
    if (!str(raw.asset_version, 64) || !/^[a-f0-9]{64}$/.test(raw.asset_version)) fail();
    const prefix = `module-assets/${raw.module_id}/`;
    const resources = raw.resources.map((r: Record<string, any>) => {if (!record(r) || !asset(r.path) || !r.path.startsWith(prefix) || !str(r.sha256,64) || !/^[a-f0-9]{64}$/.test(r.sha256)) fail(); return {path: r.path as string, sha256: r.sha256 as string};});
    if (new Set(resources.map((r: {path: string}) => r.path)).size !== resources.length) fail();
    const pages: PageDescriptor[] = raw.pages.map((p: Record<string, any>) => {
      if (!record(p) || !str(p.route_id,128) || !/^[a-z][a-z0-9_.-]*$/.test(p.route_id) || !str(p.title,256) || !Number.isSafeInteger(p.order) || p.access !== 'public_web' || !(p.capability_id === null || str(p.capability_id,128)) || !asset(p.entry) || !p.entry.startsWith(prefix) || !p.entry.endsWith('.js') || !Array.isArray(p.styles) || p.styles.length > 16 || !p.styles.every((s: unknown) => asset(s) && s.startsWith(prefix) && s.endsWith('.css'))) fail();
      if (![p.entry, ...p.styles].every(path => resources.some((r: {path: string}) => r.path === path))) fail();
      return {route_id: p.route_id as string, title: p.title as string, order: p.order as number, access: 'public_web', capability_id: p.capability_id as string | null, entry: p.entry as string, styles: [...p.styles] as string[]};
    });
    if (new Set(pages.map(p => p.route_id)).size !== pages.length) fail();
    return {display:displayMetadata(raw.display), module_id: raw.module_id, route: raw.route, state: raw.state, module_epoch: raw.module_epoch, runtime_id: raw.runtime_id, asset_version: raw.asset_version, pages: pages.sort((a,b) => a.order - b.order || a.route_id.localeCompare(b.route_id)), resources};
  });
  if (new Set(modules.map(m => m.module_id)).size !== modules.length) fail();
  return {schema_version: 1, catalog_revision: value.catalog_revision, runtime: {state: value.runtime.state}, modules};
}
export function routeHash(owner: string, route: string): string {return `#/module/${encodeURIComponent(owner)}/${encodeURIComponent(route)}`;}
export function readRoute(hash: string): {owner: string; route: string} | null {
  const match = /^#\/module\/([^/]+)\/([^/]+)$/.exec(hash); if (!match) return null;
  try {return {owner: decodeURIComponent(match[1]), route: decodeURIComponent(match[2])};} catch {return null;}
}
