import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
const host=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=host.window[name];
const {createManagementPage}=await import('./source-management.mjs');
export const digest='a'.repeat(64),context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'synthetic'};
export const keyStatus=(extra={})=>({state:'ready',reason_code:'ready',ready:true,source:'managed',generation:1,revision:2,inventory_digest:digest,unknown_count:0,impact:[{module_id:'example/demo',config_count:2,grant_count:1}],storage_security:'private',environment_present:false,environment_matches:false,recovery_action:null,...extra});
export const plan={operation_id:'synthetic-operation',source_identity:'b'.repeat(64),generation:1,revision:2,inventory_digest:digest,snapshot_digest:digest,files_digest:digest,file_count:3,source_retained:true,requires_stopped_host:true};
export const flush=async()=>{for(let i=0;i<80;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
export async function harness(initial=keyStatus(),handler=()=>undefined){
 const dom=new JSDOM('<div id="settings"></div>',{pretendToBeVisual:true}),calls=[];let status=initial,callback;
 const bridge={ready:()=>Promise.resolve(context),onContext(fn){callback=fn;return()=>{callback=null;};},async apiPost(endpoint,body){calls.push({endpoint,body});const result=await handler(endpoint,body);if(result!==undefined)return result;
  if(endpoint==='admin/catalog')return {schema_version:1,fields:[]};if(endpoint==='admin/read')return {};
  if(endpoint==='admin/master-key-status')return status;
  if(endpoint==='admin/master-key-migration-plan')return plan;
  if(endpoint==='admin/master-key-migration-execute')throw Error('requires_stopped_host');
  if(endpoint==='admin/legacy-config-plan')return {policy_id:body.policy_id,status:'migration_pending',source_revision:3,target_revision:4,migration_pending:true,written:false};
  if(endpoint==='admin/legacy-config-execute')return {policy_id:body.policy_id,status:'copied',target_revision:5,migration_pending:false,written:true};
  if(endpoint.startsWith('admin/master-key-'))return status=keyStatus({revision:status.revision+1,generation:endpoint.endsWith('reset')?status.generation+1:status.generation,environment_present:status.environment_present,environment_matches:status.environment_present});
  throw Error('unexpected endpoint');
 }};
 const page=createManagementPage(dom.window.document,bridge,dom.window,{container:dom.window.document.getElementById('settings')});page.start();await flush();
 return {dom,page,calls,doc:dom.window.document,emit:next=>callback?.(next),writes:action=>calls.filter(call=>call.endpoint==='admin/'+action),async click(selector){const node=dom.window.document.querySelector(selector);assert.ok(node,selector);node.click();await flush();},close(){page.dispose();dom.window.close();}};
}
