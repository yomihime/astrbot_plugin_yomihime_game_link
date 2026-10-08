// Deterministic offline shell contracts, never live Host acceptance.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule, root} from './compile.mjs';
import {catalog, flush} from './fixtures.mjs';
const metadata=await sourceModule(root+'pages/frontend/src/catalog.ts');
const {createShell}=await sourceModule(root+'pages/frontend/src/controller.ts');
test('module display locale fallback, compact name, collision and old IDs stay separate',()=>{
 const data=catalog();data.modules[0].display={default_name:'Default',localized_names:{zh:'名称','en-US':'US name',en:'English'},short_name:'N'};
 const module=metadata.validateCatalog(data).modules[0];
 assert.equal(metadata.moduleLabel(module,'EN-us'),'US name');assert.equal(metadata.moduleLabel(module,'en-GB'),'English');assert.equal(metadata.moduleLabel(module,'fr-FR'),'Default');assert.equal(metadata.moduleLabel(module,'zh-CN',true),'N');
 assert.equal(metadata.moduleLabel({...module,display:null},'zh-CN'),module.module_id);
 const duplicate={...module,module_id:'other/demo'};const labels=metadata.moduleLabels([module,duplicate],'zh-CN');assert.match(labels.get(module.module_id),/ff14\/ff14/);assert.match(labels.get(duplicate.module_id),/other\/demo/);
 assert.equal(module.module_id,'ff14/ff14');assert.equal(module.route,'ff14');
 for(const display of [{default_name:'',localized_names:{},short_name:null},{default_name:'X',localized_names:{en:'A',EN:'B'},short_name:null},{default_name:'X',localized_names:{},short_name:null,html:'bad'}]){data.modules[0].display=display;assert.throws(()=>metadata.validateCatalog(data));}
});
test('cold default home and fixed global settings never mount a query',async()=>{
 for(const modules of [catalog().modules,[]]){
  const dom=new JSDOM('<div id="page-root"><div id="container"></div></div>',{url:'http://localhost/#/'});dom.window.scrollTo=()=>{};let loads=0,posts=0;
  const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',session:'one'};
  const bridge={ready:()=>Promise.resolve(context),onContext:()=>()=>{},apiGet:()=>Promise.resolve({...catalog(),modules}),apiPost:()=>{posts++;return Promise.resolve({});}};
  const shell=createShell({bridge,container:dom.window.document.getElementById('container'),window:dom.window,loadPage:async()=>{loads++;throw Error();},changed(){}});shell.start();await flush();
  assert.equal(shell.state.home,true);assert.equal(shell.state.selected,null);assert.equal(loads,0);assert.equal(posts,0);
  shell.selectSettings();await flush();assert.equal(shell.state.settings,true);assert.equal(shell.state.settingsOwner,null);assert.equal(shell.state.home,false);assert.equal(loads,0);shell.dispose();dom.window.close();
 }
});
test('each public app disables preflight and shell explicitly owns font geometry',async()=>{
 for(const file of ['main.ts','management-view.ts','module-management.ts'])assert.match(await readFile(root+'pages/frontend/src/'+file,'utf8'),/preflightStyleDisabled\s*:\s*true/);
 assert.match(await readFile(root+'modules/ff14/pages/src/entry.ts','utf8'),/preflightStyleDisabled\s*:\s*true/);
 const css=await readFile(root+'pages/frontend/src/shell.css','utf8');assert.match(css,/\.game-shell\s*\{[^}]*font\s*:/);
});
