// Current source DOM navigation guard plus public owner/secret lifecycle assertions.
import assert from 'node:assert/strict';
import {consumer} from '../management/module-consumer-fixture.mjs';
for(const owner of ['ff14/ff14','example/demo']){const h=await consumer({owner});try{h.controller.edit('reply_theme','one');h.editPair();h.editPair('credential_fflogs_global');h.controller.update({...h.context,theme:'dark'});assert.equal(h.controller.state.draft.reply_theme,'one');assert.ok(h.controller.state.pairs.credential_fflogs_cn.client_secret);h.invalidate();assert.deepEqual(h.controller.state.draft,{});assert.ok(Object.values(h.controller.state.pairs).every(pair=>pair.client_secret===''));await h.controller.save();assert.equal(h.writes().length,0);}finally{h.dispose();}}
console.log('public owner navigation/hash/history passed');
