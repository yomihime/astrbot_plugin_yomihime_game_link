import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule,root} from './compile.mjs';
import {catalog,success,selection,deferred,flush} from './fixtures.mjs';
const {createShell}=await sourceModule(root+'pages/frontend/src/controller.ts');
const {contextBoundary,createScope}=await sourceModule(root+'pages/frontend/src/lifecycle.ts');
const {validateCatalog}=await sourceModule(root+'pages/frontend/src/catalog.ts');
const {createQueryController,STOP_MESSAGE}=await sourceModule(root+'modules/ff14/pages/src/query-controller.ts');
const {skipToMain}=await sourceModule(root+'pages/frontend/src/navigation.ts');
const {createSelectA11y}=await sourceModule(root+'modules/ff14/pages/src/select-a11y.ts');
test('select ARIA follows only a present pending option and disconnects when its owner unmounts',async()=>{
 const dom=new JSDOM('<div id="container"><div class="n-select"><div class="n-base-selection-label" tabindex="0"></div></div><div id="menu"><div id="option-a" role="option" class="n-base-select-option--pending"></div><div id="option-b" role="option"></div></div></div>');
 const container=dom.window.document.getElementById('container'),element=container.querySelector('.n-select'),selection=element.firstElementChild;
 const directive=createSelectA11y(container),attrs={id:'selection',labelId:'label',menuId:'menu',expanded:true,invalid:false,errorId:'error'};
 directive.mounted(element,{value:attrs});assert.equal(selection.getAttribute('aria-activedescendant'),'option-a');
 container.querySelector('#option-a').className='';container.querySelector('#option-b').className='n-base-select-option--pending';await flush();assert.equal(selection.getAttribute('aria-activedescendant'),'option-b');
 container.querySelector('#option-b').remove();await flush();assert.equal(selection.hasAttribute('aria-activedescendant'),false);
 directive.updated(element,{value:{...attrs,expanded:false}});assert.equal(selection.getAttribute('aria-expanded'),'false');
 directive.beforeUnmount(element);container.querySelector('#menu').remove();await flush();assert.equal(selection.getAttribute('aria-controls'),'menu');dom.window.close();
});
test('explicit skip focuses without implicit scroll then reveals the main start',()=>{
 const dom=new JSDOM('<main tabindex="-1"></main>'),main=dom.window.document.querySelector('main'),calls=[];
 main.focus=options=>calls.push(['focus',options]);main.scrollIntoView=options=>calls.push(['scroll',options]);
 skipToMain(null);assert.equal(calls.length,0);skipToMain(main);
 assert.deepEqual(calls,[['focus',{preventScroll:true}],['scroll',{block:'start',inline:'nearest',behavior:'instant'}]]);dom.window.close();
});
function harness({data=catalog(),loader,initialContext,readyTask,expectedPage='shell',assetEvent=()=> 'load'}={}) {
  const dom=new JSDOM('<template id="module-style-assets"></template><div id="container"></div>',{url:'http://localhost/#/module/ff14%2Fff14/items'}),window=dom.window,container=window.document.getElementById('container');
  for(const module of data.modules || []) for(const page of module.pages) for(const path of page.styles) {const link=window.document.createElement('link');link.rel='stylesheet';link.dataset.resource=path;link.setAttribute('href','./'+path+'?fixture-authorized');window.document.getElementById('module-style-assets').content.append(link);}
  const styleRequests=[],imports=[],append=window.document.head.append.bind(window.document.head);
  window.document.head.append=(...nodes)=>{append(...nodes);for(const node of nodes)if(node.matches?.('link[data-module-style]')){styleRequests.push(node);const event=assetEvent(node);if(event)queueMicrotask(()=>node.dispatchEvent(new window.Event(event)));}};
  const gets=[],posts=[],mounts=[]; let callback;
  window.scrollTo=()=>{};
  const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',binding:1,isDark:false,locale:'zh-CN'};
  const bridge={ready:()=>readyTask?.promise || Promise.resolve(initialContext === undefined ? context : initialContext),onContext(fn){callback=fn;return()=>{callback=null;};},apiGet(path){const p=deferred();gets.push({path,...p});return p.promise;},apiPost(path,body){const p=deferred();posts.push({path,body,...p});return p.promise;}};
  const loadPage=loader||(async(entry)=>({mount(view,options){const input=window.document.createElement('input');input.value='draft';view.append(input);const mount={view,input,options,updates:[],disposed:0};mounts.push(mount);return{update(context){mount.updates.push(context);},dispose(){mount.disposed++;view.replaceChildren();}};}}));
  const shell=createShell({bridge,container,window,expectedPage,loadPage:entry=>{imports.push(entry);return loadPage(entry);},changed(){},timeoutMs:100}); shell.start();
  return {shell,dom,container,gets,posts,mounts,styleRequests,imports,context,emit:c=>callback?.(c),async start(){await flush();gets[0].resolve(data);await flush();},close(){shell.dispose();dom.window.close();}};
}
test('catalog rejects traversal undeclared assets inactive pages and duplicates; second module is data-only',()=>{
  const second=catalog('micro/demo');assert.equal(validateCatalog(second).modules[0].pages.length,2);
  for(const mutate of [c=>c.modules[0].pages[0].entry='https://example.com/x.js',c=>c.modules[0].pages[0].entry='module-assets/ff14/ff14/pages/../evil.js',c=>c.modules[0].pages[0].entry='module-assets/ff14/ff14/pages/dist/unknown.js',c=>c.modules[0].state='disabled',c=>c.modules.push({...c.modules[0]})]) {const c=catalog();mutate(c);assert.throws(()=>validateCatalog(c));}
});
test('unknown context lifecycle values are never silently erased',()=>{
  assert.equal(contextBoundary({binding:1,isDark:false,locale:'zh'}),contextBoundary({binding:1,isDark:true,locale:'en'}));
  for(const v of [undefined,NaN,Infinity,-0,new Date(),[1,,3],Object.assign([1],{extra:2})])assert.throws(()=>contextBoundary({binding:v}));
});
test('scope disposes every resource once and preserves cleanup failure',()=>{const callbacks=[];const scope=createScope(()=>true);let bad=true;scope.onDispose(()=>{callbacks.push(1);if(bad)throw Error('bad');});scope.onDispose(()=>callbacks.push(2));assert.throws(()=>scope.dispose(),/cleanup_pending/);bad=false;scope.dispose();assert.deepEqual(callbacks,[1,2,1]);assert.equal(scope.isCurrent(),false);assert.equal(scope.signal.aborted,true);});
test('duplicate/theme/locale contexts retain mount draft selection focus and issue no reads',async()=>{
  const h=harness();try {await h.start();const first=h.mounts[0];first.input.focus();first.input.setSelectionRange(1,3);h.emit({...h.context,isDark:true,locale:'en-US'});await flush();assert.equal(h.mounts.length,1);assert.equal(h.gets.length,1);assert.equal(h.dom.window.document.activeElement,first.input);assert.equal(first.input.selectionStart,1);assert.equal(first.updates.at(-1).theme,'dark');}finally{h.close();}
});
test('refresh pending/failure keeps same mount and labels stale gate; manual recovery does not remount',async()=>{
  const h=harness();try {await h.start();const first=h.mounts[0];first.input.focus();first.input.setSelectionRange(1,3);h.dom.window.document.documentElement.scrollTop=240;const p=h.shell.refresh();assert.equal(first.updates.at(-1).available,false);assert.equal(h.shell.state.stale,true);h.gets[1].reject(Error('private failure'));await p;assert.equal(h.dom.window.document.activeElement,first.input);assert.equal(h.mounts.length,1);assert.equal(h.shell.state.stale,true);await assert.rejects(first.options.services.invoke('item.lookup',{query:'44091'}));const retry=h.shell.refresh();h.gets[2].resolve(catalog());await retry;assert.equal(h.mounts.length,1);assert.equal(first.updates.at(-1).available,true);assert.equal(first.input.value,'draft');assert.equal(first.input.selectionStart,1);assert.equal(first.input.selectionEnd,3);assert.equal(h.dom.window.document.documentElement.scrollTop,240);}finally{h.close();}
});
test('epoch/runtime changes fence pending request and delayed imports; disable clears styles and module DOM',async()=>{
  const h=harness();try {await h.start();const first=h.mounts[0];const pending=first.options.services.invoke('item.lookup',{query:'44091'});const p=h.shell.refresh();h.gets[1].resolve(catalog('ff14/ff14',2,'runtime-2'));await p;h.posts[0].resolve(success);await assert.rejects(pending,/revoked/);assert.equal(first.disposed,1);assert.equal(h.mounts.length,1);assert.match(h.shell.state.message,/\u5bbf\u4e3b\u63d2\u4ef6\u8be6\u60c5\u91cd\u65b0\u6253\u5f00/);const next=catalog();Object.assign(next.modules[0],{state:'disabled',enabled:false,lifecycle:'stopped',runtime_id:null,pages:[],resources:[]});const disable=h.shell.refresh();h.gets[2].resolve(next);await disable;assert.equal(h.container.children.length,0);}finally{h.close();}
  const importTask=deferred(),slow=harness({loader:()=>importTask.promise});try{await slow.start();slow.emit({...slow.context,binding:2});await flush();assert.equal(slow.gets.length,1);await flush();let calls=0;importTask.resolve({mount(){calls++;return{update(){},dispose(){}};}});await flush();assert.equal(calls,0);}finally{slow.close();}
});
test('generic micro module new page route mounts with no shell module branch; restricted services deny wrong capability',async()=>{
  const data=catalog('micro/demo');data.modules[0].pages=[{...data.modules[0].pages[0],route_id:'inspect',title:'Inspect',capability_id:'demo.inspect'}];const h=harness({data});try {h.dom.window.location.hash='#/module/micro%2Fdemo/inspect';await h.start();assert.equal(h.mounts[0].options.routeId,'inspect');await assert.rejects(h.mounts[0].options.services.invoke('item.lookup',{}));const request=h.mounts[0].options.services.invoke('demo.inspect',{sample:1});assert.equal(h.posts[0].path,'invoke');assert.deepEqual(h.posts[0].body,{owner:'micro/demo',page:'inspect',capability_id:'demo.inspect',parameters:{sample:1}});h.posts[0].resolve(success);await request;}finally{h.close();}
});
function queryHarness(route='market',timeoutMs=100) {const requests=[],scope=createScope(()=>true),context={owner:'ff14/ff14',runtimeId:'run',epoch:1,boundary:'b',theme:'light',locale:'zh-CN',available:true};const controller=createQueryController({routeId:route,context,scope,services:{invoke(cap,body,endpoint){const p=deferred();requests.push({cap,body,endpoint,...p});return p.promise;}}},()=>{},timeoutMs);return {controller,requests,scope,context};}
for(const failure of ['css','js'])test(`late A ${failure} failure revokes A without replacing healthy selected B status or view`,async()=>{
  const data=catalog();data.modules.push(catalog('micro/demo').modules[0]);
  const late=deferred(),views=[];
  const h=harness({data,assetEvent:link=>failure==='css'&&link.dataset.moduleStyle==='ff14/ff14'?null:'load',loader:entry=>{
    if(entry.includes('ff14/ff14'))return late.promise;
    return Promise.resolve({mount(view,options){const input=view.ownerDocument.createElement('input');input.value='B draft';view.append(input);const record={view,input,options,updates:[]};views.push(record);return{update(context){record.updates.push(context);},dispose(){view.replaceChildren();}};}});
  }});
  try{
    await h.start();h.shell.select('micro/demo','items');await new Promise(resolve=>setTimeout(resolve,10));await flush();
    const b=views[0];assert.ok(b);b.input.focus();b.input.setSelectionRange(1,3);const message=h.shell.state.message;
    if(failure==='css'){late.resolve({mount(){throw Error('A must never mount');}});h.styleRequests.find(link=>link.dataset.moduleStyle==='ff14/ff14').dispatchEvent(new h.dom.window.Event('error'));}
    else late.reject(Error('expired A JS'));
    await flush();assert.equal(h.shell.state.message,message);assert.equal(h.shell.state.mounted,true);assert.equal(h.container.firstElementChild,b.view);assert.equal(h.dom.window.document.activeElement,b.input);assert.equal(b.input.selectionStart,1);assert.equal(b.input.value,'B draft');assert.equal(b.updates.at(-1)?.available ?? b.options.context.available,true);
    assert.equal(h.dom.window.document.head.querySelector('link[data-module-style="ff14/ff14"]'),null);
    const imports=h.imports.length;h.shell.select('ff14/ff14','items');await flush();assert.match(h.shell.state.message,/重新打开/);assert.equal(h.shell.state.mounted,false);assert.equal(h.imports.length,imports);assert.equal(h.container.children.length,0);
  }finally{h.close();}
});
test('module request and candidate use exact old body contract; edit stops old continuation',async()=>{
  const h=queryHarness();try {h.controller.edit('query','犎牛牛排');h.controller.edit('server','1043');const p=h.controller.submit();assert.deepEqual(JSON.parse(h.requests[0].body.input),{query:'犎牛牛排',quality:'all',intent:'overview',server:'1043'});h.requests[0].resolve(selection);await p;const original=h.controller.state.result;const candidate=h.controller.choose(44091);assert.deepEqual(JSON.parse(h.requests[1].body.input),{selection:{batch_id:'batch-1',generation:'generation-1',item_id:44091}});h.requests[1].resolve(success);await candidate;h.controller.edit('query','new');h.controller.choose(8,original);assert.equal(h.requests.length,2);}finally{h.scope.dispose();}
});
test('theme/context update preserves pending request and results; stop and disposal fence late response',async()=>{
  const h=queryHarness('items');try {h.controller.edit('query','44091');const p=h.controller.submit();h.controller.update({...h.context,theme:'dark',locale:'en'});assert.equal(h.controller.state.phase,'loading');assert.equal(h.requests.length,1);h.requests[0].resolve(success);await p;assert.equal(h.controller.state.result.status,'partial_success');const p2=h.controller.submit();h.controller.stop();assert.equal(h.controller.state.message,STOP_MESSAGE);h.requests[1].resolve(success);await p2;assert.equal(h.controller.state.result,null);const p3=h.controller.submit();h.scope.dispose();h.requests[2].resolve(success);await p3;assert.equal(h.controller.state.result,null);}finally{h.scope.dispose();}
});
test('query deadline permits manual recovery and no automatic post; invalid input never posts',async()=>{
  const h=queryHarness('items',15);try {h.controller.edit('query','');await h.controller.submit();assert.equal(h.requests.length,0);h.controller.edit('query','44091');const p=h.controller.submit();await new Promise(resolve=>setTimeout(resolve,25));assert.equal(h.controller.state.phase,'timeout');h.requests[0].resolve(success);await p;assert.equal(h.controller.state.result,null);assert.equal(h.requests.length,1);const retry=h.controller.submit();h.requests[1].resolve(success);await retry;assert.equal(h.controller.state.result.status,'partial_success');}finally{h.scope.dispose();}
});

