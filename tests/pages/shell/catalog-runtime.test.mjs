// Real offline AstrBotRuntime -> public_module_catalog -> JSON -> source validator.
// This does not start a Host instance or prove browser/HTTP authorization.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {root,sourceModule} from './compile.mjs';
const {validateCatalog}=await sourceModule(root+'pages/frontend/src/catalog.ts');
const projected=JSON.parse(execFileSync(process.env.YGL_TEST_PYTHON||'python',['-B','-m','tests.fixtures.module_catalog'],{
 cwd:root,encoding:'utf8',timeout:60000,env:{...process.env,PYTHONIOENCODING:'utf-8'},
}));
const general='module-assets/neutral/demo/assets/fonts/demo.woff2';

test('actual backend catalog accepts bundled logo and neutral non-page font resource',()=>{
 const before=structuredClone(projected),checked=validateCatalog(projected);
 assert.equal(checked.runtime.state,'ready');assert.equal(checked.modules.length,2);
 const ff14=checked.modules.find(m=>m.module_id==='ff14/ff14'),neutral=checked.modules.find(m=>m.module_id==='neutral/demo');
 assert.ok(ff14.resources.some(r=>r.path==='module-assets/ff14/ff14/assets/ff14-meteor.svg'));
 assert.ok(neutral.resources.some(r=>r.path===general));assert.ok(ff14.pages.length);assert.ok(neutral.pages.length);
 assert.deepEqual(projected,before);
});

test('general resource identities reject cross-owner, malformed paths, hashes and types',()=>{
 const invalid=[
  'module-assets/ff14/ff14/assets/fonts/demo.woff2',
  'module-assets/neutral/demo/../demo.woff2','module-assets/neutral/demo/assets/./demo.woff2',
  '/module-assets/neutral/demo/assets/demo.woff2','https://example.com/demo.woff2','C:/demo.woff2',
  'module-assets/neutral/demo/assets\\demo.woff2','module-assets/neutral/demo/assets/%2e%2e/demo.woff2',
  'module-assets/neutral/demo/assets/%64emo.woff2','module-assets/neutral/demo/assets//demo.woff2',
  'module-assets/neutral/demo/assets/demo.woff2/','module-assets/neutral/demo/assets/demo.html',
  'module-assets/neutral/demo/assets/demo.woff2?x=1','module-assets/neutral/demo/assets/demo.woff2#x',
  'module-assets/neutral/demo/assets/demo.woff2.','module-assets/neutral/demo/assets/CON.woff2',
  'module-assets/neutral/demo/assets/'+ 'a'.repeat(240)+'.woff2',null,42,{},[],
 ];
 for(const path of invalid){const value=structuredClone(projected);value.modules.find(m=>m.module_id==='neutral/demo').resources.find(r=>r.path===general).path=path;assert.throws(()=>validateCatalog(value),/invalid_catalog/,JSON.stringify(path));}
 for(const sha256 of [null,42,'a'.repeat(63),'A'.repeat(64),'z'.repeat(64)]){const value=structuredClone(projected);value.modules[0].resources[0].sha256=sha256;assert.throws(()=>validateCatalog(value),/invalid_catalog/);}
 const duplicate=structuredClone(projected);duplicate.modules[0].resources.push({...duplicate.modules[0].resources[0]});assert.throws(()=>validateCatalog(duplicate),/invalid_catalog/);
});

test('general resources cannot become non-pages or wrong-type executable page entries/styles',()=>{
 for(const [field,path] of [
  ['entry','module-assets/neutral/demo/assets/entry.js'],['styles','module-assets/neutral/demo/assets/style.css'],
  ['entry',general],['styles',general],
  ['entry','module-assets/neutral/demo/pages/dist/not-js.css'],['styles','module-assets/neutral/demo/pages/dist/not-css.js'],
  ['entry','module-assets/ff14/ff14/pages/dist/entry.js'],['styles','module-assets/ff14/ff14/pages/dist/styles.css'],
  ['entry','module-assets/neutral/demo/pages/dist/../entry.js'],['styles','module-assets/neutral/demo/pages/dist/%73tyle.css'],
 ]){const value=structuredClone(projected),module=value.modules.find(m=>m.module_id==='neutral/demo');module.resources.push({path,sha256:'a'.repeat(64)});module.pages[0][field]=field==='styles'?[path]:path;assert.throws(()=>validateCatalog(value),/invalid_catalog/,field+': '+path);}
 const undeclared=structuredClone(projected);undeclared.modules[0].pages[0].entry='module-assets/ff14/ff14/pages/dist/undeclared.js';assert.throws(()=>validateCatalog(undeclared),/invalid_catalog/);
});
