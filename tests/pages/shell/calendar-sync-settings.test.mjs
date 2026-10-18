import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {JSDOM} from '../../../pages/frontend/node_modules/jsdom/lib/api.js';
import {catalog, snapshot, context} from '../management/fixtures.mjs';

const host = new JSDOM('<body></body>', {pretendToBeVisual: true});
for (const key of ['window', 'document', 'Element', 'HTMLElement', 'SVGElement', 'Node', 'MutationObserver']) globalThis[key] = host.window[key];
const {createManagementPage, validateCatalog, validateSnapshot, updateBody} = await import('../../../pages/management/app.js');
const name = 'ff14_calendar_sync_interval_seconds', owner = 'ff14/ff14';
const manifest = JSON.parse(await readFile(new URL('../../../modules/ff14/yomihime.manifest.json', import.meta.url), 'utf8'));
const field = manifest.modules[0].config_fields.find(field => field.name === name);
const declarations = catalog();
declarations.fields.push({module_id: owner, name, description: field.description, group: field.group, value_schema: field.value_schema, default: field.default, required: false, readable: true, editable: true, blocked_reason: null});
const flush = async () => {for (let i = 0; i < 100; i++) await Promise.resolve();};

async function fixture() {
  const dom = new JSDOM('<div id="management-root"></div>', {pretendToBeVisual: true}), document = dom.window.document;
  const values = snapshot(), writes = [];
  values[owner].fields[name] = {value: 21600, state: 'valid', present: false, source: 'default'};
  const bridge = {
    ready: async () => context,
    onContext() {return () => {};},
    async apiPost(endpoint, body) {
      if (endpoint === 'admin/catalog') return structuredClone(declarations);
      if (endpoint === 'admin/read') return structuredClone(values);
      if (endpoint === 'admin/credential-catalog') return {schema_version: 1, fields: []};
      if (endpoint === 'admin/credential-status') return {};
      if (endpoint === 'admin/credential-readiness') return {ready: true, state: 'ready', reason_code: 'ready',diagnostics:{key_state:'valid_format',codec_state:'available'}};
      assert.equal(endpoint, 'admin/update');
      writes.push(structuredClone(body));
      assert.equal(body.module_id, owner);
      const row = values[owner];
      if (row.revision !== body.expected_revision) throw Error('revision_conflict');
      for (const update of body.updates) {
        assert.equal(update.field, name);
        if (update.mode === 'clear') row.fields[name] = {value: 21600, state: 'valid', present: false, source: 'default'};
        else {
          assert.equal(update.mode, 'replace');
          assert.ok([21600, 43200, 86400].includes(update.value));
          row.fields[name] = {value: update.value, state: 'valid', present: true, source: 'sqlite'};
        }
      }
      return {module_id: owner, revision: ++row.revision};
    },
  };
  const page = createManagementPage(document, bridge, dom.window);
  page.start();
  await flush();
  const row = () => document.querySelector(`[data-field="${name}"]`);
  const input = () => row().querySelector('[data-role="value"]');
  const save = () => document.querySelector(`section[data-owner="${owner}"] [data-action="save"]`);
  return {
    dom, document, values, writes, page, row, input, save,
    async select(index) {input().value = String(index); input().dispatchEvent(new dom.window.Event('change', {bubbles: true})); await flush();},
    async refresh() {document.querySelector('#refresh').click(); await flush();},
    async close() {page.dispose(); await flush(); dom.window.close();},
  };
}