test('draft persistence is isolated by true context boundary runtime and epoch',()=>{const h=queryHarness('items');h.controller.edit('query','private previous draft');h.scope.dispose();const scope=createScope(()=>true);const next=createQueryController({routeId:'items',context:{...h.context,boundary:'new',runtimeId:'new-runtime',epoch:2},scope,services:{invoke(){throw Error('unused');}}},()=>{});assert.equal(next.state.draft.query,'');scope.dispose();});
test('wrong plugin/page contexts fence active view and late ready cannot restore it',async()=>{const h=harness();try {await h.start();h.emit({...h.context,pageName:'management'});await flush();assert.equal(h.shell.state.mounted,false);assert.equal(h.container.children.length,0);assert.equal(h.gets.length,1);}finally{h.close();}});
test('canonical default binding accepts its own name and rejects shell/default cross-binding',async()=>{
  const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',binding:1};
  const valid=harness({expectedPage:'00-game-link',initialContext:context});try{await valid.start();assert.equal(valid.shell.state.mounted,true);}finally{valid.close();}
  for(const [expectedPage,pageName]of [['00-game-link','shell'],['shell','00-game-link']]){const bad=harness({expectedPage,initialContext:{...context,pageName}});try{await flush();assert.equal(bad.gets.length,0);assert.equal(bad.shell.state.mounted,false);assert.equal(bad.imports.length,0);}finally{bad.close();}}
});

