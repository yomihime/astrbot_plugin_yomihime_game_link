// Build a local-only browser harness around exact production assets. No real Host.
import {readFile,writeFile,mkdir,cp} from 'node:fs/promises';
import {resolve} from 'node:path';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
const root=resolve(import.meta.dirname,'../../..'),out=resolve(process.argv[2]||'/tmp/game-link-oil-production');
await mkdir(out,{recursive:true});await cp(resolve(root,'pages/00-game-link'),out,{recursive:true});
const manifest=JSON.parse(await readFile(resolve(root,'modules/ff14/yomihime.manifest.json'),'utf8')),module=manifest.modules[0],prefix='module-assets/ff14/ff14/';
const catalog={schema_version:1,catalog_revision:1,runtime:{state:'ready'},modules:[{...module,module_id:'ff14/ff14',display:{...module.display,default_name:'最终幻想 XIV',localized_names:{},short_name:'FF14',logo_resource:prefix+module.display.logo_resource},state:'loaded',enabled:true,lifecycle:'active',module_epoch:1,runtime_id:'offline-fixture',asset_version:'a'.repeat(64),pages:module.pages.map(p=>({...p,capability_id:p.capability_id??null,entry:prefix+p.entry,styles:p.styles.map(s=>prefix+s)})),resources:module.resources.map(r=>({...r,path:prefix+r.path}))}]};
// Five-module navigation preview requires real, reviewed external package assets.
// Only bridge state/data are synthetic; no external DLC code is modified.
if(process.argv.includes('--five-modules')){
  const argument=process.argv.find(value=>value.startsWith('--dlc-root='));
  if(!argument)throw Error('--five-modules requires --dlc-root=<reviewed steam_dlc package directory>');
  const dlcRoot=resolve(argument.slice('--dlc-root='.length));
  // Use the same validated, hash-bound static projection as the release builder.
  // This is not a full release ZIP or real Host/session verification.
  execFileSync(process.env.YGL_TEST_PYTHON||'python',['-c',`
from pathlib import Path
import sys
from scripts.build_dashboard_zip import projected_page_assets
root,out,external=map(Path,sys.argv[1:])
for name,content in projected_page_assets(root,external_page_roots=(external,)).items():
    prefix='pages/00-game-link/'
    if not name.startswith(prefix): continue
    target=out/name[len(prefix):]
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(content)
`,root,out,dlcRoot],{cwd:root,stdio:'pipe'});
  const rawManifest=await readFile(resolve(dlcRoot,'yomihime.manifest.json'));
  const digest=createHash('sha256').update(rawManifest).digest('hex');
  const external=JSON.parse(rawManifest.toString('utf8'));
  if(external.package_id!=='steam_dlc')throw Error('Expected steam_dlc fixture package');
  for(const id of ['cs2','deadlock','dota2','steam']){
    const descriptor=external.modules.find(value=>value.module_id===id);
    if(!descriptor)throw Error('Missing external fixture module '+id);
    const owner=`steam_dlc/${id}`,target=`module-assets/${owner}/pages/p${digest}/`;
    catalog.modules.push({...catalog.modules[0],...descriptor,module_id:owner,
      display:{...descriptor.display,localized_names:{},logo_resource:null},
      pages:descriptor.pages.map(page=>({...page,capability_id:page.capability_id??null,entry:target+page.entry,styles:page.styles.map(style=>target+style)})),
      resources:descriptor.resources.map(resource=>({...resource,path:target+resource.path})),
    });
  }
}
const {sourceModule}=await import('./compile.mjs');
const {validateCatalog}=await sourceModule(resolve(root,'pages/frontend/src/catalog.ts'));validateCatalog(catalog);
const {themeCatalog,themeSnapshot}=await import('./theme-settings-fixtures.mjs');
const declarations=themeCatalog();declarations.fields=declarations.fields.filter(f=>f.module_id==='game_link/core'&&f.name==='image_theme');
const snapshot={'game_link/core':{revision:7,fields:{image_theme:themeSnapshot()['game_link/core'].fields.image_theme}}};
const bridge=`const catalog=${JSON.stringify(catalog)},declarations=${JSON.stringify(declarations)},snapshot=${JSON.stringify(snapshot)};
const showStates=new URLSearchParams(location.search).get('states')==='1';
if(showStates){
  for(const [index,state] of [[1,'disabled'],[2,'unavailable']]){
    const module=catalog.modules[index];if(!module)continue;
    Object.assign(module,{state,enabled:state!=='disabled',lifecycle:state==='disabled'?'stopped':'discovered',runtime_id:null,pages:[],resources:[]});
    if(module.display)module.display.logo_resource=null;
  }
  const notice=document.createElement('p');notice.id='synthetic-state-notice';notice.textContent='合成状态示例，不是实际故障';notice.style='position:fixed;right:12px;bottom:12px;z-index:99999;padding:6px 10px;margin:0;border:1px solid #a99866;background:#f7f4ed;color:#655071;font:13px system-ui';document.body.append(notice);
}
const font={revision:0,state:'ready',source:'canonical',error:null,filename:'NotoSansCJKsc-Regular.otf',size:16437364,sha256:'2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b',download_url:'https://raw.githubusercontent.com/notofonts/noto-cjk/523d033d6cb47f4a80c58a35753646f5c3608a78/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf'};
const storage={state:'ready',reason_code:'ready',ready:true,source:'managed',generation:1,revision:2,inventory_digest:'a'.repeat(64),unknown_count:0,impact:[],storage_security:'legacy_review_required',environment_present:false,environment_matches:false,recovery_action:null};
const contexts=new Set(),calls=[];let revision=1,fail=false,hold=false,release=null;
let context={pluginName:'astrbot_plugin_yomihime_game_link',pageName:'00-game-link',sessionGeneration:1,isDark:new URLSearchParams(location.search).get('theme')==='dark',locale:'zh-CN'};
let values={reply_theme:null,ff14_default_region:'cn',ff14_subscriptions_enabled:true,ff14_calendar_default_days:7,ff14_calendar_default_timezone:'Asia/Shanghai',ff14_calendar_default_delivery_time:'08:00',ff14_calendar_sync_interval_seconds:21600};
const credentials={credential_fflogs_cn:{configured:false,decryptable:false,upstream:'unverified'},credential_fflogs_global:{configured:false,decryptable:false,upstream:'unverified'}};
const fields=()=>Object.fromEntries(Object.entries(values).map(([name,value])=>[name,{value,state:'valid',present:false,source:'default'}]));
window.AstrBotPluginView={ready:()=>Promise.resolve(context),onContext(fn){contexts.add(fn);queueMicrotask(()=>{if(contexts.has(fn))fn(context);});return()=>contexts.delete(fn);},upload:async()=>{throw Error('offline-upload-disabled');},apiGet:()=>Promise.resolve(catalog),async apiPost(action,body){calls.push({action,body});if(action==='admin/module-page-session')return{page_session:'offline-only',expires_at:Date.now()/1000+30};if(action==='admin/module-page-session-close')return{closed:true};if(action==='admin/module-config-current'){if(fail){fail=false;throw Error('offline-read-failure');}return{revision,fields:fields(),descriptors:{reply_theme:{description:'回复图片主题',default:null,value_schema:{type:'string'},choices:[{value:'warm_paper',label:'暖桃纸'},{value:'deep_indigo',label:'深靛'}]}}};}if(action==='admin/module-credential-status')return{revision,fields:structuredClone(credentials)};if(action==='admin/module-config-save'){if(hold)await new Promise(r=>release=r);values={...values,...body.values};return{revision:++revision};}if(action==='admin/module-credential-replace'){credentials[body.field]={configured:true,decryptable:true,upstream:'unverified'};return{revision:++revision};}if(action==='admin/module-credential-clear'){credentials[body.field]={configured:false,decryptable:false,upstream:'unverified'};return{revision:++revision};}if(action==='admin/catalog')return declarations;if(action==='admin/read')return snapshot;if(action==='admin/master-key-status')return storage;if(action==='admin/font-status'||action==='admin/font-retry')return font;if(action==='admin/modules')return{registry_revision:1,modules:catalog.modules.map((m,i)=>({module_id:m.module_id,enabled:m.enabled,lifecycle:m.lifecycle,health:i?'unknown':'ready',epoch:1,registry_revision:1,reason_code:i?'health_unknown':null}))};throw Error('offline-unsupported');}};
const toolbar=document.createElement('details');toolbar.id='preview-controls';toolbar.style='position:fixed;right:8px;bottom:8px;z-index:99999;background:#eee;color:#222;border:1px solid #888;padding:6px;font:12px system-ui';toolbar.innerHTML='<summary>离线测试控制</summary><p>无宿主连接，请勿输入真实凭据。</p>';
function button(label,fn){const b=document.createElement('button');b.textContent=label;b.onclick=fn;toolbar.append(b);}
function theme(){const input=document.activeElement,before=input?.value;context={...context,isDark:!context.isDark};contexts.forEach(fn=>fn(context));setTimeout(()=>{readout.textContent='主题切换：同一输入 '+String(input?.isConnected&&document.activeElement===input)+'，值保留 '+String(input?.value===before);},100);}
button('切换主题',theme);button('2秒后切换主题',()=>setTimeout(theme,2000));button('下次读取失败',()=>fail=true);button('阻塞保存',()=>hold=true);button('释放保存',()=>{hold=false;release?.();});button('失效绑定',()=>{context={...context,sessionGeneration:context.sessionGeneration+1};contexts.forEach(fn=>fn(context));});const readout=document.createElement('p');toolbar.append(readout);if(new URLSearchParams(location.search).get('controls')==='1')document.body.append(toolbar);
const motion=document.createElement('div');toolbar.append(motion);
for(const [label,time] of [['暗态',0],['去程',1600],['回弹',2000],['全亮',2600],['持续全亮',8000]])button(label,()=>{document.getAnimations().filter(a=>a.animationName?.startsWith('ui-logo-')).forEach(a=>{a.pause();a.currentTime=time;});readout.textContent='真实生产 CSS 动画：'+time+' ms';});
button('播放动画',()=>document.getAnimations().filter(a=>a.animationName?.startsWith('ui-logo-')).forEach(a=>{a.currentTime=0;a.play();}));
const timeInput=document.createElement('input');timeInput.type='number';timeInput.value='0';timeInput.setAttribute('aria-label','Animation time (ms)');toolbar.append(timeInput);button('Apply animation time',()=>{document.getAnimations().filter(a=>a.animationName?.startsWith('ui-logo-')).forEach(a=>{a.pause();a.currentTime=Number(timeInput.value);});});
button('减少动态效果等效预览',()=>{const style=document.createElement('style');style.textContent='.ui-logo-rebound::before{animation:none!important;clip-path:none!important;opacity:1!important}.ui-logo-rebound::after{animation:none!important;opacity:0!important}.workspace-status-icon>svg{animation:none!important;transition:none!important;transform:none!important}';document.head.append(style);readout.textContent='仅模拟生产减少动态效果声明，未更改操作系统偏好';});
button('读取动画状态',()=>{readout.textContent=JSON.stringify({reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,animations:document.getAnimations().filter(a=>a.animationName?.startsWith('ui-logo-')).map(a=>({name:a.animationName,time:Math.round(a.currentTime||0),state:a.playState,iterations:a.effect.getTiming().iterations}))});});
button('读取状态图标动画',()=>{readout.textContent=JSON.stringify({reducedMotion:matchMedia('(prefers-reduced-motion: reduce)').matches,icons:[...document.querySelectorAll('.workspace-status-icon')].map(node=>{const svg=node.querySelector('svg'),style=getComputedStyle(svg);return {state:node.dataset.state,animation:style.animationName,iterations:style.animationIterationCount,duration:style.animationDuration,running:svg.getAnimations().some(a=>a.playState==='running')};})});});
window.YGLPreview={calls};
`;
await writeFile(resolve(out,'mock-bridge.js'),bridge);
let html=await readFile(resolve(out,'index.html'),'utf8');html=html.replace('<script src="/api/plugin/page/bridge-sdk.js"></script>','<script src="./mock-bridge.js"></script>');await writeFile(resolve(out,'index.html'),html);
await writeFile(resolve(out,'capture.html'),`<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#ddd}iframe{border:0;display:block;width:1440px;height:1000px}</style><iframe id="view" title="Production UI"></iframe><script>const p=new URLSearchParams(location.search);view.style.width=(p.get('width')||1440)+'px';view.style.height=(p.get('height')||1000)+'px';view.src='./index.html?theme='+(p.get('theme')||'light')+'&controls='+(p.get('controls')||'0')+'&states='+(p.get('states')||'0')+'#'+(p.get('route')||'/module/ff14%2Fff14/config');</script>`);
console.log(out);
