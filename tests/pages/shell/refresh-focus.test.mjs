import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {root} from './compile.mjs';

test('built refresh retains focus without native disabled or asynchronous focus restoration',()=>{
 const result=spawnSync(process.execPath,['--experimental-vm-modules',resolve(root,'tests/pages/shell/refresh-focus-contract.mjs')],{encoding:'utf8'});
 assert.equal(result.status,0,result.stdout+'\n'+result.stderr);
 assert.match(result.stdout,/refresh focus contract passed/);
});
