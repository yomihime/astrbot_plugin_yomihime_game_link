import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {sourceModule,root} from './compile.mjs';
const {createFontSettings,validateFontStatus,fontStatusText,fontDownloadUrl}=await sourceModule(root+'pages/frontend/src/font-settings.ts');
const status=(extra={})=>({revision:0,state:'unavailable',source:'system',error:'download_failed',filename:'NotoSansCJKsc-Regular.otf',size:16437364,sha256:'2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b',download_url:'https://example.invalid/pinned-font.otf',...extra});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(){let panel,denied=0,respond=async()=>status();const calls=[],timers=new Map();let timerId=0;
 const bridge={apiPost:async(endpoint,body)=>{calls.push({endpoint,body});return respond(endpoint,body);},upload:async(endpoint,file)=>{calls.push({endpoint,file});return respond(endpoint,file);}};
 const control=createFontSettings(bridge,{setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)},value=>panel=value,()=>denied++);
 return {control,calls,timers,get panel(){return panel;},get denied(){return denied;},respond(fn){respond=fn;}};
}
test('font DTO validates bounded fixed-file metadata and maps safe status text',()=>{
 assert.equal(fontDownloadUrl(status()),null);assert.equal(fontDownloadUrl(status({download_url:'javascript:alert(1)'})),null);
 assert.equal(validateFontStatus(status()).revision,0);assert.match(fontStatusText(status()),/失败.*系统字体/);
 assert.match(fontStatusText(status({state:'ready',source:'canonical',error:null})),/已就绪/);
 assert.match(fontStatusText(status({state:'downloading',error:null})),/正在下载/);
 for(const change of [{revision:-1},{size:50e6},{filename:'../font.otf'},{sha256:'a'.repeat(65)},{error:'/private/path'},{source:'other'},{state:'other'}])assert.throws(()=>validateFontStatus(status(change)));
});
test('repeated retry and upload clicks are fenced; invalid file and picker cancel never upload',async()=>{
 const f=fixture();f.control.sync(true,true);await tick();
 f.panel.upload(null);f.panel.upload({name:'wrong.otf',size:16437364});f.panel.upload({name:status().filename,size:1});await tick();assert.equal(f.calls.length,1);
 let resolve;f.respond(()=>new Promise(done=>resolve=done));f.panel.retry();f.panel.retry();assert.equal(f.panel.busy,true);assert.equal(f.calls.length,2);assert.deepEqual(f.calls[1],{endpoint:'admin/font-retry',body:{}});
 resolve(status());await tick();const file={name:status().filename,size:status().size};f.panel.upload(file);f.panel.upload(file);assert.equal(f.calls.length,3);assert.equal(f.calls[2].file,file);assert.equal(f.calls[2].endpoint,'admin/font-upload');resolve(status({state:'ready',source:'canonical',error:null}));await tick();assert.equal(f.panel.busy,false);assert.equal(f.panel.status.state,'ready');f.control.dispose();
});
test('navigation, context invalidation and disposal discard late results and polling',async()=>{
 const f=fixture();let resolve;f.respond(()=>new Promise(done=>resolve=done));f.control.sync(true,true);const old=f.panel;f.control.sync(false,true);resolve(status({state:'downloading'}));await tick();assert.equal(f.panel.visible,false);assert.equal(f.panel.status,null);assert.equal(f.timers.size,0);old.retry();assert.equal(f.calls.length,1);
 f.respond(async()=>status({state:'downloading',error:null}));f.control.sync(true,true);await tick();assert.equal(f.timers.size,1);f.panel.retry();assert.equal(f.calls.length,2);f.control.invalidate();assert.equal(f.timers.size,0);assert.equal(f.panel.enabled,false);
 f.control.sync(true,true);await tick();f.control.dispose();assert.equal(f.timers.size,0);f.panel.refresh();await tick();assert.equal(f.calls.length,3);
});
test('outdated auth disables font actions and errors never render backend paths',async()=>{
 const f=fixture();f.control.sync(true,true);await tick();f.respond(async()=>{throw Error('admin_authorization_denied');});f.panel.retry();await tick();assert.equal(f.denied,1);assert.equal(f.panel.enabled,false);assert.equal(f.panel.status,null);f.panel.retry();await tick();assert.equal(f.calls.length,2);f.control.dispose();
 const g=fixture();g.respond(async()=>{throw Error('/private/data/fonts/secret');});g.control.sync(true,true);await tick();assert.doesNotMatch(g.panel.message,/private|secret/);assert.equal(g.panel.enabled,true);g.control.dispose();
});
test('built shell font controls stay in Core reply settings and tolerate navigation',()=>{
 for(const args of [[],['--cold-module']]){const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./font-settings-contract.mjs',import.meta.url)),...args],{encoding:'utf8'});assert.equal(result.status,0,result.stdout+'\n'+result.stderr);assert.match(result.stdout,/font settings passed/);}
});

test('missing host bridge remains a safe disabled panel',()=>{const control=createFontSettings(undefined,{},()=>{},()=>{});control.sync(false,false);control.dispose();});
