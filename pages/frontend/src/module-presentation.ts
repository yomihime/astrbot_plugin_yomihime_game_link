import type {MountedLogo, ModulePresentationExports, ResolvedPageAppearance} from '../../public-contract';
import type {ModuleDescriptor, PageContext} from './contracts';
import {createScope} from './lifecycle';
import {assetIdentity, presentationPaths, type createModuleAssets} from './module-assets';
import {resolvePageAppearance} from './ui/page-appearance';

type Assets = Pick<ReturnType<typeof createModuleAssets>,
  'image' | 'presentation' | 'invalidate' | 'retryCleanup' | 'cleanupPending'>;
type Context = Pick<PageContext, 'theme' | 'locale' | 'available' | 'boundary'>;
type Surface = {
  scope: ReturnType<typeof createScope>;
  host: Element | null;
  node: HTMLElement | null;
  handle: MountedLogo | null;
  mounting: boolean;
  retiring: boolean;
  shown: boolean;
  bind(node: unknown): void;
};
type Owner = {
  identity: string;
  module: ModuleDescriptor;
  context: Readonly<PageContext>;
  url: string | null;
  appearance: ResolvedPageAppearance;
  exports: ModulePresentationExports | null;
  surface: Surface | null;
  revoked: boolean;
  failed: boolean;
};

function logoResource(module: ModuleDescriptor) {
  const path = module.display?.logo_resource;
  if (!path) return null;
  const prefix = `module-assets/${module.module_id}/pages/p${module.asset_version}/`;
  return {path, live: path.startsWith(prefix)};
}

