import {test} from 'node:test';
import assert from 'node:assert/strict';
import {consumer,flush} from '../management/module-consumer-fixture.mjs';

async function unknownWrite(h,editPending=false){
 h.controller.edit('reply_theme','one');h.state.hold='admin/module-config-save';const pending=h.controller.save();await flush();
 if(editPending)h.controller.edit('reply_theme','');
 h.state.failure='admin/module-config-save';h.state.error=Error('lost-write-receipt');h.release();await pending;
 h.state.fields.reply_theme={value:'one',state:'valid',present:true,source:'sqlite'};h.state.revision++;
}
for(const editPending of [true,false])test(`UNKNOWN preserves explicit inheritance selected ${editPending?'during':'after'} write`,async()=>{
 const h=await consumer();try{
  await unknownWrite(h,editPending);if(!editPending)h.controller.edit('reply_theme','');
  assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.dirty,true);assert.equal(h.controller.state.ready,false);
  const count=h.writes().length;await h.controller.save();assert.equal(h.writes().length,count);
  h.state.failure='';h.state.hold='';await h.controller.load();assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.dirty,true);
  await h.controller.save();assert.equal(h.writes().at(-1).body.values.reply_theme,null);assert.equal(h.controller.state.dirty,false);
 }finally{h.dispose();}
});
test('UNKNOWN ordinary default intent survives delayed fresh snapshot; revoked view cannot edit',async()=>{
 const h=await consumer();try{
  h.controller.edit('ff14_calendar_default_days','14');h.state.hold='admin/module-config-save';const pending=h.controller.save();await flush();
  h.state.failure='admin/module-config-save';h.state.error=Error('lost-write-receipt');h.release();await pending;
  h.controller.edit('ff14_calendar_default_days','7');assert.equal(h.controller.state.dirty,true);
  h.state.fields.ff14_calendar_default_days={value:14,state:'valid',present:true,source:'sqlite'};h.state.revision++;h.state.failure='';h.state.hold='admin/module-config-current';
  const read=h.controller.load();await flush();h.controller.edit('ff14_calendar_default_days','10');h.release();await read;
  assert.equal(h.controller.state.draft.ff14_calendar_default_days,'10');assert.equal(h.controller.state.dirty,true);
  h.invalidate();h.controller.edit('ff14_calendar_default_days','30');assert.deepEqual(h.controller.state.draft,{});assert.equal(h.controller.state.ready,false);
 }finally{h.dispose();}
});
test('credential ACK and failed status read remain unverified without resending or retaining secrets',async()=>{
 const h=await consumer();try{
  h.editPair();h.state.failure='admin/module-credential-status';h.state.error=Error('status_lost_after_ACK');await h.controller.replace('credential_fflogs_cn');
  assert.equal(h.writes().length,1);assert.equal(h.state.revision,5);assert.equal(h.controller.state.revision,4);assert.equal(h.controller.state.credentials.revision,4);
  assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.baselineUnverified,true);assert.equal(h.controller.state.pairs.credential_fflogs_cn.client_secret,'');
  assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');await h.controller.load();assert.equal(h.controller.state.message,'已提交，状态暂未核实，请刷新确认。');
  h.state.failure='';await h.controller.load();assert.equal(h.controller.state.ready,true);assert.equal(h.controller.state.revision,5);assert.equal(h.controller.state.credentials.revision,5);assert.equal(h.writes().length,1);
 }finally{h.dispose();}
});
