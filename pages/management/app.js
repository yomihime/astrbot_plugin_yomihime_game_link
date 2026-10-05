const TARGETS = Object.freeze({
  "game_link/core": ["default_region"],
  "ff14/ff14": ["ff14_calendar_default_days", "ff14_calendar_default_timezone", "ff14_calendar_default_delivery_time"],
});
const LABELS = {default_region: "默认区域（cn / global）", ff14_calendar_default_days: "日历天数（1–30）",
  ff14_calendar_default_timezone: "日历时区", ff14_calendar_default_delivery_time: "投递时间（HH:MM）"};

export function validateSnapshot(data) {
  if (!data || typeof data !== "object" || Object.keys(data).sort().join() !== Object.keys(TARGETS).sort().join()) throw new Error("invalid_config");
  for (const [target, names] of Object.entries(TARGETS)) {
    const row = data[target];
    if (!row || !Number.isSafeInteger(row.revision) || row.revision < 1 || !row.fields
        || Object.keys(row.fields).sort().join() !== [...names].sort().join()) throw new Error("invalid_config");
    for (const name of names) {
      const field = row.fields[name];
      if (!field || !["valid", "invalid"].includes(field.state) || typeof field.present !== "boolean"
          || !["sqlite", "default"].includes(field.source) || field.state === "invalid" && field.value !== null) throw new Error("invalid_config");
      if (field.state === "valid" && (name === "ff14_calendar_default_days"
          ? !Number.isInteger(field.value) || field.value < 1 || field.value > 30
          : typeof field.value !== "string" || field.value.length > 128)) throw new Error("invalid_config");
    }
  }
  return data;
}

export function updateBody(target, revision, controls) {
  if (!Object.hasOwn(TARGETS, target) || !Number.isSafeInteger(revision) || revision < 1) throw new Error("invalid_config");
  const updates = [];
  for (const name of TARGETS[target]) {
    const {mode, value} = controls[name];
    if (mode === "keep") continue;
    if (mode === "clear") updates.push({field: name, mode});
    else if (mode === "replace") {
      const input = name === "ff14_calendar_default_days" ? Number(value) : value;
      if (name === "ff14_calendar_default_days" && (!Number.isInteger(input) || input < 1 || input > 30)) throw new Error("invalid_config");
      updates.push({field: name, mode, value: input});
    } else throw new Error("invalid_config");
  }
  if (!updates.length) throw new Error("select_changes");
  return {module_id: target, expected_revision: revision, updates};
}