test('HTML-projected authorized style is cloned unchanged and removed by scope',async()=>{const h=harness();try {await h.start();const source=h.dom.window.document.getElementById('module-style-assets').content.querySelector('link'),clone=h.dom.window.document.head.querySelector('link[data-module-style]');assert.notEqual(source,clone);assert.equal(clone.getAttribute('href'),source.getAttribute('href'));h.shell.dispose();assert.equal(h.dom.window.document.head.querySelector('link[data-module-style]'),null);assert.ok(source.parentNode);}finally{h.close();}});
test('cleanup failure retains owner retry and blocks replacement mounting',async()=>{let fail=true,disposed=0;const h=harness({loader:async()=>({mount(){return{update(){},dispose(){disposed++;if(fail)throw Error('fixture-cleanup');}};}})});try {await h.start();const p=h.shell.refresh();h.gets[1].resolve(catalog('ff14/ff14',2));await p;assert.equal(h.shell.state.cleanupPending,true);assert.equal(h.shell.state.mounted,false);fail=false;h.shell.retryCleanup();await flush();assert.equal(h.shell.state.cleanupPending,false);assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/\u5bbf\u4e3b\u63d2\u4ef6\u8be6\u60c5\u91cd\u65b0\u6253\u5f00/);assert.equal(disposed,2);}finally{h.close();}});
test('capability cannot be routed to another page endpoint and refresh fences in-flight query',async()=>{const h=harness();try {await h.start();const services=h.mounts[0].options.services;await assert.rejects(services.invoke('market.query',{}));assert.equal(h.posts.length,0);const q=services.invoke('item.lookup',{query:'44091'});const p=h.shell.refresh();h.posts[0].resolve(success);await assert.rejects(q,/revoked/);h.gets[1].resolve(catalog());await p;assert.equal(h.posts.length,1);}finally{h.close();}});

