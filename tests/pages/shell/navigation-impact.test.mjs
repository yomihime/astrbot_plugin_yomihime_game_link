import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
test('public owner navigation protects drafts across identity and lifecycle history',()=>{
 const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./navigation-impact-contract.mjs',import.meta.url))],{encoding:'utf8'});
 assert.equal(result.status,0,result.stdout+'\n'+result.stderr);assert.match(result.stdout,/hash\/history passed/);
});
