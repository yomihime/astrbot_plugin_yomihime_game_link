import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceApp} from './source-build.mjs';
import {catalog} from './fixtures.mjs';
export const flush=async()=>{for(let i=0;i<100;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,5));};
export async function harness(customData=null){
 const dom=new JSDOM('<html data-page-name="00-game-link"><body><div id="app"></div></body></html>',{pretendToBeVisual:true,url:'http://localhost/#/'}),window=dom.window,calls=[];
 for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?window:name==='document'?window.document:window[name];
 globalThis.getComputedStyle=window.getComputedStyle.bind(window);globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
 window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 const data=customData||catalog();const base=data.modules[0].pages[0];data.modules[0].pages=[{...base,route_id:'home',role:'home',styles:[],capability_id:null},{...base,route_id:'config',role:'config',styles:[],capability_id:null},{...base,role:'aux',styles:[]}];data.legacy_config_policies=[{policy_id:'example.copy',target_owner:'ff14/ff14'}];
 let coreValue='baseline',coreRevision=1;
 const field={module_id:'game_link/core',name:'sample',description:'Core sample',group:'general',value_schema:{type:'string'},default:'baseline',required:false,readable:true,editable:true,blocked_reason:null};
 const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'source-fixture'};
 window.AstrBotPluginView={ready:()=>Promise.resolve(context),onContext(fn){fn(context);return()=>{};},apiGet:()=>Promise.resolve(data),async apiPost(action,body){calls.push({action,body});
  if(action==='admin/module-page-session')return {page_session:'proof-'+calls.length,expires_at:Date.now()/1000+30};if(action==='admin/module-page-session-close')return {closed:true};
  if(action==='admin/catalog')return {schema_version:1,fields:[field,{...field,module_id:'ff14/ff14',name:'not_global'}]};
  if(action==='admin/read')return {'game_link/core':{revision:coreRevision,fields:{sample:{value:coreValue,state:'valid',present:true,source:'sqlite'}}},'ff14/ff14':{revision:1,fields:{not_global:{value:'module-only',state:'valid',present:true,source:'sqlite'}}}};
  if(action==='admin/update'){coreValue=body.updates[0].value;return {module_id:'game_link/core',revision:++coreRevision};}
  if(action==='admin/master-key-status')return {state:'ready',reason_code:'ready',ready:true,source:'managed',generation:1,revision:1,inventory_digest:'a'.repeat(64),unknown_count:0,impact:[],storage_security:'private',environment_present:false,environment_matches:false,recovery_action:null};
  if(action==='admin/modules')return {registry_revision:1,modules:[]};throw Error('unexpected action');
 }};
 globalThis.__loadFixturePage=()=>Promise.resolve({mount(container,options){const input=window.document.createElement('input');input.dataset.moduleRole=options.routeId;container.append(input);return {update(){},dispose(){container.replaceChildren();}};}});
 await sourceApp();await flush();
 return {window,doc:window.document,calls,async go(hash){window.location.hash=hash;await flush();},close(){window.dispatchEvent(new window.Event('pagehide'));dom.window.close();delete globalThis.__loadFixturePage;}};
}
