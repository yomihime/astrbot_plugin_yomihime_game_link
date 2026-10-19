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
const old=()=>({state:'needs_restore',reason_code:'key_unselected',ready:false,source:'unselected',generation:0,revision:1,inventory_digest:digest,unknown_count:0,impact:[{module_id:'ff14/ff14',config_count:1,grant_count:0}]});
const messages=[
  '原密钥格式无效，请输入标准 Base64 编码的 32 字节密钥；旧凭据未更改。',
  '所填密钥与当前密钥不一致；旧凭据未更改，请核对后重试。',
  '所填密钥未能解开现有凭据；旧凭据未被替换，请核对原密钥后重试。',
];

async function harness(failures,initial=old()){
  const html=await readFile(new URL('../../../pages/management/index.html',import.meta.url),'utf8');
  const dom=new JSDOM(html,{pretendToBeVisual:true}),calls=[];
  let current=initial;
  const bridge={ready:()=>Promise.resolve(context),onContext(){return()=>{};},async apiPost(endpoint,body){calls.push({endpoint,body});
    if(endpoint==='admin/catalog')return catalog();
    if(endpoint==='admin/read')return snapshot();
    if(endpoint==='admin/master-key-status')return current;
    if(endpoint==='admin/credential-catalog')return credentialCatalog();
    if(endpoint==='admin/credential-status')return credentialStatus(1);
    if(endpoint==='admin/credential-readiness')return {ready:current.ready,state:current.ready?'ready':'unavailable',reason_code:current.ready?'ready':'secret_encryption_unavailable',diagnostics:{key_state:current.ready?'valid_format':'not_provided',codec_state:current.ready?'available':'not_run'}};
    if(endpoint==='admin/master-key-restore'){
      const next=failures.shift();if(next)throw Error(next);
      current={...current,state:'ready',reason_code:'ready',ready:true,source:'managed',generation:1,revision:2,impact:[]};return current;
    }
    if(endpoint==='admin/master-key-reset'){
      const next=failures.shift();if(next)throw Error(next);
      current={...current,state:'ready',reason_code:'ready',ready:true,source:'managed',generation:1,revision:2,impact:[]};return current;
    }
    throw Error('unexpected endpoint: '+endpoint);
  }};
  const page=createManagementPage(dom.window.document,bridge,dom.window);page.start();await flush();
  return {dom,page,calls,close(){page.close();dom.window.close();}};
}

test('component outage offers retry without suggesting key replacement; unknown inventory fences changes',async()=>{
  for(const [status,reason,retry] of [
    ['initialization_failed','encryption_component_unavailable',true],
    ['unknown','inventory_unknown',false],
  ]){
    const initial={...old(),state:status,reason_code:reason,unknown_count:retry?0:1};
    const h=await harness([],initial);
    try{
      const doc=h.dom.window.document;
      assert.equal(doc.getElementById('master-key-retry').disabled,!retry);
      assert.equal(doc.getElementById('master-key-restore').disabled,true);
      assert.equal(doc.getElementById('master-key-reset').disabled,true);
      assert.match(doc.getElementById('master-key-status').textContent,retry?/加密组件暂不可用/:/凭据库存无法安全确认/);
      assert.equal(h.calls.filter(c=>c.endpoint.startsWith('admin/master-key-')&&c.endpoint!=='admin/master-key-status').length,0);
    }finally{h.close();}
  }
});

function submit(h){
  const doc=h.dom.window.document,input=doc.getElementById('master-key-restore-input'),button=doc.getElementById('master-key-restore');
  assert.equal(button.disabled,false);
  input.value=Buffer.alloc(32,4).toString('base64');button.click();
}

test('bounded wrong-key refusals explain unchanged credentials and allow correction without refresh',async()=>{
  for(const message of messages){
    const h=await harness([message]);
    try{
      submit(h);await flush();
      const doc=h.dom.window.document;
      assert.equal(doc.getElementById('status').textContent,message);
      assert.equal(doc.getElementById('master-key-restore').disabled,false);
      assert.equal(doc.getElementById('master-key-restore-input').value,'');
      assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-status').length,1);
      submit(h);await flush();
      assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-restore').length,2);
      assert.match(doc.getElementById('master-key-status').textContent,/已就绪/);
      assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-status').length,2);
    }finally{h.close();}
  }
});

test('unknown storage or component result still fences retry until a fresh status read',async()=>{
  for(const message of ['未取得成功确认，请刷新核对配置与版本后重试。','synthetic-unexpected-fault']){
    const h=await harness([message]);
    try{
      submit(h);await flush();
      const doc=h.dom.window.document;
      assert.equal(doc.getElementById('status').textContent,'未取得成功确认，请刷新核对配置与版本后重试。');
      assert.equal(doc.getElementById('master-key-restore').disabled,true);
      doc.getElementById('refresh').click();await flush();
      assert.equal(doc.getElementById('master-key-restore').disabled,false);
      assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-status').length,2);
    }finally{h.close();}
  }
});

test('revision conflict and lost management authority do not enter known-refusal path',async()=>{
  for(const message of ['配置版本冲突，请刷新并重新核对后修改。','admin_authorization_denied']){
    const h=await harness([message]);
    try{
      submit(h);await flush();
      const button=h.dom.window.document.getElementById('master-key-restore');
      assert.ok(button===null||button.disabled);
      assert.notEqual(h.dom.window.document.getElementById('status').textContent,messages[2]);
    }finally{h.close();}
  }
});

test('uncertain reset result clears consent and stays fenced until a fresh status read',async()=>{
  const h=await harness(['synthetic-unexpected-fault']);
  try{
    const doc=h.dom.window.document,consent=doc.getElementById('master-key-reset-consent'),reset=doc.getElementById('master-key-reset');
    consent.click();await flush();assert.equal(reset.disabled,false);
    reset.click();await flush();
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,1);
    assert.equal(consent.checked,false);assert.equal(reset.disabled,true);
    assert.equal(doc.getElementById('status').textContent,'未取得成功确认，请刷新核对配置与版本后重试。');
    doc.getElementById('refresh').click();await flush();
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-status').length,2);
    assert.equal(consent.checked,false);assert.equal(reset.disabled,true);
    consent.click();await flush();assert.equal(reset.disabled,false);
  }finally{h.close();}
});

test('reset authorization loss revokes the page confirmation without a second write',async()=>{
  const h=await harness(['admin_authorization_denied']);
  try{
    const doc=h.dom.window.document,consent=doc.getElementById('master-key-reset-consent');
    consent.click();await flush();doc.getElementById('master-key-reset').click();await flush();
    assert.equal(h.calls.filter(c=>c.endpoint==='admin/master-key-reset').length,1);
    assert.equal(doc.getElementById('master-key-reset-consent'),null);
    assert.equal(doc.getElementById('master-key-reset'),null);
    assert.match(doc.getElementById('status').textContent,/管理授权不可用/);
  }finally{h.close();}
});
