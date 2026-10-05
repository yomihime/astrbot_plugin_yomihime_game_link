// Synthetic state/DOM tests; no browser rendering or visual acceptance.
import assert from "node:assert/strict";
import {pathToFileURL} from "node:url";
import {createPageApp, routeFromHash, validateState, copyCommand, MESSAGES, ROUTES, validateCatalog, validateWebStatus, validateQueryResult, queryInput}
  from "../../../pages/ff14/app.js";
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
class Target {
  listeners = new Map();
  addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, new Set()); this.listeners.get(name).add(fn); }
  removeEventListener(name, fn) { this.listeners.get(name)?.delete(fn); }
  async emit(name, event = {target: this}) { for (const fn of this.listeners.get(name) || []) await fn(event); }
  get listenerCount() { return [...this.listeners.values()].reduce((n, set) => n + set.size, 0); }
}
class Node extends Target {
  children = []; attributes = new Map(); value = ""; disabled = false; focused = false; selected = false;
  constructor(tag, document = null) { super(); this.tagName = tag; this._text = ""; this.ownerDocument = document; this.parentNode = null; }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get textContent() { return this._text + this.children.map((node) => node.textContent).join(" "); }
  setAttribute(key, value) { this.attributes.set(key, String(value)); if (key === "disabled") this.disabled = true; }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  removeAttribute(key) { this.attributes.delete(key); }
  append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) {
    for (const child of this.children) {
      const active = this.ownerDocument?.activeElement;
      if (active && child.all((node) => node === active).length) {
        active.focused = false;
        this.ownerDocument.activeElement = this.ownerDocument.body;
      }
      child.parentNode = null;
    }
    this._text = ""; this.children = []; this.append(...nodes);
  }
  contains(node) { return this.all((value) => value === node).length > 0; }
  focus(options) {
    this.focusOptions = options;
    if (this.ownerDocument) {
      if (this.ownerDocument.activeElement) this.ownerDocument.activeElement.focused = false;
      this.ownerDocument.activeElement = this;
    }
    this.focused = true;
  }
  select() { this.selected = true; }
  async click() {
    if (this.disabled) return;
    await this.emit("click");
    if (this.getAttribute("type") === "submit") {
      let parent = this.parentNode;
      while (parent && parent.tagName !== "form") parent = parent.parentNode;
      if (parent) await parent.emit("submit", {target: parent, preventDefault() {}});
    }
  }
  async press(key) { if (this.tagName === "button" && ["Enter", " "].includes(key)) await this.click(); }
  all(predicate) { return [this, ...this.children.flatMap((node) => node.all(predicate))].filter(predicate); }
}
function catalogDto(ids = ["ff14/ff14"]) {
  return {schema_version: 1, catalog_revision: 1, runtime: {state: "ready"}, modules: ids.map((module_id) => ({
    module_id, route: module_id.split("/")[1], category: "game", version: "1.0.0", enabled: true, lifecycle: "active",
    state: "loaded", reason: null, capabilities: [{capability_id: "public.read", invocation_policy: "command_only", web_declared: false}],
    config_fields: [{name: "ordinary", required: false}],
  }))};
}
function bridgeFixture(immediate = {isDark: false}, catalogData = catalogDto()) {
  const listeners = new Set(), calls = [], posts = [], allCalls = [], ready = deferred(); if (immediate) ready.resolve(immediate);
  return {calls, posts, allCalls, listeners, readyResult: ready,
    onContext(fn) { listeners.add(fn); if (immediate) fn(immediate); return () => listeners.delete(fn); },
    ready() { return ready.promise; },
    apiGet(endpoint, params) {
      if (endpoint === "catalog" && catalogData !== null) { allCalls.push({endpoint, params}); return Promise.resolve(catalogData); }
      const result = deferred(); const call = {endpoint, params, ...result}; allCalls.push(call); calls.push(call); return result.promise;
    },
    apiPost(endpoint, body) {
      const result = deferred(); const call = {endpoint, body, ...result}; allCalls.push(call); posts.push(call); return result.promise;
    },
    emitContext(context) { for (const fn of listeners) fn(context); },
  };
}
function dto(route = "overview", {invalid = false, days = 7} = {}) {
  const ordinary_config = {state: invalid ? "invalid" : "applied", invalid_field: invalid ? "ff14_calendar_default_days" : null};
  if (route === "settings") ordinary_config.values = invalid ? null : {
    ff14_default_region: "cn", ff14_calendar_default_days: days,
    ff14_calendar_default_timezone: "Asia/Shanghai", ff14_calendar_default_delivery_time: "08:00",
  };
  return {schema_version: 1,
    runtime: {state: invalid ? "invalid_config" : "ready", reason: invalid ? "ordinary_config_invalid" : null},
    module: {registered: invalid ? null : true, enabled: invalid ? null : true}, ordinary_config,
    credentials: {cn: {configured: null, state: "unknown"}, global: {configured: null, state: "unknown"}},
    subscription_gate: {supported: false, enabled: null},
    sources: ["xivapi_items", "garland_items", "fflogs_public_cn", "fflogs_public_global", "ff14_calendar_primary", "ff14_calendar_fallback"].map((id) => ({id, declared: invalid ? null : true, freshness: "unknown", last_success_at: null})),
  };
}
function harness(hash = "#/overview", bridge = bridgeFixture()) {
  const document = {activeElement: null};
  const moduleSelector = new Node("select", document), mobileModuleSelector = new Node("select", document);
  const root = new Node("main", document), selector = new Node("select", document), skip = new Node("button", document), html = new Node("html", document);
  const body = new Node("body", document);
  const links = Object.keys(ROUTES).map((route) => { const n = new Node("a", document); n.setAttribute("data-route", route); return n; });
  html.append(body); body.append(root, selector, moduleSelector, mobileModuleSelector, skip, ...links);
  Object.assign(document, {documentElement: html, body, activeElement: body,
    getElementById: (id) => ({"page-root": root, "page-select": selector, "module-select": moduleSelector, "module-select-mobile": mobileModuleSelector, "skip-content": skip})[id],
    querySelectorAll: () => links, createElement: (tag) => new Node(tag, document)});
  const media = Object.assign(new Target(), {matches: true});
  const window = Object.assign(new Target(), {location: {hash, origin: "https://ui.test"}, navigator: {}, matchMedia: () => media});
  const pending = new Map(); let id = 0;
  const clock = {delays: [], setTimer(fn, delay) { clock.delays.push(delay); pending.set(++id, fn); return id; }, clearTimer(key) { pending.delete(key); },
    fire() { for (const [key, fn] of [...pending]) { pending.delete(key); fn(); } }, get count() { return pending.size; }};
  const app = createPageApp({document, window, bridge, setTimer: clock.setTimer, clearTimer: clock.clearTimer});
  return {document, window, media, root, selector, moduleSelector, mobileModuleSelector, skip, links, bridge, clock, app,
    go(route) { window.location.hash = `#/${route}`; app.navigate(); }};
}
// Actual accepted B0c project_result samples from real Core + bundled FF14,
// synthetic offline transport; source SHA256 3017376f3d7160cb165e195ebeb0894bbf5ad862b6eb3828d146cd4cf17a2b9c.
const ACTUAL_DTO_SAMPLES = {
  "items": {
    "schema_version": 1,
    "status": "partial_success",
    "privacy": "public",
    "document": {
      "title": "Copper Ore",
      "subject": "FF14 物品 100",
      "blocks": [
        {
          "kind": "text",
          "text": "物品 ID：100"
        },
        {
          "kind": "text",
          "text": "物品等级：1；装备等级：1"
        },
        {
          "kind": "text",
          "text": "A test item."
        },
        {
          "kind": "text",
          "text": "Garland 当前未提供可解析的获取途径；这不代表物品不可获得。"
        },
        {
          "kind": "text",
          "text": "提示：Garland 未提供可识别的获取途径记录；这不代表物品不可获得。"
        },
        {
          "kind": "links",
          "links": [
            {
              "label": "XIVAPI-compatible 详情",
              "url": "https://xivapi-v2.xivcdn.com/api/sheet/Item/100"
            },
            {
              "label": "Garland Tools 国服详情",
              "url": "https://garlandtools.cn/db/#item/100"
            }
          ]
        }
      ],
      "sources": [
        "XIVAPI-compatible (xivcdn)",
        "Garland Tools CN"
      ],
      "timestamps": []
    },
    "provenance": [],
    "timestamps": [],
    "warnings": [
      "Garland 未提供可识别的获取途径记录；这不代表物品不可获得。"
    ],
    "model_facts": null,
    "error": null
  },
  "character": {
    "schema_version": 1,
    "status": "partial_success",
    "privacy": "public",
    "document": {
      "title": "FFLogs 角色战绩：Synthetic Hero",
      "subject": "Cerberus 上的公开角色战绩",
      "blocks": [
        {
          "kind": "text",
          "text": "角色：Synthetic Hero @ Cerberus"
        },
        {
          "kind": "text",
          "text": "战绩指标：rDPS；数据来自 FFLogs 公开记录。"
        },
        {
          "kind": "text",
          "text": "查询条件：FFLogs 默认副本、难度和分区。"
        },
        {
          "kind": "text",
          "text": "分区范围：全部分区。"
        },
        {
          "kind": "text",
          "text": "Synthetic Trial · 最佳 rDPS 12,345.6 · 排名百分位 93.5%"
        },
        {
          "kind": "links",
          "links": [
            {
              "label": "FFLogs 公开角色页面",
              "url": "https://www.fflogs.com/character/NA/Cerberus/Synthetic%20Hero"
            }
          ]
        }
      ],
      "sources": [
        "FFLogs 公开角色记录"
      ],
      "timestamps": []
    },
    "provenance": [],
    "timestamps": [],
    "warnings": [
      "国际服角色数据尚未完成核验，当前结果仅供参考。"
    ],
    "model_facts": null,
    "error": null
  },
  "calendar": {
    "schema_version": 1,
    "status": "success",
    "privacy": "public",
    "document": {
      "title": "FF14 活动日历摘要",
      "subject": "2026-10-03 起 7 个本地日历日",
      "blocks": [
        {
          "kind": "text",
          "text": "范围：2026-10-03 起 7 个本地日历日（UTC）"
        },
        {
          "kind": "text",
          "text": "数据源：国际服主日历（Google Calendar）；获取时间：2026-10-03 06:29 UTC。"
        },
        {
          "kind": "text",
          "text": "来源更新时间未知；仅汇总所选公开日历来源，不保证覆盖全部活动。"
        },
        {
          "kind": "table",
          "columns": [
            "活动",
            "开始",
            "结束"
          ],
          "rows": [
            [
              "Synthetic event",
              "2026-10-04 06:29",
              "2026-10-04 07:29"
            ]
          ]
        },
        {
          "kind": "links",
          "links": [
            {
              "label": "查看公开日历源",
              "url": "https://calendar.google.com/calendar/ical/1gpnler51bgs1ajti10ao946ou367bf6%40import.calendar.google.com/public/basic.ics"
            }
          ]
        },
        {
          "kind": "text",
          "text": "无时区标记的时间按 UTC 解释；这不代表发布者时区。"
        }
      ],
      "sources": [
        "国际服主日历（Google Calendar）"
      ],
      "timestamps": [
        {
          "value": "2026-10-03T06:29:59.776967+00:00",
          "timezone": "UTC"
        }
      ]
    },
    "provenance": [],
    "timestamps": [],
    "warnings": [],
    "model_facts": null,
    "error": null
  }
};

