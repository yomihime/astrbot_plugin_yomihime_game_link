import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {sourceModule,root} from './compile.mjs';

const {normalizeUiLocale,translateUi}=await sourceModule(root+'pages/frontend/src/i18n/locale.ts');
const {readPageLanguage}=await sourceModule(root+'pages/frontend/src/i18n/page-language.ts');

test('AstrBot language aliases map to four locales with a deterministic missing key fallback',()=>{
  for(const [input,expected] of Object.entries({'zh':'zh-CN','zh_CN':'zh-CN','zh-HK':'zh-TW',
    'zh-Hant':'zh-TW','zh-Hans-TW':'zh-CN',ja:'ja-JP','ja-JP':'ja-JP','en-GB':'en-US'})){
    assert.equal(normalizeUiLocale(input),expected,input);
  }
  for(const input of [null,'','zh--HK','ko-KR','en/?','auto',{}])assert.equal(normalizeUiLocale(input),null);
  const messages={'zh-CN':{summary:'{count} 项 {count} {missing}',empty:''},'ja-JP':{summary:'{count} 件 {count} {missing}'}};
  assert.equal(translateUi(messages,'ja','summary',{count:3}),'3 件 3 {missing}');
  assert.equal(translateUi(messages,'en','summary',{count:0}),'0 项 0 {missing}');
  assert.equal(translateUi(messages,'ja','empty'),'');
  assert.equal(translateUi(messages,'en','toString'),'toString');
  const inherited=Object.assign(Object.create({'ja-JP':{summary:'inherited'}}),{'zh-CN':{summary:'default'}});
  assert.equal(translateUi(inherited,'ja','summary'),'default');
  const invalid={'zh-CN':{summary:'{count} 项 {count}'},'ja-JP':{summary:'{count} 件'}};
  assert.equal(translateUi(invalid,'ja','summary',{count:3}),'3 项 3');
  const reordered={'zh-CN':{summary:'{count} 项 {label}'},'ja-JP':{summary:'{label} {count} 件'}};
  assert.equal(translateUi(reordered,'ja','summary',{count:3,label:'A'}),'A 3 件');
});

test('old settings omit the row and a verified invalid value returns to automatic following',()=>{
  const row=(value,state='valid',present=true)=>({'game_link/core':{revision:5,fields:{
    page_locale:{state,present,value,source:present?'sqlite':'default'},
  }}});
  assert.equal(readPageLanguage({'game_link/core':{revision:4,fields:{}}}),null);
  assert.deepEqual(readPageLanguage(row('auto','valid',false)),{revision:5,preference:'auto'});
  assert.deepEqual(readPageLanguage(row(null,'invalid')),{revision:5,preference:'auto'});
  assert.deepEqual(readPageLanguage(row('en-US')),{revision:5,preference:'en-US'});
  assert.equal(readPageLanguage(row(null)),null);
});

test('all Core catalogs keep the same keys and named placeholder multiplicity',async()=>{
  const dirs=['pages/frontend/src/i18n','modules/ff14/pages/src/i18n'];
  for(const dir of dirs){
    const [base,...others]=await Promise.all(['zh-CN','zh-TW','ja-JP','en-US'].map(async locale=>
      JSON.parse(await readFile(root+dir+'/'+locale+'.json','utf8'))));
    const keys=Object.keys(base).sort();
    const slots=text=>[...text.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)].map(match=>match[1]).sort();
    for(const localized of others){
      assert.deepEqual(Object.keys(localized).sort(),keys,dir);
      for(const key of keys){
        assert.equal(typeof localized[key],'string',dir+'/'+key);
        assert.equal(localized[key].length>0,base[key].length>0,dir+'/'+key);
        assert.deepEqual(slots(localized[key]),slots(base[key]),dir+'/'+key);
      }
    }
  }
});
