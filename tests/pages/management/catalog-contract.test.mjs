import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validateCatalog,validateSnapshot,updateBody} from '../../../pages/management/app.js';
import {catalog,snapshot} from './fixtures.mjs';
test('catalog owns fields and value access; registration grants no read or write',()=>{
  const declarations=validateCatalog(catalog());assert.equal(declarations.fields.length,5);
  assert.equal(validateSnapshot(snapshot(),declarations)['game_link/core'].revision,1);
  assert.throws(()=>validateSnapshot({...snapshot(),'example/demo':{revision:1,fields:{sample:{value:'PRIVATE'}}}},declarations));
  assert.throws(()=>updateBody('example/demo',1,{sample:{mode:'replace',value:'x'}},declarations));
});
test('catalog refuses unknown keys duplicates authority inconsistencies and all frozen budgets',()=>{
  const reject=change=>{const value=catalog();change(value);assert.throws(()=>validateCatalog(value));};
  reject(v=>{v.extra=true;});reject(v=>{v.fields[0].private='PRIVATE';});reject(v=>{v.fields.push({...v.fields[0]});});
  reject(v=>{v.fields[0].editable=true;});reject(v=>{v.fields[0].blocked_reason='unknown';});
  reject(v=>{v.fields[0].description='x'.repeat(4097);});
  reject(v=>{v.fields=Array.from({length:129},(_,i)=>({...v.fields[0],name:'sample_'+i}));});
  reject(v=>{v.fields=Array.from({length:100},(_,i)=>({...v.fields[0],name:'sample_'+i,description:'中'.repeat(4096)}));});
  reject(v=>{v.fields[0].value_schema={type:'string',pattern:'unsafe'};});
  reject(v=>{v.fields[0].value_schema={type:'integer',minimum:3,maximum:1};});
  reject(v=>{v.fields[0].value_schema={type:'boolean',enum:[true,true]};});
  reject(v=>{v.fields[0].default=42;});
  reject(v=>{let schema={type:'string'};for(let i=0;i<17;i++)schema={type:'array',items:schema};v.fields[0].value_schema=schema;});
  reject(v=>{v.fields[0].value_schema={type:'object',properties:Object.fromEntries(Array.from({length:1100},(_,i)=>['field_'+i,{type:'string'}]))};});
  const original=catalog(),checked=validateCatalog(original);original.fields[0].editable=true;assert.equal(checked.fields[0].editable,false);assert.throws(()=>{checked.fields[0].editable=true;});
});
test('basic schema conversion is generic and never grants a blocked field',()=>{
  const declarations=catalog(),base=declarations.fields[4];
  declarations.fields.push({...base,name:'flag',value_schema:{type:'boolean'},default:false});
  declarations.fields.push({...base,name:'rate',value_schema:{type:'number',minimum:0,maximum:1},default:.5});
  const checked=validateCatalog(declarations);
  assert.deepEqual(updateBody('game_link/core',2,{default_region:{mode:'replace',value:'1'},flag:{mode:'replace',value:'1'},rate:{mode:'replace',value:'.25'}},checked).updates,[{field:'default_region',mode:'replace',value:'global'},{field:'flag',mode:'replace',value:true},{field:'rate',mode:'replace',value:.25}]);
  for(const raw of ['', ' ', 'Infinity', '1.2', '31'])assert.throws(()=>updateBody('ff14/ff14',1,{ff14_calendar_default_days:{mode:'replace',value:raw}},checked));
  assert.throws(()=>updateBody('game_link/core',1,{default_region:{mode:'replace',value:'cn'}},checked));
  assert.throws(()=>updateBody('game_link/core',1,{extra:{mode:'clear'}},checked));
  assert.throws(()=>updateBody('ff14/ff14',1,{},checked),/select_changes/);
  const extra=catalog();extra.fields[0].name='sample:Count';assert.equal(validateCatalog(extra).fields[0].name,'sample:Count');
});
test('value read is closed and validates invalid/null/source/revision without exposing extras',()=>{
  const checked=validateCatalog(catalog());
  for(const edit of [v=>{v['game_link/core'].revision=0;},v=>{v['game_link/core'].fields.default_region.value='other';},v=>{v['game_link/core'].fields.default_region.state='invalid';},v=>{v['game_link/core'].fields.default_region.source='private';},v=>{v['game_link/core'].fields.default_region.extra='PRIVATE';}]){const value=snapshot();edit(value);assert.throws(()=>validateSnapshot(value,checked));}
  assert.throws(()=>validateSnapshot(snapshot()));
});
