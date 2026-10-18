import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
for(const surface of ['management','shell'])for(const scenario of ['inherit','default','unchanged','conflict','read-failure','after-failure-inherit','after-failure-default','after-failure-unchanged','after-failure-conflict','after-failure-denied','after-failure-owner','font-denied','font-network','font-late','font-fence'])test(`built ${surface} management async ${scenario}`,()=>{
 const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./management-async-vm.mjs',import.meta.url)),surface,scenario],{encoding:'utf8'});
 assert.equal(result.status,0,result.stdout+'\n'+result.stderr);assert.match(result.stdout,/passed/);
});
