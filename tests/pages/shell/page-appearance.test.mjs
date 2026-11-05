import assert from 'node:assert/strict';
import {test} from 'node:test';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceRuntimeURL} from './source-build.mjs';

test('scoped template, native and Naive colors use one palette and reset together', async () => {
  const dom = new JSDOM('<main></main>', {pretendToBeVisual: true});
  const {document} = dom.window;
  const saved = {};
  for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'MutationObserver']) {
    saved[key] = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, {value: key === 'window' ? dom.window : dom.window[key], configurable: true});
  }
  const target = document.querySelector('main');
  let app;
  try {
    const {createApp, h, ref, nextTick, PageTemplate, UiProvider, LogoMark, NButton, NInput} =
      await import(await sourceRuntimeURL());
    const context = ref({theme: 'light', locale: 'en-US'});
    const override = ref({light: {accent: '#123456', accentContrast: '#ABCDEF', text: '#252525',
      muted: '#898989', panel: '#fafafa', border: '#666666', background: '#eeeeee', logo: '#019876'},
    dark: {accent: '#345678'}});
    const content = {title: 'Fixture', actions: [{id: 'save', type: 'submit'}], sections: [
      {id: 'section', title: 'Section', fields: [{id: 'input', label: 'Input', control: 'text'},
        {id: 'custom', label: 'Custom', control: 'custom'}]},
    ]};
    const scopes = new Map();
    const capture = (slot, presentation) => {scopes.set(slot, presentation);};
    app = createApp({render: () => h('div', [
      h(PageTemplate, {content, context: context.value, idPrefix: 'left', class: 'fixture-template',
        styleMountTarget: target, appearance: override.value}, {
        logo: ({presentation}) => {capture('logo', presentation); return h(LogoMark, {effect: 'rebound'});},
        headerActions: ({presentation}) => {capture('header', presentation); return null;},
        aside: ({presentation}) => {
          capture('aside', presentation);
          return h(NButton, {type: 'primary'}, {default: () => 'Naive'});
        },
        results: ({presentation}) => {capture('results', presentation); return null;},
        footer: ({presentation}) => {capture('footer', presentation); return null;},
        sectionExtra: ({presentation}) => {capture('section', presentation); return null;},
        control: ({presentation, controlAttrs}) => {
          capture('control', presentation);
          return h(NInput, {inputProps: controlAttrs, placeholder: 'Naive input'});
        },
      }),
      h(UiProvider, {styleMountTarget: target}, {default: () => h('div', 'Legacy slot')}),
    ])});
    app.mount(target);
    const [left, right] = target.querySelectorAll('.ui-page');
    const input = left.querySelector('[name=input]');
    const naive = left.querySelector('.n-button');
    const naiveInput = left.querySelector('.n-input');
    const logo = left.querySelector('.ui-logo-mark');
    assert.ok(left.querySelector('.ui-page-template.fixture-template'));
    assert.equal(logo.classList.contains('ui-logo-rebound'), true);
    assert.equal(logo.getAttribute('aria-hidden'), 'true');
    assert.equal(logo.childElementCount, 0);
    const accent = root => root.style.getPropertyValue('--ui-accent');
    const assertNaive = (node, variable, expected) => {
      const className = Array.from(node.classList).find(name => name.startsWith(node === naive ? '__button-' : '__input-'));
      const style = [...document.querySelectorAll('style')].find(node => node.textContent.trimStart().startsWith('.' + className));
      assert.ok(style, 'Naive component generates a scoped theme class ' + className);
      const declaration = style.textContent.split(';').find(item => item.includes(variable + ':'));
      assert.equal(declaration?.split(variable + ':').at(-1).trim(), expected);
    };
    assert.equal(accent(left), '#123456');
    assert.equal(accent(right), '#655071');
    assert.equal(left.style.getPropertyValue('--ui-logo'), '#019876');
    assert.equal(left.style.getPropertyValue('--ui-error'), '');
    assert.equal(scopes.size, 7);
    assert.equal(new Set(scopes.values()).size, 1);
    const {appearance} = scopes.get('aside');
    assert.equal(appearance.naiveOverrides.common.primaryColorHover, '#123456');
    assert.equal(appearance.naiveOverrides.common.primaryColorPressed, '#123456');
    assert.equal(appearance.naiveOverrides.common.borderColor, '#666666');
    assert.equal(appearance.naiveOverrides.common.bodyColor, '#eeeeee');
    assert.equal(appearance.naiveOverrides.common.cardColor, '#fafafa');
    assertNaive(naive, '--n-color', '#123456');
    assertNaive(naive, '--n-text-color', '#ABCDEF');
    assertNaive(naiveInput, '--n-text-color', '#252525');
    assertNaive(naiveInput, '--n-placeholder-color', '#898989');

    input.focus();
    delete override.value.light.accent;
    await nextTick();
    assert.equal(accent(left), '#655071');
    assert.equal(scopes.get('control').appearance.naiveOverrides.common.primaryColorHover, '#796087');
    assertNaive(naive, '--n-color', '#655071');
    context.value = {theme: 'dark', locale: 'zh-CN'};
    await nextTick();
    assert.equal(accent(left), '#345678');
    assert.equal(accent(right), '#655071');
    assert.equal(left.style.getPropertyValue('--ui-logo'), '#c4ad78');
    assert.equal(scopes.get('control').locale, 'zh-CN');
    assertNaive(naive, '--n-color', '#345678');

    for (const invalid of [
      {dark: {accent: '#123456', error: '#ff9900'}},
      {dark: {accent: '#123456'}, light: {logo: 'url(evil)'}},
      {dark: {accent: '#12345678'}},
      {dark: {accent: '#123456\n'}},
      {dark: {accent: '#aaa'}},
      {dark: null},
      {dark: {get accent() { throw new Error('accessor must not run'); }}},
      {dark: {accent: '#123456'}, custom: {}},
    ]) {
      override.value = invalid;
      await nextTick();
      assert.equal(accent(left), '#c4ad78', 'reject the entire invalid appearance');
      assertNaive(naive, '--n-color', '#c4ad78');
      assert.equal(left.style.getPropertyValue('--ui-error'), '');
    }
    override.value = undefined;
    await nextTick();
    assert.equal(left.querySelector('[name=input]'), input);
    assert.equal(left.querySelector('.n-button'), naive);
    assert.equal(left.querySelector('.ui-logo-mark'), logo);
    assert.equal(document.activeElement, input);
    assert.equal(left.style.getPropertyValue('--ui-logo'), '#c4ad78');
  } finally {
    app?.unmount();
    dom.window.close();
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
