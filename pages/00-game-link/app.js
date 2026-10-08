import { createApp as e, defineComponent as t, h as n, nextTick as r, onBeforeUnmount as i, onMounted as a, ref as o, shallowRef as s } from "./runtime.js";
import { NAlert as c, NButton as l, NCard as u, NConfigProvider as d, NTag as f, darkTheme as p, dateEnUS as m, dateZhCN as h, enUS as g, zhCN as _ } from "./runtime.js";
import { loadPage as v } from "./module-loader.js";
//#region src/catalog.ts
var y = (e) => typeof e == "object" && !!e && !Array.isArray(e), b = (e, t = 4096) => typeof e == "string" && e.length > 0 && e.length <= t, x = (e) => b(e) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\/(?:[a-zA-Z0-9_.-]+\/)*[a-zA-Z0-9_.-]+$/.test(e) && !e.split("/").some((e) => e === "." || e === "..");
function S(e) {
	if (e == null) return null;
	let t = (e, t) => typeof e == "string" && e.trim().length > 0 && [...e].length <= t && !/[\p{Cc}\p{Cf}\p{Cs}]/u.test(e), n = () => {
		throw Error("invalid_catalog");
	};
	(!y(e) || Object.keys(e).some((e) => ![
		"default_name",
		"localized_names",
		"short_name"
	].includes(e)) || !t(e.default_name, 128)) && n();
	let r = e, i = Object.hasOwn(r, "localized_names") ? r.localized_names : {}, a = r.short_name ?? null;
	(!y(i) || Object.keys(i).length > 16 || a !== null && !t(a, 32)) && n();
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
function C(e, t, n = !1) {
	let r = e.display;
	if (!r) return e.module_id;
	if (n && r.short_name) return r.short_name;
	let i = t.toLowerCase(), a = Object.entries(r.localized_names);
	return a.find(([e]) => e.toLowerCase() === i)?.[1] ?? a.find(([e]) => e.toLowerCase() === i.split("-")[0])?.[1] ?? r.default_name ?? e.module_id;
}
function w(e, t, n = !1) {
	let r = e.map((e) => C(e, t, n));
	return new Map(e.map((e, t) => [e.module_id, r.filter((e) => e.normalize("NFC").toLowerCase() === r[t].normalize("NFC").toLowerCase()).length > 1 ? r[t] + " · " + e.module_id : r[t]]));
}
function T(e) {
	let t = () => {
		throw Error("invalid_catalog");
	};
	(!y(e) || e.schema_version !== 1 || !y(e.runtime) || ![
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
		(!y(e) || !b(e.module_id) || !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(e.module_id) || !b(e.route) || ![
			"loaded",
			"disabled",
			"unavailable"
		].includes(String(e.state)) || !Number.isSafeInteger(e.module_epoch) || Number(e.module_epoch) < 0 || !Array.isArray(e.pages) || e.pages.length > 64 || !Array.isArray(e.resources) || e.resources.length > 128) && t();
		let r = e;
		(r.state === "loaded" ? !b(r.runtime_id) || r.enabled !== !0 || r.lifecycle !== "active" || n.runtime.state !== "ready" : r.runtime_id !== null || r.pages.length || r.resources.length) && t(), (!b(r.asset_version, 64) || !/^[a-f0-9]{64}$/.test(r.asset_version)) && t();
		let i = `module-assets/${r.module_id}/`, a = r.resources.map((e) => ((!y(e) || !x(e.path) || !e.path.startsWith(i) || !b(e.sha256, 64) || !/^[a-f0-9]{64}$/.test(e.sha256)) && t(), {
			path: e.path,
			sha256: e.sha256
		}));
		new Set(a.map((e) => e.path)).size !== a.length && t();
		let o = r.pages.map((e) => ((!y(e) || !b(e.route_id, 128) || !/^[a-z][a-z0-9_.-]*$/.test(e.route_id) || !b(e.title, 256) || !Number.isSafeInteger(e.order) || e.access !== "public_web" || !(e.capability_id === null || b(e.capability_id, 128)) || !x(e.entry) || !e.entry.startsWith(i) || !e.entry.endsWith(".js") || !Array.isArray(e.styles) || e.styles.length > 16 || !e.styles.every((e) => x(e) && e.startsWith(i) && e.endsWith(".css"))) && t(), [e.entry, ...e.styles].every((e) => a.some((t) => t.path === e)) || t(), {
			route_id: e.route_id,
			title: e.title,
			order: e.order,
			access: "public_web",
			capability_id: e.capability_id,
			entry: e.entry,
			styles: [...e.styles]
		}));
		return new Set(o.map((e) => e.route_id)).size !== o.length && t(), {
			display: S(r.display),
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
function E(e, t) {
	return `#/module/${encodeURIComponent(e)}/${encodeURIComponent(t)}`;
}
function D(e) {
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
function O(e) {
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
var k = /* @__PURE__ */ new Set([
	"isDark",
	"theme",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function A(e) {
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
	return JSON.stringify(n(Object.fromEntries(Object.entries(e).filter(([e]) => !k.has(e)))));
}
function j(e, t, n) {
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
function M(e, t) {
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
function N(e, t, n) {
	let r = /* @__PURE__ */ new Map();
	function i(e) {
		let t = r.get(e);
		if (!t) {
			let n = {
				revoked: !1,
				scope: O(() => !n.revoked),
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
function P({ bridge: e, container: t, window: n, loadPage: r, changed: i, timeoutMs: a = 8e3, expectedPlugin: o = "astrbot_plugin_yomihime_game_link", expectedPage: s = "shell" }) {
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
		settingsOwner: null
	}, l = !1, u = !1, d = 0, f = 0, p = "", m = !1, h, g = null, _ = "", v = "", y = null, b = N(t.ownerDocument, r, a), x = /* @__PURE__ */ new Map(), S = /* @__PURE__ */ new Set(), C = "模块资源已失效。请关闭此页，并从宿主插件详情重新打开页面。", w = !1, k = !1, P = "", F = null, I = /* @__PURE__ */ new Set(), L = (e) => new Promise((t, n) => {
		let r = setTimeout(() => {
			I.delete(r), n(Error("timeout"));
		}, a);
		I.add(r), e.then(t, n).finally(() => {
			clearTimeout(r), I.delete(r);
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
			available: u && !c.busy && !c.stale && !F && !w && !S.has(e.module_id),
			owner: e.module_id,
			runtimeId: e.runtime_id,
			epoch: e.module_epoch,
			boundary: p
		});
	}
	function B() {
		c.cleanupPending = F !== null || b.cleanupPending, c.cleanupPending && (c.message = "页面清理未完成，请重试页面清理。");
	}
	function V(e) {
		S.add(e), P === e && U(), b.invalidate(e), B();
	}
	function H(e) {
		let t = new Map((e.modules || []).filter((e) => e.state === "loaded").map((e) => [e.module_id, M(e, p)]));
		if (k) {
			for (let [e, n] of x) t.get(e) !== n && V(e);
			for (let e of t.keys()) x.has(e) || V(e);
		}
		x.clear();
		for (let [e, n] of t) x.set(e, n);
		k = !0;
	}
	function U() {
		F ||= (f++, v = "", _ = "", c.mounted = !1, {
			scope: y,
			page: g,
			scopeDone: y === null,
			pageDone: g === null,
			domDone: !1
		});
		let e = F, n = [];
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
		e.scopeDone && e.pageDone && e.domDone && (y = null, g = null, F = null, P = ""), B();
	}
	async function W() {
		if (F) {
			i();
			return;
		}
		let n = R();
		if (!n || !u) {
			U(), !c.cleanupPending && (w || S.has(c.selected?.owner || "")) && (c.message = C), i();
			return;
		}
		let { module: r, page: a } = n, o = j(r, a, p);
		if (w || S.has(r.module_id)) {
			U(), c.cleanupPending || (c.message = C), i();
			return;
		}
		if (o === _ && g) {
			g.update(z(r)), i();
			return;
		}
		if (o === v) return;
		if (U(), F) {
			i();
			return;
		}
		v = o, P = r.module_id;
		let s = f, d = O(() => !l && f === s && R() !== null);
		y = d;
		try {
			let n = await L(b.load(r, a));
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
		if (c.settings = n.location.hash === "#/settings" || n.location.hash.startsWith("#/settings/module/"), c.home = !n.location.hash || n.location.hash === "#/" || n.location.hash === "#", c.settingsOwner = null, c.settings) {
			if (n.location.hash.startsWith("#/settings/module/")) try {
				c.settingsOwner = decodeURIComponent(n.location.hash.slice(18));
			} catch {}
			c.selected = null;
			return;
		}
		c.selected = D(n.location.hash);
	}
	async function K() {
		if (l || !u || c.busy) return;
		let t = ++d;
		c.busy = !0, c.stale = c.catalog !== null, c.message = c.stale ? "正在刷新；当前为上次目录，暂不可提交查询。" : "正在读取模块目录。";
		let n = R();
		n && g && g.update(z(n.module)), i();
		try {
			let n = T(await L(e.apiGet("catalog", {})));
			if (l || t !== d) return;
			H(n), c.catalog = n, c.busy = !1, c.stale = !1, c.cleanupPending || (c.message = "状态已更新；查询仅在手动提交时执行。"), G(), await W();
		} catch {
			if (l || t !== d) return;
			c.busy = !1, c.stale = c.catalog !== null, c.message = "状态读取失败。保留上次目录，请确认宿主会话后手动重试。";
			let e = R();
			e && g && g.update(z(e.module)), i();
		}
	}
	function q(e, t = !1) {
		if (l || w || !t && m) return;
		c.theme = typeof e?.isDark == "boolean" ? e.isDark ? "dark" : "light" : e?.theme === "dark" ? "dark" : "light", c.locale = typeof e?.locale == "string" ? e.locale : "zh-CN";
		let n = () => {
			w = !0, d++, u = !1, c.busy = !1, c.stale = c.catalog !== null, U(), b.invalidateAll(), B(), c.cleanupPending || (c.message = C), i();
		}, r;
		try {
			if (r = A(e), !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== o || e.pageName !== s) throw Error("wrong_page");
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
		m = !0, p = r, u = !0, K();
	}
	let J = () => {
		G(), W(), t.ownerDocument.getElementById("page-root")?.focus({ preventScroll: !0 }), n.scrollTo({
			top: 0,
			left: 0,
			behavior: "instant"
		});
	};
	return {
		state: c,
		selected: R,
		refresh: K,
		selectHome() {
			n.location.hash = "#/", G(), W();
		},
		selectSettings(e) {
			n.location.hash = e ? "#/settings/module/" + encodeURIComponent(e) : "#/settings", G(), W();
		},
		select(e, t) {
			n.location.hash = E(e, t), c.settings = !1, c.home = !1, c.selected = {
				owner: e,
				route: t
			}, W();
		},
		retryCleanup() {
			c.cleanupPending && (F && U(), b.retryCleanup(), B(), c.cleanupPending || (c.message = w || S.has(c.selected?.owner || "") ? C : "页面清理完成，请手动刷新状态。", l || W()), i());
		},
		start() {
			if (!l) {
				if (n.addEventListener("hashchange", J), !e?.ready || !e?.onContext || !e?.apiGet || !e?.apiPost) {
					c.message = "宿主连接尚未就绪，请重新打开页面。", i();
					return;
				}
				h = e.onContext((e) => q(e, !0)), L(Promise.resolve(e.ready())).then((e) => q(e)).catch(() => {
					!u && !l && (c.message = "连接宿主失败，请重新打开页面。", i());
				});
			}
		},
		dispose() {
			if (!l) {
				l = !0, d++, u = !1, h?.(), n.removeEventListener("hashchange", J);
				for (let e of I) clearTimeout(e);
				I.clear(), U(), b.invalidateAll(), B();
			}
		}
	};
}
//#endregion
//#region src/navigation.ts
function F(e) {
	e && (e.focus({ preventScroll: !0 }), e.scrollIntoView({
		block: "start",
		inline: "nearest",
		behavior: "instant"
	}));
}
//#endregion
//#region src/management-view.ts
function I(e) {
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
function L(r, i = r.getElementById("management-root")) {
	let a = s([]), o = s({}), l = () => {}, f = s([]), v = s(""), y = s(null), b = (e, t) => n("button", {
		id: e,
		type: "button",
		disabled: !0
	}, t), x = t({ setup() {
		return () => {
			let e = o.value.isDark === !0 || o.value.theme === "dark", t = String(o.value.locale || "").startsWith("en");
			return n(d, {
				preflightStyleDisabled: !0,
				theme: e ? p : null,
				locale: t ? g : _,
				dateLocale: t ? m : h,
				inlineThemeDisabled: !0
			}, { default: () => n("section", { class: "management" }, [
				i.id === "management-root" ? n("h1", "设置") : null,
				n(c, {
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
				}, a.value.map((e) => n("section", {
					class: "card",
					key: e.target,
					"data-owner": e.target,
					hidden: y.value !== null && y.value !== e.target
				}, [n(u, {}, { default: () => [
					...I(e.controls).map((e) => n("fieldset", {
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
									e.draftVersion++, l();
								}
							}),
							n(a ? "select" : "input", {
								id: i,
								"data-role": "value",
								"aria-describedby": e.problem ? i + "-detail" : void 0,
								onInput: () => {
									e.mode && (e.mode.value = "replace"), e.draftVersion++, l();
								},
								onChange: () => {
									e.mode && (e.mode.value = "replace"), e.draftVersion++, l();
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
									e.input.value = a ? String(a.findIndex((e) => e === n)) : n === null ? "" : String(n), l();
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
				}, v.value),
				n("section", {
					id: "credentials",
					"aria-label": "来源凭据"
				}, f.value.map((e) => {
					let t = `credential-${encodeURIComponent(e.field.module_id)}-${encodeURIComponent(e.field.name)}`, r = () => {
						e.draftVersion++;
					};
					return n(u, {
						class: "card",
						key: e.signature,
						title: e.field.description.split("；")[0],
						"data-credential": e.field.name,
						"data-owner": e.field.module_id,
						hidden: y.value !== null && y.value !== e.field.module_id
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
								r(), e.mode?.value !== "replace" && (e.clientId && (e.clientId.value = ""), e.clientSecret && (e.clientSecret.value = "")), e.confirm && (e.confirm.checked = !1), l();
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
				n("details", { class: "advanced-maintenance" }, [n("summary", "高级维护与启动修复"), n(u, {
					class: "card",
					title: "限定恢复"
				}, { default: () => [
					n("p", "恢复默认仅作用于指定配置字段。缓存清理和持久业务数据删除尚无通用范围合同，当前不提供执行入口。解除挂载始终保留数据。"),
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
	return S.mount(i), {
		owner(e) {
			y.value = e;
		},
		fields(e, t) {
			l = t, a.value = e;
		},
		credentials(e, t, n = "") {
			l = t, f.value = e, v.value = n;
		},
		context(e) {
			o.value = e || {};
		},
		dispose() {
			S.unmount();
		}
	};
}
//#endregion
//#region src/management.js
var R = (e) => typeof e == "object" && !!e && !Array.isArray(e) && [Object.prototype, null].includes(Object.getPrototypeOf(e)), z = (e, t) => R(e) && Object.keys(e).sort().join("\0") === [...t].sort().join("\0"), B = /^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/, V = (e = "invalid_config") => {
	throw Error(e);
}, H = (e) => JSON.stringify([e.module_id, e.name]), U = (e, t) => e.fields.filter((e) => e.module_id === t), W = (e) => [...e].length;
function G(e, t = 32, n = 65536) {
	let r = /* @__PURE__ */ new Set(), i = 0;
	function a(e, o) {
		if ((++i > n || o > t) && V(), typeof e == "string") {
			W(e) > 4096 && V();
			return;
		}
		if (!(e === null || typeof e == "boolean" || typeof e == "number" && Number.isFinite(e))) {
			if ((!R(e) && !Array.isArray(e) || r.has(e)) && V(), r.add(e), Array.isArray(e)) {
				Object.keys(e).length !== e.length && V();
				for (let t of e) a(t, o + 1);
			} else for (let [t, n] of Object.entries(e)) a(t, o + 1), a(n, o + 1);
			r.delete(e);
		}
	}
	a(e, 0), new TextEncoder().encode(JSON.stringify(e)).length > 262144 && V();
}
function K(e, t) {
	return e === null ? !0 : e.enum && !e.enum.includes(t) ? !1 : e.type === "string" ? typeof t == "string" && (e.minLength === void 0 || W(t) >= e.minLength) && (e.maxLength === void 0 || W(t) <= e.maxLength) : ["integer", "number"].includes(e.type) ? (e.type === "integer" ? Number.isSafeInteger(t) : typeof t == "number" && Number.isFinite(t)) && (e.minimum === void 0 || t >= e.minimum) && (e.maximum === void 0 || t <= e.maximum) : e.type === "boolean" ? typeof t == "boolean" : e.type === "array" ? Array.isArray(t) && t.every((t) => K(e.items, t)) : e.type === "object" && R(t) && (e.required || []).every((e) => Object.hasOwn(t, e)) && Object.entries(t).every(([t, n]) => Object.hasOwn(e.properties || {}, t) && K(e.properties[t], n));
}
function q(e, t = 0) {
	(!R(e) || t > 16 || ![
		"string",
		"integer",
		"number",
		"boolean",
		"object",
		"array"
	].includes(e.type)) && V();
	let n = ["type", "description"];
	if ("description" in e && (typeof e.description != "string" || !e.description.trim()) && V(), e.type === "object") {
		n.push("properties", "required", "additionalProperties"), ("properties" in e && !R(e.properties) || "additionalProperties" in e && e.additionalProperties !== !1) && V();
		let r = e.properties || {};
		for (let [e, n] of Object.entries(r)) e.trim() || V(), q(n, t + 1);
		"required" in e && (!Array.isArray(e.required) || new Set(e.required).size !== e.required.length || !e.required.every((e) => typeof e == "string" && Object.hasOwn(r, e))) && V();
	} else if (e.type === "array") n.push("items"), q(e.items, t + 1);
	else {
		n.push("enum");
		let t = e.type === "string" ? ["minLength", "maxLength"] : ["integer", "number"].includes(e.type) ? ["minimum", "maximum"] : [];
		n.push(...t);
		for (let n of t) n in e && (!Number.isFinite(e[n]) || e.type !== "number" && !Number.isSafeInteger(e[n]) || e.type === "string" && e[n] < 0) && V();
		t.length && t.every((t) => t in e) && e[t[0]] > e[t[1]] && V(), "enum" in e && (!Array.isArray(e.enum) || !e.enum.length || new Set(e.enum).size !== e.enum.length || !e.enum.every((t) => K({
			...e,
			enum: void 0
		}, t))) && V();
	}
	Object.keys(e).some((e) => !n.includes(e)) && V();
}
function J(e) {
	G(e), (!z(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 128) && V();
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
	for (let r of e.fields) (!z(r, t) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => B.test(e)) || typeof r.name != "string" || !r.name || W(r.name) > 128 || /[\s\x00-\x1f]/u.test(r.name) || typeof r.description != "string" || !(r.group === null || typeof r.group == "string" && B.test(r.group)) || ![
		r.required,
		r.readable,
		r.editable
	].every((e) => typeof e == "boolean")) && V(), (r.blocked_reason === null ? !r.readable || !r.editable : r.blocked_reason === "not_granted" ? r.readable || r.editable : r.blocked_reason !== "semantic_validator_unavailable" || !r.readable || r.editable) && V(), n.has(H(r)) && V(), n.add(H(r)), r.value_schema !== null && (G(r.value_schema, 16, 2048), q(r.value_schema), r.default !== null && !K(r.value_schema, r.default) && V());
	let r = JSON.parse(JSON.stringify(e)), i = (e) => {
		if (e && typeof e == "object") {
			for (let t of Object.values(e)) i(t);
			Object.freeze(e);
		}
		return e;
	};
	return i(r);
}
function ee(e, t) {
	t || V(), G(e);
	let n = [...new Set(t.fields.filter((e) => e.readable).map((e) => e.module_id))];
	z(e, n) || V();
	for (let r of n) {
		let n = e[r], i = U(t, r).filter((e) => e.readable);
		(!z(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !z(n.fields, i.map((e) => e.name))) && V();
		for (let e of i) {
			let t = n.fields[e.name];
			(!z(t, [
				"value",
				"state",
				"present",
				"source"
			]) || !["valid", "invalid"].includes(t.state) || typeof t.present != "boolean" || !["sqlite", "default"].includes(t.source) || (t.state === "invalid" ? t.value !== null : !K(e.value_schema, t.value))) && V();
		}
	}
	return e;
}
var Y = (e) => e.value_schema !== null && [
	"string",
	"integer",
	"number",
	"boolean"
].includes(e.value_schema.type);
function X(e, t) {
	let n = e.value_schema, r = t;
	if (n.enum || n.type === "boolean") {
		let e = n.enum || [!1, !0];
		(!/^\d+$/.test(t) || !Object.hasOwn(e, Number(t))) && V(), r = e[Number(t)];
	} else ["integer", "number"].includes(n.type) && ((typeof t != "string" || !t.trim() || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(t.trim())) && V(), r = Number(t));
	return K(n, r) || V(), r;
}
function te(e, t, n, r) {
	(!r || !R(n) || !Number.isSafeInteger(t) || t < 1) && V();
	let i = U(r, e), a = [];
	(!i.length || Object.keys(n).some((e) => !i.some((t) => t.name === e))) && V();
	for (let [e, t] of Object.entries(n)) {
		let n = i.find((t) => t.name === e);
		(!R(t) || ![
			"keep",
			"replace",
			"clear"
		].includes(t.mode)) && V(), t.mode !== "keep" && ((!n.readable || !n.editable || !Y(n)) && V("admin_authorization_denied"), a.push(t.mode === "clear" ? {
			field: e,
			mode: "clear"
		} : {
			field: e,
			mode: "replace",
			value: X(n, t.value)
		}));
	}
	return a.length || V("select_changes"), {
		module_id: e,
		expected_revision: t,
		updates: a
	};
}
function ne(e) {
	G(e), (!z(e, ["schema_version", "fields"]) || e.schema_version !== 1 || !Array.isArray(e.fields) || e.fields.length > 32) && V();
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
		(!z(r, [
			"module_id",
			"name",
			"group",
			"description",
			"value_schema"
		]) || typeof r.module_id != "string" || r.module_id.split("/").length !== 2 || !r.module_id.split("/").every((e) => B.test(e)) || typeof r.name != "string" || !B.test(r.name) || typeof r.group != "string" || !B.test(r.group) || typeof r.description != "string" || !r.description || t.has(H(r))) && V(), t.add(H(r)), (!z(r.value_schema, [
			"type",
			"properties",
			"required",
			"additionalProperties"
		]) || r.value_schema.type !== "object" || r.value_schema.additionalProperties !== !1 || !z(r.value_schema.properties, ["client_id", "client_secret"]) || JSON.stringify(r.value_schema.required) !== JSON.stringify(n.required)) && V();
		for (let e of n.required) {
			let t = r.value_schema.properties[e];
			(!z(t, [
				"type",
				"minLength",
				"maxLength"
			]) || t.type !== "string" || t.minLength !== 1 || t.maxLength !== 512) && V();
		}
	}
	return JSON.parse(JSON.stringify(e));
}
function re(e, t) {
	G(e);
	let n = [...new Set(t.fields.map((e) => e.module_id))];
	z(e, n) || V();
	for (let r of n) {
		let n = e[r];
		(!z(n, ["revision", "fields"]) || !Number.isSafeInteger(n.revision) || n.revision < 1 || !z(n.fields, U(t, r).map((e) => e.name)) || Object.values(n.fields).some((e) => ![
			"unset",
			"configured",
			"unusable"
		].includes(e))) && V();
	}
	return e;
}
function ie(e, t, n, r, i, a) {
	return (!Number.isSafeInteger(t) || t < 1 || !a.fields.some((t) => t.module_id === e && t.name === n) || !["replace", "clear"].includes(r)) && V(), r === "replace" && (!z(i, ["client_id", "client_secret"]) || Object.values(i).some((e) => typeof e != "string" || W(e) < 1 || W(e) > 512 || /[\x00-\x1f\x7f-\x9f]/u.test(e))) && V(), {
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
var Z = /* @__PURE__ */ new Set([
	"theme",
	"isDark",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function ae(e, t) {
	(!R(e) || !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== t) && V("invalid_context");
	let n = Object.fromEntries(Object.entries(e).filter(([e]) => !Z.has(e)));
	G(n);
	let r = (e) => {
		if (typeof e == "number" && Object.is(e, -0) && V("invalid_context"), e && typeof e == "object") for (let t of Object.values(e)) r(t);
	};
	r(n);
	let i = (e) => Array.isArray(e) ? e.map(i) : R(e) ? Object.fromEntries(Object.keys(e).sort().map((t) => [t, i(e[t])])) : e;
	return JSON.stringify(i(n));
}
function Q(e, t, n, { expectedPage: i = "management", container: a = e.getElementById("management-root") } = {}) {
	a || V("invalid_container");
	let o = (e) => Array.from(a.querySelectorAll("[id]")).find((t) => t.id === e) || null, s = null, c = null, l = null, u = null, d = !1, f = !1, p = !0, m = 0, h = null, g = !1, _ = null, v = null, y = L(e, a), b = /* @__PURE__ */ new Map(), x = /* @__PURE__ */ new Map(), S = o("status"), C = /* @__PURE__ */ new Map(), w = !1, T = null, E = null, D = (e) => !f && d && m === e, O = (e) => e.readable && e.editable && Y(e), k = (e) => e.disabled || e.getAttribute("aria-disabled") === "true";
	function A(t, n) {
		t.setAttribute("aria-disabled", String(n)), t.disabled = n && (f || !d || e.activeElement !== t);
	}
	function j() {
		A(o("refresh"), f || !d || v !== null);
		let e = !f && d && !p && c !== null && Object.keys(c).length > 0 && v === null;
		A(o("rollback"), !e), A(o("recover"), !e || s.fields.some((e) => e.readable && !e.editable)), o("replacement").disabled = k(o("recover"));
		for (let [t, n] of b) {
			let r = E === null || E === t;
			A(n.save, !e || !r || !s || !U(s, t).some(O)), n.discard && A(n.discard, !d || f || v !== null || !r);
		}
		for (let e of x.values()) {
			let t = !f && d && (E === null || E === e.field.module_id) && O(e.field);
			e.mode.disabled = !t, e.input.disabled = !t, e.reset && (e.reset.disabled = !t);
		}
		for (let e of C.values()) {
			let t = !f && d && (E === null || E === e.field.module_id) && u !== null && l !== null && l.fields.some((t) => H(t) === H(e.field) && JSON.stringify(t) === e.signature);
			e.save && A(e.save, !t || p || v !== null || e.mode?.value === "replace" && !w), e.mode && (e.mode.disabled = !t);
			let n = e.mode?.querySelector("option[value=\"replace\"]");
			n && (n.disabled = !t || !w);
			for (let n of [e.clientId, e.clientSecret]) n && (n.disabled = !t || !w || e.mode?.value !== "replace");
			e.confirm && (e.confirm.disabled = !t || e.mode?.value !== "clear");
		}
	}
	async function M(e, n, r) {
		D(r) || V("stale_context");
		let i = await t.apiPost(`admin/${e}`, n);
		return D(r) || V("stale_context"), e === "catalog" ? J(i) : e === "read" ? i : e === "credential-catalog" ? ne(i) : e === "credential-readiness" ? ((!z(i, [
			"ready",
			"state",
			"reason_code"
		]) || typeof i.ready != "boolean" || !["ready", "unavailable"].includes(i.state) || i.state === "ready" !== i.ready || !["ready", "secret_encryption_unavailable"].includes(i.reason_code)) && V("operation_unavailable"), i) : ((e === "update" || e === "credential-update") && (!z(i, ["module_id", "revision"]) || i.module_id !== n.module_id || !Number.isSafeInteger(i.revision) || i.revision <= n.expected_revision) && V("operation_unavailable"), e === "rollback" && (!z(i, ["rolled_back"]) || i.rolled_back !== !0) && V("operation_unavailable"), e === "recover" && (!z(i, ["recovered"]) || i.recovered !== !0) && V("operation_unavailable"), i);
	}
	function N(e, t) {
		if (t === null) return "";
		let n = e.value_schema?.enum || (e.value_schema?.type === "boolean" ? [!1, !0] : null);
		return String(n ? n.findIndex((e) => e === t) : t);
	}
	function P(e) {
		return {
			field: e,
			signature: JSON.stringify(e),
			detail: "",
			draftVersion: 0,
			mode: null,
			input: null
		};
	}
	function F(e) {
		let t = b.get(e).save;
		k(t) || q(async (t) => {
			let n = Object.fromEntries(U(s, e).map((e) => {
				let t = x.get(H(e));
				return [e.name, {
					mode: t.mode.value,
					value: t.input.value,
					draftVersion: t.draftVersion
				}];
			}));
			await M("update", te(e, c[e].revision, n, s), t), await K(t);
			for (let [t, r] of Object.entries(n)) {
				if (r.mode === "keep") continue;
				let n = x.get(JSON.stringify([e, t])), i = c[e]?.fields[t];
				n && i && n.draftVersion === r.draftVersion && n.mode.value === r.mode && n.input.value === r.value && (n.mode.value = "keep", n.input.value = N(n.field, i.value));
			}
			S.textContent = "设置已保存。";
		});
	}
	function I(e) {
		if (!f && d && v === null && c?.[e]) {
			for (let t of U(s, e)) {
				let n = x.get(H(t)), r = c[e].fields[t.name];
				n && n.mode && n.input && r && (n.draftVersion++, n.mode.value = "keep", n.input.value = N(t, r.value));
			}
			for (let t of C.values()) if (t.field.module_id === e) {
				for (let e of [t.clientId, t.clientSecret]) e && (e.value = "");
				t.confirm && (t.confirm.checked = !1), t.mode && (t.mode.value = "keep"), t.draftVersion++;
			}
			j(), S.textContent = "已放弃修改。";
		}
	}
	function R() {
		for (let e of C.values()) {
			for (let t of [e.clientId, e.clientSecret]) t && (t.value = "");
			e.confirm && (e.confirm.checked = !1), e.mode && (e.mode.value = "keep"), e.draftVersion++;
		}
	}
	function B(e) {
		k(e.save) || e.mode.value === "replace" && !w || q(async (t) => {
			let n = e.mode.value;
			n === "keep" && V("select_changes"), n === "clear" && !e.confirm.checked && V("confirm_clear");
			let r = e.draftVersion, i = {
				client_id: e.clientId.value,
				client_secret: e.clientSecret.value
			};
			await M("credential-update", ie(e.field.module_id, u[e.field.module_id].revision, e.field.name, n, i, l), t), await K(t), e.draftVersion === r && e.mode.value === n && e.clientId.value === i.client_id && e.clientSecret.value === i.client_secret && (e.clientId.value = "", e.clientSecret.value = "", e.mode.value = "keep", e.confirm.checked = !1), j(), S.textContent = n === "clear" ? "已清除所选来源凭据；业务查询需要重新配置该组凭据。" : "来源凭据已加密保存；尚未验证所属上游。";
		});
	}
	async function W() {
		let e = new Set(l.fields.map(H));
		for (let [t, n] of C) if (!e.has(t)) {
			for (let e of [n.clientId, n.clientSecret]) e && (e.value = "");
			C.delete(t);
		}
		for (let e of l.fields) {
			let t = H(e), n = JSON.stringify(e), r = C.get(t);
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
				}, r.onSave = () => B(r), C.set(t, r);
			}
			let i = u[e.module_id].fields[e.name];
			r.detail = i === "unset" ? "未配置" : i === "configured" ? "已保存 · 尚未验证上游" : "已配置但暂不可用";
		}
		y.credentials([...C.values()], j), await r(), j();
	}
	async function G() {
		let e = [...new Set(s.fields.map((e) => e.module_id))], t = new Set(s.fields.map(H));
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
				discard: null,
				onSave: () => F(t),
				onDiscard: () => I(t)
			}, b.set(t, e)), e.revision = c[t] ? `配置版本：${c[t].revision}` : "仅声明目录；未读取该模块存量值。", e.controls = [];
			for (let r of U(s, t)) {
				let i = H(r), a = JSON.stringify(r), o = x.get(i);
				(!o || o.signature !== a) && (o = P(r), x.set(i, o));
				let s = r.readable ? c[t].fields[r.name] : null, l = [
					`字段：${r.name}`,
					r.group ? `分组：${r.group}` : "未分组",
					r.required ? "必需配置" : "可选配置",
					`声明默认值：${JSON.stringify(r.default)}`
				];
				s ? l.push(s.state === "valid" ? "有效" : "无效存量，原值不显示", s.present ? "已有显式值" : "未设置", s.source === "sqlite" ? "Core 配置" : "默认值") : l.push("无存量读取与修改授权"), r.blocked_reason === "semantic_validator_unavailable" && l.push("模块语义校验器不可用，禁止修改与清除"), Y(r) || l.push("此 schema 暂不支持编辑，仅显示声明与已授权值"), o.detail = l.join(" · "), o.problem = r.readable ? r.editable ? Y(r) ? s?.state === "invalid" ? "当前值无效，请修改或恢复默认。" : "" : "此类型暂不支持编辑。" : "当前不可修改，请核对模块状态。" : "无存量读取与修改授权", Y(r) ? (!o.mode || o.mode.value === "keep") && n.push([
					o,
					s ? N(r, s.value) : "",
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
		y.fields([...b.values()], j), await r();
		for (let [e, t, r, i, a] of n) e.input && e.draftVersion === a && (r === void 0 || e.mode.value === r && e.input.value === i) && (e.input.value = t);
		j();
	}
	async function K(e) {
		p = !0, j();
		let t = await M("catalog", {}, e), n = ee(await M("read", {}, e), t);
		D(e) || V("stale_context"), s = t, c = n;
		let i = "";
		try {
			let t = await M("credential-catalog", {}, e), n = re(await M("credential-status", {}, e), t);
			D(e) || V("stale_context"), l = t, u = n, w = !1;
			try {
				w = (await M("credential-readiness", {}, e)).ready, i = w ? "" : "加密未就绪，暂不能填写凭据。请管理员配置密钥后刷新。";
			} catch {
				D(e) || V("stale_context"), i = "未能确认加密状态，暂不能填写凭据。请刷新核对。";
			}
			w || R();
		} catch (t) {
			if (!D(e)) throw t;
			t?.message === "admin_authorization_denied" && R(), l = null, u = null, i = t?.message === "admin_authorization_denied" ? "来源凭据管理授权不可用；请重新打开宿主管理页面后刷新。" : "来源凭据状态暂不可用；未读取旧值，请刷新核对后再提交。";
		}
		p = !1, await G(), l ? (await W(), y.credentials([...C.values()], j, i), await r(), j()) : y.credentials([...C.values()], j, i), D(e) || V("stale_context");
	}
	async function q(e) {
		if (v || f || !d) return;
		let t = { generation: m };
		v = t, j(), S.textContent = "管理操作正在执行。";
		try {
			await e(t.generation);
		} catch (e) {
			if (!D(t.generation)) return;
			e?.message === "revision_conflict" || e?.message === "配置版本冲突，请刷新并重新核对后修改。" ? (p = !0, S.textContent = "配置已变化，请刷新并重新核对后修改。") : e?.message === "select_changes" ? S.textContent = "请选择需要修改的字段。" : e?.message === "confirm_clear" ? S.textContent = "请勾选清除确认后再提交；保持选项不会写入。" : e?.message === "secret_encryption_unavailable" ? S.textContent = "未保存：宿主缺少或未正确配置加密密钥 YGL_SECRET_KEY；请管理员配置后重试。" : e?.message === "invalid_config" ? S.textContent = "配置或输入未通过校验，请核对字段要求后刷新重试。" : e?.message === "admin_authorization_denied" ? (R(), l = null, u = null, p = !0, S.textContent = "管理授权不可用，请重新打开宿主管理页面后手动刷新。") : (p = !0, S.textContent = "未取得成功确认，请刷新核对配置与版本后重试。");
		} finally {
			v === t && (v = null, j());
		}
	}
	let X = () => Object.fromEntries(Object.entries(c).map(([e, t]) => [e, t.revision]));
	o("refresh").addEventListener("click", () => !k(o("refresh")) && q(async (e) => {
		await K(e), S.textContent = "设置已更新。";
	})), o("rollback").addEventListener("click", () => !k(o("rollback")) && q(async (e) => {
		await M("rollback", { expected_revisions: X() }, e), await K(e), S.textContent = "受审迁移已限定回退，其他数据保留。";
	})), o("recover").addEventListener("click", () => !k(o("recover")) && q(async (e) => {
		await M("recover", {
			expected_revisions: X(),
			complete_from_current: o("replacement").checked
		}, e), await K(e), S.textContent = "配置已重新校验，业务运行已恢复。";
	}));
	function Z(t) {
		if (f) return;
		y.context(t), e.documentElement.dataset.theme = t?.isDark === !0 || t?.theme === "dark" ? "dark" : "light", e.documentElement.lang = typeof t?.locale == "string" ? t.locale : "zh-CN";
		let n;
		try {
			n = ae(t, i);
		} catch {
			R(), g = !0, m++, d = !1, p = !0, v = null, h = null, s = null, c = null, l = null, u = null, y.fields([], j), y.credentials([], j), b.clear(), x.clear(), C.clear(), j(), S.textContent = "宿主管理上下文无效，请重新打开页面。";
			return;
		}
		g = !0, !(d && n === h) && (R(), m++, h = n, d = !0, p = !0, v = null, s = null, c = null, l = null, u = null, y.fields([], j), y.credentials([], j), b.clear(), x.clear(), C.clear(), j(), q(async (e) => {
			await K(e), S.textContent = "设置已更新。";
		}));
	}
	let Q = () => [...x.values()].some((e) => e.mode?.value !== "keep") || [...C.values()].some((e) => e.mode?.value !== "keep" || e.clientId?.value || e.clientSecret?.value);
	function oe() {
		let t = e.activeElement;
		T = a.contains(t) && t?.id ? {
			id: t.id,
			start: t.selectionStart,
			end: t.selectionEnd
		} : null, R(), m++, v = null, p = !0, j();
	}
	function se() {
		!f && d && q(async (e) => {
			if (await K(e), S.textContent = "设置已更新。", T) {
				let e = o(T.id);
				if (e?.focus({ preventScroll: !0 }), e && T.start !== null && e.setSelectionRange) try {
					e.setSelectionRange(T.start, T.end);
				} catch {}
				T = null;
			}
		});
	}
	function $() {
		f || (R(), f = !0, m++, d = !1, v = null, s = null, c = null, l = null, u = null, _?.(), j());
	}
	return {
		start() {
			if (!t || typeof t.apiPost != "function" || typeof t.ready != "function" || typeof t.onContext != "function") {
				S.textContent = "请从宿主插件管理页面打开此页。";
				return;
			}
			_ = t.onContext(Z), Promise.resolve(t.ready()).then((e) => {
				g || Z(e);
			}).catch(() => {
				!g && !f && (d = !1, S.textContent = "宿主管理会话不可用。", j());
			}), n?.addEventListener("pagehide", $, { once: !0 });
		},
		close: $,
		dirty: Q,
		suspend: oe,
		resume: se,
		selectOwner(e) {
			E = e, y.owner(e), j();
		},
		dispose() {
			$(), n?.removeEventListener("pagehide", $), y.dispose();
		}
	};
}
typeof document < "u" && document.getElementById("management-root") && Q(document, window.AstrBotPluginPage, window).start();
//#endregion
//#region src/confirmation.ts
function oe(e) {
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
function se(r, i, a, o, f, m = () => !0, h, v = () => "", y) {
	let b = s([]), x = s("尚未读取模块管理状态。"), S = s(!1), C = s(!0), w = h ?? oe(r.ownerDocument), T = s(null), E = s({}), D = s(/* @__PURE__ */ new Map()), O = s({}), k = !1, j = !1, M = 0, N = "", P = 0, F = 0, I, L = (e) => typeof e == "object" && !!e && !Array.isArray(e), R = (e) => !k && j && e === M;
	function z(e) {
		if (!L(e) || Object.keys(e).sort().join(",") !== "modules,registry_revision" || !Number.isSafeInteger(e.registry_revision) || !Array.isArray(e.modules) || e.modules.length > 128) throw Error("invalid_modules");
		let t = /* @__PURE__ */ new Set();
		for (let n of e.modules) {
			if (!L(n) || Object.keys(n).sort().join(",") !== "enabled,epoch,health,lifecycle,module_id,reason_code,registry_revision" || typeof n.module_id != "string" || !/^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*\/[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$/.test(n.module_id) || t.has(n.module_id) || typeof n.enabled != "boolean" || !Number.isSafeInteger(n.epoch) || !Number.isSafeInteger(n.registry_revision) || typeof n.lifecycle != "string" || typeof n.health != "string" || n.reason_code !== null && typeof n.reason_code != "string") throw Error("invalid_modules");
			t.add(n.module_id);
		}
		return e;
	}
	async function B() {
		if (!j || k || S.value) return;
		w.cancel(), T.value = null;
		let e = M;
		S.value = !0, C.value = !0;
		try {
			let t = z(await i.apiPost("admin/modules", {}));
			if (!R(e)) return;
			if (b.value = t.modules, P = t.registry_revision, C.value = !1, x.value = "状态已更新。", y) {
				O.value = {};
				try {
					let n = y.catalog(await i.apiPost("admin/catalog", {})), r = y.snapshot(await i.apiPost("admin/read", {}), n);
					if (!R(e)) return;
					let a = {};
					for (let e of t.modules) {
						let t = n.fields.filter((t) => t.module_id === e.module_id), i = r[e.module_id];
						a[e.module_id] = t.length && i && t.every((e) => e.readable && e.editable && i.fields[e.name]?.state === "valid") ? "普通设置已校验" : i ? "普通设置待处理" : "普通设置未核对";
					}
					O.value = a;
				} catch {
					if (!R(e)) return;
					O.value = {};
				}
				D.value = y.labels();
			}
		} catch {
			R(e) && (C.value = !0, x.value = "模块管理权限或状态无法确认，请重新打开页面后刷新。");
		} finally {
			R(e) && (S.value = !1);
		}
	}
	async function V(e, t) {
		if (!R(M) || S.value || C.value || T.value || !b.value.includes(e)) return;
		let n = M, r = P, a = v(), o = ++F;
		T.value = o;
		let s = () => R(n) && !S.value && !C.value && P === r && v() === a && b.value.includes(e), c = () => T.value === o && s();
		try {
			if (!await m({
				owner: e.module_id,
				action: t,
				revision: r,
				isCurrent: c,
				canRestoreFocus: s
			}) || !c() || t === "unload" && !await w.ask({
				kind: "unload",
				owner: e.module_id,
				title: `确认解除挂载 ${e.module_id}`,
				message: `配置、凭据、缓存及业务数据全部保留。注册版本 ${r}；确认后需从宿主插件详情正式重开页面。`,
				confirmLabel: "确认解除挂载（保留数据）",
				isCurrent: c,
				canRestoreFocus: s
			}) || !c()) return;
		} finally {
			T.value === o && (T.value = null);
		}
		if (R(n) && !S.value && !C.value && P === r && v() === a && b.value.includes(e)) {
			S.value = !0, C.value = !0;
			try {
				let a = {
					module_id: e.module_id,
					expected_registry_revision: r,
					...t === "unload" ? {} : { enabled: t === "enable" }
				}, o = await i.apiPost(t === "unload" ? "admin/module-unload" : "admin/module-enabled", a);
				if (!R(n)) return;
				if (!L(o) || o.module_id !== e.module_id || !Number.isSafeInteger(o.registry_revision) || t === "unload" && (o.state !== "unloaded" || o.data_retained !== !0 || o.reopen_required !== !0)) throw Error("operation_unavailable");
				if (S.value = !1, await B(), !R(n)) return;
				f(), R(n) && (x.value = t === "enable" ? "已恢复模块。请从宿主插件详情重新打开页面，以取得新的模块资源授权。" : t === "unload" ? "模块已解除挂载，持久数据保留。恢复后请从宿主插件详情正式重开页面。" : "模块已禁用，设置与持久数据保留。");
			} catch (e) {
				R(n) && (x.value = e?.message === "revision_conflict" ? "模块状态已变化，请刷新后重新核对。" : "未确认清理完成；可能仍在排空或清理。请刷新核对，并重试解除挂载。不会创建替代实例。", C.value = !0);
			} finally {
				R(n) && (S.value = !1);
			}
		}
	}
	let H = e(t({ setup() {
		return () => n(d, {
			preflightStyleDisabled: !0,
			theme: E.value.isDark === !0 || E.value.theme === "dark" ? p : null,
			locale: E.value.locale === "en-US" ? g : _,
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
				disabled: S.value || !j,
				onClick: B
			}, () => S.value ? "正在处理…" : "刷新模块管理状态"),
			!b.value.length && !C.value ? n("p", "尚无已注册模块。全局设置仍可使用。") : null,
			...b.value.map((e) => n("section", {
				class: "module-management-row",
				key: e.module_id,
				"data-module-owner": e.module_id
			}, [
				n("h3", D.value.get(e.module_id) || e.module_id),
				n("p", { class: "module-status" }, `${e.enabled ? "已启用" : "已禁用"} · ${{
					active: "运行中",
					stopped: "已停止",
					failed: "运行失败",
					unloaded: "已解除挂载",
					discovered: "待启动",
					starting: "启动中",
					stopping: "停止中"
				}[e.lifecycle] || e.lifecycle}`),
				y ? n("p", { class: "config-readiness" }, O.value[e.module_id] || "普通设置未核对") : null,
				e.reason_code ? n("p", { role: "status" }, "需要处理：" + e.reason_code) : null,
				y ? n(l, { onClick: () => y.onSettings(e.module_id) }, () => "进入设置") : null,
				...[
					"enable",
					"disable",
					"unload"
				].map((t) => n(l, {
					disabled: S.value || T.value !== null || C.value || !j || t === "enable" && e.enabled || t === "disable" && !e.enabled,
					onClick: () => V(e, t)
				}, () => ({
					enable: "启用 / 恢复",
					disable: "禁用",
					unload: "解除挂载（保留数据）"
				})[t]))
			])),
			h ? null : w.render(),
			n("details", { class: "technical-details" }, [
				n("summary", "高级详情与操作说明"),
				n("p", "禁用仍可配置；解除挂载退出活动入口并保留配置、凭据、缓存及业务数据。恢复后须正式重开。"),
				...b.value.map((e) => n("p", `${e.module_id} · ${e.lifecycle} · ${e.health} · 注册版本 ${P}`)),
				n("p", { "data-reopen-guidance": "" }, "请在宿主左侧点击“插件”，找到 astrbot_plugin_yomihime_game_link，在插件详情中重新打开 monitor 页面，以取得当前资源授权。")
			])
		] }) });
	} }));
	H.mount(r);
	function U(e) {
		if (k) return;
		E.value = L(e) ? e : {};
		let t = "";
		try {
			if (!L(e) || e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== o) throw Error();
			t = A(e);
		} catch {
			w.cancel(), T.value = null, j = !1, M++, b.value = [], S.value = !1, C.value = !0, x.value = "管理上下文无效，请正式重开页面。";
			return;
		}
		j && t === N || (w.cancel(), T.value = null, M++, N = t, j = !0, b.value = [], S.value = !1, B());
	}
	return {
		start() {
			I = i.onContext(U), Promise.resolve(i.ready()).then(U).catch(() => {
				x.value = "宿主会话不可用，请正式重开页面。";
			});
		},
		refresh: B,
		presentationLabels(e) {
			D.value = e;
		},
		cancelConfirmation() {
			w.cancel(), T.value = null;
		},
		dispose() {
			w.cancel(), T.value = null, h || w.dispose(), k = !0, M++, j = !1, I?.(), H.unmount();
		}
	};
}
//#endregion
//#region src/main.ts
var $ = t({ setup() {
	let e = o(0), t = o(null), r = o(null), s = o(null), c = o(null), u = null, y = null, b = null, x = !1, S = null, C = document.documentElement.dataset.pageName || "shell", T = oe(document), E = window.location.hash, D = "", O = 0, k = "", j = !1, M = 0, N = 0, I = !1, L, R = () => JSON.stringify([
		O,
		k,
		j,
		M,
		u?.state.home,
		u?.state.settings,
		u?.state.settingsOwner,
		u?.state.selected,
		u?.state.catalog?.catalog_revision,
		u?.state.busy,
		u?.state.stale,
		window.location.hash
	]), z = async (e) => {
		let t = ++N, n = R();
		T.cancel();
		let r = () => !I && j && t === N && n === R();
		u?.state.settings && y?.dirty() && !await T.ask({
			kind: "navigation",
			title: "离开当前设置？",
			message: "保留普通配置草稿并离开设置？取消可继续编辑。秘密输入将清空。",
			confirmLabel: "保留普通草稿并离开",
			isCurrent: r
		}) || r() && (e(), E = window.location.hash);
	}, B = (e) => {
		let t = window.location.hash;
		if (t === D) {
			D = "", E = t;
			return;
		}
		if (t !== E) {
			if (x && y?.dirty()) {
				e.stopImmediatePropagation(), window.history.replaceState(null, "", E || "#/"), z(() => {
					D = t, window.location.hash = t;
				});
				return;
			}
			T.cancel(), E = t;
		}
	}, V = (e) => {
		s.value?.contains(e.target) && (M++, T.cancel(), b?.cancelConfirmation());
	};
	function H() {
		T.invalidate(), e.value++;
		let t = u?.state.settings || !1;
		u?.state.catalog && (t || u.state.home) && !b && c.value && (b = se(c.value, window.AstrBotPluginPage, window, C, () => {
			u?.refresh(), y?.resume();
		}, (e) => {
			let t = R();
			return !y?.dirty() || T.ask({
				kind: "module-draft",
				owner: e.owner,
				title: "确认模块操作 " + e.owner,
				message: `存在未提交的配置草稿。模块状态变化可能撤销其字段，继续执行？取消可先保存草稿。注册版本 ${e.revision}。`,
				confirmLabel: "保留草稿并继续核对",
				isCurrent: () => t === R() && e.isCurrent(),
				canRestoreFocus: () => t === R() && e.canRestoreFocus()
			});
		}, T, R, {
			onSettings: (e) => void z(() => u?.selectSettings(e)),
			labels: () => w(u?.state.catalog?.modules || [], u?.state.locale || "zh-CN"),
			catalog: J,
			snapshot: ee
		}), b.start()), b?.presentationLabels(w(u?.state.catalog?.modules || [], u?.state.locale || "zh-CN")), t && !y && s.value && c.value ? (y = Q(document, window.AstrBotPluginPage, window, {
			expectedPage: C,
			container: s.value
		}), y.start()) : t && !x ? (y?.resume(), b?.refresh()) : t && x && S !== u?.state.settingsOwner ? (y?.suspend(), y?.resume()) : !t && x && y?.suspend(), S = u?.state.settingsOwner || null, t && y?.selectOwner(u?.state.settingsOwner || "game_link/core"), x = t;
	}
	return a(() => {
		document.addEventListener("input", V), document.addEventListener("change", V), L = window.AstrBotPluginPage?.onContext((e) => {
			let t = "";
			try {
				if (e.pluginName !== "astrbot_plugin_yomihime_game_link" || e.pageName !== C) throw Error();
				t = A(e);
			} catch {
				j = !1, O++, T.cancel(), b?.cancelConfirmation();
				return;
			}
			t !== k && (O++, k = t, T.cancel(), b?.cancelConfirmation()), j = !0;
		}), window.addEventListener("hashchange", B), u = P({
			bridge: window.AstrBotPluginPage,
			container: t.value,
			window,
			loadPage: v,
			expectedPage: document.documentElement.dataset.pageName || "shell",
			changed: H
		}), u.start();
	}), i(() => {
		I = !0, O++, T.dispose(), L?.(), document.removeEventListener("input", V), document.removeEventListener("change", V), window.removeEventListener("hashchange", B), y?.dispose(), b?.dispose(), u?.dispose();
	}), () => {
		e.value;
		let i = u?.state, a = u?.selected(), o = i?.catalog?.modules || [], v = w(o, i?.locale || "zh-CN"), y = (e) => v.get(e) || e, x = o.flatMap((e) => e.pages.map((t) => ({
			module: e,
			page: t
		}))), S = i?.busy || !1, C = i?.home ?? !0, E = o.find((e) => e.module_id === (i?.settingsOwner || i?.selected?.owner)), D = (e) => (t) => {
			t.preventDefault(), z(e);
		}, O = (e, t, r, i) => n("a", {
			href: t,
			"aria-current": r ? "page" : void 0,
			onClick: D(i)
		}, e), k = (e) => n("div", { class: "shell-field" }, [n("label", { for: e }, "模块"), n("select", {
			id: e,
			value: E?.module_id || "",
			disabled: !o.length,
			onChange: (e) => {
				let t = e.target.value;
				t && z(() => u?.selectSettings(t));
			}
		}, [n("option", { value: "" }, "选择模块"), ...o.map((e) => n("option", { value: e.module_id }, y(e.module_id)))])]);
		return n(d, {
			preflightStyleDisabled: !0,
			theme: i?.theme === "dark" ? p : null,
			locale: i?.locale.startsWith("en") ? g : _,
			dateLocale: i?.locale.startsWith("en") ? m : h,
			inlineThemeDisabled: !0
		}, { default: () => [n("button", {
			class: "skip-button",
			onClick: () => F(r.value)
		}, "跳至正文"), n("div", {
			class: "game-shell",
			"data-theme": i?.theme || "light"
		}, [n("aside", {
			class: "shell-sidebar",
			"aria-label": "模块导航"
		}, [
			n("p", { class: "shell-brand" }, "游戏连结"),
			n("p", { class: "shell-eyebrow" }, "如月怜"),
			n("nav", { "aria-label": "主要导航" }, [O("首页", "#/", C, () => u?.selectHome())]),
			n("p", { class: "nav-section-label" }, "模块"),
			n("nav", { "aria-label": "模块设置" }, o.map((e) => O(y(e.module_id), "#/settings/module/" + encodeURIComponent(e.module_id), !!(i?.settings && i.settingsOwner === e.module_id), () => u?.selectSettings(e.module_id)))),
			n("details", { class: "query-navigation" }, [n("summary", "辅助查询"), n("nav", { "aria-label": "模块页面" }, x.map(({ module: e, page: t }) => O(y(e.module_id) + " · " + t.title, `#/module/${encodeURIComponent(e.module_id)}/${t.route_id}`, i?.selected?.owner === e.module_id && i.selected.route === t.route_id, () => u?.select(e.module_id, t.route_id))))]),
			n("div", { class: "shell-settings-slot" }, [n("button", {
				class: "shell-settings",
				type: "button",
				"aria-current": i?.settings && i.settingsOwner === null ? "page" : void 0,
				onClick: () => void z(() => u?.selectSettings())
			}, "全局设置")])
		]), n("div", { class: "shell-content" }, [n("div", { class: "shell-mobile" }, [
			n("p", { class: "shell-brand" }, "游戏连结"),
			n("nav", { "aria-label": "移动导航" }, [O("首页", "#/", C, () => u?.selectHome()), O("全局设置", "#/settings", !!(i?.settings && i.settingsOwner === null), () => u?.selectSettings())]),
			k("module-select-mobile"),
			n("details", [n("summary", "辅助查询"), ...x.map(({ module: e, page: t }) => O(y(e.module_id) + " · " + t.title, `#/module/${encodeURIComponent(e.module_id)}/${t.route_id}`, i?.selected?.owner === e.module_id && i.selected.route === t.route_id, () => u?.select(e.module_id, t.route_id)))])
		]), n("main", {
			ref: r,
			id: "page-root",
			tabindex: -1,
			"aria-labelledby": "page-title"
		}, [
			n("header", { class: "shell-heading" }, [n("div", [n("p", { class: "shell-eyebrow" }, i?.settings ? i.settingsOwner ? y(i.settingsOwner) : "全局" : a ? y(a.module.module_id) : "管理概览"), n("h1", { id: "page-title" }, i?.settings ? "设置" : C ? "首页" : a?.page.title || "页面不可用")]), n(l, {
				onClick: () => {
					u?.state.busy || (u?.refresh(), C && b?.refresh());
				},
				"aria-disabled": String(S),
				"aria-busy": String(S),
				"aria-label": "刷新模块状态"
			}, () => S ? "正在刷新…" : "刷新状态")]),
			n("div", {
				class: "shell-status",
				role: "status",
				"aria-live": "polite"
			}, [n(f, {
				type: i?.stale ? "warning" : "default",
				size: "small"
			}, () => i?.stale ? "上次状态" : S ? "读取中" : "状态"), n("span", i?.message || "正在准备页面。")]),
			i?.cleanupPending ? n(l, { onClick: () => u?.retryCleanup() }, () => "重试页面清理") : null,
			T.render(),
			n("div", {
				ref: t,
				id: "module-container",
				hidden: i?.settings || C
			}),
			n("div", {
				class: "settings-container",
				hidden: !i?.settings
			}, [n("div", {
				ref: s,
				id: "settings-container"
			})]),
			n("details", {
				class: "module-dashboard",
				open: C,
				hidden: !C && !i?.settings
			}, [n("summary", C ? "模块" : "高级模块管理"), n("div", {
				ref: c,
				id: "module-management-container"
			})]),
			!C && !i?.settings && !i?.mounted ? n("p", { class: "shell-empty" }, i?.cleanupPending ? "页面清理未完成，请重新打开页面。" : a ? "模块页面正在准备。" : "此路由当前没有可用的注册页面。") : null
		])])])] });
	};
} }), ce = e($);
ce.mount("#app"), window.addEventListener("pagehide", () => ce.unmount(), { once: !0 });
//#endregion
