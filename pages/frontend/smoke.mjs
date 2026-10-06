// Minimal production experiment: independent library uses the same exported identities.
import {build} from 'vite';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const here=dirname(fileURLToPath(import.meta.url)); const root=resolve(here,'../..');
const out=resolve(root,'.architecture-refactor/modular-r0-r3/runtime-smoke'); await mkdir(out,{recursive:true});
const input=resolve(out,'smoke-entry.ts'); await writeFile(input,"import {createApp} from 'vue'; import {NButton} from 'naive-ui'; export {createApp,NButton};\n");
const common={configFile:false,root:here,resolve:{alias:{vue:resolve(here,'node_modules/vue/dist/vue.runtime.esm-bundler.js'),'naive-ui':resolve(here,'node_modules/naive-ui/es/index.mjs')}},define:{__VUE_OPTIONS_API__:false,__VUE_PROD_DEVTOOLS__:false,__VUE_PROD_HYDRATION_MISMATCH_DETAILS__:false}};
await build({...common,build:{outDir:out,emptyOutDir:false,lib:{entry:resolve(here,'src/runtime.ts'),formats:['es'],fileName:()=> 'runtime.js'},minify:true}});
await build({...common,build:{outDir:out,emptyOutDir:false,lib:{entry:input,formats:['es'],fileName:()=> 'module.js'},rolldownOptions:{external:['vue','naive-ui'],output:{paths:{vue:'./runtime.js','naive-ui':'./runtime.js'}}},minify:true}});
const runtime=await import(pathToFileURL(resolve(out,'runtime.js'))), module=await import(pathToFileURL(resolve(out,'module.js')));
assert.equal(module.createApp,runtime.createApp); assert.equal(module.NButton,runtime.NButton);
const bytes=await readFile(resolve(out,'module.js'),'utf8'); assert.match(bytes,/runtime\.js/); assert.ok(bytes.length<1000);
await writeFile(resolve(out,'result.json'),JSON.stringify({kind:'production-library-shared-runtime-identity',vue:true,naive:true,moduleBytes:bytes.length,node:process.version},null,2)+'\n');
console.log('Minimal production shared runtime identities passed');
