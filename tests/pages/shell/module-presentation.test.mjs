import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceModule} from './compile.mjs';
import {catalog, deferred, flush} from './fixtures.mjs';

const {createModuleAssets} = await sourceModule('pages/frontend/src/module-assets.ts');
const {createDirectoryPresentation} = await sourceModule('pages/frontend/src/module-presentation.ts');
const {createShell} = await sourceModule('pages/frontend/src/controller.ts');
const {contextBoundary, createScope} = await sourceModule('pages/frontend/src/lifecycle.ts');
const hash = value => createHash('sha256').update(value).digest('hex');
const tick = () => new Promise(resolve => setImmediate(resolve));
const owner = 'example/demo.v2';
const executable = 'export const presentation={schemaVersion:1,appearance:{light:{logo:"#123456"}}}';
const entry = 'pages/dist/presentation-demo.v2.js';
const style = 'pages/dist/presentation-demo.v2.css';
const context = {theme: 'light', locale: 'zh-CN', available: true, boundary: 'fixture'};

function fixture(live = false) {
  const version = 'a'.repeat(64);
  const prefix = `module-assets/${owner}/${live ? `pages/p${version}/` : ''}`;
  const page = 'pages/entry.js';
  const business = 'throw Error("business entry evaluated on the directory")';
  const resources = [
    [page, 'text/javascript', business],
    ['pages/entry.css', 'text/css', '.business {color:red}'],
    ['assets/logo.svg', 'image/svg+xml', '<svg xmlns="http://www.w3.org/2000/svg"/>'],
    [entry, 'text/javascript', executable],
    [style, 'text/css', '.demo-v2-logo {display:block}'],
  ].map(([path, media_type, content]) => ({path, media_type, sha256: hash(content),
    base64: Buffer.from(content).toString('base64')}));
  const module = {
    module_id: owner, source: {kind: live ? 'extension' : 'builtin', package_id: 'example'},
    state: 'loaded', runtime_id: 'runtime-fixture', module_epoch: 1, asset_version: version,
    display: {logo_resource: prefix + 'assets/logo.svg'},
    pages: [{entry: prefix + page, styles: [prefix + 'pages/entry.css'], route_id: 'home', capability_id: null}],
    resources: resources.map(resource => ({path: prefix + resource.path, sha256: resource.sha256})),
  };
  return {module, prefix, response: {asset_version: version, entry: page, styles: ['pages/entry.css'], resources}};
}

function directoryShell(loadPresentation, initial) {
  const dom = new JSDOM('<div id="page"></div><span id="logo"></span>', {url: 'http://localhost/#/'});
  const {window} = dom;
  const host = {pluginName: 'astrbot_plugin_yomihime_game_link', pageName: '00-game-link', projection: 1};
  const boundary = contextBoundary(host);
  let data = initial, visible = true, onContext;
  const loadPage = () => assert.fail('directory must not import the business page');
  const assets = createModuleAssets(window.document, loadPage, 100, undefined, loadPresentation);
  const directory = createDirectoryPresentation(assets, () => {}, module => shell.canExecuteModule(module));
  function sync() {
    const {catalog, theme, locale, busy, stale} = shell.state;
    directory.sync(catalog?.modules ?? [], {theme, locale, available: !busy && !stale, boundary}, visible);
    directory.view(owner)?.bind?.(window.document.getElementById('logo'));
  }
  const shell = createShell({
    window, container: window.document.getElementById('page'), changed: sync, loadPage,
    bridge: {
      ready: async () => host,
      onContext(callback) { onContext = callback; },
      apiGet: async () => data,
      apiPost: async action => assert.fail(action),
    },
  });
  return {
    directory, shell,
    start: async () => { shell.start(); await tick(); },
    refresh: async next => { data = next; await shell.refresh(); await tick(); },
    visibility(next) { visible = next; sync(); },
    context(changes) { onContext({...host, ...changes}); },
    close() { directory.close(); shell.dispose(); dom.window.close(); },
  };
}

