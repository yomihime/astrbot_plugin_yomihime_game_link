import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {test} from 'node:test';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule} from './compile.mjs';
import {sourceApp} from './source-build.mjs';
import {catalog, deferred} from './fixtures.mjs';

const {createModuleAssets} = await sourceModule('pages/frontend/src/module-assets.ts');
const {parseModuleCopy, MODULE_SUMMARY_KEY, moduleRouteTitleKey} = await sourceModule('pages/frontend/src/module-copy.ts');
const {findUiMessage} = await sourceModule('pages/frontend/src/i18n/locale.ts');
const owner = 'ff14/ff14';
const entry = 'pages/dist/presentation-ff14.js';
const body = 'export const presentation = {schemaVersion: 1};';
const data = {
  'en-US': {summary: 'Item and market lookups', titles: {items: "Item lookup from 'this page'"}},
  'ja-JP': {summary: 'アイテムとマーケットの検索', titles: {items: 'アイテム検索'}},
};
const envelope = (copy = data) => '// ygl-copy-v1 ' + JSON.stringify(copy) + '\n' + body;
const bytes = value => new TextEncoder().encode(value);
const hash = value => createHash('sha256').update(value).digest('hex');

test('only the bounded first line and declared routes supply optional plain copy', () => {
  const routes = new Set(['items']);
  const parse = value => parseModuleCopy(bytes(value), routes);
  const valid = parse(envelope());
  assert.equal(valid['en-US'][moduleRouteTitleKey('items')], data['en-US'].titles.items);
  assert.equal(valid['ja-JP'][MODULE_SUMMARY_KEY], data['ja-JP'].summary);
  assert.equal(valid['zh-CN'], undefined);
  for (const value of [
    body, '\n' + envelope(), '// ygl-copy-v1 invalid\n' + body,
    envelope({'en-US': {titles: {another_route: 'Different owner'}}}),
    envelope({'en_US': {summary: 'Unexpected key'}}),
    envelope({'en-US': {summary: 'hidden\u202e text'}}),
    envelope({'en-US': {summary: 'source\u2028line'}}),
    envelope({'en-US': {summary: '界'.repeat(161)}}),
    envelope({'en-US': {titles: {items: '界'.repeat(257)}}}),
    '// ygl-copy-v1 ' + ' '.repeat(16384) + '{}\n' + body,
  ]) assert.equal(parse(value), null);
});

test('catalog summary and route title fall back independently within the declared owner', () => {
  const copy = parseModuleCopy(bytes(envelope({
    'zh-CN': {summary: '默认模块简介', titles: {items: '默认查询'}},
    'ja-JP': {summary: 'モジュールの紹介'},
  })), new Set(['items']));
  assert.equal(findUiMessage(copy, 'ja', MODULE_SUMMARY_KEY), 'モジュールの紹介');
  assert.equal(findUiMessage(copy, 'ja', moduleRouteTitleKey('items')), '默认查询');
  assert.equal(findUiMessage(copy, 'fr', MODULE_SUMMARY_KEY), '默认模块简介');
  assert.equal(findUiMessage(copy, 'ja', moduleRouteTitleKey('foreign')), undefined);
});

function builtinCatalog(content = envelope()) {
  const result = catalog(owner);
  const module = result.modules[0];
  module.source = {kind: 'builtin', package_id: 'ff14'};
  module.display = {default_name: 'FF14', summary: '旧包摘要'};
  module.resources.push({path: `module-assets/${owner}/${entry}`, sha256: hash(content)});
  return result;
}

test('static copy reads exact same-origin declared bytes, with a digest check and no script import', async () => {
  const dom = new JSDOM('', {url: 'http://localhost/plugin/view/index.html#/view'});
  let content = envelope();
  const module = builtinCatalog(content).modules[0];
  const requests = [];
  dom.window.fetch = async (url, options) => {
    requests.push({url, options});
    return new Response(content);
  };
  const assets = createModuleAssets(dom.window.document, () => assert.fail('business import'), 1000,
    undefined, () => assert.fail('presentation import'));
  const signal = new AbortController().signal;
  try {
    assert.ok(await assets.moduleCopy(module, signal));
    assert.equal(requests[0].url, `http://localhost/plugin/view/module-assets/${owner}/${entry}`);
    assert.equal(requests[0].options.redirect, 'error');
    content += '\n// mismatched';
    assert.equal(await assets.moduleCopy(module, signal), null);
    assert.equal(await assets.moduleCopy({...module, resources: []}, signal), null);
    assert.equal(requests.length, 2);
  } finally { dom.window.close(); }
});

