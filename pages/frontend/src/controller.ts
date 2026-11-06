import {createModuleServices} from './module-services';
import {validateCatalog, readRoute, routeHash} from './catalog';
import {createScope, contextBoundary, mountIdentity} from './lifecycle';
import {createModuleAssets, assetIdentity} from './module-assets';
import {DEFAULT_UI_LOCALE, effectiveUiLocale, type UiLanguage} from './i18n/locale';
import {coreText, type CoreTextKey} from './i18n/messages';
import {readPageLanguage} from './i18n/page-language';
import type {Bridge, Catalog, ModuleDescriptor, PageDescriptor, PageContext, PageModule, MountedPage} from './contracts';
export function createShell({bridge, container, window, loadPage, runtimeUrl, changed, timeoutMs = 8000, expectedPlugin = 'astrbot_plugin_yomihime_game_link', expectedPage = '00-game-link', confirmClear = async () => false}: {bridge: Bridge; container: HTMLElement; window: Window; loadPage(entry: string): Promise<PageModule>; runtimeUrl?:string; changed(): void; timeoutMs?: number; expectedPlugin?: string; expectedPage?: string; confirmClear?(owner:string,field:string,revision:number,isCurrent:()=>boolean):Promise<boolean>}) {
  let preference: UiLanguage = 'auto', preferenceRevision = -1, hostLocale: unknown;
  let messageKey: CoreTextKey = 'shell.connecting', withRecovery = false;
  const show = (key: CoreTextKey, reopen = false) => {messageKey = key; withRecovery = reopen;};
  const state = {catalog: null as Catalog | null, busy: false, stale: false,
    get messageKey() {return messageKey;},
    get message(): string {
      return coreText(this.locale, messageKey) + (withRecovery
        ? (this.locale === 'en-US' ? ' ' : '') + coreText(this.locale, 'shell.reopen') : '');
    },
    theme: 'light' as 'light'|'dark', locale: DEFAULT_UI_LOCALE, selected: null as {owner: string; route: string}|null, mounted: false, cleanupPending: false, home: true, modules: false, settings: false, settingsOwner: null as string|null, moduleOwner:null as string|null, moduleSection:null as 'home'|'config'|'queries'|null};
  let closed = false, ready = false, sequence = 0, generation = 0, boundary = '', contextSeen = false;
  let off: (() => void) | undefined, mounted: MountedPage | null = null, identity = '', pendingIdentity = '';
  let scope: ReturnType<typeof createScope> | null = null;
  const assets = createModuleAssets(container.ownerDocument, loadPage, timeoutMs, runtimeUrl?{bridge,runtimeUrl}:undefined), bindings = new Map<string, string>(), revokedOwners = new Set<string>();
  const ownerFailures = new Map<string,CoreTextKey>();
  const revokedMessage = (): CoreTextKey => projectionRevoked ? 'shell.assetsRevoked' : ownerFailures.get(state.selected?.owner || '') || 'shell.assetsRevoked';
  function failureMessage(error:unknown, phase:'load'|'bootstrap'|'mount'):CoreTextKey {
    // Show only our fixed messages, never a bridge/module exception or URL.
    if (phase === 'mount') return 'shell.mountFailed';
    const row=error as {code?:unknown;error_code?:unknown;message?:unknown;status?:unknown;response?:{status?:unknown}}|null;
    const codes=[row?.code,row?.error_code,row?.message];
    if ([row?.status,row?.response?.status].some(status=>status===401||status===403) || codes.includes('admin_authorization_denied')) return 'shell.authFailed';
    if (codes.some(code=>typeof code==='string'&&['asset_revoked','page_revoked','stale_context','revision_conflict'].includes(code))) return 'shell.assetsRevoked';
    if (codes.includes('invalid_page_entry')) return 'shell.mountFailed';
    if (codes.includes('asset_digest_unavailable')) return 'shell.digestUnavailable';
    if (codes.some(code=>typeof code==='string'&&['asset_digest_mismatch','invalid_live_projection','invalid_live_resource','incomplete_live_projection','live_resource_budget','invalid_live_style','invalid_live_entry','invalid_shared_runtime','unsupported_live_style_reference','unsupported_live_import'].includes(code))) return 'shell.digestMismatch';
    if (phase === 'bootstrap') return 'shell.serviceUnavailable';
    return 'shell.assetsFailed';
  }
  let projectionRevoked = false, catalogSeen = false, activeOwner = '';
  let navigationVersion=0,lastPageNavigationVersion=-1;
  function navigationImpact(){
    if(!mounted||!scope?.isCurrent()||!activeOwner)return null;
    try{
      if(mounted.navigationImpact===undefined)return null;
      if(typeof mounted.navigationImpact!=='function')throw Error();
      const value=mounted.navigationImpact();
      if(!value||typeof value!=='object'||Object.keys(value).sort().join(',')!=='credentials,ordinary,pending,version'||typeof value.ordinary!=='boolean'||typeof value.credentials!=='boolean'||typeof value.pending!=='boolean'||!Number.isSafeInteger(value.version)||value.version<0||value.version<lastPageNavigationVersion)throw Error();
      lastPageNavigationVersion=value.version;
      return Object.freeze({owner:activeOwner,ordinary:value.ordinary,credentials:value.credentials,pending:value.pending,version:value.version,notification:navigationVersion,blocked:false});
    }catch{return Object.freeze({owner:activeOwner,ordinary:false,credentials:false,pending:false,version:0,notification:navigationVersion,blocked:true});}
  }
  let retiring: {scope: ReturnType<typeof createScope> | null; page: MountedPage | null; scopeDone: boolean; pageDone: boolean; domDone: boolean} | null = null;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const deadline = <T>(promise: Promise<T>): Promise<T> => new Promise((resolve,reject) => {const timer = setTimeout(() => {timers.delete(timer); reject(Error('timeout'));}, timeoutMs); timers.add(timer); promise.then(resolve,reject).finally(() => {clearTimeout(timer); timers.delete(timer);});});
  function selected(): {module: ModuleDescriptor; page: PageDescriptor}|null {if (state.settings) return null; const module = state.catalog?.modules?.find(m => m.module_id === state.selected?.owner); const page = module?.pages.find(p => p.route_id === state.selected?.route); return module?.state === 'loaded' && page ? {module,page} : null;}
  function pageContext(module: ModuleDescriptor): Readonly<PageContext> {return Object.freeze({theme: state.theme, locale: state.locale, available: ready && !state.busy && !state.stale && !retiring && !projectionRevoked && !revokedOwners.has(module.module_id), owner: module.module_id, runtimeId: module.runtime_id!, epoch: module.module_epoch, boundary});}
  function cleanupState() {state.cleanupPending = retiring !== null || assets.cleanupPending; if (state.cleanupPending) show('shell.cleanupPending');}
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
  function canExecuteModule(module: ModuleDescriptor) {
    // The document's executable projection cannot be renewed by a catalog refresh.
    return ready && !closed && !projectionRevoked && !revokedOwners.has(module.module_id) &&
      bindings.get(module.module_id) === assetIdentity(module, boundary);
  }
  function disposePage() {
    if (!retiring) {
      generation++; navigationVersion++;lastPageNavigationVersion=-1;pendingIdentity=''; identity=''; state.mounted=false;
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
    if (!selection || !ready) {disposePage(); if (!state.cleanupPending && (projectionRevoked || revokedOwners.has(state.selected?.owner || ''))) show(revokedMessage(), true); changed(); return;}
    const {module,page} = selection, target = mountIdentity(module,page,boundary);
    if (!canExecuteModule(module)) {disposePage(); if (!state.cleanupPending) show(revokedMessage(), true); changed(); return;}
    if (target === identity && mounted) {mounted.update(pageContext(module)); changed(); return;}
    if (target === pendingIdentity) return;
    disposePage(); if (retiring) {changed(); return;}
    pendingIdentity = target; activeOwner = module.module_id; const version = generation;
    const ownScope = createScope(() => !closed && generation === version && selected() !== null); scope = ownScope;
    let phase:'load'|'bootstrap'|'mount'='load';
    try {
      const code = await deadline(assets.load(module, page));
      if (!ownScope.isCurrent() || pendingIdentity !== target) return;
      if (!code || typeof code.mount !== 'function') {phase='mount';throw Error('invalid_page_entry');}
      const view = container.ownerDocument.createElement('div'); view.className = 'module-view'; view.dataset.owner = module.module_id; container.append(view);
      phase='bootstrap';
      let services: Awaited<ReturnType<typeof createModuleServices>>;
      try {
        services = await createModuleServices({onDispose:callback=>ownScope.onDispose(callback),bridge, binding:{owner:module.module_id,page:page.route_id,runtime_id:module.runtime_id!,module_epoch:module.module_epoch}, capabilityId:page.capability_id, isCurrent:()=>ownScope.isCurrent() && !state.stale && !state.busy && ready, confirmClear:(field,revision,isCurrent)=>confirmClear(module.module_id,field,revision,isCurrent)});
      } catch (error) {
        // A disposed view's bootstrap cannot revoke a newer view of the same owner.
        // Asset failures above still retire their owner, even after navigation.
        if (!ownScope.isCurrent() || pendingIdentity !== target) return;
        throw error;
      }
      if (!ownScope.isCurrent() || pendingIdentity !== target) return;
      let logoResourceUrl: string | undefined;
      if (page.role === 'home' && pageContext(module).available) {
        // Decoration is optional even if URL creation fails.
        try { logoResourceUrl = await assets.pageLogo(module, page, ownScope); } catch {}
        const current = selected();
        if (!ownScope.isCurrent() || pendingIdentity !== target || !current ||
            mountIdentity(current.module, current.page, boundary) !== target) return;
        if (current.page.role !== 'home' || !pageContext(current.module).available) logoResourceUrl = undefined;
      }
      phase='mount';
      const instance = code.mount(view, Object.freeze({routeId: page.route_id, context: pageContext(module), services, scope: ownScope,
        ...(logoResourceUrl === undefined ? {} : {logoResourceUrl}),
        onNavigationStateChange:()=>{if(ownScope.isCurrent()){navigationVersion++;changed();}}}));
      if (!instance || typeof instance.update !== 'function' || typeof instance.dispose !== 'function') throw Error('invalid_page_instance');
      mounted = instance; identity = target; pendingIdentity = ''; state.mounted = true; changed();
    } catch (error) {if (!closed && !projectionRevoked && !revokedOwners.has(module.module_id)) {
      // A first load failure still belongs to this owner after its view leaves.
      ownerFailures.set(module.module_id,failureMessage(error,phase));
      revokeOwner(module.module_id); if (!state.cleanupPending && state.selected?.owner === module.module_id) show(revokedMessage(), true); changed();
    }}
  }
  function pickRoute() {
    const hash=window.location.hash;state.settings=hash==='#/settings';state.modules=hash==='#/modules';state.home=!hash||hash==='#/'||hash==='#';state.settingsOwner=null;state.moduleOwner=null;state.moduleSection=null;state.selected=null;
    if(state.settings||state.home||state.modules)return;
    const landing=/^#\/module\/([^/]+)(\/\-\/queries)?$/.exec(hash);
    if(landing){try{state.moduleOwner=decodeURIComponent(landing[1]);state.moduleSection=landing[2]?'queries':'home';
      const module=state.catalog?.modules?.find(m=>m.module_id===state.moduleOwner),home=module?.pages.find(p=>p.role==='home');
      if(!landing[2]&&home)state.selected={owner:module!.module_id,route:home.route_id};
    }catch{}return;}
    state.selected=readRoute(hash);state.moduleOwner=state.selected?.owner||null;
    const page=state.catalog?.modules?.find(m=>m.module_id===state.moduleOwner)?.pages.find(p=>p.route_id===state.selected?.route);
    state.moduleSection=page?.role==='config'?'config':page?.role==='home'?'home':page?'queries':null;
  }
  function applyLocale() {
    const next = effectiveUiLocale(preference, hostLocale);
    const changedLocale = next !== state.locale;
    state.locale = next;
    container.ownerDocument.documentElement.lang = next;
    container.ownerDocument.title = coreText(next, 'shell.brandShort');
    return changedLocale;
  }
  function applyLanguageSnapshot(payload: unknown) {
    if (!ready || closed || projectionRevoked) return;
    const value = readPageLanguage(payload);
    if (!value || value.revision < preferenceRevision) return;
    preferenceRevision = value.revision;
    preference = value.preference;
    if (!applyLocale()) return;
    const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed();
  }
  async function readLanguage() {
    try {applyLanguageSnapshot(await deadline(bridge.apiPost('admin/read', {scope:'page_locale'})));}
    catch { /* The current verified preference or Host fallback remains usable. */ }
  }
  async function refresh() {
    if (closed || !ready || state.busy) return;
    const version = ++sequence; state.busy = true; state.stale = state.catalog !== null; show(state.stale ? 'shell.refreshStale' : 'shell.catalogLoading');
    void readLanguage();
    const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed();
    try {const data = validateCatalog(await deadline(bridge.apiGet('catalog', {}))); if (closed || version !== sequence) return; reconcileAssets(data); state.catalog = data; state.busy = false; state.stale = false; if (!state.cleanupPending) show('shell.catalogReady'); pickRoute(); await syncPage();}
    catch {if (closed || version !== sequence) return; state.busy = false; state.stale = state.catalog !== null; show('shell.catalogFailed'); const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed();}
  }
  function accept(context: Record<string,unknown>, event = false) {
    if (closed || projectionRevoked || !event && contextSeen) return;
    const revokeProjection = () => {projectionRevoked=true; sequence++; ready=false; state.busy=false; state.stale=state.catalog!==null; disposePage(); assets.invalidateAll(); cleanupState(); if (!state.cleanupPending) show('shell.assetsRevoked', true); changed();};
    let next: string; try {next = contextBoundary(context); if (!Object.hasOwn(context,'pluginName') || !Object.hasOwn(context,'pageName') || context.pluginName !== expectedPlugin || context.pageName !== expectedPage) throw Error('wrong_page');} catch {contextSeen=true; revokeProjection(); return;}
    if (contextSeen && (!ready || next !== boundary)) {revokeProjection(); return;}
    state.theme = typeof context.isDark === 'boolean' ? context.isDark ? 'dark' : 'light' : context.theme === 'dark' ? 'dark' : 'light';
    hostLocale = context.locale; applyLocale();
    if (contextSeen) {const old = selected(); if (old && mounted) mounted.update(pageContext(old.module)); changed(); return;}
    contextSeen = true; boundary = next; ready = true; void refresh();
  }
  const hashChanged = () => {pickRoute(); void syncPage(); const main = container.ownerDocument.getElementById('page-root'); main?.focus({preventScroll:true}); window.scrollTo({top:0,left:0,behavior:'instant'});};
  return {state, selected, refresh,applyLanguageSnapshot,canExecuteModule,navigationImpact,refuseNavigation(){show('shell.navigationBlocked');changed();}, selectModules(){window.location.hash='#/modules';pickRoute();void syncPage();}, selectHome() {window.location.hash='#/'; pickRoute(); void syncPage();}, selectModule(owner:string,section:'home'|'queries'='home'){window.location.hash='#/module/'+encodeURIComponent(owner)+(section==='queries'?'/-/queries':'');pickRoute();void syncPage();}, selectSettings(owner?:string) {const config=state.catalog?.modules?.find(m=>m.module_id===owner)?.pages.find(p=>p.role==='config');window.location.hash=owner?(config?routeHash(owner,config.route_id):'#/module/'+encodeURIComponent(owner)):'#/settings'; pickRoute(); void syncPage();}, select(owner: string, route: string) {window.location.hash = routeHash(owner,route); pickRoute(); void syncPage();},
    retryCleanup() {if (!state.cleanupPending) return; if (retiring) disposePage(); assets.retryCleanup(); cleanupState(); if (!state.cleanupPending) {if (projectionRevoked || revokedOwners.has(state.selected?.owner || '')) show(revokedMessage(), true); else show('shell.cleanupDone'); if (!closed) void syncPage();} changed();},
    start() {if (closed) return; window.addEventListener('hashchange',hashChanged); if (!bridge?.ready || !bridge?.onContext || !bridge?.apiGet || !bridge?.apiPost) {show('shell.bridgeMissing'); changed(); return;} off = bridge.onContext(context => accept(context,true)); deadline(Promise.resolve(bridge.ready())).then(context => accept(context)).catch(() => {if (!ready && !closed) {show('shell.bridgeFailed'); changed();}});},
    dispose() {if (closed) return; closed = true; sequence++; ready = false; off?.(); window.removeEventListener('hashchange',hashChanged); for (const timer of timers) clearTimeout(timer); timers.clear(); disposePage(); assets.invalidateAll(); cleanupState();},
  };
}