test('calendar sync setting uses declared numeric enum with Chinese labels and verified save readback', async () => {
  const f = await fixture();
  try {
    assert.equal(f.row().querySelector('label').textContent, '日历同步周期');
    assert.equal(f.input().tagName, 'SELECT');
    assert.deepEqual([...f.input().options].filter(option => !option.disabled).map(option => option.textContent), ['每6小时', '每12小时', '每24小时']);
    assert.equal(f.input().value, '0');
    for (const [index, seconds] of [[1, 43200], [2, 86400], [0, 21600]]) {
      const count = f.writes.length, revision = f.values[owner].revision;
      await f.select(index);
      assert.equal(f.writes.length, count);
      f.save().click(); f.save().click();
      await flush();
      assert.equal(f.writes.length, count + 1);
      assert.deepEqual(f.writes.at(-1), {module_id: owner, expected_revision: revision, updates: [{field: name, mode: 'replace', value: seconds}]});
      assert.equal(f.values[owner].fields[name].value, seconds);
      assert.equal(f.row().querySelector('[data-role="mode"]').value, 'keep');
      assert.equal(f.input().value, String(index));
      assert.equal(f.document.querySelector('#status').textContent, '已保存。');
    }
    f.values[owner].fields[name].value = 86400;
    f.values[owner].revision++;
    await f.refresh();
    assert.equal(f.input().value, '2');
    f.row().querySelector('[data-action="default"]').click();
    assert.equal(f.input().value, '0');
    assert.equal(f.row().querySelector('[data-role="mode"]').value, 'clear');
    f.save().click(); await flush();
    assert.deepEqual(f.writes.at(-1).updates, [{field: name, mode: 'clear'}]);
    assert.equal(f.values[owner].fields[name].value, 21600);
  } finally {await f.close();}
});

test('calendar sync conflicts keep the draft and require a fresh CAS revision', async () => {
  const f = await fixture();
  try {
    await f.select(1);
    f.values[owner].fields[name].value = 86400;
    f.values[owner].revision = 8;
    f.save().click(); await flush();
    assert.equal(f.writes.length, 1);
    assert.equal(f.values[owner].fields[name].value, 86400);
    assert.equal(f.input().value, '1');
    assert.match(f.document.querySelector('#status').textContent, /配置已变化/);
    assert.equal(f.save().getAttribute('aria-disabled'), 'true');
    f.save().dispatchEvent(new f.dom.window.Event('click')); await flush();
    assert.equal(f.writes.length, 1);
    await f.refresh();
    assert.equal(f.input().value, '1');
    f.save().click(); await flush();
    assert.equal(f.writes.at(-1).expected_revision, 8);
    assert.equal(f.values[owner].fields[name].value, 43200);
    assert.equal(f.values[owner].revision, 9);
    assert.equal(f.input().value, '1');
  } finally {await f.close();}
});

test('calendar sync rejects undeclared values, preserves ordinary access checks, and flags invalid stored data', async () => {
  const checked = validateCatalog(declarations);
  for (const value of ['', '-1', '3', '21600', '1.5', 'true']) {
    assert.throws(() => updateBody(owner, 1, {[name]: {mode: 'replace', value}}, checked));
  }
  const denied = structuredClone(declarations), deniedField = denied.fields.find(field => field.name === name);
  Object.assign(deniedField, {readable: false, editable: false, blocked_reason: 'not_granted'});
  assert.throws(() => updateBody(owner, 1, {[name]: {mode: 'replace', value: '0'}}, validateCatalog(denied)), /admin_authorization_denied/);
  const f = await fixture();
  try {
    for (const invalid of [900, '21600', true, 21601]) {
      const values = structuredClone(f.values);
      values[owner].fields[name].value = invalid;
      assert.throws(() => validateSnapshot(values, checked));
    }
    f.values[owner].fields[name] = {value: null, state: 'invalid', present: true, source: 'sqlite'};
    await f.refresh();
    assert.equal(f.input().value, '');
    assert.match(f.row().textContent, /当前值无效/);
    assert.equal(f.writes.length, 0);
    f.row().querySelector('[data-action="default"]').click();
    f.save().click(); await flush();
    assert.equal(f.input().value, '0');
    assert.equal(f.values[owner].fields[name].value, 21600);
  } finally {await f.close();}
});
