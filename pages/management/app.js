import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NAlert as a, NCard as o, NConfigProvider as s, darkTheme as c, dateEnUS as l, dateZhCN as u, enUS as d, zhCN as f } from "./runtime.js";
//#region src/management-view.ts
function p(r) {
	let p = i([]), m = i({}), h = () => {}, g = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), _ = t({ setup() {
		return () => {
			let e = m.value.isDark === !0 || m.value.theme === "dark", t = String(m.value.locale || "").startsWith("en");
			return n(s, {
				theme: e ? c : null,
				locale: t ? d : f,
				dateLocale: t ? l : u,
				inlineThemeDisabled: !0
			}, { default: () => n("main", { class: "management" }, [
				n("p", { class: "brand" }, "如月怜的游戏连结"),
				n("h1", "配置目录与管理"),
				n("p", "目录来自可信 Core 与模块声明。当前只开放四字段管理范围；字段注册不授予存量读取或修改权限，写操作仍由后端逐请求鉴权。"),
				n("p", "保存只修改所选配置。客户端检查用于辅助输入，模块语义校验与配置版本以服务端为准。启动失败时，修复后需单独点击恢复运行。"),
				n(a, {
					type: "info",
					showIcon: !1
				}, { default: () => n("p", {
					id: "status",
					role: "status",
					"aria-live": "polite"
				}, "正在连接宿主管理页面。") }),
				g("refresh", "刷新配置与版本"),
				n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, p.value.map((e) => n("section", {
					class: "card",
					key: e.target,
					"data-owner": e.target
				}, [n(o, { title: e.target }, { default: () => [
					n("p", e.revision),
					n("div", e.controls.map((e) => {
						let t = e.field, r = t.value_schema, i = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, a = r?.enum || (r?.type === "boolean" ? [!1, !0] : null);
						return n("div", {
							class: "config-field",
							key: e.signature,
							"data-field": t.name,
							"data-owner": t.module_id
						}, [
							n("label", { for: i }, t.description || t.name),
							n("p", {
								class: "field-detail",
								id: i + "-detail"
							}, e.detail),
							n("select", {
								"data-role": "mode",
								"aria-label": `${t.description || t.name}修改方式`,
								ref: (t) => {
									e.mode = t;
								},
								onChange: () => {
									e.draftVersion++, h();
								}
							}, [
								["keep", "保持"],
								["replace", "替换 / 修复"],
								["clear", "清除显式值"]
							].map(([e, t]) => n("option", { value: e }, t))),
							n(a ? "select" : "input", {
								id: i,
								"data-role": "value",
								"aria-describedby": i + "-detail",
								onInput: () => {
									e.draftVersion++;
								},
								onChange: () => {
									e.draftVersion++;
								},
								ref: (t) => {
									e.input = t;
								},
								...a ? {} : {
									type: ["integer", "number"].includes(r?.type || "") ? "number" : "text",
									min: r?.minimum,
									max: r?.maximum,
									step: r?.type === "integer" ? "1" : "any",
									maxlength: r?.maxLength
								}
							}, a ? [n("option", {
								value: "",
								disabled: !0
							}, "请选择有效值"), ...a.map((e, t) => n("option", { value: String(t) }, String(e)))] : void 0)
						]);
					})),
					n("button", {
						type: "button",
						"data-action": "save",
						disabled: !0,
						ref: (t) => {
							e.save = t;
						},
						onClick: e.onSave
					}, "保存本组修改")
				] })]))),
				n(o, {
					class: "card",
					title: "限定恢复"
				}, { default: () => [
					n("p", "回退仅处理本次四字段迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。"),
					g("rollback", "回退四字段迁移"),
					n("p", n("label", [n("input", {
						id: "replacement",
						type: "checkbox"
					}), "以四个已显式保存的有效新值完成恢复（缺少旧准备材料时）"])),
					g("recover", "重新校验并恢复运行")
				] })
			]) });
		};
	} }), v = e(_);
	return v.mount(r.getElementById("management-root")), {
		fields(e, t) {
			h = t, p.value = e;
		},
		context(e) {
			m.value = e || {};
		},
		dispose() {
			v.unmount();
		}
	};
}
//#endregion
//#region src/management.js
var m = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), h = (e, t) => m(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), g = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, _ = (e = "invalid_config") => {
	throw Error(e);
}, v = (e) => JSON.stringify([e.module_id, e.name]), y = (e, t) => e.fields.filter((e) => e.module_id === t), b = (e) => [...e].length;
function x(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && _(), typeof e == "string") {
			b(e) > 4096 && _();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!m(e) && !Array.isArray(e) || r.has(e)) && _(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && _();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && _();
}
function S(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || b(t) >= e.minLength) && (e.maxLength === void 0 || b(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => S(e.items, t)) : e.type === "object" && m(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && S(e.properties[t], n));
}
function C(e, t = 0) {
	(!m(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && _();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && _(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !m(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && _();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || _(), C(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && _();
	} else if (e.type === "array") n.push("items"), C(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && _();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && _(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => S({
			...e,
			enum: void 0
		}, t))) && _();
	}
	Object.keys(e).some((e) => !n.includes(e)) && _();
}
function w(e) {
	x(e), (!h(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && _();
	let t = [
		"module_id",
		"name",
		"description",
		"group",
		"value_schema",
		"default",
		"required",
		"readable",
		"editable",
		"blocked_reason"
	], n = /* @__PURE__ */ new Set();
	for (let r of e.fields) (!h(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => g.test(e)) || typeof r.name != "string" || !r.name || b(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && g.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && _(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && _(), n.has(v(r)) && _(), n.add(v(r)), r.value_schema !== null && (x(r.value_schema, 16, 2048), C(r.value_schema), r.default !== null && !S(r.value_schema, r.default) && _());
	let r = JSON.parse(JSON.stringify(e)), i = (e) => {
		if (e && typeof e == "object") {
			for (let t of Object.values(e)) i(t);
			Object.freeze(e);
		}
		return e;
	};
	return i(r);
}
function T(e, t) {
	t || _(), x(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	h(e, n) || _();
	for (let r of n) {
		let n = e[r], i = y(t, r).filter((e) => e.readable);
		(!h(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !h(n.fields, i.map((e) => e.name))) && _();
		for (let e of i) {
			let t = n.fields[e.name];
			(!h(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !S(e.value_schema, t.value))) && _();
		}
	}
	return e;
}
var E = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function D(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let e = n.enum || [!1, !0];
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && _(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && _(), r = Number(t));
	return S(n, r) || _(), r;
}
function O(e, t, n, r) {
	(!r || !m(n) || !Number.isSafeInteger(t) || t < 1) && _();
	let i = y(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && _();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!m(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && _(), t.mode !== "keep" && ((!n.readable || !n.editable || !E(n)) && _("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: D(n, t.value)
		}));
	}
	return a.length || _("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
var k = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function A(e) {
	(!m(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== "management") && _("invalid_context");
	let t = Object.fromEntries(Object.entries(e).filter(([e]) => !k.has(e)));
	x(t);
	let n = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && _("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) n(t);
	};
	n(t);
	let r = (e) => Array.isArray(e) ? e.map(r) : m(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, r(e[t])])) : e;
	return JSON.stringify(r(t));
}
function j(e, t, n) {
	let i = null, a = null, o = !1, s = !1, c = !0, l = 0, u = null, d = !1, f = null, m = null, g = p(e), b = /* @__PURE__ */ new Map(), x = /* @__PURE__ */ new Map(), S = e.getElementById("status"), C = (e) => !s && o && l === e, D = (e) => e.readable && e.editable && E(e), k = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function j(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (s || !o || e.activeElement !== t);
	}
	function M() {
		j(e.getElementById("refresh"), s || !o || m !== null);
		let t = !s && o && !c && a !== null && Object.keys(a).length > 0 && m === null;
		j(e.getElementById("rollback"), !t), j(e.getElementById("recover"), !t || i.fields.some((e) => e.readable && !e.editable)), e.getElementById("replacement").disabled = k(e.getElementById("recover"));
		for (let [e, n] of b) j(n.save, !t || !i || !y(i, e).some(D));
		for (let e of x.values()) {
			let t = !s && o && D(e.field);
			e.mode.disabled = !t, e.input.disabled = !t || e.mode.value !== "replace";
		}
	}
	async function N(e, n, r) {
		C(r) || _("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return C(r) || _("stale_context"), e === "catalog" ? w(i) : e === "read" ? i : (e === "update" && (!h(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && _("operation_unavailable"), e === "rollback" && (!h(i, ["rolled_back"]) || i.rolled_back !== !0) && _("operation_unavailable"), e === "recover" && (!h(i, ["recovered"]) || i.recovered !== !0) && _("operation_unavailable"), i);
	}
	function P(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function F(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function I(e) {
		let t = b.get(e).save;
		k(t) || z(async (t) => {
			let n = Object.fromEntries(y(i, e).map((e) => {
				let t = x.get(v(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await N("update", O(e, a[e].revision, n, i), t), await R(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = x.get(JSON.stringify([e, t])), i = a[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = P(n.field, i.value));
			}
			S.textContent = "配置已保存；需要恢复时请单独执行恢复运行。";
		});
	}
	async function L() {
		let e = [...new Set(i.fields.map((e) => e.module_id))], t = new Set(i.fields.map(v));
		for (let e of x.keys()) t.has(e) || x.delete(e);
		for (let t of b.keys()) e.includes(t) || b.delete(t);
		let n = [];
		for (let t of e) {
			let e = b.get(t);
			e || (e = {
				target: t,
				revision: "",
				controls: [],
				save: null,
				onSave: () => I(t)
			}, b.set(t, e)), e.revision = a[t] ? `配置版本：${a[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of y(i, t)) {
				let i = v(r), o = JSON.stringify(r), s = x.get(i);
				(!s || s.signature !== o) && (s = F(r), x.set(i, s));
				let c = r.readable ? a[t].fields[r.name] : null, l = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				c ? l.push(c.state === "valid" ? "有效" : "无效存量，原值不显示", c.present ? "已有显式值" : "未设置", c.source === "sqlite" ? "Core 配置" : "默认值") : l.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && l.push("模块语义校验器不可用，禁止修改与清除"), E(r) || l.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), s.detail = l.join(" · "), E(r) ? (!s.mode || s.mode.value === "keep") && n.push([
					s,
					c ? P(r, c.value) : "",
					s.mode?.value,
					s.input?.value,
					s.draftVersion
				]) : n.push([
					s,
					c?.state === "valid" ? JSON.stringify(c.value) : "",
					s.mode?.value,
					s.input?.value,
					s.draftVersion
				]), e.controls.push(s);
			}
		}
		g.fields([...b.values()], M), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		M();
	}
	async function R(e) {
		c = !0, M();
		let t = await N("catalog", {}, e), n = T(await N("read", {}, e), t);
		C(e) || _("stale_context"), i = t, a = n, c = !1, await L(), C(e) || _("stale_context");
	}
	async function z(e) {
		if (m || s || !o) return;
		let t = { generation: l };
		m = t, M(), S.textContent = "管理操作正在执行。";
		try {
			await e(t.generation);
		} catch (e) {
			if (!C(t.generation)) return;
			e?.message === "revision_conflict" ? (c = !0, S.textContent = "配置已变化，请刷新并重新核对后修改。") : e?.message === "select_changes" ? S.textContent = "请选择需要修改的字段。" : e?.message === "invalid_config" ? S.textContent = "配置或输入未通过校验，请核对字段要求后刷新重试。" : e?.message === "admin_authorization_denied" ? (c = !0, S.textContent = "管理授权不可用，请重新打开宿主管理页面后手动刷新。") : (c = !0, S.textContent = "未取得成功确认，请刷新核对配置与版本后重试。");
		} finally {
			m === t && (m = null, M());
		}
	}
	let B = () => Object.fromEntries(Object.entries(a).map(([e, t]) => [e, t.revision]));
	e.getElementById("refresh").addEventListener("click", () => !k(e.getElementById("refresh")) && z(async (e) => {
		await R(e), S.textContent = "已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。";
	})), e.getElementById("rollback").addEventListener("click", () => !k(e.getElementById("rollback")) && z(async (e) => {
		await N("rollback", { expected_revisions: B() }, e), await R(e), S.textContent = "四字段迁移已限定回退，其他数据保留。";
	})), e.getElementById("recover").addEventListener("click", () => !k(e.getElementById("recover")) && z(async (t) => {
		await N("recover", {
			expected_revisions: B(),
			complete_from_current: e.getElementById("replacement").checked
		}, t), await R(t), S.textContent = "配置已重新校验，业务运行已恢复。";
	}));
	function V(t) {
		if (s) return;
		g.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = A(t);
		} catch {
			d = !0, l++, o = !1, c = !0, m = null, u = null, i = null, a = null, g.fields([], M), b.clear(), x.clear(), M(), S.textContent = "宿主管理上下文无效，请重新打开页面。";
			return;
		}
		d = !0, !(o && n === u) && (l++, u = n, o = !0, c = !0, m = null, i = null, a = null, g.fields([], M), b.clear(), x.clear(), M(), z(async (e) => {
			await R(e), S.textContent = "已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。";
		}));
	}
	function H() {
		s || (s = !0, l++, o = !1, m = null, i = null, a = null, f?.(), M());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				S.textContent = "请从宿主插件管理页面打开此页。";
				return;
			}
			f = t.onContext(V), Promise.resolve(t.ready()).then((e) => {
				d || V(e);
			}).catch(() => {
				!d && !s && (o = !1, S.textContent = "宿主管理会话不可用。", M());
			}), n?.addEventListener("pagehide", H, { once: !0 });
		},
		close: H
	};
}
typeof document < "u" && document.getElementById("management-root") && j(document, window.AstrBotPluginPage, window).start();
//#endregion
export { j as createManagementPage, O as updateBody, w as validateCatalog, T as validateSnapshot };
