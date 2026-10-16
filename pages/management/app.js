import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NConfigProvider as a, darkTheme as o, dateEnUS as s, dateZhCN as ee, enUS as te, zhCN as c } from "./runtime.js";
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
var l = t({
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
}), u = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-settings-row" }, t.default?.());
} }), d = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-action-bar" }, t.default?.());
} }), f = t({
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
}), p = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
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
p.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var m = (e) => p.get(e), h = (e) => p.get(e.module_id)?.fields?.[e.name] || {}, g = (e, t) => t === "reply_style" ? "回复样式" : p.get(e)?.groups?.[t]?.title || t || "普通设置", _ = (e) => p.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name, v = (e) => e.group === "reply_style" && (e.module_id === "game_link/core" ? e.name === "image_theme" : e.name === "reply_theme"), y = (e) => v(e) && e.module_id !== "game_link/core" && e.default === null && e.choices?.[0]?.value === null, b = (e) => e.choices?.map((e) => e.value) ?? e.value_schema?.enum ?? (e.value_schema?.type === "boolean" ? [!1, !0] : null);
//#endregion
//#region src/management-view.ts
function x(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (h(e.field).order || 0) - (h(t.field).order || 0))
	}));
}
function ne(r, p = r.getElementById("management-root"), v = "settings") {
	let ne = i([]), S = i({}), C = i([]), w = i(""), T = i(null), E = i(0), D = i({}), O = i({}), k = () => {}, A = () => {
		E.value++, k();
	}, j = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), M = (e) => !!(e.mode && e.mode.value !== "keep"), N = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function P(e) {
		let t = e.field, r = t.value_schema, i = h(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = b(t), s = () => {
			e.mode && (e.mode.value = y(t) && e.input?.value !== "" && o?.[Number(e.input?.value)] === null ? e.inheritBaseline ? "keep" : "clear" : "replace"), e.draftVersion++, A();
		};
		return n(u, {
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
				"aria-describedby": e.problem ? a + "-detail" : i.hint ? a + "-hint" : void 0,
				onInput: s,
				onChange: s,
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
			}, "请选择有效值"), ...o.map((e, r) => n("option", { value: String(r) }, t.choices?.[r]?.label || (i.booleanLabels && typeof e == "boolean" ? i.booleanLabels[String(e)] : String(e))))] : void 0),
			n("button", {
				type: "button",
				"data-action": "default",
				ref: (t) => {
					e.reset = t;
				},
				onClick: () => {
					if (!e.mode || !e.input || e.reset?.disabled) return;
					e.mode.value = i.reset === "replace-default" ? "replace" : e.inheritBaseline ? "keep" : "clear", e.draftVersion++;
					let n = t.default;
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), A();
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
	function F(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = _(e.field), a = () => {
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
			n(f, { class: "credential-detail" }, { default: () => e.detail }),
			n(u, { "data-width": "medium" }, { default: () => [n("label", { for: r + "-mode" }, "本次操作"), n("select", {
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
			...["client_id", "client_secret"].map((t) => n(u, {
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
				class: "ui-check-label credential-confirm",
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
			}), n("span", "确认清除本组已保存凭据")]),
			n(d, {}, { default: () => [n("button", {
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
			return n(a, {
				preflightStyleDisabled: !0,
				theme: e ? o : null,
				locale: t ? te : c,
				dateLocale: t ? s : ee,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				p.id === "management-root" ? n("h1", v === "maintenance" ? "配置维护" : "设置") : null,
				n("div", { class: "management-toolbar" }, [n(f, { id: "status" }), j("refresh", "刷新配置与版本")]),
				v === "settings" ? n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, ne.value.map((e) => {
					let t = e.controls.filter((e) => h(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: T.value !== null && T.value !== e.target
					}, [
						...x(r).map((t) => n(l, {
							title: g(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => t.items.map(P) })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...x(t).map((t) => n(l, {
							title: g(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(P) }))]) : null,
						n(d, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, m(e.target)?.saveLabel || "保存普通设置"),
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
						] })
					]);
				})) : null,
				v === "settings" ? n(f, {
					id: "credential-status",
					tone: "warning",
					hidden: T.value !== null && C.value.length > 0 && !C.value.some((e) => e.field.module_id === T.value)
				}, { default: () => w.value }) : null,
				v === "settings" ? n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === D.value[e]) ? D.value[e] : t[0].field.name;
					return n(l, {
						title: g(t[0].field.module_id, t[0].field.group || ""),
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
					}, _(t.field) + (N(t) ? " · 未保存" : "")))) : null, ...t.map((e) => F(e, r === e.field.name))] });
				})) : null,
				v === "maintenance" ? n(l, {
					title: "限定恢复",
					class: "maintenance-panel"
				}, { default: () => [
					n("p", "仅处理受审普通配置迁移；后续修改会阻止回退。订阅及其它持久数据保留。"),
					j("rollback", "回退受审迁移"),
					n("p", n("label", { class: "ui-check-label" }, [n("input", {
						id: "replacement",
						type: "checkbox",
						onChange: A
					}), n("span", "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）")])),
					j("recover", "重新校验并恢复运行")
				] }) : null
			]) });
		};
	} }), L = e(I);
	return L.mount(p), {
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
			k = t, ne.value = e;
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
//#region src/ui/notice.ts
function S(e, t) {
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
var C = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), w = (e, t) => C(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), T = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, E = (e = "invalid_config") => {
	throw Error(e);
}, D = (e) => [
	e?.code,
	e?.error_code,
	e?.message
].some((e) => [
	"admin_authorization_denied",
	"stale_context",
	"page_revoked",
	"asset_revoked"
].includes(e)) || [e?.status, e?.response?.status].some((e) => e === 401 || e === 403), O = (e) => JSON.stringify([e.module_id, e.name]), k = (e, t) => e.fields.filter((e) => e.module_id === t), A = (e) => [...e].length;
function j(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && E(), typeof e == "string") {
			A(e) > 4096 && E();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!C(e) && !Array.isArray(e) || r.has(e)) && E(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && E();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && E();
}
function M(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || A(t) >= e.minLength) && (e.maxLength === void 0 || A(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => M(e.items, t)) : e.type === "object" && C(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && M(e.properties[t], n));
}
function N(e, t = 0) {
	(!C(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && E();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && E(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !C(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && E();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || E(), N(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && E();
	} else if (e.type === "array") n.push("items"), N(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && E();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && E(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => M({
			...e,
			enum: void 0
		}, t))) && E();
	}
	Object.keys(e).some((e) => !n.includes(e)) && E();
}
function P(e) {
	j(e), (!w(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && E();
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
	for (let r of e.fields) if ((!C(r) || !w(r, Object.hasOwn(r, "choices") ? [...t, "choices"] : t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => T.test(e)) || typeof r.name != "string" || !r.name || A(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && T.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && E(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && E(), n.has(O(r)) && E(), n.add(O(r)), r.value_schema !== null && (j(r.value_schema, 16, 2048), N(r.value_schema), r.default !== null && !M(r.value_schema, r.default) && E()), Object.hasOwn(r, "choices")) {
		(!v(r) || r.value_schema?.type !== "string" || !Array.isArray(r.value_schema.enum) || !Array.isArray(r.choices)) && E();
		let e = r.module_id !== "game_link/core", t = e ? [null, ...r.value_schema.enum] : r.value_schema.enum;
		(e ? r.default !== null : !M(r.value_schema, r.default)) && E(), (r.choices.length !== t.length || !r.choices.every((e, n) => w(e, ["value", "label"]) && e.value === t[n] && typeof e.label == "string" && e.label.trim().length > 0 && A(e.label) <= 128 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(e.label))) && E();
	}
	let r = JSON.parse(JSON.stringify(e)), i = (e) => {
		if (e && typeof e == "object") {
			for (let t of Object.values(e)) i(t);
			Object.freeze(e);
		}
		return e;
	};
	return i(r);
}
function F(e, t) {
	t || E(), j(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	w(e, n) || E();
	for (let r of n) {
		let n = e[r], i = k(t, r).filter((e) => e.readable);
		(!w(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !w(n.fields, i.map((e) => e.name))) && E();
		for (let e of i) {
			let t = n.fields[e.name];
			(!w(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !(t.value === null && y(e) && t.present === !1 && t.source === "default") && !M(e.value_schema, t.value))) && E();
		}
	}
	return e;
}
var I = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function L(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let n = b(e);
		(!/^\d+$/.test(t) || !Object.hasOwn(n, Number(t))) && E(), r = n[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && E(), r = Number(t));
	return !(r === null && y(e)) && !M(n, r) && E(), r;
}
function re(e, t, n, r) {
	(!r || !C(n) || !Number.isSafeInteger(t) || t < 1) && E();
	let i = k(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && E();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		if ((!C(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && E(), t.mode === "keep") continue;
		(!n.readable || !n.editable || !I(n)) && E("admin_authorization_denied");
		let r = t.mode === "clear" ? null : L(n, t.value);
		a.push(t.mode === "clear" || r === null && y(n) ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: r
		});
	}
	return a.length || E("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function ie(e) {
	j(e), (!w(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && E();
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
		(!w(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => T.test(e)) || typeof r.name != "string" || !T.test(r.name) || typeof r.group != "string" || !T.test(r.group) || typeof r.description != "string" || !r.description || t.has(O(r))) && E(), t.add(O(r)), (!w(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !w(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && E();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!w(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && E();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function ae(e, t) {
	j(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	w(e, n) || E();
	for (let r of n) {
		let n = e[r];
		(!w(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !w(n.fields, k(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && E();
	}
	return e;
}
function oe(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && E(), r === "replace" && (!w(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || A(e) < 1 || A(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && E(), {
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
function se(e, t) {
	(!C(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && E("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !R.has(e)));
	j(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && E("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : C(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function z(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), scope: o = "settings", beforeMaintenance: s = async (e) => !1, onDraftMutation: ee = () => {}, onConfigurationSaved: te = () => {}, onCapabilities: c = (e, t = !1) => {} } = {}) {
	a || E("invalid_container");
	let l = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, u = null, d = null, f = null, p = null, m = !1, h = !1, g = !0, _ = 0, v = null, x = !1, C = null, T = null, A = ne(e, a, o), j = /* @__PURE__ */ new Map(), M = /* @__PURE__ */ new Map(), N = l("status"), L = /* @__PURE__ */ new Map(), R = !1, z = null, B = null, ce = o === "maintenance" ? "[[],[],false]" : "[[],[]]", V = ce, H = S(n, (e, t) => {
		N.textContent = e, N.dataset.tone = t;
	}), U = (e, t = "error") => H.show(e, t), le = "当前加密不可用，请管理员检查宿主密钥与加密组件；修改外部环境后需正常重启同一宿主，再重开此页。已有凭据时请保留原密钥。";
	function W() {
		let e = JSON.stringify([
			[...M].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
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
			...o === "maintenance" ? [!!l("replacement")?.checked] : []
		]);
		e !== V && (V = e, ee()), A.drafts(), J();
	}
	let G = (e) => !h && m && _ === e, ue = (e) => e.readable && e.editable && I(e), K = (e) => !e || e.disabled || e.getAttribute("aria-disabled") === "true";
	function q(t, n) {
		t && (t.setAttribute("aria-disabled", String(n)), t.disabled = n && (h || !m || e.activeElement !== t));
	}
	function J() {
		q(l("refresh"), h || !m || T !== null);
		let e = !h && m && !g && d !== null && Object.keys(d).length > 0 && T === null;
		q(l("rollback"), !e), q(l("recover"), !e || u.fields.some((e) => e.readable && !e.editable)), l("replacement") && (l("replacement").disabled = K(l("recover")));
		for (let [t, n] of j) {
			let r = B === null || B === t;
			q(n.save, !e || !r || !u || !k(u, t).some(ue)), n.discard && q(n.discard, !m || h || T !== null || !r);
		}
		for (let e of M.values()) {
			let t = !h && m && (B === null || B === e.field.module_id) && ue(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of L.values()) {
			let t = !h && m && (B === null || B === e.field.module_id) && p !== null && f !== null && f.fields.some((t) => O(t) === O(e.field) && JSON.stringify(t) === e.signature);
			e.save && q(e.save, !t || g || T !== null || e.mode?.value === "replace" && !R), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !R);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !R || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function Y(e, n, r) {
		G(r) || E("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return G(r) || E("stale_context"), e === "catalog" ? P(i) : e === "read" ? i : e === "credential-catalog" ? ie(i) : e === "credential-readiness" ? ((!w(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && E("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!w(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && E("operation_unavailable"), e === "rollback" && (!w(i, ["rolled_back"]) || i.rolled_back !== !0) && E("operation_unavailable"), e === "recover" && (!w(i, ["recovered"]) || i.recovered !== !0) && E("operation_unavailable"), i);
	}
	function de(e, t) {
		let n = b(e);
		return t === null && !y(e) ? "" : String(n ? n.findIndex((e) => e === t) : t);
	}
	function fe(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function pe(e) {
		let t = j.get(e).save;
		K(t) || Q(async (t) => {
			let n = Object.fromEntries(k(u, e).map((e) => {
				let t = M.get(O(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await Y("update", re(e, d[e].revision, n, u), t), te();
			try {
				await Z(t);
			} catch (e) {
				if (!G(t)) throw e;
				g = !0, E(D(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = M.get(JSON.stringify([e, t])), i = d[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = i.state === "valid" ? de(n.field, i.value) : "");
			}
			W(), U("已保存。", "success");
		});
	}
	function me(e) {
		if (!h && m && T === null && d?.[e]) {
			for (let t of k(u, e)) {
				let n = M.get(O(t)), r = d[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = r.state === "valid" ? de(t, r.value) : "");
			}
			for (let t of L.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			A.maskSecrets(), W(), U("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function X() {
		A.maskSecrets();
		for (let e of L.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		W();
	}
	function he(e) {
		K(e.save) || e.mode.value === "replace" && !R || Q(async (t) => {
			let n = e.mode.value;
			n === "keep" && E("select_changes"), n === "clear" && !e.confirm.checked && E("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await Y("credential-update", oe(e.field.module_id, p[e.field.module_id].revision, e.field.name, n, i, f), t);
			let a = !1;
			try {
				a = (await Z(t)).credentials;
			} catch (e) {
				if (!G(t)) throw e;
				D(e) && E("write_ack_authorization_denied");
			}
			e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), W(), a || (g = !0, E("write_ack_readback_unverified")), U("已保存。", "success");
		});
	}
	async function ge() {
		let e = new Set(f.fields.map(O));
		for (let [t, n] of L) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			L.delete(t);
		}
		for (let e of f.fields) {
			let t = O(e), n = JSON.stringify(e), r = L.get(t);
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
				}, r.onSave = () => he(r), L.set(t, r);
			}
			let i = p[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		A.credentials([...L.values()], W), await r(), J();
	}
	async function _e() {
		let e = [...new Set(u.fields.map((e) => e.module_id))], t = new Set(u.fields.map(O));
		for (let e of M.keys()) t.has(e) || M.delete(e);
		for (let t of j.keys()) e.includes(t) || j.delete(t);
		let n = [];
		for (let t of e) {
			let e = j.get(t);
			e || (e = {
				target: t,
				controls: [],
				save: null,
				discard: null,
				onSave: () => pe(t),
				onDiscard: () => me(t)
			}, j.set(t, e)), e.controls = [];
			for (let r of k(u, t)) {
				let i = O(r), a = JSON.stringify(r), o = M.get(i);
				(!o || o.signature !== a) && (o = fe(r), M.set(i, o));
				let s = r.readable ? d[t].fields[r.name] : null;
				o.inheritBaseline = !!(y(r) && s?.state === "valid" && s.value === null && s.present === !1 && s.source === "default"), o.problem = r.readable ? r.editable ? I(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前只读，请核对模块状态。" : "无读取或修改权限。", I(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s?.state === "valid" ? de(r, s.value) : "",
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
		A.fields([...j.values()], W), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		J();
	}
	async function Z(e) {
		g = !0, J();
		let t = await Y("catalog", {}, e), n = F(await Y("read", {}, e), t);
		G(e) || E("stale_context"), u = t, d = n;
		let i = "";
		try {
			let t = await Y("credential-catalog", {}, e), n = ae(await Y("credential-status", {}, e), t);
			G(e) || E("stale_context"), f = t, p = n, R = !1;
			try {
				R = (await Y("credential-readiness", {}, e)).ready, i = R ? "" : le;
			} catch (t) {
				if (!G(e) || D(t)) throw t;
				i = "未能确认加密状态，暂不能填写凭据；请核对宿主管理会话与部署后重开此页。";
			}
			R || X();
		} catch (t) {
			if (!G(e)) throw t;
			if (f = null, p = null, D(t)) throw X(), t;
			i = "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		return g = !1, o === "settings" && (await _e(), f ? (await ge(), A.credentials([...L.values()], W, i), await r(), J()) : A.credentials([...L.values()], W, i)), G(e) || E("stale_context"), c([...new Set([...u.fields, ...f?.fields || []].map((e) => e.module_id))], f !== null), { credentials: f !== null };
	}
	async function Q(e) {
		if (T || h || !m) return;
		let t = { generation: _ };
		T = t, J();
		try {
			await e(t.generation);
		} catch (e) {
			if (!G(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (g = !0, U("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? U("请选择需要修改的字段。") : e?.message === "confirm_clear" ? U("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "write_ack_readback_unverified" ? U("已提交，状态暂未核实，请刷新确认。", "warning") : e?.message === "write_ack_authorization_denied" ? (X(), f = null, p = null, g = !0, U("已提交，但管理授权不可用，状态暂未核实。请重新打开宿主管理页面确认。", "warning")) : e?.message === "secret_encryption_unavailable" ? U("未保存。" + le) : e?.message === "invalid_config" ? U("配置或输入未通过校验，请核对字段要求后刷新重试。") : D(e) ? (X(), f = null, p = null, g = !0, U("管理授权不可用或页面已失效，请重新打开宿主管理页面后手动刷新。")) : (g = !0, U("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			T === t && (T = null, J());
		}
	}
	let ve = () => Object.fromEntries(Object.entries(d).map(([e, t]) => [e, t.revision]));
	l("refresh").addEventListener("click", () => !K(l("refresh")) && Q(async (e) => {
		let t = await Z(e);
		U(t.credentials ? "已刷新。" : "普通设置已刷新，凭据状态暂未核实。", t.credentials ? "success" : "warning");
	}));
	for (let e of ["rollback", "recover"]) l(e)?.addEventListener("click", () => !K(l(e)) && Q(async (t) => {
		if (await s({
			action: e,
			isCurrent: () => G(t)
		}) && G(t)) {
			await Y(e, {
				expected_revisions: ve(),
				...e === "recover" ? { complete_from_current: l("replacement").checked } : {}
			}, t);
			try {
				await Z(t);
			} catch (e) {
				if (!G(t)) throw e;
				g = !0, E(D(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			te(), U(e === "rollback" ? "受审迁移已限定回退，其他数据保留。" : "配置已重新校验，业务运行已恢复。", "success");
		}
	}));
	function ye(t) {
		if (h) return;
		A.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = se(t, i);
		} catch {
			c(null), X(), x = !0, _++, m = !1, g = !0, T = null, v = null, u = null, d = null, f = null, p = null, A.fields([], W), A.credentials([], W), j.clear(), M.clear(), L.clear(), V = ce, J(), U("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		x = !0, !(m && n === v) && (H.clear(), c(null), X(), _++, v = n, m = !0, g = !0, T = null, u = null, d = null, f = null, p = null, A.fields([], W), A.credentials([], W), j.clear(), M.clear(), L.clear(), V = ce, J(), Q(async (e) => {
			await Z(e);
		}));
	}
	let be = () => [...M.values()].some((e) => e.mode?.value !== "keep") || [...L.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function xe() {
		let e = [...new Set([...M.values()].filter((e) => (B === null || e.field.module_id === B) && ["replace", "clear"].includes(e.mode?.value)).map((e) => e.field.module_id))], t = [...new Set([...L.values()].filter((e) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map((e) => e.field.module_id))];
		return {
			ordinaryOwners: e,
			credentialOwners: t,
			requiresConfirmation: e.length > 0 || t.length > 0
		};
	}
	function Se() {
		N.dataset.tone === "success" ? H.clear() : H.cancel();
		let t = e.activeElement;
		z = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, X(), _++, T = null, g = !0, J();
	}
	function Ce() {
		!h && m && Q(async (e) => {
			if (await Z(e), z) {
				let e = l(z.id);
				if (e?.focus({ preventScroll: !0 }), e && z.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(z.start, z.end);
				} catch {}
				z = null;
			}
		});
	}
	function $() {
		h || (H.cancel(), c(null), X(), h = !0, _++, m = !1, T = null, u = null, d = null, f = null, p = null, C?.(), J());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				U("请从宿主插件管理页面打开此页。");
				return;
			}
			C = t.onContext(ye), Promise.resolve(t.ready()).then((e) => {
				x || ye(e);
			}).catch(() => {
				!x && !h && (m = !1, U("宿主管理会话不可用。"), J());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: be,
		navigationImpact: xe,
		suspend: Se,
		resume: Ce,
		selectOwner(e) {
			B = e, A.owner(e), J();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), A.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && z(document, window.AstrBotPluginPage, window).start();
//#endregion
export { z as createManagementPage, oe as credentialUpdateBody, re as updateBody, P as validateCatalog, ie as validateCredentialCatalog, ae as validateCredentialStatus, F as validateSnapshot };
