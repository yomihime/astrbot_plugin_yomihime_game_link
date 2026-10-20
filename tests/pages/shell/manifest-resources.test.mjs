import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,readFile,rm,symlink,realpath} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {mergeManifestResources} from '../../../pages/frontend/manifest-resources.mjs';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const generated=[{path:'pages/dist/entry.js',sha256:'b'.repeat(64)},{path:'pages/dist/styles.css',sha256:'c'.repeat(64)}];
test('frontend manifest rebuild preserves non-page resources and recomputes asset hashes',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ygl-theme-resources-'));
 try{
  await mkdir(join(root,'assets'));await writeFile(join(root,'assets/logo.svg'),'<svg>new bytes</svg>');
  const existing=[{path:'pages/dist/old.js',sha256:'a'.repeat(64)},{path:'assets/logo.svg',sha256:'d'.repeat(64)}],before=structuredClone(existing);
  const result=await mergeManifestResources(root,existing,generated);
  assert.deepEqual(result,[{path:'assets/logo.svg',sha256:hash('<svg>new bytes</svg>')},...generated]);assert.deepEqual(existing,before);assert.equal(result.some(resource=>resource.path==='pages/dist/old.js'),false);
  assert.deepEqual(await mergeManifestResources(root,result,generated),result);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('resource merge rejects missing and malformed package assets',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ygl-theme-resource-failure-'));
 try{
  await mkdir(join(root,'package'));
  for(const path of ['../outside.svg','/outside.svg','assets//logo.svg','assets/./logo.svg','assets\\logo.svg','missing.svg'])await assert.rejects(mergeManifestResources(join(root,'package'),[{path,sha256:'a'.repeat(64)}],generated));
  await assert.rejects(mergeManifestResources(root,[],[{path:'assets/logo.svg',sha256:'a'.repeat(64)}]));
  await assert.rejects(mergeManifestResources(root,[],[generated[0],{...generated[0],path:'pages/dist/ENTRY.js'}]));
 }finally{await rm(root,{recursive:true,force:true});}
});
test('resource merge rejects a real path escaping through a platform-supported link',async()=>{
 const root=await mkdtemp(join(tmpdir(),'ygl-theme-resource-link-failure-'));
 try{
  await mkdir(join(root,'package'));
  let resource;
  if(process.platform==='win32'){
   // Directory junctions do not require the file-symlink creation privilege.
   // This proves the same realpath containment guard, not Windows file-symlink behavior.
   await mkdir(join(root,'outside-dir'));await writeFile(join(root,'outside-dir/asset.svg'),'<svg/>');
   await symlink(join(root,'outside-dir'),join(root,'package/escape'),'junction');resource='escape/asset.svg';
   assert.equal(await realpath(join(root,'package',resource)),await realpath(join(root,'outside-dir/asset.svg')));
  }else{
   await writeFile(join(root,'outside.svg'),'<svg/>');await symlink(join(root,'outside.svg'),join(root,'package/escape.svg'));resource='escape.svg';
   assert.equal(await realpath(join(root,'package',resource)),await realpath(join(root,'outside.svg')));
  }
  await assert.rejects(mergeManifestResources(join(root,'package'),[{path:resource,sha256:'a'.repeat(64)}],generated),/Manifest resource escapes package/);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('published FF14 manifest includes its logo and generated page resource contract',async()=>{
 const manifest=JSON.parse(await readFile(new URL('../../../modules/ff14/yomihime.manifest.json',import.meta.url),'utf8'));
 const pageResources=JSON.parse(await readFile(new URL('../../../modules/ff14/pages/resources.json',import.meta.url),'utf8'));
 const module=manifest.modules.find(module=>module.module_id==='ff14'),logo=module.resources.find(resource=>resource.path===module.display.logo_resource);
 assert.ok(logo);assert.equal(logo.sha256,hash(await readFile(new URL('../../../modules/ff14/'+logo.path,import.meta.url))));
 assert.deepEqual(module.resources.filter(resource=>resource.path.startsWith('pages/dist/')),pageResources);assert.ok(pageResources.every(resource=>resource.path.startsWith('pages/dist/')));
});
