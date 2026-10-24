// Geometry contracts complement real-browser light/dark and narrow-screen QA.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const css=await readFile(new URL('../../../pages/frontend/src/shell.css',import.meta.url),'utf8');
test('home query actions stay in one accessible scrolling row without redundant labels',async()=>{
 assert.match(css,/\.workspace-page-index\{display:flex;flex-wrap:nowrap;gap:8px/);
 assert.match(css,/\.workspace-page-index>a\{[^}]*flex:1 0 auto;min-width:64px/);
 assert.match(css,/\.workspace-page-index>a\{[^}]*min-height:40px;padding:8px 12px/);
 assert.match(css,/\.workspace-page-index\{[^}]*overflow-x:auto;padding:6px;scroll-padding-inline:6px/);
 assert.match(css,/\.workspace-page-copy strong\{[^}]*font-size:14px/);
 const source=await readFile(new URL('../../../pages/frontend/src/main.ts',import.meta.url),'utf8');
 assert.doesNotMatch(source,/workspace-page-number/);
 assert.match(source,/class:'workspace-page-copy'\},\[h\('strong',page.title\)\]/);
 assert.match(source,/aria-label':label\(module.module_id\)\+'功能'/);
 assert.match(css,/\.workspace-emblem\{[^}]*width:60px;height:60px/);
});
test('management surface follows shared theme tokens without shrinking action targets',()=>{
 assert.match(css,/\.module-management\{background:var\(--ui-panel\);border:1px solid var\(--ui-border\)/);
 assert.match(css,/\.module-management-actions\{display:flex;flex-wrap:wrap;gap:8px/);
 assert.match(css,/\.module-management-actions \.n-button\{min-height:40px\}/);
 assert.match(css,/\.module-management-info p\{[^}]*font-size:14px/);
 assert.match(css,/\.module-management-row\{[^}]*padding:10px 0/);
 assert.match(css,/\.module-management-identity,\.module-management-metadata\{display:flex/);
 assert.match(css,/\.module-management-warning\{color:var\(--ui-warning\)\}/);
 assert.match(css,/\.technical-details summary\{[^}]*min-height:40px/);
});

test('global settings bound control widths, separate maintenance, and preserve visible warnings',async()=>{
 const view=await readFile(new URL('../../../pages/frontend/src/management-view.ts',import.meta.url),'utf8');
 assert.match(view,/import.*UiProvider.*from '@game-link\/ui'/);
 assert.match(view,/class:'global-font-panel'/);
 assert.match(view,/card.target==='game_link\/core'\?'放弃全局修改':'放弃本模块修改'/);
 assert.match(css,/\.management-global \.ui-settings-row[^}]*minmax\(0,420px\)/);
 assert.match(css,/\.management-global input\[type=file\]::file-selector-button/);
 assert.match(css,/\.management-global \.master-key-panel>\.ui-status-hint\[data-tone=warning\]\{border-left-color:var\(--ui-warning\)\}/);
 assert.match(css,/@media\(min-width:1200px\)\{\.management-global\{display:grid;grid-template-columns:minmax\(0,1.1fr\) minmax\(0,1fr\)/);
 assert.doesNotMatch(css,/\.management-global[^}]*display:none/);
});
