// Current source boundary scenarios. Historical VM name retained for the test entrypoint.
import assert from 'node:assert/strict';
import {consumer,flush} from '../management/module-consumer-fixture.mjs';
const [, ,surface,scenario]=process.argv;
const h=await consumer();
try{
 if(['after-failure-inherit','after-failure-default','after-failure-unchanged'].includes(scenario)){
  h.controller.edit('reply_theme','one');h.state.hold='admin/module-config-save';const pending=h.controller.save();await flush();h.state.failure='admin/module-config-save';h.state.error=Error('lost-write-receipt');h.release();await pending;
  // The server may have committed even though the caller received no receipt.
  h.state.fields.reply_theme={value:'one',state:'valid',present:true,source:'sqlite'};h.state.revision++;
  if(!scenario.endsWith('unchanged')){h.controller.edit('reply_theme','');assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.dirty,true,'unknown write keeps explicit inherited/default intent even against old absence');}
  const count=h.writes().length;await h.controller.save();assert.equal(h.writes().length,count);
  h.state.failure='';h.state.hold='';await h.controller.load();assert.equal(h.controller.state.draft.reply_theme,scenario.endsWith('unchanged')?'one':'');
  if(!scenario.endsWith('unchanged')){await h.controller.save();assert.equal(h.writes().at(-1).body.values.reply_theme,null);}
 }else if(scenario.startsWith('font-')){
  h.editPair();h.controller.edit('reply_theme','one');
  if(scenario==='font-network'){h.state.failure='admin/module-config-current';h.state.error=Error('network');await h.controller.load();assert.equal(h.controller.state.draft.reply_theme,'one');assert.equal(h.writes().length,0);}
  else if(scenario==='font-late'){
   h.state.hold='admin/module-config-current';const read=h.controller.load();await flush();h.dispose();h.release();await read;assert.equal(h.writes().length,0);assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));
  }else{h.invalidate();await h.controller.save();assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));assert.equal(h.writes().length,0);}
 }else{
  h.controller.edit('reply_theme','one');h.state.hold='admin/module-config-save';
  const pending=h.controller.save();await flush();assert.equal(h.writes().length,1);if(scenario.includes('conflict'))h.state.revision++;
  if(!scenario.includes('unchanged'))h.controller.edit('reply_theme','');
  if(scenario==='after-failure-owner'){h.controller.update({...h.context,owner:'example/demo'});h.release();await pending;assert.deepEqual(h.controller.state.draft,{});assert.equal(h.controller.state.ready,false);}
  else if(scenario==='after-failure-denied'){h.invalidate();h.release();await pending;assert.equal(h.controller.state.ready,false);assert.equal(h.writes().length,1);}
  else{
   h.release();await pending;
   if(scenario.includes('conflict'))assert.equal(h.controller.state.ready,false);
   assert.equal(h.controller.state.draft.reply_theme,scenario.includes('unchanged')?'one':'');
   // Root repair-2 decision: a rejected CAS cannot verify the old baseline.
   // Keep a newer explicit clear dirty and forbid resubmission until refresh.
   if(!scenario.includes('unchanged'))assert.equal(h.controller.state.dirty,true);
   if(scenario.includes('conflict')){const count=h.writes().length;await h.controller.save();assert.equal(h.writes().length,count);assert.equal(h.controller.state.draft.reply_theme,'');}
   h.state.hold='';await h.controller.load();assert.equal(h.controller.state.draft.reply_theme,scenario.includes('unchanged')?'one':'');
   if(!scenario.includes('unchanged')){await h.controller.save();assert.equal(h.writes().at(-1).body.values.reply_theme,null);assert.equal(h.controller.state.dirty,false);}
  }
 }
 console.log(surface+' '+scenario+' passed');
}finally{h.dispose();}
