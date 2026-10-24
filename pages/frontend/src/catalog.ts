import type {Catalog, ModuleDescriptor, ModuleDisplay, PageDescriptor} from './contracts';
const record = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const str = (v: unknown, n = 4096): v is string => typeof v === 'string' && v.length > 0 && v.length <= n;
function moduleAsset(v: unknown): v is string {
  if (!str(v)) return false;
  const match = /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/(.+)$/.exec(v);
  if (!match || match[1].length > 240 || !/\.(?:js|css|svg|png|jpg|jpeg|webp|woff|woff2)$/.test(match[1])) return false;
  // Match the portable manifest resource identity, never a URL or a file read.
  return match[1].split('/').every(part => /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(part) && !part.endsWith('.') && !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part));
}
const pageAsset = (v: unknown): v is string => moduleAsset(v) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\//.test(v);
function displayMetadata(value: unknown, prefix: string, resources: {path: string}[]): ModuleDisplay | null {
  if (value === undefined || value === null) return null;
  const name = (v: unknown, limit: number): v is string => typeof v === 'string' && v.trim().length > 0 && [...v].length <= limit && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(v);
  const fail = (): never => {throw Error('invalid_catalog');};
  if (!record(value) || Object.keys(value).some(key => !['default_name','localized_names','short_name','logo_resource','summary'].includes(key)) || !name(value.default_name,128)) fail();
  const raw=value as Record<string,unknown>;
  const names = Object.hasOwn(raw,'localized_names')?raw.localized_names:{}, short = raw.short_name ?? null;
  if (!record(names) || Object.keys(names).length > 16 || short !== null && !name(short,32)) fail();
  const summary=raw.summary??null;if(summary!==null&&!name(summary,160))fail();
  const translations=names as Record<string,unknown>;
  const folded = new Set<string>();
  for (const [locale, text] of Object.entries(translations)) {
    const key = locale.toLowerCase(), parts = key.split('-');
    if (!/^[a-z]{2,8}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3})){0,4}$/i.test(locale) || folded.has(key) || new Set(parts).size !== parts.length || !name(text,128)) fail();
    folded.add(key);
  }
  const logo = raw.logo_resource === '' ? null : raw.logo_resource ?? null;
  // Display assets use the same owner-scoped declared resource authority as pages.
  if (logo !== null && (!moduleAsset(logo) || !logo.startsWith(prefix) || !/\.(?:svg|png|jpg|jpeg)$/.test(logo) || !resources.some(resource => resource.path === logo))) fail();
  return {summary:summary as string|null, logo_resource:logo as string|null, default_name:raw.default_name as string, localized_names:{...translations} as Record<string,string>, short_name:short as string|null};
}
export function moduleLabel(module: Pick<ModuleDescriptor,'module_id'|'display'>, locale: string, compact = false): string {
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
  const policies=value.legacy_config_policies??[];
  if(!Array.isArray(policies)||policies.length>64||policies.some((policy:unknown)=>!record(policy)||Object.keys(policy).sort().join()!=='policy_id,target_owner'||!str(policy.policy_id,128)||!/^[a-z][a-z0-9_.-]*$/.test(policy.policy_id)||!str(policy.target_owner)||!value.modules.some((module:Record<string,unknown>)=>module.module_id===policy.target_owner))||new Set(policies.map((policy:Record<string,unknown>)=>policy.policy_id)).size!==policies.length)fail();
  const modules: ModuleDescriptor[] = value.modules.map((m: Record<string, any>) => {
    if (!record(m) || !str(m.module_id) || !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(m.module_id) || !str(m.route) || !['loaded','disabled','unavailable'].includes(String(m.state)) || !Number.isSafeInteger(m.module_epoch) || Number(m.module_epoch) < 0 || !Array.isArray(m.pages) || m.pages.length > 64 || !Array.isArray(m.resources) || m.resources.length > 128) fail();
    const raw = m as Record<string, any>;
    const packageId=raw.module_id.split('/')[0], source=raw.source??{kind:'unknown',package_id:packageId};
    if(!record(source)||Object.keys(source).some(key=>!['kind','package_id','display_name','module_order'].includes(key))||typeof source.kind!=='string'||!['builtin','extension','unknown'].includes(source.kind)||source.package_id!==packageId)fail();
    const moduleOrder=source.module_order??[];
    if(!Array.isArray(moduleOrder)||moduleOrder.length>64||moduleOrder.some(id=>typeof id!=='string'||id.length>4096||!/^[a-z][a-z0-9_.-]*$/.test(id))||new Set(moduleOrder).size!==moduleOrder.length)fail();
    const packageName=source.display_name??null;
    if(packageName!==null&&(typeof packageName!=='string'||[...packageName].length>128||/[\p{Cc}\p{Cf}\p{Cs}]/u.test(packageName)))fail();
    const displayName=typeof packageName==='string'&&packageName.trim()?packageName:null;
    if (raw.state === 'loaded' ? !str(raw.runtime_id) || raw.enabled !== true || raw.lifecycle !== 'active' || value.runtime.state !== 'ready' : raw.runtime_id !== null || raw.pages.length || raw.resources.length) fail();
    if (!str(raw.asset_version, 64) || !/^[a-f0-9]{64}$/.test(raw.asset_version)) fail();
    const prefix = `module-assets/${raw.module_id}/`;
    const resources = raw.resources.map((r: Record<string, any>) => {if (!record(r) || !moduleAsset(r.path) || !r.path.startsWith(prefix) || !str(r.sha256,64) || !/^[a-f0-9]{64}$/.test(r.sha256)) fail(); return {path: r.path as string, sha256: r.sha256 as string};});
    if (new Set(resources.map((r: {path: string}) => r.path)).size !== resources.length) fail();
    const pages: PageDescriptor[] = raw.pages.map((p: Record<string, any>) => {
      if (!record(p) || !str(p.route_id,128) || !/^[a-z][a-z0-9_.-]*$/.test(p.route_id) || !str(p.title,256) || !(p.role===undefined||['home','config','aux'].includes(String(p.role))) || !Number.isSafeInteger(p.order) || p.access !== 'public_web' || !(p.capability_id === null || str(p.capability_id,128)) || !pageAsset(p.entry) || !p.entry.startsWith(prefix) || !p.entry.endsWith('.js') || !Array.isArray(p.styles) || p.styles.length > 16 || !p.styles.every((s: unknown) => pageAsset(s) && s.startsWith(prefix) && s.endsWith('.css'))) fail();
      if (![p.entry, ...p.styles].every(path => resources.some((r: {path: string}) => r.path === path))) fail();
      return {role:(p.role ?? 'aux') as PageDescriptor['role'], route_id: p.route_id as string, title: p.title as string, order: p.order as number, access: 'public_web', capability_id: p.capability_id as string | null, entry: p.entry as string, styles: [...p.styles] as string[]};
    });
    if (new Set(pages.map(p => p.route_id)).size !== pages.length || ['home','config'].some(role=>pages.filter(p=>p.role===role).length>1)) fail();
    return {source:{module_order:[...moduleOrder],display_name:displayName,kind:source.kind as 'builtin'|'extension'|'unknown',package_id:packageId},display:displayMetadata(raw.display, prefix, resources), module_id: raw.module_id, route: raw.route, state: raw.state, module_epoch: raw.module_epoch, runtime_id: raw.runtime_id, asset_version: raw.asset_version, pages: pages.sort((a,b) => a.order - b.order || a.route_id.localeCompare(b.route_id)), resources};
  });
  if (new Set(modules.map(m => m.module_id)).size !== modules.length) fail();
  return {legacy_config_policies:policies.map((policy:Record<string,string>)=>({policy_id:policy.policy_id,target_owner:policy.target_owner})),schema_version: 1, catalog_revision: value.catalog_revision, runtime: {state: value.runtime.state}, modules};
}
export function routeHash(owner: string, route: string): string {return `#/module/${encodeURIComponent(owner)}/${encodeURIComponent(route)}`;}
export function readRoute(hash: string): {owner: string; route: string} | null {
  const match = /^#\/module\/([^/]+)\/([^/]+)$/.exec(hash); if (!match) return null;
  try {return {owner: decodeURIComponent(match[1]), route: decodeURIComponent(match[2])};} catch {return null;}
}

