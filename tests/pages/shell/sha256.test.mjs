import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {sourceBundle} from './source-build.mjs';
// Test production-compiled code, including the bundled fallback, not a copy.
const code=await sourceBundle('pages/frontend/src/sha256.ts');
const {sha256Hex}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
const original=Object.getOwnPropertyDescriptor(globalThis,'crypto');
const setCrypto=value=>Object.defineProperty(globalThis,'crypto',{configurable:true,value});
const hex=bytes=>createHash('sha256').update(bytes).digest('hex');
const vectors=[
 [new Uint8Array(),'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'],
 [new TextEncoder().encode('abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'],
 [new TextEncoder().encode('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'),'248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1'],
 [new Uint8Array(1000000).fill(97),'cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0'],
];
const additional=[new TextEncoder().encode('蒸汽 DLC 🎮\0é'),... [1,55,56,63,64,65,119,120,127,128,129,1024,4194304].map(size=>Uint8Array.from({length:size},(_,i)=>(i*137+255)%256))];
for(const bytes of additional)vectors.push([bytes,hex(bytes)]);
for(const [mode,crypto] of [['native',webcrypto],['no crypto',undefined],['no subtle',{}]])test(`SHA-256 known, Unicode, binary, padding, multi-block and max-resource vectors: ${mode}`,async()=>{
 let calls=0;
 try{setCrypto(mode==='native'?{subtle:{digest(...args){calls++;return webcrypto.subtle.digest(...args);}}}:crypto);
  for(const [bytes,expected] of vectors)assert.equal(await sha256Hex(bytes),expected);
  const offset=new Uint8Array([99,0,255,128,17,99]).subarray(1,5);assert.equal(await sha256Hex(offset),hex(offset));
  assert.equal(calls,mode==='native'?vectors.length+1:0);
 }finally{Object.defineProperty(globalThis,'crypto',original);}
});
test('present but failing native digest fails closed, without fallback',async()=>{
 try{setCrypto({subtle:{digest:async()=>{throw Error('native failure');}}});await assert.rejects(sha256Hex(new Uint8Array()),/asset_digest_unavailable/);}
 finally{Object.defineProperty(globalThis,'crypto',original);}
});
