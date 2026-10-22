import {resolve, dirname, basename} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {readFile, writeFile, mkdir, readdir, lstat, realpath, mkdtemp, rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {mergeManifestResources} from './manifest-resources.mjs';
import {publishBuild} from './build-output.mjs';
const {build}=await import('vite').catch(error=>{
 if(error.code==='ERR_MODULE_NOT_FOUND')throw Error('Missing frontend dependencies; run npm ci --prefix pages/frontend before building',{cause:error});
 throw error;
});
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const frontend = resolve(root, 'pages/frontend');
const invocationRoot = process.cwd();
// Vite/Rolldown may derive minifier names from cwd; all entry points use one root.
process.chdir(frontend);
const args=process.argv.slice(2);
const outputOptions=args.filter(value=>value.startsWith('--out-root='));
const bundleRoots=args.filter(value=>value.startsWith('--bundle-root=')).map(value=>value.slice('--bundle-root='.length));
if(outputOptions.length>1||args.some(value=>!value.startsWith('--out-root=')&&!value.startsWith('--bundle-root=')))throw Error('Unknown or repeated build option');
if(!bundleRoots.length)bundleRoots.push('modules');
const safeRelative=value=>typeof value==='string'&&/^[-a-zA-Z0-9_./]+$/.test(value)&&value.split('/').every(part=>part&&part!=='.'&&part!=='..');
if(bundleRoots.some(value=>!safeRelative(value)))throw Error('Invalid reviewed build root');
const outputRoot=outputOptions.length?resolve(invocationRoot,outputOptions[0].slice('--out-root='.length)):root;
const stageRoot=await mkdtemp(resolve(tmpdir(),'ygl-frontend-'));
try {
const ids=new Set();
const shell = resolve(stageRoot, 'pages/00-game-link');
const aliases = {'@game-link/ui':resolve(root,'pages/public-ui.ts'),vue: resolve(frontend, 'node_modules/vue/dist/vue.runtime.esm-bundler.js'), 'naive-ui': resolve(frontend, 'node_modules/naive-ui/es/index.mjs')};
const defines = {'process.env.NODE_ENV': JSON.stringify('production'), __VUE_OPTIONS_API__: false, __VUE_PROD_DEVTOOLS__: false, __VUE_PROD_HYDRATION_MISMATCH_DETAILS__: false};
const common = {configFile: false, root: frontend, resolve: {alias: aliases}, define: defines, logLevel: 'info'};
// The Vue public export facade can disappear from chunk.modules after folding.
// Keep its package notice alongside the rendered implementation package notices.
const runtimePackages = new Set(['vue']);
const moduleAudit = [];
const runtimeAudit = {name: 'runtime-package-notices', generateBundle(_options, bundle) {
  for (const chunk of Object.values(bundle)) if (chunk.type === 'chunk') for (const [id, meta] of Object.entries(chunk.modules)) {
    const match = /node_modules[\\/](?:(@[^\\/]+)[\\/])?([^\\/]+)/.exec(id);
    if (match) {const name = (match[1] ? match[1] + '/' : '') + match[2];
      // Preserve the Vue export facade's attribution even when production
      // folding leaves its implementation entirely in the @vue packages.
      if (meta.renderedLength === 0 && name !== 'vue') continue;
      runtimePackages.add(name); moduleAudit.push({package:name,id:id.replaceAll('\\','/').split('/node_modules/')[1],renderedLength:meta.renderedLength});}
  }
}};
await mkdir(shell, {recursive: true});
await build({...common, plugins:[runtimeAudit], build: {outDir: shell, emptyOutDir: false, lib: {entry: resolve(frontend, 'src/runtime.ts'), formats: ['es'], fileName: () => 'runtime.js'}, minify: true, sourcemap: false}});
await build({...common, build: {outDir: shell, emptyOutDir: false, lib: {entry: resolve(frontend, 'src/main.ts'), formats: ['es'], fileName: () => 'app.js', cssFileName: 'styles'}, rolldownOptions: {external: ['vue', 'naive-ui', '@game-link/ui', './module-loader.js'], output: {paths: {'vue': './runtime.js', 'naive-ui': './runtime.js','@game-link/ui':'./runtime.js'}}}, minify: true}});
const projected=[];
const branches=[],stylePaths=new Set();
for(const bundleRoot of bundleRoots)for(const packageName of (await readdir(resolve(root,bundleRoot))).sort()) {
 const source=resolve(root,bundleRoot,packageName),destination=resolve(stageRoot,bundleRoot,packageName);
 const metadata=await lstat(source);
 if(metadata.isSymbolicLink())throw Error('Reviewed source cannot be a link');
 if(!metadata.isDirectory())continue;
 if(await realpath(source)!==source)throw Error('Reviewed source changed or traverses a link');
 let manifestSource;
 try {manifestSource=await readFile(resolve(source,'yomihime.manifest.json'),'utf8');}
 catch(error){if(error.code==='ENOENT'||error.code==='ENOTDIR')continue;throw error;}
 const manifest=JSON.parse(manifestSource),entries=new Map();
 if(!/^[a-z][a-z0-9_.-]*$/.test(packageName)||manifest.package_id!==packageName||ids.has(packageName)||!Array.isArray(manifest.modules))throw Error('Invalid or duplicate reviewed package identity');
 ids.add(packageName);
 const moduleIds=new Set();
 for(const module of manifest.modules){
  if(!/^[a-z][a-z0-9_.-]*$/.test(module.module_id)||moduleIds.has(module.module_id)||!Array.isArray(module.resources))throw Error('Invalid module identity');
  moduleIds.add(module.module_id);
  const paths=new Set();
  for(const resource of module.resources){
   if(!safeRelative(resource.path)||paths.has(resource.path.toLowerCase())||!/^[0-9a-f]{64}$/.test(resource.sha256))throw Error('Invalid declared resource');
   paths.add(resource.path.toLowerCase());
  }
  for(const page of module.pages||[]){
   if(!safeRelative(page.entry)||!paths.has(page.entry.toLowerCase())||(page.styles||[]).some(style=>!safeRelative(style)||!paths.has(style.toLowerCase())))throw Error('Page must reference declared resources');
  }
 }
 for(const module of manifest.modules)for(const page of module.pages||[]) {
  const match=/^pages\/dist\/([A-Za-z0-9_-]+)\.js$/.exec(page.entry);
  if(match)entries.set(match[1],page);
 }
 for(const [entry,page] of entries) {
  const styles=page.styles||[],style=styles.find(value=>/^pages\/dist\/[A-Za-z0-9_-]+\.css$/.test(value));
  await build({...common, build:{outDir:resolve(destination,'pages/dist'),emptyOutDir:false,
   lib:{entry:resolve(source,'pages/src',entry+'.ts'),formats:['es'],fileName:()=>entry+'.js',cssFileName:style?style.split('/').at(-1).slice(0,-4):'styles'},
   rolldownOptions:{external:['vue','naive-ui','@game-link/ui'],output:{minify:true,paths:{'vue':'../../../../../runtime.js','naive-ui':'../../../../../runtime.js','@game-link/ui':'../../../../../runtime.js'}}},minify:true}});
 }
 const generated=[];
 const generatedNames=new Set();
 for(const [entry,page] of entries){generatedNames.add(entry+'.js');for(const style of page.styles||[])if(style.startsWith('pages/dist/'))generatedNames.add(style.slice('pages/dist/'.length));}
 if(entries.size)for(const file of [...generatedNames].sort()) {
  const bytes=await readFile(resolve(destination,'pages/dist',file));
  generated.push({path:'pages/dist/'+file,sha256:createHash('sha256').update(bytes).digest('hex')});
 }
 for(const module of manifest.modules)module.resources=await mergeManifestResources(source,module.resources||[],generated);
 await mkdir(resolve(destination,'pages'),{recursive:true});
 await writeFile(resolve(destination,'pages/resources.json'),JSON.stringify(generated,null,2)+'\n');
 await writeFile(resolve(destination,'yomihime.manifest.json'),(JSON.stringify(manifest,null,2)+'\n').replaceAll('\n',manifestSource.includes('\r\n')?'\r\n':'\n'));
 for(const module of manifest.modules) {
  const owner=manifest.package_id+'/'+module.module_id;
  for(const resource of module.resources) {
   const generatedResource=resource.path.startsWith('pages/dist/')&&entries.size;
   const bytes=await readFile(resolve(generatedResource?destination:source,resource.path));
   if(createHash('sha256').update(bytes).digest('hex')!==resource.sha256)throw Error('Resource hash mismatch');
   const asset=resolve(shell,'module-assets',owner,resource.path);await mkdir(dirname(asset),{recursive:true});await writeFile(asset,bytes);
  }
  for(const page of module.pages||[]) {
   const entry='module-assets/'+owner+'/'+page.entry;
   branches.push('case '+JSON.stringify(entry)+': return import('+JSON.stringify('./'+entry)+');');
   for(const style of page.styles||[])stylePaths.add('module-assets/'+owner+'/'+style);
  }
 }
 projected.push({package:packageName,resources:generated});
}
await writeFile(resolve(shell,'module-loader.js'),"export async function loadPage(entry){switch(entry){\n"+[...new Set(branches)].sort().join('\n')+"\ndefault:throw Error('Unknown module page');}}\n");
const template='<template id="module-style-assets"></template>';
let index=await readFile(resolve(frontend,'src/index.html'),'utf8');
if(index.split(template).length!==2)throw Error('Expected one inert module style template');
const styles=[...stylePaths].sort().map(path=>'<link rel="stylesheet" data-resource="'+path+'" href="./'+path+'">').join('');
index=index.replace(template,'<template id="module-style-assets">'+styles+'</template>').replace('<html ','<html data-page-name="00-game-link" ');
await writeFile(resolve(shell,'index.html'),index);
await mkdir(resolve(shell, 'licenses'), {recursive: true});
const notices = ['Third-party notices for the production shared Vue / Naive UI runtime.\nSources are locked in pages/frontend/package-lock.json.\n'];
const licenseFiles=[];
for (const name of [...runtimePackages].sort()) {
  const directory=resolve(frontend,'node_modules',name), metadata=JSON.parse(await readFile(resolve(directory,'package.json'),'utf8'));
  const files=(await readdir(directory)).filter(file=>/^(?:licen[sc]e|copying|notice)(?:\.|$)/i.test(file));
  let content=`${name} ${metadata.version}\nLicense: ${metadata.license || 'see below'}\n`;
  if (files.length) for (const file of files.sort()) content+=`\n${file}\n${await readFile(resolve(directory,file),'utf8')}\n`;
  else if (name==='css-render' || name.startsWith('@css-render/')) content+='\n'+await readFile(resolve(frontend,'legal/css-render.txt'),'utf8');
  else if (name==='vdirs') content+='\n'+await readFile(resolve(frontend,'legal/vdirs.txt'),'utf8');
  else throw Error(`Missing license text for ${name}`);
  const file=name.replaceAll('/','_')+'.txt'; await writeFile(resolve(shell,'licenses',file),content); licenseFiles.push(`licenses/${file}`); notices.push(content);
}
await writeFile(resolve(shell,'THIRD_PARTY_NOTICES.txt'),notices.join('\n\n--------------------\n\n'));
const auditDir=resolve(stageRoot,'.build/frontend'); await mkdir(auditDir,{recursive:true});
await writeFile(resolve(auditDir,'runtime-packages.json'),JSON.stringify({packages:[...runtimePackages].sort(),facades:[{package:'vue',reason:'public runtime export facade; implementation rendered from @vue packages'}],modules:moduleAudit,licenseFiles},null,2)+'\n');
await publishBuild(stageRoot,outputRoot);
console.log(JSON.stringify({outputRoot,runtimeBytes:(await readFile(resolve(shell,'runtime.js'))).length,projected}));
} finally {
 if(dirname(stageRoot)!==resolve(tmpdir())||!basename(stageRoot).startsWith('ygl-frontend-'))throw Error('Invalid owned build staging directory');
 await rm(stageRoot,{recursive:true,force:true});
}
