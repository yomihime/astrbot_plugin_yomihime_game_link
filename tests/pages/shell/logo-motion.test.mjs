import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const read=path=>readFile(new URL('../../../'+path,import.meta.url),'utf8');
test('shared vector rebound has bounded phases, clipping, and static reduced motion',async()=>{
 const css=await read('pages/frontend/src/ui/tokens.css');
 assert.match(css,/\.ui-logo-rebound\{[^}]*overflow:hidden;pointer-events:none/);
 assert.match(css,/ui-logo-reveal 5\.6s linear 1 forwards/);
 assert.match(css,/@keyframes ui-logo-beam\{.*23\.2143%.*33\.0357%.*38\.75%.*38\.751%,100%/);
 assert.match(css,/ui-logo-beam 5\.6s linear 1 forwards/);
 assert.doesNotMatch(css,/ui-logo-fade|ui-logo-[^;}]*infinite/);
 assert.match(css,/@keyframes ui-logo-reveal\{.*38\.75%,100%\{clip-path:polygon\(0 -100%,100% 0,100% 100%,0 100%\)\}/);
 assert.match(css,/@media\(prefers-reduced-motion:reduce\)\{\.ui-logo-rebound::before\{animation:none;clip-path:none;opacity:1\}\.ui-logo-rebound::after\{animation:none;opacity:0\}\}/);
 assert.doesNotMatch(css,/\.gif|\.webm|\.mp4|data:image/);
});
test('decorative logo is public and module-agnostic; absent logos keep static fallback',async()=>{
 const facade=await read('pages/public-ui.ts'),shared=await read('pages/frontend/src/ui/shared-components.ts');
 assert.match(facade,/LogoMark/);assert.match(shared,/class:'ui-logo-rebound','aria-hidden':'true'/);
 assert.doesNotMatch(shared,/ff14|meteor|setTimeout|requestAnimationFrame|addEventListener/i);
 const shell=await read('pages/frontend/src/main.ts');assert.match(shell,/module\.display\?\.logo_resource\?h\(LogoMark/);
 const module=await read('modules/ff14/pages/src/settings-page.ts');assert.match(module,/LogoMark.*from '@game-link\/ui'/);assert.match(module,/h\(LogoMark,\{class:'ff14-module-mark'/);
});