test('route navigation focuses main with preventScroll then explicitly scrolls to top',async()=>{const h=harness();try{await h.start();const main=h.dom.window.document.createElement('main');main.id='page-root';main.tabIndex=-1;h.dom.window.document.body.append(main);const focusArgs=[],scrollArgs=[];const focus=main.focus.bind(main);main.focus=options=>{focusArgs.push(options);focus(options);};h.dom.window.scrollTo=options=>scrollArgs.push(options);h.shell.select('ff14/ff14','market');await new Promise(resolve=>setTimeout(resolve,10));assert.equal(h.dom.window.document.activeElement,main);assert.deepEqual(focusArgs.at(-1),{preventScroll:true});assert.deepEqual(scrollArgs.at(-1),{top:0,left:0,behavior:'instant'});}finally{h.close();}});

test('refresh completion cannot revive a query started before refresh began',async()=>{const h=queryHarness('items');try{h.controller.edit('query','44091');const pending=h.controller.submit();h.controller.update({...h.context,available:false});assert.equal(h.controller.state.phase,'idle');assert.equal(h.controller.state.draft.query,'44091');h.controller.update({...h.context,available:true});h.requests[0].resolve(success);await pending;assert.equal(h.controller.state.result,null);assert.equal(h.requests.length,1);const retry=h.controller.submit();h.requests[1].resolve(success);await retry;assert.equal(h.controller.state.result.status,'partial_success');}finally{h.scope.dispose();}});

