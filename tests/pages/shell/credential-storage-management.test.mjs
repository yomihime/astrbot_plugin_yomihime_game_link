import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog,snapshot,context,credentialCatalog,credentialStatus} from '../management/fixtures.mjs';

const host=new JSDOM('<body></body>',{pretendToBeVisual:true});
for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=host.window[name];
const {createManagementPage}=await import('../../../pages/management/app.js');
const flush=async()=>{for(let i=0;i<80;i++)await Promise.resolve();await new Promise(resolve=>setTimeout(resolve,0));};
const digest='a'.repeat(64);
const state=(kind,revision=1,generation=kind==='ready'?1:0)=>({state:kind,reason_code:kind==='ready'?'ready':'managed_key_missing',ready:kind==='ready',source:kind==='ready'?'managed':'unselected',generation,revision,inventory_digest:digest,unknown_count:0,impact:kind==='ready'?[]:[{module_id:'ff14/ff14',config_count:1,grant_count:0}]});

async function harness(initial,{holdReset=false}={}){
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];
  let current=initial,credentialRevision=1,configured=false,failRestore=false,onContext=null,releaseReset;
  const resetHeld=holdReset?new Promise(resolve=>{releaseReset=resolve;}):null;
  const bridge={ready:()=>Promise.resolve(context),onContext(listener){onContext=listener;return()=>{onContext=null;};},async apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return catalog();
    if(endpoint==='admin/read')return snapshot();
    if(endpoint==='admin/master-key-status'){
      if(current===undefined)throw Error('key_status_unavailable');
      return current;
    }
    if(endpoint==='admin/credential-catalog')return credentialCatalog();
    if(endpoint==='admin/credential-status'){const value=credentialStatus(credentialRevision);value['ff14/ff14'].fields.credential_fflogs_cn=configured?'configured':'unset';return value;}
    if(endpoint==='admin/credential-readiness')return {ready:current?.ready===true,state:current?.ready?'ready':'unavailable',reason_code:current?.ready?'ready':'secret_encryption_unavailable',diagnostics:{key_state:current?.ready?'valid_format':'not_provided',codec_state:current?.ready?'available':'not_run'}};
    if(endpoint==='admin/master-key-restore'){
      if(failRestore){failRestore=false;throw Error('revision_conflict');}
      current=state('ready',current.revision+1,Math.max(1,current.generation));return current;
    }
    if(endpoint==='admin/master-key-reset'){if(resetHeld)await resetHeld;current=state('ready',current.revision+1,current.generation+1);return current;}
    if(endpoint==='admin/credential-update'){configured=true;return {module_id:'ff14/ff14',revision:++credentialRevision};}
    throw Error('unexpected endpoint: '+endpoint);
  }};
  const page=createManagementPage(dom.window.document,bridge,dom.window);
  page.start();await flush();
  return {dom,page,calls,failNextRestore(){failRestore=true;},releaseReset(){releaseReset?.();},switchContext(next){onContext?.(next);},close(){releaseReset?.();page.close();dom.window.close();}};
}

function fillPair(dom){
  const card=dom.window.document.querySelector('[data-credential="credential_fflogs_cn"]');
  assert.ok(card);
  const mode=card.querySelector('select'),client=card.querySelector('[data-role="client_id"]'),secret=card.querySelector('[data-role="client_secret"]'),save=card.querySelector('[data-action="credential-save"]');
  mode.value='replace';mode.dispatchEvent(new dom.window.Event('change'));
  client.value='synthetic-id';secret.value='synthetic-secret';secret.dispatchEvent(new dom.window.Event('input'));
  return {client,secret,save};
}

test('fresh managed key lets a normal user save a synthetic FFLogs pair immediately',async()=>{
  const h=await harness(state('ready'));
  try{
    assert.equal(h.calls.filter(call=>call.endpoint==='admin/master-key-restore').length,0);
    const {client,secret,save}=fillPair(h.dom);
    assert.equal(client.disabled,false);assert.equal(secret.disabled,false);assert.equal(save.disabled,false);
    save.click();await flush();
    const writes=h.calls.filter(call=>call.endpoint==='admin/credential-update');
    assert.equal(writes.length,1);
    assert.deepEqual(writes[0].body.updates,[{field:'credential_fflogs_cn',mode:'replace',value:{client_id:'synthetic-id',client_secret:'synthetic-secret'}}]);
    assert.equal(h.dom.window.document.querySelector('[data-credential="credential_fflogs_cn"] [data-role="client_secret"]').value,'');
  }finally{h.close();}
});

