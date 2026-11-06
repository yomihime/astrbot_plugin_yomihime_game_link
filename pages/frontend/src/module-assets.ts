import {sha256Hex} from './sha256';
import {createScope} from './lifecycle';
import {MODULE_COPY_MARKER, parseModuleCopy, type ModuleCopy} from './module-copy';
import {isPageAppearance} from './ui/page-appearance';
import type {Bridge, ModuleDescriptor, PageDescriptor, PageModule} from './contracts';
import type {ModulePresentationExports, ResourceScope} from '../../public-contract';

// This identity belongs to the HTML projection, not to a transient page view.
export function assetIdentity(module: ModuleDescriptor, boundary: string): string {
  return JSON.stringify([module.module_id, module.runtime_id, module.module_epoch, module.asset_version, boundary,
    module.resources, module.pages.map(page => [page.route_id, page.entry, page.styles, page.capability_id])]);
}

export function presentationPaths(module: ModuleDescriptor) {
  if (module.state !== 'loaded' || !module.runtime_id ||
      !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(module.module_id)) return null;
  const ownerPrefix = `module-assets/${module.module_id}/`;
  const livePrefix = `${ownerPrefix}pages/p${module.asset_version}/`;
  const page = /^[0-9a-f]{64}$/.test(module.asset_version)
    ? module.pages.find(item => item.entry.startsWith(livePrefix))
    : undefined;
  if (!page && module.source?.kind !== 'builtin') return null;
  const prefix = page ? livePrefix : ownerPrefix;
  const base = `pages/dist/presentation-${module.module_id.split('/')[1]}`;
  const entry = `${prefix}${base}.js`;
  const style = `${prefix}${base}.css`;
  const declared = (path: string) => module.resources.some(item =>
    item.path === path && /^[0-9a-f]{64}$/.test(item.sha256));
  if (!declared(entry) || module.pages.some(page => page.entry === entry || page.styles.includes(style))) {
    return null;
  }
  return {entry, style: declared(style) ? style : null, prefix, page};
}

function presentationExports(value: unknown): ModulePresentationExports {
  if (!value || typeof value !== 'object' ||
      Object.keys(value).some(key => key !== 'presentation' && key !== 'mountLogo')) {
    throw Error('invalid_presentation');
  }
  const {presentation, mountLogo} = value as ModulePresentationExports;
  if ((!presentation && !mountLogo) ||
      (Object.hasOwn(value, 'mountLogo') && typeof mountLogo !== 'function') ||
      (Object.hasOwn(value, 'presentation') &&
        (!presentation || presentation.schemaVersion !== 1 ||
          Object.keys(presentation).some(key => key !== 'schemaVersion' && key !== 'appearance') ||
          (Object.hasOwn(presentation, 'appearance') && !isPageAppearance(presentation.appearance))))) {
    throw Error('invalid_presentation');
  }
  return {presentation, mountLogo};
}

