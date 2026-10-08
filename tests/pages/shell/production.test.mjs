import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir,copyFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule,root} from './compile.mjs';
import {catalog,success,selection,flush} from './fixtures.mjs';
const {createScope}=await sourceModule(root+'pages/frontend/src/lifecycle.ts');
const {createShell}=await sourceModule(root+'pages/frontend/src/controller.ts');
const out=resolve(root,'.architecture-refactor/modular-r0-r3/production-module-test');
await mkdir(resolve(out,'module-assets/ff14/ff14/pages/dist'),{recursive:true});
await copyFile(resolve(root,'pages/shell/runtime.js'),resolve(out,'runtime.js'));
await copyFile(resolve(root,'modules/ff14/pages/dist/entry.js'),resolve(out,'module-assets/ff14/ff14/pages/dist/entry.js'));
const dom=new JSDOM('<div id="module"></div>',{pretendToBeVisual:true,url:'http://localhost/'}),window=dom.window;
for(const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','Event','MouseEvent','KeyboardEvent','InputEvent']) globalThis[key]=key==='window'?window:key==='document'?window.document:window[key];
globalThis.getComputedStyle=window.getComputedStyle.bind(window); globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
window.Element.prototype.scrollTo=function(){};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
const runtime=await import(pathToFileURL(resolve(out,'runtime.js')));
const {mount}=await import(pathToFileURL(resolve(out,'module-assets/ff14/ff14/pages/dist/entry.js')));
const container=window.document.getElementById('module');
const context={owner:'ff14/ff14',runtimeId:'production-test',epoch:1,boundary:'bound',theme:'light',locale:'zh-CN',available:true};
test('production select names its actual focus node and tracks popup keyboard state without changing requests',async()=>{
 const scope=createScope(()=>true),posts=[];
 const page=mount(container,{routeId:'market',context:{...context,boundary:'select-a11y'},scope,services:{invoke(...args){posts.push(args);return Promise.resolve(success);}}});
 try {
  const quality=container.querySelector('#ff14-market-quality');assert.ok(quality);assert.equal(quality.tabIndex,0);assert.equal(quality.getAttribute('role'),'combobox');assert.equal(quality.getAttribute('aria-expanded'),'false');
  const label=container.querySelector('#'+quality.getAttribute('aria-labelledby'));assert.equal(label.textContent,'品质');label.click();assert.equal(window.document.activeElement,quality);
  const key=async value=>{quality.dispatchEvent(new window.KeyboardEvent('keydown',{key:value,bubbles:true,cancelable:true}));await runtime.nextTick();await flush();};
  await key(' ');assert.equal(quality.getAttribute('aria-expanded'),'true');
  const menu=container.querySelector('#'+quality.getAttribute('aria-controls'));assert.equal(menu.getAttribute('role'),'listbox');assert.ok(container.contains(menu));
  // JSDOM's virtual list has no measured rows. Never point ARIA at an absent option.
  await key('ArrowDown');assert.equal(quality.hasAttribute('aria-activedescendant'),false);
  await key('Enter');assert.equal(quality.getAttribute('aria-expanded'),'false');assert.equal(quality.hasAttribute('aria-activedescendant'),false);
  page.update({...context,theme:'dark',locale:'en-US'});await runtime.nextTick();assert.equal(container.querySelector('#ff14-market-quality'),quality);assert.equal(window.document.activeElement,quality);assert.equal(posts.length,0);
  await key(' ');assert.equal(quality.getAttribute('aria-expanded'),'true');assert.equal(container.querySelectorAll('#ff14-market-quality').length,1);
 } finally {scope.dispose();page.dispose();assert.equal(container.querySelector('[role=combobox]'),null);assert.equal(container.querySelector('[role=listbox]'),null);}
});
test('production module header rules outrank locked Naive transitions and shell scroll root disables anchoring',async()=>{
 const scope=createScope(()=>true),page=mount(container,{routeId:'items',context,scope,services:{invoke(){throw Error('unexpected-request');}}});
 const stylesheet=window.document.createElement('style');stylesheet.textContent=await readFile(resolve(root,'modules/ff14/pages/dist/styles.css'),'utf8');window.document.head.append(stylesheet);
 try {
  const header=container.querySelector('.n-card-header__main');assert.ok(header);
  const native=container.querySelector('style[cssr-id="n-card"]');assert.ok(native);
  const moduleRules=[...stylesheet.sheet.cssRules],nativeRules=[...native.sheet.cssRules];
  const matching=rules=>rules.flatMap(rule=>rule.selectorText?.split(',').filter(selector=>header.matches(selector)).map(selector=>({selector,style:rule.style}))||[]);
  const overrides=matching(moduleRules).filter(rule=>rule.style.getPropertyValue('transition')==='none');assert.equal(overrides.length,1);
  const nativeTransitions=matching(nativeRules).filter(rule=>rule.style.getPropertyValue('transition').includes('color'));assert.ok(nativeTransitions.length);
  // These locked selectors contain only class/type selectors and child/descendant combinators.
  // Compare their class specificity independently of document order or JSDOM's cascade shortcuts.
  const classes=selector=>(selector.match(/\.[\w-]+/g)||[]).length;
  for(const rule of nativeTransitions)assert.ok(classes(overrides[0].selector)>classes(rule.selector),`${overrides[0].selector} must outrank ${rule.selector}`);
  assert.equal(overrides[0].style.getPropertyValue('color'),'var(--ff14-text)');assert.equal(overrides[0].style.getPropertyPriority('transition'),'');
  const shellStyle=window.document.createElement('style');shellStyle.textContent=await readFile(resolve(root,'pages/shell/styles.css'),'utf8');window.document.head.append(shellStyle);
  try {const html=[...shellStyle.sheet.cssRules].find(rule=>rule.selectorText==='html');assert.equal(html.style.getPropertyValue('overflow-anchor'),'none');assert.equal(html.style.getPropertyValue('overflow-y'),'auto');} finally {shellStyle.remove();}
 } finally {stylesheet.remove();scope.dispose();page.dispose();}
});
test('production cold evaluation needs no Node process/global/require shim',()=>{
  const result=spawnSync(process.execPath,['--experimental-vm-modules',resolve(root,'tests/pages/shell/browser-global-contract.mjs')],{encoding:'utf8'});
  assert.equal(result.status,0,result.stdout+'\n'+result.stderr);
  assert.match(result.stdout,/without Node globals passed/);
});
test('production independently built module renders unchanged public projection, focus and candidate ticket',async()=>{
  const scope=createScope(()=>true),posts=[];let response=selection;
  const shellStyle=window.document.createElement('style');shellStyle.setAttribute('cssr-id','n-button-theme-shell');
  const page=mount(container,{routeId:'market',context,scope,services:{invoke(cap,body){posts.push({cap,body});return Promise.resolve(response);}}});
  try {
    const input=container.querySelector('#ff14-market-query');assert.ok(input);input.value='犎牛牛排';input.dispatchEvent(new window.Event('input',{bubbles:true}));await runtime.nextTick();input.focus();input.setSelectionRange(1,3);
    const inputNode=input;page.update({...context,theme:'dark',locale:'en-US'});await runtime.nextTick();assert.equal(container.querySelector('#ff14-market-query'),inputNode);assert.equal(window.document.activeElement,inputNode);assert.equal(inputNode.selectionStart,1);assert.equal(posts.length,0);
    container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();await runtime.nextTick();assert.deepEqual(posts[0],{cap:'ff14.market.query',body:{input:JSON.stringify({query:'犎牛牛排',quality:'all',intent:'overview'})}});
    assert.match(container.textContent,/关联详情缺项/);assert.match(container.textContent,/公开口径/);assert.match(container.textContent,/123 Gil/);assert.equal(container.querySelector('a').getAttribute('rel'),'noopener noreferrer');
    window.document.head.append(shellStyle);response=success;container.querySelector('[data-item-id="44091"]').click();await flush();await runtime.nextTick();assert.deepEqual(JSON.parse(posts[1].body.input),{selection:{batch_id:'batch-1',generation:'generation-1',item_id:44091}});
    const resultNode=container.querySelector('.ff14-result');page.update({...context,available:false});await runtime.nextTick();assert.equal(container.querySelector('.ff14-result'),resultNode);assert.equal(container.querySelector('button[type="submit"]').disabled,true);assert.match(container.textContent,/关联详情缺项/);
    page.update({...context,available:true});await runtime.nextTick();assert.equal(container.querySelector('button[type="submit"]').disabled,false);assert.equal(posts.length,2);
    // All CSS generated by module Naive UI instances remains within their container.
    container.querySelector('.n-base-selection').click();await runtime.nextTick();await new Promise(resolve=>setTimeout(resolve,25));const popups=[...window.document.querySelectorAll('.n-base-select-menu')];assert.ok(popups.length);assert.ok(popups.every(p=>container.contains(p)));assert.ok(container.querySelector('style'));assert.equal(window.document.head.querySelectorAll('style:not([cssr-id^="vueuc/"]):not([cssr-id="n-button-theme-shell"])').length,0);
  } finally {scope.dispose();page.dispose();assert.equal(container.children.length,0);assert.equal(window.document.head.querySelectorAll('style').length,1);assert.equal(window.document.head.querySelector('style'),shellStyle);shellStyle.remove();}
});
test('production runtime graph and module remain local and share one Vue/UI implementation',async()=>{
  const module=await readFile(resolve(root,'modules/ff14/pages/dist/entry.js'),'utf8'),shell=await readFile(resolve(root,'pages/shell/app.js'),'utf8'),runtimeBytes=await readFile(resolve(root,'pages/shell/runtime.js'),'utf8');
  assert.match(module,/\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/runtime\.js/);assert.match(shell,/\.\/runtime\.js/);assert.match(shell,/\.\/module-loader\.js/);assert.ok(module.length<30000);assert.ok(runtimeBytes.length>100000);assert.ok(!/https?:\/\/[^\s'"]+\.(?:js|css)/.test(module));
  for(const source of [module,shell,runtimeBytes]) {
    assert.doesNotMatch(source,/\bprocess\s*\.\s*env\b/,'production NODE_ENV must be folded');
    assert.doesNotMatch(source,/(?:from|import)\s*[('" ]+node:/,'published chunks must not import Node builtins');
    assert.doesNotMatch(source,/(?<![.\w])require\s*\(/,'published ESM must not need a bare CommonJS require');
  }
  const audit=JSON.parse(await readFile(resolve(root,'.architecture-refactor/modular-r0-r3/runtime-packages.json'),'utf8'));
  for(const name of audit.packages) assert.ok(audit.licenseFiles.includes(`licenses/${name.replaceAll('/','_')}.txt`));
  assert.ok(audit.packages.includes('css-render'));assert.ok(audit.packages.includes('lodash-es'));assert.ok(audit.packages.includes('vue'));assert.ok(audit.packages.includes('naive-ui'));
});
process.on('exit',()=>dom.window.close());

test('public error metadata remains visible and empty results differ from authorization/upstream failures',async()=>{
 for(const [code,label] of [['not_found','无匹配结果'],['no_records','没有可展示记录'],['auth_expired','查询失败'],['upstream_error','查询失败']]) {
  const scope=createScope(()=>true);
  const page=mount(container,{routeId:'items',context:{...context,boundary:'error-'+code},scope,services:{invoke(){return Promise.resolve({...success,status:'error',document:null,error:{code,message:'PRIVATE-ERROR-DO-NOT-RENDER'},warnings:['公开错误警告'],provenance:['错误来源口径'],timestamps:[{value:'2026-10-06T01:01:01Z',timezone:'UTC'}]});}}});
  try {
   const input=container.querySelector('input');input.value='44091';input.dispatchEvent(new window.InputEvent('input',{bubbles:true}));await runtime.nextTick();
   container.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();await runtime.nextTick();
   assert.ok(container.querySelector('.ff14-result').textContent.includes(label),code);
   assert.ok(container.textContent.includes('公开错误警告'));assert.ok(container.textContent.includes('错误来源口径'));assert.ok(container.textContent.includes('2026-10-06T01:01:01Z'));assert.ok(!container.textContent.includes('PRIVATE-ERROR-DO-NOT-RENDER'));
  } finally {scope.dispose();page.dispose();}
 }
});

test('production popup cleanup failure retains exact owned CSS for a successful retry',async()=>{
 const scope=createScope(()=>true);const rootStyle=window.document.createElement('style');rootStyle.setAttribute('cssr-id','n-button-theme-root-retry');window.document.head.append(rootStyle);
 const page=mount(container,{routeId:'market',context:{...context,boundary:'cleanup-retry'},scope,services:{invoke(){throw Error('unused');}}});
 const popup=container.querySelector('.n-base-selection');popup.click();await runtime.nextTick();await new Promise(resolve=>setTimeout(resolve,25));
 const style=window.document.head.querySelector('style[cssr-id="vueuc/binder"]');assert.ok(style);assert.ok(window.document.querySelector('.n-base-select-menu'));
 const remove=style.remove.bind(style);let fail=true,calls=0;style.remove=()=>{calls++;if(fail)throw Error('deterministic-style-cleanup');remove();};
 try {assert.throws(()=>scope.dispose(),/cleanup_pending/);assert.equal(scope.isCurrent(),false);assert.equal(style.isConnected,true);fail=false;scope.dispose();page.dispose();assert.equal(style.isConnected,false);assert.equal(calls,2);assert.equal(container.children.length,0);assert.equal(window.document.querySelector('.n-base-select-menu'),null);assert.equal(rootStyle.isConnected,true);}finally{fail=false;scope.dispose();page.dispose();style.remove=remove;rootStyle.remove();}
});

test('shell retains production cleanup owner and blocks new mount until the popup resource retry completes',async()=>{
 const template=window.document.createElement('template');template.id='module-style-assets';const data=catalog();
 for(const path of data.modules[0].pages[0].styles){const link=window.document.createElement('link');link.rel='stylesheet';link.dataset.resource=path;link.href='./'+path;template.content.append(link);}window.document.body.append(template);
 const append=window.document.head.append.bind(window.document.head);window.document.head.append=(...nodes)=>{append(...nodes);for(const node of nodes)if(node.matches?.('link[data-module-style]'))queueMicrotask(()=>node.dispatchEvent(new window.Event('load')));};
 const outer=window.document.createElement('div');window.document.body.append(outer);window.location.hash='#/module/ff14%2Fff14/market';window.scrollTo=()=>{};
 let loads=0;const options=[];const bridge={ready:()=>Promise.resolve({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'shell',binding:1}),onContext(){return()=>{};},apiGet:()=>Promise.resolve(data),apiPost:()=>Promise.resolve(success)};
 const shell=createShell({bridge,container:outer,window,loadPage:async()=>{loads++;return{mount(view,option){options.push(option);return mount(view,option);}};},changed(){}});shell.start();await flush();await runtime.nextTick();
 outer.querySelector('.n-base-selection').click();await runtime.nextTick();await new Promise(resolve=>setTimeout(resolve,25));const style=window.document.head.querySelector('style[cssr-id="vueuc/binder"]');assert.ok(style);
 const remove=style.remove.bind(style);let fail=true;style.remove=()=>{if(fail)throw Error('deterministic-owner-retirement');remove();};
 try {shell.select('ff14/ff14','items');await flush();assert.equal(shell.state.cleanupPending,true);assert.equal(shell.state.mounted,false);assert.equal(options[0].scope.isCurrent(),false);assert.equal(style.isConnected,true);assert.equal(loads,1);
  shell.retryCleanup();await flush();assert.equal(shell.state.cleanupPending,true);assert.equal(loads,1);
  fail=false;shell.retryCleanup();await flush();await runtime.nextTick();assert.equal(shell.state.cleanupPending,false);assert.equal(shell.state.mounted,true);assert.equal(style.isConnected,false);assert.equal(loads,1);
 }finally{fail=false;shell.retryCleanup();shell.dispose();style.remove=remove;outer.remove();template.remove();window.document.head.append=append;window.location.hash='';}
});
