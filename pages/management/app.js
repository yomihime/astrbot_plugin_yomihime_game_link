import { createApp as e, defineComponent as t, h as n, nextTick as r, shallowRef as i } from "./runtime.js";
import { NConfigProvider as a, darkTheme as o, dateEnUS as s, dateZhCN as c, enUS as ee, zhCN as l } from "./runtime.js";
//#region src/font-settings.ts
function u(e) {
	let t = e;
	if (!t || typeof t != "object" || !Number.isSafeInteger(t.revision) || t.revision < 0 || ![
		"checking",
		"downloading",
		"ready",
		"unavailable",
		"stopped"
	].includes(t.state) || ![
		"canonical",
		"system",
		"none"
	].includes(t.source) || !(t.error === null || typeof t.error == "string" && /^[a-z_]{1,80}$/.test(t.error)) || typeof t.filename != "string" || !/^[A-Za-z0-9._-]{1,128}\.otf$/.test(t.filename) || !Number.isSafeInteger(t.size) || t.size < 1 || t.size > 20971520 || typeof t.sha256 != "string" || !/^[a-f0-9]{64}$/.test(t.sha256) || typeof t.download_url != "string") throw Error("invalid_font_status");
	return { ...t };
}
function d(e) {
	return e && /^https:\/\/raw\.githubusercontent\.com\/notofonts\/noto-cjk\/[a-f0-9]{40}\/Sans\/OTF\/SimplifiedChinese\/NotoSansCJKsc-Regular\.otf$/.test(e.download_url) ? e.download_url : null;
}
function f(e) {
	if (!e) return "字体状态暂不可用";
	let t = e.source === "system" ? " · 正在使用系统字体" : e.source === "none" ? " · 暂无可用图片字体" : "";
	return e.state === "ready" && e.source === "canonical" ? "标准字体已就绪" : e.state === "checking" ? "正在检查字体" + t : e.state === "downloading" ? "正在下载标准字体" + t : e.error ? "标准字体获取失败" + t : (e.source === "system" ? "正在使用系统字体" : "标准字体暂不可用") + (e.source === "none" ? " · 暂无可用图片字体" : "");
}
function p(e, t, n, r) {
	let i = 0, a = !1, o, s = {
		visible: !1,
		busy: !1,
		enabled: !1,
		message: "",
		status: null,
		progress: "",
		uploadAvailable: typeof e?.upload == "function",
		refresh: () => void h(),
		retry: () => void g(),
		upload: (e) => void _(e)
	}, c = () => n({ ...s }), ee = (e) => !a && s.enabled && i === e, l = () => {
		o !== void 0 && t.clearTimeout(o), o = void 0;
	}, d = (e) => {
		let t = e;
		return [
			"admin_authorization_denied",
			"stale_context",
			"page_revoked",
			"asset_revoked"
		].some((e) => t?.message === e || t?.code === e) || t?.status === 401 || t?.status === 403;
	};
	function f(e) {
		s.progress = "", s.message = d(e) ? "管理授权已失效，请重新打开页面" : e?.message === "revision_conflict" ? "字体状态已变化，请刷新后重试" : "字体操作未确认，请刷新状态后重试；可继续使用现有字体", s.status = null, d(e) && (s.enabled = !1, r());
	}
	function p() {
		l(), s.enabled && ["checking", "downloading"].includes(s.status?.state || "") && (o = t.setTimeout(() => void h(), 2e3));
	}
	async function m(e, t) {
		if (a || !s.enabled || s.busy) return;
		let n = i;
		s.busy = !0, s.message = "", s.progress = t, l(), c();
		try {
			let t = u(await e());
			if (!ee(n)) return;
			s.status = t, s.progress = "";
		} catch (e) {
			ee(n) && f(e);
		} finally {
			ee(n) ? (s.busy = !1, c(), p()) : i === n && (s.busy = !1, c());
		}
	}
	async function h() {
		await m(() => e.apiPost("admin/font-status", {}), "正在读取字体状态…");
	}
	async function g() {
		s.status && !["checking", "downloading"].includes(s.status.state) && await m(() => e.apiPost("admin/font-retry", {}), "正在请求重新下载…");
	}
	async function _(t) {
		if (t && s.enabled && !s.busy && s.status && e.upload && !["checking", "downloading"].includes(s.status.state)) {
			if (t.name !== s.status.filename || t.size !== s.status.size) {
				s.message = "请选择指定版本的 " + s.status.filename + "（文件大小必须匹配）", c();
				return;
			}
			await m(() => e.upload("admin/font-upload", t), "正在上传并校验标准字体…");
		}
	}
	return {
		sync(e, t) {
			let n = e && t;
			(s.visible !== e || s.enabled !== n) && (i++, l(), s.visible = e, s.enabled = n, s.busy = !1, s.progress = "", s.message = "", s.status = null, c(), n && h());
		},
		invalidate() {
			i++, l(), s.enabled = !1, s.busy = !1, s.status = null, s.progress = "", s.message = "", c();
		},
		refresh: h,
		dispose() {
			a = !0, i++, l();
		}
	};
}
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
var m = t({
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
}), h = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-settings-row" }, t.default?.());
} }), g = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-action-bar" }, t.default?.());
} }), _ = t({
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
}), v = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
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
		ff14_calendar_sync_interval_seconds: {
			label: "日历同步周期",
			width: "short",
			order: 4,
			optionLabels: {
				21600: "每6小时",
				43200: "每12小时",
				86400: "每24小时"
			}
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
v.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var y = (e) => v.get(e), b = (e) => v.get(e.module_id)?.fields?.[e.name] || {}, x = (e, t) => t === "reply_style" ? "回复样式" : v.get(e)?.groups?.[t]?.title || t || "普通设置", S = (e) => v.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name, C = (e) => e.group === "reply_style" && (e.module_id === "game_link/core" ? e.name === "image_theme" : e.name === "reply_theme"), w = (e) => C(e) && e.module_id !== "game_link/core" && e.default === null && e.choices?.[0]?.value === null, te = (e) => e.choices?.map((e) => e.value) ?? e.value_schema?.enum ?? (e.value_schema?.type === "boolean" ? [!1, !0] : null);
//#endregion
//#region src/management-view.ts
function T(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (b(e.field).order || 0) - (b(t.field).order || 0))
	}));
}
function E(r, u = r.getElementById("management-root"), p = "settings") {
	let v = i(null), C = i([]), E = i({}), D = i([]), O = i(""), k = i(null), A = i(0), j = i({}), M = i({}), N = () => {}, P = () => {
		A.value++, N();
	}, F = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), I = (e) => !!(e.mode && e.mode.value !== "keep"), L = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function R(e) {
		let t = e.field, r = t.value_schema, i = b(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = te(t), s = () => e.inheritBaseline && !e.writePending && !e.baselineUnverified ? "keep" : "clear", c = () => {
			e.mode && (e.mode.value = w(t) && e.input?.value !== "" && o?.[Number(e.input?.value)] === null ? s() : "replace"), e.draftVersion++, P();
		};
		return n(h, {
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
					e.draftVersion++, P();
				}
			}),
			n(o ? "select" : "input", {
				id: a,
				"data-role": "value",
				"aria-describedby": e.problem ? a + "-detail" : i.hint ? a + "-hint" : void 0,
				onInput: c,
				onChange: c,
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
			}, "请选择有效值"), ...o.map((e, r) => n("option", { value: String(r) }, t.choices?.[r]?.label || i.optionLabels?.[String(e)] || (i.booleanLabels && typeof e == "boolean" ? i.booleanLabels[String(e)] : String(e))))] : void 0),
			n("button", {
				type: "button",
				"data-action": "default",
				ref: (t) => {
					e.reset = t;
				},
				onClick: () => {
					if (!e.mode || !e.input || e.reset?.disabled) return;
					e.mode.value = i.reset === "replace-default" ? "replace" : s(), e.draftVersion++;
					let n = t.default;
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), P();
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
	function ne(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = S(e.field), a = () => {
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
			n(_, { class: "credential-detail" }, { default: () => e.detail }),
			n(h, { "data-width": "medium" }, { default: () => [n("label", { for: r + "-mode" }, "本次操作"), n("select", {
				id: r + "-mode",
				"data-role": "credential-mode",
				ref: (t) => {
					e.mode = t;
				},
				onChange: () => {
					a(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), M.value = {
						...M.value,
						[r + "-client_id"]: !1,
						[r + "-client_secret"]: !1
					}, P();
				}
			}, [
				["keep", "保持（不写入）"],
				["replace", "替换完整凭据对"],
				["clear", "清除已保存凭据"]
			].map(([e, t]) => n("option", { value: e }, t)))] }),
			...["client_id", "client_secret"].map((t) => n(h, {
				class: "credential-field",
				key: t
			}, { default: () => [n("label", { for: r + "-" + t }, t === "client_id" ? "Client ID" : "Client Secret"), n("div", { class: "credential-input ui-control" }, [n("input", {
				id: r + "-" + t,
				type: M.value[r + "-" + t] ? "text" : "password",
				autocomplete: "off",
				maxlength: 512,
				"data-role": t,
				ref: (n) => {
					t === "client_id" ? e.clientId = n : e.clientSecret = n;
				},
				onInput: () => {
					a(), e.confirm && (e.confirm.checked = !1), P();
				},
				onChange: () => {
					a(), e.confirm && (e.confirm.checked = !1), P();
				}
			}), n("button", {
				type: "button",
				"data-action": "reveal",
				"aria-label": (M.value[r + "-" + t] ? "隐藏" : "显示") + "本次输入 " + (t === "client_id" ? "Client ID" : "Client Secret"),
				"aria-pressed": !!M.value[r + "-" + t],
				onClick: () => {
					M.value = {
						...M.value,
						[r + "-" + t]: !M.value[r + "-" + t]
					};
				}
			}, M.value[r + "-" + t] ? "隐藏" : "显示")])] })),
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
					a(), P();
				}
			}), n("span", "确认清除本组已保存凭据")]),
			n(g, {}, { default: () => [n("button", {
				type: "button",
				"data-action": "credential-save",
				disabled: !0,
				ref: (t) => {
					e.save = t;
				},
				onClick: e.onSave
			}, "保存" + i + "凭据"), L(e) ? n("span", {
				class: "dirty-indicator",
				role: "status"
			}, i + "有未保存修改") : null] })
		]);
	}
	function re() {
		let e = v.value;
		if (!e?.visible) return null;
		let t = !e.enabled || e.busy || e.status?.state === "downloading" || e.status?.state === "checking";
		return n("section", {
			"data-font-settings": "",
			"aria-label": "图片字体",
			"aria-busy": String(e.busy)
		}, [
			n(_, { id: "font-status" }, { default: () => e.progress || e.message || f(e.status) }),
			n("p", {
				class: "field-hint",
				id: "font-help"
			}, "启动时自动获取标准字体；下载失败时优先使用系统字体；缺字时回复文字。上传仅接受指定版本并校验文件。"),
			n(g, {}, { default: () => [
				n("button", {
					type: "button",
					"data-action": "font-refresh",
					disabled: !e.enabled || e.busy,
					onClick: e.refresh
				}, "刷新字体状态"),
				n("button", {
					type: "button",
					"data-action": "font-retry",
					disabled: t || !e.status,
					onClick: e.retry
				}, "重试下载"),
				n("label", { for: "font-upload" }, "上传标准字体"),
				n("input", {
					id: "font-upload",
					type: "file",
					accept: ".otf",
					disabled: t || !e.status || !e.uploadAvailable,
					"aria-describedby": "font-help font-status",
					onChange: (t) => {
						let n = t.target, r = n.files?.[0] || null;
						n.value = "", e.upload(r);
					}
				})
			] }),
			e.status ? n("p", { class: "field-hint" }, ["指定文件：" + e.status.filename + " ", d(e.status) ? n("a", {
				href: d(e.status),
				target: "_blank",
				rel: "noopener noreferrer"
			}, "下载指定字体") : null]) : null
		]);
	}
	let ie = t({ setup() {
		return () => {
			A.value;
			let e = E.value.isDark === !0 || E.value.theme === "dark", t = String(E.value.locale || "").startsWith("en"), r = /* @__PURE__ */ new Map();
			for (let e of D.value) {
				let t = JSON.stringify([e.field.module_id, e.field.group || ""]);
				r.has(t) || r.set(t, []), r.get(t).push(e);
			}
			return n(a, {
				preflightStyleDisabled: !0,
				theme: e ? o : null,
				locale: t ? ee : l,
				dateLocale: t ? s : c,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				u.id === "management-root" ? n("h1", p === "maintenance" ? "配置维护" : "设置") : null,
				n("div", { class: "management-toolbar" }, [n(_, { id: "status" }), F("refresh", "刷新配置与版本")]),
				p === "settings" ? n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, C.value.map((e) => {
					let t = e.controls.filter((e) => b(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: k.value !== null && k.value !== e.target
					}, [
						...T(r).map((t) => n(m, {
							title: x(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => [...t.items.map(R), e.target === "game_link/core" && t.group === "reply_style" ? re() : null] })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...T(t).map((t) => n(m, {
							title: x(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(R) }))]) : null,
						n(g, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, y(e.target)?.saveLabel || "保存普通设置"),
							n("button", {
								type: "button",
								"data-action": "discard",
								ref: (t) => {
									e.discard = t;
								},
								onClick: () => e.onDiscard?.()
							}, "放弃本模块修改"),
							e.controls.some(I) ? n("span", {
								class: "dirty-indicator",
								role: "status"
							}, "普通设置有未保存修改") : null
						] })
					]);
				})) : null,
				p === "settings" ? n(_, {
					id: "credential-status",
					tone: "warning",
					hidden: k.value !== null && D.value.length > 0 && !D.value.some((e) => e.field.module_id === k.value)
				}, { default: () => O.value }) : null,
				p === "settings" ? n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === j.value[e]) ? j.value[e] : t[0].field.name;
					return n(m, {
						title: x(t[0].field.module_id, t[0].field.group || ""),
						key: e,
						hidden: k.value !== null && k.value !== t[0].field.module_id
					}, { default: () => [t.length > 1 ? n("nav", {
						class: "credential-tabs",
						"aria-label": "凭据来源"
					}, t.map((t) => n("button", {
						type: "button",
						"aria-pressed": r === t.field.name,
						onClick: () => {
							j.value = {
								...j.value,
								[e]: t.field.name
							};
						}
					}, S(t.field) + (L(t) ? " · 未保存" : "")))) : null, ...t.map((e) => ne(e, r === e.field.name))] });
				})) : null,
				p === "maintenance" ? n(m, {
					title: "限定恢复",
					class: "maintenance-panel"
				}, { default: () => [
					n("p", "仅处理受审普通配置迁移；后续修改会阻止回退。订阅及其它持久数据保留。"),
					F("rollback", "回退受审迁移"),
					n("p", n("label", { class: "ui-check-label" }, [n("input", {
						id: "replacement",
						type: "checkbox",
						onChange: P
					}), n("span", "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）")])),
					F("recover", "重新校验并恢复运行")
				] }) : null
			]) });
		};
	} }), z = e(ie);
	return z.mount(u), {
		font(e) {
			v.value = { ...e };
		},
		owner(e) {
			k.value = e;
		},
		drafts() {
			A.value++;
		},
		maskSecrets() {
			M.value = {};
		},
		fields(e, t) {
			N = t, C.value = e;
		},
		credentials(e, t, n = "") {
			N = t, D.value = e, O.value = n;
		},
		context(e) {
			E.value = e || {};
		},
		dispose() {
			z.unmount();
		}
	};
}
//#endregion
//#region src/ui/notice.ts
function D(e, t) {
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
var O = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), k = (e, t) => O(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), A = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, j = (e = "invalid_config") => {
	throw Error(e);
}, M = (e) => [
	e?.code,
	e?.error_code,
	e?.message
].some((e) => [
	"admin_authorization_denied",
	"stale_context",
	"page_revoked",
	"asset_revoked"
].includes(e)) || [e?.status, e?.response?.status].some((e) => e === 401 || e === 403), N = (e) => JSON.stringify([e.module_id, e.name]), P = (e, t) => e.fields.filter((e) => e.module_id === t), F = (e) => [...e].length;
function I(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && j(), typeof e == "string") {
			F(e) > 4096 && j();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!O(e) && !Array.isArray(e) || r.has(e)) && j(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && j();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && j();
}
function L(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || F(t) >= e.minLength) && (e.maxLength === void 0 || F(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => L(e.items, t)) : e.type === "object" && O(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && L(e.properties[t], n));
}
function R(e, t = 0) {
	(!O(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && j();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && j(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !O(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && j();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || j(), R(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && j();
	} else if (e.type === "array") n.push("items"), R(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && j();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && j(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => L({
			...e,
			enum: void 0
		}, t))) && j();
	}
	Object.keys(e).some((e) => !n.includes(e)) && j();
}
function ne(e) {
	I(e), (!k(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && j();
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
	for (let r of e.fields) if ((!O(r) || !k(r, Object.hasOwn(r, "choices") ? [...t, "choices"] : t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => A.test(e)) || typeof r.name != "string" || !r.name || F(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && A.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && j(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && j(), n.has(N(r)) && j(), n.add(N(r)), r.value_schema !== null && (I(r.value_schema, 16, 2048), R(r.value_schema), r.default !== null && !L(r.value_schema, r.default) && j()), Object.hasOwn(r, "choices")) {
		(!C(r) || r.value_schema?.type !== "string" || !Array.isArray(r.value_schema.enum) || !Array.isArray(r.choices)) && j();
		let e = r.module_id !== "game_link/core", t = e ? [null, ...r.value_schema.enum] : r.value_schema.enum;
		(e ? r.default !== null : !L(r.value_schema, r.default)) && j(), (r.choices.length !== t.length || !r.choices.every((e, n) => k(e, ["value", "label"]) && e.value === t[n] && typeof e.label == "string" && e.label.trim().length > 0 && F(e.label) <= 128 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(e.label))) && j();
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
function re(e, t) {
	t || j(), I(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	k(e, n) || j();
	for (let r of n) {
		let n = e[r], i = P(t, r).filter((e) => e.readable);
		(!k(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !k(n.fields, i.map((e) => e.name))) && j();
		for (let e of i) {
			let t = n.fields[e.name];
			(!k(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !(t.value === null && w(e) && t.present === !1 && t.source === "default") && !L(e.value_schema, t.value))) && j();
		}
	}
	return e;
}
var ie = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function z(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let n = te(e);
		(!/^\d+$/.test(t) || !Object.hasOwn(n, Number(t))) && j(), r = n[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && j(), r = Number(t));
	return !(r === null && w(e)) && !L(n, r) && j(), r;
}
function ae(e, t, n, r) {
	(!r || !O(n) || !Number.isSafeInteger(t) || t < 1) && j();
	let i = P(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && j();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		if ((!O(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && j(), t.mode === "keep") continue;
		(!n.readable || !n.editable || !ie(n)) && j("admin_authorization_denied");
		let r = t.mode === "clear" ? null : z(n, t.value);
		a.push(t.mode === "clear" || r === null && w(n) ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: r
		});
	}
	return a.length || j("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function oe(e) {
	I(e), (!k(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && j();
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
		(!k(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => A.test(e)) || typeof r.name != "string" || !A.test(r.name) || typeof r.group != "string" || !A.test(r.group) || typeof r.description != "string" || !r.description || t.has(N(r))) && j(), t.add(N(r)), (!k(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !k(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && j();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!k(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && j();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function se(e, t) {
	I(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	k(e, n) || j();
	for (let r of n) {
		let n = e[r];
		(!k(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !k(n.fields, P(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && j();
	}
	return e;
}
function ce(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && j(), r === "replace" && (!k(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || F(e) < 1 || F(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && j(), {
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
var B = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function le(e, t) {
	(!O(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && j("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !B.has(e)));
	I(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && j("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : O(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function V(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), scope: o = "settings", initiallySuspended: s = !1, beforeMaintenance: c = async (e) => !1, onDraftMutation: ee = () => {}, onConfigurationSaved: l = () => {}, onCapabilities: u = (e, t = !1) => {} } = {}) {
	a || j("invalid_container");
	let d = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, f = null, m = null, h = null, g = null, _ = !1, v = !1, y = !0, b = 0, x = null, S = !1, C = null, T = null, O = E(e, a, o), A = /* @__PURE__ */ new Map(), F = /* @__PURE__ */ new Map(), I = d("status"), L = s, R = p(t, n, (e) => O.font(e), () => ve()), z = /* @__PURE__ */ new Map(), B = !1, V = null, H = null, ue = o === "maintenance" ? "[[],[],false]" : "[[],[]]", de = ue, U = D(n, (e, t) => {
		I.textContent = e, I.dataset.tone = t;
	}), W = (e, t = "error") => U.show(e, t), fe = "当前加密不可用，请管理员检查宿主密钥与加密组件；修改外部环境后需正常重启同一宿主，再重开此页。已有凭据时请保留原密钥。";
	function G() {
		let e = JSON.stringify([
			[...F].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
				e,
				t.mode.value,
				t.input?.value
			]),
			[...z].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
				e,
				t.mode?.value,
				t.clientId?.value,
				t.clientSecret?.value,
				t.confirm?.checked
			]),
			...o === "maintenance" ? [!!d("replacement")?.checked] : []
		]);
		e !== de && (de = e, ee()), O.drafts(), Y();
	}
	let K = (e) => !v && _ && b === e, pe = (e) => e.readable && e.editable && ie(e), q = (e) => !e || e.disabled || e.getAttribute("aria-disabled") === "true";
	function J(t, n) {
		t && (t.setAttribute("aria-disabled", String(n)), t.disabled = n && (v || !_ || e.activeElement !== t));
	}
	function Y() {
		let e = o === "settings" && (H === null || H === "game_link/core") && !!f?.fields.some((e) => e.module_id === "game_link/core" && e.group === "reply_style" && e.readable);
		R.sync(e, !v && _ && !L && !y), J(d("refresh"), v || !_ || T !== null);
		let t = !v && _ && !y && m !== null && Object.keys(m).length > 0 && T === null;
		J(d("rollback"), !t), J(d("recover"), !t || f.fields.some((e) => e.readable && !e.editable)), d("replacement") && (d("replacement").disabled = q(d("recover")));
		for (let [e, n] of A) {
			let r = H === null || H === e;
			J(n.save, !t || !r || !f || !P(f, e).some(pe)), n.discard && J(n.discard, !_ || v || T !== null || !r);
		}
		for (let e of F.values()) {
			let t = !v && _ && (H === null || H === e.field.module_id) && pe(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of z.values()) {
			let t = !v && _ && (H === null || H === e.field.module_id) && g !== null && h !== null && h.fields.some((t) => N(t) === N(e.field) && JSON.stringify(t) === e.signature);
			e.save && J(e.save, !t || y || T !== null || e.mode?.value === "replace" && !B), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !B);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !B || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function X(e, n, r) {
		K(r) || j("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return K(r) || j("stale_context"), e === "catalog" ? ne(i) : e === "read" ? i : e === "credential-catalog" ? oe(i) : e === "credential-readiness" ? ((!k(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && j("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!k(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && j("operation_unavailable"), e === "rollback" && (!k(i, ["rolled_back"]) || i.rolled_back !== !0) && j("operation_unavailable"), e === "recover" && (!k(i, ["recovered"]) || i.recovered !== !0) && j("operation_unavailable"), i);
	}
	function me(e, t) {
		let n = te(e);
		return t === null && !w(e) ? "" : String(n ? n.findIndex((e) => e === t) : t);
	}
	function he(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function ge(e) {
		let t = A.get(e).save;
		q(t) || $(async (t) => {
			let n = Object.fromEntries(P(f, e).map((e) => {
				let t = F.get(N(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			})), r = ae(e, m[e].revision, n, f), i = r.updates.map((t) => F.get(JSON.stringify([e, t.field])));
			for (let e of i) e.writePending = !0, e.baselineUnverified = !0;
			try {
				await X("update", r, t), l();
				try {
					await Q(t);
				} catch (e) {
					if (!K(t)) throw e;
					y = !0, j(M(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
				}
				for (let [t, r] of Object.entries(n)) {
					if (r.mode === "keep") continue;
					let n = F.get(JSON.stringify([e, t])), i = m[e]?.fields[t];
					n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = i.state === "valid" ? me(n.field, i.value) : "");
				}
				G(), W("已保存。", "success");
			} finally {
				for (let e of i) e.writePending = !1;
			}
		});
	}
	function _e(e) {
		if (!v && _ && T === null && m?.[e]) {
			for (let t of P(f, e)) {
				let n = F.get(N(t)), r = m[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = r.state === "valid" ? me(t, r.value) : "");
			}
			for (let t of z.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			O.maskSecrets(), G(), W("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function Z() {
		O.maskSecrets();
		for (let e of z.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		G();
	}
	function ve(e = "管理授权不可用或页面已失效，请重新打开宿主管理页面后手动刷新。", t = "error") {
		b++, T = null, y = !0, B = !1, h = null, g = null, R.invalidate(), Z(), u(null), Y(), W(e, t);
	}
	function ye(e) {
		q(e.save) || e.mode.value === "replace" && !B || $(async (t) => {
			let n = e.mode.value;
			n === "keep" && j("select_changes"), n === "clear" && !e.confirm.checked && j("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await X("credential-update", ce(e.field.module_id, g[e.field.module_id].revision, e.field.name, n, i, h), t);
			let a = !1;
			try {
				a = (await Q(t)).credentials;
			} catch (e) {
				if (!K(t)) throw e;
				M(e) && j("write_ack_authorization_denied");
			}
			e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), G(), a || (y = !0, j("write_ack_readback_unverified")), W("已保存。", "success");
		});
	}
	async function be() {
		let e = new Set(h.fields.map(N));
		for (let [t, n] of z) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			z.delete(t);
		}
		for (let e of h.fields) {
			let t = N(e), n = JSON.stringify(e), r = z.get(t);
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
				}, r.onSave = () => ye(r), z.set(t, r);
			}
			let i = g[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		O.credentials([...z.values()], G), await r(), Y();
	}
	async function xe() {
		let e = [...new Set(f.fields.map((e) => e.module_id))], t = new Set(f.fields.map(N));
		for (let e of F.keys()) t.has(e) || F.delete(e);
		for (let t of A.keys()) e.includes(t) || A.delete(t);
		let n = [];
		for (let t of e) {
			let e = A.get(t);
			e || (e = {
				target: t,
				controls: [],
				save: null,
				discard: null,
				onSave: () => ge(t),
				onDiscard: () => _e(t)
			}, A.set(t, e)), e.controls = [];
			for (let r of P(f, t)) {
				let i = N(r), a = JSON.stringify(r), o = F.get(i);
				(!o || o.signature !== a) && (o = he(r), F.set(i, o)), o.baselineUnverified = !1;
				let s = r.readable ? m[t].fields[r.name] : null;
				o.inheritBaseline = !!(w(r) && s?.state === "valid" && s.value === null && s.present === !1 && s.source === "default"), o.problem = r.readable ? r.editable ? ie(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前只读，请核对模块状态。" : "无读取或修改权限。", ie(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s?.state === "valid" ? me(r, s.value) : "",
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
		O.fields([...A.values()], G), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		Y();
	}
	async function Q(e) {
		y = !0, Y();
		let t = await X("catalog", {}, e), n = re(await X("read", {}, e), t);
		K(e) || j("stale_context"), f = t, m = n;
		let i = "";
		try {
			let t = await X("credential-catalog", {}, e), n = se(await X("credential-status", {}, e), t);
			K(e) || j("stale_context"), h = t, g = n, B = !1;
			try {
				B = (await X("credential-readiness", {}, e)).ready, i = B ? "" : fe;
			} catch (t) {
				if (!K(e) || M(t)) throw t;
				i = "未能确认加密状态，暂不能填写凭据；请核对宿主管理会话与部署后重开此页。";
			}
			B || Z();
		} catch (t) {
			if (!K(e)) throw t;
			if (h = null, g = null, M(t)) throw Z(), t;
			i = "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		return y = !1, o === "settings" && (await xe(), h ? (await be(), O.credentials([...z.values()], G, i), await r(), Y()) : O.credentials([...z.values()], G, i)), K(e) || j("stale_context"), u([...new Set([...f.fields, ...h?.fields || []].map((e) => e.module_id))], h !== null), { credentials: h !== null };
	}
	async function $(e) {
		if (T || v || !_) return;
		let t = { generation: b };
		T = t, Y();
		try {
			await e(t.generation);
		} catch (e) {
			if (!K(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (y = !0, W("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? W("请选择需要修改的字段。") : e?.message === "confirm_clear" ? W("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "write_ack_readback_unverified" ? W("已提交，状态暂未核实，请刷新确认。", "warning") : e?.message === "write_ack_authorization_denied" ? ve("已提交，但管理授权不可用，状态暂未核实。请重新打开宿主管理页面确认。", "warning") : e?.message === "secret_encryption_unavailable" ? W("未保存。" + fe) : e?.message === "invalid_config" ? W("配置或输入未通过校验，请核对字段要求后刷新重试。") : M(e) ? ve() : (y = !0, W("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			T === t && (T = null, Y());
		}
	}
	let Se = () => Object.fromEntries(Object.entries(m).map(([e, t]) => [e, t.revision]));
	d("refresh").addEventListener("click", () => !q(d("refresh")) && $(async (e) => {
		let t = await Q(e);
		W(t.credentials ? "已刷新。" : "普通设置已刷新，凭据状态暂未核实。", t.credentials ? "success" : "warning");
	}));
	for (let e of ["rollback", "recover"]) d(e)?.addEventListener("click", () => !q(d(e)) && $(async (t) => {
		if (await c({
			action: e,
			isCurrent: () => K(t)
		}) && K(t)) {
			await X(e, {
				expected_revisions: Se(),
				...e === "recover" ? { complete_from_current: d("replacement").checked } : {}
			}, t);
			try {
				await Q(t);
			} catch (e) {
				if (!K(t)) throw e;
				y = !0, j(M(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			l(), W(e === "rollback" ? "受审迁移已限定回退，其他数据保留。" : "配置已重新校验，业务运行已恢复。", "success");
		}
	}));
	function Ce(t) {
		if (v) return;
		O.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = le(t, i);
		} catch {
			R.invalidate(), u(null), Z(), S = !0, b++, _ = !1, y = !0, T = null, x = null, f = null, m = null, h = null, g = null, O.fields([], G), O.credentials([], G), A.clear(), F.clear(), z.clear(), de = ue, Y(), W("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		S = !0, !(_ && n === x) && (R.invalidate(), U.clear(), u(null), Z(), b++, x = n, _ = !0, y = !0, T = null, f = null, m = null, h = null, g = null, O.fields([], G), O.credentials([], G), A.clear(), F.clear(), z.clear(), de = ue, Y(), $(async (e) => {
			await Q(e);
		}));
	}
	let we = () => [...F.values()].some((e) => e.mode?.value !== "keep") || [...z.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function Te() {
		let e = [...new Set([...F.values()].filter((e) => (H === null || e.field.module_id === H) && ["replace", "clear"].includes(e.mode?.value)).map((e) => e.field.module_id))], t = [...new Set([...z.values()].filter((e) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map((e) => e.field.module_id))];
		return {
			ordinaryOwners: e,
			credentialOwners: t,
			requiresConfirmation: e.length > 0 || t.length > 0
		};
	}
	function Ee() {
		L = !0, R.invalidate(), I.dataset.tone === "success" ? U.clear() : U.cancel();
		let t = e.activeElement;
		V = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, Z(), b++, T = null, y = !0, Y();
	}
	function De() {
		!v && _ && (L = !1, $(async (e) => {
			if (await Q(e), V) {
				let e = d(V.id);
				if (e?.focus({ preventScroll: !0 }), e && V.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(V.start, V.end);
				} catch {}
				V = null;
			}
		}));
	}
	function Oe() {
		v || (R.dispose(), U.cancel(), u(null), Z(), v = !0, b++, _ = !1, T = null, f = null, m = null, h = null, g = null, C?.(), Y());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				W("请从宿主插件管理页面打开此页。");
				return;
			}
			C = t.onContext(Ce), Promise.resolve(t.ready()).then((e) => {
				S || Ce(e);
			}).catch(() => {
				!S && !v && (_ = !1, W("宿主管理会话不可用。"), Y());
			}), n?.addEventListener("pagehide", Oe, { once: !0 });
		},
		close: Oe,
		dirty: we,
		navigationImpact: Te,
		suspend: Ee,
		resume: De,
		selectOwner(e) {
			H = e, O.owner(e), Y();
		},
		dispose() {
			Oe(), n?.removeEventListener("pagehide", Oe), O.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && V(document, window.AstrBotPluginPage, window).start();
//#endregion
export { V as createManagementPage, ce as credentialUpdateBody, ae as updateBody, ne as validateCatalog, oe as validateCredentialCatalog, se as validateCredentialStatus, re as validateSnapshot };
