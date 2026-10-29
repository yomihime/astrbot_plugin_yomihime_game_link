// Actual source TypeScript and mounted source App, with synthetic static bytes.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule} from './compile.mjs';
import {sourceApp} from './source-build.mjs';
import {catalog,deferred} from './fixtures.mjs';
const {createModuleAssets}=await sourceModule('pages/frontend/src/module-assets.ts');
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const flush=async()=>{for(let i=0;i<5;i++)await new Promise(resolve=>setTimeout(resolve,5));};
function fixture(version='a',extension='svg'){
 const data=catalog('minimal/demo'),module=data.modules[0];module.asset_version=version.repeat(64);
 const prefix=`module-assets/${module.module_id}/pages/p${module.asset_version}/`;
 const mime={svg:'image/svg+xml',png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg'}[extension];
 const content=[['pages/entry.js','text/javascript','throw Error("Logo must never import this page");'],['pages/styles.css','text/css','.business{color:red}'],['assets/logo.'+extension,mime,extension==='svg'?'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0h1v1z"/></svg>':'synthetic-image-bytes']];
 const resources=content.map(([path,media_type,text])=>({path,media_type,sha256:hash(text),base64:Buffer.from(text).toString('base64')}));
 module.pages=[{...module.pages[0],entry:prefix+'pages/entry.js',styles:[prefix+'pages/styles.css'],role:'aux'}];
 module.resources=resources.map(item=>({path:prefix+item.path,sha256:item.sha256}));
 module.display={default_name:'Synthetic extension',localized_names:{},short_name:null,logo_resource:prefix+'assets/logo.'+extension};
 module.source={kind:'extension',package_id:'minimal'};
 return {data,module,response:{asset_version:module.asset_version,entry:'pages/entry.js',styles:['pages/styles.css'],resources}};
}
function blobs(){
 const created=[],revoked=[],create=URL.createObjectURL,revoke=URL.revokeObjectURL;
 URL.createObjectURL=blob=>{const url='blob:http://localhost/logo-'+created.length;created.push({url,blob});return url;};
 URL.revokeObjectURL=url=>revoked.push(url);
 return {created,revoked,restore(){URL.createObjectURL=create;URL.revokeObjectURL=revoke;}};
}
test('declared SVG/PNG/JPEG logos load on home without importing a page or attaching CSS; payload is singleflight',async()=>{
 const dom=new JSDOM('',{url:'http://localhost/'}),urls=blobs();
 try{for(const extension of ['svg','png','jpg','jpeg']){
  const f=fixture('a',extension),calls=[];let imports=0;
  const assets=createModuleAssets(dom.window.document,async()=>{imports++;throw Error('business import');},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async(path,body)=>{calls.push({path,body});return f.response;}}});
  const [first,second]=await Promise.all([assets.image(f.module,f.module.display.logo_resource),assets.image(f.module,f.module.display.logo_resource)]);
  assert.equal(first,second);assert.equal(calls.length,1);assert.equal(calls[0].path,'admin/module-page-assets');
  assert.deepEqual(calls[0].body,{owner:'minimal/demo',page:'items',runtime_id:'runtime-1',module_epoch:1});
  assert.equal(imports,0);assert.equal(dom.window.document.querySelectorAll('link').length,0);
  assert.equal(urls.created.at(-1).blob.type,f.response.resources.at(-1).media_type);
  assert.equal(await urls.created.at(-1).blob.text(),Buffer.from(f.response.resources.at(-1).base64,'base64').toString());
  assets.invalidateAll();assert.ok(urls.revoked.includes(first));
 }}finally{urls.restore();dom.window.close();}
});
test('whole response metadata, hash, MIME, identity and declared image authority reject before any Blob exists',async()=>{
 const dom=new JSDOM('',{url:'http://localhost/'}),urls=blobs();
 const mutations=[
  f=>f.response.asset_version='b'.repeat(64),
  f=>f.response.resources.at(-1).base64=Buffer.from('changed').toString('base64'),
  f=>f.response.resources.at(-1).media_type='text/html',
  f=>f.response.resources[0].media_type='image/svg+xml',
  f=>f.response.resources[0].base64=Buffer.from('other entry').toString('base64'),
  f=>f.response.resources.pop(),
  f=>f.response.resources.push({...f.response.resources[0]}),
  f=>f.response.resources.at(-1).path='../outside.svg',
  f=>f.response.styles=[],
 ];
 try{for(const mutate of mutations){
  const f=fixture();mutate(f);
  const assets=createModuleAssets(dom.window.document,async()=>{throw Error();},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>f.response}});
  await assert.rejects(assets.image(f.module,f.module.display.logo_resource));assets.invalidateAll();
 }
 const f=fixture();let calls=0;
 const assets=createModuleAssets(dom.window.document,async()=>{throw Error();},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>{calls++;return f.response;}}});
 for(const path of ['https://example.invalid/logo.svg',f.module.pages[0].entry,f.module.display.logo_resource.replace('minimal/demo','other/demo')])await assert.rejects(assets.image(f.module,path));
 assert.equal(calls,0);assert.equal(urls.created.length,0);
 }finally{urls.restore();dom.window.close();}
});
test('runtime/epoch/version replacement revokes prior images and late responses cannot recreate them',async()=>{
 const dom=new JSDOM('',{url:'http://localhost/'}),urls=blobs();
 try{for(const change of [f=>f.module.runtime_id='runtime-new',f=>f.module.module_epoch++,f=>Object.assign(f,fixture('b'))]){
  const f=fixture(),pending=deferred();let reads=0;
  const assets=createModuleAssets(dom.window.document,async()=>{throw Error();},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>++reads===1?pending.promise:f.response}});
  const old=assets.image(f.module,f.module.display.logo_resource);change(f);
  const current=await assets.image(f.module,f.module.display.logo_resource);pending.resolve(fixture().response);
  await assert.rejects(old);assert.equal(reads,2);assets.invalidateAll();assert.ok(urls.revoked.includes(current));
 }
 const f=fixture();const assets=createModuleAssets(dom.window.document,async()=>{throw Error();},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>f.response}});
 const first=await assets.image(f.module,f.module.display.logo_resource);f.module.runtime_id='next';
 const next=await assets.image(f.module,f.module.display.logo_resource);assert.notEqual(first,next);assert.ok(urls.revoked.includes(first));assets.invalidateAll();
 }finally{urls.restore();dom.window.close();}
});
async function appHarness(initial,reply){
 const dom=new JSDOM('<html data-page-name="00-game-link"><body><div id="app"></div></body></html>',{pretendToBeVisual:true,url:'http://localhost/#/'}),window=dom.window,calls=[],listeners=[];
 for(const name of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver'])globalThis[name]=name==='window'?window:name==='document'?window.document:window[name];
 globalThis.getComputedStyle=window.getComputedStyle.bind(window);globalThis.requestAnimationFrame=window.requestAnimationFrame.bind(window);globalThis.cancelAnimationFrame=window.cancelAnimationFrame.bind(window);
 window.scrollTo=()=>{};window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
 let current=initial,imports=0;const context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'synthetic-logo'};
 window.AstrBotPluginView={ready:async()=>context,onContext(fn){listeners.push(fn);fn(context);return()=>{};},apiGet:async()=>current,apiPost:async(path,body)=>{calls.push({path,body});if(path==='admin/module-page-assets')return reply(body);if(path==='admin/modules')return {registry_revision:1,modules:[]};if(path==='admin/catalog')return {schema_version:1,fields:[]};if(path==='admin/read')return {};throw Error('No business query expected');}};
 globalThis.__loadFixturePage=async()=>{imports++;throw Error('No page mount on home');};
 await sourceApp();await flush();
 return {window,doc:window.document,calls,get imports(){return imports;},async refresh(data){current=data;const button=[...window.document.querySelectorAll('button')].find(item=>item.textContent.includes('刷新'));assert.ok(button);button.click();await flush();},async context(context){for(const listener of listeners)listener(context);await flush();},close(){window.dispatchEvent(new window.Event('pagehide'));dom.window.close();delete globalThis.__loadFixturePage;}};
}
test('mounted source home resolves an external logo, replaces v2, and revokes on context change/disposal',async()=>{
 const urls=blobs();let app;
 try{
  let f=fixture();app=await appHarness(f.data,()=>f.response);
  let logo=app.doc.querySelector('.workspace-emblem-resource');assert.ok(logo);assert.match(logo.style.maskImage,/blob:http:\/\/localhost\/logo-0/);
  assert.equal(app.imports,0);assert.equal(app.calls.filter(call=>call.path==='admin/module-page-assets').length,1);assert.equal(app.doc.querySelectorAll('link[data-module-style]').length,0);
  await app.context({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'synthetic-logo',isDark:true});assert.equal(app.doc.querySelector('.workspace-emblem-resource'),logo);assert.equal(urls.created.length,1);
  f=fixture('b');await app.refresh(f.data);logo=app.doc.querySelector('.workspace-emblem-resource');assert.match(logo.style.maskImage,/logo-1/);assert.deepEqual(urls.revoked,[urls.created[0].url]);
  await app.context({pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',session:'replacement'});
  assert.equal(app.doc.querySelector('.workspace-emblem-resource'),null);assert.ok(urls.revoked.includes(urls.created[1].url));
  assert.equal(app.imports,0);assert.equal(app.calls.filter(call=>!['admin/module-page-assets','admin/modules','admin/catalog','admin/read'].includes(call.path)).length,0);
 }finally{app?.close();urls.restore();}
});
test('mounted home revokes a live logo on pagehide and late v2 never attaches after its owner leaves',async()=>{
 const urls=blobs();let app;
 try{
  let f=fixture(),reply=()=>f.response;app=await appHarness(f.data,()=>reply());
  assert.equal(urls.created.length,1);app.close();app=null;assert.deepEqual(urls.revoked,[urls.created[0].url]);
  app=await appHarness(f.data,()=>reply());const old=urls.created.at(-1).url,late=deferred();f=fixture('b');reply=()=>late.promise;
  await app.refresh(f.data);assert.equal(app.doc.querySelector('.workspace-emblem-resource'),null);assert.ok(urls.revoked.includes(old));
  const count=urls.created.length;await app.refresh({schema_version:1,catalog_revision:3,runtime:{state:'ready'},modules:[]});late.resolve(f.response);await flush();assert.equal(urls.created.length,count);assert.equal(app.doc.querySelector('.workspace-emblem-resource'),null);
 }finally{app?.close();urls.restore();}
});
test('mounted home keeps fallback for missing/invalid logo and discards a response after close',async()=>{
 const urls=blobs();let app;
 try{
  const missing=fixture();missing.module.display.logo_resource=null;app=await appHarness(missing.data,()=>{throw Error('No logo request');});assert.equal(app.calls.filter(call=>call.path==='admin/module-page-assets').length,0);assert.equal(app.doc.querySelector('.workspace-emblem').textContent,'✦');app.close();app=null;
  const bad=fixture();bad.response.resources.at(-1).media_type='text/html';app=await appHarness(bad.data,()=>bad.response);assert.equal(app.doc.querySelector('.workspace-emblem-resource'),null);assert.equal(app.doc.querySelector('.workspace-emblem').textContent,'✦');app.close();app=null;
  const f=fixture(),late=deferred();app=await appHarness(f.data,()=>late.promise);assert.equal(app.calls.filter(call=>call.path==='admin/module-page-assets').length,1);app.close();app=null;late.resolve(f.response);await flush();assert.equal(urls.created.length,0);
 }finally{app?.close();urls.restore();}
});
test('builtin declared static logos retain their packaged URL and never request external assets',async()=>{
 const data=catalog(),module=data.modules[0],path='module-assets/ff14/ff14/assets/logo.svg';
 module.source={kind:'builtin',package_id:'ff14'};module.display={default_name:'Builtin',localized_names:{},short_name:null,logo_resource:path};module.resources.push({path,sha256:hash('<svg/>')});
 const app=await appHarness(data,()=>{throw Error('Builtin must keep its packaged static contract');});
 try{const logo=app.doc.querySelector('.workspace-emblem-resource');assert.ok(logo);assert.equal(logo.style.maskImage,'url("./'+path+'")');assert.equal(app.calls.filter(call=>call.path==='admin/module-page-assets').length,0);assert.equal(app.imports,0);}
 finally{app.close();}
});

test('actual asset loader validates all bytes without crypto/subtle and rejects tampering in both digest paths',async()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'crypto'),native=globalThis.crypto;
 const dom=new JSDOM('',{url:'http://192.168.1.20/'}),urls=blobs();
 try{for(const crypto of [native,undefined,{}]){
  Object.defineProperty(globalThis,'crypto',{configurable:true,value:crypto});
  const f=fixture(),before=urls.created.length;
  const assets=createModuleAssets(dom.window.document,async()=>{throw Error('unexpected import');},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>f.response}});
  assert.match(await assets.image(f.module,f.module.display.logo_resource),/^blob:/);assets.invalidateAll();
  assert.equal(urls.created.length,before+1);assert.ok(urls.revoked.includes(urls.created.at(-1).url));
  f.response.resources[0].base64=Buffer.from('modified executable').toString('base64');
  const tampered=createModuleAssets(dom.window.document,async()=>{throw Error('unexpected import');},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>f.response}});
  await assert.rejects(tampered.image(f.module,f.module.display.logo_resource),/asset_digest_mismatch/);
  assert.equal(urls.created.length,before+1);tampered.invalidateAll();
 }}finally{Object.defineProperty(globalThis,'crypto',original);urls.restore();dom.window.close();}
});
test('production-compiled mounted home resolves and cleans up an external SVG without SubtleCrypto',async()=>{
 const original=Object.getOwnPropertyDescriptor(globalThis,'crypto'),urls=blobs();let app;
 try{Object.defineProperty(globalThis,'crypto',{configurable:true,value:{}});
  const f=fixture();app=await appHarness(f.data,()=>f.response);
  const logo=app.doc.querySelector('.workspace-emblem-resource');assert.ok(logo);assert.match(logo.style.maskImage,/blob:/);
  assert.equal(app.imports,0);assert.equal(urls.created.length,1);
  app.close();app=null;assert.deepEqual(urls.revoked,[urls.created[0].url]);
 }finally{app?.close();Object.defineProperty(globalThis,'crypto',original);urls.restore();}
});
test('malformed projections and invalid base64 report validation, not revision loss',async()=>{
 const dom=new JSDOM('',{url:'http://192.168.1.20/'}),urls=blobs();
 try{for(const [mutate,code] of [
  [f=>f.response=null,'invalid_live_projection'],
  [f=>f.response.resources=null,'invalid_live_projection'],
  [f=>f.response.resources[0].base64='%%%','invalid_live_resource'],
  [f=>f.response.asset_version='b'.repeat(64),'asset_revoked'],
 ]){const f=fixture();mutate(f);
  const assets=createModuleAssets(dom.window.document,async()=>{throw Error('unexpected import');},100,{runtimeUrl:'/runtime.js',bridge:{apiPost:async()=>f.response}});
  await assert.rejects(assets.image(f.module,f.module.display.logo_resource),{message:code});assets.invalidateAll();
 }assert.equal(urls.created.length,0);
 }finally{urls.restore();dom.window.close();}
});