function builtinDirectoryCatalog(epoch, state = 'loaded', digest = hash(executable)) {
  const data = catalog(owner, epoch);
  data.catalog_revision = epoch;
  const module = data.modules[0];
  module.source = {kind: 'builtin', package_id: 'example'};
  if (state === 'loaded') {
    const prefix = `module-assets/${owner}/`;
    const logo = prefix + 'assets/logo.svg';
    module.resources.push({path: logo, sha256: hash('<svg/>')}, {path: prefix + entry, sha256: digest});
    module.display = {default_name: 'Fixture', logo_resource: logo};
  } else {
    Object.assign(module, {state, enabled: false, runtime_id: null, pages: [], resources: []});
  }
  return data;
}

test('directory and business share the Host projection fence across disable, restore and same-path digest replacement', async () => {
  const calls = [], mounts = [], updates = [], disposals = [];
  const load = async path => {
    calls.push(path);
    return {
      presentation: {schemaVersion: 1, appearance: {light: {logo: '#123456'}}},
      mountLogo(_node, {context, scope}) {
        mounts.push(context.epoch);
        return {
          update(next) { updates.push([next.theme, next.locale]); },
          dispose() { disposals.push([context.epoch, scope.isCurrent()]); },
        };
      },
    };
  };
  const old = directoryShell(load, builtinDirectoryCatalog(1));
  const replacement = builtinDirectoryCatalog(4, 'loaded', hash('replacement bytes'));
  try {
    await old.start();
    assert.deepEqual(mounts, [1]);
    old.context({theme: 'dark', locale: 'en-US'});
    assert.deepEqual(updates, [['dark', 'en-US']]);
    assert.deepEqual(mounts, [1]);
    old.visibility(false);
    assert.deepEqual(disposals, [[1, false]]);
    await old.refresh(builtinDirectoryCatalog(2, 'disabled'));
    await old.refresh(builtinDirectoryCatalog(3));
    old.visibility(true);
    await tick();
    assert.equal(old.shell.canExecuteModule(old.shell.state.catalog.modules[0]), false);
    assert.equal(old.directory.view(owner).shown, false);
    assert.equal(old.directory.view(owner).url, `./module-assets/${owner}/assets/logo.svg`);
    await old.refresh(replacement);
    assert.deepEqual(calls, [`module-assets/${owner}/${entry}`]);
    assert.deepEqual(mounts, [1]);
    assert.equal(old.directory.view(owner).shown, false);
  } finally { old.close(); }

  const fresh = directoryShell(load, replacement);
  try {
    await fresh.start();
    assert.equal(fresh.shell.canExecuteModule(fresh.shell.state.catalog.modules[0]), true);
    assert.deepEqual(mounts, [1, 4]);
    assert.equal(calls.length, 2);
  } finally { fresh.close(); }
});

test('a late presentation result cannot apply even its appearance after Host projection revocation', async () => {
  const imported = deferred();
  let calls = 0;
  const harness = directoryShell(() => { calls++; return imported.promise; }, builtinDirectoryCatalog(1));
  try {
    await harness.start();
    assert.equal(calls, 1);
    const neutral = harness.directory.view(owner).appearance.colors.logo;
    harness.context({projection: 2});
    assert.equal(harness.shell.canExecuteModule(harness.shell.state.catalog.modules[0]), false);
    imported.resolve({presentation: {schemaVersion: 1, appearance: {light: {logo: '#123456'}}}});
    await tick();
    assert.equal(harness.directory.view(owner).appearance.colors.logo, neutral);
    assert.equal(harness.directory.view(owner).shown, false);
    harness.visibility(false);
    harness.visibility(true);
    await tick();
    assert.equal(calls, 1);
  } finally { harness.close(); }
});

