import { createApp as e, defineComponent as t, h as n, onBeforeUnmount as r, onMounted as i, ref as a } from "./runtime.js";
import { NButton as o, NConfigProvider as s, NTag as c, darkTheme as l, dateEnUS as u, dateZhCN as d, enUS as f, zhCN as p } from "./runtime.js";
import { loadPage as m } from "./module-loader.js";
//#region src/catalog.ts
var h = (e) => typeof e == "object" && !!e && !Array.isArray(e), g = (e, t = 4096) => typeof e == "string" && e.length > 0 && e.length <= t, _ = (e) => g(e) && /^module-assets\/[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*\/pages\/(?:[a-zA-Z0-9_.-]+\/)*[a-zA-Z0-9_.-]+$/.test(e) && !e.split("/").some((e) => e === "." || e === "..");
function v(e) {
	let t = () => {
		throw Error("invalid_catalog");
	};
	(!h(e) || e.schema_version !== 1 || !h(e.runtime) || ![
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
		(!h(e) || !g(e.module_id) || !/^[a-z][a-z0-9_.-]*\/[a-z][a-z0-9_.-]*$/.test(e.module_id) || !g(e.route) || ![
			"loaded",
			"disabled",
			"unavailable"
		].includes(String(e.state)) || !Number.isSafeInteger(e.module_epoch) || Number(e.module_epoch) < 0 || !Array.isArray(e.pages) || e.pages.length > 64 || !Array.isArray(e.resources) || e.resources.length > 128) && t();
		let r = e;
		(r.state === "loaded" ? !g(r.runtime_id) || r.enabled !== !0 || r.lifecycle !== "active" || n.runtime.state !== "ready" : r.runtime_id !== null || r.pages.length || r.resources.length) && t(), (!g(r.asset_version, 64) || !/^[a-f0-9]{64}$/.test(r.asset_version)) && t();
		let i = `module-assets/${r.module_id}/`, a = r.resources.map((e) => ((!h(e) || !_(e.path) || !e.path.startsWith(i) || !g(e.sha256, 64) || !/^[a-f0-9]{64}$/.test(e.sha256)) && t(), {
			path: e.path,
			sha256: e.sha256
		}));
		new Set(a.map((e) => e.path)).size !== a.length && t();
		let o = r.pages.map((e) => ((!h(e) || !g(e.route_id, 128) || !/^[a-z][a-z0-9_.-]*$/.test(e.route_id) || !g(e.title, 256) || !Number.isSafeInteger(e.order) || e.access !== "public_web" || !(e.capability_id === null || g(e.capability_id, 128)) || !_(e.entry) || !e.entry.startsWith(i) || !e.entry.endsWith(".js") || !Array.isArray(e.styles) || e.styles.length > 16 || !e.styles.every((e) => _(e) && e.startsWith(i) && e.endsWith(".css"))) && t(), [e.entry, ...e.styles].every((e) => a.some((t) => t.path === e)) || t(), {
			route_id: e.route_id,
			title: e.title,
			order: e.order,
			access: "public_web",
			capability_id: e.capability_id,
			entry: e.entry,
			styles: [...e.styles]
		}));
		return new Set(o.map((e) => e.route_id)).size !== o.length && t(), {
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
function y(e, t) {
	return `#/module/${encodeURIComponent(e)}/${encodeURIComponent(t)}`;
}
function b(e) {
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
function x(e) {
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
var S = /* @__PURE__ */ new Set([
	"isDark",
	"theme",
	"locale",
	"i18n",
	"displayName",
	"pageTitle"
]);
function C(e) {
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
	return JSON.stringify(n(Object.fromEntries(Object.entries(e).filter(([e]) => !S.has(e)))));
}
function w(e, t, n) {
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
function T(e, t) {
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
function E(e, t, n) {
	let r = /* @__PURE__ */ new Map();
	function i(e) {
		let t = r.get(e);
		if (!t) {
			let n = {
				revoked: !1,
				scope: x(() => !n.revoked),
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
function D({ bridge: e, container: t, window: n, loadPage: r, changed: i, timeoutMs: a = 8e3, expectedPlugin: o = "astrbot_plugin_yomihime_game_link", expectedPage: s = "shell" }) {
	let c = {
		catalog: null,
		busy: !1,
		stale: !1,
		message: "正在连接宿主。",
		theme: "light",
		locale: "zh-CN",
		selected: null,
		mounted: !1,
		cleanupPending: !1
	}, l = !1, u = !1, d = 0, f = 0, p = "", m = !1, h, g = null, _ = "", S = "", D = null, O = E(t.ownerDocument, r, a), k = /* @__PURE__ */ new Map(), A = /* @__PURE__ */ new Set(), j = "模块资源已失效。请关闭此页，并从宿主插件详情重新打开页面。", M = !1, N = !1, P = "", F = null, I = /* @__PURE__ */ new Set(), L = (e) => new Promise((t, n) => {
		let r = setTimeout(() => {
			I.delete(r), n(Error("timeout"));
		}, a);
		I.add(r), e.then(t, n).finally(() => {
			clearTimeout(r), I.delete(r);
		});
	});
	function R() {
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
			available: u && !c.busy && !c.stale && !F && !M && !A.has(e.module_id),
			owner: e.module_id,
			runtimeId: e.runtime_id,
			epoch: e.module_epoch,
			boundary: p
		});
	}
	function B() {
		c.cleanupPending = F !== null || O.cleanupPending, c.cleanupPending && (c.message = "页面清理未完成，请重试页面清理。");
	}
	function V(e) {
		A.add(e), P === e && U(), O.invalidate(e), B();
	}
	function H(e) {
		let t = new Map((e.modules || []).filter((e) => e.state === "loaded").map((e) => [e.module_id, T(e, p)]));
		if (N) {
			for (let [e, n] of k) t.get(e) !== n && V(e);
			for (let e of t.keys()) k.has(e) || V(e);
		}
		k.clear();
		for (let [e, n] of t) k.set(e, n);
		N = !0;
	}
	function U() {
		F ||= (f++, S = "", _ = "", c.mounted = !1, {
			scope: D,
			page: g,
			scopeDone: D === null,
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
		e.scopeDone && e.pageDone && e.domDone && (D = null, g = null, F = null, P = ""), B();
	}
	async function W() {
		if (F) {
			i();
			return;
		}
		let n = R();
		if (!n || !u) {
			U(), !c.cleanupPending && (M || A.has(c.selected?.owner || "")) && (c.message = j), i();
			return;
		}
		let { module: r, page: a } = n, o = w(r, a, p);
		if (M || A.has(r.module_id)) {
			U(), c.cleanupPending || (c.message = j), i();
			return;
		}
		if (o === _ && g) {
			g.update(z(r)), i();
			return;
		}
		if (o === S) return;
		if (U(), F) {
			i();
			return;
		}
		S = o, P = r.module_id;
		let s = f, d = x(() => !l && f === s && R() !== null);
		D = d;
		try {
			let n = await L(O.load(r, a));
			if (!d.isCurrent() || S !== o) return;
			if (!n || typeof n.mount != "function") throw Error("invalid_page_entry");
			let s = t.ownerDocument.createElement("div");
			s.className = "module-view", s.dataset.owner = r.module_id, t.append(s);
			let l = Object.freeze({ async invoke(t, n, r) {
				if (!d.isCurrent() || c.stale || c.busy || !u || t !== a.capability_id || r !== `queries/${a.route_id}`) throw Error("page_unavailable");
				let i = await e.apiPost(r, n);
				if (!d.isCurrent() || c.stale || c.busy || !u) throw Error("page_revoked");
				return i;
			} }), f = n.mount(s, Object.freeze({
				routeId: a.route_id,
				context: z(r),
				services: l,
				scope: d
			}));
			if (!f || typeof f.update != "function" || typeof f.dispose != "function") throw Error("invalid_page_instance");
			g = f, _ = o, S = "", c.mounted = !0, i();
		} catch {
			!l && !M && !A.has(r.module_id) && (V(r.module_id), !c.cleanupPending && c.selected?.owner === r.module_id && (c.message = j), i());
		}
	}
	function G() {
		let e = b(n.location.hash);
		if (e) {
			c.selected = e;
			return;
		}
		if (n.location.hash && n.location.hash !== "#/" && n.location.hash !== "#") {
			c.selected = null;
			return;
		}
		let t = c.catalog?.modules?.find((e) => e.state === "loaded" && e.pages.length);
		c.selected = t ? {
			owner: t.module_id,
			route: t.pages[0].route_id
		} : null;
	}
	async function K() {
		if (l || !u || c.busy) return;
		let t = ++d;
		c.busy = !0, c.stale = c.catalog !== null, c.message = c.stale ? "正在刷新；当前为上次目录，暂不可提交查询。" : "正在读取模块目录。";
		let n = R();
		n && g && g.update(z(n.module)), i();
		try {
			let n = v(await L(e.apiGet("catalog", {})));
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
		if (l || M || !t && m) return;
		c.theme = typeof e?.isDark == "boolean" ? e.isDark ? "dark" : "light" : e?.theme === "dark" ? "dark" : "light", c.locale = typeof e?.locale == "string" ? e.locale : "zh-CN";
		let n = () => {
			M = !0, d++, u = !1, c.busy = !1, c.stale = c.catalog !== null, U(), O.invalidateAll(), B(), c.cleanupPending || (c.message = j), i();
		}, r;
		try {
			if (r = C(e), !Object.hasOwn(e, "pluginName") || !Object.hasOwn(e, "pageName") || e.pluginName !== o || e.pageName !== s) throw Error("wrong_page");
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
		select(e, t) {
			n.location.hash = y(e, t), c.selected = {
				owner: e,
				route: t
			}, W();
		},
		retryCleanup() {
			c.cleanupPending && (F && U(), O.retryCleanup(), B(), c.cleanupPending || (c.message = M || A.has(c.selected?.owner || "") ? j : "页面清理完成，请手动刷新状态。", l || W()), i());
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
				I.clear(), U(), O.invalidateAll(), B();
			}
		}
	};
}
//#endregion
//#region src/navigation.ts
function O(e) {
	e && (e.focus({ preventScroll: !0 }), e.scrollIntoView({
		block: "start",
		inline: "nearest",
		behavior: "instant"
	}));
}
//#endregion
//#region src/main.ts
var k = t({ setup() {
	let e = a(0), t = a(null), h = a(null), g = null;
	return i(() => {
		g = D({
			bridge: window.AstrBotPluginPage,
			container: t.value,
			window,
			loadPage: m,
			expectedPage: document.documentElement.dataset.pageName || "shell",
			changed: () => {
				e.value++;
			}
		}), g.start();
	}), r(() => g?.dispose()), () => {
		e.value;
		let r = g?.state, i = g?.selected(), a = r?.catalog?.modules || [], m = a.flatMap((e) => e.pages.map((t) => ({
			module: e,
			page: t
		}))), _ = r?.busy || !1, v = (e) => {
			let t = a.find((t) => t.module_id === e.target.value);
			t?.pages[0] && g.select(t.module_id, t.pages[0].route_id);
		}, y = (e) => n("div", { class: "shell-field" }, [n("label", { for: e }, "模块"), n("select", {
			id: e,
			value: r?.selected?.owner || "",
			disabled: !a.some((e) => e.pages.length),
			onChange: v
		}, [r?.selected ? null : n("option", { value: "" }, "请选择模块"), ...a.map((e) => n("option", {
			value: e.module_id,
			disabled: !e.pages.length
		}, e.module_id))])]), b = n("div", { class: "shell-field" }, [n("label", { for: "page-select" }, "页面"), n("select", {
			id: "page-select",
			value: r?.selected?.route || "",
			disabled: !i,
			onChange: (e) => g.select(r.selected.owner, e.target.value)
		}, i?.module.pages.map((e) => n("option", { value: e.route_id }, e.title)) || [n("option", { value: "" }, "请先选择模块")])]);
		return n(s, {
			theme: r?.theme === "dark" ? l : null,
			locale: r?.locale.startsWith("en") ? f : p,
			dateLocale: r?.locale.startsWith("en") ? u : d,
			inlineThemeDisabled: !0
		}, { default: () => [n("button", {
			class: "skip-button",
			onClick: () => O(h.value)
		}, "跳至正文"), n("div", {
			class: "game-shell",
			"data-theme": r?.theme || "light"
		}, [n("aside", {
			class: "shell-sidebar",
			"aria-label": "模块导航"
		}, [
			n("p", { class: "shell-brand" }, "如月怜的游戏连结"),
			n("p", { class: "shell-eyebrow" }, "注册目录 · 公开查询"),
			y("module-select"),
			n("nav", { "aria-label": "模块页面" }, m.map(({ module: e, page: t }) => n("a", {
				href: `#/module/${encodeURIComponent(e.module_id)}/${t.route_id}`,
				"aria-current": r?.selected?.owner === e.module_id && r.selected.route === t.route_id ? "page" : void 0
			}, t.title))),
			n("p", { class: "shell-note" }, "此处为公开查询入口。配置管理使用独立授权页面。")
		]), n("div", { class: "shell-content" }, [
			n("div", { class: "shell-mobile" }, [
				n("p", { class: "shell-brand" }, "游戏连结"),
				y("module-select-mobile"),
				b
			]),
			n("main", {
				ref: h,
				id: "page-root",
				tabindex: -1,
				"aria-labelledby": "page-title"
			}, [
				n("header", { class: "shell-heading" }, [n("div", [n("p", { class: "shell-eyebrow" }, i?.module.module_id || "模块目录"), n("h1", { id: "page-title" }, i?.page.title || "游戏连结")]), n(o, {
					onClick: () => g?.refresh(),
					disabled: _,
					"aria-label": "刷新模块状态"
				}, () => _ ? "正在刷新…" : "刷新状态")]),
				n("div", {
					class: "shell-status",
					role: "status",
					"aria-live": "polite"
				}, [n(c, {
					type: r?.stale ? "warning" : "default",
					size: "small"
				}, () => r?.stale ? "旧状态" : _ ? "读取中" : "只读"), n("span", r?.message || "正在准备页面。")]),
				r?.cleanupPending ? n(o, { onClick: () => g?.retryCleanup() }, () => "重试页面清理") : null,
				n("div", {
					ref: t,
					id: "module-container"
				}),
				r?.mounted ? null : n("p", { class: "shell-empty" }, r?.cleanupPending ? "页面清理未完成，请重新打开页面。" : i ? "模块页面正在准备。" : "此路由当前没有可用的注册页面。")
			]),
			n("footer", "只读目录与手动查询 · 来源与缺项以本次结果为准")
		])])] });
	};
} }), A = e(k);
A.mount("#app"), window.addEventListener("pagehide", () => A.unmount(), { once: !0 });
//#endregion
