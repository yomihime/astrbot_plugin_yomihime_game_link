// Routing/lifecycle contracts use offline descriptors, never a live Host.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule,root} from './compile.mjs';
import {catalog,flush} from './fixtures.mjs';
const {createShell}=await sourceModule(root+'pages/frontend/src/controller.ts');

test('cold module home, auxiliary landing and legacy direct routes remain distinct and lazy',async()=>{
 for(const [hash,section,settings,selected] of [
  ['#/module/ff14%2Fff14','home',false,null],
  ['#/module/ff14%2Fff14/-/queries','queries',false,null],
  ['#/settings/module/ff14%2Fff14',null,true,null],
  ['#/module/ff14%2Fff14/items',null,false,'items'],
 ]){
  const dom=new JSDOM('<main id="page-root"><div id="module"></div></main>',{url:'http://localhost/'+hash});dom.window.scrollTo=()=>{};const data=catalog();data.modules[0].pages.forEach(page=>page.styles=[]);let loads=0,invokes=0;
  const bridge={ready:()=>Promise.resolve({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',generation:1}),onContext(){return()=>{};},apiGet:()=>Promise.resolve(data),apiPost(){invokes++;throw Error('unexpected');}};
  const shell=createShell({bridge,container:dom.window.document.getElementById('module'),window:dom.window,loadPage:async()=>{loads++;return{mount:()=>({update(){},dispose(){}})};},changed(){}});
  try{shell.start();await flush();assert.equal(shell.state.moduleOwner,'ff14/ff14');assert.equal(shell.state.moduleSection,section);assert.equal(shell.state.settings,settings);assert.equal(shell.state.selected?.route||null,selected);assert.equal(loads,selected?1:0);assert.equal(invokes,0);
   shell.selectModule('ff14/ff14');await flush();assert.equal(dom.window.location.hash,'#/module/ff14%2Fff14');assert.equal(shell.state.selected,null);assert.equal(shell.state.moduleSection,'home');shell.selectModule('ff14/ff14','queries');await flush();assert.equal(shell.state.moduleSection,'queries');assert.equal(shell.state.selected,null);assert.equal(invokes,0);
  }finally{shell.dispose();dom.window.close();}
 }
});

test('detached owner and late query load cannot restore a module page; restored catalog needs formal reopen',async()=>{
 const dom=new JSDOM('<main id="page-root"><div id="module"></div></main>',{url:'http://localhost/#/module/ff14%2Fff14/items'});dom.window.scrollTo=()=>{};let data=catalog(),release,mounts=0;data.modules[0].pages.forEach(page=>page.styles=[]);
 const bridge={ready:()=>Promise.resolve({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',generation:1}),onContext(){return()=>{};},apiGet:()=>Promise.resolve(data),apiPost(){throw Error('unexpected');}};
 const shell=createShell({bridge,container:dom.window.document.getElementById('module'),window:dom.window,loadPage:()=>new Promise(resolve=>{release=resolve;}),changed(){}});
 try{shell.start();await flush();assert.equal(typeof release,'function');data={...data,catalog_revision:2,modules:[]};await shell.refresh();release({mount(){mounts++;return{update(){},dispose(){}};}});await flush();assert.equal(mounts,0);assert.equal(shell.state.mounted,false);assert.equal(shell.selected(),null);
  data=catalog('ff14/ff14',2,'runtime-2');data.modules[0].pages.forEach(page=>page.styles=[]);await shell.refresh();assert.equal(mounts,0);assert.equal(shell.state.mounted,false);assert.match(shell.state.message,/重新打开/);shell.selectModule('ff14/ff14');await flush();assert.equal(shell.state.moduleSection,'home');assert.equal(mounts,0);
 }finally{shell.dispose();dom.window.close();}
});