test('live copy uses the existing owner/page proof and never poisons the optional presentation on failure', async () => {
  const dom = new JSDOM('', {url: 'http://localhost/index.html'});
  const version = 'a'.repeat(64), prefix = `module-assets/${owner}/pages/p${version}/`;
  const page = 'pages/entry.js';
  const resources = [[page, 'throw Error("business")'], [entry, envelope()]].map(([path, value]) => ({
    path, sha256: hash(value), media_type: 'text/javascript', base64: Buffer.from(value).toString('base64'),
  }));
  const module = {...builtinCatalog().modules[0], source: {kind: 'extension', package_id: 'ff14'},
    pages: [{route_id: 'items', entry: prefix + page, styles: []}],
    resources: resources.map(({path, sha256}) => ({path: prefix + path, sha256})),
  };
  const valid = {asset_version: version, entry: page, styles: [], resources};
  const invalid = {...valid, resources: [resources[0], {...resources[1], base64: Buffer.from('tampered').toString('base64')}]};
  const requests = [], signal = new AbortController().signal;
  const assets = createModuleAssets(dom.window.document, () => assert.fail('static import'), 1000, {
    runtimeUrl: '/runtime.js',
    bridge: {async apiPost(endpoint, payload) {requests.push({endpoint, payload}); return requests.length === 1 ? invalid : valid;}},
  });
  const create = URL.createObjectURL;
  let imported;
  URL.createObjectURL = blob => {
    imported = blob.text();
    return 'data:text/javascript,' + encodeURIComponent(body);
  };
  try {
    assert.equal(await assets.moduleCopy(module, signal), null);
    assert.equal(imported, undefined);
    assert.deepEqual(requests[0], {endpoint: 'admin/module-page-assets', payload: {
      owner, page: 'items', runtime_id: module.runtime_id, module_epoch: module.module_epoch,
    }});
    assert.deepEqual(await assets.presentation(module), {presentation: {schemaVersion: 1}, mountLogo: undefined});
    assert.equal(await imported, body); // The comment never enters the JS import scan.
    assert.ok(await assets.moduleCopy(module, signal));
    assert.equal(requests.length, 2);
  } finally {
    URL.createObjectURL = create;
    assets.invalidateAll();
    dom.window.close();
  }
});

test('shell deep link localizes only the visible owner, reuses it on home, and revokes it across contexts', async () => {
  const dom = new JSDOM('<html data-page-name="00-game-link"><body><div id="app"></div></body></html>', {
    pretendToBeVisual: true, url: 'http://localhost/index.html#/module/ff14%2Fff14/-/queries',
  });
  const {window} = dom, document = window.document;
  for (const key of ['window','document','Element','HTMLElement','SVGElement','Node','MutationObserver']) {
    globalThis[key] = key === 'window' ? window : window[key];
  }
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
  globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
  window.matchMedia = () => ({matches: false, addEventListener(){}, removeEventListener(){}});
  window.scrollTo = () => {};
  const context = {pluginName: 'astrbot_plugin_yomihime_game_link', pageName: '00-game-link', session: 'one', locale: 'en-US'};
  const listeners = new Set(), fetched = [], reply = deferred(), late = deferred();
  const directory = builtinCatalog();
  const other = catalog('example/other').modules[0];
  other.source = {kind: 'builtin', package_id: 'example'};
  other.display = {default_name: 'Example', summary: 'Other fallback'};
  const otherCopy = envelope({'en-US': {summary: 'Stale translated summary'}});
  other.resources.push({path: 'module-assets/example/other/pages/dist/presentation-other.js', sha256: hash(otherCopy)});
  directory.modules.push(other);
  window.fetch = async (url, options) => {
    fetched.push({url, signal: options.signal});
    return new Response(await (url.includes(owner) ? reply.promise : late.promise));
  };
  window.AstrBotPluginView = {
    ready: async () => context,
    onContext(fn) {listeners.add(fn); fn(context); return () => listeners.delete(fn);},
    apiGet: async () => directory,
    async apiPost(endpoint) {if (endpoint === 'admin/modules') return {registry_revision: 1, modules: []}; return {};},
  };
  globalThis.__loadFixturePage = () => assert.fail('no page should execute to display copy');
  const render = async () => {for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve));};
  const emit = value => {for (const listener of listeners) listener(value);};
  const title = () => document.querySelector('.query-links a').textContent;
  try {
    await sourceApp();
    await render();
    assert.equal(title(), '物品查询');
    assert.equal(fetched.length, 1);
    emit({...context, locale: 'ja-JP'});
    reply.resolve(envelope());
    await render();
    assert.equal(title(), 'アイテム検索');
    emit(context);
    await render();
    assert.equal(title(), data['en-US'].titles.items);
    assert.equal(fetched.length, 1);
    window.location.hash = '#/';
    await render();
    assert.deepEqual(Array.from(document.querySelectorAll('.workspace-module-summary'), node => node.textContent),
      ['Other fallback', data['en-US'].summary]);
    assert.equal(fetched.length, 2);
    emit({...context, session: 'two'});
    late.resolve(otherCopy);
    await render();
    assert.ok(Array.from(document.querySelectorAll('.workspace-module-summary'), node => node.textContent).includes('旧包摘要'));
    assert.ok(fetched[1].signal.aborted);
    assert.doesNotMatch(document.body.textContent, /Stale translated summary/);
    assert.equal(fetched.length, 2);
  } finally {
    reply.resolve(envelope());
    late.resolve(otherCopy);
    window.dispatchEvent(new window.Event('pagehide'));
    dom.window.close();
    delete globalThis.__loadFixturePage;
  }
});
