import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,keyStatus,flush,context} from './global-settings-fixture.mjs';

const synthetic=()=>Buffer.alloc(32,4).toString('base64');
const writes=h=>h.calls.filter(c=>/admin\/(update|recover|master-key-(?:restore|reset|adopt-environment))$/.test(c.endpoint));
const declarations={schema_version:1,fields:[{module_id:'game_link/core',name:'sample',description:'Sample',group:null,value_schema:{type:'string'},default:'old',required:false,readable:true,editable:true,blocked_reason:null}]};
const snapshot=()=>({'game_link/core':{revision:1,fields:{sample:{value:'old',state:'valid',present:false,source:'default'}}}});
const fontStatus=()=>({revision:1,state:'ready',source:'canonical',error:null,filename:'fixture.otf',size:1,sha256:'a'.repeat(64),download_url:''});
const fontCatalog=()=>{const value=structuredClone(declarations);value.fields[0].group='reply_style';return value;};
function ordinary(h){const input=h.doc.querySelector('[data-field=sample] [data-role=value]');input.value='ordinary draft';input.dispatchEvent(new h.dom.window.Event('input'));return input;}
function keepOrdinary(h){assert.equal(h.doc.querySelector('[data-field=sample] [data-role=value]').value,'ordinary draft');assert.equal(h.doc.querySelector('[data-field=sample] [data-role=mode]').value,'replace');}

for(const state of ['ready','needs_restore'])for(const endpoint of ['admin/catalog','admin/read','admin/master-key-status'])test(`global ${state}: unknown ${endpoint} scrubs even a detached restore input, keeps ordinary edits and reports UNKNOWN`,async()=>{
 let failure='';const initial=keyStatus(state==='ready'?{}:{state,ready:false,reason_code:'managed_key_missing'});
 const h=await harness(initial,action=>{if(action===failure)throw Error('synthetic-unclassified');if(action==='admin/catalog')return declarations;if(action==='admin/read')return snapshot();});
 try{ordinary(h);const secret=h.doc.getElementById('master-key-restore-input');assert.ok(secret);secret.value=synthetic();failure=endpoint;await h.click('#refresh');
  assert.equal(secret.value.length,0);keepOrdinary(h);assert.equal(writes(h).length,0);
  assert.match(h.doc.getElementById('status').textContent,/未取得成功确认/);assert.doesNotMatch(h.doc.getElementById('status').textContent,/已刷新|授权不可用/);
  failure='';await h.click('#refresh');assert.equal(h.doc.getElementById('master-key-restore-input').value.length,0);keepOrdinary(h);assert.equal(writes(h).length,0);
 }finally{h.close();}
});

for(const kind of ['legacy','storage'])test(`global ${kind} read failure clears restore draft without executing a mutation`,async()=>{
 let failure='';const h=await harness(keyStatus({storage_security:'legacy_review_required'}),endpoint=>{if(endpoint===failure)throw TypeError('Failed to fetch');});
 try{const secret=h.doc.getElementById('master-key-restore-input');secret.value=synthetic();
  if(kind==='legacy'){h.page.setLegacyPolicies([{policy_id:'sample.copy',target_owner:'example/demo'}]);await flush();failure='admin/legacy-config-plan';await h.click('[data-action=legacy-plan]');}
  else{failure='admin/master-key-migration-plan';await h.click('#storage-migration-plan');}
  assert.equal(secret.value.length,0);assert.match(h.doc.getElementById('status').textContent,/未取得成功确认/);assert.equal(writes(h).length,0);
 }finally{h.close();}
});

test('global ACK then unknown read clears restore draft, keeps ordinary edit, sends one write without retry',async()=>{
 let ack=false;const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/update'){ack=true;return {module_id:'game_link/core',revision:2};}
  if(endpoint==='admin/catalog'){if(ack)throw Error('synthetic-read-after-ACK');return declarations;}
  if(endpoint==='admin/read')return snapshot();});
 try{ordinary(h);const secret=h.doc.getElementById('master-key-restore-input');secret.value=synthetic();await h.click('section[data-owner="game_link/core"] [data-action=save]');
  assert.equal(secret.value.length,0);keepOrdinary(h);assert.match(h.doc.getElementById('status').textContent,/已提交，状态暂未核实/);assert.equal(writes(h).length,1);
  await h.click('section[data-owner="game_link/core"] [data-action=save]');assert.equal(writes(h).length,1);
 }finally{h.close();}
});

