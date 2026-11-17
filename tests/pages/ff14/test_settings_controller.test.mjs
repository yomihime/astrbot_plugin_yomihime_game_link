import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {sourceModule,root} from '../shell/compile.mjs';
const {createSettingsController,directoryGenerationField}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
const {createQueryController}=await sourceModule(root+'modules/ff14/pages/src/query-controller.ts');
test('settings fields are explicitly granted by the module config page',async()=>{
 const {ordinaryFields}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
 const manifest=JSON.parse(await readFile(root+'modules/ff14/yomihime.manifest.json','utf8'));
 const module=manifest.modules.find(module=>module.module_id==='ff14');
 const page=module.pages.find(page=>page.route_id==='config');
 assert.deepEqual([...ordinaryFields.map(field=>field.name),directoryGenerationField].sort(),[...page.config_fields].sort());
 const sensitive=new Set(module.config_fields.filter(field=>field.sensitive).map(field=>field.name));
 assert.ok(page.config_fields.every(name=>!sensitive.has(name)));
});
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

test('news lists round-trip as arrays and reject invalid routes before mutation',async()=>{
 const h=harness();await h.controller.load();
 h.controller.edit('ff14_news_admins','platform-a:17, platform-b:17');
 h.controller.edit('ff14_news_targets','platform-a:GroupMessage:42, platform-b:FriendMessage:42');
 h.controller.edit('ff14_news_interval_seconds','3600');await h.controller.save();
 assert.deepEqual(h.calls[0][2].ff14_news_admins,['platform-a:17','platform-b:17']);
 assert.deepEqual(h.calls[0][2].ff14_news_targets,['platform-a:GroupMessage:42','platform-b:FriendMessage:42']);
 assert.equal(h.calls[0][2].ff14_news_interval_seconds,3600);
 assert.equal(Object.hasOwn(h.calls[0][2],directoryGenerationField),false);
 h.controller.edit('ff14_news_targets','platform-a:GroupMessage:../private');await h.controller.save();
 assert.equal(h.calls.length,1);assert.equal(h.controller.state.error,true);h.dispose();
});

function directoryHarness(boundary){
 let revision=4,generation='initial';
 const calls=[],credentialCalls=[];
 const config={
  current:async()=>({revision,fields:{[directoryGenerationField]:{state:'valid',value:generation,present:true,source:'sqlite'}}}),
  save:async(expected,patch)=>{
   calls.push([expected,patch]);
   if(Object.hasOwn(patch,directoryGenerationField))generation=patch[directoryGenerationField];
   return {revision:++revision};
  },
 };
 const credentialWrite=async(...args)=>{credentialCalls.push(args);return {revision:++revision};};
 const credentials={status:async()=>({revision,fields:{}}),replace:credentialWrite,clear:credentialWrite};
 return {...harness({config,credentials},{...context,boundary}),config,configCalls:calls,credentialCalls,
  changeGeneration(value){generation=value;revision++;}};
}

