import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readFile} from 'node:fs/promises';
import {validateCatalog,validateSnapshot,updateBody} from '../../../pages/management/app.js';
import {themeCatalog,themeSnapshot} from './theme-settings-fixtures.mjs';
const field=(catalog,owner='ff14/ff14')=>catalog.fields.find(f=>f.module_id===owner&&f.group==='reply_style');
test('Core theme choices validate exact enum projection and immutable catalog labels',()=>{
 const declarations=themeCatalog(),checked=validateCatalog(declarations);
 assert.equal(validateSnapshot(themeSnapshot(),checked)['ff14/ff14'].fields.reply_theme.value,null);
 field(declarations).choices[1].label='Changed';assert.equal(field(checked).choices[1].label,'暖桃纸');assert.ok(Object.isFrozen(field(checked).choices[1]));
 const reject=change=>{const value=themeCatalog();change(field(value),value);assert.throws(()=>validateCatalog(value),/invalid_config/);};
 for(const edit of [f=>f.choices=null,f=>f.choices=[],f=>f.choices.pop(),f=>f.choices.push({value:'extra',label:'额外'}),f=>f.choices[1].value='unknown',f=>f.choices[2].value=f.choices[1].value,f=>f.choices.reverse(),f=>f.choices[1].extra=true,f=>f.choices[1].label='',f=>f.choices[1].label=' ',f=>f.choices[1].label='a\u0000',f=>f.choices[1].label='a'.repeat(129),f=>f.choices[1].label=1,f=>f.choices[0].value='null',f=>f.default='warm_paper',f=>f.group='general',f=>f.name='other',f=>f.value_schema.type='number',f=>f.value_schema.enum.push(null),(_f,c)=>field(c,'game_link/core').choices.unshift({value:null,label:'跟随核心'}),(_f,c)=>field(c,'game_link/core').default=null])reject(edit);
 const ordinary=themeCatalog();ordinary.fields[0].choices=[{value:'value',label:'Label'}];assert.throws(()=>validateCatalog(ordinary));
 const compatible=themeCatalog();delete field(compatible,'game_link/core').choices;assert.doesNotThrow(()=>validateCatalog(compatible));
});
test('only absent Core-managed module themes accept a valid null snapshot',()=>{
 const checked=validateCatalog(themeCatalog());
 const reject=change=>{const values=themeSnapshot();change(values);assert.throws(()=>validateSnapshot(values,checked),/invalid_config/);};
 for(const edit of [v=>v['ff14/ff14'].fields.reply_theme.present=true,v=>v['ff14/ff14'].fields.reply_theme.source='sqlite',v=>v['game_link/core'].fields.image_theme.value=null,v=>v['game_link/core'].fields.default_region.value=null,v=>delete v['ff14/ff14'].fields.reply_theme,v=>v['ff14/ff14'].fields.reply_theme.value='unknown'])reject(edit);
 const invalid=themeSnapshot();Object.assign(invalid['ff14/ff14'].fields.reply_theme,{state:'invalid',present:true,source:'sqlite'});assert.doesNotThrow(()=>validateSnapshot(invalid,checked));
 const declarations=themeCatalog();delete field(declarations).choices;assert.throws(()=>validateSnapshot(themeSnapshot(),validateCatalog(declarations)));
});
test('theme update conversion preserves owner/CAS, explicit themes and inheritance clearing',()=>{
 const checked=validateCatalog(themeCatalog());
 assert.deepEqual(updateBody('game_link/core',7,{image_theme:{mode:'replace',value:'2'}},checked),{module_id:'game_link/core',expected_revision:7,updates:[{field:'image_theme',mode:'replace',value:'deep_indigo'}]});
 assert.deepEqual(updateBody('ff14/ff14',8,{reply_theme:{mode:'replace',value:'1'}},checked).updates,[{field:'reply_theme',mode:'replace',value:'warm_paper'}]);
 for(const mode of ['replace','clear'])assert.deepEqual(updateBody('ff14/ff14',8,{reply_theme:{mode,value:'0'}},checked).updates,[{field:'reply_theme',mode:'clear'}]);
 assert.deepEqual(updateBody('game_link/core',9,{image_theme:{mode:'clear'}},checked).updates,[{field:'image_theme',mode:'clear'}]);
 for(const raw of ['',null,'null','-1','4','warm_paper'])assert.throws(()=>updateBody('ff14/ff14',8,{reply_theme:{mode:'replace',value:raw}},checked));
 assert.throws(()=>updateBody('ff14/ff14',7,{image_theme:{mode:'clear'}},checked));assert.throws(()=>updateBody('game_link/core',7,{reply_theme:{mode:'clear'}},checked));
 for(const blocked of [{readable:false,editable:false,blocked_reason:'not_granted'},{readable:true,editable:false,blocked_reason:'semantic_validator_unavailable'}]){const declarations=themeCatalog();Object.assign(field(declarations),blocked);for(const mode of ['replace','clear'])assert.throws(()=>updateBody('ff14/ff14',7,{reply_theme:{mode,value:'0'}},validateCatalog(declarations)),/admin_authorization_denied/);}
});
test('theme labels/palette are never copied into frontend implementation',async()=>{
 for(const name of ['management.js','management-view.ts','setting-choices.ts','ui/presentation.ts'])assert.doesNotMatch(await readFile(new URL('../../../pages/frontend/src/'+name,import.meta.url),'utf8'),/warm_paper|cool_white|deep_indigo|暖桃纸|冷白|深靛/);
});
test('built shell theme controls retain drafts, Core/module isolation, clear semantics and refresh fences',()=>{
 const result=spawnSync(process.execPath,['--experimental-vm-modules',fileURLToPath(new URL('./theme-settings-contract.mjs',import.meta.url))],{encoding:'utf8'});
 assert.equal(result.status,0,result.stdout+'\n'+result.stderr);assert.match(result.stdout,/theme settings passed/);
});
