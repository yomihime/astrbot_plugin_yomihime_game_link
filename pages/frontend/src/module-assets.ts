import {createScope} from './lifecycle';
import type {ModuleDescriptor, PageDescriptor, PageModule} from './contracts';

// This identity belongs to the HTML projection, not to a transient page view.
export function assetIdentity(module: ModuleDescriptor, boundary: string): string {
  return JSON.stringify([module.module_id, module.runtime_id, module.module_epoch, module.asset_version, boundary,
    module.resources, module.pages.map(page => [page.route_id, page.entry, page.styles, page.capability_id])]);
}

export function createModuleAssets(document: Document, loadPage: (entry: string) => Promise<PageModule>, timeoutMs: number) {
  type Record = {revoked: boolean; scope: ReturnType<typeof createScope>; styles: Map<string, Promise<void>>; entries: Map<string, Promise<PageModule>>};
  const owners = new Map<string, Record>();
  function record(owner: string): Record {
    let value = owners.get(owner);
    if (!value) {const next: Record = {revoked: false, scope: createScope(() => !next.revoked), styles: new Map(), entries: new Map()}; owners.set(owner, next); value = next;}
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
    async load(module: ModuleDescriptor, page: PageDescriptor): Promise<PageModule> {
      const value = record(module.module_id);
      const styles = page.styles.map(path => style(module.module_id, value, path));
      let entry = value.entries.get(page.entry);
      if (!entry) {entry = Promise.resolve().then(() => loadPage(page.entry)); value.entries.set(page.entry, entry);}
      const [code] = await Promise.all([entry, ...styles]);
      if (value.revoked) throw Error('asset_revoked');
      return code;
    },
    invalidate(owner: string) {
      const value = owners.get(owner); if (!value) return;
      value.revoked = true;
      try {value.scope.dispose(); owners.delete(owner);} catch { /* Failed callbacks retain their exact owner for retry. */ }
    },
    invalidateAll() {for (const owner of owners.keys()) this.invalidate(owner);},
    retryCleanup() {for (const [owner, value] of owners) if (value.revoked) this.invalidate(owner);},
    get cleanupPending() {return [...owners.values()].some(value => value.revoked);},
  };
}