test('a same-epoch digest change retires only the changed owner’s appearance', async () => {
  const other = 'example/other';
  const otherEntry = `module-assets/${other}/pages/dist/presentation-other.js`;
  const companion = catalog(other).modules[0];
  companion.source = {kind: 'builtin', package_id: 'example'};
  companion.resources.push({path: otherEntry, sha256: hash(executable)});
  const calls = [];
  const initial = builtinDirectoryCatalog(1);
  initial.modules.push(companion);
  const harness = directoryShell(async path => {
    calls.push(path);
    return {presentation: {schemaVersion: 1, appearance: {light: {logo: '#123456'}}}};
  }, initial);
  try {
    await harness.start();
    assert.equal(harness.directory.view(owner).appearance.colors.logo, '#123456');
    assert.equal(harness.directory.view(other).appearance.colors.logo, '#123456');
    const next = builtinDirectoryCatalog(1, 'loaded', hash('different bytes at the same path'));
    next.catalog_revision = 2;
    next.modules.push(companion);
    await harness.refresh(next);
    assert.equal(harness.shell.canExecuteModule(harness.shell.state.catalog.modules[0]), false);
    assert.equal(harness.shell.canExecuteModule(harness.shell.state.catalog.modules[1]), true);
    assert.notEqual(harness.directory.view(owner).appearance.colors.logo, '#123456');
    assert.equal(harness.directory.view(other).appearance.colors.logo, '#123456');
    assert.deepEqual(calls, [`module-assets/${owner}/${entry}`, otherEntry]);
  } finally { harness.close(); }
});

test('home mount receives the declared static Logo without requesting additional assets', async () => {
  for (const declared of [true, false]) {
    const dom = new JSDOM('<div id="page"></div>', {url: 'http://localhost/#/module/example%2Fdemo.v2'});
    const {window} = dom;
    const data = catalog(owner);
    const module = data.modules[0];
    module.source = {kind: 'builtin', package_id: 'example'};
    module.pages[0].role = 'home';
    module.pages[0].styles = [];
    const path = `module-assets/${owner}/assets/logo.svg`;
    if (declared) {
      module.display = {default_name: 'Fixture', logo_resource: path};
      module.resources.push({path, sha256: hash('<svg/>')});
    }
    const calls = [], mounts = [];
    const shell = createShell({
      container: window.document.getElementById('page'), window, changed() {},
      bridge: {
        ready: async () => ({pluginName: 'astrbot_plugin_yomihime_game_link', pageName: '00-game-link'}),
        onContext() {},
        apiGet: async () => data,
        apiPost: async action => {
          calls.push(action);
          if (action === 'admin/module-page-session') {
            return {page_session: 'fixture-proof', expires_at: Date.now() / 1000 + 30};
          }
          if (action === 'admin/module-page-session-close') return {closed: true};
          assert.fail(action);
        },
      },
      loadPage: async () => ({
        mount(_node, options) { mounts.push(options); return {update() {}, dispose() {}}; },
      }),
    });
    try {
      shell.start();
      await flush();
      assert.equal(mounts.length, 1);
      assert.equal(mounts[0].logoResourceUrl, declared ? './' + path : undefined);
      assert.deepEqual(calls, ['admin/module-page-session']);
    } finally {
      shell.dispose();
      dom.window.close();
    }
  }
});

test('live home Logo reuses its verified route payload and releases each page lease', async () => {
  const dom = new JSDOM('', {url: 'http://localhost/'});
  const {module, response, prefix} = fixture(true);
  const page = module.pages[0];
  page.role = 'home';
  page.styles = [];
  response.styles = [];
  const code = 'export function mount() {}';
  const business = response.resources[0];
  business.base64 = Buffer.from(code).toString('base64');
  module.resources[0].sha256 = business.sha256 = hash(code);
  const reply = deferred(), calls = [], created = [], revoked = [];
  const create = URL.createObjectURL, revoke = URL.revokeObjectURL;
  URL.createObjectURL = blob => {
    const url = blob.type === 'text/javascript'
      ? 'data:text/javascript;base64,' + Buffer.from(code).toString('base64')
      : 'blob:http://localhost/page-logo-' + created.length;
    created.push({url, blob});
    return url;
  };
  URL.revokeObjectURL = url => revoked.push(url);
  const assets = createModuleAssets(dom.window.document, () => assert.fail('static loader'), 100, {
    runtimeUrl: '/runtime.js', bridge: {apiPost: (...args) => { calls.push(args); return reply.promise; }},
  });
  try {
    const left = createScope(() => true);
    const loaded = assets.load(module, page);
    const late = assets.pageLogo(module, page, left);
    left.dispose();
    reply.resolve(response);
    assert.equal(await late, undefined);
    await loaded;
    assert.equal(created.length, 1);
    const scope = createScope(() => true);
    assert.equal(await assets.pageLogo({...module, display: {logo_resource: prefix.replace(owner, 'other/demo.v2') + 'assets/logo.svg'}}, page, scope), undefined);
    assert.equal(await assets.pageLogo(module, {...page, role: 'aux'}, scope), undefined);
    const first = await assets.pageLogo(module, page, scope);
    assert.match(first, /^blob:/);
    assert.equal(created.at(-1).blob.type, 'image/svg+xml');
    scope.dispose();
    assert.deepEqual(revoked, [first]);
    assert.equal(await assets.pageLogo(module, page, scope), undefined);
    const reentry = createScope(() => true);
    const second = await assets.pageLogo(module, page, reentry);
    assert.notEqual(second, first);
    reentry.dispose();
    assert.deepEqual(revoked, [first, second]);
    assert.equal(calls.length, 1);
  } finally {
    assets.invalidateAll();
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    dom.window.close();
  }
});

