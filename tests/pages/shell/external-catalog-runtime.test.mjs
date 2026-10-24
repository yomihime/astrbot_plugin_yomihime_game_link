// Real Python metadata projection with validated synthetic external manifests.
// This does not start a Host instance or prove browser/HTTP authorization.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {root,sourceModule} from './compile.mjs';
const {validateCatalog}=await sourceModule(root+'pages/frontend/src/catalog.ts');
const external=JSON.parse(execFileSync(process.env.YGL_TEST_PYTHON||'python',['-B','-m','tests.fixtures.external_page_catalog'],{
 cwd:root,encoding:'utf8',timeout:60000,env:{...process.env,PYTHONIOENCODING:'utf-8'},
}));

test('paired external logo uses the same projected resource identity',()=>{
 const before=structuredClone(external['logo-matched']);
 const module=validateCatalog(external['logo-matched']).modules[0];
 assert.match(module.display.logo_resource,/^module-assets\/minimal\/demo\/pages\/p[a-f0-9]{64}\/assets\/logo\.svg$/);
 assert.ok(module.resources.some(resource=>resource.path===module.display.logo_resource));
 assert.deepEqual(external['logo-matched'],before);
 const stale=structuredClone(before);stale.modules[0].display.logo_resource='module-assets/minimal/demo/assets/logo.svg';
 assert.throws(()=>validateCatalog(stale),/invalid_catalog/);
});

test('unpaired or hidden external resources also hide the display logo',()=>{
 for(const name of ['logo-mismatch','logo-withdrawn','logo-resources-hidden','logo-projection-missing']) {
  const module=validateCatalog(external[name]).modules[0];
  assert.deepEqual(module.pages,[],name);assert.deepEqual(module.resources,[],name);
  assert.equal(module.display.logo_resource,null,name);
 }
});

test('external modules without a logo validate in matched and hidden branches',()=>{
 for(const name of ['no-logo-matched','no-logo-mismatch','no-logo-withdrawn']) {
  const module=validateCatalog(external[name]).modules[0];
  assert.equal(module.display.logo_resource,null,name);
  assert.equal(module.pages.length>0,name==='no-logo-matched',name);
 }
});