test('missing page binding fails closed through ready and onContext; late ready cannot undo the event',async()=>{
 const valid={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',binding:1};
 const invalid=[{}, {pluginName:valid.pluginName}, {pageName:'shell'}, {...valid,pluginName:null}, {...valid,pageName:undefined}, {...valid,pluginName:4}, {...valid,pageName:'management'}];
 for(const context of invalid){const h=harness({initialContext:context});try{await flush();assert.equal(h.gets.length,0);assert.equal(h.mounts.length,0);assert.equal(h.posts.length,0);assert.equal(h.shell.state.mounted,false);}finally{h.close();}}
 for(const context of invalid){const readyTask=deferred(),h=harness({readyTask});try{h.emit(context);readyTask.resolve(valid);await flush();assert.equal(h.gets.length,0);assert.equal(h.mounts.length,0);assert.equal(h.posts.length,0);}finally{h.close();}}
 const h=harness();try{await h.start();h.emit({binding:1});await flush();assert.equal(h.shell.state.mounted,false);assert.equal(h.gets.length,1);}finally{h.close();}
});
test('failed scope callback is retried while successful callbacks are not repeated',()=>{const scope=createScope(()=>true);let success=0,attempts=0,fail=true;scope.onDispose(()=>success++);scope.onDispose(()=>{attempts++;if(fail)throw Error('keep-owned-resource');});assert.throws(()=>scope.dispose(),/cleanup_pending/);assert.equal(scope.isCurrent(),false);fail=false;scope.dispose();scope.dispose();assert.equal(success,1);assert.equal(attempts,2);});

test('resource registered after revocation retains a failed cleanup for retry',()=>{const scope=createScope(()=>true);scope.dispose();let fail=true,attempts=0;assert.throws(()=>scope.onDispose(()=>{attempts++;if(fail)throw Error('late-owned-cleanup');}));fail=false;scope.dispose();scope.dispose();assert.equal(attempts,2);assert.equal(scope.isCurrent(),false);});
test('missing binding revokes an active query and delayed import even when names previously matched',async()=>{
 const h=harness();try{await h.start();const pending=h.mounts[0].options.services.invoke('item.lookup',{query:'44091'});h.emit({pluginName:h.context.pluginName,binding:1});h.posts[0].resolve(success);await assert.rejects(pending,/revoked/);assert.equal(h.gets.length,1);assert.equal(h.shell.state.mounted,false);}finally{h.close();}
 const task=deferred(),slow=harness({loader:()=>task.promise});try{await slow.start();slow.emit({pageName:'shell',binding:1});let mounts=0;task.resolve({mount(){mounts++;return{update(){},dispose(){}};}});await flush();assert.equal(mounts,0);assert.equal(slow.gets.length,1);}finally{slow.close();}
});

test('shell DOM cleanup failure retains its stage without repeating successful page cleanup',async()=>{const h=harness();try{await h.start();const first=h.mounts[0],replace=h.container.replaceChildren.bind(h.container);let fail=true;h.container.replaceChildren=(...nodes)=>{if(fail)throw Error('deterministic-shell-DOM-cleanup');replace(...nodes);};h.shell.select('ff14/ff14','market');await flush();assert.equal(h.shell.state.cleanupPending,true);assert.equal(h.mounts.length,1);assert.equal(first.disposed,1);fail=false;h.shell.retryCleanup();await flush();assert.equal(h.shell.state.cleanupPending,false);assert.equal(h.mounts.length,2);assert.equal(first.disposed,1);}finally{h.close();}});

test('expired authorization never reloads already loaded owner CSS or shared items/market entry',async()=>{
 let now=0;const data=catalog();data.modules.push(...catalog('micro/demo').modules);
 const h=harness({data,assetEvent:()=>now<60000?'load':'error'});
 try {
  await h.start();const ffStyle=h.styleRequests[0],opaque=ffStyle.getAttribute('href');
  now=101574;h.shell.select('ff14/ff14','market');await flush();
  assert.equal(h.shell.state.mounted,true);assert.equal(h.mounts.at(-1).options.routeId,'market');
  assert.equal(h.styleRequests.length,1);assert.equal(h.imports.length,1);assert.equal(ffStyle.isConnected,true);assert.equal(ffStyle.getAttribute('href'),opaque);
  // This owner's first CSS load happens after authorization expires.
  h.shell.select('micro/demo','items');await flush();assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
  const requests=h.styleRequests.length,imports=h.imports.length;
  h.shell.select('micro/demo','market');await flush();const refresh=h.shell.refresh();h.gets[1].resolve(data);await refresh;
  assert.equal(h.styleRequests.length,requests);assert.equal(h.imports.length,imports);assert.equal(h.posts.length,0);
  h.shell.select('ff14/ff14','items');await flush();assert.equal(h.shell.state.mounted,true);assert.equal(ffStyle.isConnected,true);assert.equal(h.imports.length,imports);
 } finally {h.close();}
});

test('CSS success requires a load event; error with a sheet pointer and missing event both fail closed',async()=>{
 for(const event of ['error',null]) {
  const h=harness({assetEvent:node=>{Object.defineProperty(node,'sheet',{value:{}});return event;}});
  try {await h.start();if(event===null){assert.equal(h.shell.state.mounted,false);await new Promise(resolve=>setTimeout(resolve,125));await flush();}
   assert.equal(h.mounts.length,0);assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
   const count=h.styleRequests.length;h.shell.select('ff14/ff14','market');await flush();assert.equal(h.styleRequests.length,count);assert.equal(h.posts.length,0);
  } finally {h.close();}
 }
});

test('late first JS rejection retires only its owner and cannot retry the old entry',async()=>{
 let now=0;const data=catalog();data.modules.push(...catalog('micro/demo').modules);
 const h=harness({data,loader:async entry=>{if(now>=60000)throw Error('expired-resource');return{mount(){return{update(){},dispose(){}};}};}});
 try {await h.start();const first=h.styleRequests[0];now=61000;h.shell.select('micro/demo','items');await flush();assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
  h.shell.select('micro/demo','market');await flush();assert.equal(h.imports.length,2);
  h.shell.select('ff14/ff14','market');await flush();assert.equal(h.shell.state.mounted,true);assert.equal(h.imports.length,2);assert.equal(first.isConnected,true);
 } finally {h.close();}
});

test('catalog invalidation of an unselected owner cleans its assets while the other owner retains focus and mount',async()=>{
 const data=catalog();data.modules.push(...catalog('micro/demo').modules);const h=harness({data});
 try {await h.start();const first=h.styleRequests[0];h.shell.select('micro/demo','items');await flush();const second=h.mounts.at(-1),secondStyle=h.styleRequests[1];second.input.focus();second.input.setSelectionRange(1,3);
  const next=structuredClone(data);Object.assign(next.modules[0],{state:'disabled',runtime_id:null,pages:[],resources:[]});const p=h.shell.refresh();h.gets[1].resolve(next);await p;
  assert.equal(first.isConnected,false);assert.equal(secondStyle.isConnected,true);assert.equal(h.mounts.length,2);assert.equal(second.options.scope.isCurrent(),true);assert.equal(h.dom.window.document.activeElement,second.input);assert.equal(second.input.selectionStart,1);assert.equal(h.posts.length,0);
  h.shell.select('ff14/ff14','items');await flush();assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
 } finally {h.close();}
});

test('runtime epoch version or resource contract changes invalidate old projection; only a new document restores it',async()=>{
 for(const mutate of [m=>m.module_epoch++,m=>m.runtime_id='runtime-new',m=>m.asset_version='b'.repeat(64),m=>m.resources[0].sha256='c'.repeat(64)]) {
  const h=harness();let fresh;
  try {await h.start();const original=h.mounts[0],style=h.styleRequests[0],next=catalog();mutate(next.modules[0]);const p=h.shell.refresh();h.gets[1].resolve(next);await p;
   assert.equal(original.options.scope.isCurrent(),false);assert.equal(style.isConnected,false);assert.equal(h.mounts.length,1);assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
   h.shell.select('ff14/ff14','market');await flush();const retry=h.shell.refresh();h.gets[2].resolve(next);await retry;assert.equal(h.imports.length,1);assert.equal(h.styleRequests.length,1);assert.equal(h.posts.length,0);
   fresh=harness({data:next});await fresh.start();assert.equal(fresh.shell.state.mounted,true);assert.equal(fresh.styleRequests.length,1);
  } finally {h.close();fresh?.close();}
 }
});

test('asset cleanup failure keeps exact owner node for retry and fences delayed import permanently',async()=>{
 const task=deferred(),h=harness({loader:()=>task.promise});
 try {await h.start();const style=h.styleRequests[0],remove=style.remove.bind(style);let fail=true,attempts=0,calls=0;
  style.remove=()=>{attempts++;if(fail)throw Error('owned-style-cleanup');remove();};
  const p=h.shell.refresh();h.gets[1].resolve(catalog('ff14/ff14',2));await p;
  assert.equal(h.shell.state.cleanupPending,true);assert.equal(style.isConnected,true);assert.equal(attempts,1);
  h.shell.retryCleanup();assert.equal(attempts,2);assert.equal(h.shell.state.cleanupPending,true);
  task.resolve({mount(){calls++;return{update(){},dispose(){}};}});await flush();assert.equal(calls,0);
  fail=false;h.shell.retryCleanup();await flush();assert.equal(attempts,3);assert.equal(style.isConnected,false);assert.equal(h.shell.state.cleanupPending,false);assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);assert.equal(h.imports.length,1);
 } finally {h.close();}
});

