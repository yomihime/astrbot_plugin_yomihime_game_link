import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NAlert as a, NCard as o, NConfigProvider as s, darkTheme as c, dateEnUS as l, dateZhCN as u, enUS as d, zhCN as f } from "./runtime.js";
//#region src/management-view.ts
function p(r, p = r.getElementById("management-root")) {
	let m = i([]), h = i({}), g = () => {}, _ = i([]), v = i(""), y = i(null), b = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), x = t({ setup() {
		return () => {
			let e = h.value.isDark === !0 || h.value.theme === "dark", t = String(h.value.locale || "").startsWith("en");
			return n(s, {
				theme: e ? c : null,
				locale: t ? d : f,
				dateLocale: t ? l : u,
				inlineThemeDisabled: !0
			}, { default: () => n("main", { class: "management" }, [
				n("p", { class: "brand" }, "如月怜的游戏连结"),
				n("h1", "配置目录与管理"),
				n("p", "目录来自可信 Core 与模块声明。普通配置与来源凭据分别限定授权；字段注册不授予存量读取或修改权限，写操作仍由后端逐请求鉴权。"),
				n("p", "保存只修改所选配置。客户端检查用于辅助输入，模块语义校验与配置版本以服务端为准。启动失败时，修复后需单独点击恢复运行。"),
				n(a, {
					type: "info",
					showIcon: !1
				}, { default: () => n("p", {
					id: "status",
					role: "status",
					"aria-live": "polite"
				}, "正在连接宿主管理页面。") }),
				b("refresh", "刷新配置与版本"),
				n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, m.value.map((e) => n("section", {
					class: "card",
					key: e.target,
					"data-owner": e.target,
					hidden: y.value !== null && y.value !== e.target
				}, [n(o, { title: e.target }, { default: () => [
					n("p", e.revision),
					n("div", e.controls.map((t, r) => {
						let i = t.field, a = i.value_schema, o = `config-${encodeURIComponent(i.module_id)}-${encodeURIComponent(i.name)}`, s = a?.enum || (a?.type === "boolean" ? [!1, !0] : null);
						return n("div", {
							class: "config-field",
							key: t.signature,
							"data-field": i.name,
							"data-owner": i.module_id
						}, [
							r === 0 || i.group !== e.controls[r - 1].field.group ? n("h3", {
								class: "config-group",
								"data-group": i.group || "general"
							}, i.group || "常规") : null,
							n("label", { for: o }, i.description || i.name),
							n("p", {
								class: "field-detail",
								id: o + "-detail"
							}, t.detail),
							n("select", {
								"data-role": "mode",
								"aria-label": `${i.description || i.name}修改方式`,
								ref: (e) => {
									t.mode = e;
								},
								onChange: () => {
									t.draftVersion++, g();
								}
							}, [
								["keep", "保持"],
								["replace", "替换 / 修复"],
								["clear", "清除显式值"]
							].map(([e, t]) => n("option", { value: e }, t))),
							n(s ? "select" : "input", {
								id: o,
								"data-role": "value",
								"aria-describedby": o + "-detail",
								onInput: () => {
									t.draftVersion++;
								},
								onChange: () => {
									t.draftVersion++;
								},
								ref: (e) => {
									t.input = e;
								},
								...s ? {} : {
									type: ["integer", "number"].includes(a?.type || "") ? "number" : "text",
									min: a?.minimum,
									max: a?.maximum,
									step: a?.type === "integer" ? "1" : "any",
									maxlength: a?.maxLength
								}
							}, s ? [n("option", {
								value: "",
								disabled: !0
							}, "请选择有效值"), ...s.map((e, t) => n("option", { value: String(t) }, String(e)))] : void 0)
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
				n("p", {
					id: "credential-status",
					role: "status",
					"aria-live": "polite"
				}, v.value),
				n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, _.value.map((e) => {
					let t = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, r = () => {
						e.draftVersion++;
					};
					return n(o, {
						class: "card",
						key: e.signature,
						title: e.field.description,
						"data-credential": e.field.name,
						"data-owner": e.field.module_id,
						hidden: y.value !== null && y.value !== e.field.module_id
					}, { default: () => [
						n("p", `${e.field.module_id} · ${e.detail}`),
						n("p", "只显示保存状态，不回读 Client ID / Client Secret。保存不代表上游可连通；业务查询请从所属模块页面手动发起。"),
						n("label", { for: t + "-mode" }, "本次操作"),
						n("select", {
							id: t + "-mode",
							"data-role": "credential-mode",
							ref: (t) => {
								e.mode = t;
							},
							onChange: () => {
								r(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), g();
							}
						}, [
							["keep", "保持（不写入）"],
							["replace", "替换完整凭据对"],
							["clear", "清除已保存凭据"]
						].map(([e, t]) => n("option", { value: e }, t))),
						...["client_id", "client_secret"].map((i) => n("div", { key: i }, [n("label", { for: t + "-" + i }, i === "client_id" ? "Client ID" : "Client Secret"), n("input", {
							id: t + "-" + i,
							type: "password",
							autocomplete: "off",
							maxlength: 512,
							"data-role": i,
							ref: (t) => {
								i === "client_id" ? e.clientId = t : e.clientSecret = t;
							},
							onInput: r,
							onChange: r
						})])),
						n("label", [n("input", {
							type: "checkbox",
							"data-role": "credential-clear-confirm",
							ref: (t) => {
								e.confirm = t;
							},
							onChange: r
						}), "确认清除本组已保存凭据"]),
						n("button", {
							type: "button",
							"data-action": "credential-save",
							disabled: !0,
							ref: (t) => {
								e.save = t;
							},
							onClick: e.onSave
						}, "提交本组操作")
					] });
				})),
				n("details", { class: "advanced-maintenance" }, [n("summary", "高级维护与启动修复"), n(o, {
					class: "card",
					title: "限定恢复"
				}, { default: () => [
					n("p", "回退仅处理受审普通配置迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。"),
					b("rollback", "回退受审迁移"),
					n("p", n("label", [n("input", {
						id: "replacement",
						type: "checkbox"
					}), "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）"])),
					b("recover", "重新校验并恢复运行")
				] })])
			]) });
		};
	} }), S = e(x);
	return S.mount(p), {
		owner(e) {
			y.value = e;
		},
		fields(e, t) {
			g = t, m.value = e;
		},
		credentials(e, t, n = "") {
			g = t, _.value = e, v.value = n;
		},
		context(e) {
			h.value = e || {};
		},
		dispose() {
			S.unmount();
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
function k(e) {
	x(e), (!h(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && _();
	let t = /* @__PURE__ */ new Set(), n = {
		type: "object",
		properties: {
			client_id: {
				type: "string",
				minLength: 1,
				maxLength: 512
			},
			client_secret: {
				type: "string",
				minLength: 1,
				maxLength: 512
			}
		},
		required: ["client_id", "client_secret"],
		additionalProperties: !1
	};
	for (let r of e.fields) {
		(!h(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => g.test(e)) || typeof r.name != "string" || !g.test(r.name) || typeof r.group != "string" || !g.test(r.group) || typeof r.description != "string" || !r.description || t.has(v(r))) && _(), t.add(v(r)), (!h(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !h(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && _();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!h(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && _();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function A(e, t) {
	x(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	h(e, n) || _();
	for (let r of n) {
		let n = e[r];
		(!h(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !h(n.fields, y(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && _();
	}
	return e;
}
function j(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && _(), r === "replace" && (!h(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || b(e) < 1 || b(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && _(), {
		module_id: e,
		expected_revision: t,
		updates: [r === "replace" ? {
			field: n,
			mode: r,
			value: i
		} : {
			field: n,
			mode: r
		}]
	};
}
var M = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function N(e, t) {
	(!m(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && _("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !M.has(e)));
	x(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && _("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : m(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function P(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root") } = {}) {
	a || _("invalid_container");
	let o = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, s = null, c = null, l = null, u = null, d = !1, f = !1, m = !0, g = 0, b = null, x = !1, S = null, C = null, D = p(e, a), M = /* @__PURE__ */ new Map(), P = /* @__PURE__ */ new Map(), F = o("status"), I = /* @__PURE__ */ new Map(), L = !1, R = null, z = null, B = (e) => !f && d && g === e, V = (e) => e.readable && e.editable && E(e), H = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function U(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (f || !d || e.activeElement !== t);
	}
	function W() {
		U(o("refresh"), f || !d || C !== null);
		let e = !f && d && !m && c !== null && Object.keys(c).length > 0 && C === null;
		U(o("rollback"), !e), U(o("recover"), !e || s.fields.some((e) => e.readable && !e.editable)), o("replacement").disabled = H(o("recover"));
		for (let [t, n] of M) U(n.save, !e || !s || !y(s, t).some(V));
		for (let e of P.values()) {
			let t = !f && d && V(e.field);
			e.mode.disabled = !t, e.input.disabled = !t || e.mode.value !== "replace";
		}
		for (let e of I.values()) {
			let t = !f && d && (z === null || z === e.field.module_id) && u !== null && l !== null && l.fields.some((t) => v(t) === v(e.field) && JSON.stringify(t) === e.signature);
			e.save && U(e.save, !t || m || C !== null || e.mode?.value === "replace" && !L), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !L);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !L || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function G(e, n, r) {
		B(r) || _("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return B(r) || _("stale_context"), e === "catalog" ? w(i) : e === "read" ? i : e === "credential-catalog" ? k(i) : e === "credential-readiness" ? ((!h(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && _("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!h(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && _("operation_unavailable"), e === "rollback" && (!h(i, ["rolled_back"]) || i.rolled_back !== !0) && _("operation_unavailable"), e === "recover" && (!h(i, ["recovered"]) || i.recovered !== !0) && _("operation_unavailable"), i);
	}
	function K(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function q(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function ee(e) {
		let t = M.get(e).save;
		H(t) || X(async (t) => {
			let n = Object.fromEntries(y(s, e).map((e) => {
				let t = P.get(v(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await G("update", O(e, c[e].revision, n, s), t), await Y(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = P.get(JSON.stringify([e, t])), i = c[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = K(n.field, i.value));
			}
			F.textContent = "配置已保存；需要恢复时请单独执行恢复运行。";
		});
	}
	function J() {
		for (let e of I.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
	}
	function te(e) {
		H(e.save) || e.mode.value === "replace" && !L || X(async (t) => {
			let n = e.mode.value;
			n === "keep" && _("select_changes"), n === "clear" && !e.confirm.checked && _("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await G("credential-update", j(e.field.module_id, u[e.field.module_id].revision, e.field.name, n, i, l), t), await Y(t), e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), W(), F.textContent = n === "clear" ? "已清除所选来源凭据；业务查询需要重新配置该组凭据。" : "来源凭据已加密保存；尚未验证所属上游。";
		});
	}
	async function ne() {
		let e = new Set(l.fields.map(v));
		for (let [t, n] of I) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			I.delete(t);
		}
		for (let e of l.fields) {
			let t = v(e), n = JSON.stringify(e), r = I.get(t);
			if (!r || r.signature !== n) {
				if (r) for (let e of [r.clientId, r.clientSecret]) e && (e.value = "");
				r = {
					field: e,
					signature: n,
					draftVersion: 0,
					mode: null,
					clientId: null,
					clientSecret: null,
					confirm: null,
					save: null,
					detail: "",
					onSave: null
				}, r.onSave = () => te(r), I.set(t, r);
			}
			let i = u[e.module_id].fields[e.name];
			r.detail = (i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用") + ` · 配置版本：${u[e.module_id].revision}`;
		}
		D.credentials([...I.values()], W), await r(), W();
	}
	async function re() {
		let e = [...new Set(s.fields.map((e) => e.module_id))], t = new Set(s.fields.map(v));
		for (let e of P.keys()) t.has(e) || P.delete(e);
		for (let t of M.keys()) e.includes(t) || M.delete(t);
		let n = [];
		for (let t of e) {
			let e = M.get(t);
			e || (e = {
				target: t,
				revision: "",
				controls: [],
				save: null,
				onSave: () => ee(t)
			}, M.set(t, e)), e.revision = c[t] ? `配置版本：${c[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of y(s, t)) {
				let i = v(r), a = JSON.stringify(r), o = P.get(i);
				(!o || o.signature !== a) && (o = q(r), P.set(i, o));
				let s = r.readable ? c[t].fields[r.name] : null, l = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				s ? l.push(s.state === "valid" ? "有效" : "无效存量，原值不显示", s.present ? "已有显式值" : "未设置", s.source === "sqlite" ? "Core 配置" : "默认值") : l.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && l.push("模块语义校验器不可用，禁止修改与清除"), E(r) || l.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), o.detail = l.join(" · "), E(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? K(r, s.value) : "",
					o.mode?.value,
					o.input?.value,
					o.draftVersion
				]) : n.push([
					o,
					s?.state === "valid" ? JSON.stringify(s.value) : "",
					o.mode?.value,
					o.input?.value,
					o.draftVersion
				]), e.controls.push(o);
			}
		}
		for (let e of M.values()) e.controls.sort((e, t) => (e.field.group || "").localeCompare(t.field.group || ""));
		D.fields([...M.values()], W), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		W();
	}
	async function Y(e) {
		m = !0, W();
		let t = await G("catalog", {}, e), n = T(await G("read", {}, e), t);
		B(e) || _("stale_context"), s = t, c = n;
		let i = "";
		try {
			let t = await G("credential-catalog", {}, e), n = A(await G("credential-status", {}, e), t);
			B(e) || _("stale_context"), l = t, u = n, L = !1;
			try {
				L = (await G("credential-readiness", {}, e)).ready, i = L ? "加密设施预检已通过；旧密文与上游授权尚未验证。" : "加密设施未就绪：禁止填写和替换；已授权的清除仍需确认。请管理员配置当前部署的外部密钥（默认 YGL_SECRET_KEY），再刷新；此页不会初始化或轮换密钥。";
			} catch {
				B(e) || _("stale_context"), i = "无法确认加密设施：禁止填写和替换；已授权的清除仍需确认。请管理员检查当前密钥 provider / codec 后刷新。";
			}
			L || J();
		} catch (t) {
			if (!B(e)) throw t;
			t?.message === "admin_authorization_denied" && J(), l = null, u = null, i = t?.message === "admin_authorization_denied" ? "来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。" : "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		m = !1, await re(), l ? (await ne(), D.credentials([...I.values()], W, i), await r(), W()) : D.credentials([...I.values()], W, i), B(e) || _("stale_context");
	}
	async function X(e) {
		if (C || f || !d) return;
		let t = { generation: g };
		C = t, W(), F.textContent = "管理操作正在执行。";
		try {
			await e(t.generation);
		} catch (e) {
			if (!B(t.generation)) return;
			e?.message === "revision_conflict" ? (m = !0, F.textContent = "配置已变化，请刷新并重新核对后修改。") : e?.message === "select_changes" ? F.textContent = "请选择需要修改的字段。" : e?.message === "confirm_clear" ? F.textContent = "请勾选清除确认后再提交；保持选项不会写入。" : e?.message === "secret_encryption_unavailable" ? F.textContent = "未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。" : e?.message === "invalid_config" ? F.textContent = "配置或输入未通过校验，请核对字段要求后刷新重试。" : e?.message === "admin_authorization_denied" ? (J(), l = null, u = null, m = !0, F.textContent = "管理授权不可用，请重新打开宿主管理页面后手动刷新。") : (m = !0, F.textContent = "未取得成功确认，请刷新核对配置与版本后重试。");
		} finally {
			C === t && (C = null, W());
		}
	}
	let Z = () => Object.fromEntries(Object.entries(c).map(([e, t]) => [e, t.revision]));
	o("refresh").addEventListener("click", () => !H(o("refresh")) && X(async (e) => {
		await Y(e), F.textContent = "已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。";
	})), o("rollback").addEventListener("click", () => !H(o("rollback")) && X(async (e) => {
		await G("rollback", { expected_revisions: Z() }, e), await Y(e), F.textContent = "受审迁移已限定回退，其他数据保留。";
	})), o("recover").addEventListener("click", () => !H(o("recover")) && X(async (e) => {
		await G("recover", {
			expected_revisions: Z(),
			complete_from_current: o("replacement").checked
		}, e), await Y(e), F.textContent = "配置已重新校验，业务运行已恢复。";
	}));
	function Q(t) {
		if (f) return;
		D.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = N(t, i);
		} catch {
			J(), x = !0, g++, d = !1, m = !0, C = null, b = null, s = null, c = null, l = null, u = null, D.fields([], W), D.credentials([], W), M.clear(), P.clear(), I.clear(), W(), F.textContent = "宿主管理上下文无效，请重新打开页面。";
			return;
		}
		x = !0, !(d && n === b) && (J(), g++, b = n, d = !0, m = !0, C = null, s = null, c = null, l = null, u = null, D.fields([], W), D.credentials([], W), M.clear(), P.clear(), I.clear(), W(), X(async (e) => {
			await Y(e), F.textContent = "已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。";
		}));
	}
	let ie = () => [...P.values()].some((e) => e.mode?.value !== "keep") || [...I.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function ae() {
		let t = e.activeElement;
		R = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, J(), g++, C = null, m = !0, W();
	}
	function oe() {
		!f && d && X(async (e) => {
			if (await Y(e), F.textContent = "已读取配置目录与授权范围内的值；写操作仍逐请求鉴权。", R) {
				let e = o(R.id);
				if (e?.focus({ preventScroll: !0 }), e && R.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(R.start, R.end);
				} catch {}
				R = null;
			}
		});
	}
	function $() {
		f || (J(), f = !0, g++, d = !1, C = null, s = null, c = null, l = null, u = null, S?.(), W());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				F.textContent = "请从宿主插件管理页面打开此页。";
				return;
			}
			S = t.onContext(Q), Promise.resolve(t.ready()).then((e) => {
				x || Q(e);
			}).catch(() => {
				!x && !f && (d = !1, F.textContent = "宿主管理会话不可用。", W());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: ie,
		suspend: ae,
		resume: oe,
		selectOwner(e) {
			z = e, D.owner(e), W();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), D.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && P(document, window.AstrBotPluginPage, window).start();
//#endregion
export { P as createManagementPage, j as credentialUpdateBody, O as updateBody, w as validateCatalog, k as validateCredentialCatalog, A as validateCredentialStatus, T as validateSnapshot };
