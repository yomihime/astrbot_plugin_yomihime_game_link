import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NConfigProvider as a, darkTheme as o, dateEnUS as s, dateZhCN as ee, enUS as c, zhCN as l } from "./runtime.js";
t({
	props: {
		title: {
			type: String,
			required: !0
		},
		titleId: String
	},
	setup(e, { slots: t }) {
		return () => n("header", { class: "ui-page-header" }, [n("h1", { id: e.titleId }, e.title), t.default?.()]);
	}
});
var u = t({
	props: {
		title: {
			type: String,
			required: !0
		},
		fieldset: Boolean
	},
	setup(e, { slots: t }) {
		return () => n(e.fieldset ? "fieldset" : "section", { class: "ui-settings-group" }, [e.fieldset ? n("legend", { class: "ui-group-legend" }, e.title) : n("header", { class: "ui-group-heading" }, [n("h2", e.title), t.heading?.()]), t.default?.()]);
	}
}), d = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-settings-row" }, t.default?.());
} }), f = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-action-bar" }, t.default?.());
} }), p = t({
	props: { tone: {
		type: String,
		default: "neutral"
	} },
	setup(e, { slots: t }) {
		return () => n("p", {
			class: "ui-status-hint",
			"data-tone": e.tone,
			role: "status",
			"aria-live": "polite"
		}, t.default?.());
	}
}), m = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
	owner: "ff14/ff14",
	saveLabel: "保存普通设置",
	groups: {
		calendar: { title: "日历" },
		fflogs: { title: "FFLogs" },
		"": { title: "订阅" }
	},
	fields: {
		ff14_calendar_default_days: {
			label: "默认天数",
			width: "short",
			order: 1
		},
		ff14_calendar_default_delivery_time: {
			label: "投递时间",
			width: "short",
			input: "time",
			order: 2
		},
		ff14_calendar_default_timezone: {
			label: "时区",
			width: "medium",
			order: 3
		},
		ff14_subscriptions_enabled: {
			label: "订阅推送",
			width: "short",
			booleanLabels: {
				true: "开启",
				false: "关闭"
			},
			reset: "replace-default",
			hint: "暂停保留订阅；恢复仅处理之后的未来窗口。"
		}
	},
	credentials: {
		credential_fflogs_cn: { label: "国服" },
		credential_fflogs_global: { label: "国际服" }
	}
} })).map((e) => [e.owner, e]));
m.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var h = (e) => m.get(e), g = (e) => m.get(e.module_id)?.fields?.[e.name] || {}, _ = (e, t) => m.get(e)?.groups?.[t]?.title || t || "普通设置", v = (e) => m.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name;
//#endregion
//#region src/management-view.ts
function y(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (g(e.field).order || 0) - (g(t.field).order || 0))
	}));
}
function b(r, m = r.getElementById("management-root"), b = "settings") {
	let te = i([]), x = i({}), S = i([]), C = i(""), w = i(null), T = i(0), E = i({}), D = i({}), O = () => {}, k = () => {
		T.value++, O();
	}, A = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), j = (e) => !!(e.mode && e.mode.value !== "keep"), M = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function N(e) {
		let t = e.field, r = t.value_schema, i = g(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = r?.enum || (r?.type === "boolean" ? [!1, !0] : null);
		return n(d, {
			class: "config-field",
			key: e.signature,
			"data-field": t.name,
			"data-owner": t.module_id,
			"data-group": t.group || "general",
			"data-width": i.width || ([
				"integer",
				"number",
				"boolean"
			].includes(r?.type || "") ? "short" : "long")
		}, { default: () => [
			n("label", { for: a }, i.label || t.description || t.name),
			n("input", {
				type: "hidden",
				"data-role": "mode",
				ref: (t) => {
					e.mode = t, e.mode && !e.mode.value && (e.mode.value = "keep");
				},
				onChange: () => {
					e.draftVersion++, k();
				}
			}),
			n(o ? "select" : "input", {
				id: a,
				"data-role": "value",
				"aria-describedby": e.problem ? a + "-detail" : i.hint ? a + "-hint" : void 0,
				onInput: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, k();
				},
				onChange: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, k();
				},
				ref: (t) => {
					e.input = t;
				},
				...o ? {} : {
					type: i.input || (["integer", "number"].includes(r?.type || "") ? "number" : "text"),
					min: r?.minimum,
					max: r?.maximum,
					step: i.input === "time" ? 60 : r?.type === "integer" ? "1" : "any",
					maxlength: r?.maxLength
				}
			}, o ? [n("option", {
				value: "",
				disabled: !0
			}, "请选择有效值"), ...o.map((e, t) => n("option", { value: String(t) }, i.booleanLabels && typeof e == "boolean" ? i.booleanLabels[String(e)] : String(e)))] : void 0),
			n("button", {
				type: "button",
				"data-action": "default",
				ref: (t) => {
					e.reset = t;
				},
				onClick: () => {
					if (!e.mode || !e.input || e.reset?.disabled) return;
					e.mode.value = i.reset === "replace-default" ? "replace" : "clear", e.draftVersion++;
					let n = t.default;
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), k();
				}
			}, "恢复默认"),
			e.problem ? n("p", {
				class: "field-detail",
				id: a + "-detail",
				role: "status"
			}, e.problem) : i.hint ? n("p", {
				class: "field-hint",
				id: a + "-hint"
			}, i.hint) : null
		] });
	}
	function P(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = v(e.field), a = () => {
			e.draftVersion++;
		};
		return n("section", {
			class: "card credential-card",
			key: e.signature,
			"data-credential": e.field.name,
			"data-owner": e.field.module_id,
			hidden: !t,
			"aria-label": i
		}, [
			n(p, { class: "credential-detail" }, { default: () => e.detail }),
			n(d, { "data-width": "medium" }, { default: () => [n("label", { for: r + "-mode" }, "本次操作"), n("select", {
				id: r + "-mode",
				"data-role": "credential-mode",
				ref: (t) => {
					e.mode = t;
				},
				onChange: () => {
					a(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), D.value = {
						...D.value,
						[r + "-client_id"]: !1,
						[r + "-client_secret"]: !1
					}, k();
				}
			}, [
				["keep", "保持（不写入）"],
				["replace", "替换完整凭据对"],
				["clear", "清除已保存凭据"]
			].map(([e, t]) => n("option", { value: e }, t)))] }),
			...["client_id", "client_secret"].map((t) => n(d, {
				class: "credential-field",
				key: t
			}, { default: () => [n("label", { for: r + "-" + t }, t === "client_id" ? "Client ID" : "Client Secret"), n("div", { class: "credential-input ui-control" }, [n("input", {
				id: r + "-" + t,
				type: D.value[r + "-" + t] ? "text" : "password",
				autocomplete: "off",
				maxlength: 512,
				"data-role": t,
				ref: (n) => {
					t === "client_id" ? e.clientId = n : e.clientSecret = n;
				},
				onInput: () => {
					a(), e.confirm && (e.confirm.checked = !1), k();
				},
				onChange: () => {
					a(), e.confirm && (e.confirm.checked = !1), k();
				}
			}), n("button", {
				type: "button",
				"data-action": "reveal",
				"aria-label": (D.value[r + "-" + t] ? "隐藏" : "显示") + "本次输入 " + (t === "client_id" ? "Client ID" : "Client Secret"),
				"aria-pressed": !!D.value[r + "-" + t],
				onClick: () => {
					D.value = {
						...D.value,
						[r + "-" + t]: !D.value[r + "-" + t]
					};
				}
			}, D.value[r + "-" + t] ? "隐藏" : "显示")])] })),
			n("label", {
				class: "ui-check-label credential-confirm",
				hidden: e.mode?.value !== "clear"
			}, [n("input", {
				type: "checkbox",
				"data-role": "credential-clear-confirm",
				ref: (t) => {
					e.confirm = t;
				},
				onChange: () => {
					a(), k();
				}
			}), n("span", "确认清除本组已保存凭据")]),
			n(f, {}, { default: () => [n("button", {
				type: "button",
				"data-action": "credential-save",
				disabled: !0,
				ref: (t) => {
					e.save = t;
				},
				onClick: e.onSave
			}, "保存" + i + "凭据"), M(e) ? n("span", {
				class: "dirty-indicator",
				role: "status"
			}, i + "有未保存修改") : null] })
		]);
	}
	let F = t({ setup() {
		return () => {
			T.value;
			let e = x.value.isDark === !0 || x.value.theme === "dark", t = String(x.value.locale || "").startsWith("en"), r = /* @__PURE__ */ new Map();
			for (let e of S.value) {
				let t = JSON.stringify([e.field.module_id, e.field.group || ""]);
				r.has(t) || r.set(t, []), r.get(t).push(e);
			}
			return n(a, {
				preflightStyleDisabled: !0,
				theme: e ? o : null,
				locale: t ? c : l,
				dateLocale: t ? s : ee,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				m.id === "management-root" ? n("h1", b === "maintenance" ? "配置维护" : "设置") : null,
				n("div", { class: "management-toolbar" }, [n(p, { id: "status" }), A("refresh", "刷新配置与版本")]),
				b === "settings" ? n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, te.value.map((e) => {
					let t = e.controls.filter((e) => g(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: w.value !== null && w.value !== e.target
					}, [
						...y(r).map((t) => n(u, {
							title: _(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => t.items.map(N) })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...y(t).map((t) => n(u, {
							title: _(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(N) }))]) : null,
						n(f, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, h(e.target)?.saveLabel || "保存普通设置"),
							n("button", {
								type: "button",
								"data-action": "discard",
								ref: (t) => {
									e.discard = t;
								},
								onClick: () => e.onDiscard?.()
							}, "放弃本模块修改"),
							e.controls.some(j) ? n("span", {
								class: "dirty-indicator",
								role: "status"
							}, "普通设置有未保存修改") : null
						] })
					]);
				})) : null,
				b === "settings" ? n(p, {
					id: "credential-status",
					tone: "warning",
					hidden: w.value !== null && S.value.length > 0 && !S.value.some((e) => e.field.module_id === w.value)
				}, { default: () => C.value }) : null,
				b === "settings" ? n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === E.value[e]) ? E.value[e] : t[0].field.name;
					return n(u, {
						title: _(t[0].field.module_id, t[0].field.group || ""),
						key: e,
						hidden: w.value !== null && w.value !== t[0].field.module_id
					}, { default: () => [t.length > 1 ? n("nav", {
						class: "credential-tabs",
						"aria-label": "凭据来源"
					}, t.map((t) => n("button", {
						type: "button",
						"aria-pressed": r === t.field.name,
						onClick: () => {
							E.value = {
								...E.value,
								[e]: t.field.name
							};
						}
					}, v(t.field) + (M(t) ? " · 未保存" : "")))) : null, ...t.map((e) => P(e, r === e.field.name))] });
				})) : null,
				b === "maintenance" ? n(u, {
					title: "限定恢复",
					class: "maintenance-panel"
				}, { default: () => [
					n("p", "仅处理受审普通配置迁移；后续修改会阻止回退。订阅及其它持久数据保留。"),
					A("rollback", "回退受审迁移"),
					n("p", n("label", { class: "ui-check-label" }, [n("input", {
						id: "replacement",
						type: "checkbox",
						onChange: k
					}), n("span", "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）")])),
					A("recover", "重新校验并恢复运行")
				] }) : null
			]) });
		};
	} }), I = e(F);
	return I.mount(m), {
		owner(e) {
			w.value = e;
		},
		drafts() {
			T.value++;
		},
		maskSecrets() {
			D.value = {};
		},
		fields(e, t) {
			O = t, te.value = e;
		},
		credentials(e, t, n = "") {
			O = t, S.value = e, C.value = n;
		},
		context(e) {
			x.value = e || {};
		},
		dispose() {
			I.unmount();
		}
	};
}
//#endregion
//#region src/ui/notice.ts
function te(e, t) {
	let n = 0, r = null, i = () => {
		n++, r !== null && e.clearTimeout(r), r = null;
	};
	return {
		show(a, o = "error") {
			if (i(), t(a, o), o === "success") {
				let i = n;
				r = e.setTimeout(() => {
					i === n && (r = null, t("", "neutral"));
				}, 3500);
			}
		},
		clear() {
			i(), t("", "neutral");
		},
		cancel: i
	};
}
//#endregion
//#region src/management.js
var x = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), S = (e, t) => x(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), C = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, w = (e = "invalid_config") => {
	throw Error(e);
}, T = (e) => [
	e?.code,
	e?.error_code,
	e?.message
].some((e) => [
	"admin_authorization_denied",
	"stale_context",
	"page_revoked",
	"asset_revoked"
].includes(e)) || [e?.status, e?.response?.status].some((e) => e === 401 || e === 403), E = (e) => JSON.stringify([e.module_id, e.name]), D = (e, t) => e.fields.filter((e) => e.module_id === t), O = (e) => [...e].length;
function k(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && w(), typeof e == "string") {
			O(e) > 4096 && w();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!x(e) && !Array.isArray(e) || r.has(e)) && w(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && w();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && w();
}
function A(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || O(t) >= e.minLength) && (e.maxLength === void 0 || O(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => A(e.items, t)) : e.type === "object" && x(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && A(e.properties[t], n));
}
function j(e, t = 0) {
	(!x(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && w();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && w(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !x(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && w();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || w(), j(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && w();
	} else if (e.type === "array") n.push("items"), j(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && w();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && w(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => A({
			...e,
			enum: void 0
		}, t))) && w();
	}
	Object.keys(e).some((e) => !n.includes(e)) && w();
}
function M(e) {
	k(e), (!S(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && w();
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
	for (let r of e.fields) (!S(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => C.test(e)) || typeof r.name != "string" || !r.name || O(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && C.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && w(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && w(), n.has(E(r)) && w(), n.add(E(r)), r.value_schema !== null && (k(r.value_schema, 16, 2048), j(r.value_schema), r.default !== null && !A(r.value_schema, r.default) && w());
	let r = JSON.parse(JSON.stringify(e)), i = (e) => {
		if (e && typeof e == "object") {
			for (let t of Object.values(e)) i(t);
			Object.freeze(e);
		}
		return e;
	};
	return i(r);
}
function N(e, t) {
	t || w(), k(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	S(e, n) || w();
	for (let r of n) {
		let n = e[r], i = D(t, r).filter((e) => e.readable);
		(!S(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !S(n.fields, i.map((e) => e.name))) && w();
		for (let e of i) {
			let t = n.fields[e.name];
			(!S(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !A(e.value_schema, t.value))) && w();
		}
	}
	return e;
}
var P = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function F(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let e = n.enum || [!1, !0];
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && w(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && w(), r = Number(t));
	return A(n, r) || w(), r;
}
function I(e, t, n, r) {
	(!r || !x(n) || !Number.isSafeInteger(t) || t < 1) && w();
	let i = D(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && w();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!x(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && w(), t.mode !== "keep" && ((!n.readable || !n.editable || !P(n)) && w("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: F(n, t.value)
		}));
	}
	return a.length || w("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function ne(e) {
	k(e), (!S(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && w();
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
		(!S(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => C.test(e)) || typeof r.name != "string" || !C.test(r.name) || typeof r.group != "string" || !C.test(r.group) || typeof r.description != "string" || !r.description || t.has(E(r))) && w(), t.add(E(r)), (!S(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !S(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && w();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!S(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && w();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function re(e, t) {
	k(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	S(e, n) || w();
	for (let r of n) {
		let n = e[r];
		(!S(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !S(n.fields, D(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && w();
	}
	return e;
}
function ie(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && w(), r === "replace" && (!S(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || O(e) < 1 || O(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && w(), {
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
var L = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function ae(e, t) {
	(!x(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && w("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !L.has(e)));
	k(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && w("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : x(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function R(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), scope: o = "settings", beforeMaintenance: s = async (e) => !1, onDraftMutation: ee = () => {}, onConfigurationSaved: c = () => {}, onCapabilities: l = (e, t = !1) => {} } = {}) {
	a || w("invalid_container");
	let u = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, d = null, f = null, p = null, m = null, h = !1, g = !1, _ = !0, v = 0, y = null, x = !1, C = null, O = null, k = b(e, a, o), A = /* @__PURE__ */ new Map(), j = /* @__PURE__ */ new Map(), F = u("status"), L = /* @__PURE__ */ new Map(), R = !1, z = null, B = null, oe = o === "maintenance" ? "[[],[],false]" : "[[],[]]", V = oe, H = te(n, (e, t) => {
		F.textContent = e, F.dataset.tone = t;
	}), U = (e, t = "error") => H.show(e, t), se = "当前加密不可用，请管理员检查宿主密钥与加密组件；修改外部环境后需正常重启同一宿主，再重开此页。已有凭据时请保留原密钥。";
	function W() {
		let e = JSON.stringify([
			[...j].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
				e,
				t.mode.value,
				t.input?.value
			]),
			[...L].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
				e,
				t.mode?.value,
				t.clientId?.value,
				t.clientSecret?.value,
				t.confirm?.checked
			]),
			...o === "maintenance" ? [!!u("replacement")?.checked] : []
		]);
		e !== V && (V = e, ee()), k.drafts(), J();
	}
	let G = (e) => !g && h && v === e, ce = (e) => e.readable && e.editable && P(e), K = (e) => !e || e.disabled || e.getAttribute("aria-disabled") === "true";
	function q(t, n) {
		t && (t.setAttribute("aria-disabled", String(n)), t.disabled = n && (g || !h || e.activeElement !== t));
	}
	function J() {
		q(u("refresh"), g || !h || O !== null);
		let e = !g && h && !_ && f !== null && Object.keys(f).length > 0 && O === null;
		q(u("rollback"), !e), q(u("recover"), !e || d.fields.some((e) => e.readable && !e.editable)), u("replacement") && (u("replacement").disabled = K(u("recover")));
		for (let [t, n] of A) {
			let r = B === null || B === t;
			q(n.save, !e || !r || !d || !D(d, t).some(ce)), n.discard && q(n.discard, !h || g || O !== null || !r);
		}
		for (let e of j.values()) {
			let t = !g && h && (B === null || B === e.field.module_id) && ce(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of L.values()) {
			let t = !g && h && (B === null || B === e.field.module_id) && m !== null && p !== null && p.fields.some((t) => E(t) === E(e.field) && JSON.stringify(t) === e.signature);
			e.save && q(e.save, !t || _ || O !== null || e.mode?.value === "replace" && !R), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !R);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !R || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function Y(e, n, r) {
		G(r) || w("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return G(r) || w("stale_context"), e === "catalog" ? M(i) : e === "read" ? i : e === "credential-catalog" ? ne(i) : e === "credential-readiness" ? ((!S(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && w("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!S(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && w("operation_unavailable"), e === "rollback" && (!S(i, ["rolled_back"]) || i.rolled_back !== !0) && w("operation_unavailable"), e === "recover" && (!S(i, ["recovered"]) || i.recovered !== !0) && w("operation_unavailable"), i);
	}
	function le(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function ue(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function de(e) {
		let t = A.get(e).save;
		K(t) || Q(async (t) => {
			let n = Object.fromEntries(D(d, e).map((e) => {
				let t = j.get(E(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await Y("update", I(e, f[e].revision, n, d), t), c();
			try {
				await Z(t);
			} catch (e) {
				if (!G(t)) throw e;
				_ = !0, w(T(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = j.get(JSON.stringify([e, t])), i = f[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = le(n.field, i.value));
			}
			W(), U("已保存。", "success");
		});
	}
	function fe(e) {
		if (!g && h && O === null && f?.[e]) {
			for (let t of D(d, e)) {
				let n = j.get(E(t)), r = f[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = le(t, r.value));
			}
			for (let t of L.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			k.maskSecrets(), W(), U("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function X() {
		k.maskSecrets();
		for (let e of L.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		W();
	}
	function pe(e) {
		K(e.save) || e.mode.value === "replace" && !R || Q(async (t) => {
			let n = e.mode.value;
			n === "keep" && w("select_changes"), n === "clear" && !e.confirm.checked && w("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await Y("credential-update", ie(e.field.module_id, m[e.field.module_id].revision, e.field.name, n, i, p), t);
			let a = !1;
			try {
				a = (await Z(t)).credentials;
			} catch (e) {
				if (!G(t)) throw e;
				T(e) && w("write_ack_authorization_denied");
			}
			e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), W(), a || (_ = !0, w("write_ack_readback_unverified")), U("已保存。", "success");
		});
	}
	async function me() {
		let e = new Set(p.fields.map(E));
		for (let [t, n] of L) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			L.delete(t);
		}
		for (let e of p.fields) {
			let t = E(e), n = JSON.stringify(e), r = L.get(t);
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
				}, r.onSave = () => pe(r), L.set(t, r);
			}
			let i = m[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		k.credentials([...L.values()], W), await r(), J();
	}
	async function he() {
		let e = [...new Set(d.fields.map((e) => e.module_id))], t = new Set(d.fields.map(E));
		for (let e of j.keys()) t.has(e) || j.delete(e);
		for (let t of A.keys()) e.includes(t) || A.delete(t);
		let n = [];
		for (let t of e) {
			let e = A.get(t);
			e || (e = {
				target: t,
				controls: [],
				save: null,
				discard: null,
				onSave: () => de(t),
				onDiscard: () => fe(t)
			}, A.set(t, e)), e.controls = [];
			for (let r of D(d, t)) {
				let i = E(r), a = JSON.stringify(r), o = j.get(i);
				(!o || o.signature !== a) && (o = ue(r), j.set(i, o));
				let s = r.readable ? f[t].fields[r.name] : null;
				o.problem = r.readable ? r.editable ? P(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前只读，请核对模块状态。" : "无读取或修改权限。", P(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? le(r, s.value) : "",
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
		k.fields([...A.values()], W), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		J();
	}
	async function Z(e) {
		_ = !0, J();
		let t = await Y("catalog", {}, e), n = N(await Y("read", {}, e), t);
		G(e) || w("stale_context"), d = t, f = n;
		let i = "";
		try {
			let t = await Y("credential-catalog", {}, e), n = re(await Y("credential-status", {}, e), t);
			G(e) || w("stale_context"), p = t, m = n, R = !1;
			try {
				R = (await Y("credential-readiness", {}, e)).ready, i = R ? "" : se;
			} catch (t) {
				if (!G(e) || T(t)) throw t;
				i = "未能确认加密状态，暂不能填写凭据；请核对宿主管理会话与部署后重开此页。";
			}
			R || X();
		} catch (t) {
			if (!G(e)) throw t;
			if (p = null, m = null, T(t)) throw X(), t;
			i = "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		return _ = !1, o === "settings" && (await he(), p ? (await me(), k.credentials([...L.values()], W, i), await r(), J()) : k.credentials([...L.values()], W, i)), G(e) || w("stale_context"), l([...new Set([...d.fields, ...p?.fields || []].map((e) => e.module_id))], p !== null), { credentials: p !== null };
	}
	async function Q(e) {
		if (O || g || !h) return;
		let t = { generation: v };
		O = t, J();
		try {
			await e(t.generation);
		} catch (e) {
			if (!G(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (_ = !0, U("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? U("请选择需要修改的字段。") : e?.message === "confirm_clear" ? U("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "write_ack_readback_unverified" ? U("已提交，状态暂未核实，请刷新确认。", "warning") : e?.message === "write_ack_authorization_denied" ? (X(), p = null, m = null, _ = !0, U("已提交，但管理授权不可用，状态暂未核实。请重新打开宿主管理页面确认。", "warning")) : e?.message === "secret_encryption_unavailable" ? U("未保存。" + se) : e?.message === "invalid_config" ? U("配置或输入未通过校验，请核对字段要求后刷新重试。") : T(e) ? (X(), p = null, m = null, _ = !0, U("管理授权不可用或页面已失效，请重新打开宿主管理页面后手动刷新。")) : (_ = !0, U("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			O === t && (O = null, J());
		}
	}
	let ge = () => Object.fromEntries(Object.entries(f).map(([e, t]) => [e, t.revision]));
	u("refresh").addEventListener("click", () => !K(u("refresh")) && Q(async (e) => {
		let t = await Z(e);
		U(t.credentials ? "已刷新。" : "普通设置已刷新，凭据状态暂未核实。", t.credentials ? "success" : "warning");
	}));
	for (let e of ["rollback", "recover"]) u(e)?.addEventListener("click", () => !K(u(e)) && Q(async (t) => {
		if (await s({
			action: e,
			isCurrent: () => G(t)
		}) && G(t)) {
			await Y(e, {
				expected_revisions: ge(),
				...e === "recover" ? { complete_from_current: u("replacement").checked } : {}
			}, t);
			try {
				await Z(t);
			} catch (e) {
				if (!G(t)) throw e;
				_ = !0, w(T(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			c(), U(e === "rollback" ? "受审迁移已限定回退，其他数据保留。" : "配置已重新校验，业务运行已恢复。", "success");
		}
	}));
	function _e(t) {
		if (g) return;
		k.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = ae(t, i);
		} catch {
			l(null), X(), x = !0, v++, h = !1, _ = !0, O = null, y = null, d = null, f = null, p = null, m = null, k.fields([], W), k.credentials([], W), A.clear(), j.clear(), L.clear(), V = oe, J(), U("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		x = !0, !(h && n === y) && (H.clear(), l(null), X(), v++, y = n, h = !0, _ = !0, O = null, d = null, f = null, p = null, m = null, k.fields([], W), k.credentials([], W), A.clear(), j.clear(), L.clear(), V = oe, J(), Q(async (e) => {
			await Z(e);
		}));
	}
	let ve = () => [...j.values()].some((e) => e.mode?.value !== "keep") || [...L.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function ye() {
		let e = [...new Set([...j.values()].filter((e) => (B === null || e.field.module_id === B) && ["replace", "clear"].includes(e.mode?.value)).map((e) => e.field.module_id))], t = [...new Set([...L.values()].filter((e) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map((e) => e.field.module_id))];
		return {
			ordinaryOwners: e,
			credentialOwners: t,
			requiresConfirmation: e.length > 0 || t.length > 0
		};
	}
	function be() {
		F.dataset.tone === "success" ? H.clear() : H.cancel();
		let t = e.activeElement;
		z = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, X(), v++, O = null, _ = !0, J();
	}
	function xe() {
		!g && h && Q(async (e) => {
			if (await Z(e), z) {
				let e = u(z.id);
				if (e?.focus({ preventScroll: !0 }), e && z.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(z.start, z.end);
				} catch {}
				z = null;
			}
		});
	}
	function $() {
		g || (H.cancel(), l(null), X(), g = !0, v++, h = !1, O = null, d = null, f = null, p = null, m = null, C?.(), J());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				U("请从宿主插件管理页面打开此页。");
				return;
			}
			C = t.onContext(_e), Promise.resolve(t.ready()).then((e) => {
				x || _e(e);
			}).catch(() => {
				!x && !g && (h = !1, U("宿主管理会话不可用。"), J());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: ve,
		navigationImpact: ye,
		suspend: be,
		resume: xe,
		selectOwner(e) {
			B = e, k.owner(e), J();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), k.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && R(document, window.AstrBotPluginPage, window).start();
//#endregion
export { R as createManagementPage, ie as credentialUpdateBody, I as updateBody, M as validateCatalog, ne as validateCredentialCatalog, re as validateCredentialStatus, N as validateSnapshot };