test('context boundary revokes every owner once and cannot recover through later context or catalog refresh',async()=>{
 const data=catalog();data.modules.push(...catalog('micro/demo').modules);const h=harness({data});let fresh;
 try {await h.start();h.shell.select('micro/demo','items');await flush();const active=h.mounts.at(-1),query=active.options.services.invoke('item.lookup',{});
  h.emit({...h.context,binding:2});await flush();assert.ok(h.styleRequests.every(link=>!link.isConnected));assert.equal(active.options.scope.isCurrent(),false);h.posts[0].resolve(success);await assert.rejects(query,/revoked/);
  h.emit({...h.context,binding:2});await h.shell.refresh();h.shell.select('ff14/ff14','market');await flush();assert.equal(h.gets.length,1);assert.equal(h.imports.length,2);assert.equal(h.shell.state.mounted,false);assert.match(h.shell.state.message,/宿主插件详情重新打开/);
  fresh=harness({data,initialContext:{...h.context,binding:2}});await fresh.start();assert.equal(fresh.shell.state.mounted,true);
 } finally {h.close();fresh?.close();}
});

test('route change while the shared entry imports lets only the current view mount',async()=>{
 const task=deferred(),h=harness({loader:()=>task.promise});let calls=0;
 try {await h.start();h.shell.select('ff14/ff14','market');await flush();assert.equal(h.imports.length,1);assert.equal(h.styleRequests.length,1);
  task.resolve({mount(_view,options){calls++;assert.equal(options.routeId,'market');return{update(){},dispose(){}};}});await flush();assert.equal(calls,1);assert.equal(h.shell.state.mounted,true);
 } finally {h.close();}
});