test('late old-session status rejection cannot remove a fresh session panel or scrub its restore draft',async()=>{
 let hold=false,rejectOld;const h=await harness(keyStatus(),endpoint=>{if(hold&&endpoint==='admin/master-key-status'){hold=false;return new Promise((_,reject)=>{rejectOld=reject;});}});
 try{hold=true;await h.click('#refresh');assert.equal(typeof rejectOld,'function');h.emit({...context,session:'synthetic-next'});await flush();
  const fresh=h.doc.getElementById('master-key-restore-input');assert.ok(fresh);fresh.value=synthetic();rejectOld(Error('synthetic-old-session-read'));await flush();h.page.selectOwner();await flush();
  assert.equal(h.doc.getElementById('master-key-restore-input'),fresh);assert.equal(fresh.value.length,44);assert.equal(fresh.isConnected,true);assert.equal(writes(h).length,0);
 }finally{h.close();}
});

for(const error of [Error('synthetic-font-read-unknown'),TypeError('Failed to fetch')])test(`global font status ${error.name} read clears current restore draft without classifying authorization`,async()=>{
 let fail=false;const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/catalog')return fontCatalog();if(endpoint==='admin/read')return snapshot();if(endpoint==='admin/font-status'){if(fail)throw error;return fontStatus();}});
 try{ordinary(h);const secret=h.doc.getElementById('master-key-restore-input');secret.value=synthetic();fail=true;await h.click('[data-action=font-refresh]');
  assert.equal(secret.value.length,0);keepOrdinary(h);assert.match(h.doc.getElementById('font-status').textContent,/字体操作未确认/);assert.doesNotMatch(h.doc.getElementById('font-status').textContent,/授权/);assert.equal(writes(h).length,0);
  assert.equal(h.calls.filter(c=>c.endpoint==='admin/font-status').length,2);
 }finally{h.close();}
});

test('late old-session font read failure cannot scrub a fresh session restore draft',async()=>{
 let hold=false,rejectOld;const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/catalog')return fontCatalog();if(endpoint==='admin/read')return snapshot();if(endpoint==='admin/font-status'){if(hold){hold=false;return new Promise((_,reject)=>{rejectOld=reject;});}return fontStatus();}});
 try{hold=true;await h.click('[data-action=font-refresh]');assert.equal(typeof rejectOld,'function');h.emit({...context,session:'synthetic-font-next'});await flush();
  const fresh=h.doc.getElementById('master-key-restore-input');fresh.value=synthetic();rejectOld(Error('synthetic-old-font-read'));await flush();h.page.selectOwner();await flush();
  assert.equal(h.doc.getElementById('master-key-restore-input'),fresh);assert.equal(fresh.value.length,44);assert.equal(writes(h).length,0);
 }finally{h.close();}
});

test('successful font status reporting source failure keeps the restore draft and is not an authorization failure',async()=>{
 const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/catalog')return fontCatalog();if(endpoint==='admin/read')return snapshot();if(endpoint==='admin/font-status')return {...fontStatus(),state:'unavailable',source:'system',error:'download_failed'};});
 try{const secret=h.doc.getElementById('master-key-restore-input');secret.value=synthetic();await h.click('[data-action=font-refresh]');
  assert.equal(secret.value.length,44);assert.match(h.doc.getElementById('font-status').textContent,/获取失败.*系统字体/);assert.doesNotMatch(h.doc.getElementById('font-status').textContent,/授权|未确认/);assert.equal(writes(h).length,0);
 }finally{h.close();}
});

test('unknown current font read cancels a pending restore intent so its captured synthetic key cannot submit',async()=>{
 let fail=false;const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/catalog')return fontCatalog();if(endpoint==='admin/read')return snapshot();if(endpoint==='admin/font-status'){if(fail)throw Error('synthetic-font-read');return fontStatus();}});
 try{h.doc.getElementById('master-key-restore-input').value=synthetic();await h.click('#master-key-restore');
  const oldConfirm=h.doc.querySelector('[data-action=confirm-key-restore]');assert.ok(oldConfirm);fail=true;await h.click('[data-action=font-refresh]');
  assert.equal(Boolean(h.doc.querySelector('[data-confirmation]')),false);oldConfirm.click();await flush();assert.equal(writes(h).length,0);
 }finally{h.close();}
});

test('old-session font rejection does not cancel a new-session restore confirmation',async()=>{
 let hold=false,rejectOld;const h=await harness(keyStatus(),endpoint=>{if(endpoint==='admin/catalog')return fontCatalog();if(endpoint==='admin/read')return snapshot();if(endpoint==='admin/font-status'){if(hold){hold=false;return new Promise((_,reject)=>{rejectOld=reject;});}return fontStatus();}});
 try{hold=true;await h.click('[data-action=font-refresh]');h.emit({...context,session:'synthetic-confirm-next'});await flush();
  h.doc.getElementById('master-key-restore-input').value=synthetic();await h.click('#master-key-restore');const fresh=h.doc.querySelector('[data-action=confirm-key-restore]');assert.ok(fresh);
  rejectOld(Error('synthetic-old-font-read'));await flush();assert.equal(h.doc.querySelector('[data-action=confirm-key-restore]'),fresh);assert.equal(fresh.isConnected,true);assert.equal(writes(h).length,0);
 }finally{h.close();}
});
