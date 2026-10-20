// Shared target revision, stale receipts and owner fences through current public services.
import assert from 'node:assert/strict';
import {consumer,flush} from '../management/module-consumer-fixture.mjs';
const scenario=process.argv[2],h=await consumer();
try{
 h.controller.edit('ff14_default_region','global');h.state.hold='admin/module-config-save';const write=h.controller.save();await flush();assert.equal(h.writes().length,1);
 if(scenario==='draft'){h.controller.edit('ff14_default_region','cn');h.release();await write;assert.equal(h.controller.state.draft.ff14_default_region,'cn');assert.equal(h.controller.state.dirty,true);h.controller.discard();assert.equal(h.controller.state.draft.ff14_default_region,'global');}
 else if(scenario==='configuration-success'){h.release();await write;assert.equal(h.controller.state.revision,5);h.editPair();await h.controller.replace('credential_fflogs_cn');assert.equal(h.writes().at(-1).body.expected_revision,5);assert.equal(h.controller.state.revision,6);}
 else{h.invalidate();h.release();await write;assert.equal(h.controller.state.revision,4);assert.equal(h.controller.state.ready,false);assert.equal(h.controller.state.dirty,false);const count=h.calls.length;await h.controller.save();assert.equal(h.calls.length,count);}
 console.log(scenario+' passed');
}finally{h.dispose();}
