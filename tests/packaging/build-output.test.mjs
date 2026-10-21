import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, readFile, link, symlink, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve} from 'node:path';
import {publishBuild} from '../../pages/frontend/build-output.mjs';

test('absolute output root publishes staged files', async t => {
  const root=await mkdtemp(resolve(tmpdir(),'ygl-output-'));t.after(()=>rm(root,{recursive:true,force:true}));
  const stage=resolve(root,'stage'),out=resolve(root,'absolute-out');await mkdir(stage);await writeFile(resolve(stage,'app.js'),'new');
  await publishBuild(stage,out);assert.equal(await readFile(resolve(out,'app.js'),'utf8'),'new');
});
test('late hardlink destination fails before any original changes',async t=>{
  const root=await mkdtemp(resolve(tmpdir(),'ygl-output-'));t.after(()=>rm(root,{recursive:true,force:true}));
  const stage=resolve(root,'stage'),out=resolve(root,'out');await mkdir(stage);await mkdir(out);
  for(const file of ['a.js','z.js'])await writeFile(resolve(stage,file),'new');
  await writeFile(resolve(out,'a.js'),'previous');await writeFile(resolve(root,'sentinel'),'preserve');await link(resolve(root,'sentinel'),resolve(out,'z.js'));
  await assert.rejects(publishBuild(stage,out),/hard link/);
  assert.equal(await readFile(resolve(out,'a.js'),'utf8'),'previous');assert.equal(await readFile(resolve(root,'sentinel'),'utf8'),'preserve');
});
test('output directory junction or symlink never follows target',async t=>{
  const root=await mkdtemp(resolve(tmpdir(),'ygl-output-'));t.after(()=>rm(root,{recursive:true,force:true}));
  const stage=resolve(root,'stage'),outside=resolve(root,'outside');await mkdir(stage);await mkdir(outside);await writeFile(resolve(stage,'app.js'),'new');
  await symlink(outside,resolve(root,'linked'),process.platform==='win32'?'junction':'dir');
  await assert.rejects(publishBuild(stage,resolve(root,'linked')),/link|reparse/);
  await assert.rejects(readFile(resolve(outside,'app.js')),/ENOENT/);
});