test('server-cache clear submits only a fresh UUID even without crypto.randomUUID and preserves drafts',async(t)=>{
 const getRandomValues=crypto.getRandomValues.bind(crypto);
 t.mock.getter(globalThis,'crypto',()=>({getRandomValues}));
 assert.equal(crypto.randomUUID,undefined);
 const h=directoryHarness('server-cache-success'),{controller}=h;
 await controller.load();
 controller.edit(directoryGenerationField,'cannot edit directly');
 assert.equal(Object.hasOwn(controller.state.draft,directoryGenerationField),false);
 controller.edit('ff14_default_region','global');
 controller.editPair('credential_fflogs_cn','client_id','synthetic-id');
 controller.editPair('credential_fflogs_cn','client_secret','synthetic-secret');
 const first=controller.clearServerCache();
 assert.equal(controller.navigationImpact().pending,true);
 assert.match(controller.state.message,/正在清除服务器缓存/);
 controller.edit('ff14_calendar_default_days','11');
 await Promise.all([controller.clearServerCache(),controller.save(),controller.replace('credential_fflogs_cn'),controller.clear('credential_fflogs_cn')]);
 await first;
 assert.equal(h.configCalls.length,1);
 assert.equal(h.credentialCalls.length,0);
 assert.equal(h.configCalls[0][0],4);
 assert.deepEqual(Object.keys(h.configCalls[0][1]),[directoryGenerationField]);
 const firstGeneration=h.configCalls[0][1][directoryGenerationField];
 assert.match(firstGeneration,/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
 assert.equal(controller.state.revision,5);
 assert.equal(controller.state.draft.ff14_default_region,'global');
 assert.equal(controller.state.draft.ff14_calendar_default_days,'11');
 assert.equal(controller.state.pairs.credential_fflogs_cn.client_secret,'synthetic-secret');
 assert.match(controller.state.message,/服务器缓存已清除/);
 assert.equal(controller.navigationImpact().ordinary,true);
 assert.equal(controller.navigationImpact().credentials,true);
 assert.equal(controller.navigationImpact().pending,false);

 const ordinary=controller.save();
 await controller.clearServerCache();
 await ordinary;
 assert.equal(h.configCalls.length,2);
 assert.equal(h.configCalls[1][0],5);
 assert.equal(Object.hasOwn(h.configCalls[1][1],directoryGenerationField),false);

 const credential=controller.clear('credential_fflogs_cn');
 await controller.clearServerCache();
 await credential;
 assert.equal(h.configCalls.length,2);
 assert.equal(h.credentialCalls.length,1);
 await controller.clearServerCache();
 assert.notEqual(h.configCalls[2][1][directoryGenerationField],firstGeneration);
 h.dispose();
});

test('higher matching revisions without the requested UUID cannot confirm server-cache clear',async()=>{
 const h=directoryHarness('server-cache-mismatch'),{controller}=h;
 await controller.load();
 controller.edit('ff14_calendar_default_days','18');
 controller.editPair('credential_fflogs_cn','client_secret','synthetic-secret');
 const actualSave=h.config.save;
 h.config.save=async(revision,patch)=>{
  const receipt=await actualSave(revision,patch);
  h.changeGeneration('another-generation');
  return receipt;
 };
 await controller.clearServerCache();
 assert.equal(controller.state.ready,false);
 assert.equal(controller.state.error,true);
 assert.match(controller.state.message,/暂未能确认服务器缓存已清除/);
 assert.equal(controller.state.draft.ff14_calendar_default_days,'18');
 assert.equal(controller.state.pairs.credential_fflogs_cn.client_secret,'synthetic-secret');
 await controller.clearServerCache();
 assert.equal(h.configCalls.length,1);
 await controller.load();
 assert.equal(controller.state.ready,true);
 assert.match(controller.state.message,/配置已读取/);
 assert.equal(h.configCalls.length,1);
 h.dispose();
});

for(const interruption of ['cancel','unbind'])test(`server-cache clear ignores a late readback after ${interruption}`,async()=>{
 const h=directoryHarness(`server-cache-${interruption}`),{controller}=h;
 await controller.load();
 let resolve;
 const actualCurrent=h.config.current;
 h.config.current=()=>new Promise(done=>{resolve=done;});
 const pending=controller.clearServerCache();
 await Promise.resolve();
 assert.equal(typeof resolve,'function');
 if(interruption==='cancel')controller.cancel();
 else controller.update({...controller.state.context,available:false});
 resolve(await actualCurrent());
 await pending;
 assert.match(controller.state.message,interruption==='cancel'?/已停止等待/:/模块或授权已变化/);
 assert.equal(controller.state.revision,4);
 assert.equal(controller.state.ready,false);
 assert.equal(controller.navigationImpact().pending,false);
 assert.equal(h.configCalls.length,1);
 h.dispose();
});

for(const error of ['revision_conflict','network_lost','admin_authorization_denied'])test(`server-cache clear fences ${error} without retrying`,async()=>{
 const h=directoryHarness(`server-cache-${error}`),{controller}=h;
 await controller.load();
 controller.edit('ff14_calendar_default_days','18');
 controller.editPair('credential_fflogs_cn','client_secret','synthetic-secret');
 let writes=0;
 h.config.save=async()=>{writes++;throw Error(error);};
 await controller.clearServerCache();
 await controller.clearServerCache();
 assert.equal(writes,1);
 assert.equal(controller.state.error,true);
 assert.equal(controller.state.ready,false);
 assert.equal(controller.state.draft.ff14_calendar_default_days,'18');
 assert.equal(controller.state.pairs.credential_fflogs_cn.client_secret,error==='admin_authorization_denied'?'':'synthetic-secret');
 h.dispose();
});