const cases = [];
const check = (name, run) => cases.push({name, run});
check("five legacy routes preserve unknown hash", () => {
  for (const route of Object.keys(ROUTES)) assert.equal(routeFromHash(`#/${route}`), route);
  for (const hash of ["", "#/other", "#/settings?secret=example", "#logs", "#/constructor"]) assert.equal(routeFromHash(hash), null);
});
check("DTO discards extras and overview values", () => {
  const input = dto(); input.private_data = "do-not-retain"; input.ordinary_config.values = {credential: "do-not-retain"};
  assert.ok(!JSON.stringify(validateState(input, "overview")).includes("do-not-retain"));
});
check("false credential/gate/source claims cannot become success or empty data", () => {
  for (const mutate of [(v) => { v.credentials.cn.configured = true; }, (v) => { v.subscription_gate.supported = true; },
    (v) => { v.sources = []; }, (v) => { v.sources[0].declared = "unknown"; }, (v) => { v.sources[1].id = v.sources[0].id; }]) {
    const value = dto(); mutate(value); assert.throws(() => validateState(value, "overview"), /invalid_state/);
  }
});
check("ready and immediate onContext issue exactly one namespaced read", async () => {
  const h = harness(); h.app.start(); h.app.start(); await flush();
  assert.equal(h.bridge.calls.length, 1); assert.equal(h.bridge.calls[0].endpoint, "overview"); assert.deepEqual(h.bridge.calls[0].params, {});
  assert.deepEqual(h.bridge.allCalls.map((c) => c.endpoint), ["catalog", "overview"]);
  h.bridge.emitContext({isDark: true}); await flush(); assert.equal(h.bridge.calls.length, 1);
  assert.equal(h.document.documentElement.getAttribute("data-theme"), "dark"); h.app.destroy();
});
check("pending read ignores duplicate refresh and shows loading", async () => {
  const h = harness(); h.app.start(); await flush(); h.app.refresh(); h.app.refresh(); await flush();
  assert.equal(h.bridge.calls.length, 1); assert.match(h.root.textContent, /正在读取公共状态/); assert.equal(h.root.getAttribute("aria-busy"), "true"); h.app.destroy();
});
check("removing a focused DOM node moves focus to the document body", () => {
  const h = harness(), button = h.document.createElement("button");
  h.root.append(button); button.focus(); assert.equal(h.document.activeElement, button);
  h.root.replaceChildren(); assert.equal(h.document.activeElement, h.document.body); assert.equal(button.focused, false);
});
check("refresh preserves heading and focused button through loading and success", async () => {
  const h = harness(); h.app.start(); await flush(); h.bridge.calls[0].resolve(dto()); await flush();
  const title = h.root.all((n) => n.tagName === "h1")[0], button = h.root.all((n) => n.tagName === "button")[0];
  button.focus(); await button.click(); await flush();
  assert.equal(h.document.activeElement, button); assert.equal(button.getAttribute("aria-disabled"), "true"); assert.equal(button.disabled, false);
  await button.click(); await button.press("Enter"); await button.press(" "); await flush(); assert.equal(h.bridge.calls.length, 2);
  h.bridge.calls[1].resolve(dto()); await flush();
  assert.equal(h.document.activeElement, button); assert.equal(h.root.all((n) => n.tagName === "button")[0], button);
  assert.equal(h.root.all((n) => n.tagName === "h1")[0], title); assert.equal(button.getAttribute("aria-disabled"), "false"); h.app.destroy();
});
check("failure and retry keep the same focused keyboard target", async () => {
  const h = harness(); h.app.start(); await flush(); h.bridge.calls[0].reject(new Error("failure")); await flush();
  const button = h.root.all((n) => n.tagName === "button")[0]; assert.equal(button.textContent, "重试读取");
  button.focus(); await button.click(); await flush(); assert.equal(h.document.activeElement, button);
  h.bridge.calls[1].reject(new Error("failure")); await flush();
  assert.equal(h.document.activeElement, button); assert.equal(button.textContent, "重试读取");
  await button.click(); await flush(); h.bridge.calls[2].resolve(dto()); await flush();
  assert.equal(h.document.activeElement, button); assert.equal(h.root.all((n) => n.tagName === "button")[0], button); h.app.destroy();
});
check("read completion never takes focus back after the user moves it", async () => {
  for (const fails of [false, true]) {
    const h = harness(); h.app.start(); await flush(); h.bridge.calls[0].resolve(dto()); await flush();
    const button = h.root.all((n) => n.tagName === "button")[0]; button.focus(); await button.click(); await flush();
    h.selector.focus(); assert.equal(h.document.activeElement, h.selector);
    if (fails) h.bridge.calls[1].reject(new Error("failure")); else h.bridge.calls[1].resolve(dto());
    await flush(); assert.equal(h.document.activeElement, h.selector); assert.equal(button.focused, false); h.app.destroy();
  }
});
check("overview order and unknowns stay explicit", async () => {
  const h = harness(); h.app.start(); await flush(); const value = dto();
  value.module = {registered: null, enabled: null}; value.sources.forEach((s) => { s.declared = null; });
  h.bridge.calls[0].resolve(value); await flush();
  assert.deepEqual(h.root.all((n) => n.tagName === "h2").map((n) => n.textContent), ["运行", "模块 · FF14", "普通配置", "订阅推送", "模块任务", "来源", "订阅推送状态"]);
  for (const text of [/已注册 · 已加载/, /声明状态未知/, /当前页面无法读取凭据配置状态/, /当前页面无法确认开关状态/]) assert.match(h.root.textContent, text);
  assert.ok(!h.root.textContent.includes("未配置")); h.app.destroy();
});
check("navigation invalidates slow response and updates selection", async () => {
  const h = harness(); h.app.start(); await flush(); h.go("settings"); await flush();
  assert.deepEqual(h.bridge.calls.map((c) => c.endpoint), ["overview", "settings"]);
  h.bridge.calls[0].resolve(dto()); await flush(); assert.equal(h.app.getState().phase, "loading");
  h.bridge.calls[1].resolve(dto("settings", {days: 3})); await flush();
  assert.equal(h.app.getState().data.ordinary_config.values.ff14_calendar_default_days, 3); assert.equal(h.selector.value, "settings");
  assert.equal(h.links.find((n) => n.getAttribute("aria-current") === "page").getAttribute("data-route"), "settings"); h.app.destroy();
});
check("timeout fences late response and permits retry", async () => {
  const h = harness(); h.app.start(); await flush(); h.clock.fire(); assert.equal(h.app.getState().phase, "timeout");
  h.bridge.calls[0].resolve(dto()); await flush(); assert.equal(h.app.getState().phase, "timeout");
  h.app.refresh(); await flush(); assert.equal(h.bridge.calls.length, 2);
  h.bridge.calls[1].resolve(dto()); await flush(); assert.equal(h.app.getState().phase, "ready"); h.app.destroy();
});
check("refresh failure labels retained snapshot and hides raw message/status", async () => {
  const h = harness(); h.app.start(); await flush(); h.bridge.calls[0].resolve(dto()); await flush(); h.app.refresh(); await flush();
  assert.equal(h.app.getState().stale, true); assert.ok(h.app.getState().data); h.bridge.calls[1].reject(Object.assign(new Error("private payload"), {status: 403})); await flush();
  assert.equal(h.app.getState().phase, "error"); assert.ok(h.app.getState().data); assert.match(h.root.textContent, /旧状态.*尚未确认最新状态/); assert.ok(!h.root.textContent.includes("private payload")); h.app.destroy();
});
check("invalid settings clears values and both route caches", async () => {
  const h = harness(); h.app.start(); await flush(); h.bridge.calls[0].resolve(dto()); await flush();
  h.go("settings"); await flush(); h.bridge.calls[1].resolve(dto("settings", {days: 3})); await flush(); assert.match(h.root.textContent, /08:00/);
  h.app.refresh(); await flush(); h.bridge.calls[2].resolve(dto("settings", {invalid: true})); await flush();
  assert.equal(h.app.getState().data.ordinary_config.values, null); assert.ok(!h.root.textContent.includes("08:00")); assert.match(h.root.textContent, /未取得有效配置值/);
  h.go("overview"); await flush(); assert.equal(h.app.getState().data, null); h.app.destroy();
});
check("query pages read only settings and web status before manual submission", async () => {
  for (const route of ["logs", "items", "calendar"]) {
    const h = harness(`#/${route}`); h.app.start(); await flush(); assert.deepEqual(h.bridge.calls.map((v) => v.endpoint), ["settings", "web-status"]);
    for (const input of h.root.all((n) => n.tagName === "input")) {
      assert.ok(h.root.all((n) => n.tagName === "label" && n.getAttribute("for") === input.getAttribute("id")).length);
    }
    assert.equal(h.bridge.posts.length, 0); assert.match(h.root.textContent, /入口状态或默认配置尚未就绪/);
    assert.equal(h.root.all((n) => n.getAttribute("type") === "submit")[0].disabled, true);
    if (route === "calendar") assert.match(h.root.textContent, /本人私聊/); h.app.destroy();
  }
});
check("clipboard denial selects command for manual copy", async () => {
  const h = harness("#/items"); h.app.start(); await flush();
  const textarea = h.root.all((n) => n.tagName === "textarea")[0]; await h.root.all((n) => n.tagName === "button" && n.textContent === "复制命令")[0].click();
  assert.ok(textarea.focused && textarea.selected); assert.match(h.root.textContent, /命令已选中，请手动复制/); assert.equal(textarea.value, "/ygl ff14 item 44091"); h.app.destroy();
});
check("item page copies the exact positional item command", async () => {
  const h = harness("#/items"); let copied;
  h.window.navigator.clipboard = {writeText: async (value) => { copied = value; }};
  h.app.start(); await flush();
  await h.root.all((n) => n.tagName === "button" && n.textContent === "复制命令")[0].click();
  assert.equal(copied, "/ygl ff14 item 44091"); assert.equal(h.root.all((n) => n.tagName === "textarea")[0].value, copied); h.app.destroy();
});
check("successful copy uses exact command", async () => {
  const node = new Node("textarea"); node.value = "/ygl ff14 help"; let copied;
  assert.equal(await copyCommand(node, {writeText: async (v) => { copied = v; }}), "已复制聊天命令。"); assert.equal(copied, node.value); assert.equal(node.selected, false);
});
check("late clipboard failure cannot steal focus after navigation", async () => {
  const h = harness("#/logs"), copy = deferred(); h.window.navigator.clipboard = {writeText: () => copy.promise}; h.app.start(); await flush();
  const textarea = h.root.all((n) => n.tagName === "textarea")[0]; const copying = h.root.all((n) => n.tagName === "button" && n.textContent === "复制命令")[0].click();
  h.go("items"); copy.reject(new Error("denied")); await copying; assert.equal(textarea.focused, false); assert.equal(textarea.selected, false); h.app.destroy();
});
check("system theme only applies before host context", async () => {
  const h = harness("#/overview", bridgeFixture(null)); h.app.start(); assert.equal(h.document.documentElement.getAttribute("data-theme"), "dark");
  h.bridge.emitContext({isDark: false}); await flush(); h.media.matches = true; await h.media.emit("change");
  assert.equal(h.document.documentElement.getAttribute("data-theme"), "light"); h.app.destroy();
});
check("host readiness timeout fences late context", async () => {
  const h = harness("#/overview", bridgeFixture(null)); h.app.start(); h.clock.fire(); assert.equal(h.app.getState().phase, "error");
  h.bridge.readyResult.resolve({isDark: true}); await flush(); assert.equal(h.bridge.calls.length, 0); assert.equal(h.app.getState().message, MESSAGES.unavailable); h.app.destroy();
});
check("missing bridge shows fixed recovery text", () => {
  const h = harness("#/overview", null); h.app.start(); assert.equal(h.app.getState().phase, "error"); assert.equal(h.app.getState().message, MESSAGES.unavailable); h.app.destroy();
});
check("skip focuses main without changing hash", async () => {
  const h = harness("#/items"); h.app.start(); await h.skip.click(); assert.equal(h.window.location.hash, "#/items"); assert.ok(h.root.focused); h.app.destroy();
});
check("destroy clears listeners timers and fences late reads", async () => {
  const h = harness(); h.app.start(); await flush(); h.app.destroy(); h.app.destroy();
  assert.equal(h.clock.count, 0); assert.equal(h.bridge.listeners.size, 0);
  for (const node of [h.window, h.media, h.selector, h.skip]) assert.equal(node.listenerCount, 0);
  h.bridge.calls[0].resolve(dto()); await flush(); assert.equal(h.app.getState().data, null);
});
check("schema2 gate states and strict schema1 compatibility", () => {
  const legacy = validateState(dto(), "overview");
  assert.deepEqual(legacy.subscription_gate, {supported: false, enabled: null, can_run: null, reason: "unsupported"});
  const valid = [
    {supported: false, enabled: null, can_run: null, reason: "unsupported"},
    {supported: true, enabled: null, can_run: null, reason: "state_unknown"},
    ...["initialization_invalid", "config_missing", "config_invalid", "fence_invalid"].map((reason) => ({supported: true, enabled: null, can_run: false, reason})),
    {supported: true, enabled: false, can_run: false, reason: "gate_disabled"},
    ...["module_disabled", "runtime_not_ready"].map((reason) => ({supported: true, enabled: true, can_run: false, reason})),
    {supported: true, enabled: true, can_run: true, reason: null},
  ];
  for (const gate of valid) {
    const input = dto(); input.schema_version = 2; input.subscription_gate = gate;
    assert.deepEqual(validateState(input, "overview").subscription_gate, gate);
  }
  const rejected = [
    {supported: true, enabled: false, can_run: true, reason: null},
    {supported: false, enabled: null, can_run: false, reason: "unsupported"},
    {supported: true, enabled: true, can_run: true, reason: "gate_disabled"},
    {supported: true, enabled: null, can_run: false, reason: "state_unknown"},
    {supported: true, enabled: false, can_run: false, reason: "fence_invalid"},
    {supported: true, enabled: null, can_run: null, reason: "private_detail"},
    {supported: 1, enabled: true, can_run: true, reason: null},
    {supported: true, enabled: true, can_run: true, reason: null, intent_revision: 1},
  ];
  for (const gate of rejected) {
    const input = dto(); input.schema_version = 2; input.subscription_gate = gate;
    assert.throws(() => validateState(input, "overview"), /invalid_state/);
  }
  const legacyExtra = dto(); legacyExtra.subscription_gate.can_run = null;
  assert.throws(() => validateState(legacyExtra, "overview"), /invalid_state/);
  for (const mutate of [(v) => { v.runtime.state = "not_ready"; },
    (v) => { v.module.enabled = false; }, (v) => { v.module.registered = null; }]) {
    const input = dto(); input.schema_version = 2;
    input.subscription_gate = {supported: true, enabled: true, can_run: true, reason: null};
    mutate(input); assert.throws(() => validateState(input, "overview"), /invalid_state/);
  }
});
check("gate card renders real Chinese states and fixed error notices on both pages", async () => {
  const cases = [
    [false, null, null, "unsupported", "开关状态未知", "unknown", false],
    [true, null, null, "state_unknown", "订阅状态未知", "unknown", false],
    [true, false, false, "gate_disabled", "订阅已暂停", "warning", false],
    [true, true, false, "module_disabled", "订阅开关已开启 · 模块已禁用", "warning", false],
    [true, true, false, "runtime_not_ready", "订阅开关已开启 · 运行尚未就绪", "warning", false],
    [true, true, true, null, "订阅可运行", "success", false],
    ...["initialization_invalid", "config_missing", "config_invalid", "fence_invalid"].map((reason) => [true, null, false, reason, "订阅状态异常", "unknown", true]),
  ];
  const gateCard = (h) => h.root.all((node) => node.tagName === "section"
    && node.children[0]?.textContent === "订阅推送状态")[0];
  const projections = new Map();
  for (const route of ["overview", "settings"]) {
    const h = harness(`#/${route}`); h.app.start(); await flush();
    const unread = gateCard(h);
    assert.ok(unread);
    assert.equal(unread.children[1].getAttribute("class"), "status-label status-unknown");
    assert.equal(unread.all((n) => n.getAttribute("role") === "alert").length, 0);
    assert.equal(unread.all((n) => n.getAttribute("class") === "notice error").length, 0);
    for (const [supported, enabled, can_run, reason, label, tone, error] of cases) {
      if (h.bridge.calls.length > 1 || h.app.getState().phase === "ready") { h.app.refresh(); await flush(); }
      const input = dto(route); input.schema_version = 2; input.subscription_gate = {supported, enabled, can_run, reason};
      h.bridge.calls.at(-1).resolve(input); await flush();
      const card = gateCard(h);
      assert.ok(card);
      assert.equal(card.children[1].textContent, label);
      assert.equal(card.children[1].getAttribute("class"), `status-label status-${tone}`);
      assert.match(card.textContent, /本页仅显示状态/);
      assert.match(card.textContent, /暂停时订阅记录保留/);
      assert.match(card.textContent, /模块正常运行时，本人仍可在本人私聊中查看、修改或取消订阅/);
      assert.match(card.textContent, /恢复后仅处理后续未来窗口，不补发暂停窗口/);
      assert.match(card.textContent, /暂停前已经开始发送的通知可能完成/);
      assert.match(card.textContent, /准入成立仅表示可以运行，不保证来源可用或发送成功/);
      assert.equal(card.all((n) => ["button", "input", "a", "select"].includes(n.tagName)).length, 0);
      if (reason === "unsupported") assert.match(card.textContent, /当前页面无法确认开关状态/);
      if (error) {
        assert.match(card.textContent, /请联系管理员检查订阅开关及准入状态/);
        assert.doesNotMatch(card.textContent, /齿轮|普通配置|原生表单/);
      }
      const errors = card.all((node) => node.getAttribute("class") === "notice error");
      assert.equal(errors.length, error ? 1 : 0);
      assert.equal(card.all((n) => n.getAttribute("role") === "alert").length, error ? 1 : 0);
      if (route === "overview") projections.set(reason, card.textContent);
      else assert.equal(card.textContent, projections.get(reason));
    }
    if (route === "settings") assert.match(h.root.textContent, /公开查询页只读.*独立授权管理页/);
    h.app.destroy();
  }
});
check("schema2 runnable requires applied ordinary config with no invalid field", () => {
  for (const route of ["overview", "settings"]) {
    for (const state of ["invalid", "valid_not_ready", "unknown"]) {
      const input = dto(route); input.schema_version = 2;
      input.subscription_gate = {supported: true, enabled: true, can_run: true, reason: null};
      input.ordinary_config.state = state;
      if (route === "settings" && state !== "valid_not_ready") input.ordinary_config.values = null;
      assert.throws(() => validateState(input, route), /invalid_state/);
      input.schema_version = 1; input.subscription_gate = {supported: false, enabled: null};
      assert.doesNotThrow(() => validateState(input, route));
    }
    for (const invalidField of ["ordinary_config", "ff14_calendar_default_days"]) {
      const input = dto(route); input.schema_version = 2;
      input.subscription_gate = {supported: true, enabled: true, can_run: true, reason: null};
      input.ordinary_config.invalid_field = invalidField;
      assert.throws(() => validateState(input, route), /invalid_state/);
      input.schema_version = 1; input.subscription_gate = {supported: false, enabled: null};
      assert.doesNotThrow(() => validateState(input, route));
    }
    const valid = dto(route); valid.schema_version = 2;
    valid.subscription_gate = {supported: true, enabled: true, can_run: true, reason: null};
    assert.equal(validateState(valid, route).subscription_gate.can_run, true);
  }
});
check("catalog sanitizer bounds metadata and discards payload extras", () => {
  const value = catalogDto(["new/mod"]);
  value.private_payload = "SENTINEL_TOP";
  value.modules[0].config_fields[0].default = "SENTINEL_DEFAULT";
  value.modules[0].config_fields[0].description = "SENTINEL_DESCRIPTION";
  value.modules[0].config_fields[0].current = "SENTINEL_VALUE";
  value.modules[0].capabilities[0].input_schema = {description: "SENTINEL_SCHEMA"};
  assert.ok(!JSON.stringify(validateCatalog(value)).includes("SENTINEL"));
  for (const mutate of [
    (v) => { v.modules.push(v.modules[0]); },
    (v) => { v.runtime.state = "closed"; },
    (v) => { v.modules[0].enabled = false; },
    (v) => { v.modules[0].config_fields[0].sensitive = true; },
    (v) => { v.modules[0].capabilities[0].web_declared = true; },
    (v) => { v.modules[0].capabilities[0].capability_id = "a".repeat(4097); },
    (v) => { v.modules[0].config_fields[0].name = "a".repeat(129); },
    (v) => { v.modules = Array.from({length: 65}, (_, i) => ({...v.modules[0], module_id: `sample/m${i}`})); },
    (v) => { v.private_payload = "a".repeat(262144); },
  ]) { const input = catalogDto(); mutate(input); assert.throws(() => validateCatalog(input), /invalid_catalog/); }
});
check("catalog must resolve before any FF14 state request", async () => {
  const h = harness("#/overview", bridgeFixture({isDark: false}, null));
  h.app.start(); await flush(); assert.deepEqual(h.bridge.allCalls.map((v) => v.endpoint), ["catalog"]);
  assert.equal(h.moduleSelector.disabled, true);
  h.bridge.calls[0].resolve(catalogDto()); await flush();
  assert.deepEqual(h.bridge.allCalls.map((v) => v.endpoint), ["catalog", "overview"]);
  assert.equal(h.moduleSelector.value, "ff14/ff14");
  h.bridge.calls[1].resolve(dto()); await flush(); assert.equal(h.app.getState().phase, "ready"); h.app.destroy();
});
check("empty, unknown and failed directories never expose FF14 tasks", async () => {
  for (const payload of [catalogDto([]), {schema_version: 1, catalog_revision: null, runtime: {state: "not_ready"}, modules: null}]) {
    const h = harness("", bridgeFixture({isDark: false}, payload)); h.app.start(); await flush();
    assert.equal(h.bridge.calls.length, 0); assert.ok(h.links.every((v) => v.getAttribute("hidden") !== null));
    assert.doesNotMatch(h.root.textContent, /FFLogs|角色|物品|活动日历/);
    assert.match(h.root.textContent, payload.modules === null ? /状态未知/ : /暂无已注册模块/); h.app.destroy();
  }
  const h = harness("", bridgeFixture({isDark: false}, null)); h.app.start(); await flush();
  h.bridge.calls[0].reject(new Error("SENTINEL_PRIVATE")); await flush();
  assert.equal(h.app.getState().catalog, null); assert.match(h.root.textContent, /读取失败/);
  assert.doesNotMatch(h.root.textContent, /暂无已注册模块|SENTINEL_PRIVATE/); h.app.destroy();
});
check("registered module without renderer gets a generic view automatically", async () => {
  const h = harness("", bridgeFixture({isDark: false}, catalogDto(["unknown/module"]))); h.app.start(); await flush();
  assert.equal(h.app.getState().moduleId, "unknown/module"); assert.equal(h.bridge.calls.length, 0);
  assert.match(h.root.textContent, /普通配置声明|public.read|ordinary/);
  assert.doesNotMatch(h.root.textContent, /FFLogs|订阅推送|活动日历|潮风亭/);
  assert.equal(h.links.find((v) => v.getAttribute("data-route") === "logs").getAttribute("hidden"), "");
  assert.deepEqual(h.selector.children.map((v) => v.getAttribute("value")), ["overview", "settings"]); h.app.destroy();
});
check("two modules use separate selectors and discard late FF14 state", async () => {
  const h = harness("", bridgeFixture({isDark: false}, catalogDto(["ff14/ff14", "new/module"]))); h.app.start(); await flush();
  assert.equal(h.app.getState().moduleId, null); assert.equal(h.bridge.calls.length, 0);
  h.moduleSelector.value = "ff14/ff14"; await h.moduleSelector.emit("change"); await flush();
  assert.equal(h.bridge.calls[0].endpoint, "overview");
  h.mobileModuleSelector.value = "new/module"; await h.mobileModuleSelector.emit("change"); await flush();
  assert.equal(h.app.getState().moduleId, "new/module"); assert.match(h.root.textContent, /new\/module/);
  h.bridge.calls[0].resolve(dto()); await flush();
  assert.equal(h.app.getState().data, null); assert.doesNotMatch(h.root.textContent, /FFLogs|订阅推送|潮风亭/); h.app.destroy();
});
check("selected module exiting directory and unknown pages have explicit recovery", async () => {
  const h = harness("#/module/new%2Fmodule/overview", bridgeFixture({isDark: false}, null)); h.app.start(); await flush();
  h.bridge.calls[0].resolve(catalogDto(["new/module"])); await flush(); assert.match(h.root.textContent, /模块已加载/);
  h.app.refresh(); await flush(); h.bridge.calls[1].resolve(catalogDto([])); await flush();
  assert.match(h.root.textContent, /所选模块已退出目录/); assert.doesNotMatch(h.root.textContent, /FFLogs/);
  h.window.location.hash = "#/module/new%2Fmodule/logs"; h.app.navigate(); await flush();
  assert.match(h.root.textContent, /所选模块已退出目录/); h.app.destroy();
  const bad = harness("#/module/new%2Fmodule/logs", bridgeFixture({isDark: false}, catalogDto(["new/module"])));
  bad.app.start(); await flush(); assert.match(bad.root.textContent, /页面不存在/); assert.equal(bad.bridge.calls.length, 0); bad.app.destroy();
});
check("session context change fences old catalog and forces a fresh read", async () => {
  const h = harness("", bridgeFixture({isDark: false}, null)); h.app.start(); await flush();
  h.bridge.emitContext({isDark: true, sessionGeneration: 2}); await flush(); assert.equal(h.bridge.calls.length, 2);
  h.bridge.calls[0].resolve(catalogDto(["ff14/ff14"])); await flush(); assert.equal(h.app.getState().catalog, null);
  h.bridge.calls[1].resolve(catalogDto(["new/module"])); await flush();
  assert.equal(h.app.getState().moduleId, "new/module"); assert.equal(h.bridge.calls.length, 2); h.app.destroy();
});
check("disabled generic module displays declarations without claiming execution", async () => {
  const value = catalogDto(["new/module"]); Object.assign(value.modules[0], {enabled: false, lifecycle: "stopped", state: "disabled", reason: "module_disabled"});
  const h = harness("", bridgeFixture({isDark: false}, value)); h.app.start(); await flush();
  assert.match(h.root.textContent, /模块已禁用|任务不可用/); assert.doesNotMatch(h.root.textContent, /模块已加载/); assert.equal(h.bridge.calls.length, 0); h.app.destroy();
});

