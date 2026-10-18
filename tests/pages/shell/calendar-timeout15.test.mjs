import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {sourceModule,root} from './compile.mjs';
import {success,deferred} from './fixtures.mjs';

const {createQueryController,queryTimeoutMs}=await sourceModule(root+'modules/ff14/pages/src/query-controller.ts');
const {createScope}=await sourceModule(root+'pages/frontend/src/lifecycle.ts');
let serial=0;
function harness(route='calendar',{legacy=false}={}) {
  const requests=[],scope=createScope(()=>true),context={owner:'ff14/ff14',runtimeId:'timeout15-'+(++serial),epoch:1,boundary:'b',theme:'light',locale:'zh-CN',available:true};
  const options={routeId:route,context,scope,services:{invoke(cap,body){const pending=deferred();requests.push({cap,body,...pending});return pending.promise;}}};
  const controller=legacy?createQueryController(options,()=>{}):createQueryController(options,()=>{},queryTimeoutMs(route));
  if(route==='items'||route==='market')controller.edit('query','44091');
  return {controller,requests,scope,context};
}

test('calendar actual entry selects its budget and accepts legal results after35 through45 seconds',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  assert.equal(queryTimeoutMs('calendar'),50000);
  const entry=readFileSync(root+'modules/ff14/pages/src/entry.ts','utf8');
  assert.match(entry,/queryTimeoutMs\(options\.routeId\)/);
  const h=harness();
  try {
    const pending=h.controller.submit();
    assert.equal(h.requests[0].cap,'ff14.calendar.query');
    t.mock.timers.tick(35100);assert.equal(h.controller.state.phase,'loading');
    t.mock.timers.tick(9800);assert.equal(h.controller.state.phase,'loading');
    h.requests[0].resolve(success);await pending;
    assert.equal(h.controller.state.phase,'result');assert.equal(h.requests.length,1);
  }finally{h.scope.dispose();}
});

test('calendar remains bounded at50 and rejects timed-out late result without an automatic retry',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});const h=harness();
  try {
    const pending=h.controller.submit();t.mock.timers.tick(50001);
    assert.equal(h.controller.state.phase,'timeout');assert.match(h.controller.state.message,/50/);
    h.requests[0].resolve(success);await pending;
    assert.equal(h.controller.state.result,null);assert.equal(h.requests.length,1);
    const retry=h.controller.submit();h.requests[1].resolve(success);await retry;
    assert.equal(h.controller.state.phase,'result');assert.equal(h.requests.length,2);
  }finally{h.scope.dispose();}
});

test('noncalendar and legacy constructor retain35-second defaults',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  for(const route of ['items','market','logs'])assert.equal(queryTimeoutMs(route),35000);
  for(const [route,legacy] of [['items',false],['market',false],['calendar',true]]) {
    const h=harness(route,{legacy});try {
      const pending=h.controller.submit();t.mock.timers.tick(35001);
      assert.equal(h.controller.state.phase,'timeout');assert.match(h.controller.state.message,/35/);
      h.requests[0].resolve(success);await pending;assert.equal(h.controller.state.result,null);assert.equal(h.requests.length,1);
    }finally{h.scope.dispose();}
  }
});

test('calendar stop edit refresh epoch and unmount still fence late45-second results',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  for(const mode of ['stop','edit','refresh','epoch','unmount']) {
    const h=harness();try {
      const pending=h.controller.submit();t.mock.timers.tick(36000);assert.equal(h.controller.state.phase,'loading');
      if(mode==='stop')h.controller.stop();
      else if(mode==='edit')h.controller.edit('region','global');
      else if(mode==='refresh')h.controller.update({...h.context,available:false});
      else if(mode==='epoch')h.controller.update({...h.context,epoch:2});
      else h.scope.dispose();
      t.mock.timers.tick(8900);h.requests[0].resolve(success);await pending;
      assert.equal(h.controller.state.result,null);assert.equal(h.requests.length,1);
      t.mock.timers.tick(60000);assert.equal(h.requests.length,1);
    }finally{h.scope.dispose();}
  }
});

test('calendar transport failure after35 keeps error/manual recovery and sends no automatic retry',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});const h=harness();
  try {
    const pending=h.controller.submit();t.mock.timers.tick(40000);
    h.requests[0].reject(Error('controlled transport failure'));await pending;
    assert.equal(h.controller.state.phase,'error');assert.equal(h.controller.state.result,null);
    t.mock.timers.tick(60000);assert.equal(h.requests.length,1);
    const retry=h.controller.submit();h.requests[1].resolve(success);await retry;
    assert.equal(h.controller.state.phase,'result');assert.equal(h.requests.length,2);
  }finally{h.scope.dispose();}
});
