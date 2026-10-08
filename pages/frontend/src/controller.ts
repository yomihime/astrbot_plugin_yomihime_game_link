import {validateCatalog, readRoute, routeHash} from './catalog';
import {createScope, contextBoundary, mountIdentity} from './lifecycle';
import {createModuleAssets, assetIdentity} from './module-assets';
import type {Bridge, Catalog, ModuleDescriptor, PageDescriptor, PageContext, PageModule, MountedPage} from './contracts';
export function createShell({bridge, container, window, loadPage, changed, timeoutMs = 8000, expectedPlugin = 'astrbot_plugin_yomihime_game_link', expectedPage = 'shell'}: {bridge: Bridge; container: HTMLElement; window: Window; loadPage(entry: string): Promise<PageModule>; changed(): void; timeoutMs?: number; expectedPlugin?: string; expectedPage?: string}) {
  const state = {catalog: null as Catalog | null, busy: false, stale: false, message: '正在连接宿主。', theme: 'light' as 'light'|'dark', locale: 'zh-CN', selected: null as {owner: string; route: string}|null, mounted: false, cleanupPending: false, home: true, settings: false, settingsOwner: null as string|null};
  let closed = false, ready = false, sequence = 0, generation = 0, boundary = '', contextSeen = false;
  let off: (() => void) | undefined, mounted: MountedPage | null = null, identity = '', pendingIdentity = '';
  let scope: ReturnType<typeof createScope> | null = null;
  const assets = createModuleAssets(container.ownerDocument, loadPage, timeoutMs), bindings = new Map<string, string>(), revokedOwners = new Set<string>();
  const reopenMessage = '模块资源已失效。请关闭此页，并从宿主插件详情重新打开页面。';
  let projectionRevoked = false, catalogSeen = false, activeOwner = '';
  let retiring: {scope: ReturnType<typeof createScope> | null; page: MountedPage | null; scopeDone: boolean; pageDone: boolean; domDone: boolean} | null = null;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const deadline = <T>(promise: Promise<T>): Promise<T> => new Promise((resolve,reject) => {const timer = setTimeout(() => {timers.delete(timer); reject(Error('timeout'));}, timeoutMs); timers.add(timer); promise.then(resolve,reject).finally(() => {clearTimeout(timer); timers.delete(timer);});});
  function selected(): {module: ModuleDescriptor; page: PageDescriptor}|null {if (state.settings) return null; const module = state.catalog?.modules?.find(m => m.module_id === state.selected?.owner); const page = module?.pages.find(p => p.route_id === state.selected?.route); return module?.state === 'loaded' && page ? {module,page} : null;}
  function pageContext(module: ModuleDescriptor): Readonly<PageContext> {return Object.freeze({theme: state.theme, locale: state.locale, available: ready && !state.busy && !state.stale && !retiring && !projectionRevoked && !revokedOwners.has(module.module_id), owner: module.module_id, runtimeId: module.runtime_id!, epoch: module.module_epoch, boundary});}
  function cleanupState() {state.cleanupPending = retiring !== null || assets.cleanupPending; if (state.cleanupPending) state.message='页面清理未完成，请重试页面清理。';}
  function revokeOwner(owner: string) {revokedOwners.add(owner); if (activeOwner === owner) disposePage(); assets.invalidate(owner); cleanupState();}
  function reconcileAssets(data: Catalog) {
    const next = new Map((data.modules || []).filter(module => module.state === 'loaded').map(module => [module.module_id, assetIdentity(module, boundary)]));
    if (catalogSeen) {
      for (const [owner, key] of bindings) if (next.get(owner) !== key) revokeOwner(owner);
      // New lifecycles need a new Host HTML projection as well.
      for (const owner of next.keys()) if (!bindings.has(owner)) revokeOwner(owner);
    }
    bindings.clear(); for (const [owner, key] of next) bindings.set(owner, key); catalogSeen = true;
  }
  function disposePage() {
    if (!retiring) {
      generation++; pendingIdentity=''; identity=''; state.mounted=false;
      retiring={scope,page:mounted,scopeDone:scope===null,pageDone:mounted===null,domDone:false};
    }
    const owner=retiring!, errors: unknown[]=[];
    if (!owner.scopeDone) {try {owner.scope!.dispose(); owner.scopeDone=true;} catch (error) {errors.push(error);}}
    // Do not call a failed module cleanup again in the same pass through scope.
    if (owner.scopeDone && !owner.pageDone) {try {owner.page!.dispose(); owner.pageDone=true;} catch (error) {errors.push(error);}}
    if (owner.scopeDone && owner.pageDone && !owner.domDone) {try {container.replaceChildren(); owner.domDone=true;} catch (error) {errors.push(error);}}
    if (owner.scopeDone && owner.pageDone && owner.domDone) {scope=null; mounted=null; retiring=null; activeOwner='';}
    cleanupState();
  }
  async function syncPage() {
    if (retiring) {changed(); return;}
    const selection = selected();
    if (!selection || !ready) {disposePage(); if (!state.cleanupPending && (projectionRevoked || revokedOwners.has(state.selected?.owner || ''))) state.message=reopenMessage; changed(); return;}
    const {module,page} = selection, target = mountIdentity(module,page,boundary);
    if (projectionRevoked || revokedOwners.has(module.module_id)) {disposePage(); if (!state.cleanupPending) state.message = reopenMessage; changed(); return;}
    if (target === identity && mounted) {mounted.update(pageContext(module)); changed(); return;}
    if (target === pendingIdentity) return;
    disposePage(); if (retiring) {changed(); return;}
    pendingIdentity = target; activeOwner = module.module_id; const version = generation;
    const ownScope = createScope(() => !closed && generation === version && selected() !== null); scope = ownScope;
    try {
      const code = await deadline(assets.load(module, page));
      if (!ownScope.isCurrent() || pendingIdentity !== target) return;
      if (!code || typeof code.mount !== 'function') throw Error('invalid_page_entry');
      const view = container.ownerDocument.createElement('div'); view.className = 'module-view'; view.dataset.owner = module.module_id; container.append(view);
      const services = Object.freeze({async invoke(capabilityId: string, parameters: unknown) {
        if (!ownScope.isCurrent() || state.stale || state.busy || !ready || capabilityId !== page.capability_id) throw Error('page_unavailable');
        const result = await bridge.apiPost('invoke', {owner: module.module_id, page: page.route_id, capability_id: capabilityId, parameters});
        if (!ownScope.isCurrent() || state.stale || state.busy || !ready) throw Error('page_revoked');
        return result;
      }});
      const instance = code.mount(view, Object.freeze({routeId: page.route_id, context: pageContext(module), services, scope: ownScope}));
      if (!instance || typeof instance.update !== 'function' || typeof instance.dispose !== 'function') throw Error('invalid_page_instance');
      mounted = instance; identity = target; pendingIdentity = ''; state.mounted = true; changed();
    } catch {if (!closed && !projectionRevoked && !revokedOwners.has(module.module_id)) {
      // A first load failure still belongs to this owner after its view leaves.
      revokeOwner(module.module_id); if (!state.cleanupPending && state.selected?.owner === module.module_id) state.message = reopenMessage; changed();
    }}
  }
  function pickRoute() {state.settings = window.location.hash === '#/settings'||window.location.hash.startsWith('#/settings/module/'); state.home=!window.location.hash||window.location.hash==='#/'||window.location.hash==='#';state.settingsOwner=null; if (state.settings) {if(window.location.hash.startsWith('#/settings/module/'))try{state.settingsOwner=decodeURIComponent(window.location.hash.slice('#/settings/module/'.length));}catch{}state.selected=null; return;} state.selected=readRoute(window.location.hash);}
  async function refresh() {
    if (closed || !ready || state.busy) return;
    const version = ++sequence; state.busy = true; state.stale = state.catalog !== null; state.message = state.stale ? '正在刷新；当前为上次目录，暂不可提交查询。' : '正在读取模块目录。';
    const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed();
    try {const data = validateCatalog(await deadline(bridge.apiGet('catalog', {}))); if (closed || version !== sequence) return; reconcileAssets(data); state.catalog = data; state.busy = false; state.stale = false; if (!state.cleanupPending) state.message = '状态已更新；查询仅在手动提交时执行。'; pickRoute(); await syncPage();}
    catch {if (closed || version !== sequence) return; state.busy = false; state.stale = state.catalog !== null; state.message = '状态读取失败。保留上次目录，请确认宿主会话后手动重试。'; const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed();}
  }
  function accept(context: Record<string,unknown>, event = false) {
    if (closed || projectionRevoked || !event && contextSeen) return;
    state.theme = typeof context?.isDark === 'boolean' ? context.isDark ? 'dark' : 'light' : context?.theme === 'dark' ? 'dark' : 'light'; state.locale = typeof context?.locale === 'string' ? context.locale : 'zh-CN';
    const revokeProjection = () => {projectionRevoked=true; sequence++; ready=false; state.busy=false; state.stale=state.catalog!==null; disposePage(); assets.invalidateAll(); cleanupState(); if (!state.cleanupPending) state.message=reopenMessage; changed();};
    let next: string; try {next = contextBoundary(context); if (!Object.hasOwn(context,'pluginName') || !Object.hasOwn(context,'pageName') || context.pluginName !== expectedPlugin || context.pageName !== expectedPage) throw Error('wrong_page');} catch {contextSeen=true; revokeProjection(); return;}
    if (contextSeen && ready && next === boundary) {const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed(); return;}
    if (contextSeen) {revokeProjection(); return;}
    contextSeen = true; boundary = next; ready = true; void refresh();
  }
  const hashChanged = () => {pickRoute(); void syncPage(); const main = container.ownerDocument.getElementById('page-root'); main?.focus({preventScroll:true}); window.scrollTo({top:0,left:0,behavior:'instant'});};
  return {state, selected, refresh, selectHome() {window.location.hash='#/'; pickRoute(); void syncPage();}, selectSettings(owner?:string) {window.location.hash=owner?'#/settings/module/'+encodeURIComponent(owner):'#/settings'; pickRoute(); void syncPage();}, select(owner: string, route: string) {window.location.hash = routeHash(owner,route); state.settings=false; state.home=false; state.selected = {owner,route}; void syncPage();},
    retryCleanup() {if (!state.cleanupPending) return; if (retiring) disposePage(); assets.retryCleanup(); cleanupState(); if (!state.cleanupPending) {state.message=projectionRevoked || revokedOwners.has(state.selected?.owner || '') ? reopenMessage : '页面清理完成，请手动刷新状态。'; if (!closed) void syncPage();} changed();},
    start() {if (closed) return; window.addEventListener('hashchange',hashChanged); if (!bridge?.ready || !bridge?.onContext || !bridge?.apiGet || !bridge?.apiPost) {state.message = '宿主连接尚未就绪，请重新打开页面。'; changed(); return;} off = bridge.onContext(context => accept(context,true)); deadline(Promise.resolve(bridge.ready())).then(context => accept(context)).catch(() => {if (!ready && !closed) {state.message = '连接宿主失败，请重新打开页面。'; changed();}});},
    dispose() {if (closed) return; closed = true; sequence++; ready = false; off?.(); window.removeEventListener('hashchange',hashChanged); for (const timer of timers) clearTimeout(timer); timers.clear(); disposePage(); assets.invalidateAll(); cleanupState();},
  };
}