test('static presentation uses the exact owner declaration and isolated loader and stylesheet', async () => {
  const dom = new JSDOM('<template id="module-style-assets"></template>', {url: 'http://localhost/'});
  const {document} = dom.window;
  const {module, prefix} = fixture();
  const links = [];
  const imports = [];
  for (const path of [prefix + style, prefix + 'pages/entry.css']) {
    const source = document.createElement('link');
    source.rel = 'stylesheet';
    source.dataset.resource = path;
    source.href = './' + path;
    document.querySelector('template').content.append(source);
  }
  const append = document.head.append.bind(document.head);
  document.head.append = link => {
    append(link);
    links.push(link.dataset.resource);
    queueMicrotask(() => link.dispatchEvent(new dom.window.Event('load')));
  };
  const assets = createModuleAssets(document, () => assert.fail('business import'), 100, undefined, async path => {
    imports.push(path);
    return {presentation: {schemaVersion: 1}};
  });
  try {
    const crossOwner = structuredClone(module);
    crossOwner.resources.find(item => item.path.endsWith(entry)).path = 'module-assets/other/demo.v2/' + entry;
    assert.equal(await assets.presentation(crossOwner), null);
    assert.equal(await assets.presentation({...module, state: 'disabled'}), null);
    await assert.rejects(assets.presentation(module, () => false), /asset_revoked/);
    assert.deepEqual(imports, []);
    assert.deepEqual(links, []);
    const [left, right] = await Promise.all([assets.presentation(module), assets.presentation(module)]);
    assert.equal(left, right);
    assert.deepEqual(links, [prefix + style]);
    assert.deepEqual(imports, [prefix + entry]);
    assets.invalidate(owner);
    assert.equal(document.head.querySelector('link'), null);
  } finally {
    assets.invalidateAll();
    dom.window.close();
  }
});

