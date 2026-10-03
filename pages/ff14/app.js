export const ROUTES = Object.freeze({
  overview: "概览", logs: "公开角色 Logs", items: "物品查询",
  calendar: "活动日历", settings: "设置",
});
export const MESSAGES = Object.freeze({
  unchecked: "尚未读取状态。",
  loading: "正在读取公共状态，请稍候。",
  error: "状态读取失败。请确认宿主会话仍有效，然后重试。",
  timeout: "状态读取超时，请重试。",
  unavailable: "宿主连接尚未就绪，请重新打开插件页面或重试。",
  stale: "当前显示上次读取的旧状态，尚未确认最新状态。",
  copyFailed: "复制未完成，命令已选中，请手动复制。",
});
const FIELD_LABELS = Object.freeze({
  ff14_default_region: "默认 FF14 区域",
  ff14_calendar_default_days: "日历默认查询天数",
  ff14_calendar_default_timezone: "日历默认时区",
  ff14_calendar_default_delivery_time: "新订阅每日摘要时间",
});
const SOURCE_LABELS = Object.freeze({
  xivapi_items: "物品主源 · XIVAPI-compatible",
  garland_items: "物品补充详情 · Garland Tools CN",
  fflogs_public_cn: "国服公开角色 · FFLogs",
  fflogs_public_global: "国际服公开角色 · FFLogs",
  ff14_calendar_primary: "活动日历主源",
  ff14_calendar_fallback: "活动日历备用源",
});
const CONFIG_LABELS = Object.freeze({
  applied: "已生效", valid_not_ready: "配置有效，等待运行",
  invalid: "配置无效，插件暂停", unknown: "配置状态未知",
});
const REASONS = new Set([
  null, "runtime_not_ready", "ordinary_config_invalid", "existing_manifest_mismatch",
  "unsupported_environment", "package_permission_denied", "extension_root_permission_denied",
  "package_root_unavailable", "extension_root_unavailable", "package_write_failed", "bundle_unavailable",
]);
const nullableBool = (value) => value === null || typeof value === "boolean";
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const QUERY_ENDPOINTS = Object.freeze({items: "queries/items", logs: "queries/character", calendar: "queries/calendar"});
const QUERY_MESSAGES = Object.freeze({
  failed: "查询未完成，请检查登录、网页地址和输入后手动重试。可重新登录 Dashboard，检查插件配置齿轮中的网页地址，保存重载后重新打开本页。",
  stopped: "已停止展示。仅停止展示，本次请求可能仍在后台处理；不会自动重试。",
  timeout: "等待查询超过35秒，已停止展示。后台请求可能仍在处理，请检查会话与来源后手动重试。",
});
const ERROR_CODES = new Set(["parameter_error", "unbound", "auth_required", "auth_expired", "not_found", "not_public", "no_records", "unparsed", "rate_limited", "upstream_error", "module_unavailable", "unsupported", "unknown"]);
const text = (value, limit = 262144) => typeof value === "string" && value.length <= limit;
function validOrigin(value) {
  if (!text(value, 2048)) return false;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && url.origin === value && !url.username && !url.password; }
  catch { return false; }
}
export function validateWebStatus(input) {
  if (!object(input) || input.schema_version !== 1 || !["configured", "unconfigured", "invalid"].includes(input.configuration)
      || typeof input.entry_ready !== "boolean"
      || (input.configuration === "configured" ? !validOrigin(input.origin) : input.origin !== null || input.entry_ready)) throw new Error("invalid_web_status");
  return {schema_version: 1, configuration: input.configuration, origin: input.origin, entry_ready: input.entry_ready};
}
function publicValue(value, depth = 0) {
  if (depth > 8) throw new Error("invalid_query_result");
  if (value === null || typeof value === "boolean" || text(value) || (typeof value === "number" && Number.isFinite(value))) return value;
  if (Array.isArray(value) && value.length <= 512) return value.map((v) => publicValue(v, depth + 1));
  if (object(value) && Object.keys(value).length <= 256) {
    if (Object.keys(value).some((key) => /^(?:path|resource_?id|asset_?id|html|__proto__|prototype|constructor)$/i.test(key))) throw new Error("invalid_query_result");
    return Object.fromEntries(Object.entries(value).map(([key, v]) => [key, publicValue(v, depth + 1)]));
  }
  throw new Error("invalid_query_result");
}
function publicLink(link) {
  if (!object(link) || !text(link.label, 4096) || !text(link.url, 2048) || !/^https?:\/\//i.test(link.url) || /[\s\u0000-\u001f\u007f]/.test(link.url)) throw new Error("invalid_query_result");
  try {
    const url = new URL(link.url);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || /^\/api\/(?:plug\/|plugin\/|v1\/plugins\/)/i.test(url.pathname)) throw new Error();
    return {label: link.label, url: url.href};
  } catch { throw new Error("invalid_query_result"); }
}
export function validateQueryResult(input) {
  const fail = () => { throw new Error("invalid_query_result"); };
  if (!object(input) || input.schema_version !== 1 || input.privacy !== "public"
      || !["success", "partial_success", "needs_selection", "error"].includes(input.status)) fail();
  try { if (new TextEncoder().encode(JSON.stringify(input)).length > 262144) fail(); } catch { fail(); }
  const strings = (values) => { if (!Array.isArray(values) || values.length > 512 || values.some((v) => !text(v))) fail(); return [...values]; };
  const times = (values) => {
    if (!Array.isArray(values) || values.length > 512) fail();
    return values.map((v) => {
      if (!object(v) || !text(v.value, 128) || !(v.timezone === null || text(v.timezone, 128)) || !Number.isFinite(Date.parse(v.value))) fail();
      return {value: v.value, timezone: v.timezone};
    });
  };
  let document = null, error = null;
  if (input.status === "error") {
    if (input.document !== null || !object(input.error) || !ERROR_CODES.has(input.error.code)) fail();
    error = {code: input.error.code}; // Never render an arbitrary error message.
  } else {
    const doc = input.document;
    if (!object(doc) || !text(doc.title, 4096) || !text(doc.subject, 4096) || !Array.isArray(doc.blocks) || doc.blocks.length > 32 || input.error !== null) fail();
    const blocks = doc.blocks.map((block) => {
      if (!object(block)) fail();
      if (block.kind === "text" && text(block.text)) return {kind: "text", text: block.text};
      if (["fields", "metrics"].includes(block.kind) && object(block[block.kind])) return {kind: block.kind, [block.kind]: publicValue(block[block.kind])};
      if (block.kind === "table" && Array.isArray(block.columns) && block.columns.length > 0 && block.columns.length <= 512 && block.columns.every((v) => text(v, 4096))
          && Array.isArray(block.rows) && block.rows.length <= 512 && block.rows.every((row) => Array.isArray(row) && row.length === block.columns.length)) return {kind: "table", columns: [...block.columns], rows: publicValue(block.rows)};
      if (block.kind === "links" && Array.isArray(block.links) && block.links.length <= 512) return {kind: "links", links: block.links.map(publicLink)};
      fail();
    });
    document = {title: doc.title, subject: doc.subject, blocks, sources: strings(doc.sources), timestamps: times(doc.timestamps)};
  }
  // model_facts, result IDs, private extras and unknown metadata are not retained.
  return {schema_version: 1, status: input.status, privacy: "public", document, error,
    provenance: strings(input.provenance), timestamps: times(input.timestamps), warnings: strings(input.warnings)};
}

