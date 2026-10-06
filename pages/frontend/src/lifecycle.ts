import type {ResourceScope} from './contracts';
export function createScope(current: () => boolean): ResourceScope & {dispose(): void} {
  const controller = new AbortController();
  const callbacks = new Set<() => void>();
  let revoked = false;
  return Object.freeze({signal: controller.signal, isCurrent: () => !revoked && current(),
    onDispose(fn: () => void) {if (revoked) {try {fn();} catch (error) {callbacks.add(fn); throw error;} return () => {callbacks.delete(fn);};} callbacks.add(fn); return () => {callbacks.delete(fn);};},
    dispose() {if (revoked && !callbacks.size) return; revoked = true; controller.abort(); const errors: unknown[] = []; for (const fn of callbacks) {try {fn(); callbacks.delete(fn);} catch (error) {errors.push(error);}} if (errors.length) throw new AggregateError(errors, 'cleanup_pending');},
  });
}
const presentation = new Set(['isDark', 'theme', 'locale', 'i18n', 'displayName', 'pageTitle']);
// Unknown context fields fence the mount; presentation fields never reset it.
export function contextBoundary(input: Record<string, unknown>): string {
  const parents = new Set<unknown>();
  const stable = (v: unknown): unknown => {
    if (v === null || typeof v === 'string' || typeof v === 'boolean') return v;
    if (typeof v === 'number' && Number.isFinite(v) && !Object.is(v, -0)) return v;
    if (!v || typeof v !== 'object' || parents.has(v)) throw Error('invalid_context');
    if (Array.isArray(v)) {if (Object.keys(v).length !== v.length || Object.keys(v).some((k, i) => k !== String(i))) throw Error('invalid_context');}
    else if (![Object.prototype, null].includes(Object.getPrototypeOf(v))) throw Error('invalid_context');
    parents.add(v); const result = Array.isArray(v) ? v.map(stable) : Object.fromEntries(Object.keys(v).sort().map(k => [k, stable((v as Record<string, unknown>)[k])])); parents.delete(v); return result;
  };
  if (!input || typeof input !== 'object' || Array.isArray(input) || ![Object.prototype, null].includes(Object.getPrototypeOf(input))) throw Error('invalid_context');
  return JSON.stringify(stable(Object.fromEntries(Object.entries(input).filter(([k]) => !presentation.has(k)))));
}
export function mountIdentity(module: {module_id: string; module_epoch: number; runtime_id: string | null; asset_version: string}, page: {route_id: string; entry: string; styles: string[]; capability_id: string | null}, boundary: string) {
  return JSON.stringify([module.module_id, module.module_epoch, module.runtime_id, module.asset_version, page.route_id, page.entry, page.styles, page.capability_id, boundary]);
}
