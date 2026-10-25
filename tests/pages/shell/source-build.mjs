// Deterministic source bundles in memory. No formal assets/manifest are written.
import {build} from '../../../pages/frontend/node_modules/vite/dist/node/index.js';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const frontend=resolve(root,'pages/frontend');
const cache=new Map();
export async function sourceArtifacts(entry){
 if(!cache.has(entry))cache.set(entry,(async()=>{
  const result=await build({configFile:false,root:frontend,logLevel:'silent',resolve:{alias:{'@game-link/ui':resolve(root,'pages/public-ui.ts'),vue:resolve(frontend,'node_modules/vue/dist/vue.runtime.esm-bundler.js'),'naive-ui':resolve(frontend,'node_modules/naive-ui/es/index.mjs')}},define:{'process.env.NODE_ENV':JSON.stringify('production'),__VUE_OPTIONS_API__:false,__VUE_PROD_DEVTOOLS__:false,__VUE_PROD_HYDRATION_MISMATCH_DETAILS__:false},build:{write:false,lib:{entry:resolve(root,entry),formats:['es'],fileName:()=> 'app.js'},minify:true,rolldownOptions:{external:entry.endsWith('runtime.ts')?[]:['vue','naive-ui','@game-link/ui','./module-loader.js'],output:{paths:{vue:'./runtime.js','naive-ui':'./runtime.js','@game-link/ui':'./runtime.js'}}}}});
  return (Array.isArray(result)?result[0]:result).output;
 })());return cache.get(entry);
}
export async function sourceBundle(entry){return (await sourceArtifacts(entry)).find(item=>item.type==='chunk'&&item.isEntry).code;}
export async function managementModule(){
 const runtime='data:text/javascript;base64,'+Buffer.from(await sourceBundle('pages/frontend/src/runtime.ts')).toString('base64');
 const code=(await sourceBundle('pages/frontend/src/management.js')).replaceAll('"./runtime.js"',JSON.stringify(runtime)).replaceAll("'./runtime.js'",JSON.stringify(runtime));
 try{return await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));}catch(error){throw new Error('source management import failed: '+error.message.split('data:text')[0]);}
}
export async function sourceApp(entry='pages/frontend/src/main.ts'){
 const runtime='data:text/javascript;base64,'+Buffer.from(await sourceBundle('pages/frontend/src/runtime.ts')).toString('base64');
 const loader='data:text/javascript;base64,'+Buffer.from('export const sharedRuntimeUrl='+JSON.stringify(runtime)+';export function loadPage(entry){return globalThis.__loadFixturePage(entry)}').toString('base64');
 const code=(await sourceBundle(entry)).replaceAll('"./runtime.js"',JSON.stringify(runtime)).replaceAll("'./runtime.js'",JSON.stringify(runtime)).replaceAll('"./module-loader.js"',JSON.stringify(loader)).replaceAll("'./module-loader.js'",JSON.stringify(loader));
 return import('data:text/javascript;base64,'+Buffer.from(code).toString('base64')+'#'+Math.random());
}

export async function sourceRuntimeURL(){return 'data:text/javascript;base64,'+Buffer.from(await sourceBundle('pages/frontend/src/runtime.ts')).toString('base64');}