test('live presentation shares the complete verified image payload and never loads the business page', async () => {
  const dom = new JSDOM('', {url: 'http://localhost/'});
  const {document} = dom.window;
  const {module, response} = fixture(true);
  const blobs = [], revoked = [], calls = [], styles = [];
  const create = URL.createObjectURL, revoke = URL.revokeObjectURL;
  URL.createObjectURL = blob => {
    const url = blob.type === 'text/javascript'
      ? 'data:text/javascript;base64,' + Buffer.from(executable).toString('base64')
      : 'blob:http://localhost/fixture-' + blobs.length;
    blobs.push({blob, url});
    return url;
  };
  URL.revokeObjectURL = url => revoked.push(url);
  const append = document.head.append.bind(document.head);
  document.head.append = link => {
    append(link);
    styles.push(link.href);
    queueMicrotask(() => link.onload());
  };
  const assets = createModuleAssets(document, () => assert.fail('business import'), 100, {
    runtimeUrl: '/runtime.js', bridge: {apiPost: async (...args) => { calls.push(args); return response; }},
  }, () => assert.fail('live entry bypassed verified payload'));
  try {
    const [url, extensions] = await Promise.all([
      assets.image(module, module.display.logo_resource), assets.presentation(module),
    ]);
    assert.match(url, /^blob:/);
    assert.equal(extensions.presentation.appearance.light.logo, '#123456');
    assert.deepEqual(calls, [['admin/module-page-assets',
      {owner, page: 'home', runtime_id: 'runtime-fixture', module_epoch: 1}]]);
    assert.equal(styles.length, 1);
    assert.equal(await blobs.find(item => item.url === styles[0]).blob.text(), '.demo-v2-logo {display:block}');
    assert.equal(await blobs.find(item => item.blob.type === 'text/javascript').blob.text(), executable);
    assets.invalidate(owner);
    assert.deepEqual(new Set(revoked), new Set(blobs.map(item => item.url)));
    assert.equal(document.head.querySelector('link'), null);
  } finally {
    assets.invalidateAll();
    URL.createObjectURL = create;
    URL.revokeObjectURL = revoke;
    dom.window.close();
  }
});

test('live presentation rejects tampered sibling bytes and unsupported stylesheet URLs before import', async () => {
  const dom = new JSDOM('', {url: 'http://localhost/'});
  const create = URL.createObjectURL;
  let blobCalls = 0;
  URL.createObjectURL = () => { blobCalls++; throw Error('unexpected_blob'); };
  try {
    for (const scenario of ['tamper', 'url', 'stale']) {
      const {module, response, prefix} = fixture(true);
      if (scenario === 'tamper') response.resources[0].base64 = Buffer.from('modified business code').toString('base64');
      if (scenario === 'url') {
        const content = '.demo-v2-logo {background:url(https://example.invalid/asset)}';
        const row = response.resources.find(item => item.path === style);
        row.base64 = Buffer.from(content).toString('base64');
        row.sha256 = hash(content);
        module.resources.find(item => item.path === prefix + style).sha256 = row.sha256;
      }
      const reply = deferred();
      let current = true;
      const assets = createModuleAssets(dom.window.document, () => assert.fail('business import'), 100, {
        runtimeUrl: '/runtime.js', bridge: {apiPost: () => reply.promise},
      });
      const pending = assets.presentation(module, () => current);
      if (scenario === 'stale') current = false;
      reply.resolve(response);
      await assert.rejects(pending, {
        message: {tamper: 'asset_digest_mismatch', url: 'unsupported_live_style_reference', stale: 'asset_revoked'}[scenario],
      });
      assets.invalidateAll();
    }
    assert.equal(blobCalls, 0);
  } finally {
    URL.createObjectURL = create;
    dom.window.close();
  }
});

test('directory discards late effects and updates the same mounted effect across theme changes', async () => {
  const dom = new JSDOM('<span id="host"></span>', {url: 'http://localhost/'});
  const host = dom.window.document.getElementById('host');
  const {module} = fixture();
  const loads = [], updates = [], mounts = [], disposed = [];
  const directory = createDirectoryPresentation({
    image: () => assert.fail('static image should be projected'),
    presentation: () => { const result = deferred(); loads.push(result); return result.promise; },
    invalidate() {}, retryCleanup() {}, cleanupPending: false,
  }, () => {}, () => true);
  const code = {
    presentation: {schemaVersion: 1, appearance: {dark: {logo: '#123456'}}},
    mountLogo(node, options) {
      mounts.push({node, options});
      return {update: (ctx, appearance) => updates.push([ctx.theme, appearance.colors.logo]),
        dispose: () => disposed.push(node)};
    },
  };
  try {
    directory.sync([module], context, true);
    directory.view(owner).bind(host);
    directory.sync([module], context, false);
    directory.sync([module], context, true);
    directory.view(owner).bind(host);
    loads[0].resolve(code);
    await tick();
    assert.equal(mounts.length, 0);
    loads[1].resolve(code);
    await tick();
    assert.equal(mounts.length, 1);
    assert.deepEqual(Object.keys(mounts[0].options).sort(), ['appearance', 'context', 'resourceUrl', 'scope']);
    assert.equal(directory.view(owner).shown, true);
    directory.sync([module], {...context, theme: 'dark'}, true);
    assert.equal(mounts.length, 1);
    assert.deepEqual(updates, [['dark', '#123456']]);
    directory.close();
    assert.equal(mounts[0].options.scope.isCurrent(), false);
    assert.deepEqual(disposed, [mounts[0].node]);
    assert.equal(host.children.length, 0);
  } finally {
    directory.close();
    dom.window.close();
  }
});

