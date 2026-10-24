import {test} from 'node:test';
import assert from 'node:assert/strict';
import {harness,flush} from './single-root-fixture.mjs';
test('source single root separates home, module lifecycle, global Core settings and role pages',async()=>{
 const h=await harness();try{
  assert.match(h.doc.getElementById('page-title').textContent,/游戏工作台/);assert.equal(h.doc.querySelector('[data-module-role]'),null);
  await h.go('#/module/ff14%2Fff14');assert.ok(h.doc.querySelector('[data-module-role=home]'));assert.equal(h.doc.querySelector('.query-links'),null);
  await h.go('#/module/ff14%2Fff14/config');assert.ok(h.doc.querySelector('[data-module-role=config]'));assert.equal(h.doc.querySelector('.query-links'),null);
  await h.go('#/module/ff14%2Fff14/-/queries');assert.equal(h.doc.querySelector('[data-module-role]'),null);assert.equal(h.doc.querySelectorAll('.query-links a').length,1);
  await h.go('#/settings');assert.ok(h.doc.querySelector('[data-field=sample]'));assert.equal(h.doc.querySelector('[data-field=not_global]'),null);assert.ok(h.doc.querySelector('[data-legacy-policy="example.copy"]'));assert.equal(h.calls.some(call=>call.action.startsWith('admin/credential-')),false);
  await h.go('#/modules');assert.equal(h.doc.querySelector('.module-dashboard').hidden,false);assert.match(h.doc.getElementById('page-title').textContent,/模块管理/);
 }finally{h.close();}
});
test('Core navigation confirmation cancels safely and re-entry preserves ordinary draft',async()=>{
 const h=await harness();try{
  await h.go('#/settings');const input=h.doc.querySelector('[data-field=sample] [data-role=value]');input.value='new draft';input.dispatchEvent(new h.window.Event('input',{bubbles:true}));await flush();
  await h.go('#/');assert.equal(h.window.location.hash,'#/settings');assert.ok(h.doc.querySelector('[data-confirmation=navigation]'));h.doc.querySelector('[data-action=cancel-navigation]').click();await flush();assert.equal(input.value,'new draft');
  await h.go('#/');h.doc.querySelector('[data-action=confirm-navigation]').click();await flush();assert.equal(h.window.location.hash,'#/');await h.go('#/settings');assert.equal(h.doc.querySelector('[data-field=sample] [data-role=value]').value,'new draft');assert.equal(h.calls.filter(call=>call.action==='admin/update').length,0);
 }finally{h.close();}
});

test('home module entry leads to auxiliary queries and Back returns to module',async()=>{
 const h=await harness();try{
  assert.equal(h.doc.querySelector('.workspace-page-index'),null);
  const entry=h.doc.querySelector('.workspace-module-title h2 a');assert.ok(entry);
  const description=h.doc.getElementById(entry.getAttribute('aria-describedby'));assert.equal(description.textContent,'已加载');
  assert.equal(h.doc.querySelector('.workspace-loaded [tabindex]'),null);
  entry.click();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14');
  const tab=[...h.doc.querySelectorAll('.module-tabs a')].find(a=>a.textContent==='辅助查询');assert.ok(tab);tab.click();await flush();
  const query=h.doc.querySelector('.query-links a');assert.ok(query);const route=query.getAttribute('href');query.click();await flush();assert.equal(h.window.location.hash,route);
  h.window.history.back();await flush();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14/-/queries');
  h.window.history.back();await flush();await flush();assert.equal(h.window.location.hash,'#/module/ff14%2Fff14');
  const modules=h.doc.querySelector('.shell-sidebar a[href="#/modules"]');modules.click();await flush();assert.equal(h.window.location.hash,'#/modules');
 }finally{h.close();}
});

test('home loaded icon has a named description while disabled and unavailable remain text',async()=>{
 const {catalog}=await import('./fixtures.mjs');const data=catalog(),base=data.modules[0];
 for(const state of ['disabled','unavailable'])data.modules.push({...base,module_id:'example/'+state,route:state,state,enabled:false,lifecycle:'stopped',runtime_id:null,pages:[],resources:[]});
 const h=await harness(data);try{
  const articles=[...h.doc.querySelectorAll('.workspace-module')];
  assert.ok(articles[0].querySelector('.workspace-loaded svg circle'));
  assert.ok(articles[0].querySelector('.workspace-loaded svg path'));
  const title=articles[0].querySelector('h2 a');assert.equal(h.doc.getElementById(title.getAttribute('aria-describedby')).textContent,'已加载');
  assert.equal(articles[0].querySelector('.workspace-loaded').hasAttribute('tabindex'),false);
  for(const [index,text] of [[1,'已停用'],[2,'不可用']]){
   assert.equal(articles[index].querySelector('.workspace-loaded'),null);
   assert.equal(articles[index].querySelector('.workspace-state').textContent,text);
   assert.match(articles[index].textContent,/暂无可用功能/);
  }
 }finally{h.close();}
});
