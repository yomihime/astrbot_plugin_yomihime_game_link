import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {harness} from './single-root-fixture.mjs';
test('project footer links bilingual name safely with a decorative trailing sparkle',async()=>{
 const h=await harness();try{
  const footer=h.doc.querySelector('.shell-footer'),link=footer.querySelector('a');
  assert.equal(link.getAttribute('href'),'https://github.com/yomihime/astrbot_plugin_yomihime_game_link');
  assert.equal(link.target,'_blank');assert.equal(link.rel,'noopener noreferrer');
  assert.equal(link.textContent,'如月怜的游戏连结 / Yomihime Game Link ✧');
  assert.equal(link.querySelector('.shell-project-spark').getAttribute('aria-hidden'),'true');
  assert.equal(footer.querySelectorAll('a').length,1);assert.doesNotMatch(link.textContent,/↗|☾/);
 }finally{h.close();}
});
test('footer separator is not a link and footer remains in natural flow',async()=>{
 const css=await readFile(new URL('../../../pages/frontend/src/shell.css',import.meta.url),'utf8');
 assert.match(css,/\.shell-footer:before\{content:'';display:block;height:1px/);
 assert.match(css,/\.shell-footer \.shell-project-link\{[^}]*min-height:40px;max-width:100%/);
 assert.match(css,/\.shell-project-english\{white-space:nowrap\}/);
 assert.match(css,/\.shell-footer\{flex:none;margin-top:auto\}/);
 assert.doesNotMatch(css,/\.shell-footer\{[^}]*position:(fixed|absolute)/);
});


test('footer muted color is scoped and interaction remains clear without opacity',async()=>{
 const css=await readFile(new URL('../../../pages/frontend/src/shell.css',import.meta.url),'utf8');
 assert.match(css,/\.shell-footer \.shell-project-link\{[^}]*color:color-mix\(in srgb,var\(--ui-muted\) 95%,var\(--ui-text\)\)/);
 assert.match(css,/\.shell-footer \.shell-project-link:hover,\.shell-footer \.shell-project-link:focus-visible\{color:var\(--ui-accent\)/);
 assert.match(css,/\.shell-project-spark\{color:inherit/);
 assert.doesNotMatch(css,/\.shell-footer[^}]*opacity:/);
});
