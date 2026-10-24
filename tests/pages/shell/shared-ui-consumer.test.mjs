import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceApp, sourceBundle, sourceRuntimeURL, sourceArtifacts} from './source-build.mjs';

const entry = 'examples/modules/shared-ui/pages/src/entry.ts';
test('non-FF14 consumer imports public UI and externalizes the shared runtime', async () => {
  const source = await readFile(new URL('../../../' + entry, import.meta.url), 'utf8');
  assert.match(source, /from '@game-link\/ui'/);
  assert.doesNotMatch(source, /frontend\/src|AstrBotPluginView|privateCore|naive-ui/);
  const bundle = await sourceBundle(entry);
  const imports = [...bundle.matchAll(/from\s*["']([^"']+)["']/g)].map(match => match[1]);
  assert.ok(imports.length > 0);
  assert.ok(imports.every(path => path === './runtime.js'));
  assert.match(bundle, /UiProvider/);
  assert.doesNotMatch(bundle, /createRenderer|createHydrationRenderer/);
  assert.match(bundle, /FormField/);
  assert.match(bundle, /FormGrid/);
  assert.equal((await sourceArtifacts(entry)).filter(item => item.type === 'asset' && item.fileName.endsWith('.css')).length, 0, 'module example does not copy Core CSS');
});

test('formal shell owns one public CSS layer without management-view side effects', async () => {
  const main = await readFile(new URL('../../../pages/frontend/src/main.ts', import.meta.url), 'utf8');
  const management = await readFile(new URL('../../../pages/frontend/src/management-view.ts', import.meta.url), 'utf8');
  assert.equal((main.match(/import '\.\/ui\/tokens\.css'/g) || []).length, 1);
  assert.doesNotMatch(management, /import ['"].*tokens\.css/);
  const output = await sourceArtifacts('pages/frontend/src/main.ts');
  const css = output.filter(item => item.type === 'asset' && item.fileName.endsWith('.css'));
  assert.equal(css.length, 1);
  const source = String(css[0].source);
  assert.match(source, /--ui-control-height:40px/);
  assert.match(source, /\.ui-form-grid/);
  assert.equal((source.match(/--ui-space-1:4px/g) || []).length, 1, 'public token declarations are not duplicated');
});

test('public UI consumer keeps its instance across theme changes and fences expired requests', async () => {
  const dom = new JSDOM('<main id="consumer"></main>', {pretendToBeVisual: true, url: 'http://localhost/'});
  const previous = new Map();
  for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'MutationObserver']) {
    previous.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, {configurable: true, writable: true, value: key === 'window' ? dom.window : key === 'document' ? dom.window.document : dom.window[key]});
  }
  const container = dom.window.document.getElementById('consumer');
  const context = {owner: 'example/demo', runtimeId: 'fixture', epoch: 1, boundary: 'owner-session', available: true, theme: 'light', locale: 'en-US'};
  const requests = [], cleanups = [];
  let page, active = true;
  const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
  try {
    const module = await sourceApp(entry);
    const runtime = await import(await sourceRuntimeURL());
    const layoutRoot = dom.window.document.createElement('div');
    const layout = runtime.createApp({render: () => runtime.h(runtime.SettingsLayout, {}, {default: () => runtime.h('p', 'Main content only')})});
    try {
      layout.mount(layoutRoot);
      assert.equal(layoutRoot.querySelector('.ui-settings-layout').dataset.hasAside, 'false');
      assert.equal(layoutRoot.querySelector('aside'), null);
      assert.equal(layoutRoot.textContent, 'Main content only');
    } finally { layout.unmount(); }
    page = module.mount(container, {routeId: 'home', context, scope: {signal: new AbortController().signal, isCurrent: () => active, onDispose: fn => {cleanups.push(fn); return () => {}; }}, services: {invoke(capability, parameters) { return new Promise(resolve => requests.push({capability, parameters, resolve})); }}});
    assert.equal(container.querySelector('.ui-page').dataset.theme, 'light');
    assert.equal(container.querySelector('.ui-settings-layout').dataset.hasAside, 'true');
    assert.equal(container.querySelector('h1').textContent, 'Shared UI example');
    const heading = container.querySelector('#example-view-heading');
    assert.ok(heading);
    assert.equal(container.querySelector('label[for="example-view-heading"]').textContent, 'Local heading');
    assert.ok(dom.window.document.getElementById(heading.getAttribute('aria-describedby')));
    heading.value = 'My module';
    heading.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    await flush();
    heading.focus();
    const button = container.querySelector('button');
    button.click(); await flush();
    assert.equal(requests.length, 1);
    assert.deepEqual([requests[0].capability, requests[0].parameters], ['status', {}]);
    page.update({...context, theme: 'dark'}); await flush();
    assert.equal(container.querySelector('.ui-page').dataset.theme, 'dark');
    assert.equal(container.querySelector('#example-view-heading'), heading);
    assert.equal(heading.value, 'My module');
    assert.equal(dom.window.document.activeElement, heading);
    assert.equal(container.querySelector('button'), button);
    assert.equal(button.disabled, true);
    requests[0].resolve({status: 'success'}); await flush();
    assert.match(container.querySelector('[role="status"]').textContent, /completed/);
    button.click(); await flush();
    page.update({...context, available: false}); await flush();
    assert.equal(button.disabled, true);
    button.click(); assert.equal(requests.length, 2);
    page.update(context); await flush();
    assert.equal(container.querySelector('button'), button);
    assert.equal(button.disabled, false, 'same binding resumes after a temporary refresh');
    button.click(); await flush();
    assert.equal(requests.length, 3);
    requests[1].resolve({status: 'success'}); await flush();
    assert.match(container.querySelector('[role="status"]').textContent, /Reading/);
    assert.equal(button.disabled, true, 'old request cannot clear the new pending state');
    requests[2].resolve({status: 'success'}); await flush();
    assert.match(container.querySelector('[role="status"]').textContent, /completed/);
    assert.equal(button.disabled, false);
    button.click(); await flush();
    page.update({...context, epoch: 2}); await flush();
    requests[3].resolve({status: 'success'}); await flush();
    assert.match(container.querySelector('[role="status"]').textContent, /expired/);
    page.update(context); await flush();
    button.click(); assert.equal(requests.length, 4, 'an invalidated binding cannot be revived');
    active = false;
    cleanups.forEach(fn => fn());
    page.dispose();
    assert.equal(container.children.length, 0);
  } finally {
    page?.dispose(); dom.window.close();
    for (const [key, descriptor] of previous) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; }
  }
});