function settingsDto() {
  const value = dto("settings"); value.schema_version = 2;
  value.subscription_gate = {supported: true, enabled: false, can_run: false, reason: "gate_disabled"};
  return value;
}
function webDto() { return {schema_version: 1, configuration: "configured", origin: "https://ui.test", entry_ready: true}; }
async function queryHarness(route = "items", {settings = settingsDto(), web = webDto(), catalog = catalogDto()} = {}) {
  const h = harness(`#/${route}`, bridgeFixture({isDark: false}, catalog)); h.app.start(); await flush();
  const settingCall = h.bridge.calls.find((v) => v.endpoint === "settings"), webCall = h.bridge.calls.find((v) => v.endpoint === "web-status");
  if (settingCall) settingCall.resolve(settings);
  if (webCall) webCall.resolve(web);
  await flush(); return h;
}
const inputFor = (h, key) => h.root.all((n) => n.getAttribute("name") === key)[0];
const submitFor = (h) => h.root.all((n) => n.getAttribute("type") === "submit")[0];
async function edit(h, key, value) { const node = inputFor(h, key); node.value = value; await node.emit("input"); }

check("actual frozen Core FF14 DTO samples render all three exact manual endpoints", async () => {
  for (const [route, sample, body] of [["items", "items", {query: "100"}], ["logs", "character", {region: "global", server: "Cerberus", character: "Synthetic Hero"}], ["calendar", "calendar", {region: "global", days: 7, timezone: "UTC"}]]) {
    const h = await queryHarness(route);
    for (const [key, value] of Object.entries(body)) await edit(h, key, String(value));
    assert.equal(h.bridge.posts.length, 0); const submit = submitFor(h); submit.focus();
    await submit.press("Enter"); await flush();
    assert.equal(h.bridge.posts.length, 1); assert.equal(h.bridge.posts[0].endpoint, `queries/${sample}`);
    assert.deepEqual(h.bridge.posts[0].body, body); assert.equal(submit.disabled, true);
    await submit.click(); await flush(); assert.equal(h.bridge.posts.length, 1);
    h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES[sample]); await flush();
    assert.equal(h.app.getState().query.result.status, ACTUAL_DTO_SAMPLES[sample].status);
    assert.match(h.root.textContent, new RegExp(ACTUAL_DTO_SAMPLES[sample].document.title));
    assert.equal(h.document.activeElement, submit);
    const links = h.root.all((n) => n.tagName === "a" && n.getAttribute("target") === "_blank");
    assert.ok(links.length); assert.ok(links.every((n) => n.getAttribute("rel") === "noopener noreferrer"));
    if (route === "calendar") {
      assert.ok(h.root.all((n) => n.tagName === "th").length);
      assert.ok(h.root.all((n) => n.getAttribute("class") === "result-table-row").length);
      assert.match(h.root.textContent, /用户输入范围：国际服 \/ 7天 \/ UTC/);
    }
    h.app.destroy();
  }
});
check("entry requires real configured matching origin loaded module and ready settings", async () => {
  const scenarios = [
    {web: {...webDto(), configuration: "unconfigured", origin: null, entry_ready: false}},
    {web: {...webDto(), configuration: "invalid", origin: null, entry_ready: false}},
    {web: {...webDto(), origin: "https://other.test"}}, {web: {...webDto(), entry_ready: false}},
    {settings: {...settingsDto(), runtime: {state: "not_ready", reason: "runtime_not_ready"}}},
    {settings: {...settingsDto(), module: {registered: true, enabled: false}}},
  ];
  const disabled = catalogDto(); Object.assign(disabled.modules[0], {enabled: false, lifecycle: "stopped", state: "disabled", reason: "module_disabled"}); scenarios.push({catalog: disabled});
  for (const scenario of scenarios) {
    const h = await queryHarness("items", scenario); await edit(h, "query", "100");
    assert.equal(submitFor(h).disabled, true); await submitFor(h).click(); await flush(); assert.equal(h.bridge.posts.length, 0);
    assert.match(h.root.textContent, /刷新|配置|地址|登录|重新打开/); h.app.destroy();
  }
  const h = harness("#/calendar"); h.app.start(); await flush();
  assert.equal(inputFor(h, "days").value, ""); assert.equal(inputFor(h, "timezone").value, "");
  h.bridge.calls.find((v) => v.endpoint === "settings").reject(new Error("RAW_SECRET"));
  h.bridge.calls.find((v) => v.endpoint === "web-status").resolve(webDto()); await flush();
  assert.equal(submitFor(h).disabled, true); assert.doesNotMatch(h.root.textContent, /RAW_SECRET/); assert.equal(h.bridge.posts.length, 0); h.app.destroy();
});
check("settings defaults are read values and edited input is retained across state refresh", async () => {
  const value = settingsDto(); Object.assign(value.ordinary_config.values, {ff14_default_region: "global", ff14_calendar_default_days: 3, ff14_calendar_default_timezone: "UTC"});
  const h = await queryHarness("calendar", {settings: value});
  assert.equal(inputFor(h, "days").value, "3"); assert.equal(inputFor(h, "region").value, "global"); assert.equal(inputFor(h, "timezone").value, "UTC");
  await edit(h, "days", "11"); inputFor(h, "days").focus();
  h.app.refresh(); await flush();
  h.bridge.calls.filter((v) => v.endpoint === "settings").at(-1).resolve(settingsDto()); h.bridge.calls.filter((v) => v.endpoint === "web-status").at(-1).resolve(webDto()); await flush();
  assert.equal(inputFor(h, "days").value, "11"); assert.equal(h.document.activeElement, inputFor(h, "days")); assert.equal(h.bridge.posts.length, 0); h.app.destroy();
});
check("input bounds and accessible errors prevent invalid POST without losing input", async () => {
  for (const [route, values, key] of [["items", {query: ""}, "query"], ["logs", {server: "", character: "Synthetic"}, "server"], ["calendar", {days: "1.5", timezone: "Not/AZone"}, "days"]]) {
    const h = await queryHarness(route); for (const [field, value] of Object.entries(values)) await edit(h, field, value);
    await submitFor(h).click(); await flush(); assert.equal(h.bridge.posts.length, 0);
    const input = inputFor(h, key); assert.equal(input.getAttribute("aria-invalid"), "true"); assert.equal(h.document.activeElement, input);
    assert.ok(input.getAttribute("aria-describedby")); assert.match(h.root.textContent, /请输入|整数/); h.app.destroy();
  }
  for (const days of ["0", "31", "true", "NaN", "1e1"]) assert.ok(queryInput("calendar", {region: "cn", days, timezone: "UTC"}).errors.days);
  assert.ok(queryInput("items", {query: "x".repeat(121)}).errors.query);
});
check("four business statuses preserve public content and give explicit safe recovery", async () => {
  for (const status of ["success", "partial_success", "needs_selection", "error"]) {
    const h = await queryHarness(); await edit(h, "query", "100"); await submitFor(h).click(); await flush();
    const sample = structuredClone(ACTUAL_DTO_SAMPLES.items); sample.status = status;
    if (status === "error") { sample.document = null; sample.error = {code: "auth_required", message: "RAW_PRIVATE_ERROR"}; }
    h.bridge.posts[0].resolve(sample); await flush();
    assert.equal(h.app.getState().query.result.status, status); assert.equal(inputFor(h, "query").value, "100"); assert.doesNotMatch(h.root.textContent, /RAW_PRIVATE_ERROR/);
    assert.match(h.root.textContent, status === "success" ? /查询完成/ : status === "partial_success" ? /部分结果/ : status === "needs_selection" ? /需要更精确输入/ : /来源凭据不可用/);
    h.app.destroy();
  }
  const h = await queryHarness(); await edit(h, "query", "100"); await submitFor(h).click(); await flush();
  h.bridge.posts[0].reject(new Error("HTTP_403_RAW_SECRET")); await flush();
  assert.equal(h.app.getState().query.phase, "error"); assert.match(h.root.textContent, /查询未完成，请检查登录、网页地址和输入/); assert.doesNotMatch(h.root.textContent, /HTTP_403_RAW_SECRET/); h.app.destroy();
});
check("query DTO rejects unknown blocks private resources dangerous links and envelopes", () => {
  for (const mutate of [
    (v) => {v.privacy = "private";}, (v) => {v.document.blocks.push({kind: "html", html: "<script>"});},
    (v) => {v.document.blocks.push({kind: "image", asset_id: "private"});},
    ...["javascript:alert(1)", "data:text/html,bad", "file:///secret", "//evil.test/", "https://user:secret@evil.test/", "https://ui.test/api/plugin/page/assets/private", "https://evil.test/\n"].map((url) => (v) => {v.document.blocks = [{kind: "links", links: [{label: "bad", url}]}];}),
    (v) => {v.document.blocks = [{kind: "fields", fields: {resource_id: "private"}}];},
    (v) => {v.document.blocks = [{kind: "text", text: "x".repeat(262144)}];},
    (v) => {v.document.blocks = [{kind: "table", columns: ["one"], rows: [[1, 2]]}];},
  ]) {const value = structuredClone(ACTUAL_DTO_SAMPLES.items); mutate(value); assert.throws(() => validateQueryResult(value), /invalid_query_result/);}
  assert.throws(() => validateQueryResult({status: "ok", data: ACTUAL_DTO_SAMPLES.items}), /invalid_query_result/);
  const value = structuredClone(ACTUAL_DTO_SAMPLES.items); value.model_facts = {secret: "DO_NOT_RENDER"}; value.private_extra = "DO_NOT_RENDER";
  assert.ok(!JSON.stringify(validateQueryResult(value)).includes("DO_NOT_RENDER"));
  for (const origin of ["https://ui.test/path", "https://user@ui.test", "*", "null"]) assert.throws(() => validateWebStatus({...webDto(), origin}), /invalid_web_status/);
});
check("pure text and empty calendar do not fabricate markup classification or completeness", async () => {
  const h = await queryHarness("calendar"); await submitFor(h).click(); await flush();
  const sample = structuredClone(ACTUAL_DTO_SAMPLES.calendar);
  sample.document.blocks = [{kind: "text", text: "<img src=x onerror=secret()>"}, {kind: "table", columns: ["原列名"], rows: []}];
  h.bridge.posts[0].resolve(sample); await flush();
  assert.match(h.root.textContent, /<img src=x onerror=secret\(\)>/); assert.equal(h.root.all((n) => n.tagName === "img").length, 0);
  assert.match(h.root.textContent, /返回内容为空，无法确认该窗口无活动/); assert.doesNotMatch(h.root.textContent, /职业分类|完整无活动/); h.app.destroy();
});
check("late query cannot survive input route module metadata close or stop changes", async () => {
  for (const mode of ["input", "route", "module", "context", "close", "stop"]) {
    const h = await queryHarness(); await edit(h, "query", "100"); await submitFor(h).click(); await flush();
    const call = h.bridge.posts[0];
    if (mode === "input") await edit(h, "query", "101");
    else if (mode === "route") h.go("calendar");
    else if (mode === "module") {h.window.location.hash = "#/module/other%2Fmod/overview"; h.app.navigate();}
    else if (mode === "context") h.bridge.emitContext({isDark: true, sessionGeneration: 2});
    else if (mode === "close") h.app.destroy();
    else {const stop = h.root.all((n) => n.textContent === "停止展示")[0]; stop.focus(); await stop.click(); assert.match(h.root.textContent, /本次请求可能仍在后台处理/); assert.equal(h.document.activeElement, submitFor(h));}
    await flush(); call.resolve(ACTUAL_DTO_SAMPLES.items); await flush();
    assert.equal(h.app.getState().query.result, null); assert.doesNotMatch(h.root.textContent, /Copper Ore/); assert.equal(h.bridge.posts.length, 1); h.app.destroy();
  }
});
check("35 second query view deadline is separate from status timeout and permits manual retry", async () => {
  const h = await queryHarness(); await edit(h, "query", "100"); await submitFor(h).click(); await flush();
  assert.equal(h.clock.delays.at(-1), 35000); h.clock.fire(); assert.equal(h.app.getState().query.phase, "timeout");
  assert.match(h.root.textContent, /后台请求可能仍在处理/); h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.equal(h.app.getState().query.result, null);
  await submitFor(h).click(); await flush(); assert.equal(h.bridge.posts.length, 2); h.bridge.posts[1].resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.ok(h.app.getState().query.result); h.app.destroy(); assert.equal(h.clock.count, 0);
});

