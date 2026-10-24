import {createScope} from './lifecycle';
import type {Bridge, ModuleDescriptor, PageDescriptor, PageModule} from './contracts';

// This identity belongs to the HTML projection, not to a transient page view.
export function assetIdentity(module: ModuleDescriptor, boundary: string): string {
  return JSON.stringify([module.module_id, module.runtime_id, module.module_epoch, module.asset_version, boundary,
    module.resources, module.pages.map(page => [page.route_id, page.entry, page.styles, page.capability_id])]);
}

export function createModuleAssets(document: Document, loadPage: (entry: string) => Promise<PageModule>, timeoutMs: number, dynamic?:{bridge:Bridge;runtimeUrl:string}) {
  type Payload = Map<string,{bytes:Uint8Array;mime:string}>;
  type Projection = {entry:string;styles:string[];payload:Payload};
  type Record = {identity:string;revoked: boolean; scope: ReturnType<typeof createScope>; styles: Map<string, Promise<void>>; entries: Map<string, Promise<PageModule>>; projections:Map<string,Promise<Projection>>; images:Map<string,Promise<string>>};
  const owners = new Map<string, Record>();
  async function liveResources(module:ModuleDescriptor,page:PageDescriptor,value:Record):Promise<Projection>{
    if(!dynamic||!module.runtime_id)throw Error('missing_live_projection');
    const response=await dynamic.bridge.apiPost('admin/module-page-assets',{owner:module.module_id,page:page.route_id,runtime_id:module.runtime_id,module_epoch:module.module_epoch}) as {entry:string;styles:string[];asset_version:string;resources:{path:string;sha256:string;media_type:string;base64:string}[]};
    if(value.revoked||!response||response.asset_version!==module.asset_version||!Array.isArray(response.resources)||response.resources.length>128||!Array.isArray(response.styles))throw Error('asset_revoked');
    const prefix=`module-assets/${module.module_id}/pages/p${module.asset_version}/`;
    if(page.entry!==prefix+response.entry||JSON.stringify(page.styles)!==JSON.stringify(response.styles.map(path=>prefix+path)))throw Error('invalid_live_projection');
    const expected=new Map(module.resources.map(item=>[item.path,item.sha256])),payload:Payload=new Map();
    if(expected.size!==module.resources.length)throw Error('invalid_live_projection');
    const media:{[key:string]:string}={js:'text/javascript',css:'text/css',svg:'image/svg+xml',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp'};
    let total=0;
    for(const item of response.resources){
      if(typeof item.path!=='string'||typeof item.base64!=='string'||item.base64.length>5592416||expected.get(prefix+item.path)!==item.sha256||payload.has(item.path)||typeof item.media_type!=='string'||!media[item.path.split('.').pop()||'']||media[item.path.split('.').pop()||'']!==item.media_type)throw Error('invalid_live_resource');
      const bytes=Uint8Array.from(atob(item.base64),c=>c.charCodeAt(0));total+=bytes.length;
      if(total>16777216)throw Error('live_resource_budget');
      const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),x=>x.toString(16).padStart(2,'0')).join('');
      if(digest!==item.sha256||value.revoked)throw Error('asset_revoked');
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
  async function livePage(module:ModuleDescriptor,page:PageDescriptor,value:Record):Promise<PageModule>{
    const {entry:entryPath,styles,payload}=await projection(module,page,value);
    if(value.revoked||!dynamic)throw Error('asset_revoked');
    const runtimeUrl=new URL(dynamic.runtimeUrl,document.location.href);
    if(runtimeUrl.origin!==document.location.origin||!['http:','https:'].includes(runtimeUrl.protocol))throw Error('invalid_shared_runtime');
    const decoder=new TextDecoder('utf-8',{fatal:true});
    for(const path of styles){
      const item=payload.get(path);if(!item||item.mime!=='text/css')throw Error('invalid_live_style');
      const css=decoder.decode(item.bytes);
      if(/@import\b|url\s*\(/i.test(css))throw Error('unsupported_live_style_reference');
      const blob=URL.createObjectURL(new Blob([css],{type:'text/css'}));value.scope.onDispose(()=>URL.revokeObjectURL(blob));
      await new Promise<void>((resolve,reject)=>{
        const link=document.createElement('link');link.rel='stylesheet';link.href=blob;link.dataset.moduleStyle=module.module_id;
        let done=false;const finish=(error?:Error)=>{if(done)return;done=true;clearTimeout(timer);link.onload=null;link.onerror=null;error?reject(error):resolve();};
        const timer=setTimeout(()=>finish(Error('style_load_timeout')),timeoutMs);link.onload=()=>finish();link.onerror=()=>finish(Error('style_load_failed'));
        value.scope.onDispose(()=>{finish(Error('asset_revoked'));link.remove();});document.head.append(link);
      });
    }
    const entry=payload.get(entryPath);if(!entry||entry.mime!=='text/javascript'||value.revoked)throw Error('invalid_live_entry');
    // Installed page code uses only the shell's one public runtime import.
    let code=decoder.decode(entry.bytes);
    code=code.replace(/(["'])(?:\.\.?\/)+runtime\.js\1/g,JSON.stringify(runtimeUrl.href));
    const imports=[...code.matchAll(/(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)(["'])([^"']+)\1/g)].map(match=>match[2]);
    if(imports.some(path=>path!==runtimeUrl.href))throw Error('unsupported_live_import');
    const blob=URL.createObjectURL(new Blob([code],{type:'text/javascript'}));value.scope.onDispose(()=>URL.revokeObjectURL(blob));
    const mounted=await import(/* @vite-ignore */ blob);
    if(value.revoked||typeof mounted.mount!=='function')throw Error('asset_revoked');
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
    async image(module:ModuleDescriptor,path:string):Promise<string>{
      const prefix=`module-assets/${module.module_id}/pages/p${module.asset_version}/`;
      if(module.state!=='loaded'||!module.runtime_id||path!==module.display?.logo_resource||!path.startsWith(prefix)||!module.resources.some(item=>item.path===path)||!/\.(?:svg|png|jpg|jpeg)$/.test(path))throw Error('invalid_live_image');
      const page=module.pages.find(item=>item.entry.startsWith(prefix));if(!page)throw Error('missing_live_projection');
      const value=record(module);let cached=value.images.get(path);
      if(!cached){cached=(async()=>{
        const {payload}=await projection(module,page,value),item=payload.get(path.slice(prefix.length));
        if(value.revoked||!item||!['image/svg+xml','image/png','image/jpeg'].includes(item.mime))throw Error('invalid_live_image');
        const blob=URL.createObjectURL(new Blob([item.bytes as BlobPart],{type:item.mime}));
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