export function createDirectoryPresentation(
  assets: Assets, changed: () => void, canExecuteModule: (module: ModuleDescriptor) => boolean,
) {
  const owners = new Map<string, Owner>();
  let closed = false;

  function stop(owner: Owner) {
    const surface = owner.surface;
    if (!surface) return true;
    surface.retiring = true;
    surface.shown = false;
    if (surface.mounting) return false;
    try {
      surface.scope.dispose();
      if (surface.handle) {
        surface.handle.dispose();
        surface.handle = null;
      }
      surface.node?.remove();
      owner.surface = null;
      return true;
    } catch {
      // Keep unfinished cleanup and its resource leases for explicit retry.
      return false;
    }
  }

  function retire(owner: Owner) {
    owner.revoked = true;
    owner.url = null;
    if (!stop(owner)) return;
    const id = owner.module.module_id;
    assets.invalidate(id);
    owners.delete(id);
  }

  function mount(owner: Owner, surface: Surface) {
    if (!surface.scope.isCurrent() || surface.node || !surface.host ||
        !owner.url || !owner.exports?.mountLogo || owner.failed) return;
    const node = surface.host.ownerDocument.createElement('span');
    surface.node = node;
    surface.mounting = true;
    try {
      surface.host.append(node);
      const handle = owner.exports.mountLogo(node, Object.freeze({
        resourceUrl: owner.url,
        context: owner.context,
        appearance: owner.appearance,
        scope: surface.scope,
      }));
      // A malformed return may still provide the disposal needed to undo its work.
      if (handle && typeof handle.dispose === 'function') surface.handle = handle;
      if (!surface.handle || typeof handle.update !== 'function') throw Error('invalid_logo_handle');
      surface.shown = surface.scope.isCurrent();
    } catch {
      owner.failed = true;
      surface.retiring = true;
    } finally {
      surface.mounting = false;
      if (owner.revoked) retire(owner);
      else if (!surface.scope.isCurrent()) stop(owner);
      changed();
    }
  }

  function startSurface(owner: Owner) {
    if (owner.surface || owner.failed || !canExecuteModule(owner.module) ||
        !presentationPaths(owner.module)) return;
    const surface: Surface = {
      scope: createScope(() => !closed && !owner.revoked &&
        owner.surface === surface && !surface.retiring && canExecuteModule(owner.module)),
      host: null,
      node: null,
      handle: null,
      mounting: false,
      retiring: false,
      shown: false,
      bind(node) {
        if (!node || !surface.scope.isCurrent()) return;
        surface.host = node as Element;
        mount(owner, surface);
      },
    };
    owner.surface = surface;
    void assets.presentation(owner.module, () => surface.scope.isCurrent()).then(exports => {
      if (!surface.scope.isCurrent()) return;
      owner.exports = exports;
      owner.appearance = resolvePageAppearance(owner.context.theme, exports?.presentation?.appearance);
      mount(owner, surface);
      changed();
    }).catch(() => {
      if (!surface.scope.isCurrent()) return;
      owner.failed = true;
      stop(owner);
      changed();
    });
  }

  function update(owner: Owner, next: Readonly<PageContext>) {
    const previous = owner.context;
    if (previous.theme === next.theme && previous.locale === next.locale &&
        previous.available === next.available) return;
    owner.context = next;
    owner.appearance = resolvePageAppearance(next.theme, owner.exports?.presentation?.appearance);
    const surface = owner.surface;
    if (!surface?.scope.isCurrent() || !surface.handle) return;
    try {
      surface.handle.update(next, owner.appearance);
    } catch {
      owner.failed = true;
      stop(owner);
    }
  }

  function sync(modules: readonly ModuleDescriptor[], context: Context, visible: boolean) {
    if (closed) return;
    const loaded = new Map(modules.filter(module => module.state === 'loaded' && module.runtime_id)
      .map(module => {
        const identity = JSON.stringify([assetIdentity(module, context.boundary),
          module.source?.kind, module.display?.logo_resource]);
        return [module.module_id, {module, identity}] as const;
      }));
    for (const [id, owner] of owners) {
      if (owner.revoked) continue;
      if (loaded.get(id)?.identity !== owner.identity) retire(owner);
      else {
        const executable = canExecuteModule(owner.module);
        if (!executable && owner.exports) {
          owner.exports = null;
          owner.appearance = resolvePageAppearance(owner.context.theme);
        }
        if ((!visible || !executable) && !owner.surface?.retiring) stop(owner);
      }
    }
    if (!visible) return;
    for (const [id, {module, identity}] of loaded) {
      const existing = owners.get(id);
      if (existing?.revoked) continue;
      const current: Readonly<PageContext> = Object.freeze({
        ...context, owner: id, runtimeId: module.runtime_id!, epoch: module.module_epoch,
      });
      if (existing) {
        update(existing, current);
        startSurface(existing);
        continue;
      }
      const resource = logoResource(module);
      const owner: Owner = {
        module, identity, context: current,
        url: resource && !resource.live && module.source?.kind === 'builtin' ? './' + resource.path : null,
        appearance: resolvePageAppearance(current.theme),
        exports: null, surface: null, revoked: false, failed: false,
      };
      owners.set(id, owner);
      if (resource?.live) {
        void assets.image(module, resource.path).then(url => {
          if (closed || owner.revoked || owners.get(id) !== owner) return;
          owner.url = url;
          if (owner.surface) mount(owner, owner.surface);
          changed();
        }).catch(() => { /* Keep the decorative fallback. */ });
      }
      startSurface(owner);
    }
  }

  return {
    sync,
    view(id: string) {
      const owner = owners.get(id);
      if (!owner || owner.revoked) return null;
      return {url: owner.url, appearance: owner.appearance, shown: owner.surface?.shown === true,
        bind: owner.surface?.bind};
    },
    revoke() {
      for (const owner of owners.values()) if (!owner.revoked) retire(owner);
    },
    close() {
      closed = true;
      this.revoke();
    },
    retryCleanup() {
      // Only retry asset records that had already been retired before this call.
      assets.retryCleanup();
      for (const owner of owners.values()) {
        if (owner.revoked) retire(owner);
        else if (owner.surface?.retiring) stop(owner);
      }
    },
    get cleanupPending() {
      return assets.cleanupPending || [...owners.values()].some(owner => owner.surface?.retiring);
    },
  };
}
