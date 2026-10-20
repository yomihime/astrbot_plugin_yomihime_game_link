import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceModule,root} from '../shell/compile.mjs';
const {createSettingsController}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
let id=0;
function harness(){
 let revision=4,held=null,clearError=null;const calls=[],reads=[];
 const fields={reply_theme:{value:null,state:'valid',present:false,source:'default'}};
 const choices=[{value:'safe-theme',label:'Server theme'},{value:'another-theme',label:'Another server theme'}];
 const context={owner:'ff14/ff14',runtimeId:'test',epoch:1,boundary:'repair-'+(++id),available:true,theme:'light',locale:'zh-CN'};
 const services={config:{current:async()=>{reads.push('config');return {revision,fields:structuredClone(fields),descriptors:{reply_theme:{description:'Reply theme',default:null,value_schema:{type:['string','null']},choices}}};},save:async(r,v)=>{calls.push(['config',r,{...v}]);if(r!==revision)throw Error('revision_conflict');if(held)await held.promise;for(const [name,value] of Object.entries(v))fields[name]={value,state:'valid',present:value!==null,source:value===null?'default':'sqlite'};return {revision:++revision};}},credentials:{status:async()=>{reads.push('credentials');return {revision,fields:{}};},replace:async(r,f,p)=>{calls.push(['replace',r,f]);if(r!==revision)throw Error('revision_conflict');return {revision:++revision};},clear:async(r,f)=>{if(clearError)throw Object.assign(Error(clearError),{code:clearError});calls.push(['clear',r,f]);if(r!==revision)throw Error('revision_conflict');return {revision:++revision};}}};
 const controller=createSettingsController({routeId:'config',context,services,scope:{isCurrent:()=>true,onDispose(){}}},()=>{});
 const pair=f=>{controller.editPair(f,'client_id','id');controller.editPair(f,'client_secret','secret');};
 return {controller,calls,reads,pair,services,context,choices,hold(){let resolve;const promise=new Promise(r=>resolve=r);held={promise,resolve};return resolve;},clearError(value){clearError=value;},external(){revision++;}};
}
test('shared target revision supports ordinary, both realms and clear sequentially',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('ff14_default_region','global');await h.controller.save();
 h.pair('credential_fflogs_cn');await h.controller.replace('credential_fflogs_cn');h.pair('credential_fflogs_global');await h.controller.replace('credential_fflogs_global');
 await h.controller.clear('credential_fflogs_cn');h.controller.edit('ff14_default_region','cn');await h.controller.save();
 assert.equal(h.controller.state.ready,true);assert.deepEqual(h.calls.map(c=>c[1]),[4,5,6,7,8]);h.controller.dispose();
});
test('pending ordinary/theme edits survive acknowledgement and stay dirty',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('reply_theme','safe-theme');h.controller.edit('ff14_default_region','global');const release=h.hold();const pending=h.controller.save();
 h.controller.edit('reply_theme','');h.controller.edit('ff14_default_region','cn');release();await pending;
 assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.draft.ff14_default_region,'cn');assert.equal(h.controller.state.dirty,true);assert.equal(h.calls[0][2].reply_theme,'safe-theme');h.controller.dispose();
});
test('cancelled clear preserves other realm inputs and verified state',async()=>{
 const h=harness();await h.controller.load();h.pair('credential_fflogs_global');h.clearError('operation_cancelled');await h.controller.clear('credential_fflogs_cn');
 assert.equal(h.controller.state.ready,true);assert.equal(h.controller.state.error,false);assert.equal(h.controller.state.pairs.credential_fflogs_global.client_secret,'secret');assert.equal(h.calls.length,0);h.controller.dispose();
});
test('credential controller rejects oversized input before dispatch',async()=>{
 const h=harness();await h.controller.load();h.controller.editPair('credential_fflogs_cn','client_id','i');h.controller.editPair('credential_fflogs_cn','client_secret','s'.repeat(513));await h.controller.replace('credential_fflogs_cn');
 assert.equal(h.calls.length,0);assert.equal(h.controller.state.ready,true);h.controller.dispose();
});
test('unknown theme, external CAS, auth loss and lost write have distinct outcomes',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('reply_theme','unknown');await h.controller.save();assert.equal(h.calls.length,0);
 h.controller.edit('reply_theme','');h.external();await h.controller.save();assert.equal(h.controller.state.ready,false);assert.match(h.controller.state.message,/其他操作/);
 await h.controller.load();h.pair('credential_fflogs_global');h.clearError('admin_authorization_denied');await h.controller.clear('credential_fflogs_cn');assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.pairs.credential_fflogs_global.client_secret,'');
 await h.controller.load();h.pair('credential_fflogs_cn');h.services.credentials.replace=async()=>{throw Error('network_lost');};await h.controller.replace('credential_fflogs_cn');assert.equal(h.controller.state.ready,false);assert.match(h.controller.state.message,/操作未确认/);h.controller.dispose();
});
test('inconsistent concurrent snapshots refuse a falsely shared revision',async()=>{
 const h=harness();h.services.credentials.status=async()=>({revision:99,fields:{}});await h.controller.load();assert.equal(h.controller.state.ready,false);assert.equal(h.calls.length,0);h.controller.dispose();
});
test('new inheritance draft survives credential refresh and a late ordinary read',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('reply_theme','safe-theme');
 let resolveRead;h.services.config.current=()=>new Promise(r=>resolveRead=r);h.pair('credential_fflogs_cn');
 const mutation=h.controller.replace('credential_fflogs_cn');await Promise.resolve();await Promise.resolve();h.controller.edit('reply_theme','');
 resolveRead({revision:5,fields:{reply_theme:{value:'safe-theme',state:'valid',present:true,source:'sqlite'}},descriptors:{reply_theme:{description:'Theme',default:null,value_schema:{},choices:h.choices}}});await mutation;
 assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.dirty,true);assert.equal(h.controller.state.revision,5);h.controller.dispose();
});

