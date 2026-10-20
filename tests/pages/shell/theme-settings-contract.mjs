// Current public Module consumer; global Core controls are independently covered by test_management.
import assert from 'node:assert/strict';
import {consumer} from '../management/module-consumer-fixture.mjs';
for(const owner of ['ff14/ff14','example/demo']){
 const h=await consumer({owner});try{
  assert.equal(h.controller.state.themeAvailable,true);assert.deepEqual(h.controller.state.themeChoices.map(item=>item.label),['Server one','Server two']);assert.equal(h.controller.state.draft.reply_theme,'');assert.equal(h.controller.state.dirty,false);assert.equal(h.writes().length,0);
  h.controller.edit('reply_theme','one');assert.equal(h.controller.state.dirty,true);h.controller.edit('reply_theme','');assert.equal(h.controller.state.dirty,false);assert.equal(h.writes().length,0);
  h.controller.edit('reply_theme','two');await h.controller.save();assert.equal(h.writes().at(-1).body.owner,owner);assert.equal(h.writes().at(-1).body.values.reply_theme,'two');assert.equal(h.writes().at(-1).body.expected_revision,4);
  h.controller.edit('reply_theme','');await h.controller.save();assert.equal(h.writes().at(-1).body.values.reply_theme,null);assert.equal(h.writes().at(-1).body.expected_revision,5);assert.equal(h.state.fields.reply_theme.present,false);
  const before=h.writes().length;h.controller.edit('reply_theme','unknown-choice');await h.controller.save();assert.equal(h.writes().length,before);assert.equal(h.controller.state.error,true);
  h.controller.discard();h.state.fields.reply_theme={value:null,state:'invalid',present:true,source:'sqlite'};await h.controller.load();assert.equal(h.controller.state.draft.reply_theme,'!invalid');await h.controller.save();assert.equal(h.writes().length,before);h.controller.edit('reply_theme','');await h.controller.save();assert.equal(h.writes().at(-1).body.values.reply_theme,null);
  const count=h.calls.length;h.controller.update({...h.context,theme:'dark',locale:'en-US'});assert.equal(h.calls.length,count);
 }finally{h.dispose();}
}
console.log('public theme settings passed');
