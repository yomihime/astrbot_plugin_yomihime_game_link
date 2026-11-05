declare module '*module-loader.js' {
  import type {PageModule} from './contracts';
  export function loadPage(entry: string): Promise<PageModule>;
  export function loadPresentation(entry: string): Promise<unknown>;
  export const sharedRuntimeUrl: string;
}
