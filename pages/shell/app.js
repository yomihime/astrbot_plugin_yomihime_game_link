import { createApp as e, defineComponent as t, h as n, nextTick as r, onBeforeUnmount as i, onMounted as a, ref as o, shallowRef as s } from "./runtime.js";
import { NAlert as c, NButton as l, NCard as u, NConfigProvider as d, darkTheme as f, dateEnUS as p, dateZhCN as m, enUS as h, zhCN as g } from "./runtime.js";
import { loadPage as _ } from "./module-loader.js";
//#region src/ui/components.ts
var v = t({
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
}), y = t({
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
}), b = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-settings-row" }, t.default?.());
} }), x = t({ setup(e, { slots: t }) {
	return () => n("div", { class: "ui-action-bar" }, t.default?.());
} }), S = t({
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
}), C = (e) => typeof e == "object" && !!e && !Array.isArray(e), w = (e, t = 4096) => typeof e == "string" && e.length > 0 && e.length <= t, T = (e) => w(e) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\/(?:[a-zA-Z0-9_.-]+\/)*[a-zA-Z0-9_.-]+$/.test(e) && !e.split("/").some((e) => e === "." || e === "..");
function E(e) {
	if (e == null) return null;
	let t = (e, t) => typeof e == "string" && e.trim().length > 0 && [...e].length <= t && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(e), n = () => {
		throw Error("invalid_catalog");
	};
	(!C(e) || Object.keys(e).some((e) => ![
		"default_name",
		"localized_names",
		"short_name"
	].includes(e)) || !t(e.default_name, 128)) && n();
	let r = e, i = Object.hasOwn(r, "localized_names") ? r.localized_names : {}, a = r.short_name ?? null;
	(!C(i) || Object.keys(i).length > 16 || a !== null && !t(a, 32)) && n();
	let o = i, s = /* @__PURE__ */ new Set();
	for (let [e, r] of Object.entries(o)) {
		let i = e.toLowerCase(), a = i.split("-");
		(!/^[a-z]{2,8}(?:-[a-z]{4})?(?:-(?:[a-z]{2}|[0-9]{3}))?(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3})){0,4}$/i.test(e) || s.has(i) || new Set(a).size !== a.length || !t(r, 128)) && n(), s.add(i);
	}
	return {
		default_name: r.default_name,
		localized_names: { ...o },
		short_name: a
	};
}
function D(e, t, n = !1) {
	let r = e.display;
	if (!r) return e.module_id;
	if (n && r.short_name) return r.short_name;
	let i = t.toLowerCase(), a = Object.entries(r.localized_names);
	return a.find(([e]) => e.toLowerCase() === i)?.[1] ?? a.find(([e]) => e.toLowerCase() === i.split("-")[0])?.[1] ?? r.default_name ?? e.module_id;
}
function O(e, t, n = !1) {
	let r = e.map((e) => D(e, t, n));
	return new Map(e.map((e, t) => [e.module_id, r.filter((e) => e.normalize("NFC").toLowerCase() === r[t].normalize("NFC").toLowerCase()).length > 1 ? r[t] + " · " + e.module_id : r[t]]));
}
function k(e) {
	let t = () => {
		throw Error("invalid_catalog");
	};
	(!C(e) || e.schema_version !== 1 || !C(e.runtime) || ![
		"ready",
		"not_ready",
		"invalid_config",
		"closing",
		"closed"
	].includes(String(e.runtime.state)) || new TextEncoder().encode(JSON.stringify(e)).length > 262144) && t();
	let n = e;
	if (n.modules === null) return (n.catalog_revision !== null || !["not_ready", "invalid_config"].includes(n.runtime.state)) && t(), {
		schema_version: 1,
		catalog_revision: null,
		runtime: { state: n.runtime.state },
		modules: null
	};
	(!Number.isSafeInteger(n.catalog_revision) || n.catalog_revision < 0 || !Array.isArray(n.modules) || n.modules.length > 64) && t();
	let r = n.modules.map((e) => {
		(!C(e) || !w(e.module_id) || !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(e.module_id) || !w(e.route) || ![
			"loaded",
			"disabled",
			"unavailable"
		].includes(String(e.state)) || !Number.isSafeInteger(e.module_epoch) || Number(e.module_epoch) < 0 || !Array.isArray(e.pages) || e.pages.length > 64 || !Array.isArray(e.resources) || e.resources.length > 128) && t();
		let r = e;
		(r.state === "loaded" ? !w(r.runtime_id) || r.enabled !== !0 || r.lifecycle !== "active" || n.runtime.state !== "ready" : r.runtime_id !== null || r.pages.length || r.resources.length) && t(), (!w(r.asset_version, 64) || !/^[a-f0-9]{64}$/.test(r.asset_version)) && t();
		let i = `module-assets/${r.module_id}/`, a = r.resources.map((e) => ((!C(e) || !T(e.path) || !e.path.startsWith(i) || !w(e.sha256, 64) || !/^[a-f0-9]{64}$/.test(e.sha256)) && t(), {
			path: e.path,
			sha256: e.sha256
		}));
		new Set(a.map((e) => e.path)).size !== a.length && t();
		let o = r.pages.map((e) => ((!C(e) || !w(e.route_id, 128) || !/^[a-z][a-z0-9_.-]*$/.test(e.route_id) || !w(e.title, 256) || !Number.isSafeInteger(e.order) || e.access !== "public_web" || !(e.capability_id === null || w(e.capability_id, 128)) || !T(e.entry) || !e.entry.startsWith(i) || !e.entry.endsWith(".js") || !Array.isArray(e.styles) || e.styles.length > 16 || !e.styles.every((e) => T(e) && e.startsWith(i) && e.endsWith(".css"))) && t(), [e.entry, ...e.styles].every((e) => a.some((t) => t.path === e)) || t(), {
			route_id: e.route_id,
			title: e.title,
			order: e.order,
			access: "public_web",
			capability_id: e.capability_id,
			entry: e.entry,
			styles: [...e.styles]
		}));
		return new Set(o.map((e) => e.route_id)).size !== o.length && t(), {
			display: E(r.display),
			module_id: r.module_id,
			route: r.route,
			state: r.state,
			module_epoch: r.module_epoch,
			runtime_id: r.runtime_id,
			asset_version: r.asset_version,
			pages: o.sort((e, t) => e.order - t.order || e.route_id.localeCompare(t.route_id)),
			resources: a
		};
	});
	return new Set(r.map((e) => e.module_id)).size !== r.length && t(), {
		schema_version: 1,
		catalog_revision: n.catalog_revision,
		runtime: { state: n.runtime.state },
		modules: r
	};
}
function A(e, t) {
	return `#/module/${encodeURIComponent(e)}/${encodeURIComponent(t)}`;
}
function j(e) {
	let t = /^#\/module\/([^/]+)\/([^/]+)$/.exec(e);
	if (!t) return null;
	try {
		return {
			owner: decodeURIComponent(t[1]),
			route: decodeURIComponent(t[2])
		};
	} catch {
		return null;
	}
}
//#endregion
//#region src/lifecycle.ts
function M(e) {
	let t = new AbortController(), n = /* @__PURE__ */ new Set(), r = !1;
	return Object.freeze({
		signal: t.signal,
		isCurrent: () => !r && e(),
		onDispose(e) {
			if (r) {
				try {
					e();
				} catch (t) {
					throw n.add(e), t;
				}
				return () => {
					n.delete(e);
				};
			}
			return n.add(e), () => {
				n.delete(e);
			};
		},
		dispose() {
			if (r && !n.size) return;
			r = !0, t.abort();
			let e = [];
			for (let t of n) try {
				t(), n.delete(t);
			} catch (t) {
				e.push(t);
			}
			if (e.length) throw AggregateError(e, "cleanup_pending");
		}
	});
}
var N = /* @__PURE__ */ new Set([
	"isDark",
	"theme",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function P(e) {
	let t = /* @__PURE__ */ new Set(), n = (e) => {
		if (e === null || typeof e == "string" || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e) && !Object.is(e, -0)) return e;
		if (!e || typeof e != "object" || t.has(e)) throw Error("invalid_context");
		if (Array.isArray(e)) {
			if (Object.keys(e).length !== e.length || Object.keys(e).some((e, t) => e !== String(t))) throw Error("invalid_context");
		} else if (![Object.prototype, null].includes(Object.getPrototypeOf(e))) throw Error("invalid_context");
		t.add(e);
		let r = Array.isArray(e) ? e.map(n) : Object.fromEntries(Object.keys(e).sort().map((t) => [t, n(e[t])]));
		return t.delete(e), r;
	};
	if (!e || typeof e != "object" || Array.isArray(e) || ![Object.prototype, null].includes(Object.getPrototypeOf(e))) throw Error("invalid_context");
	return JSON.stringify(n(Object.fromEntries(Object.entries(e).filter(([e]) => !N.has(e)))));
}
function F(e, t, n) {
	return JSON.stringify([
		e.module_id,
		e.module_epoch,
		e.runtime_id,
		e.asset_version,
		t.route_id,
		t.entry,
		t.styles,
		t.capability_id,
		n
	]);
}
//#endregion
//#region src/module-assets.ts
function I(e, t) {
	return JSON.stringify([
		e.module_id,
		e.runtime_id,
		e.module_epoch,
		e.asset_version,
		t,
		e.resources,
		e.pages.map((e) => [
			e.route_id,
			e.entry,
			e.styles,
			e.capability_id
		])
	]);
}
function L(e, t, n) {
	let r = /* @__PURE__ */ new Map();
	function i(e) {
		let t = r.get(e);
		if (!t) {
			let n = {
				revoked: !1,
				scope: M(() => !n.revoked),
				styles: /* @__PURE__ */ new Map(),
				entries: /* @__PURE__ */ new Map()
			};
			r.set(e, n), t = n;
		}
		if (t.revoked) throw Error("asset_revoked");
		return t;
	}
	function a(t, r, i) {
		let a = r.styles.get(i);
		if (a) return a;
		let o = new Promise((a, o) => {
			let s = e.getElementById("module-style-assets"), c = Array.from(s?.content.querySelectorAll("link[data-resource]") || []).find((e) => e.dataset.resource === i && e.rel === "stylesheet");
			if (!c) {
				o(Error("missing_style_projection"));
				return;
			}
			let l = c.cloneNode(!0);
			l.dataset.moduleStyle = t;
			let u = !1, d = (e) => {
				u || (u = !0, clearTimeout(m), l.removeEventListener("load", f), l.removeEventListener("error", p), e ? o(e) : a());
			}, f = () => d(), p = () => d(Error("style_load_failed")), m = setTimeout(() => d(Error("style_load_timeout")), n);
			l.addEventListener("load", f), l.addEventListener("error", p), r.scope.onDispose(() => {
				d(Error("asset_revoked")), l.remove();
			});
			try {
				e.head.append(l);
			} catch {
				d(Error("style_load_failed"));
			}
		});
		return r.styles.set(i, o), o;
	}
	return {
		async load(e, n) {
			let r = i(e.module_id), o = n.styles.map((t) => a(e.module_id, r, t)), s = r.entries.get(n.entry);
			s || (s = Promise.resolve().then(() => t(n.entry)), r.entries.set(n.entry, s));
			let [c] = await Promise.all([s, ...o]);
			if (r.revoked) throw Error("asset_revoked");
			return c;
		},
		invalidate(e) {
			let t = r.get(e);
			if (t) {
				t.revoked = !0;
				try {
					t.scope.dispose(), r.delete(e);
				} catch {}
			}
		},
		invalidateAll() {
			for (let e of r.keys()) this.invalidate(e);
		},
		retryCleanup() {
			for (let [e, t] of r) t.revoked && this.invalidate(e);
		},
		get cleanupPending() {
			return [...r.values()].some((e) => e.revoked);
		}
	};
}
//#endregion
//#region src/controller.ts
function R({ bridge: e, container: t, window: n, loadPage: r, changed: i, timeoutMs: a = 8e3, expectedPlugin: o = "astrbot_plugin_yomihime_game_link", expectedPage: s = "shell" }) {
	let c = {
		catalog: null,
		busy: !1,
		stale: !1,
		message: "正在连接宿主。",
		theme: "light",
		locale: "zh-CN",
		selected: null,
		mounted: !1,
		cleanupPending: !1,
		home: !0,
		settings: !1,
		settingsOwner: null,
		moduleOwner: null,
		moduleSection: null
	}, l = !1, u = !1, d = 0, f = 0, p = "", m = !1, h, g = null, _ = "", v = "", y = null, b = L(t.ownerDocument, r, a), x = /* @__PURE__ */ new Map(), S = /* @__PURE__ */ new Set(), C = "模块资源已失效。请关闭此页，并从宿主插件详情重新打开页面。", w = !1, T = !1, E = "", D = null, O = /* @__PURE__ */ new Set(), N = (e) => new Promise((t, n) => {
		let r = setTimeout(() => {
			O.delete(r), n(Error("timeout"));
		}, a);
		O.add(r), e.then(t, n).finally(() => {
			clearTimeout(r), O.delete(r);
		});
	});
	function R() {
		if (c.settings) return null;
		let e = c.catalog?.modules?.find((e) => e.module_id === c.selected?.owner), t = e?.pages.find((e) => e.route_id === c.selected?.route);
		return e?.state === "loaded" && t ? {
			module: e,
			page: t
		} : null;
	}
	function z(e) {
		return Object.freeze({
			theme: c.theme,
			locale: c.locale,
			available: u && !c.busy && !c.stale && !D && !w && !S.has(e.module_id),
			owner: e.module_id,
			runtimeId: e.runtime_id,
			epoch: e.module_epoch,
			boundary: p
		});
	}
	function B() {
		c.cleanupPending = D !== null || b.cleanupPending, c.cleanupPending && (c.message = "页面清理未完成，请重试页面清理。");
	}
	function V(e) {
		S.add(e), E === e && U(), b.invalidate(e), B();
	}
	function H(e) {
		let t = new Map((e.modules || []).filter((e) => e.state === "loaded").map((e) => [e.module_id, I(e, p)]));
		if (T) {
			for (let [e, n] of x) t.get(e) !== n && V(e);
			for (let e of t.keys()) x.has(e) || V(e);
		}
		x.clear();
		for (let [e, n] of t) x.set(e, n);
		T = !0;
	}
	function U() {
		D ||= (f++, v = "", _ = "", c.mounted = !1, {
			scope: y,
			page: g,
			scopeDone: y === null,
			pageDone: g === null,
			domDone: !1
		});
		let e = D, n = [];
		if (!e.scopeDone) try {
			e.scope.dispose(), e.scopeDone = !0;
		} catch (e) {
			n.push(e);
		}
		if (e.scopeDone && !e.pageDone) try {
			e.page.dispose(), e.pageDone = !0;
		} catch (e) {
			n.push(e);
		}
		if (e.scopeDone && e.pageDone && !e.domDone) try {
			t.replaceChildren(), e.domDone = !0;
		} catch (e) {
			n.push(e);
		}
		e.scopeDone && e.pageDone && e.domDone && (y = null, g = null, D = null, E = ""), B();
	}
	async function W() {
		if (D) {
			i();
			return;
		}
		let n = R();
		if (!n || !u) {
			U(), !c.cleanupPending && (w || S.has(c.selected?.owner || "")) && (c.message = C), i();
			return;
		}
		let { module: r, page: a } = n, o = F(r, a, p);
		if (w || S.has(r.module_id)) {
			U(), c.cleanupPending || (c.message = C), i();
			return;
		}
		if (o === _ && g) {
			g.update(z(r)), i();
			return;
		}
		if (o === v) return;
		if (U(), D) {
			i();
			return;
		}
		v = o, E = r.module_id;
		let s = f, d = M(() => !l && f === s && R() !== null);
		y = d;
		try {
			let n = await N(b.load(r, a));
			if (!d.isCurrent() || v !== o) return;
			if (!n || typeof n.mount != "function") throw Error("invalid_page_entry");
			let s = t.ownerDocument.createElement("div");
			s.className = "module-view", s.dataset.owner = r.module_id, t.append(s);
			let l = Object.freeze({ async invoke(t, n) {
				if (!d.isCurrent() || c.stale || c.busy || !u || t !== a.capability_id) throw Error("page_unavailable");
				let i = await e.apiPost("invoke", {
					owner: r.module_id,
					page: a.route_id,
					capability_id: t,
					parameters: n
				});
				if (!d.isCurrent() || c.stale || c.busy || !u) throw Error("page_revoked");
				return i;
			} }), f = n.mount(s, Object.freeze({
				routeId: a.route_id,
				context: z(r),
				services: l,
				scope: d
			}));
			if (!f || typeof f.update != "function" || typeof f.dispose != "function") throw Error("invalid_page_instance");
			g = f, _ = o, v = "", c.mounted = !0, i();
		} catch {
			!l && !w && !S.has(r.module_id) && (V(r.module_id), !c.cleanupPending && c.selected?.owner === r.module_id && (c.message = C), i());
		}
	}
	function G() {
		let e = n.location.hash;
		if (c.settings = e === "#/settings" || e.startsWith("#/settings/module/"), c.home = !e || e === "#/" || e === "#", c.settingsOwner = null, c.moduleOwner = null, c.moduleSection = null, c.settings) {
			if (e.startsWith("#/settings/module/")) try {
				c.settingsOwner = decodeURIComponent(e.slice(18)), c.moduleOwner = c.settingsOwner;
			} catch {}
			c.selected = null;
			return;
		}
		let t = /^#\/module\/([^/]+)(\/\-\/queries)?$/.exec(e);
		if (t) {
			try {
				c.moduleOwner = decodeURIComponent(t[1]), c.moduleSection = t[2] ? "queries" : "home";
			} catch {}
			c.selected = null;
			return;
		}
		c.selected = j(e), c.moduleOwner = c.selected?.owner || null;
	}
	async function ee() {
		if (l || !u || c.busy) return;
		let t = ++d;
		c.busy = !0, c.stale = c.catalog !== null, c.message = c.stale ? "正在刷新；当前为上次目录，暂不可提交查询。" : "正在读取模块目录。";
		let n = R();
		n && g && g.update(z(n.module)), i();
		try {
			let n = k(await N(e.apiGet("catalog", {})));
			if (l || t !== d) return;
			H(n), c.catalog = n, c.busy = !1, c.stale = !1, c.cleanupPending || (c.message = "状态已更新；查询仅在手动提交时执行。"), G(), await W();
		} catch {
			if (l || t !== d) return;
			c.busy = !1, c.stale = c.catalog !== null, c.message = "状态读取失败。保留上次目录，请确认宿主会话后手动重试。";
			let e = R();
			e && g && g.update(z(e.module)), i();
		}
	}
	function K(e, t = !1) {
		if (l || w || !t && m) return;
		c.theme = typeof e?.isDark == "boolean" ? e.isDark ? "dark" : "light" : e?.theme === "dark" ? "dark" : "light", c.locale = typeof e?.locale == "string" ? e.locale : "zh-CN";
		let n = () => {
			w = !0, d++, u = !1, c.busy = !1, c.stale = c.catalog !== null, U(), b.invalidateAll(), B(), c.cleanupPending || (c.message = C), i();
		}, r;
		try {
			if (r = P(e), !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== o || e.pageName !== s) throw Error("wrong_page");
		} catch {
			m = !0, n();
			return;
		}
		if (m && u && r === p) {
			let e = R();
			e && g && g.update(z(e.module)), i();
			return;
		}
		if (m) {
			n();
			return;
		}
		m = !0, p = r, u = !0, ee();
	}
	let q = () => {
		G(), W(), t.ownerDocument.getElementById("page-root")?.focus({ preventScroll: !0 }), n.scrollTo({
			top: 0,
			left: 0,
			behavior: "instant"
		});
	};
	return {
		state: c,
		selected: R,
		refresh: ee,
		selectHome() {
			n.location.hash = "#/", G(), W();
		},
		selectModule(e, t = "home") {
			n.location.hash = "#/module/" + encodeURIComponent(e) + (t === "queries" ? "/-/queries" : ""), G(), W();
		},
		selectSettings(e) {
			n.location.hash = e ? "#/settings/module/" + encodeURIComponent(e) : "#/settings", G(), W();
		},
		select(e, t) {
			n.location.hash = A(e, t), G(), W();
		},
		retryCleanup() {
			c.cleanupPending && (D && U(), b.retryCleanup(), B(), c.cleanupPending || (c.message = w || S.has(c.selected?.owner || "") ? C : "页面清理完成，请手动刷新状态。", l || W()), i());
		},
		start() {
			if (!l) {
				if (n.addEventListener("hashchange", q), !e?.ready || !e?.onContext || !e?.apiGet || !e?.apiPost) {
					c.message = "宿主连接尚未就绪，请重新打开页面。", i();
					return;
				}
				h = e.onContext((e) => K(e, !0)), N(Promise.resolve(e.ready())).then((e) => K(e)).catch(() => {
					!u && !l && (c.message = "连接宿主失败，请重新打开页面。", i());
				});
			}
		},
		dispose() {
			if (!l) {
				l = !0, d++, u = !1, h?.(), n.removeEventListener("hashchange", q);
				for (let e of O) clearTimeout(e);
				O.clear(), U(), b.invalidateAll(), B();
			}
		}
	};
}
//#endregion
//#region src/navigation.ts
function z(e) {
	e && (e.focus({ preventScroll: !0 }), e.scrollIntoView({
		block: "start",
		inline: "nearest",
		behavior: "instant"
	}));
}
//#endregion
//#region src/ui/presentation.ts
var B = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
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
B.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var V = (e) => B.get(e), H = (e) => B.get(e.module_id)?.fields?.[e.name] || {}, U = (e, t) => B.get(e)?.groups?.[t]?.title || t || "普通设置", W = (e) => B.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name;
//#endregion
//#region src/management-view.ts
function G(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (H(e.field).order || 0) - (H(t.field).order || 0))
	}));
}
function ee(r, i = r.getElementById("management-root")) {
	let a = s([]), o = s({}), l = s([]), u = s(""), _ = s(null), v = s(0), C = s({}), w = s({}), T = () => {}, E = () => {
		v.value++, T();
	}, D = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), O = (e) => !!(e.mode && e.mode.value !== "keep"), k = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function A(e) {
		let t = e.field, r = t.value_schema, i = H(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = r?.enum || (r?.type === "boolean" ? [!1, !0] : null);
		return n(b, {
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
					e.draftVersion++, E();
				}
			}),
			n(o ? "select" : "input", {
				id: a,
				"data-role": "value",
				"aria-describedby": e.problem ? a + "-detail" : void 0,
				onInput: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, E();
				},
				onChange: () => {
					e.mode && (e.mode.value = "replace"), e.draftVersion++, E();
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
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), E();
				}
			}, "恢复默认"),
			e.problem ? n("p", {
				class: "field-detail",
				id: a + "-detail",
				role: "status"
			}, e.problem) : null
		] });
	}
	function j(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = W(e.field), a = () => {
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
			n(S, { class: "credential-detail" }, { default: () => e.detail }),
			n(b, { "data-width": "medium" }, { default: () => [n("label", { for: r + "-mode" }, "本次操作"), n("select", {
				id: r + "-mode",
				"data-role": "credential-mode",
				ref: (t) => {
					e.mode = t;
				},
				onChange: () => {
					a(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), w.value = {
						...w.value,
						[r + "-client_id"]: !1,
						[r + "-client_secret"]: !1
					}, E();
				}
			}, [
				["keep", "保持（不写入）"],
				["replace", "替换完整凭据对"],
				["clear", "清除已保存凭据"]
			].map(([e, t]) => n("option", { value: e }, t)))] }),
			...["client_id", "client_secret"].map((t) => n(b, {
				class: "credential-field",
				key: t
			}, { default: () => [n("label", { for: r + "-" + t }, t === "client_id" ? "Client ID" : "Client Secret"), n("div", { class: "credential-input ui-control" }, [n("input", {
				id: r + "-" + t,
				type: w.value[r + "-" + t] ? "text" : "password",
				autocomplete: "off",
				maxlength: 512,
				"data-role": t,
				ref: (n) => {
					t === "client_id" ? e.clientId = n : e.clientSecret = n;
				},
				onInput: () => {
					a(), e.confirm && (e.confirm.checked = !1), E();
				},
				onChange: () => {
					a(), e.confirm && (e.confirm.checked = !1), E();
				}
			}), n("button", {
				type: "button",
				"data-action": "reveal",
				"aria-label": (w.value[r + "-" + t] ? "隐藏" : "显示") + "本次输入 " + (t === "client_id" ? "Client ID" : "Client Secret"),
				"aria-pressed": !!w.value[r + "-" + t],
				onClick: () => {
					w.value = {
						...w.value,
						[r + "-" + t]: !w.value[r + "-" + t]
					};
				}
			}, w.value[r + "-" + t] ? "隐藏" : "显示")])] })),
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
					a(), E();
				}
			}), "确认清除本组已保存凭据"]),
			n(x, {}, { default: () => [n("button", {
				type: "button",
				"data-action": "credential-save",
				disabled: !0,
				ref: (t) => {
					e.save = t;
				},
				onClick: e.onSave
			}, "保存" + i + "凭据"), k(e) ? n("span", {
				class: "dirty-indicator",
				role: "status"
			}, i + "有未保存修改") : null] })
		]);
	}
	let M = t({ setup() {
		return () => {
			v.value;
			let e = o.value.isDark === !0 || o.value.theme === "dark", t = String(o.value.locale || "").startsWith("en"), r = /* @__PURE__ */ new Map();
			for (let e of l.value) {
				let t = JSON.stringify([e.field.module_id, e.field.group || ""]);
				r.has(t) || r.set(t, []), r.get(t).push(e);
			}
			return n(d, {
				preflightStyleDisabled: !0,
				theme: e ? f : null,
				locale: t ? h : g,
				dateLocale: t ? p : m,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				i.id === "management-root" ? n("h1", "设置") : null,
				n("div", { class: "management-toolbar" }, [n(c, {
					type: "info",
					showIcon: !1
				}, { default: () => n(S, { id: "status" }, { default: () => "正在连接宿主管理页面。" }) }), D("refresh", "刷新配置与版本")]),
				n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, a.value.map((e) => {
					let t = e.controls.filter((e) => H(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: _.value !== null && _.value !== e.target
					}, [
						...G(r).map((t) => n(y, {
							title: U(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => t.items.map(A) })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...G(t).map((t) => n(y, {
							title: U(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(A) }))]) : null,
						n(x, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, V(e.target)?.saveLabel || "保存普通设置"),
							n("button", {
								type: "button",
								"data-action": "discard",
								ref: (t) => {
									e.discard = t;
								},
								onClick: () => e.onDiscard?.()
							}, "放弃本模块修改"),
							e.controls.some(O) ? n("span", {
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
				n(S, {
					id: "credential-status",
					tone: "warning",
					hidden: _.value !== null && l.value.length > 0 && !l.value.some((e) => e.field.module_id === _.value)
				}, { default: () => u.value }),
				n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === C.value[e]) ? C.value[e] : t[0].field.name;
					return n(y, {
						title: U(t[0].field.module_id, t[0].field.group || ""),
						key: e,
						hidden: _.value !== null && _.value !== t[0].field.module_id
					}, { default: () => [t.length > 1 ? n("nav", {
						class: "credential-tabs",
						"aria-label": "凭据来源"
					}, t.map((t) => n("button", {
						type: "button",
						"aria-pressed": r === t.field.name,
						onClick: () => {
							C.value = {
								...C.value,
								[e]: t.field.name
							};
						}
					}, W(t.field) + (k(t) ? " · 未保存" : "")))) : null, ...t.map((e) => j(e, r === e.field.name))] });
				})),
				n("details", { class: "advanced-maintenance" }, [n("summary", "高级维护与启动修复"), n(y, { title: "限定恢复" }, { default: () => [
					n("p", "恢复默认仅作用于指定配置字段。缓存清理和持久业务数据删除尚无通用范围合同，当前不提供执行入口。解除挂载始终保留数据。"),
					n("p", "回退仅处理受审普通配置迁移；配置发生后续修改时会拒绝回退。订阅等其它数据继续保留。"),
					D("rollback", "回退受审迁移"),
					n("p", n("label", [n("input", {
						id: "replacement",
						type: "checkbox"
					}), "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）"])),
					D("recover", "重新校验并恢复运行")
				] })])
			]) });
		};
	} }), N = e(M);
	return N.mount(i), {
		owner(e) {
			_.value = e;
		},
		drafts() {
			v.value++;
		},
		maskSecrets() {
			w.value = {};
		},
		fields(e, t) {
			T = t, a.value = e;
		},
		credentials(e, t, n = "") {
			T = t, l.value = e, u.value = n;
		},
		context(e) {
			o.value = e || {};
		},
		dispose() {
			N.unmount();
		}
	};
}
//#endregion
//#region src/management.js
var K = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), q = (e, t) => K(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), J = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, Y = (e = "invalid_config") => {
	throw Error(e);
}, X = (e) => JSON.stringify([e.module_id, e.name]), Z = (e, t) => e.fields.filter((e) => e.module_id === t), te = (e) => [...e].length;
function ne(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && Y(), typeof e == "string") {
			te(e) > 4096 && Y();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!K(e) && !Array.isArray(e) || r.has(e)) && Y(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && Y();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && Y();
}
function Q(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || te(t) >= e.minLength) && (e.maxLength === void 0 || te(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => Q(e.items, t)) : e.type === "object" && K(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && Q(e.properties[t], n));
}
function $(e, t = 0) {
	(!K(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && Y();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && Y(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !K(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && Y();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || Y(), $(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && Y();
	} else if (e.type === "array") n.push("items"), $(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && Y();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && Y(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => Q({
			...e,
			enum: void 0
		}, t))) && Y();
	}
	Object.keys(e).some((e) => !n.includes(e)) && Y();
}
function re(e) {
	ne(e), (!q(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && Y();
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
	for (let r of e.fields) (!q(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => J.test(e)) || typeof r.name != "string" || !r.name || te(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && J.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && Y(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && Y(), n.has(X(r)) && Y(), n.add(X(r)), r.value_schema !== null && (ne(r.value_schema, 16, 2048), $(r.value_schema), r.default !== null && !Q(r.value_schema, r.default) && Y());
	let r = JSON.parse(JSON.stringify(e)), i = (e) => {
		if (e && typeof e == "object") {
			for (let t of Object.values(e)) i(t);
			Object.freeze(e);
		}
		return e;
	};
	return i(r);
}
function ie(e, t) {
	t || Y(), ne(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	q(e, n) || Y();
	for (let r of n) {
		let n = e[r], i = Z(t, r).filter((e) => e.readable);
		(!q(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !q(n.fields, i.map((e) => e.name))) && Y();
		for (let e of i) {
			let t = n.fields[e.name];
			(!q(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !Q(e.value_schema, t.value))) && Y();
		}
	}
	return e;
}
var ae = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function oe(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let e = n.enum || [!1, !0];
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && Y(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && Y(), r = Number(t));
	return Q(n, r) || Y(), r;
}
function se(e, t, n, r) {
	(!r || !K(n) || !Number.isSafeInteger(t) || t < 1) && Y();
	let i = Z(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && Y();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!K(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && Y(), t.mode !== "keep" && ((!n.readable || !n.editable || !ae(n)) && Y("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: oe(n, t.value)
		}));
	}
	return a.length || Y("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function ce(e) {
	ne(e), (!q(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && Y();
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
		(!q(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => J.test(e)) || typeof r.name != "string" || !J.test(r.name) || typeof r.group != "string" || !J.test(r.group) || typeof r.description != "string" || !r.description || t.has(X(r))) && Y(), t.add(X(r)), (!q(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !q(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && Y();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!q(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && Y();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function le(e, t) {
	ne(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	q(e, n) || Y();
	for (let r of n) {
		let n = e[r];
		(!q(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !q(n.fields, Z(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && Y();
	}
	return e;
}
function ue(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && Y(), r === "replace" && (!q(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || te(e) < 1 || te(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && Y(), {
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
var de = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function fe(e, t) {
	(!K(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && Y("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !de.has(e)));
	ne(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && Y("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : K(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function pe(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), onDraftMutation: o = () => {}, onConfigurationSaved: s = () => {}, onCapabilities: c = (e, t = !1) => {} } = {}) {
	a || Y("invalid_container");
	let l = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, u = null, d = null, f = null, p = null, m = !1, h = !1, g = !0, _ = 0, v = null, y = !1, b = null, x = null, S = ee(e, a), C = /* @__PURE__ */ new Map(), w = /* @__PURE__ */ new Map(), T = l("status"), E = /* @__PURE__ */ new Map(), D = !1, O = null, k = null, A = "[[],[]]", j = null;
	function M(e, t = "error") {
		j !== null && n.clearTimeout(j), T.textContent = e, T.dataset.tone = t, t === "success" && (j = n.setTimeout(() => {
			T.textContent = "", j = null;
		}, 3500));
	}
	function N() {
		let e = JSON.stringify([[...w].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
			e,
			t.mode.value,
			t.input?.value
		]), [...E].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
			e,
			t.mode?.value,
			t.clientId?.value,
			t.clientSecret?.value,
			t.confirm?.checked
		])]);
		e !== A && (A = e, o()), S.drafts(), R();
	}
	let P = (e) => !h && m && _ === e, F = (e) => e.readable && e.editable && ae(e), I = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function L(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (h || !m || e.activeElement !== t);
	}
	function R() {
		L(l("refresh"), h || !m || x !== null);
		let e = !h && m && !g && d !== null && Object.keys(d).length > 0 && x === null;
		L(l("rollback"), !e), L(l("recover"), !e || u.fields.some((e) => e.readable && !e.editable)), l("replacement").disabled = I(l("recover"));
		for (let [t, n] of C) {
			let r = k === null || k === t;
			L(n.save, !e || !r || !u || !Z(u, t).some(F)), n.discard && L(n.discard, !m || h || x !== null || !r);
		}
		for (let e of w.values()) {
			let t = !h && m && (k === null || k === e.field.module_id) && F(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of E.values()) {
			let t = !h && m && (k === null || k === e.field.module_id) && p !== null && f !== null && f.fields.some((t) => X(t) === X(e.field) && JSON.stringify(t) === e.signature);
			e.save && L(e.save, !t || g || x !== null || e.mode?.value === "replace" && !D), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !D);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !D || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function z(e, n, r) {
		P(r) || Y("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return P(r) || Y("stale_context"), e === "catalog" ? re(i) : e === "read" ? i : e === "credential-catalog" ? ce(i) : e === "credential-readiness" ? ((!q(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && Y("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!q(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && Y("operation_unavailable"), e === "rollback" && (!q(i, ["rolled_back"]) || i.rolled_back !== !0) && Y("operation_unavailable"), e === "recover" && (!q(i, ["recovered"]) || i.recovered !== !0) && Y("operation_unavailable"), i);
	}
	function B(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function H(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function U(e) {
		let t = C.get(e).save;
		I(t) || $(async (t) => {
			let n = Object.fromEntries(Z(u, e).map((e) => {
				let t = w.get(X(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await z("update", se(e, d[e].revision, n, u), t), s(), await Q(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = w.get(JSON.stringify([e, t])), i = d[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = B(n.field, i.value));
			}
			N(), M((V(e)?.saveLabel || "普通设置") + "已保存。其它组草稿未提交。", "success");
		});
	}
	function G(e) {
		if (!h && m && x === null && d?.[e]) {
			for (let t of Z(u, e)) {
				let n = w.get(X(t)), r = d[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = B(t, r.value));
			}
			for (let t of E.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			S.maskSecrets(), N(), M("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function K() {
		S.maskSecrets();
		for (let e of E.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		N();
	}
	function J(e) {
		I(e.save) || e.mode.value === "replace" && !D || $(async (t) => {
			let n = e.mode.value;
			n === "keep" && Y("select_changes"), n === "clear" && !e.confirm.checked && Y("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await z("credential-update", ue(e.field.module_id, p[e.field.module_id].revision, e.field.name, n, i, f), t), await Q(t), e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), N(), M(n === "clear" ? "已清除" + W(e.field) + "凭据；业务查询需要重新配置该组凭据。" : W(e.field) + "凭据已加密保存；尚未验证所属上游。其它组草稿未提交。", "success");
		});
	}
	async function te() {
		let e = new Set(f.fields.map(X));
		for (let [t, n] of E) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			E.delete(t);
		}
		for (let e of f.fields) {
			let t = X(e), n = JSON.stringify(e), r = E.get(t);
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
				}, r.onSave = () => J(r), E.set(t, r);
			}
			let i = p[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		S.credentials([...E.values()], N), await r(), R();
	}
	async function ne() {
		let e = [...new Set(u.fields.map((e) => e.module_id))], t = new Set(u.fields.map(X));
		for (let e of w.keys()) t.has(e) || w.delete(e);
		for (let t of C.keys()) e.includes(t) || C.delete(t);
		let n = [];
		for (let t of e) {
			let e = C.get(t);
			e || (e = {
				target: t,
				revision: "",
				controls: [],
				save: null,
				discard: null,
				onSave: () => U(t),
				onDiscard: () => G(t)
			}, C.set(t, e)), e.revision = d[t] ? `配置版本：${d[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of Z(u, t)) {
				let i = X(r), a = JSON.stringify(r), o = w.get(i);
				(!o || o.signature !== a) && (o = H(r), w.set(i, o));
				let s = r.readable ? d[t].fields[r.name] : null, c = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				s ? c.push(s.state === "valid" ? "有效" : "无效存量，原值不显示", s.present ? "已有显式值" : "未设置", s.source === "sqlite" ? "Core 配置" : "默认值") : c.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && c.push("模块语义校验器不可用，禁止修改与清除"), ae(r) || c.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), o.detail = c.join(" · "), o.problem = r.readable ? r.editable ? ae(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前不可修改，请核对模块状态。" : "无存量读取与修改授权", ae(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? B(r, s.value) : "",
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
		S.fields([...C.values()], N), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		R();
	}
	async function Q(e) {
		g = !0, R();
		let t = await z("catalog", {}, e), n = ie(await z("read", {}, e), t);
		P(e) || Y("stale_context"), u = t, d = n;
		let i = "";
		try {
			let t = await z("credential-catalog", {}, e), n = le(await z("credential-status", {}, e), t);
			P(e) || Y("stale_context"), f = t, p = n, D = !1;
			try {
				D = (await z("credential-readiness", {}, e)).ready, i = D ? "" : "加密未就绪，暂不能填写凭据。请管理员配置密钥后刷新。";
			} catch {
				P(e) || Y("stale_context"), i = "未能确认加密状态，暂不能填写凭据。请刷新核对。";
			}
			D || K();
		} catch (t) {
			if (!P(e)) throw t;
			t?.message === "admin_authorization_denied" && K(), f = null, p = null, i = t?.message === "admin_authorization_denied" ? "来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。" : "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		g = !1, await ne(), f ? (await te(), S.credentials([...E.values()], N, i), await r(), R()) : S.credentials([...E.values()], N, i), P(e) || Y("stale_context"), c([...new Set([...u.fields, ...f?.fields || []].map((e) => e.module_id))], f !== null);
	}
	async function $(e) {
		if (x || h || !m) return;
		let t = { generation: _ };
		x = t, R(), M("管理操作正在执行。", "neutral");
		try {
			await e(t.generation);
		} catch (e) {
			if (!P(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (g = !0, M("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? M("请选择需要修改的字段。") : e?.message === "confirm_clear" ? M("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "secret_encryption_unavailable" ? M("未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。") : e?.message === "invalid_config" ? M("配置或输入未通过校验，请核对字段要求后刷新重试。") : e?.message === "admin_authorization_denied" ? (K(), f = null, p = null, g = !0, M("管理授权不可用，请重新打开宿主管理页面后手动刷新。")) : (g = !0, M("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			x === t && (x = null, R());
		}
	}
	let oe = () => Object.fromEntries(Object.entries(d).map(([e, t]) => [e, t.revision]));
	l("refresh").addEventListener("click", () => !I(l("refresh")) && $(async (e) => {
		await Q(e), M("设置已更新。", "success");
	})), l("rollback").addEventListener("click", () => !I(l("rollback")) && $(async (e) => {
		await z("rollback", { expected_revisions: oe() }, e), await Q(e), M("受审迁移已限定回退，其他数据保留。", "success");
	})), l("recover").addEventListener("click", () => !I(l("recover")) && $(async (e) => {
		await z("recover", {
			expected_revisions: oe(),
			complete_from_current: l("replacement").checked
		}, e), await Q(e), M("配置已重新校验，业务运行已恢复。", "success");
	}));
	function de(t) {
		if (h) return;
		S.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = fe(t, i);
		} catch {
			c(null), K(), y = !0, _++, m = !1, g = !0, x = null, v = null, u = null, d = null, f = null, p = null, S.fields([], R), S.credentials([], R), C.clear(), w.clear(), E.clear(), A = "[[],[]]", R(), M("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		y = !0, !(m && n === v) && (c(null), K(), _++, v = n, m = !0, g = !0, x = null, u = null, d = null, f = null, p = null, S.fields([], R), S.credentials([], R), C.clear(), w.clear(), E.clear(), A = "[[],[]]", R(), $(async (e) => {
			await Q(e), M("设置已更新。", "success");
		}));
	}
	let pe = () => [...w.values()].some((e) => e.mode?.value !== "keep") || [...E.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function me() {
		let e = [...new Set([...w.values()].filter((e) => (k === null || e.field.module_id === k) && ["replace", "clear"].includes(e.mode?.value)).map((e) => e.field.module_id))], t = [...new Set([...E.values()].filter((e) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map((e) => e.field.module_id))];
		return {
			ordinaryOwners: e,
			credentialOwners: t,
			requiresConfirmation: e.length > 0 || t.length > 0
		};
	}
	function he() {
		let t = e.activeElement;
		O = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, K(), _++, x = null, g = !0, R();
	}
	function ge() {
		!h && m && $(async (e) => {
			if (await Q(e), M("设置已更新。", "success"), O) {
				let e = l(O.id);
				if (e?.focus({ preventScroll: !0 }), e && O.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(O.start, O.end);
				} catch {}
				O = null;
			}
		});
	}
	function _e() {
		h || (j !== null && n.clearTimeout(j), c(null), K(), h = !0, _++, m = !1, x = null, u = null, d = null, f = null, p = null, b?.(), R());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				M("请从宿主插件管理页面打开此页。");
				return;
			}
			b = t.onContext(de), Promise.resolve(t.ready()).then((e) => {
				y || de(e);
			}).catch(() => {
				!y && !h && (m = !1, M("宿主管理会话不可用。"), R());
			}), n?.addEventListener("pagehide", _e, { once: !0 });
		},
		close: _e,
		dirty: pe,
		navigationImpact: me,
		suspend: he,
		resume: ge,
		selectOwner(e) {
			k = e, S.owner(e), R();
		},
		dispose() {
			_e(), n?.removeEventListener("pagehide", _e), S.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && pe(document, window.AstrBotPluginPage, window).start();
//#endregion
//#region src/confirmation.ts
function me(e) {
	let t = s(null), i = 0, a = !1, o = null;
	function c(n, s, c = !1) {
		if (t.value !== n) return;
		let l = !!o?.contains(e.activeElement);
		t.value = null, n.resolve(s && n.isCurrent()), c && l && r().then(() => r()).then(() => {
			!a && i === n.id && !t.value && (n.canRestoreFocus ?? n.isCurrent)() && e.activeElement === e.body && n.trigger?.isConnected && n.trigger.focus({ preventScroll: !0 });
		});
	}
	function d(e = !1) {
		t.value && c(t.value, !1, e);
	}
	return {
		ask(n) {
			return d(), a || !n.isCurrent() ? Promise.resolve(!1) : new Promise((a) => {
				let s = {
					...n,
					id: ++i,
					trigger: n.trigger ?? e.activeElement,
					resolve: a
				};
				t.value = s, r().then(() => {
					t.value === s && s.isCurrent() && o?.querySelector("[data-confirm-cancel]")?.focus({ preventScroll: !0 });
				});
			});
		},
		cancel: d,
		invalidate() {
			t.value && !t.value.isCurrent() && d();
		},
		dispose() {
			d(), a = !0;
		},
		render() {
			let e = t.value;
			return e ? n("section", {
				key: e.id,
				role: "group",
				"aria-label": e.title,
				"data-unload-confirm": e.kind === "unload" ? e.owner : void 0,
				"data-confirmation": e.kind,
				ref: (e) => {
					o = e;
				},
				onKeydown: (t) => {
					t.key === "Escape" && (t.preventDefault(), c(e, !1, !0));
				}
			}, [n(u, { title: e.title }, { default: () => [
				n("p", e.message),
				n(l, {
					"data-action": "confirm-" + e.kind,
					onClick: () => c(e, !0)
				}, () => e.confirmLabel),
				n(l, {
					"data-action": "cancel-" + e.kind,
					"data-confirm-cancel": "",
					onClick: () => c(e, !1, !0)
				}, () => "取消")
			] })]) : null;
		}
	};
}
//#endregion
//#region src/module-management.ts
function he(r, i, a, o, p, m = () => !0, _, v = () => "", y) {
	let b = s([]), x = s("尚未读取模块管理状态。"), S = s(!1), C = s(!0), w = s(/* @__PURE__ */ new Set()), T = _ ?? me(r.ownerDocument), E = s(null), D = s({}), O = s(/* @__PURE__ */ new Map()), k = s({}), A = s(null), j = !1, M = !1, N = 0, F = "", I = 0, L = 0, R, z = 0, B = !0, V = !1, H = !1, U = null, W = (e) => typeof e == "object" && !!e && !Array.isArray(e), G = (e) => !j && M && e === N;
	function ee(e = !0) {
		z++, B = !0, V = !1, k.value = {}, e && H && K();
	}
	function K(e = !1) {
		if (U) return U;
		if (!y || !M || j || !B || V || !e && (!H || S.value)) return Promise.resolve();
		let t = N, n = z;
		return V = !0, U = (async () => {
			try {
				let e = y.catalog(await i.apiPost("admin/catalog", {}));
				if (!G(t) || n !== z) return;
				w.value = new Set(e.fields.map((e) => e.module_id));
				let r = y.snapshot(await i.apiPost("admin/read", {}), e);
				if (!G(t) || n !== z) return;
				let a = {};
				for (let t of b.value) {
					let n = e.fields.filter((e) => e.module_id === t.module_id), i = r[t.module_id];
					a[t.module_id] = n.length && i && n.every((e) => e.readable && e.editable && i.fields[e.name]?.state === "valid") ? "普通设置已校验" : i ? "普通设置待处理" : "普通设置未核对";
				}
				k.value = a, B = !1;
			} catch {
				G(t) && n === z && (k.value = {});
			}
		})().finally(() => {
			U = null, H && B && !V && K();
		}), U;
	}
	function q(e) {
		if (!W(e) || Object.keys(e).sort().join(",") !== "modules,registry_revision" || !Number.isSafeInteger(e.registry_revision) || !Array.isArray(e.modules) || e.modules.length > 128) throw Error("invalid_modules");
		let t = /* @__PURE__ */ new Set();
		for (let n of e.modules) {
			if (!W(n) || Object.keys(n).sort().join(",") !== "enabled,epoch,health,lifecycle,module_id,reason_code,registry_revision" || typeof n.module_id != "string" || !/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(n.module_id) || t.has(n.module_id) || typeof n.enabled != "boolean" || !Number.isSafeInteger(n.epoch) || !Number.isSafeInteger(n.registry_revision) || typeof n.lifecycle != "string" || typeof n.health != "string" || n.reason_code !== null && typeof n.reason_code != "string") throw Error("invalid_modules");
			t.add(n.module_id);
		}
		return e;
	}
	async function J() {
		if (!M || j || S.value) return;
		T.cancel(), E.value = null;
		let e = N;
		S.value = !0, C.value = !0, ee(!1);
		try {
			let t = q(await i.apiPost("admin/modules", {}));
			if (!G(e)) return;
			if (b.value = t.modules, I = t.registry_revision, C.value = !1, x.value = "状态已更新。", y) {
				if (await K(!0), !G(e)) return;
				O.value = y.labels();
			}
		} catch {
			G(e) && (C.value = !0, x.value = "模块管理权限或状态无法确认，请重新打开页面后刷新。");
		} finally {
			G(e) && (S.value = !1, H && B && !V && K());
		}
	}
	async function Y(e, t) {
		if (!G(N) || S.value || C.value || E.value || !b.value.includes(e)) return;
		let n = N, r = I, a = v(), o = ++L;
		E.value = o;
		let s = () => G(n) && !S.value && !C.value && I === r && v() === a && b.value.includes(e), c = () => E.value === o && s();
		try {
			if (!await m({
				owner: e.module_id,
				action: t,
				revision: r,
				isCurrent: c,
				canRestoreFocus: s
			}) || !c() || t === "unload" && !await T.ask({
				kind: "unload",
				owner: e.module_id,
				title: `确认解除挂载 ${e.module_id}`,
				message: `配置、凭据、缓存及业务数据全部保留。注册版本 ${r}；确认后需从宿主插件详情正式重开页面。`,
				confirmLabel: "确认解除挂载（保留数据）",
				isCurrent: c,
				canRestoreFocus: s
			}) || !c()) return;
		} finally {
			E.value === o && (E.value = null);
		}
		if (G(n) && !S.value && !C.value && I === r && v() === a && b.value.includes(e)) {
			S.value = !0, C.value = !0;
			try {
				let a = {
					module_id: e.module_id,
					expected_registry_revision: r,
					...t === "unload" ? {} : { enabled: t === "enable" }
				}, o = await i.apiPost(t === "unload" ? "admin/module-unload" : "admin/module-enabled", a);
				if (!G(n)) return;
				if (!W(o) || o.module_id !== e.module_id || !Number.isSafeInteger(o.registry_revision) || t === "unload" && (o.state !== "unloaded" || o.data_retained !== !0 || o.reopen_required !== !0)) throw Error("operation_unavailable");
				if (S.value = !1, await J(), !G(n)) return;
				p(), G(n) && (x.value = t === "enable" ? "已恢复模块。请从宿主插件详情重新打开页面，以取得新的模块资源授权。" : t === "unload" ? "模块已解除挂载，持久数据保留。恢复后请从宿主插件详情正式重开页面。" : "模块已禁用，设置与持久数据保留。");
			} catch (e) {
				G(n) && (x.value = e?.message === "revision_conflict" ? "模块状态已变化，请刷新后重新核对。" : "未确认清理完成；可能仍在排空或清理。请刷新核对，并重试解除挂载。不会创建替代实例。", C.value = !0);
			} finally {
				G(n) && (S.value = !1);
			}
		}
	}
	let X = e(t({ setup() {
		return () => n(d, {
			preflightStyleDisabled: !0,
			theme: D.value.isDark === !0 || D.value.theme === "dark" ? f : null,
			locale: D.value.locale === "en-US" ? h : g,
			styleMountTarget: r
		}, { default: () => n(u, {
			title: "模块",
			class: "module-management"
		}, { default: () => [
			n(c, {
				type: C.value ? "warning" : "info",
				showIcon: !1
			}, { default: () => x.value }),
			n(l, {
				disabled: S.value || !M,
				onClick: J
			}, () => S.value ? "正在处理…" : "刷新模块管理状态"),
			!b.value.length && !C.value ? n("p", "尚无已注册模块。全局设置仍可使用。") : null,
			...b.value.filter((e) => A.value === null || e.module_id === A.value).map((e) => n("section", {
				class: "module-management-row",
				key: e.module_id,
				"data-module-owner": e.module_id
			}, [
				n("h3", O.value.get(e.module_id) || e.module_id),
				n("p", { class: "module-status" }, `${e.enabled ? "已启用" : "已禁用"} · ${{
					active: "运行中",
					stopped: "已停止",
					failed: "运行失败",
					unloaded: "已解除挂载",
					discovered: "待启动",
					starting: "启动中",
					stopping: "停止中"
				}[e.lifecycle] || e.lifecycle}`),
				y ? n("p", { class: "config-readiness" }, k.value[e.module_id] || "普通设置未核对") : null,
				e.reason_code ? n("p", { role: "status" }, "需要处理：" + e.reason_code) : null,
				y && (w.value.has(e.module_id) || y.hasSettings?.(e.module_id)) ? n(l, { onClick: () => y.onSettings(e.module_id) }, () => "进入设置") : null,
				...[
					"enable",
					"disable",
					"unload"
				].map((t) => n(l, {
					disabled: S.value || E.value !== null || C.value || !M || t === "enable" && e.enabled || t === "disable" && !e.enabled,
					onClick: () => Y(e, t)
				}, () => ({
					enable: "启用 / 恢复",
					disable: "禁用",
					unload: "解除挂载（保留数据）"
				})[t]))
			])),
			_ ? null : T.render(),
			n("details", { class: "technical-details" }, [
				n("summary", "高级详情与操作说明"),
				n("p", "禁用仍可配置；解除挂载退出活动入口并保留配置、凭据、缓存及业务数据。恢复后须正式重开。"),
				...b.value.map((e) => n("p", `${e.module_id} · ${e.lifecycle} · ${e.health} · 注册版本 ${I}`)),
				n("p", { "data-reopen-guidance": "" }, "请在宿主左侧点击“插件”，找到 astrbot_plugin_yomihime_game_link，在插件详情中重新打开 monitor 页面，以取得当前资源授权。")
			])
		] }) });
	} }));
	X.mount(r);
	function Z(e) {
		if (j) return;
		D.value = W(e) ? e : {};
		let t = "";
		try {
			if (!W(e) || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== o) throw Error();
			t = P(e);
		} catch {
			T.cancel(), E.value = null, M = !1, N++, b.value = [], S.value = !1, C.value = !0, x.value = "管理上下文无效，请正式重开页面。";
			return;
		}
		M && t === F || (T.cancel(), E.value = null, N++, F = t, M = !0, b.value = [], S.value = !1, J());
	}
	return {
		start() {
			R = i.onContext(Z), Promise.resolve(i.ready()).then(Z).catch(() => {
				x.value = "宿主会话不可用，请正式重开页面。";
			});
		},
		refresh: J,
		invalidateConfiguration: ee,
		setHomeVisible(e) {
			H = e, e && K();
		},
		selectOwner(e) {
			A.value = e;
		},
		presentationLabels(e) {
			O.value = e;
		},
		cancelConfirmation() {
			T.cancel(), E.value = null;
		},
		dispose() {
			T.cancel(), E.value = null, _ || T.dispose(), j = !0, N++, M = !1, R?.(), X.unmount();
		}
	};
}
//#endregion
//#region src/main.ts
var ge = t({ setup() {
	let e = o(0), t = o(null), r = o(null), s = o(null), c = o(null), u = null, b = null, x = null, C = !1, w = null, T = document.documentElement.dataset.pageName || "shell", E = o(null), D = o(!1), k = me(document), A = window.location.hash, j = "", M = 0, N = "", F = !1, I = 0, L = 0, B = !1, V, H = () => JSON.stringify([
		M,
		N,
		F,
		I,
		u?.state.home,
		u?.state.settings,
		u?.state.settingsOwner,
		u?.state.selected,
		u?.state.catalog?.catalog_revision,
		u?.state.busy,
		u?.state.stale,
		window.location.hash
	]), U = async (e) => {
		let t = ++L, n = H();
		k.cancel();
		let r = () => !B && F && t === L && n === H(), i = u?.state.settings ? b?.navigationImpact() : null;
		if (i?.requiresConfirmation) {
			let e = O(u?.state.catalog?.modules || [], u?.state.locale || "zh-CN"), t = (t) => t.map((t) => t === "game_link/core" ? "全局设置" : e.get(t) || t).join("、"), n = [i.ordinaryOwners.length ? `${t(i.ordinaryOwners)}的普通设置草稿将保留。` : "", i.credentialOwners.length ? `${t(i.credentialOwners)}的凭据输入及操作草稿将清空。` : ""].join("");
			if (!await k.ask({
				kind: "navigation",
				title: "离开设置？",
				message: n,
				confirmLabel: "确认离开",
				isCurrent: r
			})) return;
		}
		r() && (e(), A = window.location.hash);
	}, W = (e) => {
		let t = window.location.hash;
		if (t === j) {
			j = "", A = t;
			return;
		}
		if (t !== A) {
			if (C && b?.navigationImpact().requiresConfirmation) {
				e.stopImmediatePropagation(), window.history.replaceState(null, "", A || "#/"), U(() => {
					j = t, window.location.hash = t;
				});
				return;
			}
			k.cancel(), A = t;
		}
	}, G = () => {
		I++, k.cancel(), x?.cancelConfirmation();
	};
	function ee() {
		k.invalidate(), e.value++;
		let t = u?.state.settings || !1;
		u?.state.catalog && !x && c.value && (x = he(c.value, window.AstrBotPluginPage, window, T, () => {
			u?.refresh(), b?.resume();
		}, (e) => {
			let t = H();
			return !b?.dirty() || k.ask({
				kind: "module-draft",
				owner: e.owner,
				title: "确认模块操作 " + e.owner,
				message: `存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。注册版本 ${e.revision}。`,
				confirmLabel: "保留草稿并继续核对",
				isCurrent: () => t === H() && e.isCurrent(),
				canRestoreFocus: () => t === H() && e.canRestoreFocus()
			});
		}, k, H, {
			hasSettings: (e) => E.value?.includes(e) || !1,
			onSettings: (e) => void U(() => u?.selectSettings(e)),
			labels: () => O(u?.state.catalog?.modules || [], u?.state.locale || "zh-CN"),
			catalog: re,
			snapshot: ie
		}), x.start()), x?.presentationLabels(O(u?.state.catalog?.modules || [], u?.state.locale || "zh-CN")), x?.setHomeVisible(!!(u?.state.home || u?.state.moduleSection === "home")), x?.selectOwner(u?.state.moduleOwner || null), u?.state.catalog && (t || u.state.moduleOwner) && !b && s.value && c.value ? (b = pe(document, window.AstrBotPluginPage, window, {
			expectedPage: T,
			container: s.value,
			onDraftMutation: G,
			onConfigurationSaved: () => x?.invalidateConfiguration(),
			onCapabilities: (e, t = !1) => {
				E.value = e, D.value = t;
			}
		}), b.start()) : t && !C ? b?.resume() : t && C && w !== u?.state.settingsOwner ? (b?.suspend(), b?.resume()) : !t && C && b?.suspend(), w = u?.state.settingsOwner || null, t && b?.selectOwner(u?.state.settingsOwner || "game_link/core"), C = t;
	}
	return a(() => {
		V = window.AstrBotPluginPage?.onContext((e) => {
			let t = "";
			try {
				if (e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== T) throw Error();
				t = P(e);
			} catch {
				F = !1, M++, k.cancel(), x?.cancelConfirmation();
				return;
			}
			t !== N && (M++, N = t, k.cancel(), x?.cancelConfirmation()), F = !0;
		}), window.addEventListener("hashchange", W), u = R({
			bridge: window.AstrBotPluginPage,
			container: t.value,
			window,
			loadPage: _,
			expectedPage: document.documentElement.dataset.pageName || "shell",
			changed: ee
		}), u.start();
	}), i(() => {
		B = !0, M++, k.dispose(), V?.(), window.removeEventListener("hashchange", W), b?.dispose(), x?.dispose(), u?.dispose();
	}), () => {
		e.value;
		let i = u?.state, a = u?.selected(), o = i?.catalog?.modules || [], _ = O(o, i?.locale || "zh-CN"), C = (e) => _.get(e) || e, w = i?.busy || !1, T = i?.home ?? !0, A = o.find((e) => e.module_id === i?.moduleOwner), j = !!(A && i?.moduleSection === "home"), M = !(!A || i?.moduleSection !== "queries" && !a), N = (e) => (t) => {
			t.preventDefault(), U(e);
		}, P = (e, t, r, i) => n("a", {
			href: t,
			"aria-current": r ? "page" : void 0,
			onClick: N(i)
		}, e), F = (e) => n("div", { class: "shell-field" }, [n("label", { for: e }, "模块"), n("select", {
			id: e,
			value: A?.module_id || "",
			disabled: !o.length,
			onChange: (e) => {
				let t = e.target.value;
				t && U(() => u?.selectModule(t));
			}
		}, [n("option", { value: "" }, "选择模块"), ...o.map((e) => n("option", { value: e.module_id }, C(e.module_id)))])]);
		return n(d, {
			preflightStyleDisabled: !0,
			theme: i?.theme === "dark" ? f : null,
			locale: i?.locale.startsWith("en") ? h : g,
			dateLocale: i?.locale.startsWith("en") ? p : m,
			inlineThemeDisabled: !0
		}, { default: () => [n("button", {
			class: "skip-button",
			onClick: () => z(r.value)
		}, "跳至正文"), n("div", {
			class: "game-shell",
			"data-theme": i?.theme || "light"
		}, [n("aside", {
			class: "shell-sidebar",
			"aria-label": "模块导航"
		}, [
			n("p", { class: "shell-brand" }, "如月怜的游戏连结"),
			n("nav", { "aria-label": "主要导航" }, [P("首页", "#/", T, () => u?.selectHome())]),
			n("nav", { "aria-label": "已挂载模块" }, o.map((e) => P(C(e.module_id), "#/module/" + encodeURIComponent(e.module_id), i?.moduleOwner === e.module_id, () => u?.selectModule(e.module_id)))),
			n("div", { class: "shell-settings-slot" }, [n("button", {
				class: "shell-settings",
				type: "button",
				"aria-current": i?.settings && i.settingsOwner === null ? "page" : void 0,
				onClick: () => void U(() => u?.selectSettings())
			}, "全局设置")])
		]), n("div", { class: "shell-content" }, [n("div", { class: "shell-mobile" }, [
			n("p", { class: "shell-brand" }, "如月怜的游戏连结"),
			n("nav", { "aria-label": "移动导航" }, [P("首页", "#/", T, () => u?.selectHome()), P("全局设置", "#/settings", !!(i?.settings && i.settingsOwner === null), () => u?.selectSettings())]),
			F("module-select-mobile")
		]), n("main", {
			ref: r,
			id: "page-root",
			tabindex: -1,
			"aria-labelledby": "page-title"
		}, [
			n(v, {
				class: "shell-heading",
				titleId: "page-title",
				title: A ? C(A.module_id) : i?.settings && i.settingsOwner === null ? "全局设置" : T ? "首页" : "页面不可用"
			}, { default: () => n(l, {
				onClick: () => {
					u?.state.busy || (u?.refresh(), (T || j) && x?.refresh());
				},
				"aria-disabled": String(w),
				"aria-busy": String(w),
				"aria-label": "刷新模块状态"
			}, () => w ? "正在刷新…" : "刷新模块状态") }),
			A ? n("nav", {
				class: "module-tabs",
				"aria-label": "模块内分页"
			}, [
				P("主页", "#/module/" + encodeURIComponent(A.module_id), j, () => u?.selectModule(A.module_id)),
				E.value?.includes(A.module_id) ? P("配置", "#/settings/module/" + encodeURIComponent(A.module_id), !!i?.settings, () => u?.selectSettings(A.module_id)) : null,
				A.pages.length ? P("辅助查询", "#/module/" + encodeURIComponent(A.module_id) + "/-/queries", M, () => u?.selectModule(A.module_id, "queries")) : null
			]) : null,
			n(S, {
				class: "shell-status",
				hidden: !w && !i?.stale && i?.message === "状态已更新；查询仅在手动提交时执行。",
				tone: i?.stale ? "warning" : "neutral"
			}, { default: () => i?.message || "正在准备页面。" }),
			A && !D.value && !E.value?.includes(A.module_id) ? n(S, {}, { default: () => ["配置能力尚未核对。", n("button", {
				type: "button",
				onClick: () => b?.resume()
			}, "重试读取配置能力")] }) : null,
			i?.cleanupPending ? n(l, { onClick: () => u?.retryCleanup() }, () => "重试页面清理") : null,
			k.render(),
			M ? n("nav", {
				class: "query-links",
				"aria-label": "辅助查询入口"
			}, A.pages.map((e) => P(e.title, `#/module/${encodeURIComponent(A.module_id)}/${e.route_id}`, i?.selected?.route === e.route_id, () => u?.select(A.module_id, e.route_id)))) : null,
			j && A.pages.length ? n(y, { title: "可用功能" }, { default: () => n("div", { class: "module-capabilities" }, A.pages.map((e) => n("span", { key: e.route_id }, e.title))) }) : null,
			M && !a ? n("p", { class: "shell-empty" }, "选择一项辅助查询；仅在手动提交时请求数据。") : null,
			n("div", {
				ref: t,
				id: "module-container",
				hidden: !a
			}),
			n("div", {
				class: "settings-container",
				hidden: !i?.settings || !(!i.settingsOwner || A)
			}, [n("div", {
				ref: s,
				id: "settings-container"
			})]),
			n("details", {
				class: "module-dashboard",
				open: T || j,
				hidden: !T && !j && !i?.settings
			}, [n("summary", T || j ? "模块状态与管理" : "高级模块管理"), n("div", {
				ref: c,
				id: "module-management-container"
			})]),
			!T && !i?.settings && !j && !M && !i?.mounted || i?.settingsOwner && !A ? n("p", { class: "shell-empty" }, i?.cleanupPending ? "页面清理未完成，请重新打开页面。" : a ? "模块页面正在准备。" : "此路由当前没有可用的注册页面。恢复模块后请从宿主插件详情正式重开。") : null
		])])])] });
	};
} }), _e = e(ge);
_e.mount("#app"), window.addEventListener("pagehide", () => _e.unmount(), { once: !0 });
//#endregion