test('old ciphertext restore rejects failed CAS, refreshes, and then enables the pair form',async()=>{
  const h=await harness(state('needs_restore'));
  try{
    const doc=h.dom.window.document,input=doc.getElementById('master-key-restore-input'),restore=doc.getElementById('master-key-restore');
    assert.ok(input);assert.equal(restore.disabled,false);
    const initially=fillPair(h.dom);assert.equal(initially.secret.disabled,true);assert.equal(initially.save.disabled,true);
    input.value='not-a-key';restore.click();await flush();assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-restore').length,0);
    input.value=Buffer.alloc(32,4).toString('base64');h.failNextRestore();restore.click();await flush();
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-restore').length,1);
    assert.equal(doc.getElementById('master-key-restore-input').value,'');
    doc.getElementById('refresh').click();await flush();
    doc.getElementById('master-key-restore-input').value=Buffer.alloc(32,4).toString('base64');doc.getElementById('master-key-restore').click();await flush();
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-restore').length,2);
    const form=fillPair(h.dom);assert.equal(form.secret.disabled,false);assert.equal(form.save.disabled,false);
    form.save.click();await flush();assert.equal(h.calls.filter(c=>c.endpoint==='admin/credential-update').length,1);
  }finally{h.close();}
});

test('visible reset consent lists every affected module, cancel writes nothing, and one confirmed click enables saving',async()=>{
  const initial={...state('needs_restore'),impact:[{module_id:'ff14/ff14',config_count:2,grant_count:0},{module_id:'example/demo',config_count:1,grant_count:2}]};
  const h=await harness(initial,{holdReset:true});
  try{
    const doc=h.dom.window.document,consent=doc.getElementById('master-key-reset-consent'),cancel=doc.getElementById('master-key-reset-cancel'),reset=doc.getElementById('master-key-reset');
    assert.ok(consent);assert.equal(consent.type,'checkbox');assert.equal(consent.checked,false);assert.equal(reset.disabled,true);assert.notEqual(reset.getAttribute('aria-disabled'),'false');
    reset.click();await flush();assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,0);
    assert.equal(h.page.navigationImpact().requiresConfirmation,false);
    assert.match(doc.getElementById('master-key-reset-impact').textContent,/配置凭据 3 项.*授权凭据 2 项/);
    assert.match(doc.querySelector('.master-key-panel').textContent,/example\/demo：配置凭据 1 项，授权凭据 2 项/);
    consent.click();await flush();assert.equal(consent.checked,true);assert.equal(reset.disabled,false);assert.notEqual(reset.getAttribute('aria-disabled'),'true');
    assert.equal(h.page.navigationImpact().requiresConfirmation,false);
    cancel.click();await flush();assert.equal(consent.checked,false);assert.equal(reset.disabled,true);
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,0);
    consent.click();await flush();reset.click();await flush();assert.equal(reset.disabled,true);assert.equal(consent.disabled,true);
    reset.click();await flush();
    const writes=h.calls.filter(c=>c.endpoint==='admin/master-key-reset');assert.equal(writes.length,1);
    assert.deepEqual(writes[0].body,{expected_revision:1,expected_generation:0,inventory_digest:digest,confirmation:'RESET ENCRYPTED CREDENTIALS'});
    assert.equal(doc.getElementById('master-key-reset-consent').checked,false);
    h.releaseReset();await flush();
    const form=fillPair(h.dom);assert.equal(form.secret.disabled,false);assert.equal(form.save.disabled,false);
    form.save.click();await flush();assert.equal(h.calls.filter(c=>c.endpoint==='admin/credential-update').length,1);
  }finally{h.close();}
});

test('reset consent is revoked by refresh, suspend, and management context replacement',async()=>{
  const h=await harness(state('needs_restore'));
  try{
    const doc=h.dom.window.document;
    const consent=()=>doc.getElementById('master-key-reset-consent');
    const reset=()=>doc.getElementById('master-key-reset');
    consent().click();await flush();assert.equal(reset().disabled,false);
    doc.getElementById('refresh').click();await flush();assert.equal(consent().checked,false);assert.equal(reset().disabled,true);
    consent().click();await flush();h.page.suspend();await flush();assert.equal(consent().checked,false);
    reset().click();await flush();assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,0);
    h.page.resume();await flush();consent().click();await flush();assert.equal(reset().disabled,false);
    const detached=reset();h.switchContext({...context,session:'replacement'});await flush();
    detached.click();assert.equal(consent().checked,false);assert.equal(reset().disabled,true);
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,0);
  }finally{h.close();}
});

test('missing or failed global key status fences credential writes',async()=>{
  for(const status of [null,undefined]){
    const h=await harness(status);
    try{
      const card=h.dom.window.document.querySelector('[data-credential="credential_fflogs_cn"]');
      if(card){
        const {secret,save}=fillPair(h.dom);
        assert.equal(secret.disabled,true);
        save.click();await flush();
      }
      assert.equal(h.calls.filter(call=>call.endpoint==='admin/credential-update').length,0);
    }finally{h.close();}
  }
});
