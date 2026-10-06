import { createApp as e, defineComponent as t, h as n, ref as r, withDirectives as i } from "../../../../../runtime.js";
import { NButton as a, NCard as o, NConfigProvider as s, NInput as c, NSelect as l, darkTheme as u, dateEnUS as d, dateZhCN as f, enUS as p, zhCN as m } from "../../../../../runtime.js";
//#region ../../modules/ff14/pages/src/query-contract.js
var h = (e) => e === null || typeof e == "boolean", g = (e) => typeof e == "object" && !!e && !Array.isArray(e), _ = /* @__PURE__ */ new Set([
	"parameter_error",
	"unbound",
	"auth_required",
	"auth_expired",
	"not_found",
	"not_public",
	"no_records",
	"unparsed",
	"rate_limited",
	"upstream_error",
	"module_unavailable",
	"unsupported",
	"unknown"
]), v = (e, t = 262144) => typeof e == "string" && e.length <= t;
function y(e, t = 0) {
	if (t > 8) throw Error("invalid_query_result");
	if (e === null || typeof e == "boolean" || v(e) || typeof e == "number" && Number.isFinite(e)) return e;
	if (Array.isArray(e) && e.length <= 512) return e.map((e) => y(e, t + 1));
	if (g(e) && Object.keys(e).length <= 256) {
		if (Object.keys(e).some((e) => /^(?:path|resource_?id|asset_?id|html|__proto__|prototype|constructor)$/i.test(e))) throw Error("invalid_query_result");
		return Object.fromEntries(Object.entries(e).map(([e, n]) => [e, y(n, t + 1)]));
	}
	throw Error("invalid_query_result");
}
function b(e) {
	if (!g(e) || !v(e.label, 4096) || !v(e.url, 2048) || !/^https?:\/\//i.test(e.url) || /[\s\u0000-\u001f\u007f]/.test(e.url)) throw Error("invalid_query_result");
	try {
		let t = new URL(e.url);
		if (!["http:", "https:"].includes(t.protocol) || !t.hostname || t.username || t.password || /^\/api\/(?:plug\/|plugin\/|v1\/plugins\/)/i.test(t.pathname)) throw Error();
		return {
			label: e.label,
			url: t.href
		};
	} catch {
		throw Error("invalid_query_result");
	}
}
function x(e) {
	let t = () => {
		throw Error("invalid_query_result");
	};
	if (!g(e)) return null;
	let n = {};
	if (e.market !== void 0) {
		let r = e.market, i = r?.scope, a = (e) => e === null || v(e, 256) || Number.isSafeInteger(e);
		(!g(r) || !v(r.query, 120) || !g(i) || ![
			"world",
			"dc",
			"region"
		].includes(i.kind) || !a(i.target) || !Array.isArray(i.regions) || i.regions.length > 8 || !i.regions.every((e) => v(e, 64)) || !["explicit", "default"].includes(i.source) || ![
			"all",
			"nq",
			"hq"
		].includes(r.quality) || ![
			"overview",
			"min",
			"listings"
		].includes(r.intent) || !Number.isSafeInteger(r.module_revision) || !Number.isSafeInteger(r.core_revision) || !Array.isArray(r.coverage) || r.coverage.length > 512 || typeof r.truncated != "boolean") && t();
		let o = r.coverage.map((e) => ((!g(e) || !v(e.region, 64) || !a(e.target) || ![
			"available",
			"empty",
			"failed"
		].includes(e.state) || !(e.stage === null || v(e.stage, 64)) || !(e.reason === null || v(e.reason, 256)) || !(e.status_code === null || Number.isSafeInteger(e.status_code)) || !h(e.cached) || !(e.fetched_at === null || v(e.fetched_at, 128))) && t(), {
			region: e.region,
			target: e.target,
			state: e.state,
			stage: e.stage,
			reason: e.reason,
			status_code: e.status_code,
			cached: e.cached,
			fetched_at: e.fetched_at
		}));
		n.market = {
			query: r.query,
			scope: {
				kind: i.kind,
				target: i.target,
				regions: [...i.regions],
				source: i.source
			},
			quality: r.quality,
			intent: r.intent,
			module_revision: r.module_revision,
			core_revision: r.core_revision,
			coverage: o,
			truncated: r.truncated
		};
	}
	if (e.selection !== void 0) {
		let r = e.selection;
		(!g(r) || r.kind !== "item" || !v(r.batch_id, 128) || !r.batch_id || !v(r.generation, 128) || !r.generation || !Array.isArray(r.candidates) || !r.candidates.length || r.candidates.length > 6 || typeof r.truncated != "boolean") && t();
		let i = r.candidates.map((e) => ((!g(e) || !Number.isSafeInteger(e.item_id) || e.item_id < 1 || !v(e.name, 4096)) && t(), {
			item_id: e.item_id,
			name: e.name
		}));
		new Set(i.map((e) => e.item_id)).size !== i.length && t(), n.selection = {
			kind: "item",
			batch_id: r.batch_id,
			generation: r.generation,
			candidates: i,
			truncated: r.truncated
		};
	}
	return Object.keys(n).length ? n : null;
}
function S(e) {
	let t = () => {
		throw Error("invalid_query_result");
	};
	(!g(e) || e.schema_version !== 1 || e.privacy !== "public" || ![
		"success",
		"partial_success",
		"needs_selection",
		"error"
	].includes(e.status)) && t();
	try {
		new TextEncoder().encode(JSON.stringify(e)).length > 262144 && t();
	} catch {
		t();
	}
	let n = (e) => ((!Array.isArray(e) || e.length > 512 || e.some((e) => !v(e))) && t(), [...e]), r = (e) => ((!Array.isArray(e) || e.length > 512) && t(), e.map((e) => ((!g(e) || !v(e.value, 128) || !(e.timezone === null || v(e.timezone, 128)) || !Number.isFinite(Date.parse(e.value))) && t(), {
		value: e.value,
		timezone: e.timezone
	}))), i = null, a = null;
	if (e.status === "error") (e.document !== null || !g(e.error) || !_.has(e.error.code)) && t(), a = { code: e.error.code };
	else {
		let a = e.document;
		(!g(a) || !v(a.title, 4096) || !v(a.subject, 4096) || !Array.isArray(a.blocks) || a.blocks.length > 32 || e.error !== null) && t();
		let o = a.blocks.map((e) => {
			if (g(e) || t(), e.kind === "text" && v(e.text)) return {
				kind: "text",
				text: e.text
			};
			if (["fields", "metrics"].includes(e.kind) && g(e[e.kind])) return {
				kind: e.kind,
				[e.kind]: y(e[e.kind])
			};
			if (e.kind === "table" && Array.isArray(e.columns) && e.columns.length > 0 && e.columns.length <= 512 && e.columns.every((e) => v(e, 4096)) && Array.isArray(e.rows) && e.rows.length <= 512 && e.rows.every((t) => Array.isArray(t) && t.length === e.columns.length)) return {
				kind: "table",
				columns: [...e.columns],
				rows: y(e.rows)
			};
			if (e.kind === "links" && Array.isArray(e.links) && e.links.length <= 512) return {
				kind: "links",
				links: e.links.map(b)
			};
			t();
		});
		i = {
			title: a.title,
			subject: a.subject,
			blocks: o,
			sources: n(a.sources),
			timestamps: r(a.timestamps)
		};
	}
	return {
		schema_version: 1,
		status: e.status,
		privacy: "public",
		document: i,
		error: a,
		model_facts: x(e.model_facts),
		provenance: n(e.provenance),
		timestamps: r(e.timestamps),
		warnings: n(e.warnings)
	};
}
function C(e, t) {
	let n = {}, r = (e, r, i) => {
		let a = String(t[e] ?? "").trim();
		return (!a || a.length > i) && (n[e] = `请输入${r}，最多${i}个字符。`), a;
	}, i;
	if (e === "items") i = { query: r("query", "名称或 ID", 120) };
	else if (e === "market") {
		i = {
			query: r("query", "物品名称或 ID", 120),
			quality: String(t.quality ?? "all"),
			intent: String(t.intent ?? "overview")
		}, [
			"all",
			"nq",
			"hq"
		].includes(i.quality) || (n.quality = "请选择全部、NQ 或 HQ。"), [
			"overview",
			"min",
			"listings"
		].includes(i.intent) || (n.intent = "请选择概览、最低价或在售列表。");
		for (let e of [
			"server",
			"dc",
			"region"
		]) {
			let r = String(t[e] ?? "").trim();
			r.length > 100 && (n[e] = "范围输入最多100个字符。"), r && (i[e] = r);
		}
		i.region && !["cn", "global"].includes(i.region) && (n.region = "请选择国服或国际服，或留空使用服务端默认范围。");
	} else {
		let a = String(t.region ?? "");
		if (["cn", "global"].includes(a) || (n.region = "请选择国服或国际服。"), e === "logs") i = {
			region: a,
			server: r("server", "服务器", 100),
			character: r("character", "角色名", 120)
		};
		else if (e === "calendar") {
			let e = String(t.days ?? ""), o = /^[0-9]+$/.test(e) ? Number(e) : NaN;
			(!Number.isInteger(o) || o < 1 || o > 30) && (n.days = "查询天数须为1至30的整数。");
			let s = r("timezone", "IANA 时区", 128);
			try {
				new Intl.DateTimeFormat("en-US", { timeZone: s });
			} catch {
				n.timezone = "请输入有效 IANA 时区，例如 Asia/Shanghai。";
			}
			i = {
				region: a,
				days: o,
				timezone: s
			};
		} else throw Error("invalid_query_route");
	}
	return {
		body: i,
		errors: n
	};
}
//#endregion
//#region ../../modules/ff14/pages/src/query-controller.ts
var w = {
	items: [{
		name: "query",
		label: "物品名称或 ID",
		placeholder: "例如 犎牛牛排，或 44091",
		limit: 120
	}],
	market: [
		{
			name: "query",
			label: "物品名称或 ID",
			placeholder: "例如 犎牛牛排，或 44091",
			limit: 120
		},
		{
			name: "server",
			label: "服务器（可选）",
			placeholder: "World ID 或明确服务器名",
			limit: 100
		},
		{
			name: "dc",
			label: "数据中心（可选）",
			placeholder: "规范数据中心名",
			limit: 100
		},
		{
			name: "region",
			label: "区域（可选）",
			options: [
				{
					label: "服务端默认范围",
					value: ""
				},
				{
					label: "国服",
					value: "cn"
				},
				{
					label: "国际服",
					value: "global"
				}
			]
		},
		{
			name: "quality",
			label: "品质",
			options: [
				{
					label: "全部品质",
					value: "all"
				},
				{
					label: "NQ",
					value: "nq"
				},
				{
					label: "HQ",
					value: "hq"
				}
			]
		},
		{
			name: "intent",
			label: "展示",
			options: [
				{
					label: "概览",
					value: "overview"
				},
				{
					label: "最低价",
					value: "min"
				},
				{
					label: "在售列表",
					value: "listings"
				}
			]
		}
	]
}, T = {
	items: "item.lookup",
	market: "ff14.market.query"
}, E = {
	items: "queries/items",
	market: "queries/market"
}, D = "已停止展示。仅停止展示，本次请求可能仍在后台处理；不会自动重试。", O = /* @__PURE__ */ new Map();
function k(e, t, n = 35e3) {
	let r = e.routeId;
	if (!w[r]) throw Error("invalid_query_route");
	let i = JSON.stringify([
		e.context.owner,
		r,
		e.context.boundary,
		e.context.runtimeId,
		e.context.epoch
	]), a = {
		draft: { ...O.get(i) || Object.fromEntries(w[r].map((e) => [e.name, e.name === "quality" ? "all" : e.name === "intent" ? "overview" : ""])) },
		errors: {},
		result: null,
		phase: "idle",
		message: "尚未提交查询。",
		context: e.context
	}, o = !1, s = 0, c = null;
	function l() {
		c && clearTimeout(c), c = null;
	}
	let u = (t) => !o && e.scope.isCurrent() && t === s;
	function d(e = !0) {
		s++, l(), a.phase = "idle", e && (a.result = null);
	}
	function f(e, n) {
		e in a.draft && !o && (a.draft[e] = n, O.set(i, { ...a.draft }), d(), a.errors = {}, a.message = "输入已修改，请手动提交查询。", t());
	}
	async function p(i) {
		if (o || !e.scope.isCurrent() || !a.context.available || a.phase === "loading") return;
		let f = i && r === "market" ? {
			body: { selection: i },
			errors: {}
		} : C(r, a.draft), p = f.errors;
		if (d(), a.errors = p, Object.keys(p).length) {
			a.message = "请先修正表单中标出的输入。", t();
			return;
		}
		let m = s;
		a.phase = "loading", a.message = "正在查询…", t(), c = setTimeout(() => {
			u(m) && (s++, c = null, a.phase = "timeout", a.message = "等待查询超过35秒，已停止展示。后台请求可能仍在处理，请检查会话与来源后手动重试。", t());
		}, n);
		try {
			let n = await e.services.invoke(T[r], f.body, E[r]);
			if (!u(m)) return;
			let i = S(n);
			l(), a.result = i, a.phase = "result", a.message = "", t();
		} catch {
			if (!u(m)) return;
			l(), a.phase = "error", a.message = "查询未完成，请检查登录、网页地址和输入后手动重试。可重新登录 Dashboard，检查插件配置齿轮中的网页地址，保存重载后重新打开本页。", t();
		}
	}
	function m(e, t = a.result) {
		let n = t?.model_facts?.selection;
		if (t === a.result && n && a.phase !== "loading" && n.candidates.some((t) => t.item_id === e)) return p({
			batch_id: n.batch_id,
			generation: n.generation,
			item_id: e
		});
	}
	function h() {
		o || (O.set(i, { ...a.draft }), o = !0, d());
	}
	return e.scope.onDispose(h), {
		state: a,
		edit: f,
		submit: p,
		choose: m,
		stop() {
			a.phase === "loading" && (d(), a.message = D, t());
		},
		update(e) {
			o || (e.owner !== a.context.owner || e.runtimeId !== a.context.runtimeId || e.epoch !== a.context.epoch || e.boundary !== a.context.boundary ? (d(), a.message = "入口绑定已变化，请手动重新查询。") : a.context.available && !e.available && a.phase === "loading" && (d(), a.message = "状态刷新已停止待返回结果展示。本次请求可能仍在后台处理，请确认状态后手动重新查询。"), a.context = e, t());
		},
		dispose: h
	};
}
//#endregion
//#region ../../modules/ff14/pages/src/select-a11y.ts
function A(e) {
	let t = /* @__PURE__ */ new WeakMap();
	return {
		mounted(n, r) {
			let i = r.value, a = () => {
				let t = n.querySelector(".n-base-selection-label[tabindex]");
				if (!t) return;
				t.id = i.id, t.setAttribute("role", "combobox"), t.setAttribute("aria-labelledby", i.labelId), t.setAttribute("aria-haspopup", "listbox"), t.setAttribute("aria-expanded", String(i.expanded)), t.setAttribute("aria-invalid", String(i.invalid)), t.setAttribute("aria-describedby", i.errorId);
				let r = e.querySelector(`#${i.menuId}`);
				r ? t.setAttribute("aria-controls", i.menuId) : t.removeAttribute("aria-controls");
				let a = i.expanded ? r?.querySelector(".n-base-select-option--pending[role=option][id]") : null;
				a ? t.setAttribute("aria-activedescendant", a.id) : t.removeAttribute("aria-activedescendant");
			}, o = new e.ownerDocument.defaultView.MutationObserver(a);
			o.observe(e, {
				subtree: !0,
				childList: !0,
				attributes: !0,
				attributeFilter: ["class"]
			}), t.set(n, {
				sync: (e) => {
					i = e, a();
				},
				observer: o
			}), a();
		},
		updated(e, n) {
			t.get(e)?.sync(n.value);
		},
		beforeUnmount(e) {
			t.get(e)?.observer.disconnect(), t.delete(e);
		}
	};
}
//#endregion
//#region ../../modules/ff14/pages/src/entry.ts
function j(e) {
	return e === null ? "—" : e && typeof e == "object" && !Array.isArray(e) && Object.hasOwn(e, "value") ? [String(e.value), e.currency || e.unit || e.timezone].filter(Boolean).join(" ") : e && typeof e == "object" ? JSON.stringify(e) : String(e);
}
var M = {
	parameter_error: "参数未被来源接受；范围含糊时使用明确 World ID 或规范数据中心名。",
	auth_required: "来源凭据不可用，请联系管理员配置相应区域的来源凭据。",
	auth_expired: "授权已过期，请重新登录 Dashboard。",
	rate_limited: "查询受到限流，请稍后手动重试。",
	module_unavailable: "模块或查询期限不可用，请刷新状态后手动重试。",
	upstream_error: "来源暂时不可用，请稍后手动重试。",
	not_found: "来源未找到匹配内容，请检查名称或 ID。",
	no_records: "来源未返回记录；这不表示查询窗口已被完整覆盖。",
	not_public: "来源内容未公开。"
};
function N(e) {
	let t = e.document, r = t ? [n("h3", t.title), n("p", { class: "ff14-hint" }, t.subject)] : [];
	for (let e of t?.blocks || []) e.kind === "text" ? r.push(n("p", { class: "ff14-result-text" }, e.text)) : ["fields", "metrics"].includes(e.kind) ? r.push(n("dl", { class: "ff14-fields" }, Object.entries(e[e.kind]).flatMap(([e, t]) => [n("dt", e), n("dd", j(t))]))) : e.kind === "table" ? (r.push(n("div", {
		class: "ff14-table-desktop",
		tabindex: 0,
		"aria-label": "结果表格"
	}, [n("table", [n("thead", [n("tr", e.columns.map((e) => n("th", { scope: "col" }, e)))]), n("tbody", e.rows.map((e) => n("tr", e.map((e) => n("td", j(e))))))])]), n("div", { class: "ff14-table-mobile" }, e.rows.map((t) => n("dl", { class: "ff14-fields" }, t.flatMap((t, r) => [n("dt", e.columns[r]), n("dd", j(t))]))))), e.rows.length || r.push(n("p", { class: "ff14-warning" }, "来源返回的表格内容为空。"))) : r.push(n("ul", e.links.map((e) => n("li", [n("a", {
		href: e.url,
		target: "_blank",
		rel: "noopener noreferrer"
	}, e.label)]))));
	t && !t.blocks.length && r.push(n("p", { class: "ff14-warning" }, "来源未返回可展示内容。"));
	for (let t of e.warnings) r.push(n("p", { class: "ff14-warning" }, t));
	let i = [...t?.sources || [], ...e.provenance];
	i.length && r.push(n("p", { class: "ff14-hint" }, `来源 / 口径：${i.join("；")}`));
	let a = [...t?.timestamps || [], ...e.timestamps];
	return a.length && r.push(n("p", { class: "ff14-hint" }, `返回时间：${a.map(j).join("；")}`)), r;
}
function P(h, g) {
	let _ = new Set(Array.from(h.ownerDocument.head.querySelectorAll("style"))), v = A(h), y = r({}), b = r(0), x = k(g, () => {
		b.value++;
	}), S = e(t({ setup() {
		return () => {
			b.value;
			let e = x.state, t = e.phase === "loading", r = e.result, _ = e.context, S = r?.model_facts?.selection, C = async (t) => {
				t?.preventDefault(), await x.submit();
				let n = Object.keys(e.errors)[0];
				n && h.querySelector(`#ff14-${g.routeId}-${n}`)?.focus();
			}, T = r?.model_facts?.market;
			return n(s, {
				theme: _.theme === "dark" ? u : null,
				locale: _.locale.startsWith("en") ? p : m,
				dateLocale: _.locale.startsWith("en") ? d : f,
				styleMountTarget: h,
				preflightStyleDisabled: !0,
				inlineThemeDisabled: !0
			}, { default: () => n("section", {
				class: "ff14-page",
				"data-theme": _.theme
			}, [n(o, {
				title: g.routeId === "market" ? "按物品查询市场" : "查找物品与公开详情",
				bordered: !1
			}, { default: () => [
				n("p", { class: "ff14-hint" }, g.routeId === "market" ? "范围可留空，由服务端解析默认区域的全服范围；名称与范围别名也由服务端判定。" : "名称或 ID 查询；缺项与来源说明保留在结果中。"),
				n("form", {
					class: "ff14-form",
					"aria-busy": t,
					onSubmit: C
				}, [...w[g.routeId].map((t) => n("div", { class: `ff14-field ${t.name === "query" ? "ff14-query" : ""}` }, [
					n("label", {
						id: `label-${g.routeId}-${t.name}`,
						for: `ff14-${g.routeId}-${t.name}`,
						onClick: () => h.querySelector(`#ff14-${g.routeId}-${t.name}`)?.focus()
					}, t.label),
					t.options ? i(n(l, {
						value: e.draft[t.name],
						options: t.options,
						to: h,
						menuProps: {
							id: `menu-${g.routeId}-${t.name}`,
							role: "listbox",
							"aria-labelledby": `label-${g.routeId}-${t.name}`
						},
						nodeProps: (n) => ({
							id: `option-${g.routeId}-${t.name}-${n.value}`,
							role: "option",
							"aria-selected": e.draft[t.name] === n.value
						}),
						"onUpdate:show": (e) => {
							y.value = {
								...y.value,
								[t.name]: e
							};
						},
						"onUpdate:value": (e) => x.edit(t.name, e)
					}), [[v, {
						id: `ff14-${g.routeId}-${t.name}`,
						labelId: `label-${g.routeId}-${t.name}`,
						menuId: `menu-${g.routeId}-${t.name}`,
						expanded: !!y.value[t.name],
						invalid: !!e.errors[t.name],
						errorId: `error-${t.name}`
					}]]) : n(c, {
						value: e.draft[t.name],
						placeholder: t.placeholder,
						maxlength: t.limit,
						inputProps: {
							id: `ff14-${g.routeId}-${t.name}`,
							"aria-label": t.label,
							"aria-invalid": !!e.errors[t.name],
							"aria-describedby": `error-${t.name}`
						},
						"onUpdate:value": (e) => x.edit(t.name, e)
					}),
					n("span", {
						class: "ff14-field-error",
						id: `error-${t.name}`
					}, e.errors[t.name] || "")
				])), n("div", { class: "ff14-actions" }, [n(a, {
					type: "primary",
					attrType: "submit",
					disabled: t || !_.available
				}, () => g.routeId === "market" ? "查询市场" : "查询物品"), t ? n(a, { onClick: () => {
					x.stop(), h.querySelector("input")?.focus();
				} }, () => "停止展示") : null])]),
				n("p", {
					class: _.available ? "ff14-hint" : "ff14-warning",
					role: "status"
				}, _.available ? "模块目录已加载；实际查询仍由后端逐请求鉴权，仅手动提交。" : "当前入口状态尚未确认，请刷新状态；仍失败时请重新登录 Dashboard 或重新打开本页。")
			] }), n(o, {
				title: "本次结果",
				bordered: !1,
				class: "ff14-result",
				"aria-live": "polite"
			}, { default: () => [
				r ? n("p", {
					class: r.status === "error" ? "ff14-error" : r.status === "success" ? "ff14-success" : "ff14-warning",
					role: r.status === "error" ? "alert" : "status"
				}, r.status === "success" ? "查询完成" : r.status === "partial_success" ? "部分结果 · 保留来源说明" : r.status === "needs_selection" ? "需要选择物品 · 请确认本次候选" : `${r.error?.code === "not_found" ? "无匹配结果" : r.error?.code === "no_records" ? "没有可展示记录" : "查询失败"} · ${M[r.error?.code || ""] || "查询未完成，请检查输入与来源后手动重试。"}`) : n("p", { role: ["error", "timeout"].includes(e.phase) ? "alert" : "status" }, e.message),
				t ? n("p", { class: "ff14-hint" }, "仅停止展示，本次请求可能仍在后台处理。") : null,
				...r ? N(r) : [],
				T ? n("div", { class: "ff14-market-context" }, [
					n("dl", { class: "ff14-fields" }, [
						n("dt", "当前解析范围"),
						n("dd", `${{
							world: "服务器",
							dc: "数据中心",
							region: "区域全服"
						}[T.scope.kind]} / ${T.scope.target ?? "—"} / ${T.scope.regions.join("、")}（${T.scope.source === "default" ? "服务端默认" : "显式指定"}）`),
						n("dt", "品质"),
						n("dd", {
							all: "全部品质",
							nq: "NQ",
							hq: "HQ"
						}[T.quality]),
						n("dt", "意图"),
						n("dd", {
							overview: "概览",
							min: "最低价",
							listings: "在售列表"
						}[T.intent])
					]),
					...T.coverage.map((e) => n("p", { class: "ff14-hint" }, `来源覆盖：${e.region} / ${e.target ?? "—"} · ${{
						available: "已取得记录",
						empty: "来源返回空记录",
						failed: "获取失败"
					}[e.state]}；${e.cached === !0 ? "缓存结果" : e.cached === !1 ? "本次获取" : "缓存状态未知"}；来源获取时间：${e.fetched_at ?? "未知"}${e.reason ? `；原因：${e.reason}` : ""}。`)),
					T.truncated ? n("p", { class: "ff14-warning" }, "结果已截断；只展示本次返回的有限记录，不代表全部市场。") : null
				]) : null,
				S ? n("div", {
					class: "ff14-candidates",
					"aria-label": "物品候选"
				}, [
					n("p", { class: "ff14-hint" }, "请选择本次返回的物品候选；修改输入或重新提交后旧候选失效。"),
					...S.candidates.map((e) => n(a, {
						disabled: t || !_.available,
						"data-item-id": e.item_id,
						onClick: () => {
							x.choose(e.item_id, r), h.querySelector("input")?.focus();
						}
					}, () => `${e.name} · ID ${e.item_id}`)),
					S.truncated ? n("p", { class: "ff14-warning" }, "候选已截断，请进一步明确名称后手动查询。") : null
				]) : null
			] })]) });
		};
	} }));
	S.mount(h);
	let C = !1, T = !1, E = /* @__PURE__ */ new Set(["vueuc/binder", "vueuc/virtual-list"]), D = () => Array.from(h.ownerDocument.head.querySelectorAll("style[cssr-id]")).filter((e) => E.has(e.getAttribute("cssr-id")) && !_.has(e)), O = null, j = () => {
		if (T) return;
		C = !0, O ||= /* @__PURE__ */ new Set([
			() => x.dispose(),
			() => S.unmount(),
			...D().map((e) => () => e.remove()),
			() => h.replaceChildren()
		]);
		let e = [];
		for (let t of O) try {
			t(), O.delete(t);
		} catch (t) {
			e.push(t);
		}
		if (T = O.size === 0, e.length) throw AggregateError(e, "cleanup_pending");
	};
	return g.scope.onDispose(j), {
		update: (e) => {
			C || x.update(e);
		},
		dispose: j
	};
}
//#endregion
export { P as mount };
