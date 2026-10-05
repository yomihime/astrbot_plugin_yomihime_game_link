// Public contract fixtures, synthetic data only; not evidence of upstream health.
export function marketSample(scenario = "success") {
  const value = {schema_version: 1, status: "success", privacy: "public", error: null,
    provenance: [], timestamps: [], warnings: [], document: {title: "犎牛牛排 · 市场", subject: "模拟公开市场结果 · 物品 44091",
      blocks: [{kind: "table", columns: ["服务器", "品质", "单价", "数量", "来源记录时间"], rows: [["测试服务器", "NQ", "1250 gil", 8, "2026-10-05T00:00:00+00:00"]]},
        {kind: "links", links: [{label: "Universalis 公开来源", url: "https://universalis.app/market/44091"}]}], sources: ["Universalis · 模拟响应"], timestamps: []},
    model_facts: {market: {query: "犎牛牛排", scope: {kind: "region", target: null, regions: ["cn"], source: "default"}, quality: "all", intent: "overview",
      module_revision: 1, core_revision: 1, coverage: [{region: "cn", target: null, state: "available", stage: null, reason: null, status_code: null,
        cached: false, fetched_at: "2026-10-05T00:00:00+00:00"}], truncated: false}}};
  if (scenario === "cache") value.model_facts.market.coverage[0].cached = true;
  if (scenario === "partial") {
    value.status = "partial_success"; value.warnings = ["一部分区域获取失败；现有记录不代表全部市场。"];
    value.model_facts.market.coverage.push({region: "global", target: null, state: "failed", stage: "deadline", reason: "source_unavailable", status_code: null, cached: null, fetched_at: null});
  }
  if (scenario === "needsselection") {
    value.status = "needs_selection"; value.document.blocks = [{kind: "text", text: "请选择本次返回的候选。"}]; value.model_facts.market.coverage = [];
    value.model_facts.selection = {kind: "item", batch_id: "fixture-batch-1", generation: "fixture-generation-1",
      candidates: [{item_id: 44091, name: "犎牛牛排"}, {item_id: 100, name: "测试物品"}], truncated: true};
  }
  if (scenario === "nodata") {
    value.document.blocks = [{kind: "text", text: "来源返回空记录；当前查询范围没有可展示的在售记录。"}]; value.model_facts.market.coverage[0].state = "empty";
  }
  if (["error", "no_records", "parameter_error"].includes(scenario)) {
    value.status = "error"; value.document = null; value.model_facts = null;
    value.error = {code: scenario === "error" ? "upstream_error" : scenario, message: "DO_NOT_RENDER_PRIVATE_ERROR"};
  }
  if (scenario === "longrows") {
    value.document.blocks[0].rows = Array.from({length: 5}, (_, i) => ["很长的模拟服务器及数据中心名称".repeat(5), i % 2 ? "HQ" : "NQ", `${1234567 + i} gil`, 99, "2026-10-05T00:00:00+00:00"]);
    value.model_facts.market.truncated = true; value.warnings = ["记录已截断，最多展示5条在售记录。"];
  }
  return value;
}
