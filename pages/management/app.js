import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NAlert as a, NConfigProvider as o, darkTheme as s, dateEnUS as c, dateZhCN as l, enUS as u, zhCN as d } from "./runtime.js";
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
var f = t({
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
}), p = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-settings-row" }, t.default?.());
} }), m = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-action-bar" }, t.default?.());
} }), h = t({
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
}), g = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
	owner: "ff14/ff14",
	saveLabel: "保存日历设置",
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
			label: "启用订阅",
			width: "short",
			advanced: !0
		}
	},
	credentials: {
		credential_fflogs_cn: { label: "国服" },
		credential_fflogs_global: { label: "国际服" }
	}
} })).map((e) => [e.owner, e]));
g.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var ee = (e) => g.get(e), _ = (e) => g.get(e.module_id)?.fields?.[e.name] || {}, v = (e, t) => g.get(e)?.groups?.[t]?.title || t || "普通设置", y = (e) => g.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name;
//#endregion
//#region src/management-view.ts
function b(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (_(e.field).order || 0) - (_(t.field).order || 0))
	}));
}
function x(r, g = r.getElementById("management-root")) {
	let x = i([]), S = i({}), C = i([]), w = i(""), T = i(null), E = i(0), D = i({}), O = i({}), k = () => {}, A = () => {
		E.value++, k();
	}, j = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), M = (e) => !!(e.mode && e.mode.value !== "keep"), N = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function P(e) {
		let t = e.field, r = t.value_schema, i = _(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = r?.enum || (r?.type === "boolean" ? [!1, !0] : null);
		return n(p, {
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
					e.draftVersion++, A();
				}
			}),
			n(o ? "select" : "input", {
				id: a,
				"data-role": "value",
				"aria-describedby": e.problem ? a + "-detail" : void 0,
				onInput: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, A();
				},
				onChange: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, A();
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
			}, "请选择有效值"), ...o.map((e, t) => n("option", { value: String(t) }, String(e)))] : void 0),
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
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), A();
				}
			}, "恢复默认"),
			e.problem ? n("p", {
				class: "field-detail",
				id: a + "-detail",
				role: "status"
			}, e.problem) : null
		] });
	}
	function F(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = y(e.field), a = () => {
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
			n(h, { class: "credential-detail" }, { default: () => e.detail }),
			n(p, { "data-width": "medium" }, { default: () => [n("label", { for: r + "-mode" }, "本次操作"), n("select", {
				id: r + "-mode",
				"data-role": "credential-mode",
				ref: (t) => {
					e.mode = t;
				},
				onChange: () => {
					a(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), O.value = {
						...O.value,
						[r + "-client_id"]: !1,
						[r + "-client_secret"]: !1
					}, A();
				}
			}, [
				["keep", "保持（不写入）"],
				["replace", "替换完整凭据对"],
				["clear", "清除已保存凭据"]
			].map(([e, t]) => n("option", { value: e }, t)))] }),
			...["client_id", "client_secret"].map((t) => n(p, {
				class: "credential-field",
				key: t
			}, { default: () => [n("label", { for: r + "-" + t }, t === "client_id" ? "Client ID" : "Client Secret"), n("div", { class: "credential-input ui-control" }, [n("input", {
				id: r + "-" + t,
				type: O.value[r + "-" + t] ? "text" : "password",
				autocomplete: "off",
				maxlength: 512,
				"data-role": t,
				ref: (n) => {
					t === "client_id" ? e.clientId = n : e.clientSecret = n;
				},
				onInput: () => {
					a(), e.confirm && (e.confirm.checked = !1), A();
				},
				onChange: () => {
					a(), e.confirm && (e.confirm.checked = !1), A();
				}
			}), n("button", {
				type: "button",
				"data-action": "reveal",
				"aria-label": (O.value[r + "-" + t] ? "隐藏" : "显示") + "本次输入 " + (t === "client_id" ? "Client ID" : "Client Secret"),
				"aria-pressed": !!O.value[r + "-" + t],
				onClick: () => {
					O.value = {
						...O.value,
						[r + "-" + t]: !O.value[r + "-" + t]
					};
				}
			}, O.value[r + "-" + t] ? "隐藏" : "显示")])] })),
			n("label", {
				class: "credential-confirm",
				hidden: e.mode?.value !== "clear"
			}, [n("input", {
				type: "checkbox",
				"data-role": "credential-clear-confirm",
				ref: (t) => {
					e.confirm = t;
				},
				onChange: () => {
					a(), A();
				}
			}), "确认清除本组已保存凭据"]),
			n(m, {}, { default: () => [n("button", {
				type: "button",
				"data-action": "credential-save",
				disabled: !0,
				ref: (t) => {
					e.save = t;
				},
				onClick: e.onSave
			}, "保存" + i + "凭据"), N(e) ? n("span", {
				class: "dirty-indicator",
				role: "status"
			}, i + "有未保存修改") : null] })
		]);
	}
	let I = t({ setup() {
		return () => {
			E.value;
			let e = S.value.isDark === !0 || S.value.theme === "dark", t = String(S.value.locale || "").startsWith("en"), r = /* @__PURE__ */ new Map();
			for (let e of C.value) {
				let t = JSON.stringify([e.field.module_id, e.field.group || ""]);
				r.has(t) || r.set(t, []), r.get(t).push(e);
			}
			return n(o, {
				preflightStyleDisabled: !0,
				theme: e ? s : null,
				locale: t ? u : d,
				dateLocale: t ? c : l,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				g.id === "management-root" ? n("h1", "设置") : null,
				n("div", { class: "management-toolbar" }, [n(a, {
					type: "info",
					showIcon: !1
				}, { default: () => n(h, { id: "status" }, { default: () => "正在连接宿主管理页面。" }) }), j("refresh", "刷新配置与版本")]),
				n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, x.value.map((e) => {
					let t = e.controls.filter((e) => _(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: T.value !== null && T.value !== e.target
					}, [
						...b(r).map((t) => n(f, {
							title: v(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => t.items.map(P) })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...b(t).map((t) => n(f, {
							title: v(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(P) }))]) : null,
						n(m, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, ee(e.target)?.saveLabel || "保存普通设置"),
							n("button", {
								type: "button",
								"data-action": "discard",
								ref: (t) => {
									e.discard = t;
								},
								onClick: () => e.onDiscard?.()
							}, "放弃本模块修改"),
							e.controls.some(M) ? n("span", {
								class: "dirty-indicator",
								role: "status"
							}, "普通设置有未保存修改") : null
						] }),
						n("details", { class: "technical-details" }, [
							n("summary", "配置详细信息"),
							n("p", e.revision),
							n("p", "保存仅提交本模块普通设置；放弃本模块修改同时清空本模块凭据草稿。"),
							...e.controls.map((e) => n("p", e.detail))
						])
					]);
				})),
				n(h, {
					id: "credential-status",
					tone: "warning",
					hidden: T.value !== null && C.value.length > 0 && !C.value.some((e) => e.field.module_id === T.value)
				}, { default: () => w.value }),
				n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === D.value[e]) ? D.value[e] : t[0].field.name;
					return n(f, {
						title: v(t[0].field.module_id, t[0].field.group || ""),
						key: e,
						hidden: T.value !== null && T.value !== t[0].field.module_id
					}, { default: () => [t.length > 1 ? n("nav", {
						class: "credential-tabs",
						"aria-label": "凭据来源"
					}, t.map((t) => n("button", {
						type: "button",
						"aria-pressed": r === t.field.name,
						onClick: () => {
							D.value = {
								...D.value,
								[e]: t.field.name
							};
						}
					}, y(t.field) + (N(t) ? " · 未保存" : "")))) : null, ...t.map((e) => F(e, r === e.field.name))] });
				})),
				n("details", { class: "advanced-maintenance" }, [n("summary", "高级维护与启动修复"), n(f, { title: "限定恢复" }, { default: () => [
					n("p", "恢复默认仅作用于指定配置字段。缓存清理和持久业务数据删除尚无通用范围合同，当前不提供执行入口。解除挂载始终保留数据。"),
					n("p", "回退仅处理受审普通配置迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。"),
					j("rollback", "回退受审迁移"),
					n("p", n("label", [n("input", {
						id: "replacement",
						type: "checkbox"
					}), "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）"])),
					j("recover", "重新校验并恢复运行")
				] })])
			]) });
		};
	} }), L = e(I);
	return L.mount(g), {
		owner(e) {
			T.value = e;
		},
		drafts() {
			E.value++;
		},
		maskSecrets() {
			O.value = {};
		},
		fields(e, t) {
			k = t, x.value = e;
		},
		credentials(e, t, n = "") {
			k = t, C.value = e, w.value = n;
		},
		context(e) {
			S.value = e || {};
		},
		dispose() {
			L.unmount();
		}
	};
}
//#endregion
//#region src/management.js
var S = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), C = (e, t) => S(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), w = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, T = (e = "invalid_config") => {
	throw Error(e);
}, E = (e) => JSON.stringify([e.module_id, e.name]), D = (e, t) => e.fields.filter((e) => e.module_id === t), O = (e) => [...e].length;
function k(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && T(), typeof e == "string") {
			O(e) > 4096 && T();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!S(e) && !Array.isArray(e) || r.has(e)) && T(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && T();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && T();
}
function A(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || O(t) >= e.minLength) && (e.maxLength === void 0 || O(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => A(e.items, t)) : e.type === "object" && S(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && A(e.properties[t], n));
}
function j(e, t = 0) {
	(!S(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && T();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && T(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !S(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && T();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || T(), j(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && T();
	} else if (e.type === "array") n.push("items"), j(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && T();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && T(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => A({
			...e,
			enum: void 0
		}, t))) && T();
	}
	Object.keys(e).some((e) => !n.includes(e)) && T();
}
function M(e) {
	k(e), (!C(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && T();
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
	for (let r of e.fields) (!C(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => w.test(e)) || typeof r.name != "string" || !r.name || O(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && w.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && T(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && T(), n.has(E(r)) && T(), n.add(E(r)), r.value_schema !== null && (k(r.value_schema, 16, 2048), j(r.value_schema), r.default !== null && !A(r.value_schema, r.default) && T());
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
	t || T(), k(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	C(e, n) || T();
	for (let r of n) {
		let n = e[r], i = D(t, r).filter((e) => e.readable);
		(!C(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !C(n.fields, i.map((e) => e.name))) && T();
		for (let e of i) {
			let t = n.fields[e.name];
			(!C(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !A(e.value_schema, t.value))) && T();
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
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && T(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && T(), r = Number(t));
	return A(n, r) || T(), r;
}
function I(e, t, n, r) {
	(!r || !S(n) || !Number.isSafeInteger(t) || t < 1) && T();
	let i = D(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && T();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!S(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && T(), t.mode !== "keep" && ((!n.readable || !n.editable || !P(n)) && T("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: F(n, t.value)
		}));
	}
	return a.length || T("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function L(e) {
	k(e), (!C(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && T();
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
		(!C(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => w.test(e)) || typeof r.name != "string" || !w.test(r.name) || typeof r.group != "string" || !w.test(r.group) || typeof r.description != "string" || !r.description || t.has(E(r))) && T(), t.add(E(r)), (!C(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !C(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && T();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!C(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && T();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function te(e, t) {
	k(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	C(e, n) || T();
	for (let r of n) {
		let n = e[r];
		(!C(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !C(n.fields, D(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && T();
	}
	return e;
}
function ne(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && T(), r === "replace" && (!C(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || O(e) < 1 || O(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && T(), {
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
var R = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function re(e, t) {
	(!S(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && T("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !R.has(e)));
	k(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && T("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : S(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function z(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), onDraftMutation: o = () => {}, onConfigurationSaved: s = () => {}, onCapabilities: c = (e, t = !1) => {} } = {}) {
	a || T("invalid_container");
	let l = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, u = null, d = null, f = null, p = null, m = !1, h = !1, g = !0, _ = 0, v = null, b = !1, S = null, w = null, O = x(e, a), k = /* @__PURE__ */ new Map(), A = /* @__PURE__ */ new Map(), j = l("status"), F = /* @__PURE__ */ new Map(), R = !1, z = null, B = null, V = "[[],[]]", H = null;
	function U(e, t = "error") {
		H !== null && n.clearTimeout(H), j.textContent = e, j.dataset.tone = t, t === "success" && (H = n.setTimeout(() => {
			j.textContent = "", H = null;
		}, 3500));
	}
	function W() {
		let e = JSON.stringify([[...A].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
			e,
			t.mode.value,
			t.input?.value
		]), [...F].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
			e,
			t.mode?.value,
			t.clientId?.value,
			t.clientSecret?.value,
			t.confirm?.checked
		])]);
		e !== V && (V = e, o()), O.drafts(), J();
	}
	let G = (e) => !h && m && _ === e, ie = (e) => e.readable && e.editable && P(e), K = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function q(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (h || !m || e.activeElement !== t);
	}
	function J() {
		q(l("refresh"), h || !m || w !== null);
		let e = !h && m && !g && d !== null && Object.keys(d).length > 0 && w === null;
		q(l("rollback"), !e), q(l("recover"), !e || u.fields.some((e) => e.readable && !e.editable)), l("replacement").disabled = K(l("recover"));
		for (let [t, n] of k) {
			let r = B === null || B === t;
			q(n.save, !e || !r || !u || !D(u, t).some(ie)), n.discard && q(n.discard, !m || h || w !== null || !r);
		}
		for (let e of A.values()) {
			let t = !h && m && (B === null || B === e.field.module_id) && ie(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of F.values()) {
			let t = !h && m && (B === null || B === e.field.module_id) && p !== null && f !== null && f.fields.some((t) => E(t) === E(e.field) && JSON.stringify(t) === e.signature);
			e.save && q(e.save, !t || g || w !== null || e.mode?.value === "replace" && !R), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !R);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !R || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function Y(e, n, r) {
		G(r) || T("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return G(r) || T("stale_context"), e === "catalog" ? M(i) : e === "read" ? i : e === "credential-catalog" ? L(i) : e === "credential-readiness" ? ((!C(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && T("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!C(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && T("operation_unavailable"), e === "rollback" && (!C(i, ["rolled_back"]) || i.rolled_back !== !0) && T("operation_unavailable"), e === "recover" && (!C(i, ["recovered"]) || i.recovered !== !0) && T("operation_unavailable"), i);
	}
	function ae(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function oe(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function se(e) {
		let t = k.get(e).save;
		K(t) || Q(async (t) => {
			let n = Object.fromEntries(D(u, e).map((e) => {
				let t = A.get(E(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await Y("update", I(e, d[e].revision, n, u), t), s(), await Z(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = A.get(JSON.stringify([e, t])), i = d[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = ae(n.field, i.value));
			}
			W(), U((ee(e)?.saveLabel || "普通设置") + "已保存。其它组草稿未提交。", "success");
		});
	}
	function ce(e) {
		if (!h && m && w === null && d?.[e]) {
			for (let t of D(u, e)) {
				let n = A.get(E(t)), r = d[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = ae(t, r.value));
			}
			for (let t of F.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			O.maskSecrets(), W(), U("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function X() {
		O.maskSecrets();
		for (let e of F.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		W();
	}
	function le(e) {
		K(e.save) || e.mode.value === "replace" && !R || Q(async (t) => {
			let n = e.mode.value;
			n === "keep" && T("select_changes"), n === "clear" && !e.confirm.checked && T("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await Y("credential-update", ne(e.field.module_id, p[e.field.module_id].revision, e.field.name, n, i, f), t), await Z(t), e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), W(), U(n === "clear" ? "已清除" + y(e.field) + "凭据；业务查询需要重新配置该组凭据。" : y(e.field) + "凭据已加密保存；尚未验证所属上游。其它组草稿未提交。", "success");
		});
	}
	async function ue() {
		let e = new Set(f.fields.map(E));
		for (let [t, n] of F) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			F.delete(t);
		}
		for (let e of f.fields) {
			let t = E(e), n = JSON.stringify(e), r = F.get(t);
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
				}, r.onSave = () => le(r), F.set(t, r);
			}
			let i = p[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		O.credentials([...F.values()], W), await r(), J();
	}
	async function de() {
		let e = [...new Set(u.fields.map((e) => e.module_id))], t = new Set(u.fields.map(E));
		for (let e of A.keys()) t.has(e) || A.delete(e);
		for (let t of k.keys()) e.includes(t) || k.delete(t);
		let n = [];
		for (let t of e) {
			let e = k.get(t);
			e || (e = {
				target: t,
				revision: "",
				controls: [],
				save: null,
				discard: null,
				onSave: () => se(t),
				onDiscard: () => ce(t)
			}, k.set(t, e)), e.revision = d[t] ? `配置版本：${d[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of D(u, t)) {
				let i = E(r), a = JSON.stringify(r), o = A.get(i);
				(!o || o.signature !== a) && (o = oe(r), A.set(i, o));
				let s = r.readable ? d[t].fields[r.name] : null, c = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				s ? c.push(s.state === "valid" ? "有效" : "无效存量，原值不显示", s.present ? "已有显式值" : "未设置", s.source === "sqlite" ? "Core 配置" : "默认值") : c.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && c.push("模块语义校验器不可用，禁止修改与清除"), P(r) || c.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), o.detail = c.join(" · "), o.problem = r.readable ? r.editable ? P(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前不可修改，请核对模块状态。" : "无存量读取与修改授权", P(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? ae(r, s.value) : "",
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
		O.fields([...k.values()], W), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		J();
	}
	async function Z(e) {
		g = !0, J();
		let t = await Y("catalog", {}, e), n = N(await Y("read", {}, e), t);
		G(e) || T("stale_context"), u = t, d = n;
		let i = "";
		try {
			let t = await Y("credential-catalog", {}, e), n = te(await Y("credential-status", {}, e), t);
			G(e) || T("stale_context"), f = t, p = n, R = !1;
			try {
				R = (await Y("credential-readiness", {}, e)).ready, i = R ? "" : "加密未就绪，暂不能填写凭据。请管理员配置密钥后刷新。";
			} catch {
				G(e) || T("stale_context"), i = "未能确认加密状态，暂不能填写凭据。请刷新核对。";
			}
			R || X();
		} catch (t) {
			if (!G(e)) throw t;
			t?.message === "admin_authorization_denied" && X(), f = null, p = null, i = t?.message === "admin_authorization_denied" ? "来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。" : "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		g = !1, await de(), f ? (await ue(), O.credentials([...F.values()], W, i), await r(), J()) : O.credentials([...F.values()], W, i), G(e) || T("stale_context"), c([...new Set([...u.fields, ...f?.fields || []].map((e) => e.module_id))], f !== null);
	}
	async function Q(e) {
		if (w || h || !m) return;
		let t = { generation: _ };
		w = t, J(), U("管理操作正在执行。", "neutral");
		try {
			await e(t.generation);
		} catch (e) {
			if (!G(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (g = !0, U("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? U("请选择需要修改的字段。") : e?.message === "confirm_clear" ? U("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "secret_encryption_unavailable" ? U("未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。") : e?.message === "invalid_config" ? U("配置或输入未通过校验，请核对字段要求后刷新重试。") : e?.message === "admin_authorization_denied" ? (X(), f = null, p = null, g = !0, U("管理授权不可用，请重新打开宿主管理页面后手动刷新。")) : (g = !0, U("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			w === t && (w = null, J());
		}
	}
	let fe = () => Object.fromEntries(Object.entries(d).map(([e, t]) => [e, t.revision]));
	l("refresh").addEventListener("click", () => !K(l("refresh")) && Q(async (e) => {
		await Z(e), U("设置已更新。", "success");
	})), l("rollback").addEventListener("click", () => !K(l("rollback")) && Q(async (e) => {
		await Y("rollback", { expected_revisions: fe() }, e), await Z(e), U("受审迁移已限定回退，其他数据保留。", "success");
	})), l("recover").addEventListener("click", () => !K(l("recover")) && Q(async (e) => {
		await Y("recover", {
			expected_revisions: fe(),
			complete_from_current: l("replacement").checked
		}, e), await Z(e), U("配置已重新校验，业务运行已恢复。", "success");
	}));
	function pe(t) {
		if (h) return;
		O.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = re(t, i);
		} catch {
			c(null), X(), b = !0, _++, m = !1, g = !0, w = null, v = null, u = null, d = null, f = null, p = null, O.fields([], J), O.credentials([], J), k.clear(), A.clear(), F.clear(), V = "[[],[]]", J(), U("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		b = !0, !(m && n === v) && (c(null), X(), _++, v = n, m = !0, g = !0, w = null, u = null, d = null, f = null, p = null, O.fields([], J), O.credentials([], J), k.clear(), A.clear(), F.clear(), V = "[[],[]]", J(), Q(async (e) => {
			await Z(e), U("设置已更新。", "success");
		}));
	}
	let me = () => [...A.values()].some((e) => e.mode?.value !== "keep") || [...F.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function he() {
		let t = e.activeElement;
		z = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, X(), _++, w = null, g = !0, J();
	}
	function ge() {
		!h && m && Q(async (e) => {
			if (await Z(e), U("设置已更新。", "success"), z) {
				let e = l(z.id);
				if (e?.focus({ preventScroll: !0 }), e && z.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(z.start, z.end);
				} catch {}
				z = null;
			}
		});
	}
	function $() {
		h || (H !== null && n.clearTimeout(H), c(null), X(), h = !0, _++, m = !1, w = null, u = null, d = null, f = null, p = null, S?.(), J());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				U("请从宿主插件管理页面打开此页。");
				return;
			}
			S = t.onContext(pe), Promise.resolve(t.ready()).then((e) => {
				b || pe(e);
			}).catch(() => {
				!b && !h && (m = !1, U("宿主管理会话不可用。"), J());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: me,
		suspend: he,
		resume: ge,
		selectOwner(e) {
			B = e, O.owner(e), J();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), O.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && z(document, window.AstrBotPluginPage, window).start();
//#endregion
export { z as createManagementPage, ne as credentialUpdateBody, I as updateBody, M as validateCatalog, L as validateCredentialCatalog, te as validateCredentialStatus, N as validateSnapshot };
