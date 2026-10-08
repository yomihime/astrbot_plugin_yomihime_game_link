// Formal built app + offline bridge. This does not claim real Host/browser acceptance.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
for(const scenario of ['draft','configuration','configuration-success'])test('built shell management revision: '+scenario,()=>{
  const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./management-revisions-vm.mjs',import.meta.url)),scenario],{encoding:'utf8'});
  assert.equal(result.status,0,result.stdout+'\n'+result.stderr);
  assert.match(result.stdout,/passed/);
});
