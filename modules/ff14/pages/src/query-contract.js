const nullableBool = (value) => value === null || typeof value === "boolean";
const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
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
function marketFacts(input) {
  const fail = () => { throw new Error("invalid_query_result"); };
  if (!object(input)) return null;
  const facts = {};
  if (input.market !== undefined) {
    const v = input.market, s = v?.scope;
    const target = (value) => value === null || text(value, 256) || Number.isSafeInteger(value);
    if (!object(v) || !text(v.query, 120) || !object(s) || !["world", "dc", "region"].includes(s.kind) || !target(s.target)
        || !Array.isArray(s.regions) || s.regions.length > 8 || !s.regions.every((r) => text(r, 64)) || !["explicit", "default"].includes(s.source)
        || !["all", "nq", "hq"].includes(v.quality) || !["overview", "min", "listings"].includes(v.intent)
        || !Number.isSafeInteger(v.module_revision) || !Number.isSafeInteger(v.core_revision)
        || !Array.isArray(v.coverage) || v.coverage.length > 512 || typeof v.truncated !== "boolean") fail();
    const coverage = v.coverage.map((c) => {
      if (!object(c) || !text(c.region, 64) || !target(c.target) || !["available", "empty", "failed"].includes(c.state) || !(c.stage === null || text(c.stage, 64))
          || !(c.reason === null || text(c.reason, 256)) || !(c.status_code === null || Number.isSafeInteger(c.status_code))
          || !nullableBool(c.cached) || !(c.fetched_at === null || text(c.fetched_at, 128))) fail();
      return {region: c.region, target: c.target, state: c.state, stage: c.stage, reason: c.reason,
        status_code: c.status_code, cached: c.cached, fetched_at: c.fetched_at};
    });
    facts.market = {query: v.query, scope: {kind: s.kind, target: s.target, regions: [...s.regions], source: s.source},
      quality: v.quality, intent: v.intent, module_revision: v.module_revision, core_revision: v.core_revision, coverage, truncated: v.truncated};
  }
  if (input.selection !== undefined) {
    const v = input.selection;
    if (!object(v) || v.kind !== "item" || !text(v.batch_id, 128) || !v.batch_id || !text(v.generation, 128) || !v.generation
        || !Array.isArray(v.candidates) || !v.candidates.length || v.candidates.length > 6 || typeof v.truncated !== "boolean") fail();
    const candidates = v.candidates.map((c) => {
      if (!object(c) || !Number.isSafeInteger(c.item_id) || c.item_id < 1 || !text(c.name, 4096)) fail();
      return {item_id: c.item_id, name: c.name};
    });
    if (new Set(candidates.map((c) => c.item_id)).size !== candidates.length) fail();
    facts.selection = {kind: "item", batch_id: v.batch_id, generation: v.generation, candidates, truncated: v.truncated};
  }
  return Object.keys(facts).length ? facts : null;
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
  // Only the public market context and bounded candidate contract are retained.
  return {schema_version: 1, status: input.status, privacy: "public", document, error,
    model_facts: marketFacts(input.model_facts),
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
  else if (route === "market") {
    body = {query: bounded("query", "物品名称或 ID", 120), quality: String(draft.quality ?? "all"), intent: String(draft.intent ?? "overview")};
    if (!["all", "nq", "hq"].includes(body.quality)) errors.quality = "请选择全部、NQ 或 HQ。";
    if (!["overview", "min", "listings"].includes(body.intent)) errors.intent = "请选择概览、最低价或在售列表。";
    for (const key of ["server", "dc", "region"]) {
      const value = String(draft[key] ?? "").trim();
      if (value.length > 100) errors[key] = "范围输入最多100个字符。";
      if (value) body[key] = value;
    }
    if (body.region && !["cn", "global"].includes(body.region)) errors.region = "请选择国服或国际服，或留空使用服务端默认范围。";
  }
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

