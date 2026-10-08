import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NAlert as a, NCard as o, NConfigProvider as s, darkTheme as c, dateEnUS as l, dateZhCN as u, enUS as d, zhCN as f } from "./runtime.js";
//#region src/management-view.ts
function p(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, n], r) => ({
		group: e,
		controls: n,
		title: t.size === 1 ? "普通设置" : `设置组 ${r + 1}`
	}));
}
function m(r, m = r.getElementById("management-root")) {
	let h = i([]), g = i({}), _ = () => {}, v = i([]), y = i(""), b = i(null), x = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), S = t({ setup() {
		return () => {
			let e = g.value.isDark === !0 || g.value.theme === "dark", t = String(g.value.locale || "").startsWith("en");
			return n(s, {
				preflightStyleDisabled: !0,
				theme: e ? c : null,
				locale: t ? d : f,
				dateLocale: t ? l : u,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				m.id === "management-root" ? n("h1", "设置") : null,
				n(a, {
					type: "info",
					showIcon: !1
				}, { default: () => n("p", {
					id: "status",
					role: "status",
					"aria-live": "polite"
				}, "正在连接宿主管理页面。") }),
				x("refresh", "刷新配置与版本"),
				n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, h.value.map((e) => n("section", {
					class: "card",
					key: e.target,
					"data-owner": e.target,
					hidden: b.value !== null && b.value !== e.target
				}, [n(o, {}, { default: () => [
					...p(e.controls).map((e) => n("fieldset", {
						class: "config-group",
						"data-group": e.group || "general",
						key: e.group,
						style: {
							border: "0",
							padding: "0",
							minWidth: "0",
							font: "inherit",
							margin: "0 0 24px"
						}
					}, [n("legend", { style: {
						fontSize: "16px",
						fontWeight: "600",
						padding: "0",
						marginBottom: "8px"
					} }, e.title), ...e.controls.map((e) => {
						let t = e.field, r = t.value_schema, i = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, a = r?.enum || (r?.type === "boolean" ? [!1, !0] : null);
						return n("div", {
							class: "config-field",
							key: e.signature,
							"data-field": t.name,
							"data-owner": t.module_id,
							"data-group": t.group || "general"
						}, [
							n("label", { for: i }, t.description || t.name),
							n("input", {
								type: "hidden",
								"data-role": "mode",
								ref: (t) => {
									e.mode = t, e.mode && !e.mode.value && (e.mode.value = "keep");
								},
								onChange: () => {
									e.draftVersion++, _();
								}
							}),
							n(a ? "select" : "input", {
								id: i,
								"data-role": "value",
								"aria-describedby": e.problem ? i + "-detail" : void 0,
								onInput: () => {
									e.mode && (e.mode.value = "replace"), e.draftVersion++, _();
								},
								onChange: () => {
									e.mode && (e.mode.value = "replace"), e.draftVersion++, _();
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
							}, "请选择有效值"), ...a.map((e, t) => n("option", { value: String(t) }, String(e)))] : void 0),
							e.problem ? n("p", {
								class: "field-detail",
								id: i + "-detail",
								role: "status"
							}, e.problem) : null,
							n("button", {
								type: "button",
								"data-action": "default",
								ref: (t) => {
									e.reset = t;
								},
								onClick: () => {
									if (!e.mode || !e.input || e.reset?.disabled) return;
									e.mode.value = "clear", e.draftVersion++;
									let n = t.default;
									e.input.value = a ? String(a.findIndex((e) => e === n)) : n === null ? "" : String(n), _();
								}
							}, "恢复默认")
						]);
					})])),
					n("div", { class: "form-actions" }, [n("button", {
						type: "button",
						"data-action": "save",
						disabled: !0,
						ref: (t) => {
							e.save = t;
						},
						onClick: e.onSave
					}, "保存"), n("button", {
						type: "button",
						"data-action": "discard",
						ref: (t) => {
							e.discard = t;
						},
						onClick: () => e.onDiscard?.()
					}, "放弃修改")]),
					n("details", { class: "technical-details" }, [
						n("summary", "配置详细信息"),
						n("p", e.revision),
						...e.controls.map((e) => n("p", e.detail))
					])
				] })]))),
				n("p", {
					id: "credential-status",
					role: "status",
					"aria-live": "polite"
				}, y.value),
				n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, v.value.map((e) => {
					let t = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, r = () => {
						e.draftVersion++;
					};
					return n(o, {
						class: "card",
						key: e.signature,
						title: e.field.description.split("；")[0],
						"data-credential": e.field.name,
						"data-owner": e.field.module_id,
						hidden: b.value !== null && b.value !== e.field.module_id
					}, { default: () => [
						n("p", e.detail),
						n("label", { for: t + "-mode" }, "本次操作"),
						n("select", {
							id: t + "-mode",
							"data-role": "credential-mode",
							ref: (t) => {
								e.mode = t;
							},
							onChange: () => {
								r(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), _();
							}
						}, [
							["keep", "保持（不写入）"],
							["replace", "替换完整凭据对"],
							["clear", "清除已保存凭据"]
						].map(([e, t]) => n("option", { value: e }, t))),
						...["client_id", "client_secret"].map((i) => n("div", {
							class: "credential-field",
							key: i
						}, [n("label", { for: t + "-" + i }, i === "client_id" ? "Client ID" : "Client Secret"), n("input", {
							id: t + "-" + i,
							type: "password",
							autocomplete: "off",
							maxlength: 512,
							"data-role": i,
							ref: (t) => {
								i === "client_id" ? e.clientId = t : e.clientSecret = t;
							},
							onInput: () => {
								r(), _();
							},
							onChange: () => {
								r(), _();
							}
						})])),
						n("label", [n("input", {
							type: "checkbox",
							"data-role": "credential-clear-confirm",
							ref: (t) => {
								e.confirm = t;
							},
							onChange: () => {
								r(), _();
							}
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
					n("p", "恢复默认仅作用于指定配置字段。缓存清理和持久业务数据删除尚无通用范围合同，当前不提供执行入口。解除挂载始终保留数据。"),
					n("p", "回退仅处理受审普通配置迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。"),
					x("rollback", "回退受审迁移"),
					n("p", n("label", [n("input", {
						id: "replacement",
						type: "checkbox"
					}), "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）"])),
					x("recover", "重新校验并恢复运行")
				] })])
			]) });
		};
	} }), C = e(S);
	return C.mount(m), {
		owner(e) {
			b.value = e;
		},
		fields(e, t) {
			_ = t, h.value = e;
		},
		credentials(e, t, n = "") {
			_ = t, v.value = e, y.value = n;
		},
		context(e) {
			g.value = e || {};
		},
		dispose() {
			C.unmount();
		}
	};
}
//#endregion
//#region src/management.js
var h = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), g = (e, t) => h(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), _ = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, v = (e = "invalid_config") => {
	throw Error(e);
}, y = (e) => JSON.stringify([e.module_id, e.name]), b = (e, t) => e.fields.filter((e) => e.module_id === t), x = (e) => [...e].length;
function S(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && v(), typeof e == "string") {
			x(e) > 4096 && v();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!h(e) && !Array.isArray(e) || r.has(e)) && v(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && v();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && v();
}
function C(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || x(t) >= e.minLength) && (e.maxLength === void 0 || x(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => C(e.items, t)) : e.type === "object" && h(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && C(e.properties[t], n));
}
function w(e, t = 0) {
	(!h(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && v();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && v(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !h(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && v();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || v(), w(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && v();
	} else if (e.type === "array") n.push("items"), w(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && v();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && v(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => C({
			...e,
			enum: void 0
		}, t))) && v();
	}
	Object.keys(e).some((e) => !n.includes(e)) && v();
}
function ee(e) {
	S(e), (!g(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && v();
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
	for (let r of e.fields) (!g(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => _.test(e)) || typeof r.name != "string" || !r.name || x(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && _.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && v(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && v(), n.has(y(r)) && v(), n.add(y(r)), r.value_schema !== null && (S(r.value_schema, 16, 2048), w(r.value_schema), r.default !== null && !C(r.value_schema, r.default) && v());
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
	t || v(), S(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	g(e, n) || v();
	for (let r of n) {
		let n = e[r], i = b(t, r).filter((e) => e.readable);
		(!g(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !g(n.fields, i.map((e) => e.name))) && v();
		for (let e of i) {
			let t = n.fields[e.name];
			(!g(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !C(e.value_schema, t.value))) && v();
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
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && v(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && v(), r = Number(t));
	return C(n, r) || v(), r;
}
function O(e, t, n, r) {
	(!r || !h(n) || !Number.isSafeInteger(t) || t < 1) && v();
	let i = b(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && v();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!h(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && v(), t.mode !== "keep" && ((!n.readable || !n.editable || !E(n)) && v("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: D(n, t.value)
		}));
	}
	return a.length || v("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function k(e) {
	S(e), (!g(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && v();
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
		(!g(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => _.test(e)) || typeof r.name != "string" || !_.test(r.name) || typeof r.group != "string" || !_.test(r.group) || typeof r.description != "string" || !r.description || t.has(y(r))) && v(), t.add(y(r)), (!g(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !g(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && v();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!g(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && v();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function A(e, t) {
	S(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	g(e, n) || v();
	for (let r of n) {
		let n = e[r];
		(!g(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !g(n.fields, b(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && v();
	}
	return e;
}
function j(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && v(), r === "replace" && (!g(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || x(e) < 1 || x(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && v(), {
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
function te(e, t) {
	(!h(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && v("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !M.has(e)));
	S(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && v("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : h(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function N(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), onDraftMutation: o = () => {}, onConfigurationSaved: s = () => {} } = {}) {
	a || v("invalid_container");
	let c = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, l = null, u = null, d = null, f = null, p = !1, h = !1, _ = !0, x = 0, S = null, C = !1, w = null, D = null, M = m(e, a), N = /* @__PURE__ */ new Map(), P = /* @__PURE__ */ new Map(), F = c("status"), I = /* @__PURE__ */ new Map(), L = !1, R = null, z = null, B = "[[],[]]";
	function V() {
		let e = JSON.stringify([[...P].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
			e,
			t.mode.value,
			t.input?.value
		]), [...I].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
			e,
			t.mode?.value,
			t.clientId?.value,
			t.clientSecret?.value,
			t.confirm?.checked
		])]);
		e !== B && (B = e, o()), K();
	}
	let H = (e) => !h && p && x === e, U = (e) => e.readable && e.editable && E(e), W = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function G(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (h || !p || e.activeElement !== t);
	}
	function K() {
		G(c("refresh"), h || !p || D !== null);
		let e = !h && p && !_ && u !== null && Object.keys(u).length > 0 && D === null;
		G(c("rollback"), !e), G(c("recover"), !e || l.fields.some((e) => e.readable && !e.editable)), c("replacement").disabled = W(c("recover"));
		for (let [t, n] of N) {
			let r = z === null || z === t;
			G(n.save, !e || !r || !l || !b(l, t).some(U)), n.discard && G(n.discard, !p || h || D !== null || !r);
		}
		for (let e of P.values()) {
			let t = !h && p && (z === null || z === e.field.module_id) && U(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of I.values()) {
			let t = !h && p && (z === null || z === e.field.module_id) && f !== null && d !== null && d.fields.some((t) => y(t) === y(e.field) && JSON.stringify(t) === e.signature);
			e.save && G(e.save, !t || _ || D !== null || e.mode?.value === "replace" && !L), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !L);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !L || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function q(e, n, r) {
		H(r) || v("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return H(r) || v("stale_context"), e === "catalog" ? ee(i) : e === "read" ? i : e === "credential-catalog" ? k(i) : e === "credential-readiness" ? ((!g(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && v("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!g(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && v("operation_unavailable"), e === "rollback" && (!g(i, ["rolled_back"]) || i.rolled_back !== !0) && v("operation_unavailable"), e === "recover" && (!g(i, ["recovered"]) || i.recovered !== !0) && v("operation_unavailable"), i);
	}
	function J(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function ne(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function re(e) {
		let t = N.get(e).save;
		W(t) || Z(async (t) => {
			let n = Object.fromEntries(b(l, e).map((e) => {
				let t = P.get(y(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await q("update", O(e, u[e].revision, n, l), t), s(), await X(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = P.get(JSON.stringify([e, t])), i = u[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = J(n.field, i.value));
			}
			V(), F.textContent = "设置已保存。";
		});
	}
	function ie(e) {
		if (!h && p && D === null && u?.[e]) {
			for (let t of b(l, e)) {
				let n = P.get(y(t)), r = u[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = J(t, r.value));
			}
			for (let t of I.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			V(), F.textContent = "已放弃修改。";
		}
	}
	function Y() {
		for (let e of I.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		V();
	}
	function ae(e) {
		W(e.save) || e.mode.value === "replace" && !L || Z(async (t) => {
			let n = e.mode.value;
			n === "keep" && v("select_changes"), n === "clear" && !e.confirm.checked && v("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await q("credential-update", j(e.field.module_id, f[e.field.module_id].revision, e.field.name, n, i, d), t), await X(t), e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), V(), F.textContent = n === "clear" ? "已清除所选来源凭据；业务查询需要重新配置该组凭据。" : "来源凭据已加密保存；尚未验证所属上游。";
		});
	}
	async function oe() {
		let e = new Set(d.fields.map(y));
		for (let [t, n] of I) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			I.delete(t);
		}
		for (let e of d.fields) {
			let t = y(e), n = JSON.stringify(e), r = I.get(t);
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
				}, r.onSave = () => ae(r), I.set(t, r);
			}
			let i = f[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		M.credentials([...I.values()], V), await r(), K();
	}
	async function se() {
		let e = [...new Set(l.fields.map((e) => e.module_id))], t = new Set(l.fields.map(y));
		for (let e of P.keys()) t.has(e) || P.delete(e);
		for (let t of N.keys()) e.includes(t) || N.delete(t);
		let n = [];
		for (let t of e) {
			let e = N.get(t);
			e || (e = {
				target: t,
				revision: "",
				controls: [],
				save: null,
				discard: null,
				onSave: () => re(t),
				onDiscard: () => ie(t)
			}, N.set(t, e)), e.revision = u[t] ? `配置版本：${u[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of b(l, t)) {
				let i = y(r), a = JSON.stringify(r), o = P.get(i);
				(!o || o.signature !== a) && (o = ne(r), P.set(i, o));
				let s = r.readable ? u[t].fields[r.name] : null, c = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				s ? c.push(s.state === "valid" ? "有效" : "无效存量，原值不显示", s.present ? "已有显式值" : "未设置", s.source === "sqlite" ? "Core 配置" : "默认值") : c.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && c.push("模块语义校验器不可用，禁止修改与清除"), E(r) || c.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), o.detail = c.join(" · "), o.problem = r.readable ? r.editable ? E(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前不可修改，请核对模块状态。" : "无存量读取与修改授权", E(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? J(r, s.value) : "",
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
		M.fields([...N.values()], V), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		K();
	}
	async function X(e) {
		_ = !0, K();
		let t = await q("catalog", {}, e), n = T(await q("read", {}, e), t);
		H(e) || v("stale_context"), l = t, u = n;
		let i = "";
		try {
			let t = await q("credential-catalog", {}, e), n = A(await q("credential-status", {}, e), t);
			H(e) || v("stale_context"), d = t, f = n, L = !1;
			try {
				L = (await q("credential-readiness", {}, e)).ready, i = L ? "" : "加密未就绪，暂不能填写凭据。请管理员配置密钥后刷新。";
			} catch {
				H(e) || v("stale_context"), i = "未能确认加密状态，暂不能填写凭据。请刷新核对。";
			}
			L || Y();
		} catch (t) {
			if (!H(e)) throw t;
			t?.message === "admin_authorization_denied" && Y(), d = null, f = null, i = t?.message === "admin_authorization_denied" ? "来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。" : "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		_ = !1, await se(), d ? (await oe(), M.credentials([...I.values()], V, i), await r(), K()) : M.credentials([...I.values()], V, i), H(e) || v("stale_context");
	}
	async function Z(e) {
		if (D || h || !p) return;
		let t = { generation: x };
		D = t, K(), F.textContent = "管理操作正在执行。";
		try {
			await e(t.generation);
		} catch (e) {
			if (!H(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (_ = !0, F.textContent = "配置已变化，请刷新并重新核对后修改。") : e?.message === "select_changes" ? F.textContent = "请选择需要修改的字段。" : e?.message === "confirm_clear" ? F.textContent = "请勾选清除确认后再提交；保持选项不会写入。" : e?.message === "secret_encryption_unavailable" ? F.textContent = "未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。" : e?.message === "invalid_config" ? F.textContent = "配置或输入未通过校验，请核对字段要求后刷新重试。" : e?.message === "admin_authorization_denied" ? (Y(), d = null, f = null, _ = !0, F.textContent = "管理授权不可用，请重新打开宿主管理页面后手动刷新。") : (_ = !0, F.textContent = "未取得成功确认，请刷新核对配置与版本后重试。");
		} finally {
			D === t && (D = null, K());
		}
	}
	let Q = () => Object.fromEntries(Object.entries(u).map(([e, t]) => [e, t.revision]));
	c("refresh").addEventListener("click", () => !W(c("refresh")) && Z(async (e) => {
		await X(e), F.textContent = "设置已更新。";
	})), c("rollback").addEventListener("click", () => !W(c("rollback")) && Z(async (e) => {
		await q("rollback", { expected_revisions: Q() }, e), await X(e), F.textContent = "受审迁移已限定回退，其他数据保留。";
	})), c("recover").addEventListener("click", () => !W(c("recover")) && Z(async (e) => {
		await q("recover", {
			expected_revisions: Q(),
			complete_from_current: c("replacement").checked
		}, e), await X(e), F.textContent = "配置已重新校验，业务运行已恢复。";
	}));
	function ce(t) {
		if (h) return;
		M.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = te(t, i);
		} catch {
			Y(), C = !0, x++, p = !1, _ = !0, D = null, S = null, l = null, u = null, d = null, f = null, M.fields([], K), M.credentials([], K), N.clear(), P.clear(), I.clear(), B = "[[],[]]", K(), F.textContent = "宿主管理上下文无效，请重新打开页面。";
			return;
		}
		C = !0, !(p && n === S) && (Y(), x++, S = n, p = !0, _ = !0, D = null, l = null, u = null, d = null, f = null, M.fields([], K), M.credentials([], K), N.clear(), P.clear(), I.clear(), B = "[[],[]]", K(), Z(async (e) => {
			await X(e), F.textContent = "设置已更新。";
		}));
	}
	let le = () => [...P.values()].some((e) => e.mode?.value !== "keep") || [...I.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function ue() {
		let t = e.activeElement;
		R = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, Y(), x++, D = null, _ = !0, K();
	}
	function de() {
		!h && p && Z(async (e) => {
			if (await X(e), F.textContent = "设置已更新。", R) {
				let e = c(R.id);
				if (e?.focus({ preventScroll: !0 }), e && R.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(R.start, R.end);
				} catch {}
				R = null;
			}
		});
	}
	function $() {
		h || (Y(), h = !0, x++, p = !1, D = null, l = null, u = null, d = null, f = null, w?.(), K());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				F.textContent = "请从宿主插件管理页面打开此页。";
				return;
			}
			w = t.onContext(ce), Promise.resolve(t.ready()).then((e) => {
				C || ce(e);
			}).catch(() => {
				!C && !h && (p = !1, F.textContent = "宿主管理会话不可用。", K());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: le,
		suspend: ue,
		resume: de,
		selectOwner(e) {
			z = e, M.owner(e), K();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), M.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && N(document, window.AstrBotPluginPage, window).start();
//#endregion
export { N as createManagementPage, j as credentialUpdateBody, O as updateBody, ee as validateCatalog, k as validateCredentialCatalog, A as validateCredentialStatus, T as validateSnapshot };
