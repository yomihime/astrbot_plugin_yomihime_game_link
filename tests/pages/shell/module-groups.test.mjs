import {test} from 'node:test';
import assert from 'node:assert/strict';
import {sourceModule,root} from './compile.mjs';
import {catalog} from './fixtures.mjs';
import {harness} from './single-root-fixture.mjs';
const {validateCatalog,moduleGroups}=await sourceModule(root+'pages/frontend/src/catalog.ts');
test('source metadata is optional and bound to verified package identity',()=>{
 const value=catalog(),id=value.modules[0].module_id.split('/')[0];
 assert.deepEqual(validateCatalog(value).modules[0].source,{kind:'unknown',package_id:id,display_name:null,module_order:[]});
 for(const source of [{kind:['builtin'],package_id:id},{kind:null,package_id:id},{kind:{},package_id:id},{kind:'builtin',package_id:'wrong'},{kind:'invented',package_id:id},{kind:'builtin',package_id:id,path:'/private'}]){
  value.modules[0].source=source;assert.throws(()=>validateCatalog(value),/invalid_catalog/);
 }
 value.modules[0].source={kind:'builtin',package_id:id};assert.equal(validateCatalog(value).modules[0].source.kind,'builtin');
});
test('grouping combines package members without inferring source or invented DLCs',()=>{
 const module=(id,kind)=>({module_id:id,source:{kind,package_id:id.split('/')[0]}});
 const groups=moduleGroups([module('ff14/ff14','builtin'),...['cs2','deadlock','dota2','steam'].map(id=>module('steam_dlc/'+id,'extension'))]);
 assert.deepEqual(groups.map(g=>[g.title,g.modules.length]),[['内置模块',1],['steam_dlc',4]]);
 assert.equal(moduleGroups([module('ff14/ff14','unknown')])[0].title,'ff14 · 来源未识别');
 assert.equal(moduleGroups([module('same/a','builtin'),module('same/b','extension')])[0].title,'same · 来源未识别');
 assert.equal(moduleGroups([module('x/a','builtin'),module('builtin/b','extension')]).length,2);
 assert.deepEqual(moduleGroups([]),[]);
 assert.deepEqual(moduleGroups([module('z/a','builtin'),module('a/b','builtin'),module('dlc2/a','extension'),module('dlc1/a','extension')]).map(g=>[g.title,g.modules.length]),[['内置模块',2],['dlc1',1],['dlc2',1]]);
});
test('group headings preserve module links and unknown provenance is not labelled builtin',async()=>{
 const h=await harness(catalog());try{
  assert.match(h.doc.querySelector('.workspace-package-title').textContent,/来源未识别/);
  assert.ok(h.doc.querySelector('.workspace-package-grid .workspace-module h2 a'));
  assert.equal(h.doc.querySelector('.workspace-module h2').getAttribute('aria-level'),'4');
 }finally{h.close();}
});


test('package display names are optional bounded text with empty fallback',()=>{
 const value=catalog(),id=value.modules[0].module_id.split('/')[0];
 for(const display_name of [undefined,null,'','   ','　']){
  value.modules[0].source={kind:'extension',package_id:id,display_name};
  const parsed=validateCatalog(value);assert.equal(parsed.modules[0].source.display_name,null);assert.equal(moduleGroups(parsed.modules)[0].title,id);
 }
 for(const display_name of ['真实扩展','😀'.repeat(128),'<b>文字</b>']){
  value.modules[0].source={kind:'extension',package_id:id,display_name};assert.equal(validateCatalog(value).modules[0].source.display_name,display_name);
 }
 for(const display_name of [false,1,[],{},'😀'.repeat(129),'line\nbreak','\t','zero\u200bwidth','\ud800']){
  value.modules[0].source={kind:'extension',package_id:id,display_name};assert.throws(()=>validateCatalog(value),/invalid_catalog/);
 }
});
test('same labels do not merge different packages and inconsistent labels fall back',()=>{
 const module=(id,name)=>({module_id:id,source:{kind:'extension',package_id:id.split('/')[0],display_name:name}});
 const groups=moduleGroups([module('one/a','扩展'),module('two/a','扩展')]);
 assert.equal(groups.length,2);assert.deepEqual(groups.map(g=>g.title),['扩展','扩展']);assert.notEqual(groups[0].key,groups[1].key);
 assert.equal(moduleGroups([module('one/a','甲'),module('one/b','乙')])[0].title,'one');
});
test('package display names render literally in group headings',async()=>{
 const data=catalog(),id=data.modules[0].module_id.split('/')[0];data.modules[0].source={kind:'extension',package_id:id,display_name:'<b>扩展</b>'};
 const h=await harness(data);try{const heading=h.doc.querySelector('.workspace-package-title');assert.equal(heading.textContent,'<b>扩展</b>');assert.equal(heading.querySelector('b'),null);}finally{h.close();}
});


test('package order is optional validated local IDs and absent members are allowed',()=>{
 const value=catalog(),id=value.modules[0].module_id.split('/')[0];
 for(const module_order of [undefined,null,[],['not_visible'],['a'.repeat(129)]]){value.modules[0].source={kind:'extension',package_id:id,module_order};assert.deepEqual(validateCatalog(value).modules[0].source.module_order,module_order||[]);}
 for(const module_order of ['first',[null],['x','x'],['other/x'],['X'],Array(65).fill('x')]){value.modules[0].source={kind:'extension',package_id:id,module_order};assert.throws(()=>validateCatalog(value),/invalid_catalog/);}
});
test('shared grouping uses declared order then localized names and stable IDs',()=>{
 const module=(id,name,order)=>({module_id:'pkg/'+id,display:{default_name:name,localized_names:{},short_name:null},source:{kind:'extension',package_id:'pkg',module_order:order}});
 const rows=[module('z','Beta',['z','not_visible']),module('b','Alpha',['z','not_visible']),module('a','Alpha',['z','not_visible'])];
 assert.deepEqual(moduleGroups(rows,'en')[0].modules.map(m=>m.module_id),['pkg/z','pkg/a','pkg/b']);
 rows[0].source.module_order=[];
 assert.deepEqual(moduleGroups(rows,'en')[0].modules.map(m=>m.module_id),['pkg/a','pkg/b','pkg/z']);
});