export function createModuleAssets(
  document: Document,
  loadPage: (entry: string) => Promise<PageModule>,
  timeoutMs: number,
  dynamic?: {bridge: Bridge; runtimeUrl: string},
  loadPresentation?: (entry: string) => Promise<unknown>,
) {
  type Payload = Map<string,{bytes:Uint8Array;mime:string}>;
  type Projection = {entry:string;styles:string[];payload:Payload};
  type Record = {identity:string;revoked: boolean; scope: ReturnType<typeof createScope>; styles: Map<string, Promise<void>>; entries: Map<string, Promise<PageModule>>; projections:Map<string,Promise<Projection>>; images:Map<string,Promise<string>>; presentation?: Promise<ModulePresentationExports>};
  const owners = new Map<string, Record>();
  async function staticCopyBytes(entry: string, digest: string, value: Record, signal: AbortSignal): Promise<Uint8Array | null> {
    const window = document.defaultView;
    if (!window?.fetch || value.revoked || signal.aborted) return null;
    const url = new URL('./' + entry, document.location.href);
    if (url.origin !== document.location.origin || !['http:', 'https:'].includes(url.protocol)) return null;
    const abort = new AbortController();
    const cancel = () => abort.abort();
    value.scope.signal.addEventListener('abort', cancel, {once: true});
    signal.addEventListener('abort', cancel, {once: true});
    const timer = setTimeout(cancel, timeoutMs);
    const maxBytes = 4 * 1024 * 1024; // Existing per-resource bound.
    try {
      const response = await window.fetch(url.href, {credentials: 'same-origin', mode: 'same-origin', redirect: 'error', signal: abort.signal});
      if (!response.ok || !response.body || Number(response.headers.get('content-length')) > maxBytes) return null;
      const chunks: Uint8Array[] = [];
      let length = 0;
      const reader = response.body.getReader();
      while (!abort.signal.aborted) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.length;
        if (length > maxBytes) return null;
        chunks.push(part.value);
      }
      if (abort.signal.aborted || value.revoked) return null;
      const bytes = new Uint8Array(length);
      let offset = 0;
      for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
      return await sha256Hex(bytes) === digest && !abort.signal.aborted && !value.revoked ? bytes : null;
    } finally {
      cancel();
      clearTimeout(timer);
      value.scope.signal.removeEventListener('abort', cancel);
      signal.removeEventListener('abort', cancel);
    }
  }
  async function liveResources(module:ModuleDescriptor,page:PageDescriptor,value:Record):Promise<Projection>{
    if(!dynamic||!module.runtime_id)throw Error('missing_live_projection');
    const response=await dynamic.bridge.apiPost('admin/module-page-assets',{owner:module.module_id,page:page.route_id,runtime_id:module.runtime_id,module_epoch:module.module_epoch}) as {entry:string;styles:string[];asset_version:string;resources:{path:string;sha256:string;media_type:string;base64:string}[]};
    if(value.revoked)throw Error('asset_revoked');
    if(!response||!Array.isArray(response.resources)||response.resources.length>128||!Array.isArray(response.styles))throw Error('invalid_live_projection');
    if(response.asset_version!==module.asset_version)throw Error('asset_revoked');
    const prefix=`module-assets/${module.module_id}/pages/p${module.asset_version}/`;
    if(page.entry!==prefix+response.entry||JSON.stringify(page.styles)!==JSON.stringify(response.styles.map(path=>prefix+path)))throw Error('invalid_live_projection');
    const expected=new Map(module.resources.map(item=>[item.path,item.sha256])),payload:Payload=new Map();
    if(expected.size!==module.resources.length)throw Error('invalid_live_projection');
    const media:{[key:string]:string}={js:'text/javascript',css:'text/css',svg:'image/svg+xml',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'};
    let total=0;
    for(const item of response.resources){
      if(typeof item.path!=='string'||typeof item.base64!=='string'||item.base64.length>5592416||expected.get(prefix+item.path)!==item.sha256||payload.has(item.path)||typeof item.media_type!=='string'||!media[item.path.split('.').pop()||'']||media[item.path.split('.').pop()||'']!==item.media_type)throw Error('invalid_live_resource');
      let bytes:Uint8Array;
      try{bytes=Uint8Array.from(atob(item.base64),c=>c.charCodeAt(0));}catch{throw Error('invalid_live_resource');}
      total+=bytes.length;
      if(total>16777216)throw Error('live_resource_budget');
      const digest=await sha256Hex(bytes);
      if(digest!==item.sha256)throw Error('asset_digest_mismatch');
      if(value.revoked)throw Error('asset_revoked');
      payload.set(item.path,{bytes,mime:item.media_type});
    }
    if(payload.size!==expected.size)throw Error('incomplete_live_projection');
    const entry=payload.get(response.entry);
    if(!entry||entry.mime!=='text/javascript'||response.styles.some(path=>payload.get(path)?.mime!=='text/css'))throw Error('invalid_live_projection');
    return {entry:response.entry,styles:response.styles,payload};
  }
  function projection(module:ModuleDescriptor,page:PageDescriptor,value:Record):Promise<Projection>{
    let cached=value.projections.get(page.route_id);
    if(!cached){cached=liveResources(module,page,value);value.projections.set(page.route_id,cached);}return cached;
  }
  function liveLogo(payload: Payload, path: string) {
    const item = payload.get(path);
    if (!item || !['image/svg+xml', 'image/png', 'image/jpeg'].includes(item.mime)) {
      throw Error('invalid_live_image');
    }
    return new Blob([item.bytes as BlobPart], {type: item.mime});
  }
  async function liveStyle(owner: string, payload: Payload, path: string, value: Record) {
    if (value.revoked) throw Error('asset_revoked');
    const item = payload.get(path);
    if (!item || item.mime !== 'text/css') throw Error('invalid_live_style');
    const css = new TextDecoder('utf-8', {fatal: true}).decode(item.bytes);
    if (/@import\b|url\s*\(/i.test(css)) throw Error('unsupported_live_style_reference');
    const blob = URL.createObjectURL(new Blob([css], {type: 'text/css'}));
    await new Promise<void>((resolve, reject) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = blob;
      link.dataset.moduleStyle = owner;
      let done = false;
      const finish = (error?: Error) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        link.onload = null;
        link.onerror = null;
        error ? reject(error) : resolve();
      };
      const timer = setTimeout(() => finish(Error('style_load_timeout')), timeoutMs);
      link.onload = () => finish();
      link.onerror = () => finish(Error('style_load_failed'));
      value.scope.onDispose(() => {
        finish(Error('asset_revoked'));
        link.remove();
        URL.revokeObjectURL(blob);
      });
      try { document.head.append(link); } catch { finish(Error('style_load_failed')); }
    });
  }

  function liveRuntime(value: Record) {
    if(value.revoked||!dynamic)throw Error('asset_revoked');
    const runtimeUrl=new URL(dynamic.runtimeUrl,document.location.href);
    if(runtimeUrl.origin!==document.location.origin||!['http:','https:'].includes(runtimeUrl.protocol))throw Error('invalid_shared_runtime');
    return runtimeUrl;
  }

  async function importLiveEntry(payload: Payload, entryPath: string, value: Record, runtimeUrl: URL, presentationEntry = false) {
    const decoder=new TextDecoder('utf-8',{fatal:true});
    const entry=payload.get(entryPath);if(!entry||entry.mime!=='text/javascript'||value.revoked)throw Error('invalid_live_entry');
    // Installed page code uses only the shell's one public runtime import.
    let code=decoder.decode(entry.bytes);
    if (presentationEntry && code.startsWith(MODULE_COPY_MARKER)) {
      const end = code.indexOf('\n');
      code = end === -1 ? '' : code.slice(end + 1);
    }
    code=code.replace(/(["'])(?:\.\.?\/)+runtime\.js\1/g,JSON.stringify(runtimeUrl.href));
    const imports=[...code.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)(["'])([^"']+)\1/g)].map(match=>match[2]);
    if(imports.some(path=>path!==runtimeUrl.href))throw Error('unsupported_live_import');
    const blob=URL.createObjectURL(new Blob([code],{type:'text/javascript'}));value.scope.onDispose(()=>URL.revokeObjectURL(blob));
    const mounted=await import(/* @vite-ignore */ blob);
    if(value.revoked)throw Error('asset_revoked');
    return mounted;
  }

  async function livePage(module:ModuleDescriptor,page:PageDescriptor,value:Record):Promise<PageModule>{
    const {entry, styles, payload} = await projection(module, page, value);
    const runtimeUrl = liveRuntime(value);
    for (const path of styles) await liveStyle(module.module_id, payload, path, value);
    const mounted = await importLiveEntry(payload, entry, value, runtimeUrl);
    if(typeof mounted.mount!=='function')throw Error('invalid_page_entry');
    return mounted;
  }
  function invalidate(owner:string){
    const value=owners.get(owner);if(!value)return;value.revoked=true;
    try {value.scope.dispose();owners.delete(owner);}catch{ /* Keep a failed cleanup owner for retry. */ }
  }
  function record(module:ModuleDescriptor): Record {
    const owner=module.module_id,identity=assetIdentity(module,'');
    let value = owners.get(owner);
    if(value&&value.identity!==identity){invalidate(owner);value=owners.get(owner);}
    if (!value) {const next: Record = {identity,revoked: false, scope: createScope(() => !next.revoked), styles: new Map(), entries: new Map(),projections:new Map(),images:new Map()}; owners.set(owner, next); value = next;}
    if (value.revoked) throw Error('asset_revoked');
    return value;
  }
  function style(owner: string, value: Record, path: string): Promise<void> {
    const cached = value.styles.get(path); if (cached) return cached;
    const pending = new Promise<void>((resolve, reject) => {
      const template = document.getElementById('module-style-assets') as HTMLTemplateElement | null;
      const source = Array.from(template?.content.querySelectorAll<HTMLLinkElement>('link[data-resource]') || []).find(link => link.dataset.resource === path && link.rel === 'stylesheet');
      if (!source) {reject(Error('missing_style_projection')); return;}
      // Keep the Host's URL opaque. Presence of a link/sheet is not load evidence.
      const link = source.cloneNode(true) as HTMLLinkElement; link.dataset.moduleStyle = owner;
      let settled = false;
      const finish = (error?: Error) => {if (settled) return; settled = true; clearTimeout(timer); link.removeEventListener('load', loaded); link.removeEventListener('error', failed); error ? reject(error) : resolve();};
      const loaded = () => finish(), failed = () => finish(Error('style_load_failed'));
      const timer = setTimeout(() => finish(Error('style_load_timeout')), timeoutMs);
      link.addEventListener('load', loaded); link.addEventListener('error', failed);
      value.scope.onDispose(() => {finish(Error('asset_revoked')); link.remove();});
      try {document.head.append(link);} catch {finish(Error('style_load_failed'));}
    });
    value.styles.set(path, pending); return pending;
  }
  return {
    async moduleCopy(module: ModuleDescriptor, signal: AbortSignal): Promise<ModuleCopy | null> {
      const paths = presentationPaths(module);
      if (!paths || signal.aborted) return null;
      try {
        const value = record(module);
        let bytes: Uint8Array | null | undefined;
        if (paths.page) {
          // Share any existing verification, but do not seed the business or
          // logo projection cache with failures from this optional read.
          const live = await (value.projections.get(paths.page.route_id) ?? liveResources(module, paths.page, value));
          const item = live.payload.get(paths.entry.slice(paths.prefix.length));
          if (item?.mime === 'text/javascript') bytes = item.bytes;
        } else {
          const resource = module.resources.find(resource => resource.path === paths.entry)!;
          bytes = await staticCopyBytes(paths.entry, resource.sha256, value, signal);
        }
        if (!bytes || signal.aborted || value.revoked || owners.get(module.module_id) !== value) return null;
        return parseModuleCopy(bytes, new Set(module.pages.map(page => page.route_id)));
      } catch {
        return null;
      }
    },
    async pageLogo(module: ModuleDescriptor, page: PageDescriptor, scope: ResourceScope): Promise<string | undefined> {
      const path = module.display?.logo_resource;
      if (!scope.isCurrent() || module.state !== 'loaded' || !module.runtime_id ||
          page.role !== 'home' || !module.pages.includes(page) || !path ||
          !/\.(?:svg|png|jpg|jpeg)$/.test(path) ||
          !module.resources.some(item => item.path === path)) return undefined;
      const prefix = `module-assets/${module.module_id}/`;
      const livePrefix = `${prefix}pages/p${module.asset_version}/`;
      const owner = owners.get(module.module_id);
      if (!owner || owner.revoked || owner.identity !== assetIdentity(module, '')) return undefined;

      if (!page.entry.startsWith(livePrefix)) {
        if (module.source?.kind === 'builtin' && page.entry.startsWith(prefix) &&
            path.startsWith(prefix) && !path.startsWith(livePrefix)) return './' + path;
        return undefined;
      }
      if (!path.startsWith(livePrefix)) return undefined;
      // The page loader has already verified this route's full resource payload.
      const captured = owner.projections.get(page.route_id);
      if (!captured) return undefined;
      const {payload} = await captured;
      if (!scope.isCurrent() || owner.revoked || owners.get(module.module_id) !== owner) return undefined;
      const url = URL.createObjectURL(liveLogo(payload, path.slice(livePrefix.length)));
      scope.onDispose(() => URL.revokeObjectURL(url));
      return url;
    },
    async presentation(module: ModuleDescriptor, isCurrent: () => boolean = () => true): Promise<ModulePresentationExports | null> {
      const paths = presentationPaths(module);
      if (!paths) return null;
      const value = record(module);
      const live = paths.page ? await projection(module, paths.page, value) : null;
      if (value.revoked || !isCurrent()) throw Error('asset_revoked');
      const runtimeUrl = live ? liveRuntime(value) : null;
      if (!live && !loadPresentation) throw Error('missing_presentation_projection');
      if (paths.style) {
        if (live) {
          let pending = value.styles.get(paths.style);
          if (!pending) {
            pending = liveStyle(module.module_id, live.payload, paths.style.slice(paths.prefix.length), value);
            value.styles.set(paths.style, pending);
          }
          await pending;
        } else {
          await style(module.module_id, value, paths.style);
        }
      }
      if (value.revoked || !isCurrent()) throw Error('asset_revoked');
      if (!value.presentation) {
        value.presentation = (async () => {
          if (live && runtimeUrl) {
            const imported = await importLiveEntry(live.payload, paths.entry.slice(paths.prefix.length), value, runtimeUrl, true);
            return presentationExports(imported);
          }
          return presentationExports(await loadPresentation!(paths.entry));
        })();
      }
      const exports = await value.presentation;
      if (value.revoked || !isCurrent()) throw Error('asset_revoked');
      return exports;
    },
    async image(module:ModuleDescriptor,path:string):Promise<string>{
      const prefix=`module-assets/${module.module_id}/pages/p${module.asset_version}/`;
      if(module.state!=='loaded'||!module.runtime_id||path!==module.display?.logo_resource||!path.startsWith(prefix)||!module.resources.some(item=>item.path===path)||!/\.(?:svg|png|jpg|jpeg)$/.test(path))throw Error('invalid_live_image');
      const page=module.pages.find(item=>item.entry.startsWith(prefix));if(!page)throw Error('missing_live_projection');
      const value=record(module);let cached=value.images.get(path);
      if(!cached){cached=(async()=>{
        const {payload}=await projection(module,page,value);
        if(value.revoked)throw Error('invalid_live_image');
        const blob=URL.createObjectURL(liveLogo(payload,path.slice(prefix.length)));
        value.scope.onDispose(()=>URL.revokeObjectURL(blob));return blob;
      })();value.images.set(path,cached);}return cached;
    },
    async load(module: ModuleDescriptor, page: PageDescriptor): Promise<PageModule> {
      const value = record(module);
      if(new RegExp('/pages/p[0-9a-f]{64}/').test(page.entry)){
        let entry=value.entries.get(page.entry);if(!entry){entry=livePage(module,page,value);value.entries.set(page.entry,entry);}return entry;
      }
      const styles = page.styles.map(path => style(module.module_id, value, path));
      let entry = value.entries.get(page.entry);
      if (!entry) {entry = Promise.resolve().then(() => loadPage(page.entry)); value.entries.set(page.entry, entry);}
      const [code] = await Promise.all([entry, ...styles]);
      if (value.revoked) throw Error('asset_revoked');
      return code;
    },
    invalidate,
    invalidateAll() {for (const owner of owners.keys()) this.invalidate(owner);},
    retryCleanup() {for (const [owner, value] of owners) if (value.revoked) this.invalidate(owner);},
    get cleanupPending() {return [...owners.values()].some(value => value.revoked);},
  };
}
