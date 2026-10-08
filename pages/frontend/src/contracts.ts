export interface PageDescriptor {route_id: string; title: string; order: number; access: 'public_web'; capability_id: string | null; entry: string; styles: string[]}
export interface ModuleDescriptor {module_id: string; route: string; state: string; module_epoch: number; runtime_id: string | null; asset_version: string; pages: PageDescriptor[]; resources: {path: string; sha256: string}[]}
export interface Catalog {schema_version: number; catalog_revision: number | null; runtime: {state: string}; modules: ModuleDescriptor[] | null}
export interface PageContext {readonly theme: 'light' | 'dark'; readonly locale: string; readonly available: boolean; readonly owner: string; readonly runtimeId: string; readonly epoch: number; readonly boundary: string}
export interface ResourceScope {readonly signal: AbortSignal; isCurrent(): boolean; onDispose(callback: () => void): () => void}
export interface PageOptions {routeId: string; context: Readonly<PageContext>; services: {invoke(capabilityId: string, parameters: unknown): Promise<unknown>}; scope: ResourceScope}
export interface MountedPage {update(context: Readonly<PageContext>): void; dispose(): void}
export interface PageModule {mount(container: HTMLElement, options: PageOptions): MountedPage}
export interface Bridge {ready(): Promise<Record<string, unknown>>; onContext(callback: (context: Record<string, unknown>) => void): (() => void) | undefined; apiGet(path: string, body: unknown): Promise<unknown>; apiPost(path: string, body: unknown): Promise<unknown>}
