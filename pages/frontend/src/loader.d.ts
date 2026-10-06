declare module '*module-loader.js' {import type {PageModule} from './contracts'; export function loadPage(entry: string): Promise<PageModule>;}
