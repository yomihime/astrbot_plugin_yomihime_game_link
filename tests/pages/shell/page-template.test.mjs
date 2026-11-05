import assert from 'node:assert/strict';
import {test} from 'node:test';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {sourceRuntimeURL} from './source-build.mjs';

test('template renders text, choices and stable labels and gates the single native submit', async () => {
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
    const {createApp, h, ref, nextTick, PageTemplate, uiText} = await import(await sourceRuntimeURL());
    const context = ref({theme: 'light', locale: 'en-US'});
    const pending = ref(false);
    const unavailable = ref(false);
    const readonly = ref(false);
    const fields = ref([
      {id: 'b-c', control: 'text', label: '<strong>Title</strong>', hint: 'Hint'},
      {id: 'region', control: 'select', label: 'Region', choices: [
        {value: '', label: 'Inherit'}, {value: 'x', label: 'Unavailable', disabled: true}, {value: 'y', label: 'Choice'},
      ]},
      {id: 'quantity', control: 'number', label: 'Quantity'},
      {id: 'active', control: 'checkbox', label: 'Active'},
      {id: 'extra', control: 'custom', label: 'Custom'},
    ]);
    const actions = ref([{id: 'query', type: 'submit'}, {id: 'cancelWait'}]);
    const values = ref({'b-c': 'Draft', region: '', quantity: '2', active: false, extra: 'Custom draft'});
    const events = [];
    let main, submit, asideActivations = 0;
    const views = () => [
      h(PageTemplate, {
        content: {title: 'Unknown module', sections: [
          {id: 'a', title: 'Fields', fields: fields.value},
          {id: 'a-b', title: 'More fields', fields: [{id: 'c', label: 'Other', control: 'text'}]},
        ], actions: actions.value, notes: ['<i>Plain note</i>']},
        state: {values: values.value, errors: {'b-c': '<b>Error</b>'}, pending: pending.value, disabled: unavailable.value,
          readonly: readonly.value, notice: {key: 'pendingUnverified'}},
        context: context.value, idPrefix: 'prefix 😺', styleMountTarget: target,
        onFieldChange: ({id, value}) => {events.push([id, value]); values.value[id] = value;},
        onAction: id => {
          events.push(id);
          if (id === 'query') {
            pending.value = true;
            main.requestSubmit(submit);
          }
        },
      }, {
        logo: () => h('span', {id: 'fixture-logo'}),
        headerActions: () => h('button', {id: 'header-action', type: 'button'}),
        sectionExtra: ({section}) => h('span', {class: 'section-extra'}, section.id),
        control: ({controlAttrs, value, disabled, readonly, onChange}) => h('input', {
          ...controlAttrs, name: 'custom', value, disabled, readonly,
          onInput: e => onChange(e.target.value),
        }),
        aside: () => h('form', {id: 'aside', onSubmit: event => {event.preventDefault(); asideActivations++;}}, [
          h('button', {type: 'submit'}, 'Credential action'),
        ]),
        results: () => h('output', {id: 'results'}),
        footer: () => h('div', {id: 'footer'}),
      }),
      h(PageTemplate, {
        content: {title: 'Sibling', sections: [{id: 'a', title: 'Other', fields: [
          {id: 'b-c', control: 'text', label: 'Sibling field', hint: 'Sibling hint'},
        ]}], actions: [{id: 'refresh'}]},
        context: context.value, styleMountTarget: target, idPrefix: 'prefix sibling',
      }),
    ];
    app = createApp({render: () => h('div', views())});
    app.mount(target);
    const [left, right] = target.querySelectorAll('.ui-page-template');
    main = left.querySelector('.ui-settings-main form');
    submit = main.querySelector('button[type=submit]');
    const cancel = main.querySelector('[data-action=cancelWait]');
    const input = main.querySelector('[name=b-c]');
    assert.equal(input.id, 'ygl-0070007200650066006900780020d83dde3a-0061-0062002d0063-control');
    const other = main.querySelector('[name=c]');
    const sibling = right.querySelector('[name=b-c]');
    assert.equal(new Set([input.id, other.id, sibling.id]).size, 3);
    for (const control of [input, other, sibling]) {
      const label = document.getElementById(control.getAttribute('aria-labelledby'));
      assert.equal(label.control, control);
      label.click();
      assert.equal(document.activeElement, control);
    }
    assert.equal(left.querySelector('h2').textContent, 'Unknown module');
    assert.equal(left.querySelector('h3').textContent, 'Fields');
    assert.equal(input.labels[0].textContent, '<strong>Title</strong>');
    assert.equal(left.querySelector('strong,b,i'), null);
    const described = input.getAttribute('aria-describedby').split(' ');
    assert.deepEqual(described.map(id => document.getElementById(id).textContent), ['Hint', '<b>Error</b>']);
    assert.equal(input.getAttribute('aria-invalid'), 'true');
    assert.equal(left.querySelector('[role=status]').textContent, uiText('pendingUnverified', 'en-US'));
    assert.equal(uiText('save', 'fr-FR'), '保存配置');
    assert.equal(submit.textContent, 'Query');
    assert.equal(main.noValidate, true);
    assert.equal(main.hasAttribute('action'), false);
    assert.equal(left.querySelectorAll('form').length, 2);
    assert.equal(left.querySelector('form form'), null);
    for (const selector of ['#fixture-logo', '#header-action', '#aside', '#results', '#footer', '[role=status]']) {
      assert.equal(left.querySelector(selector).closest('form') === main, false, selector);
    }
    assert.equal(left.querySelector('.section-extra').closest('form'), main);
    assert.equal(right.querySelector('form'), null);
    left.querySelector('#aside button').click();
    assert.equal(asideActivations, 1);
    const enterWithoutForm = new dom.window.KeyboardEvent('keydown', {key: 'Enter', bubbles: true});
    sibling.dispatchEvent(enterWithoutForm);
    const fake = new dom.window.SubmitEvent('submit', {cancelable: true, bubbles: true, submitter: cancel});
    assert.equal(main.dispatchEvent(fake), false);
    assert.deepEqual(events, []);

    const select = main.querySelector('select');
    const number = main.querySelector('input[type=number]');
    const check = main.querySelector('input[type=checkbox]');
    const custom = main.querySelector('[name=custom]');
    assert.deepEqual(Array.from(select.options, option => [option.value, option.disabled]), [['', false], ['x', true], ['y', false]]);
    select.value = 'y';
    select.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
    number.value = '1.5';
    number.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    check.click();
    custom.value = 'Edited';
    custom.dispatchEvent(new dom.window.Event('input', {bubbles: true}));
    assert.deepEqual(events.splice(0), [['region', 'y'], ['quantity', '1.5'], ['active', true], ['extra', 'Edited']]);
    await nextTick();

    submit.focus();
    submit.click();
    await nextTick();
    assert.deepEqual(events, ['query']);
    assert.equal(document.activeElement, submit);
    assert.equal(submit.disabled, false);
    assert.equal(submit.getAttribute('aria-disabled'), 'true');
    assert.equal(submit.getAttribute('aria-busy'), 'true');
    submit.click();
    main.requestSubmit(submit);
    cancel.click();
    assert.deepEqual(events, ['query', 'cancelWait']);
    context.value = {theme: 'dark', locale: 'zh-CN'};
    fields.value = [fields.value[3], ...fields.value.slice(0, 3), fields.value[4]];
    await nextTick();
    assert.equal(left.querySelector('form'), main);
    assert.equal(main.querySelector('button[type=submit]'), submit);
    assert.equal(main.querySelector('[name=b-c]'), input);
    assert.equal(document.activeElement, submit);
    assert.equal(input.value, 'Draft');
    assert.equal(submit.textContent, '查询');

    pending.value = false;
    readonly.value = true;
    await nextTick();
    assert.equal(submit.hasAttribute('aria-busy'), false);
    assert.equal(input.readOnly, true);
    assert.equal(custom.readOnly, true);
    assert.equal(select.disabled, true);
    assert.equal(check.disabled, true);
    assert.equal(document.activeElement, submit);
    // requestSubmit models the browser's form activation, without synthesizing an Enter default action in JSDOM.
    main.requestSubmit();
    assert.deepEqual(events, ['query', 'cancelWait', 'query']);
    unavailable.value = true;
    await nextTick();
    assert.equal(submit.disabled, true);
    assert.equal(cancel.disabled, true);
    main.requestSubmit(submit);
    assert.deepEqual(events, ['query', 'cancelWait', 'query']);

    actions.value = [];
    fields.value = [{id: 'duplicate', label: 'Duplicate', control: 'text'},
      {id: 'duplicate', label: 'Duplicate', control: 'text'}];
    await nextTick();
    assert.ok(target.querySelector('[data-error=template_invalid]'));
    fields.value = [{id: 'bad', label: 'Invalid', control: 'textarea'}];
    await nextTick();
    assert.ok(target.querySelector('[data-error=template_invalid]'));
    fields.value = [];
    context.value = {theme: 'other', locale: 'en-US'};
    await nextTick();
    assert.equal(target.querySelectorAll('[data-error=template_invalid]').length, 2);

    actions.value = [{id: 'query', type: 'submit'}, {id: 'save', type: 'submit'}];
    await nextTick();
    assert.equal(target.querySelector('[data-error=template_invalid]').getAttribute('role'), 'alert');
    assert.equal(left.isConnected, false);
    main.requestSubmit(submit);
    assert.deepEqual(events, ['query', 'cancelWait', 'query']);
  } finally {
    app?.unmount();
    dom.window.close();
    for (const [key, descriptor] of Object.entries(saved)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
