/** Module UI contract v1. Trusted, reviewed same-page code; not a JS sandbox. */
export type JsonValue = null | boolean | number | string | readonly JsonValue[] | {readonly [key: string]: JsonValue};
export type ModulePageRole = 'home' | 'config' | 'aux';
/** Metadata registered by the module's trusted manifest/capture, not at runtime. */
export interface ModulePageDefinition {readonly routeId: string; readonly title: string; readonly role: ModulePageRole; readonly order?: number; readonly capabilityId?: string | null}
export interface PageContext {readonly theme: 'light' | 'dark'; readonly locale: string; readonly available: boolean; readonly owner: string; readonly runtimeId: string; readonly epoch: number; readonly boundary: string}
export interface ResourceScope {readonly signal: AbortSignal; isCurrent(): boolean; onDispose(callback: () => void): () => void}
export interface ConfigField {readonly value: JsonValue; readonly state: 'valid' | 'invalid'; readonly present: boolean; readonly source: 'sqlite' | 'default' | 'legacy'; readonly migration_pending?: boolean}
export interface ConfigDescriptor {readonly description:string; readonly value_schema:JsonValue|null; readonly default:JsonValue; readonly choices?:readonly {readonly value:JsonValue;readonly label:string}[]}
export interface ConfigSnapshot {readonly revision: number; readonly fields: Readonly<Record<string, ConfigField>>; readonly descriptors?:Readonly<Record<string,ConfigDescriptor>>}
export interface CredentialState {readonly configured: boolean; readonly decryptable: boolean; readonly upstream: 'unverified'}
export interface CredentialSnapshot {readonly revision: number; readonly fields: Readonly<Record<string, CredentialState>>}
export interface MutationReceipt {readonly revision: number}
export type CredentialPair = {readonly client_id: string; readonly client_secret: string}
  | {readonly token: string; readonly subject?: string};
export interface ModulePageServices {
  readonly jobs: {
    submit(request:{handler_id:string;parameters:Readonly<Record<string,JsonValue>>;idempotency_key?:string|null}):Promise<unknown>;
    status(jobId:string):Promise<unknown>;
    result(jobId:string,options?:{cursor?:string|null;page_size?:number}):Promise<unknown>;
    cancel(jobId:string,expectedRevision:number):Promise<unknown>;
    retry(jobId:string,expectedRevision:number):Promise<unknown>;
    resource(jobId:string,assetId:string):Promise<Blob>;
  };
  invoke(capabilityId: string, parameters: unknown): Promise<unknown>;
  preview(capabilityId: string, parameters: unknown): Promise<unknown>;
  readonly config: {
    current(): Promise<ConfigSnapshot>;
    save(expectedRevision: number, values: Readonly<Record<string, JsonValue>>): Promise<MutationReceipt>;
    clear(expectedRevision: number, fields: readonly string[]): Promise<MutationReceipt>;
  };
  readonly credentials: {
    status(): Promise<CredentialSnapshot>;
    replace(expectedRevision: number, field: string, pair: CredentialPair): Promise<MutationReceipt>;
    /** The shell asks for an owner/page/revision-bound confirmation. */
    clear(expectedRevision: number, field: string): Promise<MutationReceipt>;
    authorize(expectedRevision: number, flowId: string, step: 'begin'|'poll'|'cancel'|'qr'|'revoke', sessionId?: string): Promise<unknown>;
  };
}
/** Bounded draft/pending summary only; the shell derives the owning module. */
export interface NavigationImpact {readonly ordinary:boolean; readonly credentials:boolean; readonly pending:boolean; readonly version:number}
export interface PageOptions {readonly routeId: string; readonly context: Readonly<PageContext>; readonly services: ModulePageServices; readonly scope: ResourceScope; readonly onNavigationStateChange?:()=>void}
export interface MountedPage {update(context: Readonly<PageContext>): void; dispose(): void; navigationImpact?():Readonly<NavigationImpact>}
export interface PageModule {mount(container: HTMLElement, options: PageOptions): MountedPage}
