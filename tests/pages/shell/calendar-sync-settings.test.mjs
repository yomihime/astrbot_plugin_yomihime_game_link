import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog, snapshot, context} from '../management/fixtures.mjs';

const host = new JSDOM('<body></body>', {pretendToBeVisual: true});
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'MutationObserver']) globalThis[key] = host.window[key];
const {validateCatalog, validateSnapshot, updateBody} = await import('../management/source-management.mjs');
const name = 'ff14_calendar_sync_interval_seconds', owner = 'ff14/ff14';
const manifest = JSON.parse(await readFile(new URL('../../../modules/ff14/yomihime.manifest.json', import.meta.url), 'utf8'));
const field = manifest.modules[0].config_fields.find(field => field.name === name);
const declarations = catalog();
declarations.fields.push({module_id: owner, name, description: field.description, group: field.group, value_schema: field.value_schema, default: field.default, required: false, readable: true, editable: true, blocked_reason: null});

import {consumer} from '../management/module-consumer-fixture.mjs';
import {sourceModule,root} from './compile.mjs';
const {ordinaryFields}=await sourceModule(root+'modules/ff14/pages/src/settings-controller.ts');
test('Module calendar sync selection agrees with approved enum and saves exact numeric CAS values',async()=>{
 const choice=ordinaryFields.find(field=>field.name===name);assert.deepEqual(choice.choices.map(([value])=>Number(value)),field.value_schema.enum);
 const h=await consumer();try{for(const seconds of [43200,86400,21600]){const count=h.writes().length,revision=h.state.revision;h.controller.edit(name,String(seconds));assert.equal(h.writes().length,count);await h.controller.save();assert.equal(h.writes().length,count+1);assert.equal(h.writes().at(-1).body.values[name],seconds);assert.equal(h.writes().at(-1).body.expected_revision,revision);assert.equal(h.state.fields[name].value,seconds);assert.equal(h.controller.state.dirty,false);}}finally{h.dispose();}
});
test('Module calendar conflict retains draft, blocks duplicate writes and reloads current CAS',async()=>{
 const h=await consumer();try{h.controller.edit(name,'43200');h.state.revision=8;await h.controller.save();assert.equal(h.writes().length,1);assert.equal(h.controller.state.draft[name],'43200');assert.equal(h.controller.state.ready,false);await h.controller.save();assert.equal(h.writes().length,1);await h.controller.load();assert.equal(h.controller.state.draft[name],'43200');await h.controller.save();assert.equal(h.writes().at(-1).body.expected_revision,8);assert.equal(h.state.fields[name].value,43200);}finally{h.dispose();}
});
test('calendar values and grants remain closed; invalid stored field needs explicit repair',async()=>{
 const checked=validateCatalog(declarations);for(const value of ['', '-1','3','21600','1.5','true'])assert.throws(()=>updateBody(owner,1,{[name]:{mode:'replace',value}},checked));
 const denied=structuredClone(declarations);Object.assign(denied.fields.find(field=>field.name===name),{readable:false,editable:false,blocked_reason:'not_granted'});assert.throws(()=>updateBody(owner,1,{[name]:{mode:'replace',value:'0'}},validateCatalog(denied)),/admin_authorization_denied/);
 const h=await consumer();try{h.state.fields[name]={value:null,state:'invalid',present:true,source:'sqlite'};await h.controller.load();assert.equal(h.controller.state.draft[name],'');await h.controller.save();assert.equal(h.writes().length,0);h.controller.edit(name,'21600');await h.controller.save();assert.equal(h.state.fields[name].value,21600);}finally{h.dispose();}
});
