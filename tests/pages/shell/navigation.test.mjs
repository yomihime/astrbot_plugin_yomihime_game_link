// Actual built shell DOM, with an isolated catalog/management bridge fixture.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,copyFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog as moduleCatalog} from './fixtures.mjs';
import {catalog,snapshot,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';

test('built mobile navigation retains settings owner and exposes pages or pageless module settings',async()=>{
 const dom=new JSDOM('<html data-page-name="shell"><body><template id="module-style-assets"></template><div id="app"></div></body></html>',{pretendToBeVisual:true,url:'http://localhost/#/settings/module/ff14%2Fff14'}),window=dom.window;
 for(const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent'])globalThis[key]=key==='window'?window:key==='document'?window.document:window[key];
 globalThis.getComputedStyle=window.getComputedStyle.bind(window);globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
 window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});window.scrollTo=()=>{};window.confirm=()=>true;
 const data=moduleCatalog();data.modules.push({...data.modules[0],module_id:'example/demo',route:'demo',category:'platform',pages:[],resources:[]});
 const binding={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',binding:1},calls=[];
 window.AstrBotPluginPage={ready:()=>Promise.resolve(binding),onContext(fn){fn(binding);return()=>{};},apiGet:()=>Promise.resolve(data),apiPost(endpoint,body){calls.push({endpoint,body});if(endpoint==='admin/catalog')return Promise.resolve(catalog());if(endpoint==='admin/read')return Promise.resolve(snapshot());if(endpoint==='admin/credential-catalog')return Promise.resolve(credentialCatalog());if(endpoint==='admin/credential-status')return Promise.resolve(credentialStatus());if(endpoint==='admin/credential-readiness')return Promise.resolve(null);if(endpoint==='admin/modules')return Promise.resolve({registry_revision:1,modules:[]});return Promise.reject(Error('unexpected_write'));}};
 const directory=resolve('.architecture-refactor/unified-settings/shell-navigation-'+process.pid);await mkdir(directory,{recursive:true});for(const name of ['app.js','runtime.js'])await copyFile(resolve('pages/shell',name),resolve(directory,name));
 // The loader exists only in release-builder projection. This navigation test
 // injects asset failure, while production.test.mjs covers literal module loads.
 await writeFile(resolve(directory,'module-loader.js'),"export async function loadPage(){throw Error('fixture_asset_unavailable');}\n");
 const flush=async()=>{for(let i=0;i<100;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,5));};
 try{await import(pathToFileURL(resolve(directory,'app.js')));await flush();
  const module=window.document.getElementById('module-select-mobile'),page=window.document.getElementById('page-select'),mobile=window.document.querySelector('.shell-mobile');
  assert.equal(module.value,'ff14/ff14');assert.equal(page.disabled,false);assert.deepEqual([...page.options].map(option=>option.value),['','items','market']);assert.ok([...mobile.querySelectorAll('button')].some(button=>button.textContent==='模块设置'));
  // Module switches inside settings keep the settings route, including owners with no pages.
  module.value='example/demo';module.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(module.value,'example/demo');assert.equal(window.location.hash,'#/settings/module/example%2Fdemo');assert.equal(page.disabled,true);assert.ok(window.document.getElementById('page-title').textContent.includes('example/demo'));const settings=[...mobile.querySelectorAll('button')].find(button=>button.textContent==='模块设置');assert.ok(settings);settings.click();await flush();assert.equal(window.location.hash,'#/settings/module/example%2Fdemo');
  module.value='ff14/ff14';module.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(page.disabled,false);assert.equal(module.value,'ff14/ff14');
  // Unknown fixture asset is allowed to fail loading; the selected page must still be exact.
  const append=window.document.head.append.bind(window.document.head);window.document.head.append=(...nodes)=>{append(...nodes);for(const node of nodes)if(node.matches?.('link[data-module-style]'))queueMicrotask(()=>node.dispatchEvent(new window.Event('load')));};
  const template=window.document.getElementById('module-style-assets');for(const path of data.modules[0].pages[0].styles){const link=window.document.createElement('link');link.rel='stylesheet';link.dataset.resource=path;link.href='./'+path;template.content.append(link);}
  page.value='market';page.dispatchEvent(new window.Event('change',{bubbles:true}));await flush();assert.equal(window.location.hash,'#/module/ff14%2Fff14/market');assert.equal(module.value,'ff14/ff14');assert.equal(page.value,'market');assert.equal(calls.filter(call=>call.endpoint==='admin/credential-update').length,0);
  const reopen=window.document.querySelector('#module-management-container a');assert.equal(reopen.getAttribute('href'),'/#/extension/plugins');assert.equal(reopen.target,'_top');
 }finally{window.dispatchEvent(new window.Event('pagehide'));dom.window.close();}
});