export function createManagementPage(document, bridge, window) {
  let snapshot = null, busy = false, closed = false, ready = false, generation = 0, off = null, lastContext = null, refreshPending = false;
  const status = document.getElementById("status"), fields = document.getElementById("fields");
  const element = (tag, text) => { const node = document.createElement(tag); if (text) node.textContent = text; return node; };
  function buttons() { for (const button of document.querySelectorAll("button")) button.disabled = closed || busy || !ready || button.id !== "refresh" && !snapshot; }
  async function post(endpoint, body) {
    if (closed || !ready) throw new Error("admin_authorization_denied");
    const attempt = generation;
    const response = await bridge.apiPost(`admin/${endpoint}`, body);
    if (closed || attempt !== generation) throw new Error("admin_authorization_denied");
    // The official parent bridge unwraps response.data.data before replying.
    if (endpoint === "read") return validateSnapshot(response);
    if (!response || typeof response !== "object" || Array.isArray(response)) throw new Error("operation_unavailable");
    if (endpoint === "update" && (response.module_id !== body.module_id || !Number.isSafeInteger(response.revision) || response.revision <= body.expected_revision)) throw new Error("operation_unavailable");
    if (endpoint === "rollback" && (Object.keys(response).sort().join() !== "rolled_back" || response.rolled_back !== true)) throw new Error("operation_unavailable");
    if (endpoint === "recover" && (Object.keys(response).sort().join() !== "recovered" || response.recovered !== true)) throw new Error("operation_unavailable");
    return response;
  }
  function render() {
    fields.replaceChildren();
    for (const [target, names] of Object.entries(TARGETS)) {
      const card = element("section"); card.className = "card";
      card.append(element("h2", target === "game_link/core" ? "Core 默认区域" : "FF14 日历"), element("p", `配置版本：${snapshot[target].revision}`));
      const controls = {};
      for (const name of names) {
        const row = snapshot[target].fields[name], label = element("label", LABELS[name]);
        const input = element("input"); input.value = row.value === null ? "" : String(row.value); input.disabled = true; input.maxLength = 128;
        input.id = name; label.htmlFor = name;
        if (name === "ff14_calendar_default_days") { input.type = "number"; input.min = "1"; input.max = "30"; input.step = "1"; }
        else input.type = "text";
        const mode = element("select"); mode.setAttribute("aria-label", `${LABELS[name]}修改方式`);
        for (const [value, text] of [["keep", "保持"], ["replace", "替换 / 修复"], ["clear", "清除显式值"]]) { const option = element("option", text); option.value = value; mode.append(option); }
        mode.addEventListener("change", () => { input.disabled = mode.value !== "replace"; });
        card.append(label, element("p", `${row.state === "valid" ? "有效" : "无效存量，原值不显示"} · ${row.present ? "已有显式值" : "未设置"} · ${row.source === "sqlite" ? "Core 配置" : "默认值"}`), mode, input);
        controls[name] = {mode, input};
      }
      const save = element("button", "保存本组修改"); save.type = "button";
      save.addEventListener("click", () => run(async () => {
        const selected = Object.fromEntries(names.map(name => [name, {mode: controls[name].mode.value, value: controls[name].input.value}]));
        await post("update", updateBody(target, snapshot[target].revision, selected));
        await refresh(); status.textContent = "配置已保存；需要恢复时请单独执行恢复运行。";
      }));
      card.append(save); fields.append(card);
    }
    buttons();
  }
  async function refresh() { const attempt = generation; const data = await post("read", {}); if (closed || attempt !== generation) throw new Error("admin_authorization_denied"); snapshot = validateSnapshot(data); render(); }
  async function run(action) {
    if (busy || closed) return;
    busy = true; buttons(); status.textContent = "管理操作正在执行。";
    try { await action(); }
    catch (error) { status.textContent = error.message === "revision_conflict" || error.message === "???????????????????" ? "配置已变化，请刷新并重新核对后修改。"
      : error.message === "select_changes" ? "请选择需要修改的字段。" : "未取得成功确认，请刷新核对配置与版本后重试。"; }
    finally { busy = false; buttons(); if (refreshPending && ready && !closed) queuedRefresh(); }
  }
  const revisions = () => Object.fromEntries(Object.keys(TARGETS).map(target => [target, snapshot[target].revision]));
  document.getElementById("refresh").addEventListener("click", () => run(async () => { await refresh(); status.textContent = "已读取四字段配置。"; }));
  document.getElementById("rollback").addEventListener("click", () => run(async () => { await post("rollback", {expected_revisions: revisions()}); await refresh(); status.textContent = "四字段迁移已限定回退，其它数据保留。"; }));
  document.getElementById("recover").addEventListener("click", () => run(async () => { await post("recover", {expected_revisions: revisions(), complete_from_current: document.getElementById("replacement").checked}); await refresh(); status.textContent = "配置已重新校验，业务运行已恢复。"; }));
  function close() { closed = true; ready = false; snapshot = null; refreshPending = false; generation++; off?.(); buttons(); }
  function queuedRefresh() { refreshPending = false; run(async () => { await refresh(); status.textContent = "已读取四字段配置。"; }); }
  function accept(context) { if (closed || context === lastContext) return; lastContext = context; generation++; snapshot = null; ready = !!context && context.pluginName === "astrbot_plugin_yomihime_game_link"; buttons(); refreshPending = ready; if (ready && !busy) queuedRefresh(); }
  return {start() { if (!bridge || typeof bridge.apiPost !== "function" || typeof bridge.ready !== "function" || typeof bridge.onContext !== "function") { status.textContent = "请从宿主插件管理页面打开此页。"; return; } off = bridge.onContext(accept); Promise.resolve(bridge.ready()).then(accept).catch(() => { status.textContent = "宿主管理会话不可用。"; }); window?.addEventListener("pagehide", close, {once: true}); }, close};
}

if (typeof document !== "undefined") createManagementPage(document, window.AstrBotPluginPage, window).start();