test('ordinary ACK requires real dual snapshots; read failure blocks writes without fabricating credential revision',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('ff14_calendar_default_days','8');
 h.services.config.current=async()=>{h.reads.push('config');throw Error('network_lost_after_ACK');};
 await h.controller.save();assert.deepEqual(h.reads,['config','credentials','config','credentials']);
 assert.equal(h.calls.length,1);assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.baselineUnverified,true);
 assert.equal(h.controller.state.revision,4);assert.equal(h.controller.state.credentials.revision,4);
 assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');assert.equal(h.controller.state.error,true);
 await h.controller.save();assert.equal(h.calls.length,1);h.controller.dispose();
});
test('ordinary ACK rejects a stale matching pair and retains its minimum revision through manual refresh',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('ff14_calendar_default_days','8');
 const configCurrent=h.services.config.current,status=h.services.credentials.status;
 h.services.config.current=async()=>({...await configCurrent(),revision:4});h.services.credentials.status=async()=>({...await status(),revision:4});
 await h.controller.save();assert.equal(h.calls.length,1);assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.revision,4);
 assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');
 await h.controller.load();assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.revision,4);
 h.services.config.current=configCurrent;h.services.credentials.status=status;await h.controller.load();
 assert.equal(h.controller.state.revision,5);assert.equal(h.controller.state.credentials.revision,5);assert.equal(h.controller.state.draft.ff14_calendar_default_days,'8');
 assert.equal(h.controller.state.dirty,false);assert.equal(h.calls.length,1);h.controller.dispose();
});
test('ordinary ACK refresh clears submitted edits while preserving newer inheritance and default intent',async()=>{
 const h=harness();await h.controller.load();h.controller.edit('reply_theme','safe-theme');h.controller.edit('ff14_calendar_default_days','8');
 const current=h.services.config.current;let release;h.services.config.current=()=>new Promise(resolve=>release=async()=>resolve(await current()));
 const pending=h.controller.save();for(let i=0;i<20&&!release;i++)await Promise.resolve();assert.equal(typeof release,'function');
 h.controller.edit('reply_theme','');h.controller.edit('ff14_calendar_default_days','7');await release();await pending;
 assert.equal(h.controller.state.revision,5);assert.equal(h.controller.state.credentials.revision,5);assert.equal(h.controller.state.draft.ff14_default_region,'cn');
 assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.draft.ff14_calendar_default_days,'7');assert.equal(h.controller.state.dirty,true);
 assert.match(h.controller.state.message,/仍有更新的未保存修改/);h.services.config.current=current;await h.controller.save();
 assert.equal(h.calls[1][2].reply_theme,null);assert.equal(h.calls[1][2].ff14_calendar_default_days,7);assert.equal(h.controller.state.dirty,false);h.controller.dispose();
});