test('failed asset cleanup for an unselected owner preserves another owner availability and navigation',async()=>{
 const data=catalog();data.modules.push(...catalog('micro/demo').modules);const h=harness({data});let fail=true;
 try {await h.start();const style=h.styleRequests[0],remove=style.remove.bind(style);style.remove=()=>{if(fail)throw Error('retained-other-owner');remove();};
  h.shell.select('micro/demo','items');await flush();const current=h.mounts.at(-1),next=structuredClone(data);next.modules[0].module_epoch++;
  const refresh=h.shell.refresh();h.gets[1].resolve(next);await refresh;assert.equal(h.shell.state.cleanupPending,true);assert.equal(current.updates.at(-1).available,true);assert.equal(current.options.scope.isCurrent(),true);
  const query=current.options.services.invoke('item.lookup',{});h.posts[0].resolve(success);assert.equal(await query,success);
  h.shell.select('micro/demo','market');await flush();assert.equal(h.shell.state.mounted,true);assert.equal(h.mounts.at(-1).options.context.available,true);assert.equal(style.isConnected,true);
  fail=false;h.shell.retryCleanup();await flush();assert.equal(style.isConnected,false);assert.equal(h.shell.state.cleanupPending,false);assert.equal(h.shell.state.mounted,true);
 } finally {fail=false;h.shell.retryCleanup();h.close();}
});