// Group by validated package identity, never by translated names or game prefixes.
export function moduleGroups<T extends Pick<ModuleDescriptor,'module_id'|'display'|'source'>>(modules: T[], locale='zh-CN'): {key:string;title:string;modules:T[]}[] {
  const packages=new Map<string,T[]>();
  const byName=(a:T,b:T)=>moduleLabel(a,locale).localeCompare(moduleLabel(b,locale),locale)||a.module_id.localeCompare(b.module_id,'en');
  for(const module of modules){const id=module.module_id.split('/')[0];const list=packages.get(id)||[];list.push(module);packages.set(id,list);}
  const groups=new Map<string,{key:string;title:string;modules:T[]}>();
  for(const [id,list] of [...packages].sort(([a],[b])=>a<b?-1:a>b?1:0)){
    const kinds=new Set(list.map(module=>module.source?.kind||'unknown'));
    const kind=kinds.size===1?[...kinds][0]:'unknown',key=kind==='builtin'?'builtin':'package:'+id;
    const names=new Set(list.map(module=>module.source?.display_name||null));
    const name=names.size===1?[...names][0]:null;
    const orders=new Set(list.map(module=>JSON.stringify(module.source?.module_order||[])));
    const order:string[]=kind==='extension'&&orders.size===1?JSON.parse([...orders][0]):[];
    const rank=(module:T)=>{const index=order.indexOf(module.module_id.split('/')[1]);return index<0?Number.MAX_SAFE_INTEGER:index;};
    const group=groups.get(key)||{key,title:kind==='builtin'?'内置模块':kind==='extension'?(name||id):id+' · 来源未识别',modules:[]};
    group.modules.push(...[...list].sort((a,b)=>rank(a)-rank(b)||byName(a,b)));groups.set(key,group);
  }
  const builtin=groups.get('builtin');if(builtin)builtin.modules.sort(byName);
  return [...groups.values()].sort((a,b)=>a.key==='builtin'?-1:b.key==='builtin'?1:a.key<b.key?-1:1);
}
