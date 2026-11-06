import {after, test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';

const dom = new JSDOM('<body></body>', {pretendToBeVisual: true, url: 'http://localhost/'});
const {window} = dom;
for (const key of ['window','document','HTMLElement','Element','SVGElement','Node','MutationObserver','InputEvent']) {
  globalThis[key] = key === 'window' ? window : window[key];
}
window.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
after(() => window.close());

const {sourceApp, sourceRuntimeURL} = await import('../shell/source-build.mjs');
const {mountSettings} = await sourceApp('modules/ff14/pages/src/settings-page.ts');
const {nextTick} = await import(await sourceRuntimeURL());
const context = {owner: 'ff14/ff14', runtimeId: 'template', epoch: 1, boundary: 'test', available: true, theme: 'light', locale: 'zh-CN'};
const scope = {isCurrent: () => true, onDispose() {}};
const flush = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
  await nextTick();
};

test('FF14 home uses one mounted logo across context updates with no business calls', async () => {
  const container = window.document.body.appendChild(window.document.createElement('main'));
  let plays = 0, cancelled = 0, business = 0;
  window.HTMLElement.prototype.animate = () => {
    plays++;
    return {cancel() { cancelled++; }};
  };
  const services = new Proxy({}, {get() { business++; throw Error('unexpected service access'); }});
  const logoResourceUrl = 'blob:http://localhost/verified-ff14-logo';
  const page = mountSettings(container, {routeId: 'home', context, scope, services, logoResourceUrl});
  try {
    assert.equal(container.querySelector('.ui-page-template h2').textContent, '最终幻想 XIV');
    const logo = container.querySelector('.ff14-logo-effect');
    assert.ok(logo.style.maskImage.includes(logoResourceUrl));
    assert.equal(logo.getAttribute('aria-hidden'), 'true');
    assert.equal(plays, 1);
    page.update({...context, theme: 'dark', locale: 'en-US'});
    await flush();
    assert.equal(container.querySelector('.ui-page-template h2').textContent, 'Final Fantasy XIV');
    assert.equal(container.querySelector('.ff14-logo-effect'), logo);
    assert.equal(logo.style.getPropertyValue('--ff14-logo-color'), '#c4ad78');
    assert.equal(plays, 1);
    assert.equal(business, 0);
  } finally {
    page.dispose();
    container.remove();
    delete window.HTMLElement.prototype.animate;
  }
  assert.equal(cancelled, 1);
});

test('FF14 home without a verified logo URL has a static decorative fallback', () => {
  const container = window.document.body.appendChild(window.document.createElement('main'));
  const page = mountSettings(container, {routeId: 'home', context, scope, services: {}});
  try {
    const mark = container.querySelector('.ff14-module-mark');
    assert.equal(mark.textContent, '✦');
    assert.equal(mark.getAttribute('aria-hidden'), 'true');
    assert.equal(container.querySelector('.ff14-logo-effect'), null);
  } finally {
    page.dispose();
    container.remove();
  }
});