check("item query polish distinguishes loading complete partial no-match and failure without dropping content", async () => {
  const h = await queryHarness("items"); await edit(h, "query", "100");
  assert.equal(h.root.all((n) => n.getAttribute("data-query-route") === "items").length, 2);
  for (const [status, code, label] of [["success", null, /查询完成/], ["partial_success", null, /部分结果/], ["error", "not_found", /无匹配结果/], ["error", "upstream_error", /查询失败/]]) {
    await submitFor(h).click(); await flush();
    assert.match(h.root.textContent, /正在查询/); assert.equal(submitFor(h).disabled, true);
    const sample = structuredClone(ACTUAL_DTO_SAMPLES.items); sample.status = status;
    if (code) { sample.document = null; sample.error = {code, message: "PRIVATE"}; }
    else {
      sample.document.blocks.push({kind: "text", text: "内容已截断：请查看来源详情。"});
      sample.warnings.push("另一个缺项警告"); sample.provenance.push("补充来源");
    }
    h.bridge.posts.at(-1).resolve(sample); await flush();
    assert.match(h.root.textContent, label); assert.equal(h.app.getState().query.result.status, status);
    if (code) assert.equal(h.root.all((n) => n.getAttribute("role") === "alert").length, 1);
    else {
      for (const block of sample.document.blocks.filter((b) => b.kind === "text")) assert.ok(h.root.textContent.includes(block.text));
      for (const text of [...sample.warnings, ...sample.document.sources, ...sample.provenance]) assert.ok(h.root.textContent.includes(text));
      assert.equal(h.root.all((n) => n.getAttribute("class") === "result-title")[0].textContent, sample.document.title);
    }
  }
  h.app.destroy();
  const logs = await queryHarness("logs"); assert.equal(logs.root.all((n) => n.getAttribute("data-query-route") !== null).length, 0); logs.app.destroy();
});
check("calendar retries use the same submit and reset on pending success input and route changes", async () => {
  const h = await queryHarness("calendar"), submit = submitFor(h);
  const fail = async () => {
    await submitFor(h).click(); await flush();
    const sample = structuredClone(ACTUAL_DTO_SAMPLES.calendar);
    sample.status = "error"; sample.document = null; sample.error = {code: "upstream_error", message: "PRIVATE"};
    h.bridge.posts.at(-1).resolve(sample); await flush();
    assert.equal(submitFor(h).textContent, "手动重试日历");
    assert.match(h.root.textContent, /来源暂时不可用/); assert.doesNotMatch(h.root.textContent, /没有活动/);
    assert.ok(h.root.textContent.includes(`用户输入范围：国服 / ${inputFor(h, "days").value}天 / Asia/Shanghai`));
  };
  await fail(); assert.equal(submitFor(h), submit); const count = h.bridge.posts.length;
  await submit.press("Enter"); await flush(); assert.equal(h.bridge.posts.length, count + 1);
  assert.equal(submit.textContent, "查询日历"); assert.equal(submit.disabled, true);
  await submit.click(); await flush(); assert.equal(h.bridge.posts.length, count + 1);
  h.bridge.posts.at(-1).resolve(ACTUAL_DTO_SAMPLES.calendar); await flush();
  assert.equal(submit.textContent, "查询日历"); assert.equal(submit.disabled, false);
  await fail(); await edit(h, "days", "8"); assert.equal(submit.textContent, "查询日历");
  await fail(); h.go("items"); await flush(); assert.equal(submitFor(h).textContent, "查询物品");
  h.go("calendar"); await flush(); assert.equal(submitFor(h).textContent, "查询日历");
  const posts = h.bridge.posts.length;
  for (const call of h.bridge.calls.filter((v) => v.endpoint === "settings")) call.resolve(settingsDto());
  for (const call of h.bridge.calls.filter((v) => v.endpoint === "web-status")) call.resolve(webDto());
  await flush(); assert.equal(h.bridge.posts.length, posts);
  await submitFor(h).click(); await flush(); h.bridge.posts.at(-1).reject(new Error("PRIVATE")); await flush();
  assert.equal(submitFor(h).textContent, "手动重试日历");
  await submitFor(h).click(); await flush(); assert.equal(submitFor(h).textContent, "查询日历");
  const late = h.bridge.posts.at(-1);
  await h.root.all((n) => n.tagName === "button" && n.textContent === "停止展示")[0].click();
  late.resolve(ACTUAL_DTO_SAMPLES.calendar); await flush();
  assert.equal(h.app.getState().query.result, null); assert.equal(submitFor(h).textContent, "查询日历"); h.app.destroy();
});

