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
}), C = (e) => typeof e == "object" && !!e && !Array.isArray(e), w = (e, t = 4096) => typeof e == "string" && e.length > 0 && e.length <= t;
function T(e) {
	if (!w(e)) return !1;
	let t = /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/(.+)$/.exec(e);
	return !t || t[1].length > 240 || !/\.(?:js|css|svg|png|jpg|jpeg|webp|woff|woff2)$/.test(t[1]) ? !1 : t[1].split("/").every((e) => /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(e) && !e.endsWith(".") && !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(e));
}
var E = (e) => T(e) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\//.test(e);
function D(e) {
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
function O(e, t, n = !1) {
	let r = e.display;
	if (!r) return e.module_id;
	if (n && r.short_name) return r.short_name;
	let i = t.toLowerCase(), a = Object.entries(r.localized_names);
	return a.find(([e]) => e.toLowerCase() === i)?.[1] ?? a.find(([e]) => e.toLowerCase() === i.split("-")[0])?.[1] ?? r.default_name ?? e.module_id;
}
function k(e, t, n = !1) {
	let r = e.map((e) => O(e, t, n));
	return new Map(e.map((e, t) => [e.module_id, r.filter((e) => e.normalize("NFC").toLowerCase() === r[t].normalize("NFC").toLowerCase()).length > 1 ? r[t] + " · " + e.module_id : r[t]]));
}
function A(e) {
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
		let o = r.pages.map((e) => ((!C(e) || !w(e.route_id, 128) || !/^[a-z][a-z0-9_.-]*$/.test(e.route_id) || !w(e.title, 256) || !Number.isSafeInteger(e.order) || e.access !== "public_web" || !(e.capability_id === null || w(e.capability_id, 128)) || !E(e.entry) || !e.entry.startsWith(i) || !e.entry.endsWith(".js") || !Array.isArray(e.styles) || e.styles.length > 16 || !e.styles.every((e) => E(e) && e.startsWith(i) && e.endsWith(".css"))) && t(), [e.entry, ...e.styles].every((e) => a.some((t) => t.path === e)) || t(), {
			route_id: e.route_id,
			title: e.title,
			order: e.order,
			access: "public_web",
			capability_id: e.capability_id,
			entry: e.entry,
			styles: [...e.styles]
		}));
		return new Set(o.map((e) => e.route_id)).size !== o.length && t(), {
			display: D(r.display),
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
function j(e, t) {
	return `#/module/${encodeURIComponent(e)}/${encodeURIComponent(t)}`;
}
function M(e) {
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
function N(e) {
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
var P = /* @__PURE__ */ new Set([
	"isDark",
	"theme",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function ee(e) {
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
	return JSON.stringify(n(Object.fromEntries(Object.entries(e).filter(([e]) => !P.has(e)))));
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
				scope: N(() => !n.revoked),
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
	}, l = !1, u = !1, d = 0, f = 0, p = "", m = !1, h, g = null, _ = "", v = "", y = null, b = L(t.ownerDocument, r, a), x = /* @__PURE__ */ new Map(), S = /* @__PURE__ */ new Set(), C = "模块资源已失效。请关闭此页，并从宿主插件详情重新打开页面。", w = !1, T = !1, E = "", D = null, O = /* @__PURE__ */ new Set(), k = (e) => new Promise((t, n) => {
		let r = setTimeout(() => {
			O.delete(r), n(Error("timeout"));
		}, a);
		O.add(r), e.then(t, n).finally(() => {
			clearTimeout(r), O.delete(r);
		});
	});
	function P() {
		if (c.settings) return null;
		let e = c.catalog?.modules?.find((e) => e.module_id === c.selected?.owner), t = e?.pages.find((e) => e.route_id === c.selected?.route);
		return e?.state === "loaded" && t ? {
			module: e,
			page: t
		} : null;
	}
	function R(e) {
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
	function z() {
		c.cleanupPending = D !== null || b.cleanupPending, c.cleanupPending && (c.message = "页面清理未完成，请重试页面清理。");
	}
	function B(e) {
		S.add(e), E === e && H(), b.invalidate(e), z();
	}
	function V(e) {
		let t = new Map((e.modules || []).filter((e) => e.state === "loaded").map((e) => [e.module_id, I(e, p)]));
		if (T) {
			for (let [e, n] of x) t.get(e) !== n && B(e);
			for (let e of t.keys()) x.has(e) || B(e);
		}
		x.clear();
		for (let [e, n] of t) x.set(e, n);
		T = !0;
	}
	function H() {
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
		e.scopeDone && e.pageDone && e.domDone && (y = null, g = null, D = null, E = ""), z();
	}
	async function U() {
		if (D) {
			i();
			return;
		}
		let n = P();
		if (!n || !u) {
			H(), !c.cleanupPending && (w || S.has(c.selected?.owner || "")) && (c.message = C), i();
			return;
		}
		let { module: r, page: a } = n, o = F(r, a, p);
		if (w || S.has(r.module_id)) {
			H(), c.cleanupPending || (c.message = C), i();
			return;
		}
		if (o === _ && g) {
			g.update(R(r)), i();
			return;
		}
		if (o === v) return;
		if (H(), D) {
			i();
			return;
		}
		v = o, E = r.module_id;
		let s = f, d = N(() => !l && f === s && P() !== null);
		y = d;
		try {
			let n = await k(b.load(r, a));
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
				context: R(r),
				services: l,
				scope: d
			}));
			if (!f || typeof f.update != "function" || typeof f.dispose != "function") throw Error("invalid_page_instance");
			g = f, _ = o, v = "", c.mounted = !0, i();
		} catch {
			!l && !w && !S.has(r.module_id) && (B(r.module_id), !c.cleanupPending && c.selected?.owner === r.module_id && (c.message = C), i());
		}
	}
	function W() {
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
		c.selected = M(e), c.moduleOwner = c.selected?.owner || null;
	}
	async function G() {
		if (l || !u || c.busy) return;
		let t = ++d;
		c.busy = !0, c.stale = c.catalog !== null, c.message = c.stale ? "正在刷新；当前为上次目录，暂不可提交查询。" : "正在读取模块目录。";
		let n = P();
		n && g && g.update(R(n.module)), i();
		try {
			let n = A(await k(e.apiGet("catalog", {})));
			if (l || t !== d) return;
			V(n), c.catalog = n, c.busy = !1, c.stale = !1, c.cleanupPending || (c.message = "状态已更新；查询仅在手动提交时执行。"), W(), await U();
		} catch {
			if (l || t !== d) return;
			c.busy = !1, c.stale = c.catalog !== null, c.message = "状态读取失败。保留上次目录，请确认宿主会话后手动重试。";
			let e = P();
			e && g && g.update(R(e.module)), i();
		}
	}
	function K(e, t = !1) {
		if (l || w || !t && m) return;
		c.theme = typeof e?.isDark == "boolean" ? e.isDark ? "dark" : "light" : e?.theme === "dark" ? "dark" : "light", c.locale = typeof e?.locale == "string" ? e.locale : "zh-CN";
		let n = () => {
			w = !0, d++, u = !1, c.busy = !1, c.stale = c.catalog !== null, H(), b.invalidateAll(), z(), c.cleanupPending || (c.message = C), i();
		}, r;
		try {
			if (r = ee(e), !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== o || e.pageName !== s) throw Error("wrong_page");
		} catch {
			m = !0, n();
			return;
		}
		if (m && u && r === p) {
			let e = P();
			e && g && g.update(R(e.module)), i();
			return;
		}
		if (m) {
			n();
			return;
		}
		m = !0, p = r, u = !0, G();
	}
	let q = () => {
		W(), U(), t.ownerDocument.getElementById("page-root")?.focus({ preventScroll: !0 }), n.scrollTo({
			top: 0,
			left: 0,
			behavior: "instant"
		});
	};
	return {
		state: c,
		selected: P,
		refresh: G,
		selectHome() {
			n.location.hash = "#/", W(), U();
		},
		selectModule(e, t = "home") {
			n.location.hash = "#/module/" + encodeURIComponent(e) + (t === "queries" ? "/-/queries" : ""), W(), U();
		},
		selectSettings(e) {
			n.location.hash = e ? "#/settings/module/" + encodeURIComponent(e) : "#/settings", W(), U();
		},
		select(e, t) {
			n.location.hash = j(e, t), W(), U();
		},
		retryCleanup() {
			c.cleanupPending && (D && H(), b.retryCleanup(), z(), c.cleanupPending || (c.message = w || S.has(c.selected?.owner || "") ? C : "页面清理完成，请手动刷新状态。", l || U()), i());
		},
		start() {
			if (!l) {
				if (n.addEventListener("hashchange", q), !e?.ready || !e?.onContext || !e?.apiGet || !e?.apiPost) {
					c.message = "宿主连接尚未就绪，请重新打开页面。", i();
					return;
				}
				h = e.onContext((e) => K(e, !0)), k(Promise.resolve(e.ready())).then((e) => K(e)).catch(() => {
					!u && !l && (c.message = "连接宿主失败，请重新打开页面。", i());
				});
			}
		},
		dispose() {
			if (!l) {
				l = !0, d++, u = !1, h?.(), n.removeEventListener("hashchange", q);
				for (let e of O) clearTimeout(e);
				O.clear(), H(), b.invalidateAll(), z();
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
//#region src/font-settings.ts
function B(e) {
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
function V(e) {
	return e && /^https:\/\/raw\.githubusercontent\.com\/notofonts\/noto-cjk\/[a-f0-9]{40}\/Sans\/OTF\/SimplifiedChinese\/NotoSansCJKsc-Regular\.otf$/.test(e.download_url) ? e.download_url : null;
}
function H(e) {
	if (!e) return "字体状态暂不可用";
	let t = e.source === "system" ? " · 正在使用系统字体" : e.source === "none" ? " · 暂无可用图片字体" : "";
	return e.state === "ready" && e.source === "canonical" ? "标准字体已就绪" : e.state === "checking" ? "正在检查字体" + t : e.state === "downloading" ? "正在下载标准字体" + t : e.error ? "标准字体获取失败" + t : (e.source === "system" ? "正在使用系统字体" : "标准字体暂不可用") + (e.source === "none" ? " · 暂无可用图片字体" : "");
}
function U(e, t, n, r) {
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
	}, c = () => n({ ...s }), l = (e) => !a && s.enabled && i === e, u = () => {
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
		u(), s.enabled && ["checking", "downloading"].includes(s.status?.state || "") && (o = t.setTimeout(() => void h(), 2e3));
	}
	async function m(e, t) {
		if (a || !s.enabled || s.busy) return;
		let n = i;
		s.busy = !0, s.message = "", s.progress = t, u(), c();
		try {
			let t = B(await e());
			if (!l(n)) return;
			s.status = t, s.progress = "";
		} catch (e) {
			l(n) && f(e);
		} finally {
			l(n) ? (s.busy = !1, c(), p()) : i === n && (s.busy = !1, c());
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
			(s.visible !== e || s.enabled !== n) && (i++, u(), s.visible = e, s.enabled = n, s.busy = !1, s.progress = "", s.message = "", s.status = null, c(), n && h());
		},
		invalidate() {
			i++, u(), s.enabled = !1, s.busy = !1, s.status = null, s.progress = "", s.message = "", c();
		},
		refresh: h,
		dispose() {
			a = !0, i++, u();
		}
	};
}
//#endregion
//#region src/ui/presentation.ts
var W = new Map(Object.values(/* @__PURE__ */ Object.assign({ "../../../../modules/ff14/pages/src/presentation.json": {
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
W.set("game_link/core", {
	owner: "game_link/core",
	saveLabel: "保存全局设置",
	groups: { "": { title: "查询默认值" } },
	fields: { default_region: {
		label: "默认区域",
		width: "short"
	} }
});
var G = (e) => W.get(e), K = (e) => W.get(e.module_id)?.fields?.[e.name] || {}, q = (e, t) => t === "reply_style" ? "回复样式" : W.get(e)?.groups?.[t]?.title || t || "普通设置", te = (e) => W.get(e.module_id)?.credentials?.[e.name]?.label || e.description.split("；")[0] || e.name, J = (e) => e.group === "reply_style" && (e.module_id === "game_link/core" ? e.name === "image_theme" : e.name === "reply_theme"), ne = (e) => J(e) && e.module_id !== "game_link/core" && e.default === null && e.choices?.[0]?.value === null, re = (e) => e.choices?.map((e) => e.value) ?? e.value_schema?.enum ?? (e.value_schema?.type === "boolean" ? [!1, !0] : null);
//#endregion
//#region src/management-view.ts
function ie(e) {
	let t = /* @__PURE__ */ new Map();
	for (let n of e) {
		let e = n.field.group || "";
		t.has(e) || t.set(e, []), t.get(e).push(n);
	}
	return [...t].map(([e, t]) => ({
		group: e,
		items: t.sort((e, t) => (K(e.field).order || 0) - (K(t.field).order || 0))
	}));
}
function ae(r, i = r.getElementById("management-root"), a = "settings") {
	let o = s(null), c = s([]), l = s({}), u = s([]), _ = s(""), v = s(null), C = s(0), w = s({}), T = s({}), E = () => {}, D = () => {
		C.value++, E();
	}, O = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), k = (e) => !!(e.mode && e.mode.value !== "keep"), A = (e) => !!(e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function j(e) {
		let t = e.field, r = t.value_schema, i = K(t), a = `config-${encodeURIComponent(t.module_id)}-${encodeURIComponent(t.name)}`, o = re(t), s = () => {
			e.mode && (e.mode.value = ne(t) && e.input?.value !== "" && o?.[Number(e.input?.value)] === null ? e.inheritBaseline ? "keep" : "clear" : "replace"), e.draftVersion++, D();
		};
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
					e.draftVersion++, D();
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
					e.input.value = o ? String(o.findIndex((e) => e === n)) : n === null ? "" : String(n), D();
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
	function M(e, t) {
		let r = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, i = te(e.field), a = () => {
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
					a(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), T.value = {
						...T.value,
						[r + "-client_id"]: !1,
						[r + "-client_secret"]: !1
					}, D();
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
				type: T.value[r + "-" + t] ? "text" : "password",
				autocomplete: "off",
				maxlength: 512,
				"data-role": t,
				ref: (n) => {
					t === "client_id" ? e.clientId = n : e.clientSecret = n;
				},
				onInput: () => {
					a(), e.confirm && (e.confirm.checked = !1), D();
				},
				onChange: () => {
					a(), e.confirm && (e.confirm.checked = !1), D();
				}
			}), n("button", {
				type: "button",
				"data-action": "reveal",
				"aria-label": (T.value[r + "-" + t] ? "隐藏" : "显示") + "本次输入 " + (t === "client_id" ? "Client ID" : "Client Secret"),
				"aria-pressed": !!T.value[r + "-" + t],
				onClick: () => {
					T.value = {
						...T.value,
						[r + "-" + t]: !T.value[r + "-" + t]
					};
				}
			}, T.value[r + "-" + t] ? "隐藏" : "显示")])] })),
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
					a(), D();
				}
			}), n("span", "确认清除本组已保存凭据")]),
			n(x, {}, { default: () => [n("button", {
				type: "button",
				"data-action": "credential-save",
				disabled: !0,
				ref: (t) => {
					e.save = t;
				},
				onClick: e.onSave
			}, "保存" + i + "凭据"), A(e) ? n("span", {
				class: "dirty-indicator",
				role: "status"
			}, i + "有未保存修改") : null] })
		]);
	}
	function N() {
		let e = o.value;
		if (!e?.visible) return null;
		let t = !e.enabled || e.busy || e.status?.state === "downloading" || e.status?.state === "checking";
		return n("section", {
			"data-font-settings": "",
			"aria-label": "图片字体",
			"aria-busy": String(e.busy)
		}, [
			n(S, { id: "font-status" }, { default: () => e.progress || e.message || H(e.status) }),
			n("p", {
				class: "field-hint",
				id: "font-help"
			}, "启动时自动获取标准字体；下载失败时优先使用系统字体；缺字时回复文字。上传仅接受指定版本并校验文件。"),
			n(x, {}, { default: () => [
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
			e.status ? n("p", { class: "field-hint" }, ["指定文件：" + e.status.filename + " ", V(e.status) ? n("a", {
				href: V(e.status),
				target: "_blank",
				rel: "noopener noreferrer"
			}, "下载指定字体") : null]) : null
		]);
	}
	let P = t({ setup() {
		return () => {
			C.value;
			let e = l.value.isDark === !0 || l.value.theme === "dark", t = String(l.value.locale || "").startsWith("en"), r = /* @__PURE__ */ new Map();
			for (let e of u.value) {
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
				i.id === "management-root" ? n("h1", a === "maintenance" ? "配置维护" : "设置") : null,
				n("div", { class: "management-toolbar" }, [n(S, { id: "status" }), O("refresh", "刷新配置与版本")]),
				a === "settings" ? n("section", {
					id: "fields",
					"aria-label": "普通配置"
				}, c.value.map((e) => {
					let t = e.controls.filter((e) => K(e.field).advanced || e.field.editable === !1), r = e.controls.filter((e) => !t.includes(e));
					return n("section", {
						class: "card",
						key: e.target,
						"data-owner": e.target,
						hidden: v.value !== null && v.value !== e.target
					}, [
						...ie(r).map((t) => n(y, {
							title: q(e.target, t.group),
							fieldset: !0,
							class: "config-group",
							"data-group": t.group || "general",
							key: t.group
						}, { default: () => [...t.items.map(j), e.target === "game_link/core" && t.group === "reply_style" ? N() : null] })),
						t.length ? n("details", { class: "advanced-config" }, [n("summary", "只读与高级设置"), ...ie(t).map((t) => n(y, {
							title: q(e.target, t.group),
							key: t.group
						}, { default: () => t.items.map(j) }))]) : null,
						n(x, { class: "ordinary-actions" }, { default: () => [
							n("button", {
								type: "button",
								"data-action": "save",
								disabled: !0,
								ref: (t) => {
									e.save = t;
								},
								onClick: e.onSave
							}, G(e.target)?.saveLabel || "保存普通设置"),
							n("button", {
								type: "button",
								"data-action": "discard",
								ref: (t) => {
									e.discard = t;
								},
								onClick: () => e.onDiscard?.()
							}, "放弃本模块修改"),
							e.controls.some(k) ? n("span", {
								class: "dirty-indicator",
								role: "status"
							}, "普通设置有未保存修改") : null
						] })
					]);
				})) : null,
				a === "settings" ? n(S, {
					id: "credential-status",
					tone: "warning",
					hidden: v.value !== null && u.value.length > 0 && !u.value.some((e) => e.field.module_id === v.value)
				}, { default: () => _.value }) : null,
				a === "settings" ? n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, [...r].map(([e, t]) => {
					let r = t.some((t) => t.field.name === w.value[e]) ? w.value[e] : t[0].field.name;
					return n(y, {
						title: q(t[0].field.module_id, t[0].field.group || ""),
						key: e,
						hidden: v.value !== null && v.value !== t[0].field.module_id
					}, { default: () => [t.length > 1 ? n("nav", {
						class: "credential-tabs",
						"aria-label": "凭据来源"
					}, t.map((t) => n("button", {
						type: "button",
						"aria-pressed": r === t.field.name,
						onClick: () => {
							w.value = {
								...w.value,
								[e]: t.field.name
							};
						}
					}, te(t.field) + (A(t) ? " · 未保存" : "")))) : null, ...t.map((e) => M(e, r === e.field.name))] });
				})) : null,
				a === "maintenance" ? n(y, {
					title: "限定恢复",
					class: "maintenance-panel"
				}, { default: () => [
					n("p", "仅处理受审普通配置迁移；后续修改会阻止回退。订阅及其它持久数据保留。"),
					O("rollback", "回退受审迁移"),
					n("p", n("label", { class: "ui-check-label" }, [n("input", {
						id: "replacement",
						type: "checkbox",
						onChange: D
					}), n("span", "以全部迁移字段已显式保存的有效新值完成恢复（缺少旧准备材料时）")])),
					O("recover", "重新校验并恢复运行")
				] }) : null
			]) });
		};
	} }), ee = e(P);
	return ee.mount(i), {
		font(e) {
			o.value = { ...e };
		},
		owner(e) {
			v.value = e;
		},
		drafts() {
			C.value++;
		},
		maskSecrets() {
			T.value = {};
		},
		fields(e, t) {
			E = t, c.value = e;
		},
		credentials(e, t, n = "") {
			E = t, u.value = e, _.value = n;
		},
		context(e) {
			l.value = e || {};
		},
		dispose() {
			ee.unmount();
		}
	};
}
//#endregion
//#region src/ui/notice.ts
function oe(e, t) {
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
var Y = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), X = (e, t) => Y(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), se = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, Z = (e = "invalid_config") => {
	throw Error(e);
}, ce = (e) => [
	e?.code,
	e?.error_code,
	e?.message
].some((e) => [
	"admin_authorization_denied",
	"stale_context",
	"page_revoked",
	"asset_revoked"
].includes(e)) || [e?.status, e?.response?.status].some((e) => e === 401 || e === 403), Q = (e) => JSON.stringify([e.module_id, e.name]), le = (e, t) => e.fields.filter((e) => e.module_id === t), ue = (e) => [...e].length;
function de(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && Z(), typeof e == "string") {
			ue(e) > 4096 && Z();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!Y(e) && !Array.isArray(e) || r.has(e)) && Z(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && Z();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && Z();
}
function $(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || ue(t) >= e.minLength) && (e.maxLength === void 0 || ue(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => $(e.items, t)) : e.type === "object" && Y(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && $(e.properties[t], n));
}
function fe(e, t = 0) {
	(!Y(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && Z();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && Z(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !Y(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && Z();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || Z(), fe(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && Z();
	} else if (e.type === "array") n.push("items"), fe(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && Z();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && Z(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => $({
			...e,
			enum: void 0
		}, t))) && Z();
	}
	Object.keys(e).some((e) => !n.includes(e)) && Z();
}
function pe(e) {
	de(e), (!X(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && Z();
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
	for (let r of e.fields) if ((!Y(r) || !X(r, Object.hasOwn(r, "choices") ? [...t, "choices"] : t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => se.test(e)) || typeof r.name != "string" || !r.name || ue(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && se.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && Z(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && Z(), n.has(Q(r)) && Z(), n.add(Q(r)), r.value_schema !== null && (de(r.value_schema, 16, 2048), fe(r.value_schema), r.default !== null && !$(r.value_schema, r.default) && Z()), Object.hasOwn(r, "choices")) {
		(!J(r) || r.value_schema?.type !== "string" || !Array.isArray(r.value_schema.enum) || !Array.isArray(r.choices)) && Z();
		let e = r.module_id !== "game_link/core", t = e ? [null, ...r.value_schema.enum] : r.value_schema.enum;
		(e ? r.default !== null : !$(r.value_schema, r.default)) && Z(), (r.choices.length !== t.length || !r.choices.every((e, n) => X(e, ["value", "label"]) && e.value === t[n] && typeof e.label == "string" && e.label.trim().length > 0 && ue(e.label) <= 128 && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(e.label))) && Z();
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
function me(e, t) {
	t || Z(), de(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	X(e, n) || Z();
	for (let r of n) {
		let n = e[r], i = le(t, r).filter((e) => e.readable);
		(!X(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !X(n.fields, i.map((e) => e.name))) && Z();
		for (let e of i) {
			let t = n.fields[e.name];
			(!X(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !(t.value === null && ne(e) && t.present === !1 && t.source === "default") && !$(e.value_schema, t.value))) && Z();
		}
	}
	return e;
}
var he = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function ge(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let n = re(e);
		(!/^\d+$/.test(t) || !Object.hasOwn(n, Number(t))) && Z(), r = n[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && Z(), r = Number(t));
	return !(r === null && ne(e)) && !$(n, r) && Z(), r;
}
function _e(e, t, n, r) {
	(!r || !Y(n) || !Number.isSafeInteger(t) || t < 1) && Z();
	let i = le(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && Z();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		if ((!Y(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && Z(), t.mode === "keep") continue;
		(!n.readable || !n.editable || !he(n)) && Z("admin_authorization_denied");
		let r = t.mode === "clear" ? null : ge(n, t.value);
		a.push(t.mode === "clear" || r === null && ne(n) ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: r
		});
	}
	return a.length || Z("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function ve(e) {
	de(e), (!X(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && Z();
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
		(!X(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => se.test(e)) || typeof r.name != "string" || !se.test(r.name) || typeof r.group != "string" || !se.test(r.group) || typeof r.description != "string" || !r.description || t.has(Q(r))) && Z(), t.add(Q(r)), (!X(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !X(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && Z();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!X(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && Z();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function ye(e, t) {
	de(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	X(e, n) || Z();
	for (let r of n) {
		let n = e[r];
		(!X(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !X(n.fields, le(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && Z();
	}
	return e;
}
function be(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && Z(), r === "replace" && (!X(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || ue(e) < 1 || ue(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && Z(), {
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
var xe = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function Se(e, t) {
	(!Y(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && Z("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !xe.has(e)));
	de(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && Z("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : Y(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function Ce(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root"), scope: o = "settings", initiallySuspended: s = !1, beforeMaintenance: c = async (e) => !1, onDraftMutation: l = () => {}, onConfigurationSaved: u = () => {}, onCapabilities: d = (e, t = !1) => {} } = {}) {
	a || Z("invalid_container");
	let f = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, p = null, m = null, h = null, g = null, _ = !1, v = !1, y = !0, b = 0, x = null, S = !1, C = null, w = null, T = ae(e, a, o), E = /* @__PURE__ */ new Map(), D = /* @__PURE__ */ new Map(), O = f("status"), k = s, A = U(t, n, (e) => T.font(e), () => {
		y = !0, G();
	}), j = /* @__PURE__ */ new Map(), M = !1, N = null, P = null, ee = o === "maintenance" ? "[[],[],false]" : "[[],[]]", F = ee, I = oe(n, (e, t) => {
		O.textContent = e, O.dataset.tone = t;
	}), L = (e, t = "error") => I.show(e, t), R = "当前加密不可用，请管理员检查宿主密钥与加密组件；修改外部环境后需正常重启同一宿主，再重开此页。已有凭据时请保留原密钥。";
	function z() {
		let e = JSON.stringify([
			[...D].filter(([, e]) => e.mode && e.mode.value !== "keep").map(([e, t]) => [
				e,
				t.mode.value,
				t.input?.value
			]),
			[...j].filter(([, e]) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map(([e, t]) => [
				e,
				t.mode?.value,
				t.clientId?.value,
				t.clientSecret?.value,
				t.confirm?.checked
			]),
			...o === "maintenance" ? [!!f("replacement")?.checked] : []
		]);
		e !== F && (F = e, l()), T.drafts(), G();
	}
	let B = (e) => !v && _ && b === e, V = (e) => e.readable && e.editable && he(e), H = (e) => !e || e.disabled || e.getAttribute("aria-disabled") === "true";
	function W(t, n) {
		t && (t.setAttribute("aria-disabled", String(n)), t.disabled = n && (v || !_ || e.activeElement !== t));
	}
	function G() {
		let e = o === "settings" && (P === null || P === "game_link/core") && !!p?.fields.some((e) => e.module_id === "game_link/core" && e.group === "reply_style" && e.readable);
		A.sync(e, !v && _ && !k && !y), W(f("refresh"), v || !_ || w !== null);
		let t = !v && _ && !y && m !== null && Object.keys(m).length > 0 && w === null;
		W(f("rollback"), !t), W(f("recover"), !t || p.fields.some((e) => e.readable && !e.editable)), f("replacement") && (f("replacement").disabled = H(f("recover")));
		for (let [e, n] of E) {
			let r = P === null || P === e;
			W(n.save, !t || !r || !p || !le(p, e).some(V)), n.discard && W(n.discard, !_ || v || w !== null || !r);
		}
		for (let e of D.values()) {
			let t = !v && _ && (P === null || P === e.field.module_id) && V(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of j.values()) {
			let t = !v && _ && (P === null || P === e.field.module_id) && g !== null && h !== null && h.fields.some((t) => Q(t) === Q(e.field) && JSON.stringify(t) === e.signature);
			e.save && W(e.save, !t || y || w !== null || e.mode?.value === "replace" && !M), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !M);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !M || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function K(e, n, r) {
		B(r) || Z("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return B(r) || Z("stale_context"), e === "catalog" ? pe(i) : e === "read" ? i : e === "credential-catalog" ? ve(i) : e === "credential-readiness" ? ((!X(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && Z("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!X(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && Z("operation_unavailable"), e === "rollback" && (!X(i, ["rolled_back"]) || i.rolled_back !== !0) && Z("operation_unavailable"), e === "recover" && (!X(i, ["recovered"]) || i.recovered !== !0) && Z("operation_unavailable"), i);
	}
	function q(e, t) {
		let n = re(e);
		return t === null && !ne(e) ? "" : String(n ? n.findIndex((e) => e === t) : t);
	}
	function te(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function J(e) {
		let t = E.get(e).save;
		H(t) || fe(async (t) => {
			let n = Object.fromEntries(le(p, e).map((e) => {
				let t = D.get(Q(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await K("update", _e(e, m[e].revision, n, p), t), u();
			try {
				await $(t);
			} catch (e) {
				if (!B(t)) throw e;
				y = !0, Z(ce(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = D.get(JSON.stringify([e, t])), i = m[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = i.state === "valid" ? q(n.field, i.value) : "");
			}
			z(), L("已保存。", "success");
		});
	}
	function ie(e) {
		if (!v && _ && w === null && m?.[e]) {
			for (let t of le(p, e)) {
				let n = D.get(Q(t)), r = m[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = r.state === "valid" ? q(t, r.value) : "");
			}
			for (let t of j.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			T.maskSecrets(), z(), L("已放弃本模块修改，含本模块凭据草稿。", "success");
		}
	}
	function Y() {
		T.maskSecrets();
		for (let e of j.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
		z();
	}
	function se(e) {
		H(e.save) || e.mode.value === "replace" && !M || fe(async (t) => {
			let n = e.mode.value;
			n === "keep" && Z("select_changes"), n === "clear" && !e.confirm.checked && Z("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await K("credential-update", be(e.field.module_id, g[e.field.module_id].revision, e.field.name, n, i, h), t);
			let a = !1;
			try {
				a = (await $(t)).credentials;
			} catch (e) {
				if (!B(t)) throw e;
				ce(e) && Z("write_ack_authorization_denied");
			}
			e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), z(), a || (y = !0, Z("write_ack_readback_unverified")), L("已保存。", "success");
		});
	}
	async function ue() {
		let e = new Set(h.fields.map(Q));
		for (let [t, n] of j) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			j.delete(t);
		}
		for (let e of h.fields) {
			let t = Q(e), n = JSON.stringify(e), r = j.get(t);
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
				}, r.onSave = () => se(r), j.set(t, r);
			}
			let i = g[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		T.credentials([...j.values()], z), await r(), G();
	}
	async function de() {
		let e = [...new Set(p.fields.map((e) => e.module_id))], t = new Set(p.fields.map(Q));
		for (let e of D.keys()) t.has(e) || D.delete(e);
		for (let t of E.keys()) e.includes(t) || E.delete(t);
		let n = [];
		for (let t of e) {
			let e = E.get(t);
			e || (e = {
				target: t,
				controls: [],
				save: null,
				discard: null,
				onSave: () => J(t),
				onDiscard: () => ie(t)
			}, E.set(t, e)), e.controls = [];
			for (let r of le(p, t)) {
				let i = Q(r), a = JSON.stringify(r), o = D.get(i);
				(!o || o.signature !== a) && (o = te(r), D.set(i, o));
				let s = r.readable ? m[t].fields[r.name] : null;
				o.inheritBaseline = !!(ne(r) && s?.state === "valid" && s.value === null && s.present === !1 && s.source === "default"), o.problem = r.readable ? r.editable ? he(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前只读，请核对模块状态。" : "无读取或修改权限。", he(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s?.state === "valid" ? q(r, s.value) : "",
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
		T.fields([...E.values()], z), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		G();
	}
	async function $(e) {
		y = !0, G();
		let t = await K("catalog", {}, e), n = me(await K("read", {}, e), t);
		B(e) || Z("stale_context"), p = t, m = n;
		let i = "";
		try {
			let t = await K("credential-catalog", {}, e), n = ye(await K("credential-status", {}, e), t);
			B(e) || Z("stale_context"), h = t, g = n, M = !1;
			try {
				M = (await K("credential-readiness", {}, e)).ready, i = M ? "" : R;
			} catch (t) {
				if (!B(e) || ce(t)) throw t;
				i = "未能确认加密状态，暂不能填写凭据；请核对宿主管理会话与部署后重开此页。";
			}
			M || Y();
		} catch (t) {
			if (!B(e)) throw t;
			if (h = null, g = null, ce(t)) throw Y(), t;
			i = "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		return y = !1, o === "settings" && (await de(), h ? (await ue(), T.credentials([...j.values()], z, i), await r(), G()) : T.credentials([...j.values()], z, i)), B(e) || Z("stale_context"), d([...new Set([...p.fields, ...h?.fields || []].map((e) => e.module_id))], h !== null), { credentials: h !== null };
	}
	async function fe(e) {
		if (w || v || !_) return;
		let t = { generation: b };
		w = t, G();
		try {
			await e(t.generation);
		} catch (e) {
			if (!B(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (y = !0, L("配置已变化，请刷新并重新核对后修改。")) : e?.message === "select_changes" ? L("请选择需要修改的字段。") : e?.message === "confirm_clear" ? L("请勾选清除确认后再提交；保持选项不会写入。") : e?.message === "write_ack_readback_unverified" ? L("已提交，状态暂未核实，请刷新确认。", "warning") : e?.message === "write_ack_authorization_denied" ? (Y(), h = null, g = null, y = !0, L("已提交，但管理授权不可用，状态暂未核实。请重新打开宿主管理页面确认。", "warning")) : e?.message === "secret_encryption_unavailable" ? L("未保存。" + R) : e?.message === "invalid_config" ? L("配置或输入未通过校验，请核对字段要求后刷新重试。") : ce(e) ? (Y(), h = null, g = null, y = !0, L("管理授权不可用或页面已失效，请重新打开宿主管理页面后手动刷新。")) : (y = !0, L("未取得成功确认，请刷新核对配置与版本后重试。"));
		} finally {
			w === t && (w = null, G());
		}
	}
	let ge = () => Object.fromEntries(Object.entries(m).map(([e, t]) => [e, t.revision]));
	f("refresh").addEventListener("click", () => !H(f("refresh")) && fe(async (e) => {
		let t = await $(e);
		L(t.credentials ? "已刷新。" : "普通设置已刷新，凭据状态暂未核实。", t.credentials ? "success" : "warning");
	}));
	for (let e of ["rollback", "recover"]) f(e)?.addEventListener("click", () => !H(f(e)) && fe(async (t) => {
		if (await c({
			action: e,
			isCurrent: () => B(t)
		}) && B(t)) {
			await K(e, {
				expected_revisions: ge(),
				...e === "recover" ? { complete_from_current: f("replacement").checked } : {}
			}, t);
			try {
				await $(t);
			} catch (e) {
				if (!B(t)) throw e;
				y = !0, Z(ce(e) ? "write_ack_authorization_denied" : "write_ack_readback_unverified");
			}
			u(), L(e === "rollback" ? "受审迁移已限定回退，其他数据保留。" : "配置已重新校验，业务运行已恢复。", "success");
		}
	}));
	function xe(t) {
		if (v) return;
		T.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = Se(t, i);
		} catch {
			A.invalidate(), d(null), Y(), S = !0, b++, _ = !1, y = !0, w = null, x = null, p = null, m = null, h = null, g = null, T.fields([], z), T.credentials([], z), E.clear(), D.clear(), j.clear(), F = ee, G(), L("宿主管理上下文无效，请重新打开页面。");
			return;
		}
		S = !0, !(_ && n === x) && (A.invalidate(), I.clear(), d(null), Y(), b++, x = n, _ = !0, y = !0, w = null, p = null, m = null, h = null, g = null, T.fields([], z), T.credentials([], z), E.clear(), D.clear(), j.clear(), F = ee, G(), fe(async (e) => {
			await $(e);
		}));
	}
	let Ce = () => [...D.values()].some((e) => e.mode?.value !== "keep") || [...j.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function we() {
		let e = [...new Set([...D.values()].filter((e) => (P === null || e.field.module_id === P) && ["replace", "clear"].includes(e.mode?.value)).map((e) => e.field.module_id))], t = [...new Set([...j.values()].filter((e) => e.mode && e.mode.value !== "keep" || e.clientId?.value || e.clientSecret?.value || e.confirm?.checked).map((e) => e.field.module_id))];
		return {
			ordinaryOwners: e,
			credentialOwners: t,
			requiresConfirmation: e.length > 0 || t.length > 0
		};
	}
	function Te() {
		k = !0, A.invalidate(), O.dataset.tone === "success" ? I.clear() : I.cancel();
		let t = e.activeElement;
		N = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, Y(), b++, w = null, y = !0, G();
	}
	function Ee() {
		!v && _ && (k = !1, fe(async (e) => {
			if (await $(e), N) {
				let e = f(N.id);
				if (e?.focus({ preventScroll: !0 }), e && N.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(N.start, N.end);
				} catch {}
				N = null;
			}
		}));
	}
	function De() {
		v || (A.dispose(), I.cancel(), d(null), Y(), v = !0, b++, _ = !1, w = null, p = null, m = null, h = null, g = null, C?.(), G());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				L("请从宿主插件管理页面打开此页。");
				return;
			}
			C = t.onContext(xe), Promise.resolve(t.ready()).then((e) => {
				S || xe(e);
			}).catch(() => {
				!S && !v && (_ = !1, L("宿主管理会话不可用。"), G());
			}), n?.addEventListener("pagehide", De, { once: !0 });
		},
		close: De,
		dirty: Ce,
		navigationImpact: we,
		suspend: Te,
		resume: Ee,
		selectOwner(e) {
			P = e, T.owner(e), G();
		},
		dispose() {
			De(), n?.removeEventListener("pagehide", De), T.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && Ce(document, window.AstrBotPluginPage, window).start();
//#endregion
//#region src/confirmation.ts
function we(e) {
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
function Te(r, i, a, o, p, m = () => !0, _, v = () => "", y) {
	let b = s([]), x = s(""), S = s("neutral"), C = s(!1), w = s(!0), T = oe(a, (e, t) => {
		x.value = e, S.value = t;
	}), E = s(/* @__PURE__ */ new Set()), D = s(!0), O = _ ?? we(r.ownerDocument), k = s(null), A = s({}), j = s(/* @__PURE__ */ new Map()), M = s({}), N = s(null), P = !1, F = !1, I = 0, L = "", R = 0, z = 0, B, V = 0, H = !0, U = !1, W = !1, G = null, K = (e) => typeof e == "object" && !!e && !Array.isArray(e), q = (e) => !P && F && e === I;
	function te(e = !0) {
		V++, H = !0, U = !1, M.value = {}, e && W && J();
	}
	function J(e = !1) {
		if (G) return G;
		if (!y || !F || P || !H || U || !e && (!W || C.value)) return Promise.resolve();
		let t = I, n = V;
		return U = !0, G = (async () => {
			try {
				let e = y.catalog(await i.apiPost("admin/catalog", {}));
				if (!q(t) || n !== V) return;
				E.value = new Set(e.fields.map((e) => e.module_id));
				let r = y.snapshot(await i.apiPost("admin/read", {}), e);
				if (!q(t) || n !== V) return;
				let a = {};
				for (let t of b.value) {
					let n = e.fields.filter((e) => e.module_id === t.module_id), i = n.filter((e) => e.readable), o = r[t.module_id];
					a[t.module_id] = n.length ? i.length ? i.some((e) => o?.fields[e.name]?.state === "invalid") ? "普通设置需要修改" : !o || i.some((e) => o.fields[e.name]?.state !== "valid") ? "普通设置未核对" : n.some((e) => !e.readable) ? "可读设置已校验，部分无权限" : i.some((e) => !e.editable) ? "普通设置有效，当前只读" : "普通设置已校验" : "普通设置无读取权限" : "无普通设置";
				}
				M.value = a, H = !1;
			} catch {
				q(t) && n === V && (M.value = {});
			}
		})().finally(() => {
			G = null, W && H && !U && J();
		}), G;
	}
	function ne(e) {
		if (!K(e) || Object.keys(e).sort().join(",") !== "modules,registry_revision" || !Number.isSafeInteger(e.registry_revision) || !Array.isArray(e.modules) || e.modules.length > 128) throw Error("invalid_modules");
		let t = /* @__PURE__ */ new Set();
		for (let n of e.modules) {
			if (!K(n) || Object.keys(n).sort().join(",") !== "enabled,epoch,health,lifecycle,module_id,reason_code,registry_revision" || typeof n.module_id != "string" || !/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(n.module_id) || t.has(n.module_id) || typeof n.enabled != "boolean" || !Number.isSafeInteger(n.epoch) || !Number.isSafeInteger(n.registry_revision) || typeof n.lifecycle != "string" || typeof n.health != "string" || n.reason_code !== null && typeof n.reason_code != "string") throw Error("invalid_modules");
			t.add(n.module_id);
		}
		return e;
	}
	async function re(e = !1) {
		if (!F || P || C.value) return;
		O.cancel(), k.value = null;
		let t = I;
		C.value = !0, w.value = !0, te(!1);
		try {
			let n = ne(await i.apiPost("admin/modules", {}));
			if (!q(t)) return !1;
			if (b.value = n.modules, R = n.registry_revision, w.value = !1, y) {
				if (await J(!0), !q(t)) return !1;
				j.value = y.labels();
			}
			return e ? T.show("已刷新。", "success") : S.value === "error" && T.clear(), !0;
		} catch {
			return q(t) && (w.value = !0, T.show("模块管理权限或状态无法确认，请重新打开页面后刷新。")), !1;
		} finally {
			q(t) && (C.value = !1, W && H && !U && J());
		}
	}
	async function ie(e, t) {
		if (!q(I) || C.value || w.value || k.value || !b.value.includes(e)) return;
		let n = I, r = R, a = v(), o = ++z;
		k.value = o;
		let s = () => q(n) && !C.value && !w.value && R === r && v() === a && b.value.includes(e), c = () => k.value === o && s();
		try {
			if (!await m({
				owner: e.module_id,
				action: t,
				revision: r,
				isCurrent: c,
				canRestoreFocus: s
			}) || !c() || t === "unload" && !await O.ask({
				kind: "unload",
				owner: e.module_id,
				title: `确认解除挂载 ${e.module_id}`,
				message: `配置、凭据、缓存及业务数据全部保留。注册版本 ${r}；确认后需从宿主插件详情正式重开页面。`,
				confirmLabel: "确认解除挂载（保留数据）",
				isCurrent: c,
				canRestoreFocus: s
			}) || !c()) return;
		} finally {
			k.value === o && (k.value = null);
		}
		if (q(n) && !C.value && !w.value && R === r && v() === a && b.value.includes(e)) {
			C.value = !0, w.value = !0;
			try {
				let a = {
					module_id: e.module_id,
					expected_registry_revision: r,
					...t === "unload" ? {} : { enabled: t === "enable" }
				}, o = await i.apiPost(t === "unload" ? "admin/module-unload" : "admin/module-enabled", a);
				if (!q(n)) return;
				if (!K(o) || o.module_id !== e.module_id || !Number.isSafeInteger(o.registry_revision) || t === "unload" && (o.state !== "unloaded" || o.data_retained !== !0 || o.reopen_required !== !0)) throw Error("operation_unavailable");
				C.value = !1;
				let s = await re();
				if (!q(n)) return;
				if (!s) {
					T.show("已提交，模块状态暂未核实，请刷新确认。", "warning");
					return;
				}
				p(), q(n) && T.show(t === "enable" ? "已恢复模块。请从宿主插件详情重新打开页面，以取得新的模块资源授权。" : t === "unload" ? "模块已解除挂载，持久数据保留。恢复后请从宿主插件详情正式重开页面。" : "模块已禁用，设置与持久数据保留。", "warning");
			} catch (e) {
				q(n) && (T.show(e?.message === "revision_conflict" ? "模块状态已变化，请刷新后重新核对。" : "未确认清理完成；可能仍在排空或清理。请刷新核对，并重试解除挂载。不会创建替代实例。"), w.value = !0);
			} finally {
				q(n) && (C.value = !1);
			}
		}
	}
	let ae = e(t({ setup() {
		return () => n(d, {
			preflightStyleDisabled: !0,
			theme: A.value.isDark === !0 || A.value.theme === "dark" ? f : null,
			locale: A.value.locale === "en-US" ? h : g,
			styleMountTarget: r
		}, { default: () => D.value ? n(u, {
			title: N.value ? void 0 : "模块",
			class: "module-management"
		}, { default: () => [
			x.value ? n(c, {
				class: "module-management-notice",
				type: S.value === "error" ? "error" : S.value === "warning" ? "warning" : S.value === "success" ? "success" : "info",
				showIcon: !1
			}, { default: () => x.value }) : null,
			n(l, {
				disabled: C.value || !F,
				onClick: () => re(!0)
			}, () => C.value ? "正在处理…" : "刷新模块管理状态"),
			!b.value.length && !w.value ? n("p", "尚无已注册模块。全局设置仍可使用。") : null,
			...b.value.filter((e) => N.value === null || e.module_id === N.value).map((e) => n("section", {
				class: "module-management-row",
				key: e.module_id,
				"data-module-owner": e.module_id
			}, [
				N.value ? null : n("h3", j.value.get(e.module_id) || e.module_id),
				n("p", { class: "module-status" }, `${e.enabled ? "已启用" : "已禁用"} · ${{
					active: "运行中",
					stopped: "已停止",
					failed: "运行失败",
					unloaded: "已解除挂载",
					discovered: "待启动",
					starting: "启动中",
					stopping: "停止中"
				}[e.lifecycle] || "运行状态未知"}`),
				y ? n("p", { class: "config-readiness" }, M.value[e.module_id] || "普通设置未核对") : null,
				e.reason_code ? n("p", { role: "status" }, e.reason_code === "health_unknown" ? "健康状态尚未确认。" : "模块有待核对状态，请刷新或查看宿主日志。") : null,
				y && (E.value.has(e.module_id) || y.hasSettings?.(e.module_id)) ? n(l, { onClick: () => y.onSettings(e.module_id) }, () => "进入设置") : null,
				...[
					"enable",
					"disable",
					"unload"
				].map((t) => n(l, {
					disabled: C.value || k.value !== null || w.value || !F || t === "enable" && e.enabled || t === "disable" && !e.enabled,
					onClick: () => ie(e, t)
				}, () => ({
					enable: "启用 / 恢复",
					disable: "禁用",
					unload: "解除挂载（保留数据）"
				})[t]))
			])),
			_ ? null : O.render(),
			n("details", { class: "technical-details" }, [
				n("summary", "高级详情与操作说明"),
				n("p", "禁用仍可配置；解除挂载退出活动入口并保留配置、凭据、缓存及业务数据。恢复后须正式重开。"),
				...b.value.map((e) => n("p", `${e.module_id} · ${e.lifecycle} · ${e.health} · 注册版本 ${R}`)),
				n("p", { "data-reopen-guidance": "" }, "请在宿主左侧点击“插件”，找到 astrbot_plugin_yomihime_game_link，在插件详情中重新打开 monitor 页面，以取得当前资源授权。")
			])
		] }) : null });
	} }));
	ae.mount(r);
	function Y(e) {
		if (P) return;
		A.value = K(e) ? e : {};
		let t = "";
		try {
			if (!K(e) || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== o) throw Error();
			t = ee(e);
		} catch {
			O.cancel(), k.value = null, F = !1, I++, b.value = [], C.value = !1, w.value = !0, T.show("管理上下文无效，请正式重开页面。");
			return;
		}
		F && t === L || (T.clear(), O.cancel(), k.value = null, I++, L = t, F = !0, b.value = [], C.value = !1, re());
	}
	return {
		start() {
			B = i.onContext(Y), Promise.resolve(i.ready()).then(Y).catch(() => {
				P || T.show("宿主会话不可用，请正式重开页面。");
			});
		},
		refresh: re,
		invalidateConfiguration: te,
		setRenderVisible(e) {
			D.value = e;
		},
		setHomeVisible(e) {
			W = e, e && J();
		},
		selectOwner(e) {
			N.value = e;
		},
		presentationLabels(e) {
			j.value = e;
		},
		cancelConfirmation() {
			O.cancel(), k.value = null;
		},
		dispose() {
			T.cancel(), O.cancel(), k.value = null, _ || O.dispose(), P = !0, I++, F = !1, B?.(), ae.unmount();
		}
	};
}
//#endregion
//#region src/main.ts
var Ee = t({ setup() {
	let e = o(0), t = o(null), r = o(null), s = o(null), c = o(null), u = o(null), b = o(!1), x = null, C = null, w = null, T = null, E = !1, D = null, O = document.documentElement.dataset.pageName || "shell", A = o(null), j = o(!1), M = we(document), N = window.location.hash, P = "", F = 0, I = "", L = !1, B = 0, V = 0, H = !1, U, W = () => JSON.stringify([
		F,
		I,
		L,
		B,
		b.value,
		C?.state.home,
		C?.state.settings,
		C?.state.settingsOwner,
		C?.state.selected,
		C?.state.catalog?.catalog_revision,
		C?.state.busy,
		C?.state.stale,
		window.location.hash
	]), G = () => {
		(x || b.value) && (M.cancel(), x?.dispose(), x = null, b.value = !1);
	}, K = () => {
		!x && u.value && L && (b.value = !0, x = Ce(document, window.AstrBotPluginPage, window, {
			expectedPage: O,
			container: u.value,
			scope: "maintenance",
			onDraftMutation: J,
			onConfigurationSaved: () => {
				w?.resume(), T?.invalidateConfiguration();
			},
			beforeMaintenance: (e) => {
				let t = W();
				return M.ask({
					kind: "maintenance",
					title: e.action === "rollback" ? "确认回退受审迁移？" : "确认重新校验并恢复？",
					message: (w?.dirty() ? "存在未保存草稿；维护后仍需重新核对。" : "") + "仅处理受审普通配置，其他持久数据保留。",
					confirmLabel: "确认维护",
					isCurrent: () => L && !H && b.value && t === W() && e.isCurrent()
				});
			}
		}), x.start());
	}, q = async (e) => {
		let t = ++V, n = W();
		M.cancel();
		let r = () => !H && L && t === V && n === W(), i = C?.state.settings ? w?.navigationImpact() : null;
		if (i?.requiresConfirmation) {
			let e = k(C?.state.catalog?.modules || [], C?.state.locale || "zh-CN"), t = (t) => t.map((t) => t === "game_link/core" ? "全局设置" : e.get(t) || t).join("、"), n = [i.ordinaryOwners.length ? `${t(i.ordinaryOwners)}的普通设置草稿将保留。` : "", i.credentialOwners.length ? `${t(i.credentialOwners)}的凭据输入及操作草稿将清空。` : ""].join("");
			if (!await M.ask({
				kind: "navigation",
				title: "离开设置？",
				message: n,
				confirmLabel: "确认离开",
				isCurrent: r
			})) return;
		}
		r() && (G(), e(), N = window.location.hash);
	}, te = (e) => {
		let t = window.location.hash;
		if (t === P) {
			P = "", N = t;
			return;
		}
		if (t !== N) {
			if (E && w?.navigationImpact().requiresConfirmation) {
				e.stopImmediatePropagation(), window.history.replaceState(null, "", N || "#/"), q(() => {
					P = t, window.location.hash = t;
				});
				return;
			}
			M.cancel(), G(), N = t;
		}
	}, J = () => {
		B++, M.cancel(), T?.cancelConfirmation();
	};
	function ne() {
		M.invalidate(), e.value++;
		let t = C?.state.settings || !1, n = !!(C?.state.home || C?.state.moduleSection === "home");
		n || G(), C?.state.catalog && !T && c.value && (T = Te(c.value, window.AstrBotPluginPage, window, O, () => {
			C?.refresh(), w?.resume();
		}, (e) => {
			let t = W();
			return !w?.dirty() || M.ask({
				kind: "module-draft",
				owner: e.owner,
				title: "确认模块操作 " + e.owner,
				message: `存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。注册版本 ${e.revision}。`,
				confirmLabel: "保留草稿并继续核对",
				isCurrent: () => t === W() && e.isCurrent(),
				canRestoreFocus: () => t === W() && e.canRestoreFocus()
			});
		}, M, W, {
			hasSettings: (e) => A.value?.includes(e) || !1,
			onSettings: (e) => void q(() => C?.selectSettings(e)),
			labels: () => k(C?.state.catalog?.modules || [], C?.state.locale || "zh-CN"),
			catalog: pe,
			snapshot: me
		}), T.start()), T?.setRenderVisible(n), T?.presentationLabels(k(C?.state.catalog?.modules || [], C?.state.locale || "zh-CN")), T?.setHomeVisible(!!(C?.state.home || C?.state.moduleSection === "home")), T?.selectOwner(C?.state.moduleOwner || null), C?.state.catalog && (t || C.state.moduleOwner) && !w && s.value && c.value ? (w = Ce(document, window.AstrBotPluginPage, window, {
			expectedPage: O,
			container: s.value,
			initiallySuspended: !t,
			onDraftMutation: J,
			onConfigurationSaved: () => T?.invalidateConfiguration(),
			onCapabilities: (e, t = !1) => {
				A.value = e, j.value = t;
			}
		}), w.start()) : t && !E ? w?.resume() : t && E && D !== C?.state.settingsOwner ? (w?.suspend(), w?.resume()) : !t && E && w?.suspend(), D = C?.state.settingsOwner || null, t && w?.selectOwner(C?.state.settingsOwner || "game_link/core"), E = t;
	}
	return a(() => {
		U = window.AstrBotPluginPage?.onContext((e) => {
			let t = "";
			try {
				if (e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== O) throw Error();
				t = ee(e);
			} catch {
				L = !1, F++, M.cancel(), T?.cancelConfirmation();
				return;
			}
			t !== I && (F++, I = t, M.cancel(), T?.cancelConfirmation()), L = !0;
		}), window.addEventListener("hashchange", te), C = R({
			bridge: window.AstrBotPluginPage,
			container: t.value,
			window,
			loadPage: _,
			expectedPage: document.documentElement.dataset.pageName || "shell",
			changed: ne
		}), C.start();
	}), i(() => {
		H = !0, F++, G(), M.dispose(), U?.(), window.removeEventListener("hashchange", te), w?.dispose(), T?.dispose(), C?.dispose();
	}), () => {
		e.value;
		let i = C?.state, a = C?.selected(), o = i?.catalog?.modules || [], _ = k(o, i?.locale || "zh-CN"), x = (e) => _.get(e) || e, E = i?.busy || !1, D = i?.home ?? !0, O = o.find((e) => e.module_id === i?.moduleOwner), N = !!(O && i?.moduleSection === "home"), P = !(!O || i?.moduleSection !== "queries" && !a), ee = (e) => (t) => {
			t.preventDefault(), q(e);
		}, F = (e, t, r, i) => n("a", {
			href: t,
			"aria-current": r ? "page" : void 0,
			onClick: ee(i)
		}, e), I = (e) => n("div", { class: "shell-field" }, [n("label", { for: e }, "模块"), n("select", {
			id: e,
			value: O?.module_id || "",
			disabled: !o.length,
			onChange: (e) => {
				let t = e.target.value;
				t && q(() => C?.selectModule(t));
			}
		}, [n("option", { value: "" }, "选择模块"), ...o.map((e) => n("option", { value: e.module_id }, x(e.module_id)))])]);
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
			n("nav", { "aria-label": "主要导航" }, [F("首页", "#/", D, () => C?.selectHome())]),
			n("nav", { "aria-label": "已挂载模块" }, o.map((e) => F(x(e.module_id), "#/module/" + encodeURIComponent(e.module_id), i?.moduleOwner === e.module_id, () => C?.selectModule(e.module_id)))),
			n("div", { class: "shell-settings-slot" }, [n("button", {
				class: "shell-settings",
				type: "button",
				"aria-current": i?.settings && i.settingsOwner === null ? "page" : void 0,
				onClick: () => void q(() => C?.selectSettings())
			}, "全局设置")])
		]), n("div", { class: "shell-content" }, [n("div", { class: "shell-mobile" }, [
			n("p", { class: "shell-brand" }, "如月怜的游戏连结"),
			n("nav", { "aria-label": "移动导航" }, [F("首页", "#/", D, () => C?.selectHome()), F("全局设置", "#/settings", !!(i?.settings && i.settingsOwner === null), () => C?.selectSettings())]),
			I("module-select-mobile")
		]), n("main", {
			ref: r,
			id: "page-root",
			tabindex: -1,
			"aria-labelledby": "page-title"
		}, [
			n(v, {
				class: "shell-heading",
				titleId: "page-title",
				title: O ? x(O.module_id) : i?.settings && i.settingsOwner === null ? "全局设置" : D ? "首页" : "页面不可用"
			}, { default: () => n(l, {
				onClick: () => {
					C?.state.busy || (C?.refresh(), (D || N) && T?.refresh(!0));
				},
				"aria-disabled": String(E),
				"aria-busy": String(E),
				"aria-label": "刷新模块状态"
			}, () => E ? "正在刷新…" : "刷新模块状态") }),
			O ? n("nav", {
				class: "module-tabs",
				"aria-label": "模块内分页"
			}, [
				F("主页", "#/module/" + encodeURIComponent(O.module_id), N, () => C?.selectModule(O.module_id)),
				A.value?.includes(O.module_id) ? F("配置", "#/settings/module/" + encodeURIComponent(O.module_id), !!i?.settings, () => C?.selectSettings(O.module_id)) : null,
				O.pages.length ? F("辅助查询", "#/module/" + encodeURIComponent(O.module_id) + "/-/queries", P, () => C?.selectModule(O.module_id, "queries")) : null
			]) : null,
			n(S, {
				class: "shell-status",
				hidden: !E && !i?.stale && i?.message === "状态已更新；查询仅在手动提交时执行。",
				tone: i?.stale ? "warning" : "neutral"
			}, { default: () => i?.message || "正在准备页面。" }),
			O && !j.value && !A.value?.includes(O.module_id) ? n(S, {}, { default: () => ["配置能力尚未核对。", n("button", {
				type: "button",
				onClick: () => w?.resume()
			}, "重试读取配置能力")] }) : null,
			i?.cleanupPending ? n(l, { onClick: () => C?.retryCleanup() }, () => "重试页面清理") : null,
			M.render(),
			P ? n("nav", {
				class: "query-links",
				"aria-label": "辅助查询入口"
			}, O.pages.map((e) => F(e.title, `#/module/${encodeURIComponent(O.module_id)}/${e.route_id}`, i?.selected?.route === e.route_id, () => C?.select(O.module_id, e.route_id)))) : null,
			N && O.pages.length ? n(y, { title: "可用功能" }, { default: () => n("div", { class: "module-capabilities" }, O.pages.map((e) => n("span", { key: e.route_id }, e.title))) }) : null,
			P && !a ? n("p", { class: "shell-empty" }, "选择一项辅助查询；仅在手动提交时请求数据。") : null,
			n("div", {
				ref: t,
				id: "module-container",
				hidden: !a
			}),
			n("div", {
				class: "settings-container",
				hidden: !i?.settings || !(!i.settingsOwner || O)
			}, [n("div", {
				ref: s,
				id: "settings-container"
			})]),
			n("div", {
				class: "module-dashboard",
				hidden: !D && !N
			}, [n("div", {
				ref: c,
				id: "module-management-container",
				hidden: b.value
			}), D || N ? n(l, {
				size: "large",
				onClick: b.value ? G : K
			}, () => b.value ? "关闭配置维护" : "配置维护") : null]),
			n("div", {
				ref: u,
				id: "maintenance-container",
				hidden: !b.value
			}),
			!D && !i?.settings && !N && !P && !i?.mounted || i?.settingsOwner && !O ? n("p", { class: "shell-empty" }, i?.cleanupPending ? "页面清理未完成，请重新打开页面。" : a ? "模块页面正在准备。" : "此路由当前没有可用的注册页面。恢复模块后请从宿主插件详情正式重开。") : null
		])])])] });
	};
} }), De = e(Ee);
De.mount("#app"), window.addEventListener("pagehide", () => De.unmount(), { once: !0 });
//#endregion
