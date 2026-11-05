import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceBundle, sourceRuntimeURL} from './source-build.mjs';

const read = path => readFile(new URL('../../../' + path, import.meta.url), 'utf8');

test('Core logos are static and FF14 owns its opaque base and sweep', async () => {
  const css = await read('pages/frontend/src/ui/tokens.css');
  assert.doesNotMatch(css, /ui-logo-(?:sweep|reveal)|@keyframes ui-logo-beam/);
  const shell = await read('pages/frontend/src/main.ts');
  assert.doesNotMatch(shell, /effect:.*rebound/);
  const ff14Css = await read('modules/ff14/pages/src/logo-effect.css');
  assert.match(ff14Css, /\.ff14-logo-effect\s*\{[^}]*background:\s*var\(--ff14-logo-color\)/);
  assert.match(ff14Css, /\.ff14-logo-sweep\s*\{[^}]*opacity:\s*0/);
  const ff14 = await read('modules/ff14/pages/src/settings-page.ts');
  assert.match(ff14, /import \{mountLogo, presentation\} from '\.\/presentation-ff14'/);
});

test('FF14 sweep plays once, respects reduced motion and cleans up', async () => {
  const dom = new JSDOM('<main></main>', {pretendToBeVisual: true, url: 'http://localhost/'});
  const previous = new Map();
  for (const key of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver']) {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, {configurable: true, writable: true, value: key === 'window' ? dom.window : key === 'document' ? dom.window.document : dom.window[key]});
  }
  let reduced = false, listener = null, added = 0, removed = 0;
  const calls = [];
  dom.window.matchMedia = () => ({
    get matches() { return reduced; },
    addEventListener(_name, fn) { listener = fn; added++; },
    removeEventListener(_name, fn) { if (listener === fn) listener = null; removed++; },
  });
  dom.window.HTMLElement.prototype.animate = function (frames, options) {
    const a = {cancelled: false, cancel() { this.cancelled = true; }};
    calls.push({target: this, frames, options, a});
    return a;
  };
  let app, dispose;
  try {
    const {createApp, h, LogoMark} = await import(await sourceRuntimeURL());
    const source = await sourceBundle('modules/ff14/pages/src/presentation-ff14.ts');
    const {mountLogo, presentation} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    const host = dom.window.document.querySelector('main');
    const light = {colors: presentation.appearance.light};
    const dark = {colors: presentation.appearance.dark};
    function mount() {
      const callbacks = [];
      const controller = new AbortController();
      dispose = () => {
        controller.abort();
        callbacks.forEach(fn => fn());
      };
      const scope = {signal: controller.signal, isCurrent: () => !controller.signal.aborted, onDispose(fn) { callbacks.push(fn); }};
      return mountLogo(host, {resourceUrl: '/verified-ff14.svg', context: {theme: 'light'}, appearance: light, scope});
    }

    const first = mount();
    const root = host.querySelector('.ff14-logo-effect');
    assert.equal(root.getAttribute('aria-hidden'), 'true');
    assert.match(root.style.maskImage, /\/verified-ff14\.svg/);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].target.className, 'ff14-logo-sweep');
    assert.notEqual(calls[0].target, root);
    assert.deepEqual(calls[0].options, {duration: 870, delay: 1300, iterations: 1, fill: 'forwards', easing: 'linear'});
    assert.deepEqual(calls[0].frames.map(frame => frame.transform), ['translateY(-100%)', 'translateY(-100%)', 'translateY(100%)', 'translateY(-100%)']);
    assert.equal(calls[0].frames.at(-1).opacity, 0);
    first.update({theme: 'dark', locale: 'en-US'}, dark);
    assert.equal(calls.length, 1);
    assert.equal(host.querySelector('.ff14-logo-effect'), root);
    assert.equal(root.style.getPropertyValue('--ff14-logo-color'), '#c4ad78');
    reduced = true;
    listener({matches: true});
    assert.equal(calls[0].a.cancelled, true);
    reduced = false;
    listener({matches: false});
    assert.equal(calls.length, 1);
    dispose();
    first.dispose();
    assert.equal(added, removed);
    assert.equal(host.children.length, 0);

    mount();
    assert.equal(calls.length, 2);
    dispose();
    assert.equal(calls[1].a.cancelled, true);

    app = createApp({render: () => h(LogoMark, {effect: 'rebound'})});
    app.mount(host);
    assert.equal(calls.length, 2);
    app.unmount();
    app = undefined;

    reduced = true;
    mount();
    assert.equal(calls.length, 2);
    dispose();
    reduced = false;
    delete dom.window.HTMLElement.prototype.animate;
    mount();
    assert.equal(calls.length, 2);
    dispose();
    dom.window.HTMLElement.prototype.animate = () => { throw Error('unsupported'); };
    mount();
    assert.ok(host.querySelector('.ff14-logo-effect'));
    dispose();
    delete dom.window.matchMedia;
    mount();
    assert.ok(host.querySelector('.ff14-logo-effect'));
    assert.equal(calls.length, 2);
    dispose();
    assert.equal(added, removed);
  } finally {
    dispose?.();
    app?.unmount();
    dom.window.close();
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