test('directory retirement retains failed scope and handle cleanup before releasing assets', async () => {
  const dom = new JSDOM('<span id="host"></span>', {url: 'http://localhost/'});
  const host = dom.window.document.getElementById('host');
  const {module} = fixture();
  const calls = [];
  let scopeFails = true, disposeFails = true, loads = 0;
  const directory = createDirectoryPresentation({
    image: () => assert.fail('static image should be projected'),
    presentation: async () => { loads++; return {mountLogo(node, {scope}) {
      scope.onDispose(() => { calls.push('scope'); if (scopeFails) throw Error('scope fixture'); });
      return {update() {}, dispose() { calls.push('handle'); if (disposeFails) throw Error('handle fixture'); }};
    }}; },
    invalidate() { calls.push('assets'); }, retryCleanup() {}, cleanupPending: false,
  }, () => {}, () => true);
  try {
    directory.sync([module], context, true);
    directory.view(owner).bind(host);
    await tick();
    const next = {...module, runtime_id: 'next-runtime'};
    directory.sync([next], context, true);
    assert.equal(directory.cleanupPending, true);
    assert.deepEqual(calls, ['scope']);
    assert.equal(host.children.length, 1);
    assert.equal(loads, 1);
    scopeFails = false;
    directory.retryCleanup();
    directory.sync([next], context, true);
    assert.deepEqual(calls, ['scope', 'scope', 'handle']);
    assert.equal(directory.cleanupPending, true);
    assert.equal(loads, 1);
    disposeFails = false;
    directory.retryCleanup();
    assert.deepEqual(calls, ['scope', 'scope', 'handle', 'handle', 'assets']);
    assert.equal(directory.cleanupPending, false);
    assert.equal(host.children.length, 0);
    directory.sync([next], context, true);
    assert.equal(loads, 2);
  } finally {
    scopeFails = disposeFails = false;
    directory.retryCleanup();
    directory.close();
    dom.window.close();
  }
});

test('a failed hook mount or update restores the static logo and cleans registered work once', async () => {
  const {module} = fixture();
  for (const phase of ['mount', 'update']) {
    const dom = new JSDOM('<span id="host"></span>', {url: 'http://localhost/'});
    const host = dom.window.document.getElementById('host');
    const scopes = [];
    let loads = 0, cleanup = 0, disposals = 0;
    const directory = createDirectoryPresentation({
      image: () => assert.fail('static image should be projected'),
      presentation: async () => {
        loads++;
        return {mountLogo(node, {scope}) {
          scopes.push(scope);
          scope.onDispose(() => { cleanup++; });
          node.textContent = 'owned effect';
          if (phase === 'mount') throw Error('mount fixture');
          return {update() { throw Error('update fixture'); }, dispose() { disposals++; }};
        }};
      },
      invalidate() {}, retryCleanup() {}, cleanupPending: false,
    }, () => {}, () => true);
    try {
      directory.sync([module], context, true);
      directory.view(owner).bind(host);
      await tick();
      directory.sync([module], {...context, theme: 'dark'}, true);
      assert.equal(directory.view(owner).shown, false);
      assert.match(directory.view(owner).url, /assets\/logo\.svg$/);
      assert.equal(directory.cleanupPending, false);
      assert.equal(host.children.length, 0);
      assert.equal(scopes[0].isCurrent(), false);
      assert.equal(cleanup, 1);
      assert.equal(disposals, phase === 'update' ? 1 : 0);
      assert.equal(loads, 1);
    } finally {
      directory.close();
      dom.window.close();
    }
  }
});