test('fixed settings route remains reachable with empty catalog and catalog failure',async()=>{
 for(const failure of [false,true]){const data=catalog();data.modules=[];const h=harness({data});try{await flush();if(failure)h.gets[0].reject(Error('catalog_unavailable'));else h.gets[0].resolve(data);await flush();h.shell.selectSettings();await flush();assert.equal(h.shell.state.settings,true);assert.equal(h.dom.window.location.hash,'#/settings');assert.equal(h.shell.selected(),null);assert.equal(h.mounts.length,0);}finally{h.close();}}
});
test('generic services bind same local route to exact two owners',async()=>{
 const data=catalog('micro/first');data.modules.push(...catalog('micro/second').modules);const h=harness({data});try{await h.start();for(const owner of ['micro/first','micro/second']){h.shell.select(owner,'items');await flush();const p=h.mounts.at(-1).options.services.invoke('item.lookup',{query:'44091'}),request=h.posts.at(-1);assert.deepEqual(request.body,{owner,page:'items',capability_id:'item.lookup',parameters:{query:'44091'}});assert.equal(request.path,'invoke');request.resolve(success);await p;}}finally{h.close();}
});
test('Logs and calendar use module-owned capability inputs and retain manual error recovery',async()=>{
 for(const [route,expected] of [['logs',{realm:'cn',server:'白银乡',character:'测试角色',metric:'rdps'}],['calendar',{region:'cn',days:7,timezone:'Asia/Shanghai'}]]){const h=queryHarness(route);try{if(route==='logs'){h.controller.edit('server','白银乡');h.controller.edit('character','测试角色');}const p=h.controller.submit();assert.deepEqual(h.requests[0].body,expected);assert.equal(h.requests[0].cap,route==='logs'?'ff14.logs.character':'ff14.calendar.query');h.requests[0].reject(Error('upstream'));await p;assert.equal(h.controller.state.phase,'error');assert.equal(h.requests.length,1);const retry=h.controller.submit();h.requests[1].resolve(success);await retry;assert.equal(h.controller.state.result.status,'partial_success');}finally{h.scope.dispose();}}
});