test('FF14 generated submit preserves focus while waiting and credential form stays independent', async () => {
  const container = window.document.body.appendChild(window.document.createElement('main'));
  let revision = 1, writes = 0, replacements = 0, reads = 0, finishSave;
  const services = {
    config: {
      current: async () => {
        reads++;
        return {
          revision,
          fields: {ff14_calendar_default_days: {value: 7, state: 'valid'}},
          descriptors: {reply_theme: {choices: [
            {value: 'warm_paper', label: '暖桃纸'},
            {value: 'future-theme', label: 'Untranslated host label'},
          ]}},
        };
      },
      save: async () => {
        writes++;
        await new Promise(resolve => { finishSave = resolve; });
        return {revision: ++revision};
      },
    },
    credentials: {
      status: async () => ({revision, fields: {}}),
      replace: async () => { replacements++; return {revision: ++revision}; },
    },
  };
  const page = mountSettings(container, {routeId: 'config', context, scope, services});
  const edit = (input, value) => {
    input.value = value;
    input.dispatchEvent(new window.Event('input', {bubbles: true}));
  };
  try {
    await flush();
    const save = container.querySelector('button[data-action="save"]');
    const ordinary = save.form;
    const credential = container.querySelector('form.ff14-credential:not([hidden])');
    assert.equal(container.querySelectorAll('form').length, 3);
    assert.equal(container.querySelector('form form'), null);
    assert.notEqual(ordinary, credential);
    const days = ordinary.querySelector('input[name="ff14_calendar_default_days"]');
    assert.equal(days.labels[0].textContent, '日历默认天数');
    edit(days, '12');
    const secret = credential.querySelector('input[type=password]');
    edit(secret, 'draft-fixture');
    secret.focus();
    secret.setSelectionRange(1, 3);
    const impact = page.navigationImpact();
    page.update({...context, locale: 'zh-TW'});
    await flush();
    assert.equal(days.labels[0].textContent, '行事曆預設天數');
    assert.equal(days.value, '12');
    assert.equal(credential.querySelector('input[type=password]'), secret);
    assert.equal(window.document.activeElement, secret);
    assert.equal(secret.value, 'draft-fixture');
    assert.equal(secret.selectionStart, 1);
    assert.deepEqual(page.navigationImpact(), impact);
    assert.equal(reads, 1);
    const theme = ordinary.querySelector('select[name="reply_theme"]');
    assert.equal(theme.options[1].value, 'warm_paper');
    assert.notEqual(theme.options[1].textContent, '暖桃纸');
    assert.equal(theme.options[2].textContent, 'Untranslated host label');
    save.focus();
    ordinary.requestSubmit(save);
    ordinary.requestSubmit(save);
    await flush();
    assert.equal(writes, 1);
    assert.equal(save.disabled, false);
    assert.equal(save.getAttribute('aria-busy'), 'true');
    assert.equal(window.document.activeElement, save);
    assert.equal(replacements, 0);
    finishSave();
    await flush();

    for (const input of credential.querySelectorAll('input[type="password"]')) edit(input, 'synthetic');
    await flush();
    credential.requestSubmit(credential.querySelector('button[type="submit"]'));
    await flush();
    assert.equal(replacements, 1);
    assert.equal(writes, 1);
  } finally {
    finishSave?.();
    page.dispose();
    container.remove();
  }
});

test('all FF14 queries use one template form and keep the reviewed control identities', async () => {
  const {mount} = await sourceApp('modules/ff14/pages/src/entry.ts');
  for (const [routeId, firstField, title] of [
    ['items', 'query', 'アイテム検索'], ['market', 'query', 'マーケット相場'],
    ['logs', 'server', 'キャラクターの Logs'], ['calendar', 'days', 'イベントカレンダー'],
  ]) {
    const container = window.document.body.appendChild(window.document.createElement('main'));
    let posts = 0;
    const page = mount(container, {routeId, context, scope, services: {invoke() { posts++; }}});
    try {
      await flush();
      const form = container.querySelector('.ui-page-template form');
      assert.equal(container.querySelectorAll('form').length, 1, routeId);
      const result = container.querySelector('.ui-page-template .ff14-result');
      assert.ok(result && !form.contains(result), routeId);
      assert.equal(form.querySelectorAll('button[type=submit][data-action=query]').length, 1, routeId);
      const input = form.querySelector(`#ff14-${routeId}-${firstField}`);
      assert.equal(input.labels[0].htmlFor, input.id, routeId);
      input.focus();
      page.update({...context, locale: 'ja-JP'});
      await flush();
      assert.equal(container.querySelector('.ui-page-template h2').textContent, title, routeId);
      assert.equal(form.querySelector(`#ff14-${routeId}-${firstField}`), input, routeId);
      assert.equal(window.document.activeElement, input, routeId);
      assert.equal(posts, 0, routeId);
    } finally {
      page.dispose();
      container.remove();
    }
  }
});