export function queryInput(route, draft) {
  const errors = {};
  const bounded = (key, label, limit) => {
    const value = String(draft[key] ?? "").trim();
    if (!value || value.length > limit) errors[key] = `请输入${label}，最多${limit}个字符。`;
    return value;
  };
  let body;
  if (route === "items") body = {query: bounded("query", "名称或 ID", 120)};
  else {
    const region = String(draft.region ?? "");
    if (!["cn", "global"].includes(region)) errors.region = "请选择国服或国际服。";
    if (route === "logs") body = {region, server: bounded("server", "服务器", 100), character: bounded("character", "角色名", 120)};
    else if (route === "calendar") {
      const rawDays = String(draft.days ?? "");
      const days = /^[0-9]+$/.test(rawDays) ? Number(rawDays) : NaN;
      if (!Number.isInteger(days) || days < 1 || days > 30) errors.days = "查询天数须为1至30的整数。";
      const timezone = bounded("timezone", "IANA 时区", 128);
      try { new Intl.DateTimeFormat("en-US", {timeZone: timezone}); } catch { errors.timezone = "请输入有效 IANA 时区，例如 Asia/Shanghai。"; }
      body = {region, days, timezone};
    } else throw new Error("invalid_query_route");
  }
  return {body, errors};
}

export function routeFromHash(hash) {
  const route = String(hash || "").replace(/^#\//, "");
  return Object.hasOwn(ROUTES, route) ? route : null;
}

export function validateState(input, route) {
  // Copy a precise DTO rather than retaining the bridge payload or extra fields.
  if (!object(input) || ![1, 2].includes(input.schema_version) || !object(input.runtime)
      || !["ready", "not_ready", "invalid_config"].includes(input.runtime.state)
      || !REASONS.has(input.runtime.reason) || !object(input.module)
      || !nullableBool(input.module.registered) || !nullableBool(input.module.enabled)
      || !object(input.ordinary_config) || !Object.hasOwn(CONFIG_LABELS, input.ordinary_config.state)
      || !(input.ordinary_config.invalid_field === null
        || input.ordinary_config.invalid_field === "ordinary_config"
        || Object.hasOwn(FIELD_LABELS, input.ordinary_config.invalid_field))) throw new Error("invalid_state");
  const ordinary = {state: input.ordinary_config.state, invalid_field: input.ordinary_config.invalid_field};
  if (route === "settings") {
    const values = input.ordinary_config.values;
    if (["invalid", "unknown"].includes(ordinary.state)) {
      if (values !== null) throw new Error("invalid_state");
      ordinary.values = null;
    } else {
      if (!object(values) || Object.keys(values).length !== 4
          || !Object.keys(FIELD_LABELS).every((key) => Object.hasOwn(values, key))
          || !["cn", "global"].includes(values.ff14_default_region)
          || !Number.isInteger(values.ff14_calendar_default_days)
          || values.ff14_calendar_default_days < 1 || values.ff14_calendar_default_days > 30
          || typeof values.ff14_calendar_default_timezone !== "string"
          || !values.ff14_calendar_default_timezone || values.ff14_calendar_default_timezone.length > 128
          || typeof values.ff14_calendar_default_delivery_time !== "string"
          || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(values.ff14_calendar_default_delivery_time)) throw new Error("invalid_state");
      ordinary.values = Object.fromEntries(Object.keys(FIELD_LABELS).map((key) => [key, values[key]]));
    }
  }
  if (!object(input.credentials) || !["cn", "global"].every((region) =>
    object(input.credentials[region]) && input.credentials[region].configured === null
      && input.credentials[region].state === "unknown")
    || !Array.isArray(input.sources)
    || input.sources.length !== Object.keys(SOURCE_LABELS).length) throw new Error("invalid_state");
  const rawGate = input.subscription_gate;
  let subscriptionGate;
  if (input.schema_version === 1) {
    if (!object(rawGate) || Object.keys(rawGate).length !== 2
        || rawGate.supported !== false || rawGate.enabled !== null) throw new Error("invalid_state");
    subscriptionGate = {supported: false, enabled: null, can_run: null, reason: "unsupported"};
  } else {
    const errorReasons = ["initialization_invalid", "config_missing", "config_invalid", "fence_invalid"];
    if (!object(rawGate) || Object.keys(rawGate).length !== 4
        || !["supported", "enabled", "can_run", "reason"].every((key) => Object.hasOwn(rawGate, key))
        || typeof rawGate.supported !== "boolean" || !nullableBool(rawGate.enabled)
        || !nullableBool(rawGate.can_run)) throw new Error("invalid_state");
    const {supported, enabled, can_run: canRun, reason} = rawGate;
    const valid = !supported ? enabled === null && canRun === null && reason === "unsupported"
      : reason === "state_unknown" ? enabled === null && canRun === null
      : errorReasons.includes(reason) ? enabled === null && canRun === false
      : reason === "gate_disabled" ? enabled === false && canRun === false
      : ["module_disabled", "runtime_not_ready"].includes(reason) ? enabled === true && canRun === false
      : reason === null && enabled === true && canRun === true;
    if (!valid) throw new Error("invalid_state");
    if (canRun === true && (input.runtime.state !== "ready"
        || input.module.registered !== true || input.module.enabled !== true
        || ordinary.state !== "applied" || ordinary.invalid_field !== null)) throw new Error("invalid_state");
    subscriptionGate = {supported, enabled, can_run: canRun, reason};
  }
  const sources = input.sources.map((source) => {
    if (!object(source) || !Object.hasOwn(SOURCE_LABELS, source.id)
        || !nullableBool(source.declared) || source.freshness !== "unknown"
        || source.last_success_at !== null) throw new Error("invalid_state");
    return {id: source.id, declared: source.declared, freshness: "unknown", last_success_at: null};
  });
  if (new Set(sources.map((source) => source.id)).size !== sources.length) throw new Error("invalid_state");
  return {
    schema_version: input.schema_version, runtime: {state: input.runtime.state, reason: input.runtime.reason},
    module: {registered: input.module.registered, enabled: input.module.enabled},
    ordinary_config: ordinary,
    credentials: {cn: {configured: null, state: "unknown"}, global: {configured: null, state: "unknown"}},
    subscription_gate: subscriptionGate, sources,
  };
}

export async function copyCommand(textarea, clipboard) {
  try {
    if (!clipboard || typeof clipboard.writeText !== "function") throw new Error("clipboard_unavailable");
    await clipboard.writeText(textarea.value);
    return "已复制聊天命令。";
  } catch {
    textarea.focus();
    textarea.select();
    return MESSAGES.copyFailed;
  }
}

export function validateCatalog(input) {
  const fail = () => { throw new Error("invalid_catalog"); };
  if (!object(input) || input.schema_version !== 1 || !object(input.runtime)
      || !["ready", "not_ready", "invalid_config", "closing", "closed"].includes(input.runtime.state)
      || new TextEncoder().encode(JSON.stringify(input)).length > 262144) fail();
  if (input.modules === null) {
    if (input.catalog_revision !== null || !["not_ready", "invalid_config"].includes(input.runtime.state)) fail();
    return {schema_version: 1, catalog_revision: null, runtime: {state: input.runtime.state}, modules: null};
  }
  if (!Number.isSafeInteger(input.catalog_revision) || input.catalog_revision < 0
      || !Array.isArray(input.modules) || input.modules.length > 64) fail();
  const bounded = (v, n = 4096) => typeof v === "string" && v.length > 0 && v.length <= n;
  const identifier = (v) => bounded(v) && /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(v);
  const moduleIdentifier = (v) => bounded(v, 8193) && v.split("/").length === 2 && v.split("/").every(identifier);
  const reasons = ["module_disabled", "runtime_not_ready", "cleanup_pending", "module_not_active", "identity_mismatch"];
  const modules = input.modules.map((v) => {
    if (!object(v) || !moduleIdentifier(v.module_id) || !identifier(v.route)
        || !["game", "platform"].includes(v.category) || !bounded(v.version)
        || typeof v.enabled !== "boolean" || !["discovered", "starting", "active", "stopping", "stopped", "failed"].includes(v.lifecycle)
        || !["loaded", "disabled", "unavailable"].includes(v.state)
        || !(v.reason === null || reasons.includes(v.reason))
        || !Array.isArray(v.capabilities) || v.capabilities.length > 128
        || !Array.isArray(v.config_fields) || v.config_fields.length > 128) fail();
    if (v.state === "loaded" && (!v.enabled || v.lifecycle !== "active" || input.runtime.state !== "ready" || v.reason !== null)) fail();
    if (v.state === "disabled" && (v.enabled !== false || v.reason !== "module_disabled")) fail();
    if (v.state === "unavailable" && (!v.enabled || v.reason === null || v.reason === "module_disabled")) fail();
    const capabilities = v.capabilities.map((c) => {
      if (!object(c) || !identifier(c.capability_id)
          || !["command_only", "command_and_public_web", "natural_language_allowed"].includes(c.invocation_policy)
          || typeof c.web_declared !== "boolean"
          || c.web_declared !== (c.invocation_policy === "command_and_public_web")) fail();
      return {capability_id: c.capability_id, invocation_policy: c.invocation_policy, web_declared: c.web_declared};
    });
    const config_fields = v.config_fields.map((f) => {
      if (!object(f) || f.sensitive === true || !bounded(f.name, 128) || /\s|[\u0000-\u001f]/.test(f.name) || typeof f.required !== "boolean") fail();
      return {name: f.name, required: f.required};
    });
    if (new Set(capabilities.map((v) => v.capability_id)).size !== capabilities.length
        || new Set(config_fields.map((v) => v.name)).size !== config_fields.length) fail();
    return {module_id: v.module_id, route: v.route, category: v.category, version: v.version,
      enabled: v.enabled, lifecycle: v.lifecycle, state: v.state, reason: v.reason, capabilities, config_fields};
  });
  if (new Set(modules.map((v) => v.module_id)).size !== modules.length) fail();
  return {schema_version: 1, catalog_revision: input.catalog_revision, runtime: {state: input.runtime.state}, modules};
}

const RENDERERS = Object.freeze({"ff14/ff14": {label: "FF14", routes: ROUTES}});
const GENERIC_ROUTES = Object.freeze({overview: "概览", settings: "普通配置"});
function selectionFromHash(hash) {
  if (!hash || hash === "#" || hash === "#/") return {moduleId: null, route: "overview", legacy: false, invalid: false};
  const match = /^#\/module\/([^/]+)\/([^/]+)$/.exec(hash);
  if (match) {
    try {
      const moduleId = decodeURIComponent(match[1]);
      if (moduleId.length > 8193 || moduleId.split("/").length !== 2) throw new Error();
      return {moduleId, route: match[2], legacy: false, invalid: false};
    } catch { return {moduleId: null, route: null, invalid: true}; }
  }
  const route = routeFromHash(hash);
  return {moduleId: null, route, legacy: route !== null, invalid: route === null};
}

export function createPageApp({document, window, bridge, timeoutMs = 8000, queryTimeoutMs = 35000,
  setTimer = setTimeout, clearTimer = clearTimeout}) {
  const root = document.getElementById("page-root");
  const selector = document.getElementById("page-select");
  const moduleSelectors = [document.getElementById("module-select"), document.getElementById("module-select-mobile")].filter(Boolean);
  const media = window.matchMedia?.("(prefers-color-scheme: dark)");
  let selection = selectionFromHash(window.location.hash), moduleId = selection.moduleId, route = selection.route;
  let catalog = null, data = null, disposed = false, started = false;
  let phase = "unchecked", message = MESSAGES.unchecked, stale = false;
  let bridgeReady = false, hostContext = false, sequence = 0, connection = 0, renderEpoch = 0;
  let requestTimer = null, readyTimer = null, unbindContext = null;
  let renderedKey = null, content = null, refreshButton = null;
  let webStatus = null, querySequence = 0, queryTimer = null, queryDom = null;
  let queryView = {phase: "idle", result: null, message: "", errors: {}, submitted: null};
  const drafts = {items: {query: ""}, logs: {region: "", server: "", character: ""}, calendar: {region: "", days: "", timezone: ""}};
  const editedFields = {items: new Set(), logs: new Set(), calendar: new Set()};
  const selectedModule = () => catalog?.modules?.find((v) => v.module_id === moduleId) || null;
  const routesFor = () => selectedModule() ? RENDERERS[moduleId]?.routes || GENERIC_ROUTES : {};
  const moduleLabel = (id) => RENDERERS[id]?.label || id;
  function element(tag, text = "", attributes = {}) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    return node;
  }
  function card(title, children) {
    const node = element("section", "", {class: "card"});
    node.append(element("h2", title), ...children);
    return node;
  }
  function status(label, tone = "unknown") {
    return element("p", label, {class: `status-label status-${tone}`});
  }
  function nativeSteps() {
    const steps = element("ol");
    for (const text of ["返回 AstrBot 已安装插件列表。", "找到“如月怜的游戏连结”，点击插件卡片的配置齿轮。", "编辑前刷新表单；修改普通设置并保存，AstrBot 会重载插件。"])
      steps.append(element("li", text));
    return steps;
  }
  function command(text, label = "聊天命令") {
    const box = element("div", "", {class: "command"});
    const textarea = element("textarea", "", {readonly: "", rows: "2", "aria-label": label, spellcheck: "false"});
    textarea.value = text;
    const copy = element("button", "复制命令", {type: "button"});
    const notice = element("p", "", {role: "status", class: "hint"});
    const epoch = renderEpoch;
    copy.addEventListener("click", async () => {
      copy.disabled = true;
      // Use the Clipboard API when available. A rejected write falls back to
      // selecting this textarea; never touch the parent document.
      const activeClipboard = {writeText: async (value) => {
        if (!window.navigator?.clipboard?.writeText) throw new Error("unavailable");
        await window.navigator.clipboard.writeText(value);
      }};
      const guardedTextarea = {
        value: textarea.value,
        focus: () => { if (!disposed && epoch === renderEpoch) textarea.focus(); },
        select: () => { if (!disposed && epoch === renderEpoch) textarea.select(); },
      };
      const result = await copyCommand(guardedTextarea, activeClipboard);
      if (!disposed && epoch === renderEpoch) {
        notice.textContent = result;
        copy.disabled = false;
      }
    });
    box.append(element("p", label, {class: "hint"}), textarea, copy, notice);
    return box;
  }
  function unknownCredentials() {
    return card("FFLogs 凭据", [status("国服：状态未知 · 国际服：状态未知"),
      element("p", "当前页面无法读取凭据配置状态。"),
      element("p", "凭据管理入口尚未接通；请勿在普通配置表单中填写 Client ID 或 Client Secret。", {class: "hint"})]);
  }
  function gate() {
    const gateState = data?.subscription_gate;
    const reason = gateState?.reason || (gateState?.can_run === true ? null : "state_unknown");
    const errors = {
      initialization_invalid: "订阅准入初始化状态异常。",
      config_missing: "订阅开关配置缺失。",
      config_invalid: "订阅开关配置无效。",
      fence_invalid: "订阅准入状态异常。",
    };
    const label = reason === "unsupported" ? "开关状态未知"
      : reason === "gate_disabled" ? "订阅已暂停"
      : reason === "module_disabled" ? "订阅开关已开启 · 模块已禁用"
      : reason === "runtime_not_ready" ? "订阅开关已开启 · 运行尚未就绪"
      : reason === null ? "订阅可运行" : "订阅状态未知";
    const tone = reason === null ? "success"
      : ["gate_disabled", "module_disabled", "runtime_not_ready"].includes(reason) ? "warning" : "unknown";
    const children = [status(errors[reason] ? "订阅状态异常" : label, tone)];
    if (errors[reason]) children.push(element("p", `${errors[reason]}订阅暂不可运行，请联系管理员检查订阅开关及准入状态。`, {class: "notice error", role: "alert"}));
    else if (reason === "unsupported") children.push(element("p", "当前页面无法确认开关状态。", {class: "notice", role: "status"}));
    else if (reason === "state_unknown") children.push(element("p", "当前无法确认订阅状态，请读取或重试读取。", {class: "notice", role: "status"}));
    children.push(element("p", "本页仅显示状态，不提供模块启停或订阅控制操作。", {class: "hint"}),
      element("p", "暂停时订阅记录保留；模块正常运行时，本人仍可在本人私聊中查看、修改或取消订阅。", {class: "hint"}),
      element("p", "恢复后仅处理后续未来窗口，不补发暂停窗口；暂停前已经开始发送的通知可能完成。", {class: "hint"}),
      element("p", "准入成立仅表示可以运行，不保证来源可用或发送成功。", {class: "hint"}));
    return card("订阅推送状态", children);
  }
  function sourceCard() {
    const list = element("ul", "", {class: "source-list"});
    const sources = data?.sources || Object.keys(SOURCE_LABELS).map((id) => ({id, declared: null}));
    for (const source of sources) {
      const declared = source.declared === true ? "已声明" : source.declared === false ? "未声明" : "声明状态未知";
      const row = element("li");
      row.append(element("strong", SOURCE_LABELS[source.id]),
        element("span", `${declared}；数据新鲜度未知；最近成功时间未知。`, {class: "hint"}));
      list.append(row);
    }
    return card("来源", [element("p", "这里只显示来源声明；刷新状态不会检查外网，声明不表示可连接或数据有效。", {class: "hint"}), list]);
  }
  function ordinaryCard(settings) {
    const ordinary = data?.ordinary_config;
    const children = [status(CONFIG_LABELS[ordinary?.state || "unknown"], ordinary?.state === "invalid" ? "warning" : "unknown")];
    if (ordinary?.state === "invalid") children.push(element("p", "插件因普通配置无效而暂停。请按字段说明修正并保存重载；已有订阅记录保留。"));
    if (settings) {
      const definitions = element("dl");
      for (const [key, label] of Object.entries(FIELD_LABELS)) {
        const value = ordinary?.values?.[key];
        const text = value === undefined || value === null ? "未取得有效配置值"
          : key === "ff14_default_region" ? (value === "cn" ? "国服（cn）" : "国际服（global）") : String(value);
        definitions.append(element("dt", label), element("dd", text));
      }
      children.push(definitions, element("p", "这些值只读，来自本次插件运行快照。显式参数优先；修改默认值不会改写已有订阅。", {class: "hint"}));
    }
    if (settings) children.push(nativeSteps());
    return card("普通配置", children);
  }
  function navigateTo(id, page = "overview") {
    window.location.hash = id ? `#/module/${encodeURIComponent(id)}/${page}` : "#/";
    navigate();
  }
  function summary(title, label, tone = "unknown") {
    const node = card(title, [status(label, tone)]);
    node.setAttribute("class", "card summary-card");
    return node;
  }
  function syncControls() {
    const available = routesFor();
    for (const select of moduleSelectors) {
      const options = [element("option", "选择模块", {value: ""})];
      for (const module of catalog?.modules || []) options.push(element("option", moduleLabel(module.module_id), {value: module.module_id}));
      select.replaceChildren(...options);
      select.value = moduleId || "";
      select.disabled = !catalog?.modules?.length;
    }
    selector.replaceChildren(...Object.entries(available).map(([key, label]) => element("option", label, {value: key})));
    selector.value = route || "";
    selector.disabled = !selectedModule();
    for (const link of document.querySelectorAll("[data-route]")) {
      const key = link.getAttribute("data-route");
      if (Object.hasOwn(available, key)) {
        link.removeAttribute("hidden");
        link.setAttribute("href", `#/module/${encodeURIComponent(moduleId)}/${key}`);
      } else { link.setAttribute("hidden", ""); link.removeAttribute("href"); }
      if (key === route && Object.hasOwn(available, key)) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  }
  function genericDetails(module, settings) {
    const fields = element("ul", "", {class: "source-list"});
    for (const field of module.config_fields) {
      const row = element("li");
      row.append(element("strong", field.name), element("span", field.required ? "必填声明" : "选填声明", {class: "hint"}));
      fields.append(row);
    }
    const fieldCard = card("普通配置声明", [element("p", "这里只展示普通字段名称与必填声明。值读取和修改入口尚未接通。", {class: "hint"}),
      module.config_fields.length ? fields : element("p", "该模块未声明普通配置字段。")]);
    if (settings) return [fieldCard];
    const capabilities = element("ul", "", {class: "source-list"});
    for (const capability of module.capabilities) {
      const row = element("li");
      row.append(element("strong", capability.capability_id),
        element("span", capability.web_declared ? "已声明公开网页政策；网页执行入口尚未接通。" : "公开只读能力声明；网页执行入口尚未接通。", {class: "hint"}));
      capabilities.append(row);
    }
    return [card("模块概览", [status(module.state === "loaded" ? "模块已加载" : module.state === "disabled" ? "模块已禁用" : "模块当前不可用", module.state === "loaded" ? "success" : "warning"),
      element("p", `模块：${module.module_id} · 版本：${module.version}`, {class: "hint"}),
      element("p", `分类：${module.category} · 生命周期：${module.lifecycle}`, {class: "hint"})]),
      card("公开只读能力声明", [module.capabilities.length ? capabilities : element("p", "没有公开只读能力声明。"),
        element("p", "能力声明不代表当前可查询或具有管理权限。", {class: "hint"})]), fieldCard];
  }
  function ff14Overview(module) {
    const grid = element("div", "", {class: "status-grid"});
    const run = catalog.runtime.state === "ready" ? "已启动" : "尚未就绪";
    const gateCard = gate();
    grid.append(summary("运行", run, catalog.runtime.state === "ready" ? "success" : "unknown"),
      summary("模块 · FF14", module.state === "loaded" ? "已注册 · 已加载" : module.state === "disabled" ? "已注册 · 已禁用" : "已注册 · 当前不可用", module.state === "loaded" ? "success" : "warning"),
      summary("普通配置", CONFIG_LABELS[data?.ordinary_config.state || "unknown"]),
      summary("订阅推送", gateCard.children[1].textContent, data?.subscription_gate.can_run === true ? "success" : "unknown"));
    const taskGrid = element("div", "", {class: "task-grid"});
    for (const [page, subtitle] of [["logs", "查看公开角色战斗记录"], ["items", "查找物品与详情"], ["calendar", "查看活动窗口与时区"]]) {
      const task = element("section", "", {class: "card task-card"});
      const action = element("button", "打开查询页面 →", {type: "button", class: "text-action"});
      action.addEventListener("click", () => navigateTo(moduleId, page));
      task.append(element("h3", ROUTES[page]), element("p", subtitle, {class: "hint"}),
        element("p", "手动网页查询 · 进入页面后检查入口", {class: "task-badge"}), action);
      taskGrid.append(task);
    }
    const credentials = element("section", "", {class: "card credential-strip"});
    credentials.append(element("strong", "FFLogs 凭据"), element("span", "国服 / 国际服：状态未知 · 管理入口尚未接通", {class: "hint"}),
      element("p", "当前页面无法读取凭据配置状态。请勿在普通配置表单中填写 Client ID 或 Client Secret。", {class: "hint"}));
    const config = element("button", "普通配置 · 查看只读配置与修改说明 →", {type: "button", class: "card config-entry"});
    config.addEventListener("click", () => navigateTo(moduleId, "settings"));
    const diagnostics = element("details", "", {class: "card diagnostics"});
    diagnostics.append(element("summary", "来源与订阅诊断 · 仅展示声明，不检查外网连通性"), sourceCard(), gateCard);
    return [grid, element("h2", "模块任务"), taskGrid, credentials, config, diagnostics];
  }
  function queryPage() {
    if (!queryDom || queryDom.route !== route) {
      const form = element("form", "", {class: "query-form", novalidate: ""});
      const fields = element("div", "", {class: "form-fields"}), inputs = {}, errors = {};
      const specs = route === "items" ? [["query", "名称或 ID", "text", "例如 44091", 120]]
        : route === "logs" ? [["region", "区域", "select"], ["server", "服务器", "text", "请输入服务器", 100], ["character", "角色名", "text", "请输入角色名", 120]]
        : [["region", "区域", "select"], ["days", "天数（1–30）", "number", "请先读取默认配置"], ["timezone", "IANA 时区", "text", "例如 Asia/Shanghai", 128]];
      for (const [key, label, type, placeholder, max] of specs) {
        const id = `${route}-${key}`, field = element("div", "", {class: "field"});
        const input = element(type === "select" ? "select" : "input", "", {id, name: key, "aria-describedby": `${id}-error`});
        if (type === "select") input.append(element("option", "请选择区域", {value: ""}), element("option", "国服", {value: "cn"}), element("option", "国际服", {value: "global"}));
        else { input.setAttribute("type", type); input.setAttribute("placeholder", placeholder); if (max) input.setAttribute("maxlength", max); }
        if (type === "number") for (const [key, value] of Object.entries({min: 1, max: 30, step: 1, inputmode: "numeric"})) input.setAttribute(key, value);
        input.value = drafts[route][key];
        const error = element("p", "", {id: `${id}-error`, class: "field-error"});
        const edit = () => {
          if (disposed || queryDom?.inputs[key] !== input) return;
          drafts[route][key] = input.value;
          editedFields[route].add(key);
          invalidateQuery(); queryView.message = "输入已修改，旧结果已清除。请手动查询。"; updateQueryUi();
        };
        input.addEventListener("input", edit);
        if (type === "select") input.addEventListener("change", edit);
        inputs[key] = input; errors[key] = error;
        field.append(element("label", label, {for: id}), input, error); fields.append(field);
      }
      const submit = element("button", route === "items" ? "查询物品" : route === "logs" ? "查询 Logs" : "查询日历", {type: "submit", class: "query-submit"});
      form.addEventListener("submit", (event) => { event.preventDefault(); if (queryDom?.form === form) submitQuery(); });
      form.append(fields);
      if (route === "logs") form.append(element("p", "指标：rDPS（固定）", {class: "hint"}));
      form.append(submit, element("p", "仅手动查询；进入、刷新和恢复连接不会自动查询外网。", {class: "hint"}));
      const entry = element("div", "", {class: "query-entry", role: "status"});
      const result = element("section", "", {class: "card query-result", "aria-label": "本次结果"});
      queryDom = {route, form, entry, result, submit, inputs, errors, card: card(ROUTES[route], [form, entry])};
    }
    const cli = route === "logs" ? '/ygl ff14 logs cn "潮风亭" "如月怜"'
      : route === "items" ? "/ygl ff14 item 44091" : "/ygl ff14 calendar cn days=7 timezone=Asia/Shanghai";
    updateQueryUi();
    const alternative = element("details", "", {class: "card chat-alternative"});
    alternative.append(element("summary", "聊天替代 · 展开查看"), command(cli));
    const result = [queryDom.card, queryDom.result, alternative];
    if (route === "calendar") result.push(card("本人订阅", [element("p", "Dashboard 身份不能证明订阅 owner。本页不显示本人订阅，请在本人私聊中管理。"),
      command("/ygl ff14 calendar subscriptions", "本人私聊命令"), element("p", "创建、修改和取消请先在本人私聊运行 /ygl ff14 help；修改与取消需填写 expected_revision。", {class: "hint"})]));
    return result;
  }
  function invalidateQuery() {
    querySequence += 1;
    if (queryTimer !== null) clearTimer(queryTimer);
    queryTimer = null;
    queryView = {phase: "idle", result: null, message: "", errors: {}, submitted: null};
  }
  function queryAvailability() {
    if (webStatus && webStatus.configuration !== "configured") return "网页地址未配置或无效。请从插件配置齿轮填写网页访问地址，保存重载后重新打开本页；聊天查询仍可使用。";
    if (webStatus && webStatus.origin !== window.location.origin) return "当前网页地址与插件配置不匹配。请使用已配置地址打开 Dashboard，或从插件配置齿轮修正地址并保存重载。";
    if (!bridgeReady || phase !== "ready" || !webStatus || !data?.ordinary_config?.values) return "入口状态或默认配置尚未就绪，请刷新状态；仍失败时请重新登录 Dashboard 或重新打开本页。";
    if (moduleId !== "ff14/ff14" || selectedModule()?.state !== "loaded" || catalog?.runtime.state !== "ready" || data.runtime.state !== "ready"
        || data.module.registered !== true || data.module.enabled !== true || data.ordinary_config.state !== "applied" || !webStatus.entry_ready) return "FF14 查询入口尚未就绪或模块已禁用。请刷新状态、返回模块选择，或联系管理员检查插件。";
    if (typeof bridge.apiPost !== "function") return "宿主查询连接尚未就绪，请重新打开插件页面；也可使用聊天命令。";
    return null;
  }
  function resultValue(value) {
    if (value === null) return "—";
    if (object(value) && Object.hasOwn(value, "value")) {
      return [String(value.value), value.currency || value.unit || value.timezone].filter(Boolean).join(" ");
    }
    return object(value) || Array.isArray(value) ? JSON.stringify(value) : String(value);
  }
  function renderBlocks(result) {
    const nodes = [];
    if (!result.document) return nodes;
    const doc = result.document;
    nodes.push(element("h3", doc.title), element("p", doc.subject, {class: "hint"}));
    for (const block of doc.blocks) {
      if (block.kind === "text") nodes.push(element("p", block.text, {class: "result-text"}));
      else if (["fields", "metrics"].includes(block.kind)) {
        const fields = element("dl", "", {class: "result-fields"});
        for (const [name, value] of Object.entries(block[block.kind])) fields.append(element("dt", name), element("dd", resultValue(value)));
        nodes.push(fields);
      } else if (block.kind === "table") {
        const table = element("table"), head = element("thead"), header = element("tr"), body = element("tbody");
        for (const name of block.columns) header.append(element("th", name, {scope: "col"}));
        head.append(header);
        const mobile = element("div", "", {class: "result-table-mobile"});
        for (const values of block.rows) {
          const row = element("tr"), fields = element("dl", "", {class: "result-table-row"});
          values.forEach((value, index) => { row.append(element("td", resultValue(value))); fields.append(element("dt", block.columns[index]), element("dd", resultValue(value))); });
          body.append(row); mobile.append(fields);
        }
        table.append(head, body); const wrapper = element("div", "", {class: "result-table-desktop", tabindex: "0", "aria-label": "结果表格"}); wrapper.append(table);
        nodes.push(wrapper, mobile);
        if (!block.rows.length) nodes.push(element("p", route === "calendar" ? "返回内容为空，无法确认该窗口无活动。" : "来源返回的表格内容为空。", {class: "notice warning", role: "status"}));
      } else {
        const links = element("ul", "", {class: "result-links"});
        for (const link of block.links) { const row = element("li"); row.append(element("a", link.label, {href: link.url, target: "_blank", rel: "noopener noreferrer"})); links.append(row); }
        nodes.push(links);
      }
    }
    if (!doc.blocks.length) nodes.push(element("p", route === "calendar" ? "返回内容为空，无法确认该窗口无活动。" : "来源未返回可展示内容。", {class: "notice warning", role: "status"}));
    for (const warning of result.warnings) nodes.push(element("p", warning, {class: "notice warning"}));
    const sources = [...doc.sources, ...result.provenance];
    if (sources.length) nodes.push(element("p", `来源 / 口径：${sources.join("；")}`, {class: "hint"}));
    const timestamps = [...doc.timestamps, ...result.timestamps];
    if (timestamps.length) nodes.push(element("p", `返回时间：${timestamps.map((v) => resultValue(v)).join("；")}`, {class: "hint"}));
    return nodes;
  }
  function queryError(code) {
    const messages = {
      parameter_error: "参数未被来源接受，请补全或修正输入后手动重试。",
      auth_required: "来源凭据不可用，请联系管理员配置相应区域的来源凭据，再手动重试。",
      auth_expired: "授权已过期，请重新登录 Dashboard；来源凭据问题请联系管理员。",
      rate_limited: "查询受到限流，请稍后手动重试；当前未提供可用的重试时间。",
      module_unavailable: "模块或查询期限不可用，请刷新状态、检查模块后手动重试。",
      upstream_error: "来源暂时不可用，请稍后手动重试。",
      not_found: "来源未找到匹配内容，请检查名称或 ID 后手动重试。",
      no_records: "来源未返回记录；这不表示查询窗口已被完整覆盖。",
      not_public: "来源内容未公开，请检查公开状态或改用聊天命令。",
    };
    return messages[code] || "查询未完成，请检查输入与来源状态后手动重试，或使用聊天命令。";
  }
  function updateQueryUi() {
    if (!queryDom || queryDom.route !== route) return;
    const unavailable = queryAvailability(), pending = queryView.phase === "loading";
    queryDom.submit.disabled = unavailable !== null || pending;
    queryDom.submit.setAttribute("aria-disabled", String(queryDom.submit.disabled));
    queryDom.form.setAttribute("aria-busy", String(pending));
    for (const [key, input] of Object.entries(queryDom.inputs)) {
      if (input.value !== drafts[route][key]) input.value = drafts[route][key];
      input.setAttribute("aria-invalid", String(Boolean(queryView.errors[key])));
      queryDom.errors[key].textContent = queryView.errors[key] || "";
    }
    queryDom.entry.textContent = unavailable || "入口已就绪；仅在提交表单时查询公开来源。";
    queryDom.entry.setAttribute("class", `query-entry notice ${unavailable ? "warning" : ""}`);
    const nodes = [element("h2", "本次结果")];
    if (pending) {
      const stop = element("button", "停止展示", {type: "button"});
      stop.addEventListener("click", () => { const active = document.activeElement === stop; invalidateQuery(); queryView.message = QUERY_MESSAGES.stopped; updateQueryUi(); if (active) queryDom.submit.focus(); });
      nodes.push(element("p", "正在查询…", {role: "status"}), stop,
        element("p", "仅停止展示，本次请求可能仍在后台处理。", {class: "hint"}));
    } else if (queryView.result) {
      const result = queryView.result;
      const label = result.status === "success" ? "查询完成" : result.status === "partial_success" ? "部分结果 · 保留来源说明" : result.status === "needs_selection" ? "需要更精确输入 · 请补充名称或 ID，或使用聊天命令" : queryError(result.error.code);
      nodes.push(element("p", label, {class: `notice ${result.status === "error" ? "error" : result.status === "success" ? "" : "warning"}`, role: result.status === "error" ? "alert" : "status"}), ...renderBlocks(result));
      if (route === "calendar" && queryView.submitted) nodes.push(element("p", `用户输入范围：${queryView.submitted.region === "cn" ? "国服" : "国际服"} / ${queryView.submitted.days}天 / ${queryView.submitted.timezone}。这不是来源完整覆盖声明。`, {class: "hint"}));
    } else nodes.push(element("p", queryView.message || "尚未提交查询。", {role: queryView.phase === "error" || queryView.phase === "timeout" ? "alert" : "status", class: queryView.phase === "error" || queryView.phase === "timeout" ? "notice error" : "hint"}));
    queryDom.result.replaceChildren(...nodes);
  }
  function submitQuery() {
    if (queryAvailability() || queryView.phase === "loading" || !Object.hasOwn(QUERY_ENDPOINTS, route)) return;
    const {body, errors} = queryInput(route, drafts[route]);
    invalidateQuery();
    if (Object.keys(errors).length) {
      queryView.errors = errors; queryView.message = "请先修正表单中标出的输入。"; updateQueryUi();
      queryDom.inputs[Object.keys(errors)[0]].focus(); return;
    }
    const request = querySequence, selectedRoute = route, selectedId = moduleId, contextGeneration = connection;
    queryView.phase = "loading"; queryView.submitted = body; updateQueryUi();
    const current = () => !disposed && request === querySequence && selectedRoute === route && selectedId === moduleId && contextGeneration === connection;
    queryTimer = setTimer(() => { if (current()) { invalidateQuery(); queryView.phase = "timeout"; queryView.message = QUERY_MESSAGES.timeout; updateQueryUi(); } }, queryTimeoutMs);
    // Actual bridge has no AbortSignal or timeout option; this deadline only
    // stops this view from accepting late results. Successful apiPost is the DTO.
    Promise.resolve().then(() => {
      if (!current() || queryAvailability()) return null;
      return bridge.apiPost(QUERY_ENDPOINTS[selectedRoute], body);
    }).then((result) => {
      if (!current()) return;
      if (queryAvailability()) throw new Error("entry_changed");
      const clean = validateQueryResult(result);
      clearTimer(queryTimer); queryTimer = null;
      queryView.phase = "ready"; queryView.result = clean; updateQueryUi();
    }).catch(() => {
      if (!current()) return;
      clearTimer(queryTimer); queryTimer = null;
      queryView.phase = "error"; queryView.result = null; queryView.message = QUERY_MESSAGES.failed; updateQueryUi();
    });
  }
  function render() {
    if (disposed) return;
    const focus = queryDom && [...Object.values(queryDom.inputs), queryDom.submit].includes(document.activeElement) ? document.activeElement : null;
    renderEpoch += 1;
    syncControls();
    const key = `${moduleId || "plugin"}/${route || "invalid"}`;
    if (key !== renderedKey) {
      const heading = element("div", "", {class: "page-heading"});
      const titles = element("div");
      const title = moduleId ? `${moduleLabel(moduleId)} · ${routesFor()[route] || "模块页面"}` : "游戏连结";
      titles.append(element("h1", title, {id: "page-title"}), element("p", "查看运行状态与模块声明", {class: "hint"}));
      refreshButton = element("button", "刷新状态", {type: "button"});
      refreshButton.addEventListener("click", () => { if (phase !== "loading") bridgeReady ? refresh() : connect(); });
      heading.append(titles, refreshButton);
      content = element("div", "", {class: "page-sections"}); root.replaceChildren(heading, content); renderedKey = key;
    }
    refreshButton.textContent = ["error", "timeout"].includes(phase) ? "重试读取" : "刷新状态";
    refreshButton.setAttribute("aria-disabled", phase === "loading" ? "true" : "false");
    const children = [];
    if (phase !== "ready") children.push(element("p", message, {class: `notice ${["error", "timeout"].includes(phase) ? "error" : ""}`, role: ["error", "timeout"].includes(phase) ? "alert" : "status"}));
    const module = selectedModule();
    if (!catalog) {
      // A failed read clears old success; a timeout is not an empty directory.
    } else if (catalog.modules === null) {
      children.push(element("p", catalog.runtime.state === "invalid_config" ? "普通配置无效，模块目录尚不可读取。请从插件配置齿轮修正并保存重载。" : "运行尚未就绪，模块目录状态未知。请重试读取。", {class: "notice", role: "status"}));
    } else if (selection.invalid || (module && !Object.hasOwn(routesFor(), route))) {
      const back = element("button", "返回模块选择", {type: "button"}); back.addEventListener("click", () => navigateTo(null));
      children.push(element("p", "页面不存在，请返回模块选择。", {class: "notice error", role: "alert"}), back);
    } else if (moduleId && !module) {
      const back = element("button", "返回模块选择", {type: "button"}); back.addEventListener("click", () => navigateTo(null));
      children.push(element("p", "所选模块已退出目录，请重新选择模块或刷新目录。", {class: "notice warning", role: "status"}), back);
    } else if (!catalog.modules.length) {
      children.push(element("p", catalog.runtime.state === "ready" ? "暂无已注册模块。" : "运行尚未就绪，当前目录没有已注册模块。", {class: "notice", role: "status"}));
    } else if (!module) {
      if (selection.legacy) children.push(element("p", "原页面对应模块当前不在目录，请选择模块。", {class: "notice warning", role: "status"}));
      children.push(element("p", "选择模块以查看状态、能力与普通配置声明。", {class: "hint"}));
      for (const item of catalog.modules) {
        const button = element("button", `${moduleLabel(item.module_id)} · ${item.state === "loaded" ? "已加载" : item.state === "disabled" ? "已禁用" : "不可用"} →`, {type: "button", class: "card module-entry"});
        button.addEventListener("click", () => navigateTo(item.module_id)); children.push(button);
      }
    } else {
      if (module.state !== "loaded") children.push(element("p", module.state === "disabled" ? "模块已禁用；声明可查看，任务不可用。" : "模块当前不可用；声明可查看，任务不可用。", {class: "notice warning", role: "status"}));
      if (!RENDERERS[moduleId]) children.push(...genericDetails(module, route === "settings"));
      else if (route === "overview") children.push(...ff14Overview(module));
      else if (route === "settings") children.push(ordinaryCard(true), unknownCredentials(), gate(), ...genericDetails(module, true));
      else children.push(...queryPage());
    }
    content.replaceChildren(...children);
    if (focus && queryDom && [...Object.values(queryDom.inputs), queryDom.submit].includes(focus)) focus.focus();
    root.setAttribute("aria-busy", phase === "loading" ? "true" : "false");
  }
  function invalidate() {
    sequence += 1;
    invalidateQuery(); webStatus = null;
    if (requestTimer !== null) clearTimer(requestTimer);
    requestTimer = null;
  }
  function read(endpoint, validate, accept) {
    const request = sequence, requestedModule = moduleId, requestedRoute = route;
    requestTimer = setTimer(() => {
      if (disposed || request !== sequence) return;
      invalidate(); phase = "timeout"; data = null; message = MESSAGES.timeout; render();
    }, timeoutMs);
    Promise.resolve().then(() => Array.isArray(endpoint) ? Promise.all(endpoint.map((path) => bridge.apiGet(path, {}))) : bridge.apiGet(endpoint, {})).then((result) => {
      if (disposed || request !== sequence || requestedModule !== moduleId || requestedRoute !== route) return;
      const clean = validate(result);
      clearTimer(requestTimer); requestTimer = null;
      accept(clean);
    }).catch(() => {
      if (disposed || request !== sequence || requestedModule !== moduleId || requestedRoute !== route) return;
      clearTimer(requestTimer); requestTimer = null;
      if (endpoint === "catalog") catalog = null;
      data = null; phase = "error"; message = MESSAGES.error; render();
    });
  }
  function readSelected() {
    data = null; webStatus = null;
    if (selectedModule() && RENDERERS[moduleId] && ["overview", "settings"].includes(route)) {
      phase = "loading"; message = MESSAGES.loading; render();
      read(route, (value) => validateState(value, route), (clean) => { data = clean; phase = "ready"; render(); });
    } else if (selectedModule() && moduleId === "ff14/ff14" && Object.hasOwn(QUERY_ENDPOINTS, route)) {
      phase = "loading"; message = MESSAGES.loading; render();
      read(["settings", "web-status"], (values) => ({settings: validateState(values[0], "settings"), web: validateWebStatus(values[1])}), (clean) => {
        data = clean.settings; webStatus = clean.web; phase = "ready";
        const values = data.ordinary_config.values;
        if (values) {
          for (const page of ["logs", "calendar"]) if (!editedFields[page].has("region")) drafts[page].region = values.ff14_default_region;
          if (!editedFields.calendar.has("days")) drafts.calendar.days = String(values.ff14_calendar_default_days);
          if (!editedFields.calendar.has("timezone")) drafts.calendar.timezone = values.ff14_calendar_default_timezone;
        }
        render();
      });
    } else { phase = "ready"; render(); }
  }
  function resolveSelection() {
    if (selection.legacy && catalog?.modules?.some((v) => v.module_id === "ff14/ff14")) moduleId = "ff14/ff14";
    else if (!window.location.hash && catalog?.modules?.length === 1) moduleId = catalog.modules[0].module_id;
  }
  function refresh() {
    if (disposed || !bridgeReady || phase === "loading") return;
    invalidate(); catalog = null; data = null; stale = false;
    phase = "loading"; message = "正在读取模块目录，请稍候。"; render();
    read("catalog", validateCatalog, (clean) => { catalog = clean; resolveSelection(); readSelected(); });
  }
  function applyTheme(context) {
    if (object(context)) {
      hostContext = true;
      const theme = typeof context.isDark === "boolean" ? (context.isDark ? "dark" : "light")
        : ["dark", "light"].includes(context.theme) ? context.theme
        : document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", theme);
    } else if (!hostContext) document.documentElement.setAttribute("data-theme", media?.matches ? "dark" : "light");
  }
  function connect() {
    if (disposed) return;
    connection += 1;
    const attempt = connection;
    if (readyTimer !== null) clearTimer(readyTimer);
    unbindContext?.(); unbindContext = null;
    phase = "loading"; message = MESSAGES.loading; render();
    const fail = () => {
      if (disposed || attempt !== connection || bridgeReady) return;
      connection += 1;
      if (readyTimer !== null) clearTimer(readyTimer);
      readyTimer = null; unbindContext?.(); unbindContext = null;
      phase = "error"; message = MESSAGES.unavailable; render();
    };
    if (!bridge || typeof bridge.ready !== "function" || typeof bridge.onContext !== "function" || typeof bridge.apiGet !== "function") { fail(); return; }
    const accept = (context, fromEvent = false) => {
      if (disposed || attempt !== connection) return;
      applyTheme(context);
      if (bridgeReady) {
        if (fromEvent) { invalidate(); catalog = null; data = null; phase = "unchecked"; refresh(); }
        return;
      }
      bridgeReady = true; clearTimer(readyTimer); readyTimer = null; phase = "unchecked"; refresh();
    };
    readyTimer = setTimer(fail, timeoutMs);
    try {
      unbindContext = bridge.onContext((context) => accept(context, true));
      Promise.resolve(bridge.ready()).then((context) => accept(context)).catch(fail);
    } catch { fail(); }
  }
  function navigate() {
    if (disposed) return;
    const next = selectionFromHash(window.location.hash);
    if (next.moduleId === selection.moduleId && next.route === selection.route && next.legacy === selection.legacy && next.invalid === selection.invalid) return;
    invalidate(); queryDom = null; selection = next; moduleId = next.moduleId; route = next.route;
    phase = "unchecked"; data = null; stale = false; message = MESSAGES.unchecked;
    if (catalog) { resolveSelection(); readSelected(); }
    else { render(); if (bridgeReady) refresh(); }
    root.focus();
  }
  const selectPage = () => navigateTo(moduleId, selector.value);
  const selectModule = (event) => navigateTo(event.target.value || null);
  const skip = () => root.focus();
  const systemTheme = () => { if (!disposed && !hostContext) applyTheme(null); };
  function destroy() {
    if (disposed) return;
    disposed = true; connection += 1; invalidate();
    if (readyTimer !== null) clearTimer(readyTimer);
    unbindContext?.(); unbindContext = null;
    window.removeEventListener("hashchange", navigate); window.removeEventListener("pagehide", destroy);
    selector.removeEventListener("change", selectPage);
    for (const select of moduleSelectors) select.removeEventListener("change", selectModule);
    document.getElementById("skip-content").removeEventListener("click", skip);
    media?.removeEventListener?.("change", systemTheme);
    catalog = null; data = null; queryDom = null;
  }
  return {
    start() {
      if (started || disposed) return;
      started = true; applyTheme(null); render();
      window.addEventListener("hashchange", navigate); window.addEventListener("pagehide", destroy);
      selector.addEventListener("change", selectPage);
      for (const select of moduleSelectors) select.addEventListener("change", selectModule);
      document.getElementById("skip-content").addEventListener("click", skip);
      media?.addEventListener?.("change", systemTheme); connect();
    }, refresh, navigate, destroy,
    getState: () => ({route, moduleId, catalog, phase, data, stale, message, disposed, webStatus, query: {...queryView}}),
  };
}

if (typeof document !== "undefined" && document.getElementById("page-root")) {
  createPageApp({document, window, bridge: window.AstrBotPluginPage}).start();
}
