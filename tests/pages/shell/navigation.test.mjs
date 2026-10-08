// Actual built shell DOM, with an isolated catalog/management bridge fixture.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,copyFile,writeFile,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog as moduleCatalog} from './fixtures.mjs';
import {root} from './compile.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';

test('built mobile navigation retains settings owner and exposes pages or pageless module settings',async()=>{
 const dom=new JSDOM('<html data-page-name="shell"><body><template id="module-style-assets"></template><div id="app"></div></body></html>',{pretendToBeVisual:true,url:'http://localhost/#/settings/module/ff14%2Fff14'}),window=dom.window;
 for(const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])globalThis[key]=key==='window'?window:key==='document'?window.document:window[key];
 globalThis.getComputedStyle=window.getComputedStyle.bind(window);globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
 window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});window.scrollTo=()=>{};window.confirm=()=>true;
 const data=moduleCatalog();data.modules.push({...data.modules[0],module_id:'example/demo',route:'demo',category:'platform',pages:[],resources:[]});
 const binding={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',binding:1},calls=[];
 window.AstrBotPluginPage={ready:()=>Promise.resolve(binding),onContext(fn){fn(binding);return()=>{};},apiGet:()=>Promise.resolve(data),apiPost(endpoint,body){calls.push({endpoint,body});if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());if(endpoint==='admin/credential-readiness')return Promise.resolve(null);if(endpoint==='admin/modules')return Promise.resolve({registry_revision:1,modules:[]});return Promise.reject(Error('unexpected_write'));}};
 const directory=resolve(root,'.architecture-refactor/management-home-settings-ux/tests/backend/shell-navigation-'+process.pid);await mkdir(directory,{recursive:true});for(const name of ['app.js','runtime.js'])await copyFile(resolve(root,'pages/shell',name),resolve(directory,name));
 // The loader exists only in release-builder projection. This navigation test
 // injects asset failure, while production.test.mjs covers literal module loads.
 await writeFile(resolve(directory,'module-loader.js'),"export async function loadPage(){throw Error('fixture_asset_unavailable');}\n");
 const flush=async()=>{for(let i=0;i<100;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,5));};
 try{await import(pathToFileURL(resolve(directory,'app.js')));await flush();
  const module=window.document.getElementById('module-select-mobile'),mobile=window.document.querySelector('.shell-mobile');
  assert.equal(module.value,'ff14/ff14');assert.ok([...mobile.querySelectorAll('a')].some(link=>link.textContent==='全局设置'));
  assert.equal(window.document.querySelector('.shell-settings[aria-current="page"]'),null);assert.equal(window.document.querySelectorAll('nav[aria-label="模块设置"] a[aria-current="page"]').length,1);
  // Module switches stay in settings, including owners with no query pages.
  module.value='example/demo';module.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(module.value,'example/demo');assert.equal(window.location.hash,'#/settings/module/example%2Fdemo');assert.equal(window.document.getElementById('page-title').textContent,'设置');assert.ok([...window.document.querySelectorAll('.shell-heading .shell-eyebrow')].some(node=>node.textContent.includes('example/demo')));
  module.value='ff14/ff14';module.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(module.value,'ff14/ff14');
  const append=window.document.head.append.bind(window.document.head);window.document.head.append=(...nodes)=>{append(...nodes);for(const node of nodes)if(node.matches?.('link[data-module-style]'))queueMicrotask(()=>node.dispatchEvent(new window.Event('load')));};
  const template=window.document.getElementById('module-style-assets');for(const path of data.modules[0].pages[0].styles){const link=window.document.createElement('link');link.rel='stylesheet';link.dataset.resource=path;link.href='./'+path;template.content.append(link);}
  const query=[...mobile.querySelectorAll('details a')].find(link=>link.getAttribute('href')==='#/module/ff14%2Fff14/market');assert.ok(query,'module queries remain secondary reachable links');query.click();await flush();assert.equal(window.location.hash,'#/module/ff14%2Fff14/market');assert.equal(module.value,'ff14/ff14');assert.equal(calls.filter(call=>call.endpoint==='admin/credential-update').length,0);
  const reopen=window.document.querySelector('#module-management-container [data-reopen-guidance]');assert.ok(reopen,'sandboxed management must provide host menu instructions');assert.match(reopen.textContent,/宿主左侧.*插件.*astrbot_plugin_yomihime_game_link.*monitor/);assert.equal(window.document.querySelector('#module-management-container a[target="_top"]'),null);assert.equal(window.document.querySelector('#module-management-container a[href="/#/extension/plugins"]'),null);
 }finally{window.dispatchEvent(new window.Event('pagehide'));dom.window.close();}
});

test('retired FF14 locator gives manual host-menu instructions without forbidden top navigation',async()=>{
 // The supported Host iframe has allow-scripts/forms/downloads but no
 // allow-top-navigation or allow-top-navigation-by-user-activation.
 const sandbox='allow-scripts allow-forms allow-downloads';
 assert.equal(sandbox.split(' ').some(token=>token.startsWith('allow-top-navigation')),false);
 for(const path of ['modules/ff14/pages/compat/index.html','pages/ff14/index.html']){
  const dom=new JSDOM(await readFile(resolve(root,path),'utf8'));
  try{const guidance=dom.window.document.querySelector('[data-reopen-guidance]');assert.ok(guidance,path);assert.match(guidance.textContent,/宿主左侧.*插件.*astrbot_plugin_yomihime_game_link.*monitor/);assert.equal(dom.window.document.querySelector('a'),null);assert.equal(dom.window.document.querySelector('script').getAttribute('src'),'./app.js');}finally{dom.window.close();}
 }
});