test('FF14 query action keeps pending focus, fences repeats and stops late presentation', async () => {
  const {mount} = await sourceApp('modules/ff14/pages/src/entry.ts');
  const {marketSample} = await import('./market_fixtures.mjs');
  const container = window.document.body.appendChild(window.document.createElement('main'));
  const posts = [];
  const page = mount(container, {routeId: 'market', context, scope, services: {
    invoke(capability, parameters) {
      return new Promise(resolve => posts.push({capability, parameters, resolve}));
    },
  }});
  try {
    const form = container.querySelector('form');
    const input = form.querySelector('#ff14-market-query');
    const submit = form.querySelector('[data-action=query]');
    form.requestSubmit(submit);
    await flush();
    assert.equal(posts.length, 0);
    assert.match(container.querySelector('#error-query').textContent, /最多120个字符/);
    page.update({...context, locale: 'en-US'});
    await flush();
    assert.match(container.querySelector('#error-query').textContent, /up to 120 characters/);
    assert.equal(form.querySelector('#ff14-market-query'), input);
    page.update(context);
    await flush();
    input.value = '犎牛牛排';
    input.dispatchEvent(new window.Event('input', {bubbles: true}));
    await flush();

    submit.focus();
    submit.click();
    form.requestSubmit(submit);
    await flush();
    assert.equal(posts.length, 1);
    assert.equal(posts[0].capability, 'ff14.market.query');
    assert.equal(submit.getAttribute('aria-busy'), 'true');
    assert.equal(submit.getAttribute('aria-disabled'), 'true');
    assert.equal(submit.disabled, false);
    assert.equal(window.document.activeElement, submit);

    page.update({...context, locale: 'en-US'});
    await flush();
    assert.equal(posts.length, 1);
    assert.equal(form.querySelector('[data-action=query]'), submit);
    assert.equal(window.document.activeElement, submit);
    assert.match(container.querySelector('.ff14-result').textContent, /Searching/);

    form.querySelector('[data-action=cancelWait]').click();
    await flush();
    assert.equal(window.document.activeElement, input);
    posts[0].resolve(marketSample('needsselection'));
    await flush();
    assert.equal(container.querySelector('[data-item-id]'), null);

    form.requestSubmit();
    await flush();
    assert.equal(posts.length, 2);
    posts[1].resolve(marketSample('success'));
    await flush();
    assert.match(container.querySelector('.ff14-result').textContent, /Resolved scope/);
    assert.equal(container.querySelector('.ff14-result h3').textContent, '犎牛牛排 · 市场');
    assert.match(container.querySelector('.ff14-result table').textContent, /测试服务器/);

    page.update({...context, available: false});
    await flush();
    assert.equal(submit.disabled, true);
    form.requestSubmit(submit);
    await flush();
    assert.equal(posts.length, 2);
    assert.equal(form.querySelector('#ff14-market-query'), input);
  } finally {
    page.dispose();
    container.remove();
  }
});

test('FF14 catalogs use the same keys and named parameters across locales', async () => {
  const load = async locale => JSON.parse(await readFile(
    new URL(`../../../modules/ff14/pages/src/i18n/${locale}.json`, import.meta.url), 'utf8',
  ));
  const base = await load('zh-CN');
  const placeholders = value => Array.from(value.matchAll(/\{([a-zA-Z][a-zA-Z0-9_]*)\}/g), match => match[1]).sort();
  for (const locale of ['zh-TW', 'ja-JP', 'en-US']) {
    const catalog = await load(locale);
    assert.deepEqual(Object.keys(catalog).sort(), Object.keys(base).sort(), locale);
    for (const [key, value] of Object.entries(base)) {
      assert.ok(catalog[key].trim(), `${locale}/${key}`);
      assert.deepEqual(placeholders(catalog[key]), placeholders(value), `${locale}/${key}`);
    }
  }
});
