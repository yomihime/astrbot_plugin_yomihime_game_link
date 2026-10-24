export interface PageDescriptor {route_id: string; title: string; role: 'home'|'config'|'aux'; order: number; access: 'public_web'; capability_id: string | null; entry: string; styles: string[]}
export interface ModuleDisplay {default_name: string; localized_names: Record<string,string>; short_name: string | null; logo_resource?: string | null; summary?: string | null}
export interface ModuleDescriptor {display?: ModuleDisplay | null;module_id: string; route: string; state: string; module_epoch: number; runtime_id: string | null; asset_version: string; pages: PageDescriptor[]; resources: {path: string; sha256: string}[]}
export interface LegacyConfigPolicy {policy_id:string;target_owner:string}
export interface Catalog {legacy_config_policies?:LegacyConfigPolicy[];schema_version: number; catalog_revision: number | null; runtime: {state: string}; modules: ModuleDescriptor[] | null}
export type {PageContext, ResourceScope, PageOptions, MountedPage, PageModule, NavigationImpact} from '../../public-contract';
export interface Bridge {upload?(path:string,file:File):Promise<unknown>; ready(): Promise<Record<string, unknown>>; onContext(callback: (context: Record<string, unknown>) => void): (() => void) | undefined; apiGet(path: string, body: unknown): Promise<unknown>; apiPost(path: string, body: unknown): Promise<unknown>}
