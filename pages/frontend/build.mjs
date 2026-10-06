import {build} from 'vite';
import {resolve, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {readFile, writeFile, mkdir, readdir, copyFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const frontend = resolve(root, 'pages/frontend');
const shell = resolve(root, 'pages/shell');
const ff14 = resolve(root, 'modules/ff14/pages');
const aliases = {vue: resolve(frontend, 'node_modules/vue/dist/vue.runtime.esm-bundler.js'), 'naive-ui': resolve(frontend, 'node_modules/naive-ui/es/index.mjs')};
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
await build({...common, build: {outDir: shell, emptyOutDir: false, lib: {entry: resolve(frontend, 'src/main.ts'), formats: ['es'], fileName: () => 'app.js', cssFileName: 'styles'}, rolldownOptions: {external: ['vue', 'naive-ui', './module-loader.js'], output: {paths: {'vue': './runtime.js', 'naive-ui': './runtime.js'}}}, minify: true}});
await build({...common, build: {outDir: resolve(root, 'pages/management'), emptyOutDir: false, lib: {entry: resolve(frontend, 'src/management.js'), formats: ['es'], fileName: () => 'app.js'}, rolldownOptions: {external: ['vue', 'naive-ui'], output: {paths: {'vue': './runtime.js', 'naive-ui': './runtime.js'}}}, minify: true}});
await build({...common, build: {outDir: resolve(ff14, 'dist'), emptyOutDir: true, lib: {entry: resolve(ff14, 'src/entry.ts'), formats: ['es'], fileName: () => 'entry.js', cssFileName: 'styles'}, rolldownOptions: {external: ['vue', 'naive-ui'], output: {paths: {'vue': '../../../../../runtime.js', 'naive-ui': '../../../../../runtime.js'}}}, minify: true}});
await copyFile(resolve(frontend, 'src/index.html'), resolve(shell, 'index.html'));
await copyFile(resolve(frontend, 'src/management-index.html'), resolve(root, 'pages/management/index.html'));
await copyFile(resolve(ff14, 'src/query-contract.js'), resolve(root, 'pages/ff14/query-contract.js'));
const resources = [];
for (const file of (await readdir(resolve(ff14, 'dist'))).sort()) {
  const bytes = await readFile(resolve(ff14, 'dist', file));
  resources.push({path: `pages/dist/${file}`, sha256: createHash('sha256').update(bytes).digest('hex')});
}
await writeFile(resolve(ff14, 'resources.json'), JSON.stringify(resources, null, 2) + '\n');
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
const auditDir=resolve(root,'.architecture-refactor/modular-r0-r3'); await mkdir(auditDir,{recursive:true});
await writeFile(resolve(auditDir,'runtime-packages.json'),JSON.stringify({packages:[...runtimePackages].sort(),facades:[{package:'vue',reason:'public runtime export facade; implementation rendered from @vue packages'}],modules:moduleAudit,licenseFiles},null,2)+'\n');
console.log(JSON.stringify({runtimeBytes: (await readFile(resolve(shell, 'runtime.js'))).length, resources}));

for (const page of ['management','00-game-link']) {
  const target=resolve(root,'pages',page);await mkdir(resolve(target,'licenses'),{recursive:true});
  for(const file of ['runtime.js','THIRD_PARTY_NOTICES.txt',...licenseFiles])await copyFile(resolve(shell,file),resolve(target,file));
}
const defaultRoot=resolve(root,'pages/00-game-link');
for(const file of ['index.html','app.js','styles.css'])await copyFile(resolve(shell,file),resolve(defaultRoot,file));
await writeFile(resolve(defaultRoot,'index.html'),(await readFile(resolve(shell,'index.html'),'utf8')).replace('<html ', '<html data-page-name="00-game-link" '));