check("presentation and duplicate contexts retain input selection focus result and all nodes without requests", async () => {
  const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
  h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush();
  const input = inputFor(h, "query"), result = h.app.getState().query.result, nodes = [...h.root.children], calls = h.bridge.allCalls.length;
  input.selectionStart = 1; input.selectionEnd = 4; input.focus();
  for (const context of [{isDark: false}, {isDark: true}, {isDark: true, locale: "en-US", i18n: {label: "Item"}}, {locale: "zh-CN", isDark: false}]) {
    h.bridge.emitContext(context); await flush();
    assert.equal(h.bridge.allCalls.length, calls); assert.equal(h.document.activeElement, input);
    assert.equal(inputFor(h, "query"), input); assert.equal(input.value, "44091");
    assert.equal(input.selectionStart, 1); assert.equal(input.selectionEnd, 4);
    assert.equal(h.app.getState().query.result, result); assert.deepEqual(h.root.children, nodes);
  }
  h.app.destroy();
});
check("presentation changes during a query retain its pending request and accept its result", async () => {
  const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
  const calls = h.bridge.allCalls.length; h.bridge.emitContext({isDark: true, locale: "en-US"}); await flush();
  assert.equal(h.app.getState().query.phase, "loading"); assert.equal(h.bridge.allCalls.length, calls);
  h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.ok(h.app.getState().query.result); h.app.destroy();
});
check("delayed catalog refresh retains navigation structure and query snapshot while gating submissions", async () => {
  const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
  h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush();
  const input = inputFor(h, "query"), result = h.app.getState().query.result, form = input.parentNode.parentNode.parentNode;
  const oldGet = h.bridge.apiGet.bind(h.bridge), delay = deferred(); h.bridge.apiGet = (endpoint, params) => endpoint === "catalog" ? delay.promise : oldGet(endpoint, params);
  input.focus(); h.app.refresh(); await flush();
  assert.equal(inputFor(h, "query"), input); assert.equal(input.parentNode.parentNode.parentNode, form);
  assert.equal(h.document.activeElement, input); assert.ok(h.links.every((v) => v.getAttribute("hidden") === null));
  assert.match(h.root.textContent, /旧状态.*尚未确认最新状态/); assert.equal(h.app.getState().query.result, result);
  assert.equal(submitFor(h).disabled, true); await submitFor(h).click(); assert.equal(h.bridge.posts.length, 1);
  delay.resolve(catalogDto()); await flush();
  h.bridge.calls.filter(v => v.endpoint === "settings").at(-1).resolve(settingsDto());
  h.bridge.calls.filter(v => v.endpoint === "web-status").at(-1).resolve(webDto()); await flush();
  assert.equal(inputFor(h, "query"), input); assert.equal(h.document.activeElement, input); assert.equal(submitFor(h).disabled, false);
  assert.equal(h.app.getState().query.result, result); assert.equal(h.bridge.posts.length, 1); h.app.destroy();
});
check("refresh failure or timeout retains labeled snapshots and never enables unconfirmed querying", async () => {
  for (const mode of ["failure", "timeout"]) {
    const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
    h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush();
    const input = inputFor(h, "query"), result = h.app.getState().query.result, delay = deferred();
    h.bridge.apiGet = () => delay.promise; input.focus(); h.app.refresh(); await flush();
    if (mode === "failure") delay.reject(new Error("PRIVATE_TRANSPORT_ERROR")); else h.clock.fire();
    await flush(); assert.equal(inputFor(h, "query"), input); assert.equal(h.document.activeElement, input);
    assert.equal(h.app.getState().query.result, result); assert.equal(submitFor(h).disabled, true);
    assert.match(h.root.textContent, /旧状态.*暂不可用/); assert.doesNotMatch(h.root.textContent, /PRIVATE_TRANSPORT_ERROR/);
    delay.resolve(catalogDto()); await flush(); assert.notEqual(h.app.getState().phase, "ready"); h.app.destroy();
  }
});
check("status revalidation fences a query already in flight without automatically submitting a replacement", async () => {
  const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
  const post = h.bridge.posts[0]; h.app.refresh(); await flush();
  post.resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.equal(h.app.getState().query.result, null);
  assert.equal(h.bridge.posts.length, 1); assert.match(h.root.textContent, /已停止展示待返回结果/); h.app.destroy();
});
check("same page names do not override a changed identity authorization or instance lifecycle", async () => {
  for (const change of [{sessionGeneration: 2}, {authorizationGeneration: 2}, {pageInstanceId: "new"}, {lifecycle: "reopened"}]) {
    const initial = {pluginName: "astrbot_plugin_yomihime_game_link", pageName: "ff14", isDark: false};
    const h = await queryHarness(); h.bridge.emitContext(initial); await flush();
    h.bridge.calls.filter(v => v.endpoint === "settings").at(-1).resolve(settingsDto()); h.bridge.calls.filter(v => v.endpoint === "web-status").at(-1).resolve(webDto()); await flush();
    await edit(h, "query", "44091"); await submitFor(h).click(); await flush(); const post = h.bridge.posts[0];
    h.bridge.emitContext({...initial, ...change}); await flush(); post.resolve(ACTUAL_DTO_SAMPLES.items); await flush();
    assert.equal(h.app.getState().query.result, null); assert.equal(inputFor(h, "query").value, ""); assert.equal(h.bridge.posts.length, 1); h.app.destroy();
  }
});
check("unrepresentable unknown lifecycle values reject context instead of being silently erased", async () => {
  const cyclic = {}; cyclic.self = cyclic;
  const extended = structuredClone(Object.assign([1], {revoked: true}));
  const holeWithExtraKey = Object.assign([1, ,], {revoked: true});
  for (const value of [undefined, NaN, Infinity, -0, 1n, new Date(0), new Map(), cyclic, Array(1), [undefined], extended, holeWithExtraKey, () => true]) {
    const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
    const old = h.bridge.posts[0], count = h.bridge.allCalls.length;
    h.bridge.emitContext({isDark: false, authorizationGeneration: value}); await flush();
    assert.equal(h.app.getState().phase, "error"); assert.equal(h.app.getState().catalog, null);
    assert.equal(h.app.getState().query.result, null); assert.equal(h.bridge.allCalls.length, count);
    old.resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.equal(h.app.getState().query.result, null);
    h.app.destroy();
  }
});
check("non-plain root contexts cannot preserve a pending query", async () => {
  for (const context of [new Date(0), new Map()]) {
    const h = await queryHarness(); await edit(h, "query", "44091"); await submitFor(h).click(); await flush();
    h.bridge.emitContext(context); await flush(); assert.equal(h.app.getState().phase, "error");
    h.bridge.posts[0].resolve(ACTUAL_DTO_SAMPLES.items); await flush(); assert.equal(h.app.getState().query.result, null); h.app.destroy();
  }
});
check("wrong bridge page rejects requests and initial ready cannot resurrect a superseded context", async () => {
  const h = harness(); h.app.start();
  h.bridge.emitContext({pluginName: "unrelated", pageName: "ff14"}); await flush();
  assert.equal(h.app.getState().phase, "error"); assert.equal(h.app.getState().catalog, null);
  const calls = h.bridge.allCalls.length;
  h.bridge.emitContext({pluginName: "astrbot_plugin_yomihime_game_link", pageName: "management"}); await flush();
  assert.equal(h.bridge.allCalls.length, calls); assert.equal(h.app.getState().phase, "error");
  h.bridge.emitContext({pluginName: "astrbot_plugin_yomihime_game_link", pageName: "ff14"}); await flush();
  assert.equal(h.app.getState().phase, "loading"); h.app.destroy();
});
check("navigation uses explicit scroll to top with preventScroll while skip keeps native focus scroll", async () => {
  const h = harness(); const scrolls = []; h.window.scrollTo = options => scrolls.push(options);
  h.app.start(); await flush(); h.go("items"); await flush();
  assert.deepEqual(h.root.focusOptions, {preventScroll: true}); assert.deepEqual(scrolls, [{top: 0, left: 0, behavior: "instant"}]);
  h.app.refresh(); await flush(); assert.equal(scrolls.length, 1);
  await h.skip.click(); assert.equal(h.root.focusOptions, undefined); assert.equal(scrolls.length, 1); h.app.destroy();
});

export async function runUiATests() {
  const passed = []; for (const {name, run} of cases) { await run(); passed.push(name); }
  return {kind: "synthetic-state-and-DOM", passed: passed.length, tests: passed};
}

// Shared only by contract tests consuming freshly projected Python results.
export {queryHarness, edit, submitFor, flush};

if (typeof process !== "undefined" && process.argv[1] &&
    import.meta.url === pathToFileURL(process.argv[1]).href) {
  console.log(JSON.stringify(await runUiATests(), null, 2));
}
