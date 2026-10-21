import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceModule,root} from '../shell/compile.mjs';
const {createSettingsController}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
const {createQueryController}=await sourceModule(root+'modules/ff14/pages/src/query-controller.ts');
const context={owner:'ff14/ff14',runtimeId:'test',epoch:1,boundary:'settings',available:true,theme:'light',locale:'zh-CN'};
function harness(overrides={},ctx=context){
 const calls=[],callbacks=[];let active=true,revision=4;
 const services={config:{current:async()=>({revision,fields:{}}),save:async(r,v)=>{calls.push(['save',r,v]);return {revision:++revision};}},credentials:{status:async()=>({revision,fields:{}}),replace:async(r,f,p)=>{calls.push(['replace',r,f,p]);return {revision:++revision};},clear:async(r,f)=>{calls.push(['clear',r,f]);return {revision:++revision};}},...overrides};
 const controller=createSettingsController({routeId:'config',context:ctx,services,scope:{isCurrent:()=>active,onDispose:f=>callbacks.push(f)}},()=>{});
 return {controller,calls,services,dispose(){active=false;callbacks.forEach(f=>f());}};
}
test('ordinary save uses module fields and CAS; duplicate save is blocked',async()=>{
 let resolve,revision=4,fields={};const h=harness({config:{current:async()=>({revision,fields}),save:(...args)=>{h.calls.push(args);return new Promise(done=>resolve=receipt=>{revision=receipt.revision;fields=Object.fromEntries(Object.entries(args[1]).map(([name,value])=>[name,{value,state:'valid',present:true,source:'sqlite'}]));done(receipt);});}},credentials:{status:async()=>({revision,fields:{}})}});
 await h.controller.load();h.controller.edit('ff14_default_region','global');
 const pending=h.controller.save();await h.controller.save();assert.equal(h.calls.length,1);assert.equal(h.calls[0][0],4);
 resolve({revision:5});await pending;assert.equal(h.controller.state.revision,5);assert.equal(h.controller.state.dirty,false);h.dispose();
});
test('credential replace is complete pair and secrets clear after save/dispose',async()=>{
 const h=harness();await h.controller.load();await h.controller.replace('credential_fflogs_cn');assert.equal(h.calls.length,0);
 h.controller.editPair('credential_fflogs_cn','client_id','synthetic-id');h.controller.editPair('credential_fflogs_cn','client_secret','synthetic-secret');
 await h.controller.replace('credential_fflogs_cn');assert.equal(h.calls[0][1],4);assert.equal(h.controller.state.pairs.credential_fflogs_cn.client_secret,'');h.dispose();
});
test('cancel and auth invalidation reject late responses and scrub secrets',async()=>{
 let resolve;const h=harness({credentials:{status:async()=>({revision:4,fields:{}}),replace:()=>new Promise(r=>resolve=r)}});
 await h.controller.load();h.controller.editPair('credential_fflogs_cn','client_id','id');h.controller.editPair('credential_fflogs_cn','client_secret','secret');
 const pending=h.controller.replace('credential_fflogs_cn');h.controller.cancel();resolve({revision:9});await pending;
 assert.equal(h.controller.state.credentialRevision,4);assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.pairs.credential_fflogs_cn.client_secret,'');
 h.controller.update({...context,available:false});assert.deepEqual(h.controller.state.draft,{});h.dispose();
});
test('owner/runtime/epoch/boundary isolate drafts; reload keeps only ordinary drafts',async()=>{
 const unique={...context,boundary:'draft-isolation'};const h=harness({},unique);await h.controller.load();h.controller.edit('ff14_default_region','global');h.dispose();
 const same=harness({},unique);await same.controller.load();assert.equal(same.controller.state.draft.ff14_default_region,'global');same.dispose();
 for(const altered of [{owner:'other/module'},{epoch:2},{runtimeId:'new'},{boundary:'new'}]){const other=harness({},{...unique,...altered});await other.controller.load();assert.equal(other.controller.state.draft.ff14_default_region,'cn');other.dispose();}
});
test('query controller revokes previous owner view and cannot save drafts into it',async()=>{
 const cleanups=[];let calls=0;
 const options={routeId:'items',context:{...context,boundary:'query-isolation'},scope:{isCurrent:()=>true,onDispose:f=>cleanups.push(f)},services:{invoke:async()=>{calls++;}}};
 const controller=createQueryController(options,()=>{});controller.edit('query','old owner input');controller.update({...options.context,owner:'other/demo'});
 assert.equal(controller.state.draft.query,'');controller.edit('query','new owner input');await controller.submit();assert.equal(calls,0);controller.dispose();
 const restored=createQueryController(options,()=>{});assert.equal(restored.state.draft.query,'old owner input');restored.dispose();
});
test('invalid stored fields require explicit repair and never silently become a default',async()=>{
 const h=harness({config:{current:async()=>({revision:4,fields:{ff14_default_region:{state:'invalid',value:null}}}),save:async()=>{h.calls.push('save');return {revision:5};}}},{...context,boundary:'invalid'});
 await h.controller.load();assert.equal(h.controller.state.draft.ff14_default_region,'');await h.controller.save();assert.equal(h.calls.length,0);assert.equal(h.controller.state.error,true);h.dispose();
});
test('late credential status cannot repopulate an invalidated page',async()=>{
 let statuses=0,resolveStatus;const h=harness({credentials:{status:()=>++statuses===1?Promise.resolve({revision:4,fields:{}}):new Promise(r=>resolveStatus=r),replace:async()=>({revision:5})}});
 await h.controller.load();h.controller.editPair('credential_fflogs_cn','client_id','id');h.controller.editPair('credential_fflogs_cn','client_secret','secret');
 const pending=h.controller.replace('credential_fflogs_cn');await Promise.resolve();await Promise.resolve();h.controller.update({...context,available:false});resolveStatus({revision:99,fields:{late:{configured:true}}});await pending;
 assert.equal(h.controller.state.credentials.revision,4);assert.equal(h.controller.state.pairs.credential_fflogs_cn.client_secret,'');h.dispose();
});
test('navigation summary is bounded, value-free and changes with ordinary, secret and pending state',async()=>{
 const h=harness({},{...context,boundary:'navigation-summary'});await h.controller.load();const clean=h.controller.navigationImpact();
 assert.deepEqual(Object.keys(clean).sort(),['credentials','ordinary','pending','version']);assert.equal(clean.ordinary,false);assert.equal(clean.credentials,false);assert.equal(clean.pending,false);assert.ok(Number.isSafeInteger(clean.version));
 h.controller.editPair('credential_fflogs_cn','client_secret','SYNTHETIC SECRET');h.controller.edit('ff14_default_region','global');const edited=h.controller.navigationImpact();assert.equal(edited.ordinary,true);assert.equal(edited.credentials,true);assert.ok(edited.version>clean.version);assert.doesNotMatch(JSON.stringify(edited),/SYNTHETIC|ff14|owner/);
 let resolve;h.services.config.save=()=>new Promise(done=>resolve=done);const write=h.controller.save();assert.equal(h.controller.navigationImpact().pending,true);h.controller.cancel();resolve({revision:5});await write;
 assert.equal(h.controller.navigationImpact().pending,false);assert.equal(h.controller.navigationImpact().credentials,false);h.dispose();
});
