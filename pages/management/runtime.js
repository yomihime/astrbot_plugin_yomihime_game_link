//#region node_modules/@vue/shared/dist/shared.esm-bundler.js
// @__NO_SIDE_EFFECTS__
function e(e) {
	let t = /* @__PURE__ */ Object.create(null);
	for (let n of e.split(",")) t[n] = 1;
	return (e) => e in t;
}
var t = {}, n = [], r = () => {}, i = () => !1, a = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && (e.charCodeAt(2) > 122 || e.charCodeAt(2) < 97), o = (e) => e.startsWith("onUpdate:"), s = Object.assign, c = (e, t) => {
	let n = e.indexOf(t);
	n > -1 && e.splice(n, 1);
}, l = Object.prototype.hasOwnProperty, u = (e, t) => l.call(e, t), d = Array.isArray, f = (e) => x(e) === "[object Map]", p = (e) => x(e) === "[object Set]", m = (e) => x(e) === "[object Date]", h = (e) => typeof e == "function", g = (e) => typeof e == "string", _ = (e) => typeof e == "symbol", v = (e) => typeof e == "object" && !!e, y = (e) => (v(e) || h(e)) && h(e.then) && h(e.catch), b = Object.prototype.toString, x = (e) => b.call(e), S = (e) => x(e).slice(8, -1), C = (e) => x(e) === "[object Object]", w = (e) => g(e) && e !== "NaN" && e[0] !== "-" && "" + parseInt(e, 10) === e, T = /* @__PURE__ */ e(",key,ref,ref_for,ref_key,onVnodeBeforeMount,onVnodeMounted,onVnodeBeforeUpdate,onVnodeUpdated,onVnodeBeforeUnmount,onVnodeUnmounted"), E = (e) => {
	let t = /* @__PURE__ */ Object.create(null);
	return ((n) => t[n] || (t[n] = e(n)));
}, D = /-\w/g, O = E((e) => e.replace(D, (e) => e.slice(1).toUpperCase())), ee = /\B([A-Z])/g, te = E((e) => e.replace(ee, "-$1").toLowerCase()), ne = E((e) => e.charAt(0).toUpperCase() + e.slice(1)), re = E((e) => e ? `on${ne(e)}` : ""), ie = (e, t) => !Object.is(e, t), ae = (e, ...t) => {
	for (let n = 0; n < e.length; n++) e[n](...t);
}, oe = (e, t, n, r = !1) => {
	Object.defineProperty(e, t, {
		configurable: !0,
		enumerable: !1,
		writable: r,
		value: n
	});
}, se = (e) => {
	let t = parseFloat(e);
	return isNaN(t) ? e : t;
}, ce = (e) => {
	let t = g(e) ? Number(e) : NaN;
	return isNaN(t) ? e : t;
}, le, ue = () => le ||= typeof globalThis < "u" ? globalThis : typeof self < "u" ? self : typeof window < "u" ? window : typeof global < "u" ? global : {};
function k(e) {
	if (d(e)) {
		let t = {};
		for (let n = 0; n < e.length; n++) {
			let r = e[n], i = g(r) ? me(r) : k(r);
			if (i) for (let e in i) t[e] = i[e];
		}
		return t;
	}
	if (g(e) || v(e)) return e;
}
var de = /;(?![^(]*\))/g, fe = /:([^]+)/, pe = /"(?:[^"\\]|\\[^])*"|'(?:[^'\\]|\\[^])*'|\\[^]|\/\*[^]*?\*\//g;
function me(e) {
	let t = {};
	return e.replace(pe, (e) => e.startsWith("/*") ? "" : e).split(de).forEach((e) => {
		if (e) {
			let n = e.split(fe);
			n.length > 1 && (t[n[0].trim()] = n[1].trim());
		}
	}), t;
}
function he(e) {
	let t = "";
	if (g(e)) t = e;
	else if (d(e)) for (let n = 0; n < e.length; n++) {
		let r = he(e[n]);
		r && (t += r + " ");
	}
	else if (v(e)) for (let n in e) e[n] && (t += n + " ");
	return t.trim();
}
var ge = "itemscope,allowfullscreen,formnovalidate,ismap,nomodule,novalidate,readonly", _e = /* @__PURE__ */ e(ge);
ge + "";
function ve(e) {
	return !!e || e === "";
}
function ye(e, t, n) {
	if (e.length !== t.length) return !1;
	let r = !0;
	for (let i = 0; r && i < e.length; i++) r = Ce(e[i], t[i], n);
	return r;
}
function be(e, t, n) {
	if (e.size !== t.size) return !1;
	let r = Array.from(t), i = new Uint8Array(r.length);
	for (let t of e) {
		let e = -1;
		for (let a = 0; a < r.length; a++) if (!i[a] && Ce(t, r[a], n)) {
			e = a;
			break;
		}
		if (e < 0) return !1;
		i[e] = 1;
	}
	return !0;
}
function xe(e, t, n) {
	let r = f(e), i = f(t);
	if (r || i || (r = p(e), i = p(t), r || i)) return r && i ? be(e, t, n) : !1;
	if (Object.keys(e).length !== Object.keys(t).length) return !1;
	for (let r in e) {
		let i = e.hasOwnProperty(r), a = t.hasOwnProperty(r);
		if (i && !a || !i && a || !Ce(e[r], t[r], n)) return !1;
	}
	return String(e) === String(t);
}
function Se(e, t, n, r) {
	n ||= [/* @__PURE__ */ new Map(), /* @__PURE__ */ new Map()];
	let [i, a] = n;
	if (i.has(e) || a.has(t)) return i.get(e) === t && a.get(t) === e;
	i.set(e, t), a.set(t, e);
	let o = r(e, t, n);
	return i.delete(e), a.delete(t), o;
}
function Ce(e, t, n) {
	if (e === t) return !0;
	let r = m(e), i = m(t);
	return r || i ? r && i ? e.getTime() === t.getTime() : !1 : (r = _(e), i = _(t), r || i ? e === t : (r = d(e), i = d(t), r || i ? r && i ? Se(e, t, n, ye) : !1 : (r = v(e), i = v(t), r || i ? !r || !i ? !1 : Se(e, t, n, xe) : String(e) === String(t))));
}
//#endregion
//#region node_modules/@vue/reactivity/dist/reactivity.esm-bundler.js
var we, Te = class {
	constructor(e = !1) {
		this.detached = e, this._active = !0, this._on = 0, this.effects = [], this.cleanups = [], this._isPaused = !1, this._warnOnRun = !0, this.__v_skip = !0, !e && we && (we.active ? (this.parent = we, this.index = (we.scopes || (we.scopes = [])).push(this) - 1) : (this._active = !1, this._warnOnRun = !1));
	}
	get active() {
		return this._active;
	}
	pause() {
		if (this._active) {
			this._isPaused = !0;
			let e, t;
			if (this.scopes) {
				let n = this.scopes.slice();
				for (e = 0, t = n.length; e < t; e++) n[e].pause();
			}
			for (e = 0, t = this.effects.length; e < t; e++) this.effects[e].pause();
		}
	}
	resume() {
		if (this._active && this._isPaused) {
			this._isPaused = !1;
			let e, t;
			if (this.scopes) {
				let n = this.scopes.slice();
				for (e = 0, t = n.length; e < t; e++) n[e].resume();
			}
			let n = this.effects.slice();
			for (e = 0, t = n.length; e < t; e++) n[e].resume();
		}
	}
	run(e) {
		if (this._active) {
			let t = we;
			try {
				return we = this, e();
			} finally {
				we = t;
			}
		}
	}
	on() {
		++this._on === 1 && (this.prevScope = we, we = this);
	}
	off() {
		if (this._on > 0 && --this._on === 0) {
			if (we === this) we = this.prevScope;
			else {
				let e = we;
				for (; e;) {
					if (e.prevScope === this) {
						e.prevScope = this.prevScope;
						break;
					}
					e = e.prevScope;
				}
			}
			this.prevScope = void 0;
		}
	}
	stop(e) {
		if (this._active) {
			this._active = !1;
			let t, n;
			for (t = 0, n = this.effects.length; t < n; t++) this.effects[t].stop();
			for (this.effects.length = 0, t = 0, n = this.cleanups.length; t < n; t++) this.cleanups[t]();
			if (this.cleanups.length = 0, this.scopes) {
				let e = this.scopes.slice();
				for (t = 0, n = e.length; t < n; t++) e[t].stop(!0);
				this.scopes.length = 0;
			}
			if (!this.detached && this.parent && !e) {
				let e = this.parent.scopes.pop();
				e && e !== this && (this.parent.scopes[this.index] = e, e.index = this.index);
			}
			this.parent = void 0;
		}
	}
};
function Ee() {
	return we;
}
var De, Oe = /* @__PURE__ */ new WeakSet(), ke = class {
	constructor(e) {
		this.fn = e, this.deps = void 0, this.depsTail = void 0, this.flags = 5, this.next = void 0, this.cleanup = void 0, this.scheduler = void 0, we && (we.active ? we.effects.push(this) : this.flags &= -2);
	}
	pause() {
		this.flags |= 64;
	}
	resume() {
		this.flags & 64 && (this.flags &= -65, Oe.has(this) && (Oe.delete(this), this.trigger()));
	}
	notify() {
		this.flags & 2 && !(this.flags & 32) || this.flags & 8 || Ne(this);
	}
	run() {
		if (!(this.flags & 1)) return this.fn();
		this.flags |= 2, Ke(this), Ie(this);
		let e = De, t = He;
		De = this, He = !0;
		try {
			return this.fn();
		} finally {
			Le(this), De = e, He = t, this.flags &= -3;
		}
	}
	stop() {
		if (this.flags & 1) {
			for (let e = this.deps; e; e = e.nextDep) Be(e);
			this.deps = this.depsTail = void 0, Ke(this), this.onStop && this.onStop(), this.flags &= -2;
		}
	}
	trigger() {
		this.flags & 64 ? Oe.add(this) : this.scheduler ? this.scheduler() : this.runIfDirty();
	}
	runIfDirty() {
		Re(this) && this.run();
	}
	get dirty() {
		return Re(this);
	}
}, Ae = 0, je, Me;
function Ne(e, t = !1) {
	if (e.flags |= 8, t) {
		e.next = Me, Me = e;
		return;
	}
	e.next = je, je = e;
}
function Pe() {
	Ae++;
}
function Fe() {
	if (--Ae > 0) return;
	if (Me) {
		let e = Me;
		for (Me = void 0; e;) {
			let t = e.next;
			e.next = void 0, e.flags &= -9, e = t;
		}
	}
	let e;
	for (; je;) {
		let t = je;
		for (je = void 0; t;) {
			let n = t.next;
			if (t.next = void 0, t.flags &= -9, t.flags & 1) try {
				t.trigger();
			} catch (t) {
				e ||= t;
			}
			t = n;
		}
	}
	if (e) throw e;
}
function Ie(e) {
	for (let t = e.deps; t; t = t.nextDep) t.version = -1, t.prevActiveLink = t.dep.activeLink, t.dep.activeLink = t;
}
function Le(e) {
	let t, n = e.depsTail, r = n;
	for (; r;) {
		let e = r.prevDep;
		r.version === -1 ? (r === n && (n = e), Be(r), Ve(r)) : t = r, r.dep.activeLink = r.prevActiveLink, r.prevActiveLink = void 0, r = e;
	}
	e.deps = t, e.depsTail = n;
}
function Re(e) {
	for (let t = e.deps; t; t = t.nextDep) if (t.dep.version !== t.version || t.dep.computed && (ze(t.dep.computed) || t.dep.version !== t.version)) return !0;
	return !!e._dirty;
}
function ze(e) {
	if (e.flags & 4 && !(e.flags & 16) || (e.flags &= -17, e.globalVersion === qe) || (e.globalVersion = qe, !e.isSSR && e.flags & 128 && (!e.deps && !e._dirty || !Re(e)))) return;
	e.flags |= 2;
	let t = e.dep, n = De, r = He;
	De = e, He = !0;
	try {
		Ie(e);
		let n = e.fn(e._value);
		(t.version === 0 || ie(n, e._value)) && (e.flags |= 128, e._value = n, t.version++);
	} catch (e) {
		throw t.version++, e;
	} finally {
		De = n, He = r, Le(e), e.flags &= -3;
	}
}
function Be(e, t = !1) {
	let { dep: n, prevSub: r, nextSub: i } = e;
	if (r && (r.nextSub = i, e.prevSub = void 0), i && (i.prevSub = r, e.nextSub = void 0), n.subs === e && (n.subs = r, !r && n.computed)) {
		n.computed.flags &= -5;
		for (let e = n.computed.deps; e; e = e.nextDep) Be(e, !0);
	}
	!t && !--n.sc && n.map && n.map.delete(n.key);
}
function Ve(e) {
	let { prevDep: t, nextDep: n } = e;
	t && (t.nextDep = n, e.prevDep = void 0), n && (n.prevDep = t, e.nextDep = void 0);
}
var He = !0, Ue = [];
function We() {
	Ue.push(He), He = !1;
}
function Ge() {
	let e = Ue.pop();
	He = e === void 0 || e;
}
function Ke(e) {
	let { cleanup: t } = e;
	if (e.cleanup = void 0, t) {
		let e = De;
		De = void 0;
		try {
			t();
		} finally {
			De = e;
		}
	}
}
var qe = 0, Je = class {
	constructor(e, t) {
		this.sub = e, this.dep = t, this.version = t.version, this.nextDep = this.prevDep = this.nextSub = this.prevSub = this.prevActiveLink = void 0;
	}
}, Ye = class {
	constructor(e) {
		this.computed = e, this.version = 0, this.activeLink = void 0, this.subs = void 0, this.map = void 0, this.key = void 0, this.sc = 0, this.__v_skip = !0;
	}
	track(e) {
		if (!De || !He || De === this.computed) return;
		let t = this.activeLink;
		if (t === void 0 || t.sub !== De) t = this.activeLink = new Je(De, this), De.deps ? (t.prevDep = De.depsTail, De.depsTail.nextDep = t, De.depsTail = t) : De.deps = De.depsTail = t, Xe(t);
		else if (t.version === -1 && (t.version = this.version, t.nextDep)) {
			let e = t.nextDep;
			e.prevDep = t.prevDep, t.prevDep && (t.prevDep.nextDep = e), t.prevDep = De.depsTail, t.nextDep = void 0, De.depsTail.nextDep = t, De.depsTail = t, De.deps === t && (De.deps = e);
		}
		return t;
	}
	trigger(e) {
		this.version++, qe++, this.notify(e);
	}
	notify(e) {
		Pe();
		try {
			for (let e = this.subs; e; e = e.prevSub) e.sub.notify() && e.sub.dep.notify();
		} finally {
			Fe();
		}
	}
};
function Xe(e) {
	if (e.dep.sc++, e.sub.flags & 4) {
		let t = e.dep.computed;
		if (t && !e.dep.subs) {
			t.flags |= 20;
			for (let e = t.deps; e; e = e.nextDep) Xe(e);
		}
		let n = e.dep.subs;
		n !== e && (e.prevSub = n, n && (n.nextSub = e)), e.dep.subs = e;
	}
}
var Ze = /* @__PURE__ */ new WeakMap(), Qe = /* @__PURE__ */ Symbol(""), $e = /* @__PURE__ */ Symbol(""), et = /* @__PURE__ */ Symbol("");
function tt(e, t, n) {
	if (He && De) {
		let t = Ze.get(e);
		t || Ze.set(e, t = /* @__PURE__ */ new Map());
		let r = t.get(n);
		r || (t.set(n, r = new Ye()), r.map = t, r.key = n), r.track();
	}
}
function nt(e, t, n, r, i, a) {
	let o = Ze.get(e);
	if (!o) {
		qe++;
		return;
	}
	let s = (e) => {
		e && e.trigger();
	};
	if (Pe(), t === "clear") o.forEach(s);
	else {
		let i = d(e), a = i && w(n);
		if (i && n === "length") {
			let e = Number(r);
			o.forEach((t, n) => {
				(n === "length" || n === et || !_(n) && n >= e) && s(t);
			});
		} else switch ((n !== void 0 || o.has(void 0)) && s(o.get(n)), a && s(o.get(et)), t) {
			case "add":
				i ? a && s(o.get("length")) : (s(o.get(Qe)), f(e) && s(o.get($e)));
				break;
			case "delete":
				i || (s(o.get(Qe)), f(e) && s(o.get($e)));
				break;
			case "set": f(e) && s(o.get(Qe));
		}
	}
	Fe();
}
function rt(e, t) {
	let n = Ze.get(e);
	return n && n.get(t);
}
function it(e) {
	let t = /* @__PURE__ */ A(e);
	return t === e || (tt(t, "iterate", et), /* @__PURE__ */ Ut(e)) ? t : /* @__PURE__ */ Ht(e) ? /* @__PURE__ */ Vt(e) ? t.map((e) => qt(Kt(e))) : t.map(qt) : t.map(Kt);
}
function at(e) {
	return tt(e = /* @__PURE__ */ A(e), "iterate", et), e;
}
function ot(e, t) {
	return /* @__PURE__ */ Ht(e) ? qt(/* @__PURE__ */ Vt(e) ? Kt(t) : t) : Kt(t);
}
var st = {
	__proto__: null,
	[Symbol.iterator]() {
		return ct(this, Symbol.iterator, (e) => ot(this, e));
	},
	concat(...e) {
		return it(this).concat(...e.map((e) => d(e) ? it(e) : e));
	},
	entries() {
		return ct(this, "entries", (e) => (e[1] = ot(this, e[1]), e));
	},
	every(e, t) {
		return ut(this, "every", e, t, void 0, arguments);
	},
	filter(e, t) {
		return ut(this, "filter", e, t, (e) => e.map((e) => ot(this, e)), arguments);
	},
	find(e, t) {
		return ut(this, "find", e, t, (e) => ot(this, e), arguments);
	},
	findIndex(e, t) {
		return ut(this, "findIndex", e, t, void 0, arguments);
	},
	findLast(e, t) {
		return ut(this, "findLast", e, t, (e) => ot(this, e), arguments);
	},
	findLastIndex(e, t) {
		return ut(this, "findLastIndex", e, t, void 0, arguments);
	},
	forEach(e, t) {
		return ut(this, "forEach", e, t, void 0, arguments);
	},
	includes(...e) {
		return ft(this, "includes", e);
	},
	indexOf(...e) {
		return ft(this, "indexOf", e);
	},
	join(e) {
		return it(this).join(e);
	},
	lastIndexOf(...e) {
		return ft(this, "lastIndexOf", e);
	},
	map(e, t) {
		return ut(this, "map", e, t, void 0, arguments);
	},
	pop() {
		return pt(this, "pop");
	},
	push(...e) {
		return pt(this, "push", e);
	},
	reduce(e, ...t) {
		return dt(this, "reduce", e, t);
	},
	reduceRight(e, ...t) {
		return dt(this, "reduceRight", e, t);
	},
	shift() {
		return pt(this, "shift");
	},
	some(e, t) {
		return ut(this, "some", e, t, void 0, arguments);
	},
	splice(...e) {
		return pt(this, "splice", e);
	},
	toReversed() {
		return it(this).toReversed();
	},
	toSorted(e) {
		return it(this).toSorted(e);
	},
	toSpliced(...e) {
		return it(this).toSpliced(...e);
	},
	unshift(...e) {
		return pt(this, "unshift", e);
	},
	values() {
		return ct(this, "values", (e) => ot(this, e));
	}
};
function ct(e, t, n) {
	let r = at(e), i = r[t]();
	return r !== e && !/* @__PURE__ */ Ut(e) && (i._next = i.next, i.next = () => {
		let e = i._next();
		return e.done || (e.value = n(e.value)), e;
	}), i;
}
var lt = Array.prototype;
function ut(e, t, n, r, i, a) {
	let o = at(e), s = o !== e && !/* @__PURE__ */ Ut(e), c = o[t];
	if (c !== lt[t]) {
		let t = c.apply(e, a);
		return s ? Kt(t) : t;
	}
	let l = n;
	o !== e && (s ? l = function(t, r) {
		return n.call(this, ot(e, t), r, e);
	} : n.length > 2 && (l = function(t, r) {
		return n.call(this, t, r, e);
	}));
	let u = c.call(o, l, r);
	return s && i ? i(u) : u;
}
function dt(e, t, n, r) {
	let i = at(e), a = i !== e && !/* @__PURE__ */ Ut(e), o = n, s = !1;
	i !== e && (a ? (s = r.length === 0, o = function(t, r, i) {
		return s && (s = !1, t = ot(e, t)), n.call(this, t, ot(e, r), i, e);
	}) : n.length > 3 && (o = function(t, r, i) {
		return n.call(this, t, r, i, e);
	}));
	let c = i[t](o, ...r);
	return s ? ot(e, c) : c;
}
function ft(e, t, n) {
	let r = /* @__PURE__ */ A(e);
	tt(r, "iterate", et);
	let i = r[t](...n);
	return (i === -1 || i === !1) && /* @__PURE__ */ Wt(n[0]) ? (n[0] = /* @__PURE__ */ A(n[0]), r[t](...n)) : i;
}
function pt(e, t, n = []) {
	We(), Pe();
	let r = (/* @__PURE__ */ A(e))[t].apply(e, n);
	return Fe(), Ge(), r;
}
var mt = /* @__PURE__ */ e("__proto__,__v_isRef,__isVue"), ht = new Set(/* @__PURE__ */ Object.getOwnPropertyNames(Symbol).filter((e) => e !== "arguments" && e !== "caller").map((e) => Symbol[e]).filter(_));
function gt(e) {
	_(e) || (e = String(e));
	let t = /* @__PURE__ */ A(this);
	return tt(t, "has", e), t.hasOwnProperty(e);
}
var _t = class {
	constructor(e = !1, t = !1) {
		this._isReadonly = e, this._isShallow = t;
	}
	get(e, t, n) {
		if (t === "__v_skip") return e.__v_skip;
		let r = this._isReadonly, i = this._isShallow;
		if (t === "__v_isReactive") return !r;
		if (t === "__v_isReadonly") return r;
		if (t === "__v_isShallow") return i;
		if (t === "__v_raw") return n === (r ? i ? Ft : Pt : i ? Nt : Mt).get(e) || Object.getPrototypeOf(e) === Object.getPrototypeOf(n) ? e : void 0;
		let a = d(e);
		if (!r) {
			let e;
			if (a && (e = st[t])) return e;
			if (t === "hasOwnProperty") return gt;
		}
		let o = Reflect.get(e, t, /* @__PURE__ */ Jt(e) ? e : n);
		if ((_(t) ? ht.has(t) : mt(t)) || (r || tt(e, "get", t), i)) return o;
		if (/* @__PURE__ */ Jt(o)) {
			let e = a && w(t) ? o : o.value;
			return r && v(e) ? /* @__PURE__ */ zt(e) : e;
		}
		return v(o) ? r ? /* @__PURE__ */ zt(o) : /* @__PURE__ */ Lt(o) : o;
	}
}, vt = class extends _t {
	constructor(e = !1) {
		super(!1, e);
	}
	set(e, t, n, r) {
		let i = e[t], a = d(e) && w(t);
		if (!this._isShallow) {
			let e = /* @__PURE__ */ Ht(i);
			if (!/* @__PURE__ */ Ut(n) && !/* @__PURE__ */ Ht(n) && (i = /* @__PURE__ */ A(i), n = /* @__PURE__ */ A(n)), !a && /* @__PURE__ */ Jt(i) && !/* @__PURE__ */ Jt(n)) return e || (i.value = n), !0;
		}
		let o = a ? Number(t) < e.length : u(e, t), s = Reflect.set(e, t, n, /* @__PURE__ */ Jt(e) ? e : r);
		return e === /* @__PURE__ */ A(r) && s && (o ? ie(n, i) && nt(e, "set", t, n, i) : nt(e, "add", t, n)), s;
	}
	deleteProperty(e, t) {
		let n = u(e, t), r = e[t], i = Reflect.deleteProperty(e, t);
		return i && n && nt(e, "delete", t, void 0, r), i;
	}
	has(e, t) {
		let n = Reflect.has(e, t);
		return (!_(t) || !ht.has(t)) && tt(e, "has", t), n;
	}
	ownKeys(e) {
		return tt(e, "iterate", d(e) ? "length" : Qe), Reflect.ownKeys(e);
	}
}, yt = class extends _t {
	constructor(e = !1) {
		super(!0, e);
	}
	set(e, t) {
		return !0;
	}
	deleteProperty(e, t) {
		return !0;
	}
}, bt = /* @__PURE__ */ new vt(), xt = /* @__PURE__ */ new yt(), St = /* @__PURE__ */ new vt(!0), Ct = (e) => e, wt = (e) => Reflect.getPrototypeOf(e);
function Tt(e, t, n) {
	return function(...r) {
		let i = this.__v_raw, a = /* @__PURE__ */ A(i), o = f(a), c = e === "entries" || e === Symbol.iterator && o, l = e === "keys" && o, u = i[e](...r), d = n ? Ct : t ? qt : Kt;
		return !t && tt(a, "iterate", l ? $e : Qe), s(Object.create(u), { next() {
			let { value: e, done: t } = u.next();
			return t ? {
				value: e,
				done: t
			} : {
				value: c ? [d(e[0]), d(e[1])] : d(e),
				done: t
			};
		} });
	};
}
function Et(e) {
	return function(...t) {
		return e === "delete" ? !1 : e === "clear" ? void 0 : this;
	};
}
function Dt(e, t) {
	let n = {
		get(n) {
			let r = this.__v_raw, i = /* @__PURE__ */ A(r), a = /* @__PURE__ */ A(n);
			e || (ie(n, a) && tt(i, "get", n), tt(i, "get", a));
			let { has: o } = wt(i), s = t ? Ct : e ? qt : Kt;
			if (o.call(i, n)) return s(r.get(n));
			if (o.call(i, a)) return s(r.get(a));
			r !== i && r.get(n);
		},
		get size() {
			let t = this.__v_raw;
			return !e && tt(/* @__PURE__ */ A(t), "iterate", Qe), t.size;
		},
		has(t) {
			let n = this.__v_raw, r = /* @__PURE__ */ A(n), i = /* @__PURE__ */ A(t);
			return e || (ie(t, i) && tt(r, "has", t), tt(r, "has", i)), t === i ? n.has(t) : n.has(t) || n.has(i);
		},
		forEach(n, r) {
			let i = this, a = i.__v_raw, o = /* @__PURE__ */ A(a), s = t ? Ct : e ? qt : Kt;
			return !e && tt(o, "iterate", Qe), a.forEach((e, t) => n.call(r, s(e), s(t), i));
		}
	};
	return s(n, e ? {
		add: Et("add"),
		set: Et("set"),
		delete: Et("delete"),
		clear: Et("clear")
	} : {
		add(e) {
			let n = /* @__PURE__ */ A(this), r = wt(n), i = /* @__PURE__ */ A(e), a = !t && !/* @__PURE__ */ Ut(e) && !/* @__PURE__ */ Ht(e) ? i : e;
			return r.has.call(n, a) || ie(e, a) && r.has.call(n, e) || ie(i, a) && r.has.call(n, i) || (n.add(a), nt(n, "add", a, a)), this;
		},
		set(e, n) {
			!t && !/* @__PURE__ */ Ut(n) && !/* @__PURE__ */ Ht(n) && (n = /* @__PURE__ */ A(n));
			let r = /* @__PURE__ */ A(this), { has: i, get: a } = wt(r), o = i.call(r, e);
			o ||= (e = /* @__PURE__ */ A(e), i.call(r, e));
			let s = a.call(r, e);
			return r.set(e, n), o ? ie(n, s) && nt(r, "set", e, n, s) : nt(r, "add", e, n), this;
		},
		delete(e) {
			let t = /* @__PURE__ */ A(this), { has: n, get: r } = wt(t), i = n.call(t, e);
			i ||= (e = /* @__PURE__ */ A(e), n.call(t, e));
			let a = r ? r.call(t, e) : void 0, o = t.delete(e);
			return i && nt(t, "delete", e, void 0, a), o;
		},
		clear() {
			let e = /* @__PURE__ */ A(this), t = e.size !== 0, n = e.clear();
			return t && nt(e, "clear", void 0, void 0, void 0), n;
		}
	}), [
		"keys",
		"values",
		"entries",
		Symbol.iterator
	].forEach((r) => {
		n[r] = Tt(r, e, t);
	}), n;
}
function Ot(e, t) {
	let n = Dt(e, t);
	return (t, r, i) => r === "__v_isReactive" ? !e : r === "__v_isReadonly" ? e : r === "__v_raw" ? t : Reflect.get(u(n, r) && r in t ? n : t, r, i);
}
var kt = { get: /* @__PURE__ */ Ot(!1, !1) }, At = { get: /* @__PURE__ */ Ot(!1, !0) }, jt = { get: /* @__PURE__ */ Ot(!0, !1) }, Mt = /* @__PURE__ */ new WeakMap(), Nt = /* @__PURE__ */ new WeakMap(), Pt = /* @__PURE__ */ new WeakMap(), Ft = /* @__PURE__ */ new WeakMap();
function It(e) {
	switch (e) {
		case "Object":
		case "Array": return 1;
		case "Map":
		case "Set":
		case "WeakMap":
		case "WeakSet": return 2;
		default: return 0;
	}
}
// @__NO_SIDE_EFFECTS__
function Lt(e) {
	return /* @__PURE__ */ Ht(e) ? e : Bt(e, !1, bt, kt, Mt);
}
// @__NO_SIDE_EFFECTS__
function Rt(e) {
	return Bt(e, !1, St, At, Nt);
}
// @__NO_SIDE_EFFECTS__
function zt(e) {
	return Bt(e, !0, xt, jt, Pt);
}
function Bt(e, t, n, r, i) {
	if (!v(e) || e.__v_raw && !(t && e.__v_isReactive) || e.__v_skip || !Object.isExtensible(e)) return e;
	let a = i.get(e);
	if (a) return a;
	let o = It(S(e));
	if (o === 0) return e;
	let s = new Proxy(e, o === 2 ? r : n);
	return i.set(e, s), s;
}
// @__NO_SIDE_EFFECTS__
function Vt(e) {
	return /* @__PURE__ */ Ht(e) ? /* @__PURE__ */ Vt(e.__v_raw) : !!(e && e.__v_isReactive);
}
// @__NO_SIDE_EFFECTS__
function Ht(e) {
	return !!(e && e.__v_isReadonly);
}
// @__NO_SIDE_EFFECTS__
function Ut(e) {
	return !!(e && e.__v_isShallow);
}
// @__NO_SIDE_EFFECTS__
function Wt(e) {
	return e ? !!e.__v_raw : !1;
}
// @__NO_SIDE_EFFECTS__
function A(e) {
	let t = e && e.__v_raw;
	return t ? /* @__PURE__ */ A(t) : e;
}
function Gt(e) {
	return !u(e, "__v_skip") && Object.isExtensible(e) && oe(e, "__v_skip", !0), e;
}
var Kt = (e) => v(e) ? /* @__PURE__ */ Lt(e) : e, qt = (e) => v(e) ? /* @__PURE__ */ zt(e) : e;
// @__NO_SIDE_EFFECTS__
function Jt(e) {
	return e ? e.__v_isRef === !0 : !1;
}
// @__NO_SIDE_EFFECTS__
function j(e) {
	return Xt(e, !1);
}
// @__NO_SIDE_EFFECTS__
function Yt(e) {
	return Xt(e, !0);
}
function Xt(e, t) {
	return /* @__PURE__ */ Jt(e) ? e : new Zt(e, t);
}
var Zt = class {
	constructor(e, t) {
		this.dep = new Ye(), this.__v_isRef = !0, this.__v_isShallow = !1, this._rawValue = t ? e : /* @__PURE__ */ A(e), this._value = t ? e : Kt(e), this.__v_isShallow = t;
	}
	get value() {
		return this.dep.track(), this._value;
	}
	set value(e) {
		let t = this._rawValue, n = this.__v_isShallow || /* @__PURE__ */ Ut(e) || /* @__PURE__ */ Ht(e);
		e = n ? e : /* @__PURE__ */ A(e), ie(e, t) && (this._rawValue = e, this._value = n ? e : Kt(e), this.dep.trigger());
	}
};
function Qt(e) {
	return /* @__PURE__ */ Jt(e) ? e.value : e;
}
var $t = {
	get: (e, t, n) => t === "__v_raw" ? e : Qt(Reflect.get(e, t, n)),
	set: (e, t, n, r) => {
		let i = e[t];
		return /* @__PURE__ */ Jt(i) && !/* @__PURE__ */ Jt(n) ? (i.value = n, !0) : Reflect.set(e, t, n, r);
	}
};
function en(e) {
	return /* @__PURE__ */ Vt(e) ? e : new Proxy(e, $t);
}
var tn = class {
	constructor(e, t, n) {
		this._object = e, this._defaultValue = n, this.__v_isRef = !0, this._value = void 0, this._key = _(t) ? t : String(t), this._raw = /* @__PURE__ */ A(e);
		let r = !0, i = e;
		if (!d(e) || _(this._key) || !w(this._key)) do
			r = !/* @__PURE__ */ Wt(i) || /* @__PURE__ */ Ut(i);
		while (r && (i = i.__v_raw));
		this._shallow = r;
	}
	get value() {
		let e = this._object[this._key];
		return this._shallow && (e = Qt(e)), this._value = e === void 0 ? this._defaultValue : e;
	}
	set value(e) {
		if (this._shallow && /* @__PURE__ */ Jt(this._raw[this._key])) {
			let t = this._object[this._key];
			if (/* @__PURE__ */ Jt(t)) {
				t.value = e;
				return;
			}
		}
		this._object[this._key] = e;
	}
	get dep() {
		return rt(this._raw, this._key);
	}
}, nn = class {
	constructor(e) {
		this._getter = e, this.__v_isRef = !0, this.__v_isReadonly = !0, this._value = void 0;
	}
	get value() {
		return this._value = this._getter();
	}
};
// @__NO_SIDE_EFFECTS__
function M(e, t, n) {
	return /* @__PURE__ */ Jt(e) ? e : h(e) ? new nn(e) : v(e) && arguments.length > 1 ? rn(e, t, n) : /* @__PURE__ */ j(e);
}
function rn(e, t, n) {
	return new tn(e, t, n);
}
var an = class {
	constructor(e, t, n) {
		this.fn = e, this.setter = t, this._value = void 0, this.dep = new Ye(this), this.__v_isRef = !0, this.deps = void 0, this.depsTail = void 0, this.flags = 16, this.globalVersion = qe - 1, this.next = void 0, this.effect = this, this.__v_isReadonly = !t, this.isSSR = n;
	}
	notify() {
		if (this.flags |= 16, !(this.flags & 8) && De !== this) return Ne(this, !0), !0;
	}
	get value() {
		let e = this.dep.track();
		return ze(this), e && (e.version = this.dep.version), this._value;
	}
	set value(e) {
		this.setter && this.setter(e);
	}
};
// @__NO_SIDE_EFFECTS__
function on(e, t, n = !1) {
	let r, i;
	return h(e) ? r = e : (r = e.get, i = e.set), new an(r, i, n);
}
var sn = {}, cn = /* @__PURE__ */ new WeakMap(), ln = void 0;
function un(e, t = !1, n = ln) {
	if (n) {
		let t = cn.get(n);
		t || cn.set(n, t = []), t.push(e);
	}
}
function dn(e, n, i = t) {
	let { immediate: a, deep: o, once: s, scheduler: l, augmentJob: u, call: f } = i, p = (e) => o ? e : /* @__PURE__ */ Ut(e) || o === !1 || o === 0 ? fn(e, 1) : fn(e), m, g, _, v, y = !1, b = !1;
	if (/* @__PURE__ */ Jt(e) ? (g = () => e.value, y = /* @__PURE__ */ Ut(e)) : /* @__PURE__ */ Vt(e) ? (g = () => p(e), y = !0) : d(e) ? (b = !0, y = e.some((e) => /* @__PURE__ */ Vt(e) || /* @__PURE__ */ Ut(e)), g = () => e.map((e) => {
		if (/* @__PURE__ */ Jt(e)) return e.value;
		if (/* @__PURE__ */ Vt(e)) return p(e);
		if (h(e)) return f ? f(e, 2) : e();
	})) : g = h(e) ? n ? f ? () => f(e, 2) : e : () => {
		if (_) {
			We();
			try {
				_();
			} finally {
				Ge();
			}
		}
		let t = ln;
		ln = m;
		try {
			return f ? f(e, 3, [v]) : e(v);
		} finally {
			ln = t;
		}
	} : r, n && o) {
		let e = g, t = o === !0 ? Infinity : o;
		g = () => fn(e(), t);
	}
	let x = Ee(), S = () => {
		m.stop(), x && x.active && c(x.effects, m);
	};
	if (s && n) {
		let e = n;
		n = (...t) => {
			let n = e(...t);
			return S(), n;
		};
	}
	let C = b ? Array(e.length).fill(sn) : sn, w = (e) => {
		if (m.flags & 1 && (m.dirty || e)) {
			if (n) {
				let t = m.run();
				if (e || o || y || (b ? t.some((e, t) => ie(e, C[t])) : ie(t, C))) {
					_ && _();
					let e = ln;
					ln = m;
					try {
						let e = [
							t,
							C === sn ? void 0 : b && C[0] === sn ? [] : C,
							v
						];
						C = t, f ? f(n, 3, e) : n(...e);
					} finally {
						ln = e;
					}
				}
			} else m.run();
		}
	};
	return u && u(w), m = new ke(g), m.scheduler = l ? () => l(w, !1) : w, v = (e) => un(e, !1, m), _ = m.onStop = () => {
		let e = cn.get(m);
		if (e) {
			if (f) f(e, 4);
			else for (let t of e) t();
			cn.delete(m);
		}
	}, n ? a ? w(!0) : C = m.run() : l ? l(w.bind(null, !0), !0) : m.run(), S.pause = m.pause.bind(m), S.resume = m.resume.bind(m), S.stop = S, S;
}
function fn(e, t = Infinity, n) {
	if (t <= 0 || !v(e) || e.__v_skip || (n ||= /* @__PURE__ */ new Map(), (n.get(e) || 0) >= t)) return e;
	if (n.set(e, t), t--, /* @__PURE__ */ Jt(e)) fn(e.value, t, n);
	else if (d(e)) for (let r = 0; r < e.length; r++) fn(e[r], t, n);
	else if (p(e) || f(e)) e.forEach((e) => {
		fn(e, t, n);
	});
	else if (C(e)) {
		for (let r in e) fn(e[r], t, n);
		for (let r of Object.getOwnPropertySymbols(e)) Object.prototype.propertyIsEnumerable.call(e, r) && fn(e[r], t, n);
	}
	return e;
}
//#endregion
//#region node_modules/@vue/runtime-core/dist/runtime-core.esm-bundler.js
function pn(e, t, n, r) {
	try {
		return r ? e(...r) : e();
	} catch (e) {
		hn(e, t, n);
	}
}
function mn(e, t, n, r) {
	if (h(e)) {
		let i = pn(e, t, n, r);
		return i && y(i) && i.catch((e) => {
			hn(e, t, n);
		}), i;
	}
	if (d(e)) {
		let i = [];
		for (let a = 0; a < e.length; a++) i.push(mn(e[a], t, n, r));
		return i;
	}
}
function hn(e, n, r, i = !0) {
	let a = n ? n.vnode : null, { errorHandler: o, throwUnhandledErrorInProduction: s } = n && n.appContext.config || t;
	if (n) {
		let t = n.parent, i = n.proxy, a = `https://vuejs.org/error-reference/#runtime-${r}`;
		for (; t;) {
			let n = t.ec;
			if (n) {
				for (let t = 0; t < n.length; t++) if (n[t](e, i, a) === !1) return;
			}
			t = t.parent;
		}
		if (o) {
			We(), pn(o, null, 10, [
				e,
				i,
				a
			]), Ge();
			return;
		}
	}
	gn(e, r, a, i, s);
}
function gn(e, t, n, r = !0, i = !1) {
	if (i) throw e;
	console.error(e);
}
var _n = [], vn = -1, yn = [], bn = null, xn = 0, Sn = /* @__PURE__ */ Promise.resolve(), Cn = null;
function wn(e) {
	let t = Cn || Sn;
	return e ? t.then(this ? e.bind(this) : e) : t;
}
function Tn(e) {
	let t = vn + 1, n = _n.length;
	for (; t < n;) {
		let r = t + n >>> 1, i = _n[r], a = jn(i);
		a < e || a === e && i.flags & 2 ? t = r + 1 : n = r;
	}
	return t;
}
function En(e) {
	if (!(e.flags & 1)) {
		let t = jn(e), n = _n[_n.length - 1];
		!n || !(e.flags & 2) && t >= jn(n) ? _n.push(e) : _n.splice(Tn(t), 0, e), e.flags |= 1, Dn();
	}
}
function Dn() {
	Cn ||= Sn.then(Mn);
}
function On(e) {
	if (!d(e)) bn && e.id === -1 ? bn.splice(xn + 1, 0, e) : e.flags & 1 || (yn.push(e), e.flags |= 1);
	else for (let t = 0; t < e.length; t++) yn.push(e[t]);
	Dn();
}
function kn(e, t, n = vn + 1) {
	for (; n < _n.length; n++) {
		let t = _n[n];
		if (t && t.flags & 2) {
			if (e && t.id !== e.uid) continue;
			_n.splice(n, 1), n--, t.flags & 4 && (t.flags &= -2), t(), t.flags & 4 || (t.flags &= -2);
		}
	}
}
function An(e) {
	if (yn.length) {
		let e = [...new Set(yn)].sort((e, t) => jn(e) - jn(t));
		if (yn.length = 0, bn) {
			for (let t = 0; t < e.length; t++) bn.push(e[t]);
			return;
		}
		for (bn = e, xn = 0; xn < bn.length; xn++) {
			let e = bn[xn];
			e.flags & 4 && (e.flags &= -2), e.flags & 8 || e(), e.flags &= -2;
		}
		bn = null, xn = 0;
	}
}
var jn = (e) => e.id == null ? e.flags & 2 ? -1 : Infinity : e.id;
function Mn(e) {
	try {
		for (vn = 0; vn < _n.length; vn++) {
			let e = _n[vn];
			e && !(e.flags & 8) && (e.flags & 4 && (e.flags &= -2), pn(e, e.i, e.i ? 15 : 14), e.flags & 4 || (e.flags &= -2));
		}
	} finally {
		for (; vn < _n.length; vn++) {
			let e = _n[vn];
			e && (e.flags &= -2);
		}
		vn = -1, _n.length = 0, An(e), Cn = null, (_n.length || yn.length) && Mn(e);
	}
}
var Nn = null, Pn = null;
function Fn(e) {
	let t = Nn;
	return Nn = e, Pn = e && e.type.__scopeId || null, t;
}
function In(e, t = Nn, n) {
	if (!t || e._n) return e;
	let r = (...n) => {
		r._d && Wi(-1);
		let i = Fn(t), a = Bi.length, o;
		try {
			o = e(...n);
		} finally {
			for (let e = Bi.length; e > a; e--) Hi();
			Fn(i), r._d && Wi(1);
		}
		return o;
	};
	return r._n = !0, r._c = !0, r._d = !0, r;
}
function Ln(e, n) {
	if (Nn === null) return e;
	let r = wa(Nn), i = e.dirs ||= [];
	for (let e = 0; e < n.length; e++) {
		let [a, o, s, c = t] = n[e];
		a && (h(a) && (a = {
			mounted: a,
			updated: a
		}), a.deep && fn(o), i.push({
			dir: a,
			instance: r,
			value: o,
			oldValue: void 0,
			arg: s,
			modifiers: c
		}));
	}
	return e;
}
function Rn(e, t, n, r) {
	let i = e.dirs, a = t && t.dirs;
	for (let o = 0; o < i.length; o++) {
		let s = i[o];
		a && (s.oldValue = a[o].value);
		let c = s.dir[r];
		c && (We(), mn(c, n, 8, [
			e.el,
			s,
			e,
			t
		]), Ge());
	}
}
function zn(e, t) {
	if (ua) {
		let n = ua.provides, r = ua.parent && ua.parent.provides;
		r === n && (n = ua.provides = Object.create(r)), n[e] = t;
	}
}
function Bn(e, t, n = !1) {
	let r = da();
	if (r || Yr) {
		let i = Yr ? Yr._context.provides : r ? r.parent == null || r.ce ? r.vnode.appContext && r.vnode.appContext.provides : r.parent.provides : void 0;
		if (i && e in i) return i[e];
		if (arguments.length > 1) return n && h(t) ? t.call(r && r.proxy) : t;
	}
}
var Vn = /* @__PURE__ */ Symbol.for("v-scx"), Hn = () => Bn(Vn);
function Un(e, t) {
	return Gn(e, null, t);
}
function Wn(e, t, n) {
	return Gn(e, t, n);
}
function Gn(e, n, i = t) {
	let { immediate: a, deep: o, flush: c, once: l } = i, u = s({}, i), d = n && a || !n && c !== "post", f;
	if (_a) {
		if (c === "sync") {
			let e = Hn();
			f = e.__watcherHandles ||= [];
		} else if (!d) {
			let e = () => {};
			return e.stop = r, e.resume = r, e.pause = r, e;
		}
	}
	let p = ua;
	u.call = (e, t, n) => mn(e, p, t, n);
	let m = !1;
	c === "post" ? u.scheduler = (e) => {
		wi(e, p && p.suspense);
	} : c !== "sync" && (m = !0, u.scheduler = (e, t) => {
		t ? e() : En(e);
	}), u.augmentJob = (e) => {
		n && (e.flags |= 4), m && (e.flags |= 2, p && (e.id = p.uid, e.i = p));
	};
	let h = dn(e, n, u);
	return _a && (f ? f.push(h) : d && h()), h;
}
var Kn = /* @__PURE__ */ new WeakMap(), qn = /* @__PURE__ */ Symbol("_vte"), Jn = (e) => e.__isTeleport, Yn = (e) => e && (e.disabled || e.disabled === ""), Xn = (e) => e && (e.defer || e.defer === ""), Zn = (e) => typeof SVGElement < "u" && e instanceof SVGElement, Qn = (e) => typeof MathMLElement == "function" && e instanceof MathMLElement, $n = (e, t) => {
	let n = e && e.to;
	return g(n) ? t ? t(n) : null : n;
}, er = {
	name: "Teleport",
	__isTeleport: !0,
	process(e, t, n, r, i, a, o, s, c, l) {
		let { mc: u, pc: d, pbc: f, o: { insert: p, querySelector: m, createText: h, createComment: g, parentNode: _ } } = l, v = Yn(t.props), { dynamicChildren: y } = t, b = (e, t, n) => {
			e.shapeFlag & 16 && u(e.children, t, n, i, a, o, s, c);
		}, x = (e = t) => {
			let n = Yn(e.props), r = e.target = $n(e.props, m), a = ar(r, e, h, p);
			r && (o !== "svg" && Zn(r) ? o = "svg" : o !== "mathml" && Qn(r) && (o = "mathml"), i && i.isCE && (i.ce._teleportTargets || (i.ce._teleportTargets = /* @__PURE__ */ new Set())).add(r), n || (b(e, r, a), ir(e, !1)));
		}, S = (e) => {
			let t = () => {
				if (Kn.get(e) === t) {
					if (Kn.delete(e), Yn(e.props)) {
						let t = _(e.el) || n;
						b(e, t, e.anchor), ir(e, !0);
					}
					x(e);
				}
			};
			Kn.set(e, t), wi(t, a);
		};
		if (e == null) {
			let e = t.el = h(""), i = t.anchor = h("");
			if (p(e, n, r), p(i, n, r), Xn(t.props) || a && a.pendingBranch) {
				S(t);
				return;
			}
			v && (b(t, n, i), ir(t, !0)), x();
		} else {
			t.el = e.el;
			let r = t.anchor = e.anchor, u = Kn.get(e);
			if (u) {
				u.flags |= 8, Kn.delete(e), S(t);
				return;
			}
			t.targetStart = e.targetStart;
			let p = t.target = e.target, h = t.targetAnchor = e.targetAnchor, g = Yn(e.props), _ = g ? n : p, b = g ? r : h;
			if (o === "svg" || Zn(p) ? o = "svg" : (o === "mathml" || Qn(p)) && (o = "mathml"), y ? (f(e.dynamicChildren, y, _, i, a, o, s), Ai(e, t, !0)) : c || d(e, t, _, b, i, a, o, s, !1), v) g ? t.props && e.props && t.props.to !== e.props.to && (t.props.to = e.props.to) : tr(t, n, r, l, 1);
			else if ((t.props && t.props.to) !== (e.props && e.props.to)) {
				let e = $n(t.props, m);
				e && (t.target = e, tr(t, e, null, l, 0));
			} else g && tr(t, p, h, l, 1);
			ir(t, v);
		}
	},
	remove(e, t, n, { um: r, o: { remove: i } }, a) {
		let { shapeFlag: o, children: s, anchor: c, targetStart: l, targetAnchor: u, target: d, props: f } = e, p = Yn(f), m = a || !p, h = Kn.get(e);
		if (h && (h.flags |= 8, Kn.delete(e)), d && (i(l), i(u)), a && i(c), !h && (p || d) && o & 16) for (let e = 0; e < s.length; e++) {
			let i = s[e];
			r(i, t, n, m, !!i.dynamicChildren);
		}
	},
	move: tr,
	hydrate: nr
};
function tr(e, t, n, { o: { insert: r }, m: i }, a = 2) {
	a === 0 && r(e.targetAnchor, t, n);
	let { el: o, anchor: s, shapeFlag: c, children: l, props: u } = e, d = a === 2;
	if (d && r(o, t, n), !Kn.has(e) && (!d || Yn(u)) && c & 16) for (let e = 0; e < l.length; e++) i(l[e], t, n, 2);
	d && r(s, t, n);
}
function nr(e, t, n, r, i, a, { o: { nextSibling: o, parentNode: s, querySelector: c, insert: l, createText: u } }, d) {
	function f(e, n) {
		let r = n;
		for (; r;) {
			if (r && r.nodeType === 8) {
				if (r.data === "teleport start anchor") t.targetStart = r;
				else if (r.data === "teleport anchor") {
					t.targetAnchor = r, e._lpa = t.targetAnchor && o(t.targetAnchor);
					break;
				}
			}
			r = o(r);
		}
	}
	function p(e, t) {
		t.anchor = d(o(e), t, s(e), n, r, i, a);
	}
	let m = t.target = $n(t.props, c), h = Yn(t.props);
	if (m) {
		let c = m._lpa || m.firstChild;
		t.shapeFlag & 16 && (h ? (p(e, t), f(m, c), t.targetAnchor || ar(m, t, u, l, s(e) === m ? e : null)) : (t.anchor = o(e), f(m, c), t.targetAnchor || ar(m, t, u, l), d(c && o(c), t, m, n, r, i, a))), ir(t, h);
	} else h && t.shapeFlag & 16 && (p(e, t), t.targetStart = e, t.targetAnchor = o(e));
	return t.anchor && o(t.anchor);
}
var rr = er;
function ir(e, t) {
	let n = e.ctx;
	if (n && n.ut) {
		let r, i;
		for (t ? (r = e.el, i = e.anchor) : (r = e.targetStart, i = e.targetAnchor); r && r !== i;) r.nodeType === 1 && r.setAttribute("data-v-owner", n.uid), r = r.nextSibling;
		n.ut();
	}
}
function ar(e, t, n, r, i = null) {
	let a = t.targetStart = n(""), o = t.targetAnchor = n("");
	return a[qn] = o, e && (r(a, e, i), r(o, e, i)), o;
}
var or = /* @__PURE__ */ Symbol("_leaveCb"), sr = /* @__PURE__ */ Symbol("_enterCb");
function cr() {
	let e = {
		isMounted: !1,
		isLeaving: !1,
		isUnmounting: !1,
		leavingVNodes: /* @__PURE__ */ new Map()
	};
	return Fr(() => {
		e.isMounted = !0;
	}), Lr(() => {
		e.isUnmounting = !0;
	}), e;
}
var lr = [Function, Array], ur = {
	mode: String,
	appear: Boolean,
	persisted: Boolean,
	onBeforeEnter: lr,
	onEnter: lr,
	onAfterEnter: lr,
	onEnterCancelled: lr,
	onBeforeLeave: lr,
	onLeave: lr,
	onAfterLeave: lr,
	onLeaveCancelled: lr,
	onBeforeAppear: lr,
	onAppear: lr,
	onAfterAppear: lr,
	onAppearCancelled: lr
}, dr = (e) => {
	let t = e.subTree;
	return t.component ? dr(t.component) : t;
}, fr = {
	name: "BaseTransition",
	props: ur,
	setup(e, { slots: t }) {
		let n = da(), r = cr();
		return () => {
			let i = t.default && br(t.default(), !0), a = i && i.length ? pr(i) : n.subTree ? ta() : void 0;
			if (!a) return;
			let o = /* @__PURE__ */ A(e), { mode: s } = o;
			if (r.isLeaving) return _r(a);
			let c = vr(a);
			if (!c) return _r(a);
			let l = gr(c, o, r, n, (e) => l = e);
			c.type !== Ri && yr(c, l);
			let u = n.subTree && vr(n.subTree);
			if (u && u.type !== Ri && !qi(u, c) && dr(n).type !== Ri) {
				let e = gr(u, o, r, n);
				if (yr(u, e), s === "out-in" && c.type !== Ri) return r.isLeaving = !0, e.afterLeave = () => {
					r.isLeaving = !1, n.job.flags & 8 || n.update(), delete e.afterLeave, u = void 0;
				}, _r(a);
				s === "in-out" && c.type !== Ri ? e.delayLeave = (e, t, n) => {
					let i = hr(r, u);
					i[String(u.key)] = u, e[or] = () => {
						t(), e[or] = void 0, delete l.delayedLeave, u = void 0;
					}, l.delayedLeave = () => {
						n(), delete l.delayedLeave, u = void 0;
					};
				} : u = void 0;
			} else u &&= void 0;
			return a;
		};
	}
};
function pr(e) {
	let t = e[0];
	if (e.length > 1) {
		for (let n of e) if (n.type !== Ri) {
			t = n;
			break;
		}
	}
	return t;
}
var mr = fr;
function hr(e, t) {
	let { leavingVNodes: n } = e, r = n.get(t.type);
	return r || (r = /* @__PURE__ */ Object.create(null), n.set(t.type, r)), r;
}
function gr(e, t, n, r, i) {
	let { appear: a, mode: o, persisted: s = !1, onBeforeEnter: c, onEnter: l, onAfterEnter: u, onEnterCancelled: f, onBeforeLeave: p, onLeave: m, onAfterLeave: h, onLeaveCancelled: g, onBeforeAppear: _, onAppear: v, onAfterAppear: y, onAppearCancelled: b } = t, x = String(e.key), S = hr(n, e), C = (e, t) => {
		e && mn(e, r, 9, t);
	}, w = (e, t) => {
		let n = t[1];
		C(e, t), d(e) ? e.every((e) => e.length <= 1) && n() : e.length <= 1 && n();
	}, T = {
		mode: o,
		persisted: s,
		beforeEnter(t) {
			let r = c;
			if (!n.isMounted) {
				if (a) r = _ || c;
				else return;
			}
			t[or] && t[or](!0);
			let i = S[x];
			i && qi(e, i) && i.el[or] && i.el[or](), C(r, [t]);
		},
		enter(t) {
			if (S[x] === e) return;
			let r = l, i = u, o = f;
			if (!n.isMounted) {
				if (a) r = v || l, i = y || u, o = b || f;
				else return;
			}
			let s = !1;
			t[sr] = (e) => {
				s || (s = !0, C(e ? o : i, [t]), T.delayedLeave && T.delayedLeave(), t[sr] = void 0);
			};
			let c = t[sr].bind(null, !1);
			r ? w(r, [t, c]) : c();
		},
		leave(t, r) {
			let i = String(e.key);
			if (t[sr] && t[sr](!0), n.isUnmounting) return r();
			C(p, [t]);
			let a = !1;
			t[or] = (n) => {
				a || (a = !0, r(), C(n ? g : h, [t]), t[or] = void 0, S[i] === e && delete S[i]);
			};
			let o = t[or].bind(null, !1);
			S[i] = e, m ? w(m, [t, o]) : o();
		},
		clone(e) {
			let a = gr(e, t, n, r, i);
			return i && i(a), a;
		}
	};
	return T;
}
function _r(e) {
	if (Dr(e)) return e = $i(e), e.children = null, e;
}
function vr(e) {
	if (!Dr(e)) return Jn(e.type) && e.children ? pr(e.children) : e;
	if (e.component) return e.component.subTree;
	let { shapeFlag: t, children: n } = e;
	if (n) {
		if (t & 16) return n[0];
		if (t & 32 && h(n.default)) return n.default();
	}
}
function yr(e, t) {
	if (e.shapeFlag & 6 && e.component) {
		e.transition = t;
		let n = e.component.subTree;
		yr(Jn(n.type) && vr(n) || n, t);
	} else e.shapeFlag & 128 ? (e.ssContent.transition = t.clone(e.ssContent), e.ssFallback.transition = t.clone(e.ssFallback)) : e.transition = t;
}
function br(e, t = !1, n) {
	let r = [], i = 0;
	for (let a = 0; a < e.length; a++) {
		let o = e[a], s = n == null ? o.key : String(n) + String(o.key == null ? a : o.key);
		o.type === P ? (o.patchFlag & 128 && i++, r = r.concat(br(o.children, t, s))) : (t || o.type !== Ri) && r.push(s == null ? o : $i(o, { key: s }));
	}
	if (i > 1) for (let e = 0; e < r.length; e++) r[e].patchFlag = -2;
	return r;
}
// @__NO_SIDE_EFFECTS__
function N(e, t) {
	return h(e) ? /* @__PURE__ */ s({ name: e.name }, t, { setup: e }) : e;
}
function xr(e) {
	e.ids = [
		e.ids[0] + e.ids[2]++ + "-",
		0,
		0
	];
}
function Sr(e, t) {
	let n;
	return !!((n = Object.getOwnPropertyDescriptor(e, t)) && !n.configurable);
}
var Cr = /* @__PURE__ */ new WeakMap();
function wr(e, n, r, a, o = !1) {
	if (d(e)) {
		e.forEach((e, t) => wr(e, n && (d(n) ? n[t] : n), r, a, o));
		return;
	}
	if (Er(a) && !o) {
		a.shapeFlag & 512 && a.type.__asyncResolved && a.component.subTree.component && wr(e, n, r, a.component.subTree);
		return;
	}
	let s = a.shapeFlag & 4 ? wa(a.component) : a.el, l = o ? null : s, { i: f, r: p } = e, m = n && n.r, _ = f.refs === t ? f.refs = {} : f.refs, v = f.setupState, y = /* @__PURE__ */ A(v), b = v === t ? i : (e) => !Sr(_, e) && u(y, e), x = (e, t) => !(t && Sr(_, t));
	if (m != null && m !== p) {
		if (Tr(n), g(m)) _[m] = null, b(m) && (v[m] = null);
		else if (/* @__PURE__ */ Jt(m)) {
			let e = n;
			x(m, e.k) && (m.value = null), e.k && (_[e.k] = null);
		}
	}
	if (h(p)) pn(p, f, 12, [l, _]);
	else {
		let t = g(p), n = /* @__PURE__ */ Jt(p);
		if (t || n) {
			let i = () => {
				if (e.f) {
					let n = t ? b(p) ? v[p] : _[p] : x(p) || !e.k ? p.value : _[e.k];
					if (o) d(n) && c(n, s);
					else if (d(n)) n.includes(s) || n.push(s);
					else if (t) _[p] = [s], b(p) && (v[p] = _[p]);
					else {
						let t = [s];
						x(p, e.k) && (p.value = t), e.k && (_[e.k] = t);
					}
				} else t ? (_[p] = l, b(p) && (v[p] = l)) : n && (x(p, e.k) && (p.value = l), e.k && (_[e.k] = l));
			};
			if (l) {
				let t = () => {
					i(), Cr.delete(e);
				};
				t.id = -1, Cr.set(e, t), wi(t, r);
			} else Tr(e), i();
		}
	}
}
function Tr(e) {
	let t = Cr.get(e);
	t && (t.flags |= 8, Cr.delete(e));
}
ue().requestIdleCallback, ue().cancelIdleCallback;
var Er = (e) => !!e.type.__asyncLoader, Dr = (e) => e.type.__isKeepAlive;
function Or(e, t) {
	Ar(e, "a", t);
}
function kr(e, t) {
	Ar(e, "da", t);
}
function Ar(e, t, n = ua) {
	let r = e.__wdc ||= () => {
		let t = n;
		for (; t;) {
			if (t.isDeactivated) return;
			t = t.parent;
		}
		return e();
	};
	if (Mr(t, r, n), n) {
		let e = n.parent;
		for (; e && e.parent;) Dr(e.parent.vnode) && jr(r, t, n, e), e = e.parent;
	}
}
function jr(e, t, n, r) {
	let i = Mr(t, e, r, !0);
	Rr(() => {
		c(r[t], i);
	}, n);
}
function Mr(e, t, n = ua, r = !1) {
	if (n) {
		let i = n[e] || (n[e] = []), a = t.__weh ||= (...r) => {
			We();
			let i = ma(n), a = mn(t, n, e, r);
			return i(), Ge(), a;
		};
		return r ? i.unshift(a) : i.push(a), a;
	}
}
var Nr = (e) => (t, n = ua) => {
	(!_a || e === "sp") && Mr(e, (...e) => t(...e), n);
}, Pr = Nr("bm"), Fr = Nr("m"), Ir = Nr("u"), Lr = Nr("bum"), Rr = Nr("um"), zr = /* @__PURE__ */ Symbol.for("v-ndc");
function Br(e, t, n, r, i, a) {
	if (n ??= {}, Nn.ce || Nn.parent && Er(Nn.parent) && Nn.parent.ce) {
		let e = a != null && n.key == null ? s({}, n, { key: a }) : n, i = Object.keys(e).length > 0;
		return t !== "default" && (e.name = t), F(), L(P, null, [Xi("slot", e, r && r())], i ? -2 : 64);
	}
	let o = e[t];
	o && o._c && (o._d = !1);
	let c = Bi.length;
	F();
	let l;
	try {
		let i = o && Vr(o(n)), s = n.key || a || i && i.key;
		l = L(P, { key: (s && !_(s) ? s : `_${t}`) + (!i && r ? "_fb" : "") }, i || (r ? r() : []), i && e._ === 1 ? 64 : -2);
	} catch (e) {
		for (let e = Bi.length; e > c; e--) Hi();
		throw e;
	} finally {
		o && o._c && (o._d = !0);
	}
	return !i && l.scopeId && (l.slotScopeIds = [l.scopeId + "-s"]), l;
}
function Vr(e) {
	return e.some((e) => !Ki(e) || !(e.type === Ri || e.type === P && !Vr(e.children))) ? e : null;
}
var Hr = (e) => e ? ga(e) ? wa(e) : Hr(e.parent) : null, Ur = /* @__PURE__ */ s(/* @__PURE__ */ Object.create(null), {
	$: (e) => e,
	$el: (e) => e.vnode.el,
	$data: (e) => e.data,
	$props: (e) => e.props,
	$attrs: (e) => e.attrs,
	$slots: (e) => e.slots,
	$refs: (e) => e.refs,
	$parent: (e) => Hr(e.parent),
	$root: (e) => Hr(e.root),
	$host: (e) => e.ce,
	$emit: (e) => e.emit,
	$options: (e) => e.type,
	$forceUpdate: (e) => e.f ||= () => {
		En(e.update);
	},
	$nextTick: (e) => e.n ||= wn.bind(e.proxy),
	$watch: (e) => r
}), Wr = (e, n) => e !== t && !e.__isScriptSetup && u(e, n), Gr = {
	get({ _: e }, n) {
		if (n === "__v_skip") return !0;
		let { ctx: r, setupState: i, data: a, props: o, accessCache: s, type: c, appContext: l } = e;
		if (n[0] !== "$") {
			let e = s[n];
			if (e !== void 0) switch (e) {
				case 1: return i[n];
				case 2: return a[n];
				case 4: return r[n];
				case 3: return o[n];
			}
			else if (Wr(i, n)) return s[n] = 1, i[n];
			else if (u(o, n)) return s[n] = 3, o[n];
			else if (r !== t && u(r, n)) return s[n] = 4, r[n];
			else s[n] = 0;
		}
		let d = Ur[n], f, p;
		if (d) return n === "$attrs" && tt(e.attrs, "get", ""), d(e);
		if ((f = c.__cssModules) && (f = f[n])) return f;
		if (r !== t && u(r, n)) return s[n] = 4, r[n];
		if (p = l.config.globalProperties, u(p, n)) return p[n];
	},
	set({ _: e }, t, n) {
		let { data: r, setupState: i, ctx: a } = e;
		return Wr(i, t) ? (i[t] = n, !0) : u(e.props, t) || t[0] === "$" && t.slice(1) in e ? !1 : (a[t] = n, !0);
	},
	has({ _: { data: e, setupState: t, accessCache: n, ctx: r, appContext: i, props: a, type: o } }, s) {
		let c;
		return !!(n[s] || Wr(t, s) || u(a, s) || u(r, s) || u(Ur, s) || u(i.config.globalProperties, s) || (c = o.__cssModules) && c[s]);
	},
	defineProperty(e, t, n) {
		return n.get == null ? u(n, "value") && this.set(e, t, n.value, null) : e._.accessCache[t] = 0, Reflect.defineProperty(e, t, n);
	}
};
function Kr() {
	return {
		app: null,
		config: {
			isNativeTag: i,
			performance: !1,
			globalProperties: {},
			optionMergeStrategies: {},
			errorHandler: void 0,
			warnHandler: void 0,
			compilerOptions: {}
		},
		mixins: [],
		components: {},
		directives: {},
		provides: /* @__PURE__ */ Object.create(null),
		optionsCache: /* @__PURE__ */ new WeakMap(),
		propsCache: /* @__PURE__ */ new WeakMap(),
		emitsCache: /* @__PURE__ */ new WeakMap()
	};
}
var qr = 0;
function Jr(e, t) {
	return function(n, r = null) {
		h(n) || (n = s({}, n)), r != null && !v(r) && (r = null);
		let i = Kr(), a = /* @__PURE__ */ new WeakSet(), o = [], c = !1, l = i.app = {
			_uid: qr++,
			_component: n,
			_props: r,
			_container: null,
			_context: i,
			_instance: null,
			version: Da,
			get config() {
				return i.config;
			},
			set config(e) {},
			use(e, ...t) {
				return a.has(e) || (e && h(e.install) ? (a.add(e), e.install(l, ...t)) : h(e) && (a.add(e), e(l, ...t))), l;
			},
			mixin(e) {
				return l;
			},
			component(e, t) {
				return t ? (i.components[e] = t, l) : i.components[e];
			},
			directive(e, t) {
				return t ? (i.directives[e] = t, l) : i.directives[e];
			},
			mount(a, o, s) {
				if (!c) {
					let u = l._ceVNode || Xi(n, r);
					return u.appContext = i, s === !0 ? s = "svg" : s === !1 && (s = void 0), o && t ? t(u, a) : e(u, a, s), c = !0, l._container = a, a.__vue_app__ = l, wa(u.component);
				}
			},
			onUnmount(e) {
				o.push(e);
			},
			unmount() {
				c && (mn(o, l._instance, 16), e(null, l._container), delete l._container.__vue_app__);
			},
			provide(e, t) {
				return i.provides[e] = t, l;
			},
			runWithContext(e) {
				let t = Yr;
				Yr = l;
				try {
					return e();
				} finally {
					Yr = t;
				}
			}
		};
		return l;
	};
}
var Yr = null, Xr = (e, t) => t === "modelValue" || t === "model-value" ? e.modelModifiers : e[`${t}Modifiers`] || e[`${O(t)}Modifiers`] || e[`${te(t)}Modifiers`];
function Zr(e, n, ...r) {
	if (e.isUnmounted) return;
	let i = e.vnode.props || t, a = r, o = n.startsWith("update:"), s = o && Xr(i, n.slice(7));
	s && (s.trim && (a = r.map((e) => g(e) ? e.trim() : e)), s.number && (a = a.map(se)));
	let c, l = i[c = re(n)] || i[c = re(O(n))];
	!l && o && (l = i[c = re(te(n))]), l && mn(l, e, 6, a);
	let u = i[c + "Once"];
	if (u) {
		if (!e.emitted) e.emitted = {};
		else if (e.emitted[c]) return;
		e.emitted[c] = !0, mn(u, e, 6, a);
	}
}
function Qr(e, t, n = !1) {
	let r = t.emitsCache, i = r.get(e);
	if (i !== void 0) return i;
	let a = e.emits, o = {};
	return a ? (d(a) ? a.forEach((e) => o[e] = null) : s(o, a), v(e) && r.set(e, o), o) : (v(e) && r.set(e, null), null);
}
function $r(e, t) {
	return !e || !a(t) ? !1 : (t = t.slice(2), t = t === "Once" ? t : t.replace(/Once$/, ""), u(e, t[0].toLowerCase() + t.slice(1)) || u(e, te(t)) || u(e, t));
}
function ei(e) {
	let { type: t, vnode: n, proxy: r, withProxy: i, propsOptions: [a], slots: s, attrs: c, emit: l, render: u, renderCache: d, props: f, data: p, setupState: m, ctx: h, inheritAttrs: g } = e, _ = Fn(e), v, y;
	try {
		if (n.shapeFlag & 4) {
			let e = i || r, t = e;
			v = na(u.call(t, e, d, f, m, p, h)), y = c;
		} else {
			let e = t;
			v = na(e.length > 1 ? e(f, {
				attrs: c,
				slots: s,
				emit: l
			}) : e(f, null)), y = t.props ? c : ti(c);
		}
	} catch (t) {
		Bi.length = 0, hn(t, e, 1), v = Xi(Ri);
	}
	let b = v;
	if (y && g !== !1) {
		let e = Object.keys(y), { shapeFlag: t } = b;
		e.length && t & 7 && (a && e.some(o) && (y = ni(y, a)), b = $i(b, y, !1, !0));
	}
	return n.dirs && (b = $i(b, null, !1, !0), b.dirs = b.dirs ? b.dirs.concat(n.dirs) : n.dirs), n.transition && yr(Jn(b.type) && vr(b) || b, n.transition), v = b, Fn(_), v;
}
var ti = (e) => {
	let t;
	for (let n in e) (n === "class" || n === "style" || a(n)) && ((t ||= {})[n] = e[n]);
	return t;
}, ni = (e, t) => {
	let n = {};
	for (let r in e) (!o(r) || !(r.slice(9) in t)) && (n[r] = e[r]);
	return n;
};
function ri(e, t, n) {
	let { props: r, children: i, component: a } = e, { props: o, children: s, patchFlag: c } = t, l = a.emitsOptions;
	if (t.dirs || t.transition) return !0;
	if (n && c >= 0) {
		if (c & 1024) return !0;
		if (c & 16) return r ? ii(r, o, l) : !!o;
		if (c & 8) {
			let e = t.dynamicProps;
			for (let t = 0; t < e.length; t++) {
				let n = e[t];
				if (ai(o, r, n) && !$r(l, n)) return !0;
			}
		}
	} else return (i || s) && (!s || !s.$stable) ? !0 : r === o ? !1 : r ? !o || ii(r, o, l) : !!o;
	return !1;
}
function ii(e, t, n) {
	let r = Object.keys(t);
	if (r.length !== Object.keys(e).length) return !0;
	for (let i = 0; i < r.length; i++) {
		let a = r[i];
		if (ai(t, e, a) && !$r(n, a)) return !0;
	}
	return !1;
}
function ai(e, t, n) {
	let r = e[n], i = t[n];
	return n === "style" && v(r) && v(i) ? !Ce(r, i) : r !== i;
}
function oi({ vnode: e, parent: t, suspense: n }, r) {
	for (; t;) {
		let n = t.subTree;
		if (n.suspense && n.suspense.activeBranch === e && (n.suspense.vnode.el = n.el = r, e = n), n === e) (e = t.vnode).el = r, t = t.parent;
		else break;
	}
	n && n.activeBranch === e && (n.vnode.el = r);
}
var si = {}, ci = () => Object.create(si), li = (e) => Object.getPrototypeOf(e) === si;
function ui(e, t, n, r = !1) {
	let i = {}, a = ci();
	e.propsDefaults = /* @__PURE__ */ Object.create(null), fi(e, t, i, a);
	for (let t in e.propsOptions[0]) t in i || (i[t] = void 0);
	e.props = n ? r ? i : /* @__PURE__ */ Rt(i) : e.type.props ? i : a, e.attrs = a;
}
function di(e, t, n, r) {
	let { props: i, attrs: a, vnode: { patchFlag: o } } = e, s = /* @__PURE__ */ A(i), [c] = e.propsOptions, l = !1;
	if ((r || o > 0) && !(o & 16)) {
		if (o & 8) {
			let n = e.vnode.dynamicProps;
			for (let r = 0; r < n.length; r++) {
				let o = n[r];
				if ($r(e.emitsOptions, o)) continue;
				let d = t[o];
				if (c) {
					if (u(a, o)) d !== a[o] && (a[o] = d, l = !0);
					else {
						let t = O(o);
						i[t] = pi(c, s, t, d, e, !1);
					}
				} else d !== a[o] && (a[o] = d, l = !0);
			}
		}
	} else {
		fi(e, t, i, a) && (l = !0);
		let r;
		for (let a in s) (!t || !u(t, a) && ((r = te(a)) === a || !u(t, r))) && (c ? n && (n[a] !== void 0 || n[r] !== void 0) && (i[a] = pi(c, s, a, void 0, e, !0)) : delete i[a]);
		if (a !== s) for (let e in a) (!t || !u(t, e)) && (delete a[e], l = !0);
	}
	l && nt(e.attrs, "set", "");
}
function fi(e, n, r, i) {
	let [a, o] = e.propsOptions, s = !1, c;
	if (n) for (let t in n) {
		if (T(t)) continue;
		let l = n[t], d;
		a && u(a, d = O(t)) ? !o || !o.includes(d) ? r[d] = l : (c ||= {})[d] = l : $r(e.emitsOptions, t) || (!(t in i) || l !== i[t]) && (i[t] = l, s = !0);
	}
	if (o) {
		let n = /* @__PURE__ */ A(r), i = c || t;
		for (let t = 0; t < o.length; t++) {
			let s = o[t];
			r[s] = pi(a, n, s, i[s], e, !u(i, s));
		}
	}
	return s;
}
function pi(e, t, n, r, i, a) {
	let o = e[n];
	if (o != null) {
		let e = u(o, "default");
		if (e && r === void 0) {
			let e = o.default;
			if (o.type !== Function && !o.skipFactory && h(e)) {
				let { propsDefaults: a } = i;
				if (n in a) r = a[n];
				else {
					let o = ma(i);
					r = a[n] = e.call(null, t), o();
				}
			} else r = e;
			i.ce && i.ce._setProp(n, r);
		}
		o[0] && (a && !e ? r = !1 : o[1] && (r === "" || r === te(n)) && (r = !0));
	}
	return r;
}
function mi(e, r, i = !1) {
	let a = r.propsCache, o = a.get(e);
	if (o) return o;
	let c = e.props, l = {}, f = [];
	if (!c) return v(e) && a.set(e, n), n;
	if (d(c)) for (let e = 0; e < c.length; e++) {
		let n = O(c[e]);
		hi(n) && (l[n] = t);
	}
	else if (c) for (let e in c) {
		let t = O(e);
		if (hi(t)) {
			let n = c[e], r = l[t] = d(n) || h(n) ? { type: n } : s({}, n), i = r.type, a = !1, o = !0;
			if (d(i)) for (let e = 0; e < i.length; ++e) {
				let t = i[e], n = h(t) && t.name;
				if (n === "Boolean") {
					a = !0;
					break;
				}
				n === "String" && (o = !1);
			}
			else a = h(i) && i.name === "Boolean";
			r[0] = a, r[1] = o, (a || u(r, "default")) && f.push(t);
		}
	}
	let p = [l, f];
	return v(e) && a.set(e, p), p;
}
function hi(e) {
	return e[0] !== "$" && !T(e);
}
var gi = (e) => e === "_" || e === "_ctx" || e === "$stable", _i = (e) => d(e) ? e.map(na) : [na(e)], vi = (e, t, n) => {
	if (t._n) return t;
	let r = In((...e) => _i(t(...e)), n);
	return r._c = !1, r;
}, yi = (e, t, n) => {
	let r = e._ctx;
	for (let n in e) {
		if (gi(n)) continue;
		let i = e[n];
		if (h(i)) t[n] = vi(n, i, r);
		else if (i != null) {
			let e = _i(i);
			t[n] = () => e;
		}
	}
}, bi = (e, t) => {
	let n = _i(t);
	e.slots.default = () => n;
}, xi = (e, t, n) => {
	for (let r in t) (n || !gi(r)) && (e[r] = t[r]);
}, Si = (e, t, n) => {
	let r = e.slots = ci();
	if (e.vnode.shapeFlag & 32) {
		let e = t._;
		e ? (xi(r, t, n), n && oe(r, "_", e, !0)) : yi(t, r);
	} else t && bi(e, t);
}, Ci = (e, n, r) => {
	let { vnode: i, slots: a } = e, o = !0, s = t;
	if (i.shapeFlag & 32) {
		let e = n._;
		e ? r && e === 1 ? o = !1 : xi(a, n, r) : (o = !n.$stable, yi(n, a)), s = n;
	} else n && (bi(e, n), s = { default: 1 });
	if (o) for (let e in a) !gi(e) && s[e] == null && delete a[e];
}, wi = Ii;
function Ti(e) {
	return Ei(e);
}
function Ei(e, i) {
	let a = ue();
	a.__VUE__ = !0;
	let { insert: o, remove: s, patchProp: c, createElement: l, createText: u, createComment: d, setText: f, setElementText: p, parentNode: m, nextSibling: h, setScopeId: g = r, insertStaticContent: _ } = e, v = (e, t, r, i = null, a = null, o = null, s = void 0, c = null, l = !!t.dynamicChildren) => {
		if (e === t) return;
		e && !qi(e, t) && (i = ye(e), me(e, a, o, !0), e = null), t.patchFlag === -2 && (l = !1, t.dynamicChildren = null), t.dynamicChildren && e && e.dynamicChildren && e.dynamicChildren.hasOnce && (t.dynamicChildren === n && (t.dynamicChildren = []), t.dynamicChildren.hasOnce = !0);
		let { type: u, ref: d, shapeFlag: f } = t;
		switch (u) {
			case Li:
				y(e, t, r, i);
				break;
			case Ri:
				b(e, t, r, i);
				break;
			case zi:
				e ?? x(t, r, i, s);
				break;
			case P:
				re(e, t, r, i, a, o, s, c, l);
				break;
			default: f & 1 ? w(e, t, r, i, a, o, s, c, l) : f & 6 ? ie(e, t, r, i, a, o, s, c, l) : (f & 64 || f & 128) && u.process(e, t, r, i, a, o, s, c, l, Se);
		}
		d != null && a ? wr(d, e && e.ref, o, t || e, !t) : d == null && e && e.ref != null && wr(e.ref, null, o, e, !0);
	}, y = (e, t, n, r) => {
		if (e == null) o(t.el = u(t.children), n, r);
		else {
			let n = t.el = e.el;
			t.children !== e.children && f(n, t.children);
		}
	}, b = (e, t, n, r) => {
		e == null ? o(t.el = d(t.children || ""), n, r) : t.el = e.el;
	}, x = (e, t, n, r) => {
		[e.el, e.anchor] = _(e.children, t, n, r, e.el, e.anchor);
	}, S = ({ el: e, anchor: t }, n, r) => {
		let i;
		for (; e && e !== t;) i = h(e), o(e, n, r), e = i;
		o(t, n, r);
	}, C = ({ el: e, anchor: t }) => {
		let n;
		for (; e && e !== t;) n = h(e), s(e), e = n;
		s(t);
	}, w = (e, t, n, r, i, a, o, s, c) => {
		if (t.type === "svg" ? o = "svg" : t.type === "math" && (o = "mathml"), e == null) E(t, n, r, i, a, o, s, c);
		else {
			let n = e.el && e.el._isVueCE ? e.el : null;
			try {
				n && n._beginPatch(), ee(e, t, i, a, o, s, c);
			} finally {
				n && n._endPatch();
			}
		}
	}, E = (e, t, n, r, i, a, s, u) => {
		let d, f, { props: m, shapeFlag: h, transition: g, dirs: _ } = e;
		if (d = e.el = l(e.type, a, m && m.is, m), h & 8 ? p(d, e.children) : h & 16 && O(e.children, d, null, r, i, Di(e, a), s, u), _ && Rn(e, null, r, "created"), D(d, e, e.scopeId, s, r), m) {
			for (let e in m) e !== "value" && !T(e) && c(d, e, null, m[e], a, r);
			"value" in m && c(d, "value", null, m.value, a), (f = m.onVnodeBeforeMount) && oa(f, r, e);
		}
		_ && Rn(e, null, r, "beforeMount");
		let v = ki(i, g);
		v && g.beforeEnter(d), o(d, t, n), ((f = m && m.onVnodeMounted) || v || _) && wi(() => {
			try {
				f && oa(f, r, e), v && g.enter(d), _ && Rn(e, null, r, "mounted");
			} finally {}
		}, i);
	}, D = (e, t, n, r, i) => {
		if (n && g(e, n), r) for (let t = 0; t < r.length; t++) g(e, r[t]);
		if (i) {
			let n = i.subTree;
			if (t === n || Fi(n.type) && (n.ssContent === t || n.ssFallback === t)) {
				let t = i.vnode;
				D(e, t, t.scopeId, t.slotScopeIds, i.parent);
			}
		}
	}, O = (e, t, n, r, i, a, o, s, c = 0) => {
		for (let l = c; l < e.length; l++) {
			let c = e[l] = s ? ra(e[l]) : na(e[l]);
			v(null, c, t, n, r, i, a, o, s);
		}
	}, ee = (e, n, r, i, a, o, s) => {
		let l = n.el = e.el, { patchFlag: u, dynamicChildren: d, dirs: f } = n;
		u |= e.patchFlag & 16;
		let m = e.props || t, h = n.props || t, g;
		if (r && Oi(r, !1), (g = h.onVnodeBeforeUpdate) && oa(g, r, n, e), f && Rn(n, e, r, "beforeUpdate"), r && Oi(r, !0), d && (!e.dynamicChildren || e.dynamicChildren.length !== d.length) && (u = 0, s = !1, d = null), (m.innerHTML && h.innerHTML == null || m.textContent && h.textContent == null) && p(l, ""), d ? te(e.dynamicChildren, d, l, r, i, Di(n, a), o) : s || k(e, n, l, null, r, i, Di(n, a), o, !1), u > 0) {
			if (u & 16) ne(l, m, h, r, a);
			else if (u & 2 && m.class !== h.class && c(l, "class", null, h.class, a), u & 4 && c(l, "style", m.style, h.style, a), u & 8) {
				let e = n.dynamicProps;
				for (let t = 0; t < e.length; t++) {
					let n = e[t], i = m[n], o = h[n];
					(o !== i || n === "value") && c(l, n, i, o, a, r);
				}
			}
			u & 1 && e.children !== n.children && p(l, n.children);
		} else !s && d == null && ne(l, m, h, r, a);
		((g = h.onVnodeUpdated) || f) && wi(() => {
			g && oa(g, r, n, e), f && Rn(n, e, r, "updated");
		}, i);
	}, te = (e, t, n, r, i, a, o) => {
		for (let s = 0; s < t.length; s++) {
			let c = e[s], l = t[s], u = c.el && (c.type === P || !qi(c, l) || c.shapeFlag & 198) ? m(c.el) : n;
			v(c, l, u, null, r, i, a, o, !0);
		}
	}, ne = (e, n, r, i, a) => {
		if (n !== r) {
			if (n !== t) for (let t in n) !T(t) && !(t in r) && c(e, t, n[t], null, a, i);
			for (let t in r) {
				if (T(t)) continue;
				let o = r[t], s = n[t];
				o !== s && t !== "value" && c(e, t, s, o, a, i);
			}
			"value" in r && c(e, "value", n.value, r.value, a);
		}
	}, re = (e, t, n, r, i, a, s, c, l) => {
		let d = t.el = e ? e.el : u(""), f = t.anchor = e ? e.anchor : u(""), { patchFlag: p, dynamicChildren: m, slotScopeIds: h } = t;
		h && (c = c ? c.concat(h) : h), e == null ? (o(d, n, r), o(f, n, r), O(t.children || [], n, f, i, a, s, c, l)) : p > 0 && p & 64 && m && e.dynamicChildren && e.dynamicChildren.length === m.length ? (te(e.dynamicChildren, m, n, i, a, s, c), (t.key != null || i && t === i.subTree) && Ai(e, t, !0)) : k(e, t, n, f, i, a, s, c, l);
	}, ie = (e, t, n, r, i, a, o, s, c) => {
		t.slotScopeIds = s, e == null ? t.shapeFlag & 512 ? i.ctx.activate(t, n, r, o, c) : oe(t, n, r, i, a, o, c) : se(e, t, c);
	}, oe = (e, t, n, r, i, a, o) => {
		let s = e.component = la(e, r, i);
		if (Dr(e) && (s.ctx.renderer = Se), va(s, !1, o), s.asyncDep) {
			if (i && i.registerDep(s, ce, o), !e.el) {
				let r = s.subTree = Xi(Ri);
				b(null, r, t, n), e.placeholder = r.el;
			}
		} else ce(s, e, t, n, i, a, o);
	}, se = (e, t, n) => {
		let r = t.component = e.component;
		if (ri(e, t, n)) {
			if (r.asyncDep && !r.asyncResolved) {
				t.el = e.el, le(r, t, n);
				return;
			}
			r.next = t, r.update();
		} else t.el = e.el, r.vnode = t;
	}, ce = (e, t, n, r, i, a, o) => {
		let s = () => {
			if (e.isMounted) {
				let { next: t, bu: n, u: r, parent: s, vnode: c } = e;
				{
					let n = Mi(e);
					if (n) {
						t && (t.el = c.el, le(e, t, o)), n.asyncDep.then(() => {
							wi(() => {
								e.isUnmounted || l();
							}, i);
						});
						return;
					}
				}
				let u = t, d;
				Oi(e, !1), t ? (t.el = c.el, le(e, t, o)) : t = c, n && ae(n), (d = t.props && t.props.onVnodeBeforeUpdate) && oa(d, s, t, c), Oi(e, !0);
				let f = ei(e), p = e.subTree;
				e.subTree = f, v(p, f, m(p.el), ye(p), e, i, a), t.el = f.el, u === null && oi(e, f.el), r && wi(r, i), (d = t.props && t.props.onVnodeUpdated) && wi(() => oa(d, s, t, c), i);
			} else {
				let o, { el: s, props: c } = t, { bm: l, m: u, parent: d, root: f, type: p } = e, m = Er(t);
				if (Oi(e, !1), l && ae(l), !m && (o = c && c.onVnodeBeforeMount) && oa(o, d, t), Oi(e, !0), s && we) {
					let t = () => {
						e.subTree = ei(e), we(s, e.subTree, e, i, null);
					};
					m && p.__asyncHydrate ? p.__asyncHydrate(s, e, t) : t();
				} else {
					f.ce && f.ce._hasShadowRoot() && f.ce._injectChildStyle(p, e.parent ? e.parent.type : void 0);
					let o = e.subTree = ei(e);
					v(null, o, n, r, e, i, a), t.el = o.el;
				}
				if (u && wi(u, i), !m && (o = c && c.onVnodeMounted)) {
					let e = t;
					wi(() => oa(o, d, e), i);
				}
				(t.shapeFlag & 256 || d && Er(d.vnode) && d.vnode.shapeFlag & 256) && e.a && wi(e.a, i), e.isMounted = !0, t = n = r = null;
			}
		};
		e.scope.on();
		let c = e.effect = new ke(s);
		e.scope.off();
		let l = e.update = c.run.bind(c), u = e.job = c.runIfDirty.bind(c);
		u.i = e, u.id = e.uid, c.scheduler = () => En(u), Oi(e, !0), l();
	}, le = (e, t, n) => {
		t.component = e;
		let r = e.vnode.props;
		e.vnode = t, e.next = null, di(e, t.props, r, n), Ci(e, t.children, n), We(), kn(e), Ge();
	}, k = (e, t, n, r, i, a, o, s, c = !1) => {
		let l = e && e.children, u = e ? e.shapeFlag : 0, d = t.children, { patchFlag: f, shapeFlag: m } = t;
		if (f > 0) {
			if (f & 128) {
				fe(l, d, n, r, i, a, o, s, c);
				return;
			}
			if (f & 256) {
				de(l, d, n, r, i, a, o, s, c);
				return;
			}
		}
		m & 8 ? (u & 16 && ve(l, i, a), d !== l && p(n, d)) : u & 16 ? m & 16 ? fe(l, d, n, r, i, a, o, s, c) : ve(l, i, a, !0) : (u & 8 && p(n, ""), m & 16 && O(d, n, r, i, a, o, s, c));
	}, de = (e, t, r, i, a, o, s, c, l) => {
		e ||= n, t ||= n;
		let u = e.length, d = t.length, f = Math.min(u, d), p = 0;
		for (; p < f; p++) {
			let n = t[p] = l ? ra(t[p]) : na(t[p]);
			v(e[p], n, r, null, a, o, s, c, l);
		}
		u > d ? ve(e, a, o, !0, !1, f) : O(t, r, i, a, o, s, c, l, f);
	}, fe = (e, t, r, i, a, o, s, c, l) => {
		let u = 0, d = t.length, f = e.length - 1, p = d - 1;
		for (; u <= f && u <= p;) {
			let n = e[u], i = t[u] = l ? ra(t[u]) : na(t[u]);
			if (qi(n, i)) v(n, i, r, null, a, o, s, c, l);
			else break;
			u++;
		}
		for (; u <= f && u <= p;) {
			let n = e[f], i = t[p] = l ? ra(t[p]) : na(t[p]);
			if (qi(n, i)) v(n, i, r, null, a, o, s, c, l);
			else break;
			f--, p--;
		}
		if (u > f) {
			if (u <= p) {
				let e = p + 1, n = e < d ? t[e].el : i;
				for (; u <= p;) v(null, t[u] = l ? ra(t[u]) : na(t[u]), r, n, a, o, s, c, l), u++;
			}
		} else if (u > p) for (; u <= f;) me(e[u], a, o, !0), u++;
		else {
			let m = u, h = u, g = /* @__PURE__ */ new Map();
			for (u = h; u <= p; u++) {
				let e = t[u] = l ? ra(t[u]) : na(t[u]);
				e.key != null && g.set(e.key, u);
			}
			let _, y = 0, b = p - h + 1, x = !1, S = 0, C = Array(b);
			for (u = 0; u < b; u++) C[u] = 0;
			for (u = m; u <= f; u++) {
				let n = e[u];
				if (y >= b) {
					me(n, a, o, !0);
					continue;
				}
				let i;
				if (n.key != null) i = g.get(n.key);
				else for (_ = h; _ <= p; _++) if (C[_ - h] === 0 && qi(n, t[_])) {
					i = _;
					break;
				}
				i === void 0 ? me(n, a, o, !0) : (C[i - h] = u + 1, i >= S ? S = i : x = !0, v(n, t[i], r, null, a, o, s, c, l), y++);
			}
			let w = x ? ji(C) : n;
			for (_ = w.length - 1, u = b - 1; u >= 0; u--) {
				let e = h + u, n = t[e], f = t[e + 1], p = e + 1 < d ? f.el || Pi(f) : i;
				C[u] === 0 ? v(null, n, r, p, a, o, s, c, l) : x && (_ < 0 || u !== w[_] ? pe(n, r, p, 2) : _--);
			}
		}
	}, pe = (e, t, n, r, i = null) => {
		let { el: a, type: c, transition: l, children: u, shapeFlag: d } = e;
		if (d & 6) {
			pe(e.component.subTree, t, n, r);
			return;
		}
		if (d & 128) {
			e.suspense.move(t, n, r);
			return;
		}
		if (d & 64) {
			c.move(e, t, n, Se);
			return;
		}
		if (c === P) {
			o(a, t, n);
			for (let e = 0; e < u.length; e++) pe(u[e], t, n, r);
			o(e.anchor, t, n);
			return;
		}
		if (c === zi) {
			S(e, t, n);
			return;
		}
		if (r !== 2 && d & 1 && l) {
			if (r === 0) l.persisted && !a[or] ? o(a, t, n) : (l.beforeEnter(a), o(a, t, n), wi(() => l.enter(a), i));
			else {
				let { leave: r, delayLeave: i, afterLeave: c } = l, u = () => {
					e.ctx.isUnmounted ? s(a) : o(a, t, n);
				}, d = () => {
					let e = a._isLeaving || !!a[or];
					a._isLeaving && a[or](!0), l.persisted && !e ? u() : r(a, () => {
						u(), c && c();
					});
				};
				i ? i(a, u, d) : d();
			}
		} else o(a, t, n);
	}, me = (e, t, n, r = !1, i = !1) => {
		let { type: a, props: o, ref: s, children: c, dynamicChildren: l, shapeFlag: u, patchFlag: d, dirs: f, cacheIndex: p, memo: m } = e;
		if ((d === -2 || l && l.hasOnce) && (i = !1), s != null && (We(), wr(s, null, n, e, !0), Ge()), p != null && (!e.ctx || e.ctx === t) && (t.renderCache[p] = void 0), u & 256) {
			t.ctx.deactivate(e);
			return;
		}
		let h = u & 1 && f, g = !Er(e), _;
		if (g && (_ = o && o.onVnodeBeforeUnmount) && oa(_, t, e), u & 6) _e(e.component, n, r);
		else {
			if (u & 128) {
				e.suspense.unmount(n, r);
				return;
			}
			h && Rn(e, null, t, "beforeUnmount"), u & 64 ? e.type.remove(e, t, n, Se, r) : l && !l.hasOnce && (a !== P || d > 0 && d & 64) ? ve(l, t, n, !1, !0) : (a === P && d & 384 || !i && u & 16) && ve(c, t, n), r && he(e);
		}
		let v = m != null && p == null;
		(g && (_ = o && o.onVnodeUnmounted) || h || v) && wi(() => {
			_ && oa(_, t, e), h && Rn(e, null, t, "unmounted"), v && (e.el = null);
		}, n);
	}, he = (e) => {
		let { type: t, el: n, anchor: r, transition: i } = e;
		if (t === P) {
			ge(n, r);
			return;
		}
		if (t === zi) {
			C(e), i && !i.persisted && i.afterLeave && i.afterLeave();
			return;
		}
		let a = () => {
			s(n), i && !i.persisted && i.afterLeave && i.afterLeave();
		};
		if (e.shapeFlag & 1 && i && !i.persisted) {
			let { leave: t, delayLeave: r } = i, o = () => t(n, a);
			r ? r(e.el, a, o) : o();
		} else a();
	}, ge = (e, t) => {
		let n;
		for (; e !== t;) n = h(e), s(e), e = n;
		s(t);
	}, _e = (e, t, n) => {
		let { bum: r, scope: i, job: a, subTree: o, um: s, m: c, a: l } = e;
		Ni(c), Ni(l), r && ae(r), i.stop(), a ? (a.flags |= 8, me(o, e, t, n)) : e.vnode.el && o && (o.transition = e.vnode.transition, me(o, e, t, n)), s && wi(s, t), wi(() => {
			e.isUnmounted = !0;
		}, t);
	}, ve = (e, t, n, r = !1, i = !1, a = 0) => {
		for (let o = a; o < e.length; o++) me(e[o], t, n, r, i);
	}, ye = (e) => {
		if (e.shapeFlag & 6) return ye(e.component.subTree);
		if (e.shapeFlag & 128) return e.suspense.next();
		let t = h(e.anchor || e.el), n = t && t[qn];
		return n ? h(n) : t;
	}, be = !1, xe = (e, t, n) => {
		let r;
		e == null ? t._vnode && (me(t._vnode, null, null, !0), r = t._vnode.component) : v(t._vnode || null, e, t, null, null, null, n), t._vnode = e, be ||= (be = !0, kn(r), An(), !1);
	}, Se = {
		p: v,
		um: me,
		m: pe,
		r: he,
		mt: oe,
		mc: O,
		pc: k,
		pbc: te,
		n: ye,
		o: e
	}, Ce, we;
	return i && ([Ce, we] = i(Se)), {
		render: xe,
		hydrate: Ce,
		createApp: Jr(xe, Ce)
	};
}
function Di({ type: e, props: t }, n) {
	return n === "svg" && e === "foreignObject" || n === "mathml" && e === "annotation-xml" && t && t.encoding && t.encoding.includes("html") ? void 0 : n;
}
function Oi({ effect: e, job: t }, n) {
	n ? (e.flags |= 32, t.flags |= 4) : (e.flags &= -33, t.flags &= -5);
}
function ki(e, t) {
	return (!e || e && !e.pendingBranch) && t && !t.persisted;
}
function Ai(e, t, n = !1) {
	let r = e.children, i = t.children;
	if (d(r) && d(i)) for (let e = 0; e < r.length; e++) {
		let t = r[e], a = i[e];
		a.shapeFlag & 1 && !a.dynamicChildren && ((a.patchFlag <= 0 || a.patchFlag === 32) && (a = i[e] = ra(i[e]), a.el = t.el), !n && a.patchFlag !== -2 && Ai(t, a)), a.type === Li && (a.patchFlag === -1 && (a = i[e] = ra(a)), a.el = t.el), a.type === Ri && !a.el && (a.el = t.el);
	}
}
function ji(e) {
	let t = e.slice(), n = [0], r, i, a, o, s, c = e.length;
	for (r = 0; r < c; r++) {
		let c = e[r];
		if (c !== 0) {
			if (i = n[n.length - 1], e[i] < c) {
				t[r] = i, n.push(r);
				continue;
			}
			for (a = 0, o = n.length - 1; a < o;) s = a + o >> 1, e[n[s]] < c ? a = s + 1 : o = s;
			c < e[n[a]] && (a > 0 && (t[r] = n[a - 1]), n[a] = r);
		}
	}
	for (a = n.length, o = n[a - 1]; a-- > 0;) n[a] = o, o = t[o];
	return n;
}
function Mi(e) {
	let t = e.subTree.component;
	if (t) return t.asyncDep && !t.asyncResolved ? t : Mi(t);
}
function Ni(e) {
	if (e) for (let t = 0; t < e.length; t++) e[t].flags |= 8;
}
function Pi(e) {
	if (e.placeholder) return e.placeholder;
	let t = e.component;
	return t ? Pi(t.subTree) : null;
}
var Fi = (e) => e.__isSuspense;
function Ii(e, t) {
	t && t.pendingBranch ? d(e) ? t.effects.push(...e) : t.effects.push(e) : On(e);
}
var P = /* @__PURE__ */ Symbol.for("v-fgt"), Li = /* @__PURE__ */ Symbol.for("v-txt"), Ri = /* @__PURE__ */ Symbol.for("v-cmt"), zi = /* @__PURE__ */ Symbol.for("v-stc"), Bi = [], Vi = null;
function F(e = !1) {
	Bi.push(Vi = e ? null : []);
}
function Hi() {
	Bi.pop(), Vi = Bi[Bi.length - 1] || null;
}
var Ui = 1;
function Wi(e, t = !1) {
	Ui += e, e < 0 && Vi && t && (Vi.hasOnce = !0);
}
function Gi(e) {
	return e.dynamicChildren = Ui > 0 ? Vi || n : null, Hi(), Ui > 0 && Vi && Vi.push(e), e;
}
function I(e, t, n, r, i, a) {
	return Gi(R(e, t, n, r, i, a, !0));
}
function L(e, t, n, r, i) {
	return Gi(Xi(e, t, n, r, i, !0));
}
function Ki(e) {
	return e ? e.__v_isVNode === !0 : !1;
}
function qi(e, t) {
	return e.type === t.type && e.key === t.key;
}
var Ji = ({ key: e }) => e ?? null, Yi = ({ ref: e, ref_key: t, ref_for: n }) => (typeof e == "number" && (e = "" + e), e == null ? null : g(e) || /* @__PURE__ */ Jt(e) || h(e) ? {
	i: Nn,
	r: e,
	k: t,
	f: !!n
} : e);
function R(e, t = null, n = null, r = 0, i = null, a = e === P ? 0 : 1, o = !1, s = !1) {
	let c = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e,
		props: t,
		key: t && Ji(t),
		ref: t && Yi(t),
		scopeId: Pn,
		slotScopeIds: null,
		children: n,
		component: null,
		suspense: null,
		ssContent: null,
		ssFallback: null,
		dirs: null,
		transition: null,
		el: null,
		anchor: null,
		target: null,
		targetStart: null,
		targetAnchor: null,
		staticCount: 0,
		shapeFlag: a,
		patchFlag: r,
		dynamicProps: i,
		dynamicChildren: null,
		appContext: null,
		ctx: Nn
	};
	return s ? (ia(c, n), a & 128 && e.normalize(c)) : n && (c.shapeFlag |= g(n) ? 8 : 16), Ui > 0 && !o && Vi && (c.patchFlag > 0 || a & 6) && c.patchFlag !== 32 && Vi.push(c), c;
}
var Xi = Zi;
function Zi(e, t = null, n = null, r = 0, i = null, a = !1) {
	if ((!e || e === zr) && (e = Ri), Ki(e)) {
		let r = $i(e, t, !0);
		return n && ia(r, n), Ui > 0 && !a && Vi && (r.shapeFlag & 6 ? Vi[Vi.indexOf(e)] = r : Vi.push(r)), r.patchFlag = -2, r;
	}
	if (Ta(e) && (e = e.__vccOpts), t) {
		t = Qi(t);
		let { class: e, style: n } = t;
		e && !g(e) && (t.class = he(e)), v(n) && (/* @__PURE__ */ Wt(n) && !d(n) && (n = s({}, n)), t.style = k(n));
	}
	let o = g(e) ? 1 : Fi(e) ? 128 : Jn(e) ? 64 : v(e) ? 4 : h(e) ? 2 : 0;
	return R(e, t, n, r, i, o, a, !0);
}
function Qi(e) {
	return e ? /* @__PURE__ */ Wt(e) || li(e) ? s({}, e) : e : null;
}
function $i(e, t, n = !1, r = !1) {
	let { props: i, ref: a, patchFlag: o, children: s, transition: c } = e, l = t ? aa(i || {}, t) : i, u = {
		__v_isVNode: !0,
		__v_skip: !0,
		type: e.type,
		props: l,
		key: l && Ji(l),
		ref: t && t.ref ? n && a ? d(a) ? a.concat(Yi(t)) : [a, Yi(t)] : Yi(t) : a,
		scopeId: e.scopeId,
		slotScopeIds: e.slotScopeIds,
		children: s,
		target: e.target,
		targetStart: e.targetStart,
		targetAnchor: e.targetAnchor,
		staticCount: e.staticCount,
		shapeFlag: e.shapeFlag,
		patchFlag: t && e.type !== P ? o === -1 ? 16 : o | 16 : o,
		dynamicProps: e.dynamicProps,
		dynamicChildren: e.dynamicChildren,
		appContext: e.appContext,
		dirs: e.dirs,
		transition: c,
		component: e.component,
		suspense: e.suspense,
		ssContent: e.ssContent && $i(e.ssContent),
		ssFallback: e.ssFallback && $i(e.ssFallback),
		placeholder: e.placeholder,
		el: e.el,
		anchor: e.anchor,
		ctx: e.ctx,
		ce: e.ce,
		cacheIndex: e.cacheIndex
	};
	return c && r && yr(u, c.clone(u)), u;
}
function ea(e = " ", t = 0) {
	return Xi(Li, null, e, t);
}
function ta(e = "", t = !1) {
	return t ? (F(), L(Ri, null, e)) : Xi(Ri, null, e);
}
function na(e) {
	return e == null || typeof e == "boolean" ? Xi(Ri) : d(e) ? Xi(P, null, e.slice()) : Ki(e) ? ra(e) : Xi(Li, null, String(e));
}
function ra(e) {
	return e.el === null && e.patchFlag !== -1 || e.memo ? e : $i(e);
}
function ia(e, t) {
	let n = 0, { shapeFlag: r } = e;
	if (t == null) t = null;
	else if (d(t)) n = 16;
	else if (typeof t == "object") {
		if (r & 65) {
			let n = t.default;
			n && (n._c && (n._d = !1), ia(e, n()), n._c && (n._d = !0));
			return;
		}
		{
			n = 32;
			let r = t._;
			!r && !li(t) ? t._ctx = Nn : r === 3 && Nn && (Nn.slots._ === 1 ? t._ = 1 : (t._ = 2, e.patchFlag |= 1024));
		}
	} else if (h(t)) {
		if (r & 65) {
			ia(e, { default: t });
			return;
		}
		t = {
			default: t,
			_ctx: Nn
		}, n = 32;
	} else t = String(t), r & 64 ? (n = 16, t = [ea(t)]) : n = 8;
	e.children = t, e.shapeFlag |= n;
}
function aa(...e) {
	let t = {};
	for (let n = 0; n < e.length; n++) {
		let r = e[n];
		for (let e in r) if (e === "class") t.class !== r.class && (t.class = he([t.class, r.class]));
		else if (e === "style") t.style = k([t.style, r.style]);
		else if (a(e)) {
			let n = t[e], i = r[e];
			i && n !== i && !(d(n) && n.includes(i)) ? t[e] = n ? [].concat(n, i) : i : i == null && n == null && !o(e) && (t[e] = i);
		} else e !== "" && (t[e] = r[e]);
	}
	return t;
}
function oa(e, t, n, r = null) {
	mn(e, t, 7, [n, r]);
}
var sa = Kr(), ca = 0;
function la(e, n, r) {
	let i = e.type, a = (n ? n.appContext : e.appContext) || sa, o = {
		uid: ca++,
		vnode: e,
		type: i,
		parent: n,
		appContext: a,
		root: null,
		next: null,
		subTree: null,
		effect: null,
		update: null,
		job: null,
		scope: new Te(!0),
		render: null,
		proxy: null,
		exposed: null,
		exposeProxy: null,
		withProxy: null,
		provides: n ? n.provides : Object.create(a.provides),
		ids: n ? n.ids : [
			"",
			0,
			0
		],
		accessCache: null,
		renderCache: [],
		components: null,
		directives: null,
		propsOptions: mi(i, a),
		emitsOptions: Qr(i, a),
		emit: null,
		emitted: null,
		propsDefaults: t,
		inheritAttrs: i.inheritAttrs,
		ctx: t,
		data: t,
		props: t,
		attrs: t,
		slots: t,
		refs: t,
		setupState: t,
		setupContext: null,
		suspense: r,
		suspenseId: r ? r.pendingId : 0,
		asyncDep: null,
		asyncResolved: !1,
		isMounted: !1,
		isUnmounted: !1,
		isDeactivated: !1,
		bc: null,
		c: null,
		bm: null,
		m: null,
		bu: null,
		u: null,
		um: null,
		bum: null,
		da: null,
		a: null,
		rtg: null,
		rtc: null,
		ec: null,
		sp: null
	};
	return o.ctx = { _: o }, o.root = n ? n.root : o, o.emit = Zr.bind(null, o), e.ce && e.ce(o), o;
}
var ua = null, da = () => ua || Nn, fa, pa;
{
	let e = ue(), t = (t, n) => {
		let r;
		return (r = e[t]) || (r = e[t] = []), r.push(n), (e) => {
			r.length > 1 ? r.forEach((t) => t(e)) : r[0](e);
		};
	};
	fa = t("__VUE_INSTANCE_SETTERS__", (e) => ua = e), pa = t("__VUE_SSR_SETTERS__", (e) => _a = e);
}
var ma = (e) => {
	let t = ua;
	return fa(e), e.scope.on(), () => {
		e.scope.off(), fa(t);
	};
}, ha = () => {
	ua && ua.scope.off(), fa(null);
};
function ga(e) {
	return e.vnode.shapeFlag & 4;
}
var _a = !1;
function va(e, t = !1, n = !1) {
	t && pa(t);
	let { props: r, children: i } = e.vnode, a = ga(e);
	ui(e, r, a, t), Si(e, i, n || t);
	let o = a ? ya(e, t) : void 0;
	return t && pa(!1), o;
}
function ya(e, t) {
	let n = e.type;
	e.accessCache = /* @__PURE__ */ Object.create(null), e.proxy = new Proxy(e.ctx, Gr);
	let { setup: r } = n;
	if (r) {
		We();
		let n = e.setupContext = r.length > 1 ? Ca(e) : null, i = ma(e), a = pn(r, e, 0, [e.props, n]), o = y(a);
		if (Ge(), i(), (o || e.sp) && !Er(e) && xr(e), o) {
			if (a.then(ha, ha), t) return a.then((n) => {
				pa(!0);
				try {
					ba(e, n, t);
				} finally {
					pa(!1);
				}
			}).catch((t) => {
				hn(t, e, 0);
			});
			e.asyncDep = a;
		} else ba(e, a, t);
	} else xa(e, t);
}
function ba(e, t, n) {
	h(t) ? e.type.__ssrInlineRender ? e.ssrRender = t : e.render = t : v(t) && (e.setupState = en(t)), xa(e, n);
}
function xa(e, t, n) {
	let i = e.type;
	e.render ||= i.render || r;
}
var Sa = { get(e, t) {
	return tt(e, "get", ""), e[t];
} };
function Ca(e) {
	return {
		attrs: new Proxy(e.attrs, Sa),
		slots: e.slots,
		emit: e.emit,
		expose: (t) => {
			e.exposed = t || {};
		}
	};
}
function wa(e) {
	return e.exposed ? e.exposeProxy ||= new Proxy(en(Gt(e.exposed)), {
		get(t, n) {
			if (n in t) return t[n];
			if (n in Ur) return Ur[n](e);
		},
		has(e, t) {
			return t in e || t in Ur;
		}
	}) : e.proxy;
}
function Ta(e) {
	return h(e) && "__vccOpts" in e;
}
var z = (e, t) => /* @__PURE__ */ on(e, t, _a);
function Ea(e, t, n) {
	try {
		Wi(-1);
		let r = arguments.length;
		return r === 2 ? v(t) && !d(t) ? Ki(t) ? Xi(e, null, [t]) : Xi(e, t) : Xi(e, null, t) : (r > 3 ? n = Array.prototype.slice.call(arguments, 2) : r === 3 && Ki(n) && (n = [n]), Xi(e, t, n));
	} finally {
		Wi(1);
	}
}
var Da = "3.5.43", Oa = void 0, ka = typeof window < "u" && window.trustedTypes;
if (ka) try {
	Oa = /* @__PURE__ */ ka.createPolicy("vue", { createHTML: (e) => e });
} catch {}
var Aa = Oa ? (e) => Oa.createHTML(e) : (e) => e, ja = "http://www.w3.org/2000/svg", Ma = "http://www.w3.org/1998/Math/MathML", Na = typeof document < "u" ? document : null, Pa = Na && /* @__PURE__ */ Na.createElement("template"), Fa = {
	insert: (e, t, n) => {
		t.insertBefore(e, n || null);
	},
	remove: (e) => {
		let t = e.parentNode;
		t && t.removeChild(e);
	},
	createElement: (e, t, n, r) => {
		let i = t === "svg" ? Na.createElementNS(ja, e) : t === "mathml" ? Na.createElementNS(Ma, e) : n ? Na.createElement(e, { is: n }) : Na.createElement(e);
		return e === "select" && r && r.multiple != null && i.setAttribute("multiple", r.multiple), i;
	},
	createText: (e) => Na.createTextNode(e),
	createComment: (e) => Na.createComment(e),
	setText: (e, t) => {
		e.nodeValue = t;
	},
	setElementText: (e, t) => {
		e.textContent = t;
	},
	parentNode: (e) => e.parentNode,
	nextSibling: (e) => e.nextSibling,
	querySelector: (e) => Na.querySelector(e),
	setScopeId(e, t) {
		e.setAttribute(t, "");
	},
	insertStaticContent(e, t, n, r, i, a) {
		let o = n ? n.previousSibling : t.lastChild;
		if (i && (i === a || i.nextSibling)) for (; t.insertBefore(i.cloneNode(!0), n), i !== a && (i = i.nextSibling););
		else {
			Pa.innerHTML = Aa(r === "svg" ? `<svg>${e}</svg>` : r === "mathml" ? `<math>${e}</math>` : e);
			let i = Pa.content;
			if (r === "svg" || r === "mathml") {
				let e = i.firstChild;
				for (; e.firstChild;) i.appendChild(e.firstChild);
				i.removeChild(e);
			}
			t.insertBefore(i, n);
		}
		return [o ? o.nextSibling : t.firstChild, n ? n.previousSibling : t.lastChild];
	}
}, Ia = "transition", La = "animation", Ra = /* @__PURE__ */ Symbol("_vtc"), za = {
	name: String,
	type: String,
	css: {
		type: Boolean,
		default: !0
	},
	duration: [
		String,
		Number,
		Object
	],
	enterFromClass: String,
	enterActiveClass: String,
	enterToClass: String,
	appearFromClass: String,
	appearActiveClass: String,
	appearToClass: String,
	leaveFromClass: String,
	leaveActiveClass: String,
	leaveToClass: String
}, Ba = /* @__PURE__ */ s({}, ur, za), Va = /* @__PURE__ */ ((e) => (e.displayName = "Transition", e.props = Ba, e))((e, { slots: t }) => Ea(mr, Wa(e), t)), Ha = (e, t = []) => {
	d(e) ? e.forEach((e) => e(...t)) : e && e(...t);
}, Ua = (e) => e ? d(e) ? e.some((e) => e.length > 1) : e.length > 1 : !1;
function Wa(e) {
	let t = {};
	for (let n in e) n in za || (t[n] = e[n]);
	if (e.css === !1) return t;
	let { name: n = "v", type: r, duration: i, enterFromClass: a = `${n}-enter-from`, enterActiveClass: o = `${n}-enter-active`, enterToClass: c = `${n}-enter-to`, appearFromClass: l = a, appearActiveClass: u = o, appearToClass: d = c, leaveFromClass: f = `${n}-leave-from`, leaveActiveClass: p = `${n}-leave-active`, leaveToClass: m = `${n}-leave-to` } = e, h = Ga(i), g = h && h[0], _ = h && h[1], { onBeforeEnter: v, onEnter: y, onEnterCancelled: b, onLeave: x, onLeaveCancelled: S, onBeforeAppear: C = v, onAppear: w = y, onAppearCancelled: T = b } = t, E = (e, t, n, r) => {
		e._enterCancelled = r, Ja(e, t ? d : c), Ja(e, t ? u : o), n && n();
	}, D = (e, t) => {
		e._isLeaving = !1, Ja(e, f), Ja(e, m), Ja(e, p), t && t();
	}, O = (e) => (t, n) => {
		let i = e ? w : y, o = () => E(t, e, n);
		Ha(i, [t, o]), Ya(() => {
			Ja(t, e ? l : a), qa(t, e ? d : c), Ua(i) || Za(t, r, g, o);
		});
	};
	return s(t, {
		onBeforeEnter(e) {
			Ha(v, [e]), qa(e, a), qa(e, o);
		},
		onBeforeAppear(e) {
			Ha(C, [e]), qa(e, l), qa(e, u);
		},
		onEnter: O(!1),
		onAppear: O(!0),
		onLeave(e, t) {
			e._isLeaving = !0;
			let n = () => D(e, t);
			qa(e, f), e._enterCancelled ? (qa(e, p), to(e)) : (to(e), qa(e, p)), Ya(() => {
				e._isLeaving && (Ja(e, f), qa(e, m), Ua(x) || Za(e, r, _, n));
			}), Ha(x, [e, n]);
		},
		onEnterCancelled(e) {
			E(e, !1, void 0, !0), Ha(b, [e]);
		},
		onAppearCancelled(e) {
			E(e, !0, void 0, !0), Ha(T, [e]);
		},
		onLeaveCancelled(e) {
			D(e), Ha(S, [e]);
		}
	});
}
function Ga(e) {
	if (e == null) return null;
	if (v(e)) return [Ka(e.enter), Ka(e.leave)];
	{
		let t = Ka(e);
		return [t, t];
	}
}
function Ka(e) {
	return ce(e);
}
function qa(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.add(t)), (e[Ra] || (e[Ra] = /* @__PURE__ */ new Set())).add(t);
}
function Ja(e, t) {
	t.split(/\s+/).forEach((t) => t && e.classList.remove(t));
	let n = e[Ra];
	n && (n.delete(t), n.size || (e[Ra] = void 0));
}
function Ya(e) {
	requestAnimationFrame(() => {
		requestAnimationFrame(e);
	});
}
var Xa = 0;
function Za(e, t, n, r) {
	let i = e._endId = ++Xa, a = () => {
		i === e._endId && r();
	};
	if (n != null) return setTimeout(a, n);
	let { type: o, timeout: s, propCount: c } = Qa(e, t);
	if (!o) return r();
	let l = o + "end", u = 0, d = () => {
		e.removeEventListener(l, f), a();
	}, f = (t) => {
		t.target === e && ++u >= c && d();
	};
	setTimeout(() => {
		u < c && d();
	}, s + 1), e.addEventListener(l, f);
}
function Qa(e, t) {
	let n = window.getComputedStyle(e), r = (e) => (n[e] || "").split(", "), i = r(`${Ia}Delay`), a = r(`${Ia}Duration`), o = $a(i, a), s = r(`${La}Delay`), c = r(`${La}Duration`), l = $a(s, c), u = null, d = 0, f = 0;
	t === Ia ? o > 0 && (u = Ia, d = o, f = a.length) : t === La ? l > 0 && (u = La, d = l, f = c.length) : (d = Math.max(o, l), u = d > 0 ? o > l ? Ia : La : null, f = u ? u === Ia ? a.length : c.length : 0);
	let p = u === Ia && /\b(?:transform|all)(?:,|$)/.test(r(`${Ia}Property`).toString());
	return {
		type: u,
		timeout: d,
		propCount: f,
		hasTransform: p
	};
}
function $a(e, t) {
	for (; e.length < t.length;) e = e.concat(e);
	return Math.max(...t.map((t, n) => eo(t) + eo(e[n])));
}
function eo(e) {
	return e === "auto" ? 0 : Number(e.slice(0, -1).replace(",", ".")) * 1e3;
}
function to(e) {
	return (e ? e.ownerDocument : document).body.offsetHeight;
}
function no(e, t, n) {
	let r = e[Ra];
	r && (t = (t ? [t, ...r] : [...r]).join(" ")), t == null ? e.removeAttribute("class") : n ? e.setAttribute("class", t) : e.className = t;
}
var ro = /* @__PURE__ */ Symbol("_vod"), io = /* @__PURE__ */ Symbol("_vsh"), ao = {
	name: "show",
	beforeMount(e, { value: t }, { transition: n }) {
		e[ro] = e.style.display === "none" ? "" : e.style.display, n && t ? n.beforeEnter(e) : oo(e, t);
	},
	mounted(e, { value: t }, { transition: n }) {
		n && t && n.enter(e);
	},
	updated(e, { value: t, oldValue: n }, { transition: r }) {
		!t != !n && (r ? t ? (r.beforeEnter(e), oo(e, !0), r.enter(e)) : r.leave(e, () => {
			oo(e, !1);
		}) : oo(e, t));
	},
	beforeUnmount(e, { value: t }) {
		oo(e, t);
	}
};
function oo(e, t) {
	e.style.display = t ? e[ro] : "none", e[io] = !t;
}
var so = /* @__PURE__ */ Symbol(""), co = /(?:^|;)\s*display\s*:/;
function lo(e, t, n) {
	let r = e.style, i = g(n), a = !1;
	if (n && !i) {
		if (t) {
			if (g(t)) for (let e of t.split(";")) {
				let t = e.slice(0, e.indexOf(":")).trim();
				n[t] ?? fo(r, t, "");
			}
			else for (let e in t) n[e] ?? fo(r, e, "");
		}
		for (let i in n) {
			i === "display" && (a = !0);
			let o = n[i];
			o == null ? fo(r, i, "") : go(e, i, !g(t) && t ? t[i] : void 0, o) || fo(r, i, o);
		}
	} else if (i) {
		if (t !== n) {
			let e = r[so];
			e && (n += ";" + e), r.cssText = n, a = co.test(n);
		}
	} else t && e.removeAttribute("style");
	ro in e && (e[ro] = a ? r.display : "", e[io] && (r.display = "none"));
}
var uo = /\s*!important$/;
function fo(e, t, n) {
	if (d(n)) n.forEach((n) => fo(e, t, n));
	else if (n ??= "", t.startsWith("--")) uo.test(n) ? e.setProperty(t, n.replace(uo, ""), "important") : e.setProperty(t, n);
	else {
		let r = ho(e, t);
		uo.test(n) ? e.setProperty(te(r), n.replace(uo, ""), "important") : e[r] = n;
	}
}
var po = [
	"Webkit",
	"Moz",
	"ms"
], mo = {};
function ho(e, t) {
	let n = mo[t];
	if (n) return n;
	let r = O(t);
	if (r !== "filter" && r in e) return mo[t] = r;
	r = ne(r);
	for (let n = 0; n < po.length; n++) {
		let i = po[n] + r;
		if (i in e) return mo[t] = i;
	}
	return t;
}
function go(e, t, n, r) {
	return e.tagName === "TEXTAREA" && (t === "width" || t === "height") && g(r) && n === r;
}
var _o = "http://www.w3.org/1999/xlink";
function vo(e, t, n, r, i, a = _e(t)) {
	r && t.startsWith("xlink:") ? n == null ? e.removeAttributeNS(_o, t.slice(6, t.length)) : e.setAttributeNS(_o, t, n) : n == null || a && !ve(n) ? e.removeAttribute(t) : e.setAttribute(t, a ? "" : _(n) ? String(n) : n);
}
function yo(e, t, n, r, i) {
	if (t === "innerHTML" || t === "textContent") {
		n != null && (e[t] = t === "innerHTML" ? Aa(n) : n);
		return;
	}
	let a = e.tagName;
	if (t === "value" && a !== "PROGRESS" && !a.includes("-")) {
		let r = a === "OPTION" ? e.getAttribute("value") || "" : e.value, i = n == null ? e.type === "checkbox" ? "on" : "" : String(n);
		(r !== i || !("_value" in e)) && (e.value = i), n ?? e.removeAttribute(t), e._value = n;
		return;
	}
	let o = !1;
	if (n === "" || n == null) {
		let r = typeof e[t];
		r === "boolean" ? n = ve(n) : n == null && r === "string" ? (n = "", o = !0) : r === "number" && (n = 0, o = !0);
	}
	try {
		e[t] = n;
	} catch {}
	o && e.removeAttribute(i || t);
}
function bo(e, t, n, r) {
	e.addEventListener(t, n, r);
}
function xo(e, t, n, r) {
	e.removeEventListener(t, n, r);
}
var So = /* @__PURE__ */ Symbol("_vei");
function Co(e, t, n, r, i = null) {
	let a = e[So] || (e[So] = {}), o = a[t];
	if (r && o) o.value = r;
	else {
		let [n, s] = Eo(t);
		r ? bo(e, n, a[t] = Ao(r, i), s) : o && (xo(e, n, o, s), a[t] = void 0);
	}
}
var wo = /(Once|Passive|Capture)$/, To = /^on:?(?:Once|Passive|Capture)$/;
function Eo(e) {
	let t, n;
	for (; (n = e.match(wo)) && !To.test(e);) t ||= {}, e = e.slice(0, e.length - n[1].length), t[n[1].toLowerCase()] = !0;
	return [e[2] === ":" ? e.slice(3) : te(e.slice(2)), t];
}
var Do = 0, Oo = /* @__PURE__ */ Promise.resolve(), ko = () => Do ||= (Oo.then(() => Do = 0), Date.now());
function Ao(e, t) {
	let n = (e) => {
		if (!e._vts) e._vts = Date.now();
		else if (e._vts <= n.attached) return;
		let r = n.value;
		if (d(r)) {
			let n = e.stopImmediatePropagation;
			e.stopImmediatePropagation = () => {
				n.call(e), e._stopped = !0;
			};
			let i = r.slice(), a = [e];
			for (let n = 0; n < i.length && !e._stopped; n++) {
				let e = i[n];
				e && mn(e, t, 5, a);
			}
		} else mn(r, t, 5, [e]);
	};
	return n.value = e, n.attached = ko(), n;
}
var jo = (e) => e.charCodeAt(0) === 111 && e.charCodeAt(1) === 110 && e.charCodeAt(2) > 96 && e.charCodeAt(2) < 123, Mo = (e, t, n, r, i, s) => {
	let c = i === "svg";
	t === "class" ? no(e, r, c) : t === "style" ? lo(e, n, r) : a(t) ? o(t) || Co(e, t, n, r, s) : (t[0] === "." ? (t = t.slice(1), 1) : t[0] === "^" ? (t = t.slice(1), 0) : No(e, t, r, c)) ? (yo(e, t, r), !e.tagName.includes("-") && (t === "value" || t === "checked" || t === "selected") && vo(e, t, r, c, s, t !== "value")) : e._isVueCE && (Po(e, t) || e._def.__asyncLoader && (/[A-Z]/.test(t) || !g(r))) ? yo(e, O(t), r, s, t) : (t === "true-value" ? e._trueValue = r : t === "false-value" && (e._falseValue = r), vo(e, t, r, c));
};
function No(e, t, n, r) {
	if (r) return !!(t === "innerHTML" || t === "textContent" || t in e && jo(t) && h(n));
	if (t === "spellcheck" || t === "draggable" || t === "translate" || t === "autocorrect" || t === "sandbox" && e.tagName === "IFRAME" || t === "form" || t === "list" && e.tagName === "INPUT" || t === "type" && e.tagName === "TEXTAREA") return !1;
	if (t === "width" || t === "height") {
		let t = e.tagName;
		if (t === "IMG" || t === "VIDEO" || t === "CANVAS" || t === "SOURCE") return !1;
	}
	return jo(t) && g(n) ? !1 : t in e;
}
function Po(e, t) {
	let n = e._def.props;
	if (!n) return !1;
	let r = O(t);
	return Array.isArray(n) ? n.some((e) => O(e) === r) : Object.keys(n).some((e) => O(e) === r);
}
var Fo = /* @__PURE__ */ new WeakMap(), Io = /* @__PURE__ */ new WeakMap(), Lo = /* @__PURE__ */ Symbol("_moveCb"), Ro = /* @__PURE__ */ Symbol("_enterCb"), zo = /* @__PURE__ */ ((e) => (delete e.props.mode, e))({
	name: "TransitionGroup",
	props: /* @__PURE__ */ s({}, Ba, {
		tag: String,
		moveClass: String
	}),
	setup(e, { slots: t }) {
		let n = da(), r = cr(), i, a;
		return Ir(() => {
			if (!i.length) return;
			let t = e.moveClass || `${e.name || "v"}-move`;
			if (!Wo(i[0].el, n.vnode.el, t)) {
				i = [];
				return;
			}
			i.forEach(Bo), i.forEach(Vo);
			let r = i.filter(Ho);
			to(n.vnode.el), r.forEach((e) => {
				let n = e.el, r = n.style;
				qa(n, t), r.transform = r.webkitTransform = r.transitionDuration = "";
				let i = n[Lo] = (e) => {
					e && e.target !== n || (!e || e.propertyName.endsWith("transform")) && (n.removeEventListener("transitionend", i), n[Lo] = null, Ja(n, t));
				};
				n.addEventListener("transitionend", i);
			}), i = [];
		}), () => {
			let o = /* @__PURE__ */ A(e), s = Wa(o), c = o.tag || P;
			if (i = [], a) for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.el && t.el instanceof Element && !t.el[io] && (i.push(t), yr(t, gr(t, s, r, n)), Fo.set(t, Uo(t.el)));
			}
			a = t.default ? br(t.default()) : [];
			for (let e = 0; e < a.length; e++) {
				let t = a[e];
				t.key != null && yr(t, gr(t, s, r, n));
			}
			return Xi(c, null, a);
		};
	}
});
function Bo(e) {
	let t = e.el;
	t[Lo] && t[Lo](), t[Ro] && t[Ro]();
}
function Vo(e) {
	Io.set(e, Uo(e.el));
}
function Ho(e) {
	let t = Fo.get(e), n = Io.get(e), r = t.left - n.left, i = t.top - n.top;
	if (r || i) {
		let t = e.el, n = t.style, a = t.getBoundingClientRect(), o = 1, s = 1;
		return t.offsetWidth && (o = a.width / t.offsetWidth), t.offsetHeight && (s = a.height / t.offsetHeight), (!Number.isFinite(o) || o === 0) && (o = 1), (!Number.isFinite(s) || s === 0) && (s = 1), Math.abs(o - 1) < .01 && (o = 1), Math.abs(s - 1) < .01 && (s = 1), n.transform = n.webkitTransform = `translate(${r / o}px,${i / s}px)`, n.transitionDuration = "0s", e;
	}
}
function Uo(e) {
	let t = e.getBoundingClientRect();
	return {
		left: t.left,
		top: t.top
	};
}
function Wo(e, t, n) {
	let r = e.cloneNode(), i = e[Ra];
	i && i.forEach((e) => {
		e.split(/\s+/).forEach((e) => e && r.classList.remove(e));
	}), n.split(/\s+/).forEach((e) => e && r.classList.add(e)), r.style.display = "none";
	let a = t.nodeType === 1 ? t : t.parentNode;
	a.appendChild(r);
	let { hasTransform: o } = Qa(r);
	return a.removeChild(r), o;
}
var Go = /* @__PURE__ */ s({ patchProp: Mo }, Fa), Ko;
function qo() {
	return Ko ||= Ti(Go);
}
var Jo = ((...e) => {
	let t = qo().createApp(...e), { mount: n } = t;
	return t.mount = (e) => {
		let r = Xo(e);
		if (!r) return;
		let i = t._component;
		!h(i) && !i.render && !i.template && (i.template = r.innerHTML), r.nodeType === 1 && (r.textContent = "");
		let a = n(r, !1, Yo(r));
		return r instanceof Element && (r.removeAttribute("v-cloak"), r.setAttribute("data-v-app", "")), a;
	}, t;
});
function Yo(e) {
	if (e instanceof SVGElement) return "svg";
	if (typeof MathMLElement == "function" && e instanceof MathMLElement) return "mathml";
}
function Xo(e) {
	return g(e) ? document.querySelector(e) : e;
}
//#endregion
//#region node_modules/@css-render/plugin-bem/esm/index.js
function Zo(e) {
	let t = ".", n = "__", r = "--", i;
	if (e) {
		let i = e.blockPrefix;
		i && (t = i), i = e.elementPrefix, i && (n = i), i = e.modifierPrefix, i && (r = i);
	}
	let a = { install(e) {
		i = e.c;
		let t = e.context;
		t.bem = {}, t.bem.b = null, t.bem.els = null;
	} };
	function o(e) {
		let n, r;
		return {
			before(e) {
				n = e.bem.b, r = e.bem.els, e.bem.els = null;
			},
			after(e) {
				e.bem.b = n, e.bem.els = r;
			},
			$({ context: n, props: r }) {
				return e = typeof e == "string" ? e : e({
					context: n,
					props: r
				}), n.bem.b = e, `${r?.bPrefix || t}${n.bem.b}`;
			}
		};
	}
	function s(e) {
		let r;
		return {
			before(e) {
				r = e.bem.els;
			},
			after(e) {
				e.bem.els = r;
			},
			$({ context: r, props: i }) {
				return e = typeof e == "string" ? e : e({
					context: r,
					props: i
				}), r.bem.els = e.split(",").map((e) => e.trim()), r.bem.els.map((e) => `${i?.bPrefix || t}${r.bem.b}${n}${e}`).join(", ");
			}
		};
	}
	function c(e) {
		return { $({ context: i, props: a }) {
			e = typeof e == "string" ? e : e({
				context: i,
				props: a
			});
			let o = e.split(",").map((e) => e.trim());
			function s(e) {
				return o.map((o) => `&${a?.bPrefix || t}${i.bem.b}${e === void 0 ? "" : `${n}${e}`}${r}${o}`).join(", ");
			}
			let c = i.bem.els;
			return c === null ? s() : s(c[0]);
		} };
	}
	function l(e) {
		return { $({ context: i, props: a }) {
			e = typeof e == "string" ? e : e({
				context: i,
				props: a
			});
			let o = i.bem.els;
			return `&:not(${a?.bPrefix || t}${i.bem.b}${o !== null && o.length > 0 ? `${n}${o[0]}` : ""}${r}${e})`;
		} };
	}
	return Object.assign(a, {
		cB: ((...e) => i(o(e[0]), e[1], e[2])),
		cE: ((...e) => i(s(e[0]), e[1], e[2])),
		cM: ((...e) => i(c(e[0]), e[1], e[2])),
		cNotM: ((...e) => i(l(e[0]), e[1], e[2]))
	}), a;
}
//#endregion
//#region node_modules/css-render/esm/parse.js
function Qo(e) {
	let t = 0;
	for (let n = 0; n < e.length; ++n) e[n] === "&" && ++t;
	return t;
}
var $o = /\s*,(?![^(]*\))\s*/g, es = /\s+/g;
function ts(e, t) {
	let n = [];
	return t.split($o).forEach((t) => {
		let r = Qo(t);
		if (!r) {
			e.forEach((e) => {
				n.push((e && e + " ") + t);
			});
			return;
		}
		if (r === 1) {
			e.forEach((e) => {
				n.push(t.replace("&", e));
			});
			return;
		}
		let i = [t];
		for (; r--;) {
			let t = [];
			i.forEach((n) => {
				e.forEach((e) => {
					t.push(n.replace("&", e));
				});
			}), i = t;
		}
		i.forEach((e) => n.push(e));
	}), n;
}
function ns(e, t) {
	let n = [];
	return t.split($o).forEach((t) => {
		e.forEach((e) => {
			n.push((e && e + " ") + t);
		});
	}), n;
}
function rs(e) {
	let t = [""];
	return e.forEach((e) => {
		e &&= e.trim(), e && (t = e.includes("&") ? ts(t, e) : ns(t, e));
	}), t.join(", ").replace(es, " ");
}
//#endregion
//#region node_modules/css-render/esm/utils.js
function is(e) {
	/* istanbul ignore if */
	if (!e) return;
	let t = e.parentElement;
	/* istanbul ignore else */
	t && t.removeChild(e);
}
function as(e, t) {
	return (t ?? document.head).querySelector(`style[cssr-id="${e}"]`);
}
function os(e) {
	let t = document.createElement("style");
	return t.setAttribute("cssr-id", e), t;
}
function ss(e) {
	return e ? /^\s*@(s|m)/.test(e) : !1;
}
//#endregion
//#region node_modules/css-render/esm/render.js
var cs = /[A-Z]/g;
function ls(e) {
	return e.replace(cs, (e) => "-" + e.toLowerCase());
}
function us(e, t = "  ") {
	return typeof e == "object" && e ? " {\n" + Object.entries(e).map((e) => t + `  ${ls(e[0])}: ${e[1]};`).join("\n") + "\n" + t + "}" : `: ${e};`;
}
function ds(e, t, n) {
	return typeof e == "function" ? e({
		context: t.context,
		props: n
	}) : e;
}
function fs(e, t, n, r) {
	if (!t) return "";
	let i = ds(t, n, r);
	if (!i) return "";
	if (typeof i == "string") return `${e} {\n${i}\n}`;
	let a = Object.keys(i);
	if (a.length === 0) return n.config.keepEmptyBlock ? e + " {\n}" : "";
	let o = e ? [e + " {"] : [];
	return a.forEach((e) => {
		let t = i[e];
		if (e === "raw") {
			o.push("\n" + t + "\n");
			return;
		}
		e = ls(e), t != null && o.push(`  ${e}${us(t)}`);
	}), e && o.push("}"), o.join("\n");
}
function ps(e, t, n) {
	/* istanbul ignore if */
	e && e.forEach((e) => {
		if (Array.isArray(e)) ps(e, t, n);
		else if (typeof e == "function") {
			let r = e(t);
			Array.isArray(r) ? ps(r, t, n) : r && n(r);
		} else e && n(e);
	});
}
function ms(e, t, n, r, i) {
	let a = e.$, o = "";
	if (!a || typeof a == "string") ss(a) ? o = a : t.push(a);
	else if (typeof a == "function") {
		let e = a({
			context: r.context,
			props: i
		});
		ss(e) ? o = e : t.push(e);
	} else if (a.before && a.before(r.context), !a.$ || typeof a.$ == "string") ss(a.$) ? o = a.$ : t.push(a.$);
	else if (a.$) {
		let e = a.$({
			context: r.context,
			props: i
		});
		ss(e) ? o = e : t.push(e);
	}
	let s = rs(t), c = fs(s, e.props, r, i);
	o ? n.push(`${o} {`) : c.length && n.push(c), e.children && ps(e.children, {
		context: r.context,
		props: i
	}, (e) => {
		if (typeof e == "string") {
			let t = fs(s, { raw: e }, r, i);
			n.push(t);
		} else ms(e, t, n, r, i);
	}), t.pop(), o && n.push("}"), a && a.after && a.after(r.context);
}
function hs(e, t, n) {
	let r = [];
	return ms(e, [], r, t, n), r.join("\n\n");
}
//#endregion
//#region node_modules/@emotion/hash/dist/hash.browser.esm.js
function gs(e) {
	for (var t = 0, n, r = 0, i = e.length; i >= 4; ++r, i -= 4) n = e.charCodeAt(r) & 255 | (e.charCodeAt(++r) & 255) << 8 | (e.charCodeAt(++r) & 255) << 16 | (e.charCodeAt(++r) & 255) << 24, n = (n & 65535) * 1540483477 + ((n >>> 16) * 59797 << 16), n ^= n >>> 24, t = (n & 65535) * 1540483477 + ((n >>> 16) * 59797 << 16) ^ (t & 65535) * 1540483477 + ((t >>> 16) * 59797 << 16);
	switch (i) {
		case 3: t ^= (e.charCodeAt(r + 2) & 255) << 16;
		case 2: t ^= (e.charCodeAt(r + 1) & 255) << 8;
		case 1: t ^= e.charCodeAt(r) & 255, t = (t & 65535) * 1540483477 + ((t >>> 16) * 59797 << 16);
	}
	return t ^= t >>> 13, t = (t & 65535) * 1540483477 + ((t >>> 16) * 59797 << 16), ((t ^ t >>> 15) >>> 0).toString(36);
}
//#endregion
//#region node_modules/css-render/esm/mount.js
typeof window < "u" && (window.__cssrContext = {});
function _s(e, t, n, r) {
	let { els: i } = t;
	if (n === void 0) i.forEach(is), t.els = [];
	else {
		let e = as(n, r);
		e && i.includes(e) && (is(e), t.els = i.filter((t) => t !== e));
	}
}
function vs(e, t) {
	e.push(t);
}
function ys(e, t, n, r, i, a, o, s, c) {
	let l;
	if (n === void 0 && (l = t.render(r), n = gs(l)), c) {
		c.adapter(n, l ?? t.render(r));
		return;
	}
	s === void 0 && (s = document.head);
	let u = as(n, s);
	if (u !== null && !a) return u;
	let d = u ?? os(n);
	if (l === void 0 && (l = t.render(r)), d.textContent = l, u !== null) return u;
	if (o) {
		let e = s.querySelector(`meta[name="${o}"]`);
		if (e) return s.insertBefore(d, e), vs(t.els, d), d;
	}
	return i ? s.insertBefore(d, s.querySelector("style, link")) : s.appendChild(d), vs(t.els, d), d;
}
//#endregion
//#region node_modules/css-render/esm/c.js
function bs(e) {
	return hs(this, this.instance, e);
}
function xs(e = {}) {
	let { id: t, ssr: n, props: r, head: i = !1, force: a = !1, anchorMetaName: o, parent: s } = e;
	return ys(this.instance, this, t, r, i, a, o, s, n);
}
function Ss(e = {}) {
	/* istanbul ignore next */
	let { id: t, parent: n } = e;
	_s(this.instance, this, t, n);
}
var Cs = function(e, t, n, r) {
	return {
		instance: e,
		$: t,
		props: n,
		children: r,
		els: [],
		render: bs,
		mount: xs,
		unmount: Ss
	};
}, ws = function(e, t, n, r) {
	return Array.isArray(t) ? Cs(e, { $: null }, null, t) : Array.isArray(n) ? Cs(e, t, null, n) : Array.isArray(r) ? Cs(e, t, n, r) : Cs(e, t, n, null);
};
//#endregion
//#region node_modules/css-render/esm/CssRender.js
function Ts(e = {}) {
	let t = {
		c: ((...e) => ws(t, ...e)),
		use: (e, ...n) => e.install(t, ...n),
		find: as,
		context: {},
		config: e
	};
	return t;
}
//#endregion
//#region node_modules/css-render/esm/exists.js
function Es(e, t) {
	if (e === void 0) return !1;
	if (t) {
		let { context: { ids: n } } = t;
		return n.has(e);
	}
	return as(e) !== null;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/cssr/index.mjs
var Ds = ".n-", Os = "__", ks = "--", As = Ts(), js = Zo({
	blockPrefix: Ds,
	elementPrefix: Os,
	modifierPrefix: ks
});
As.use(js);
var { c: B, find: Ms } = As, { cB: V, cE: H, cM: U, cNotM: Ns } = js;
function Ps(e) {
	return B(({ props: { bPrefix: e } }) => `${e || Ds}modal, ${e || Ds}drawer`, [e]);
}
function Fs(e) {
	return B(({ props: { bPrefix: e } }) => `${e || Ds}popover`, [e]);
}
function Is(e) {
	return B(({ props: { bPrefix: e } }) => `&${e || Ds}modal`, e);
}
var Ls = (...e) => B(">", [V(...e)]);
function W(e, t) {
	return e + (t === "default" ? "" : t.replace(/^[a-z]/, (e) => e.toUpperCase()));
}
//#endregion
//#region node_modules/naive-ui/es/locales/common/enUS.mjs
var Rs = {
	name: "en-US",
	global: {
		undo: "Undo",
		redo: "Redo",
		confirm: "Confirm",
		clear: "Clear"
	},
	Popconfirm: {
		positiveText: "Confirm",
		negativeText: "Cancel"
	},
	Cascader: {
		placeholder: "Please Select",
		loading: "Loading",
		loadingRequiredMessage: (e) => `Please load all ${e}'s descendants before checking it.`
	},
	Time: {
		dateFormat: "yyyy-MM-dd",
		dateTimeFormat: "yyyy-MM-dd HH:mm:ss"
	},
	DatePicker: {
		yearFormat: "yyyy",
		monthFormat: "MMM",
		dayFormat: "eeeeee",
		yearTypeFormat: "yyyy",
		monthTypeFormat: "yyyy-MM",
		dateFormat: "yyyy-MM-dd",
		dateTimeFormat: "yyyy-MM-dd HH:mm:ss",
		quarterFormat: "yyyy-qqq",
		weekFormat: "YYYY-w",
		clear: "Clear",
		now: "Now",
		confirm: "Confirm",
		selectTime: "Select Time",
		selectDate: "Select Date",
		datePlaceholder: "Select Date",
		datetimePlaceholder: "Select Date and Time",
		monthPlaceholder: "Select Month",
		yearPlaceholder: "Select Year",
		quarterPlaceholder: "Select Quarter",
		weekPlaceholder: "Select Week",
		startDatePlaceholder: "Start Date",
		endDatePlaceholder: "End Date",
		startDatetimePlaceholder: "Start Date and Time",
		endDatetimePlaceholder: "End Date and Time",
		startMonthPlaceholder: "Start Month",
		endMonthPlaceholder: "End Month",
		monthBeforeYear: !0,
		firstDayOfWeek: 6,
		today: "Today"
	},
	DataTable: {
		checkTableAll: "Select all in the table",
		uncheckTableAll: "Unselect all in the table",
		confirm: "Confirm",
		clear: "Clear"
	},
	LegacyTransfer: {
		sourceTitle: "Source",
		targetTitle: "Target"
	},
	Transfer: {
		selectAll: "Select all",
		unselectAll: "Unselect all",
		clearAll: "Clear",
		total: (e) => `Total ${e} items`,
		selected: (e) => `${e} items selected`
	},
	Empty: { description: "No Data" },
	Select: { placeholder: "Please Select" },
	TimePicker: {
		placeholder: "Select Time",
		positiveText: "OK",
		negativeText: "Cancel",
		now: "Now",
		clear: "Clear"
	},
	Pagination: {
		goto: "Goto",
		selectionSuffix: "page"
	},
	DynamicTags: { add: "Add" },
	Log: { loading: "Loading" },
	Input: { placeholder: "Please Input" },
	InputNumber: { placeholder: "Please Input" },
	DynamicInput: { create: "Create" },
	ThemeEditor: {
		title: "Theme Editor",
		clearAllVars: "Clear All Variables",
		clearSearch: "Clear Search",
		filterCompName: "Filter Component Name",
		filterVarName: "Filter Variable Name",
		import: "Import",
		export: "Export",
		restore: "Reset to Default"
	},
	Image: {
		tipPrevious: "Previous picture (←)",
		tipNext: "Next picture (→)",
		tipCounterclockwise: "Counterclockwise",
		tipClockwise: "Clockwise",
		tipZoomOut: "Zoom out",
		tipZoomIn: "Zoom in",
		tipDownload: "Download",
		tipClose: "Close (Esc)",
		tipOriginalSize: "Zoom to original size"
	},
	Heatmap: {
		less: "less",
		more: "more",
		monthFormat: "MMM",
		weekdayFormat: "eee"
	}
}, zs = {
	name: "zh-CN",
	global: {
		undo: "撤销",
		redo: "重做",
		confirm: "确认",
		clear: "清除"
	},
	Popconfirm: {
		positiveText: "确认",
		negativeText: "取消"
	},
	Cascader: {
		placeholder: "请选择",
		loading: "加载中",
		loadingRequiredMessage: (e) => `加载全部 ${e} 的子节点后才可选中`
	},
	Time: {
		dateFormat: "yyyy-MM-dd",
		dateTimeFormat: "yyyy-MM-dd HH:mm:ss"
	},
	DatePicker: {
		yearFormat: "yyyy年",
		monthFormat: "MMM",
		dayFormat: "eeeeee",
		yearTypeFormat: "yyyy",
		monthTypeFormat: "yyyy-MM",
		dateFormat: "yyyy-MM-dd",
		dateTimeFormat: "yyyy-MM-dd HH:mm:ss",
		quarterFormat: "yyyy-qqq",
		weekFormat: "YYYY-w周",
		clear: "清除",
		now: "此刻",
		confirm: "确认",
		selectTime: "选择时间",
		selectDate: "选择日期",
		datePlaceholder: "选择日期",
		datetimePlaceholder: "选择日期时间",
		monthPlaceholder: "选择月份",
		yearPlaceholder: "选择年份",
		quarterPlaceholder: "选择季度",
		weekPlaceholder: "选择周",
		startDatePlaceholder: "开始日期",
		endDatePlaceholder: "结束日期",
		startDatetimePlaceholder: "开始日期时间",
		endDatetimePlaceholder: "结束日期时间",
		startMonthPlaceholder: "开始月份",
		endMonthPlaceholder: "结束月份",
		monthBeforeYear: !1,
		firstDayOfWeek: 0,
		today: "今天"
	},
	DataTable: {
		checkTableAll: "选择全部表格数据",
		uncheckTableAll: "取消选择全部表格数据",
		confirm: "确认",
		clear: "重置"
	},
	LegacyTransfer: {
		sourceTitle: "源项",
		targetTitle: "目标项"
	},
	Transfer: {
		selectAll: "全选",
		clearAll: "清除",
		unselectAll: "取消全选",
		total: (e) => `共 ${e} 项`,
		selected: (e) => `已选 ${e} 项`
	},
	Empty: { description: "无数据" },
	Select: { placeholder: "请选择" },
	TimePicker: {
		placeholder: "请选择时间",
		positiveText: "确认",
		negativeText: "取消",
		now: "此刻",
		clear: "清除"
	},
	Pagination: {
		goto: "跳至",
		selectionSuffix: "页"
	},
	DynamicTags: { add: "添加" },
	Log: { loading: "加载中" },
	Input: { placeholder: "请输入" },
	InputNumber: { placeholder: "请输入" },
	DynamicInput: { create: "添加" },
	ThemeEditor: {
		title: "主题编辑器",
		clearAllVars: "清除全部变量",
		clearSearch: "清除搜索",
		filterCompName: "过滤组件名",
		filterVarName: "过滤变量名",
		import: "导入",
		export: "导出",
		restore: "恢复默认"
	},
	Image: {
		tipPrevious: "上一张（←）",
		tipNext: "下一张（→）",
		tipCounterclockwise: "向左旋转",
		tipClockwise: "向右旋转",
		tipZoomOut: "缩小",
		tipZoomIn: "放大",
		tipDownload: "下载",
		tipClose: "关闭（Esc）",
		tipOriginalSize: "缩放到原始尺寸"
	},
	Heatmap: {
		less: "少",
		more: "多",
		monthFormat: "MMM",
		weekdayFormat: "eeeeee"
	}
};
//#endregion
//#region node_modules/date-fns/locale/_lib/buildFormatLongFn.js
function Bs(e) {
	return (t = {}) => {
		let n = t.width ? String(t.width) : e.defaultWidth;
		return e.formats[n] || e.formats[e.defaultWidth];
	};
}
//#endregion
//#region node_modules/date-fns/locale/_lib/buildLocalizeFn.js
function Vs(e) {
	return (t, n) => {
		let r = n?.context ? String(n.context) : "standalone", i;
		if (r === "formatting" && e.formattingValues) {
			let t = e.defaultFormattingWidth || e.defaultWidth, r = n?.width ? String(n.width) : t;
			i = e.formattingValues[r] || e.formattingValues[t];
		} else {
			let t = e.defaultWidth, r = n?.width ? String(n.width) : e.defaultWidth;
			i = e.values[r] || e.values[t];
		}
		let a = e.argumentCallback ? e.argumentCallback(t) : t;
		return i[a];
	};
}
//#endregion
//#region node_modules/date-fns/locale/_lib/buildMatchFn.js
function Hs(e) {
	return (t, n = {}) => {
		let r = n.width, i = r && e.matchPatterns[r] || e.matchPatterns[e.defaultMatchWidth], a = t.match(i);
		if (!a) return null;
		let o = a[0], s = r && e.parsePatterns[r] || e.parsePatterns[e.defaultParseWidth], c = Array.isArray(s) ? Ws(s, (e) => e.test(o)) : Us(s, (e) => e.test(o)), l;
		l = e.valueCallback ? e.valueCallback(c) : c, l = n.valueCallback ? n.valueCallback(l) : l;
		let u = t.slice(o.length);
		return {
			value: l,
			rest: u
		};
	};
}
function Us(e, t) {
	for (let n in e) if (Object.prototype.hasOwnProperty.call(e, n) && t(e[n])) return n;
}
function Ws(e, t) {
	for (let n = 0; n < e.length; n++) if (t(e[n])) return n;
}
//#endregion
//#region node_modules/date-fns/locale/_lib/buildMatchPatternFn.js
function Gs(e) {
	return (t, n = {}) => {
		let r = t.match(e.matchPattern);
		if (!r) return null;
		let i = r[0], a = t.match(e.parsePattern);
		if (!a) return null;
		let o = e.valueCallback ? e.valueCallback(a[0]) : a[0];
		o = n.valueCallback ? n.valueCallback(o) : o;
		let s = t.slice(i.length);
		return {
			value: o,
			rest: s
		};
	};
}
//#endregion
//#region node_modules/date-fns/constants.js
var Ks = 365.2425, qs = 86400;
qs * 7, qs * Ks / 12 * 3;
var Js = Symbol.for("constructDateFrom");
//#endregion
//#region node_modules/date-fns/constructFrom.js
function Ys(e, t) {
	return typeof e == "function" ? e(t) : e && typeof e == "object" && Js in e ? e[Js](t) : e instanceof Date ? new e.constructor(t) : new Date(t);
}
//#endregion
//#region node_modules/date-fns/_lib/normalizeDates.js
function Xs(e, ...t) {
	let n = Ys.bind(null, e || t.find((e) => typeof e == "object"));
	return t.map(n);
}
//#endregion
//#region node_modules/date-fns/_lib/defaultOptions.js
var Zs = {};
function Qs() {
	return Zs;
}
//#endregion
//#region node_modules/date-fns/toDate.js
function $s(e, t) {
	return Ys(t || e, e);
}
//#endregion
//#region node_modules/date-fns/startOfWeek.js
function ec(e, t) {
	let n = Qs(), r = t?.weekStartsOn ?? t?.locale?.options?.weekStartsOn ?? n.weekStartsOn ?? n.locale?.options?.weekStartsOn ?? 0, i = $s(e, t?.in), a = i.getDay(), o = (a < r ? 7 : 0) + a - r;
	return i.setDate(i.getDate() - o), i.setHours(0, 0, 0, 0), i;
}
//#endregion
//#region node_modules/date-fns/isSameWeek.js
function tc(e, t, n) {
	let [r, i] = Xs(n?.in, e, t);
	return +ec(r, n) == +ec(i, n);
}
//#endregion
//#region node_modules/date-fns/locale/en-US/_lib/formatDistance.js
var nc = {
	lessThanXSeconds: {
		one: "less than a second",
		other: "less than {{count}} seconds"
	},
	xSeconds: {
		one: "1 second",
		other: "{{count}} seconds"
	},
	halfAMinute: "half a minute",
	lessThanXMinutes: {
		one: "less than a minute",
		other: "less than {{count}} minutes"
	},
	xMinutes: {
		one: "1 minute",
		other: "{{count}} minutes"
	},
	aboutXHours: {
		one: "about 1 hour",
		other: "about {{count}} hours"
	},
	xHours: {
		one: "1 hour",
		other: "{{count}} hours"
	},
	xDays: {
		one: "1 day",
		other: "{{count}} days"
	},
	aboutXWeeks: {
		one: "about 1 week",
		other: "about {{count}} weeks"
	},
	xWeeks: {
		one: "1 week",
		other: "{{count}} weeks"
	},
	aboutXMonths: {
		one: "about 1 month",
		other: "about {{count}} months"
	},
	xMonths: {
		one: "1 month",
		other: "{{count}} months"
	},
	aboutXYears: {
		one: "about 1 year",
		other: "about {{count}} years"
	},
	xYears: {
		one: "1 year",
		other: "{{count}} years"
	},
	overXYears: {
		one: "over 1 year",
		other: "over {{count}} years"
	},
	almostXYears: {
		one: "almost 1 year",
		other: "almost {{count}} years"
	}
}, rc = (e, t, n) => {
	let r, i = nc[e];
	return r = typeof i == "string" ? i : t === 1 ? i.one : i.other.replace("{{count}}", t.toString()), n?.addSuffix ? n.comparison && n.comparison > 0 ? "in " + r : r + " ago" : r;
}, ic = {
	lastWeek: "'last' eeee 'at' p",
	yesterday: "'yesterday at' p",
	today: "'today at' p",
	tomorrow: "'tomorrow at' p",
	nextWeek: "eeee 'at' p",
	other: "P"
}, ac = (e, t, n, r) => ic[e], oc = {
	ordinalNumber: (e, t) => {
		let n = Number(e), r = n % 100;
		if (r > 20 || r < 10) switch (r % 10) {
			case 1: return n + "st";
			case 2: return n + "nd";
			case 3: return n + "rd";
		}
		return n + "th";
	},
	era: Vs({
		values: {
			narrow: ["B", "A"],
			abbreviated: ["BC", "AD"],
			wide: ["Before Christ", "Anno Domini"]
		},
		defaultWidth: "wide"
	}),
	quarter: Vs({
		values: {
			narrow: [
				"1",
				"2",
				"3",
				"4"
			],
			abbreviated: [
				"Q1",
				"Q2",
				"Q3",
				"Q4"
			],
			wide: [
				"1st quarter",
				"2nd quarter",
				"3rd quarter",
				"4th quarter"
			]
		},
		defaultWidth: "wide",
		argumentCallback: (e) => e - 1
	}),
	month: Vs({
		values: {
			narrow: [
				"J",
				"F",
				"M",
				"A",
				"M",
				"J",
				"J",
				"A",
				"S",
				"O",
				"N",
				"D"
			],
			abbreviated: [
				"Jan",
				"Feb",
				"Mar",
				"Apr",
				"May",
				"Jun",
				"Jul",
				"Aug",
				"Sep",
				"Oct",
				"Nov",
				"Dec"
			],
			wide: [
				"January",
				"February",
				"March",
				"April",
				"May",
				"June",
				"July",
				"August",
				"September",
				"October",
				"November",
				"December"
			]
		},
		defaultWidth: "wide"
	}),
	day: Vs({
		values: {
			narrow: [
				"S",
				"M",
				"T",
				"W",
				"T",
				"F",
				"S"
			],
			short: [
				"Su",
				"Mo",
				"Tu",
				"We",
				"Th",
				"Fr",
				"Sa"
			],
			abbreviated: [
				"Sun",
				"Mon",
				"Tue",
				"Wed",
				"Thu",
				"Fri",
				"Sat"
			],
			wide: [
				"Sunday",
				"Monday",
				"Tuesday",
				"Wednesday",
				"Thursday",
				"Friday",
				"Saturday"
			]
		},
		defaultWidth: "wide"
	}),
	dayPeriod: Vs({
		values: {
			narrow: {
				am: "a",
				pm: "p",
				midnight: "mi",
				noon: "n",
				morning: "morning",
				afternoon: "afternoon",
				evening: "evening",
				night: "night"
			},
			abbreviated: {
				am: "AM",
				pm: "PM",
				midnight: "midnight",
				noon: "noon",
				morning: "morning",
				afternoon: "afternoon",
				evening: "evening",
				night: "night"
			},
			wide: {
				am: "a.m.",
				pm: "p.m.",
				midnight: "midnight",
				noon: "noon",
				morning: "morning",
				afternoon: "afternoon",
				evening: "evening",
				night: "night"
			}
		},
		defaultWidth: "wide",
		formattingValues: {
			narrow: {
				am: "a",
				pm: "p",
				midnight: "mi",
				noon: "n",
				morning: "in the morning",
				afternoon: "in the afternoon",
				evening: "in the evening",
				night: "at night"
			},
			abbreviated: {
				am: "AM",
				pm: "PM",
				midnight: "midnight",
				noon: "noon",
				morning: "in the morning",
				afternoon: "in the afternoon",
				evening: "in the evening",
				night: "at night"
			},
			wide: {
				am: "a.m.",
				pm: "p.m.",
				midnight: "midnight",
				noon: "noon",
				morning: "in the morning",
				afternoon: "in the afternoon",
				evening: "in the evening",
				night: "at night"
			}
		},
		defaultFormattingWidth: "wide"
	})
}, sc = {
	ordinalNumber: Gs({
		matchPattern: /^(\d+)(th|st|nd|rd)?/i,
		parsePattern: /\d+/i,
		valueCallback: (e) => parseInt(e, 10)
	}),
	era: Hs({
		matchPatterns: {
			narrow: /^(b|a)/i,
			abbreviated: /^(b\.?\s?c\.?|b\.?\s?c\.?\s?e\.?|a\.?\s?d\.?|c\.?\s?e\.?)/i,
			wide: /^(before christ|before common era|anno domini|common era)/i
		},
		defaultMatchWidth: "wide",
		parsePatterns: { any: [/^b/i, /^(a|c)/i] },
		defaultParseWidth: "any"
	}),
	quarter: Hs({
		matchPatterns: {
			narrow: /^[1234]/i,
			abbreviated: /^q[1234]/i,
			wide: /^[1234](th|st|nd|rd)? quarter/i
		},
		defaultMatchWidth: "wide",
		parsePatterns: { any: [
			/1/i,
			/2/i,
			/3/i,
			/4/i
		] },
		defaultParseWidth: "any",
		valueCallback: (e) => e + 1
	}),
	month: Hs({
		matchPatterns: {
			narrow: /^[jfmasond]/i,
			abbreviated: /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i,
			wide: /^(january|february|march|april|may|june|july|august|september|october|november|december)/i
		},
		defaultMatchWidth: "wide",
		parsePatterns: {
			narrow: [
				/^j/i,
				/^f/i,
				/^m/i,
				/^a/i,
				/^m/i,
				/^j/i,
				/^j/i,
				/^a/i,
				/^s/i,
				/^o/i,
				/^n/i,
				/^d/i
			],
			any: [
				/^ja/i,
				/^f/i,
				/^mar/i,
				/^ap/i,
				/^may/i,
				/^jun/i,
				/^jul/i,
				/^au/i,
				/^s/i,
				/^o/i,
				/^n/i,
				/^d/i
			]
		},
		defaultParseWidth: "any"
	}),
	day: Hs({
		matchPatterns: {
			narrow: /^[smtwf]/i,
			short: /^(su|mo|tu|we|th|fr|sa)/i,
			abbreviated: /^(sun|mon|tue|wed|thu|fri|sat)/i,
			wide: /^(sunday|monday|tuesday|wednesday|thursday|friday|saturday)/i
		},
		defaultMatchWidth: "wide",
		parsePatterns: {
			narrow: [
				/^s/i,
				/^m/i,
				/^t/i,
				/^w/i,
				/^t/i,
				/^f/i,
				/^s/i
			],
			any: [
				/^su/i,
				/^m/i,
				/^tu/i,
				/^w/i,
				/^th/i,
				/^f/i,
				/^sa/i
			]
		},
		defaultParseWidth: "any"
	}),
	dayPeriod: Hs({
		matchPatterns: {
			narrow: /^(a|p|mi|n|(in the|at) (morning|afternoon|evening|night))/i,
			any: /^([ap]\.?\s?m\.?|midnight|noon|(in the|at) (morning|afternoon|evening|night))/i
		},
		defaultMatchWidth: "any",
		parsePatterns: { any: {
			am: /^a/i,
			pm: /^p/i,
			midnight: /^mi/i,
			noon: /^no/i,
			morning: /morning/i,
			afternoon: /afternoon/i,
			evening: /evening/i,
			night: /night/i
		} },
		defaultParseWidth: "any"
	})
}, cc = {
	code: "en-US",
	formatDistance: rc,
	formatLong: {
		date: Bs({
			formats: {
				full: "EEEE, MMMM do, y",
				long: "MMMM do, y",
				medium: "MMM d, y",
				short: "MM/dd/yyyy"
			},
			defaultWidth: "full"
		}),
		time: Bs({
			formats: {
				full: "h:mm:ss a zzzz",
				long: "h:mm:ss a z",
				medium: "h:mm:ss a",
				short: "h:mm a"
			},
			defaultWidth: "full"
		}),
		dateTime: Bs({
			formats: {
				full: "{{date}} 'at' {{time}}",
				long: "{{date}} 'at' {{time}}",
				medium: "{{date}}, {{time}}",
				short: "{{date}}, {{time}}"
			},
			defaultWidth: "full"
		})
	},
	formatRelative: ac,
	localize: oc,
	match: sc,
	options: {
		weekStartsOn: 0,
		firstWeekContainsDate: 1
	}
}, lc = {
	lessThanXSeconds: {
		one: "不到 1 秒",
		other: "不到 {{count}} 秒"
	},
	xSeconds: {
		one: "1 秒",
		other: "{{count}} 秒"
	},
	halfAMinute: "半分钟",
	lessThanXMinutes: {
		one: "不到 1 分钟",
		other: "不到 {{count}} 分钟"
	},
	xMinutes: {
		one: "1 分钟",
		other: "{{count}} 分钟"
	},
	xHours: {
		one: "1 小时",
		other: "{{count}} 小时"
	},
	aboutXHours: {
		one: "大约 1 小时",
		other: "大约 {{count}} 小时"
	},
	xDays: {
		one: "1 天",
		other: "{{count}} 天"
	},
	aboutXWeeks: {
		one: "大约 1 个星期",
		other: "大约 {{count}} 个星期"
	},
	xWeeks: {
		one: "1 个星期",
		other: "{{count}} 个星期"
	},
	aboutXMonths: {
		one: "大约 1 个月",
		other: "大约 {{count}} 个月"
	},
	xMonths: {
		one: "1 个月",
		other: "{{count}} 个月"
	},
	aboutXYears: {
		one: "大约 1 年",
		other: "大约 {{count}} 年"
	},
	xYears: {
		one: "1 年",
		other: "{{count}} 年"
	},
	overXYears: {
		one: "超过 1 年",
		other: "超过 {{count}} 年"
	},
	almostXYears: {
		one: "将近 1 年",
		other: "将近 {{count}} 年"
	}
}, uc = (e, t, n) => {
	let r, i = lc[e];
	return r = typeof i == "string" ? i : t === 1 ? i.one : i.other.replace("{{count}}", String(t)), n?.addSuffix ? n.comparison && n.comparison > 0 ? r + "内" : r + "前" : r;
}, dc = {
	date: Bs({
		formats: {
			full: "y'年'M'月'd'日' EEEE",
			long: "y'年'M'月'd'日'",
			medium: "yyyy-MM-dd",
			short: "yy-MM-dd"
		},
		defaultWidth: "full"
	}),
	time: Bs({
		formats: {
			full: "zzzz a h:mm:ss",
			long: "z a h:mm:ss",
			medium: "a h:mm:ss",
			short: "a h:mm"
		},
		defaultWidth: "full"
	}),
	dateTime: Bs({
		formats: {
			full: "{{date}} {{time}}",
			long: "{{date}} {{time}}",
			medium: "{{date}} {{time}}",
			short: "{{date}} {{time}}"
		},
		defaultWidth: "full"
	})
};
//#endregion
//#region node_modules/date-fns/locale/zh-CN/_lib/formatRelative.js
function fc(e, t, n) {
	return tc(e, t, n) ? "eeee p" : e.getTime() > t.getTime() ? "'下个'eeee p" : "'上个'eeee p";
}
var pc = {
	lastWeek: fc,
	yesterday: "'昨天' p",
	today: "'今天' p",
	tomorrow: "'明天' p",
	nextWeek: fc,
	other: "PP p"
}, mc = {
	code: "zh-CN",
	formatDistance: uc,
	formatLong: dc,
	formatRelative: (e, t, n, r) => {
		let i = pc[e];
		return typeof i == "function" ? i(t, n, r) : i;
	},
	localize: {
		ordinalNumber: (e, t) => {
			let n = Number(e);
			switch (t?.unit) {
				case "date": return n.toString() + "日";
				case "hour": return n.toString() + "时";
				case "minute": return n.toString() + "分";
				case "second": return n.toString() + "秒";
				default: return "第 " + n.toString();
			}
		},
		era: Vs({
			values: {
				narrow: ["前", "公元"],
				abbreviated: ["前", "公元"],
				wide: ["公元前", "公元"]
			},
			defaultWidth: "wide"
		}),
		quarter: Vs({
			values: {
				narrow: [
					"1",
					"2",
					"3",
					"4"
				],
				abbreviated: [
					"第一季",
					"第二季",
					"第三季",
					"第四季"
				],
				wide: [
					"第一季度",
					"第二季度",
					"第三季度",
					"第四季度"
				]
			},
			defaultWidth: "wide",
			argumentCallback: (e) => e - 1
		}),
		month: Vs({
			values: {
				narrow: [
					"一",
					"二",
					"三",
					"四",
					"五",
					"六",
					"七",
					"八",
					"九",
					"十",
					"十一",
					"十二"
				],
				abbreviated: [
					"1月",
					"2月",
					"3月",
					"4月",
					"5月",
					"6月",
					"7月",
					"8月",
					"9月",
					"10月",
					"11月",
					"12月"
				],
				wide: [
					"一月",
					"二月",
					"三月",
					"四月",
					"五月",
					"六月",
					"七月",
					"八月",
					"九月",
					"十月",
					"十一月",
					"十二月"
				]
			},
			defaultWidth: "wide"
		}),
		day: Vs({
			values: {
				narrow: [
					"日",
					"一",
					"二",
					"三",
					"四",
					"五",
					"六"
				],
				short: [
					"日",
					"一",
					"二",
					"三",
					"四",
					"五",
					"六"
				],
				abbreviated: [
					"周日",
					"周一",
					"周二",
					"周三",
					"周四",
					"周五",
					"周六"
				],
				wide: [
					"星期日",
					"星期一",
					"星期二",
					"星期三",
					"星期四",
					"星期五",
					"星期六"
				]
			},
			defaultWidth: "wide"
		}),
		dayPeriod: Vs({
			values: {
				narrow: {
					am: "上",
					pm: "下",
					midnight: "凌晨",
					noon: "午",
					morning: "早",
					afternoon: "下午",
					evening: "晚",
					night: "夜"
				},
				abbreviated: {
					am: "上午",
					pm: "下午",
					midnight: "凌晨",
					noon: "中午",
					morning: "早晨",
					afternoon: "中午",
					evening: "晚上",
					night: "夜间"
				},
				wide: {
					am: "上午",
					pm: "下午",
					midnight: "凌晨",
					noon: "中午",
					morning: "早晨",
					afternoon: "中午",
					evening: "晚上",
					night: "夜间"
				}
			},
			defaultWidth: "wide",
			formattingValues: {
				narrow: {
					am: "上",
					pm: "下",
					midnight: "凌晨",
					noon: "午",
					morning: "早",
					afternoon: "下午",
					evening: "晚",
					night: "夜"
				},
				abbreviated: {
					am: "上午",
					pm: "下午",
					midnight: "凌晨",
					noon: "中午",
					morning: "早晨",
					afternoon: "中午",
					evening: "晚上",
					night: "夜间"
				},
				wide: {
					am: "上午",
					pm: "下午",
					midnight: "凌晨",
					noon: "中午",
					morning: "早晨",
					afternoon: "中午",
					evening: "晚上",
					night: "夜间"
				}
			},
			defaultFormattingWidth: "wide"
		})
	},
	match: {
		ordinalNumber: Gs({
			matchPattern: /^(第\s*)?\d+(日|时|分|秒)?/i,
			parsePattern: /\d+/i,
			valueCallback: (e) => parseInt(e, 10)
		}),
		era: Hs({
			matchPatterns: {
				narrow: /^(前)/i,
				abbreviated: /^(前)/i,
				wide: /^(公元前|公元)/i
			},
			defaultMatchWidth: "wide",
			parsePatterns: { any: [/^(前)/i, /^(公元)/i] },
			defaultParseWidth: "any"
		}),
		quarter: Hs({
			matchPatterns: {
				narrow: /^[1234]/i,
				abbreviated: /^第[一二三四]刻/i,
				wide: /^第[一二三四]刻钟/i
			},
			defaultMatchWidth: "wide",
			parsePatterns: { any: [
				/(1|一)/i,
				/(2|二)/i,
				/(3|三)/i,
				/(4|四)/i
			] },
			defaultParseWidth: "any",
			valueCallback: (e) => e + 1
		}),
		month: Hs({
			matchPatterns: {
				narrow: /^(一|二|三|四|五|六|七|八|九|十[二一]?)/i,
				abbreviated: /^(一|二|三|四|五|六|七|八|九|十[二一]?|\d|1[0-2])月/i,
				wide: /^(一|二|三|四|五|六|七|八|九|十[二一]?)月/i
			},
			defaultMatchWidth: "wide",
			parsePatterns: {
				narrow: [
					/^一/i,
					/^二/i,
					/^三/i,
					/^四/i,
					/^五/i,
					/^六/i,
					/^七/i,
					/^八/i,
					/^九/i,
					/^十(?!(一|二))/i,
					/^十一/i,
					/^十二/i
				],
				any: [
					/^(一|1(?!\d))/i,
					/^(二|2)/i,
					/^(三|3)/i,
					/^(四|4)/i,
					/^(五|5)/i,
					/^(六|6)/i,
					/^(七|7)/i,
					/^(八|8)/i,
					/^(九|9)/i,
					/^(十(?!(一|二))|10)/i,
					/^(十一|11)/i,
					/^(十二|12)/i
				]
			},
			defaultParseWidth: "any"
		}),
		day: Hs({
			matchPatterns: {
				narrow: /^[一二三四五六日]/i,
				short: /^[一二三四五六日]/i,
				abbreviated: /^周[一二三四五六日]/i,
				wide: /^星期[一二三四五六日]/i
			},
			defaultMatchWidth: "wide",
			parsePatterns: { any: [
				/日/i,
				/一/i,
				/二/i,
				/三/i,
				/四/i,
				/五/i,
				/六/i
			] },
			defaultParseWidth: "any"
		}),
		dayPeriod: Hs({
			matchPatterns: { any: /^(上午?|下午?|午夜|[中正]午|早上?|下午|晚上?|凌晨|)/i },
			defaultMatchWidth: "any",
			parsePatterns: { any: {
				am: /^上午?/i,
				pm: /^下午?/i,
				midnight: /^午夜/i,
				noon: /^[中正]午/i,
				morning: /^早上/i,
				afternoon: /^下午/i,
				evening: /^晚上?/i,
				night: /^凌晨/i
			} },
			defaultParseWidth: "any"
		})
	},
	options: {
		weekStartsOn: 1,
		firstWeekContainsDate: 4
	}
}, hc = {
	name: "en-US",
	locale: cc
}, gc = {
	name: "zh-CN",
	locale: mc
}, _c = typeof global == "object" && global && global.Object === Object && global, vc = typeof self == "object" && self && self.Object === Object && self, yc = _c || vc || Function("return this")(), bc = yc.Symbol, xc = Object.prototype, Sc = xc.hasOwnProperty, Cc = xc.toString, wc = bc ? bc.toStringTag : void 0;
function Tc(e) {
	var t = Sc.call(e, wc), n = e[wc];
	try {
		e[wc] = void 0;
		var r = !0;
	} catch {}
	var i = Cc.call(e);
	return r && (t ? e[wc] = n : delete e[wc]), i;
}
//#endregion
//#region node_modules/lodash-es/_objectToString.js
var Ec = Object.prototype.toString;
function Dc(e) {
	return Ec.call(e);
}
//#endregion
//#region node_modules/lodash-es/_baseGetTag.js
var Oc = "[object Null]", kc = "[object Undefined]", Ac = bc ? bc.toStringTag : void 0;
function jc(e) {
	return e == null ? e === void 0 ? kc : Oc : Ac && Ac in Object(e) ? Tc(e) : Dc(e);
}
//#endregion
//#region node_modules/lodash-es/isObjectLike.js
function Mc(e) {
	return typeof e == "object" && !!e;
}
//#endregion
//#region node_modules/lodash-es/isSymbol.js
var Nc = "[object Symbol]";
function Pc(e) {
	return typeof e == "symbol" || Mc(e) && jc(e) == Nc;
}
//#endregion
//#region node_modules/lodash-es/_arrayMap.js
function Fc(e, t) {
	for (var n = -1, r = e == null ? 0 : e.length, i = Array(r); ++n < r;) i[n] = t(e[n], n, e);
	return i;
}
//#endregion
//#region node_modules/lodash-es/isArray.js
var Ic = Array.isArray, Lc = 1 / 0, Rc = bc ? bc.prototype : void 0, zc = Rc ? Rc.toString : void 0;
function Bc(e) {
	if (typeof e == "string") return e;
	if (Ic(e)) return Fc(e, Bc) + "";
	if (Pc(e)) return zc ? zc.call(e) : "";
	var t = e + "";
	return t == "0" && 1 / e == -Lc ? "-0" : t;
}
//#endregion
//#region node_modules/lodash-es/isObject.js
function Vc(e) {
	var t = typeof e;
	return e != null && (t == "object" || t == "function");
}
//#endregion
//#region node_modules/lodash-es/identity.js
function Hc(e) {
	return e;
}
//#endregion
//#region node_modules/lodash-es/isFunction.js
var Uc = "[object AsyncFunction]", Wc = "[object Function]", Gc = "[object GeneratorFunction]", Kc = "[object Proxy]";
function qc(e) {
	if (!Vc(e)) return !1;
	var t = jc(e);
	return t == Wc || t == Gc || t == Uc || t == Kc;
}
//#endregion
//#region node_modules/lodash-es/_coreJsData.js
var Jc = yc["__core-js_shared__"], Yc = function() {
	var e = /[^.]+$/.exec(Jc && Jc.keys && Jc.keys.IE_PROTO || "");
	return e ? "Symbol(src)_1." + e : "";
}();
function Xc(e) {
	return !!Yc && Yc in e;
}
//#endregion
//#region node_modules/lodash-es/_toSource.js
var Zc = Function.prototype.toString;
function Qc(e) {
	if (e != null) {
		try {
			return Zc.call(e);
		} catch {}
		try {
			return e + "";
		} catch {}
	}
	return "";
}
//#endregion
//#region node_modules/lodash-es/_baseIsNative.js
var $c = /[\\^$.*+?()[\]{}|]/g, el = /^\[object .+?Constructor\]$/, tl = Function.prototype, nl = Object.prototype, rl = tl.toString, il = nl.hasOwnProperty, al = RegExp("^" + rl.call(il).replace($c, "\\$&").replace(/hasOwnProperty|(function).*?(?=\\\()| for .+?(?=\\\])/g, "$1.*?") + "$");
function ol(e) {
	return !Vc(e) || Xc(e) ? !1 : (qc(e) ? al : el).test(Qc(e));
}
//#endregion
//#region node_modules/lodash-es/_getValue.js
function sl(e, t) {
	return e?.[t];
}
//#endregion
//#region node_modules/lodash-es/_getNative.js
function cl(e, t) {
	var n = sl(e, t);
	return ol(n) ? n : void 0;
}
//#endregion
//#region node_modules/lodash-es/_WeakMap.js
var ll = cl(yc, "WeakMap"), ul = Object.create, dl = function() {
	function e() {}
	return function(t) {
		if (!Vc(t)) return {};
		if (ul) return ul(t);
		e.prototype = t;
		var n = new e();
		return e.prototype = void 0, n;
	};
}();
//#endregion
//#region node_modules/lodash-es/_apply.js
function fl(e, t, n) {
	switch (n.length) {
		case 0: return e.call(t);
		case 1: return e.call(t, n[0]);
		case 2: return e.call(t, n[0], n[1]);
		case 3: return e.call(t, n[0], n[1], n[2]);
	}
	return e.apply(t, n);
}
//#endregion
//#region node_modules/lodash-es/_copyArray.js
function pl(e, t) {
	var n = -1, r = e.length;
	for (t ||= Array(r); ++n < r;) t[n] = e[n];
	return t;
}
//#endregion
//#region node_modules/lodash-es/_shortOut.js
var ml = 800, hl = 16, gl = Date.now;
function _l(e) {
	var t = 0, n = 0;
	return function() {
		var r = gl(), i = hl - (r - n);
		if (n = r, i > 0) {
			if (++t >= ml) return arguments[0];
		} else t = 0;
		return e.apply(void 0, arguments);
	};
}
//#endregion
//#region node_modules/lodash-es/constant.js
function vl(e) {
	return function() {
		return e;
	};
}
//#endregion
//#region node_modules/lodash-es/_defineProperty.js
var yl = function() {
	try {
		var e = cl(Object, "defineProperty");
		return e({}, "", {}), e;
	} catch {}
}(), bl = _l(yl ? function(e, t) {
	return yl(e, "toString", {
		configurable: !0,
		enumerable: !1,
		value: vl(t),
		writable: !0
	});
} : Hc), xl = 9007199254740991, Sl = /^(?:0|[1-9]\d*)$/;
function Cl(e, t) {
	var n = typeof e;
	return t ??= xl, !!t && (n == "number" || n != "symbol" && Sl.test(e)) && e > -1 && e % 1 == 0 && e < t;
}
//#endregion
//#region node_modules/lodash-es/_baseAssignValue.js
function wl(e, t, n) {
	t == "__proto__" && yl ? yl(e, t, {
		configurable: !0,
		enumerable: !0,
		value: n,
		writable: !0
	}) : e[t] = n;
}
//#endregion
//#region node_modules/lodash-es/eq.js
function Tl(e, t) {
	return e === t || e !== e && t !== t;
}
//#endregion
//#region node_modules/lodash-es/_assignValue.js
var El = Object.prototype.hasOwnProperty;
function Dl(e, t, n) {
	var r = e[t];
	(!(El.call(e, t) && Tl(r, n)) || n === void 0 && !(t in e)) && wl(e, t, n);
}
//#endregion
//#region node_modules/lodash-es/_copyObject.js
function Ol(e, t, n, r) {
	var i = !n;
	n ||= {};
	for (var a = -1, o = t.length; ++a < o;) {
		var s = t[a], c = r ? r(n[s], e[s], s, n, e) : void 0;
		c === void 0 && (c = e[s]), i ? wl(n, s, c) : Dl(n, s, c);
	}
	return n;
}
//#endregion
//#region node_modules/lodash-es/_overRest.js
var kl = Math.max;
function Al(e, t, n) {
	return t = kl(t === void 0 ? e.length - 1 : t, 0), function() {
		for (var r = arguments, i = -1, a = kl(r.length - t, 0), o = Array(a); ++i < a;) o[i] = r[t + i];
		i = -1;
		for (var s = Array(t + 1); ++i < t;) s[i] = r[i];
		return s[t] = n(o), fl(e, this, s);
	};
}
//#endregion
//#region node_modules/lodash-es/_baseRest.js
function jl(e, t) {
	return bl(Al(e, t, Hc), e + "");
}
//#endregion
//#region node_modules/lodash-es/isLength.js
var Ml = 9007199254740991;
function Nl(e) {
	return typeof e == "number" && e > -1 && e % 1 == 0 && e <= Ml;
}
//#endregion
//#region node_modules/lodash-es/isArrayLike.js
function Pl(e) {
	return e != null && Nl(e.length) && !qc(e);
}
//#endregion
//#region node_modules/lodash-es/_isIterateeCall.js
function Fl(e, t, n) {
	if (!Vc(n)) return !1;
	var r = typeof t;
	return (r == "number" ? Pl(n) && Cl(t, n.length) : r == "string" && t in n) ? Tl(n[t], e) : !1;
}
//#endregion
//#region node_modules/lodash-es/_createAssigner.js
function Il(e) {
	return jl(function(t, n) {
		var r = -1, i = n.length, a = i > 1 ? n[i - 1] : void 0, o = i > 2 ? n[2] : void 0;
		for (a = e.length > 3 && typeof a == "function" ? (i--, a) : void 0, o && Fl(n[0], n[1], o) && (a = i < 3 ? void 0 : a, i = 1), t = Object(t); ++r < i;) {
			var s = n[r];
			s && e(t, s, r, a);
		}
		return t;
	});
}
//#endregion
//#region node_modules/lodash-es/_isPrototype.js
var Ll = Object.prototype;
function Rl(e) {
	var t = e && e.constructor;
	return e === (typeof t == "function" && t.prototype || Ll);
}
//#endregion
//#region node_modules/lodash-es/_baseTimes.js
function zl(e, t) {
	for (var n = -1, r = Array(e); ++n < e;) r[n] = t(n);
	return r;
}
//#endregion
//#region node_modules/lodash-es/_baseIsArguments.js
var Bl = "[object Arguments]";
function Vl(e) {
	return Mc(e) && jc(e) == Bl;
}
//#endregion
//#region node_modules/lodash-es/isArguments.js
var Hl = Object.prototype, Ul = Hl.hasOwnProperty, Wl = Hl.propertyIsEnumerable, Gl = Vl(function() {
	return arguments;
}()) ? Vl : function(e) {
	return Mc(e) && Ul.call(e, "callee") && !Wl.call(e, "callee");
};
//#endregion
//#region node_modules/lodash-es/stubFalse.js
function Kl() {
	return !1;
}
//#endregion
//#region node_modules/lodash-es/isBuffer.js
var ql = typeof exports == "object" && exports && !exports.nodeType && exports, Jl = ql && typeof module == "object" && module && !module.nodeType && module, Yl = Jl && Jl.exports === ql ? yc.Buffer : void 0, Xl = (Yl ? Yl.isBuffer : void 0) || Kl, Zl = "[object Arguments]", Ql = "[object Array]", $l = "[object Boolean]", eu = "[object Date]", tu = "[object Error]", nu = "[object Function]", ru = "[object Map]", iu = "[object Number]", au = "[object Object]", ou = "[object RegExp]", su = "[object Set]", cu = "[object String]", lu = "[object WeakMap]", uu = "[object ArrayBuffer]", du = "[object DataView]", fu = "[object Float32Array]", pu = "[object Float64Array]", mu = "[object Int8Array]", hu = "[object Int16Array]", gu = "[object Int32Array]", _u = "[object Uint8Array]", vu = "[object Uint8ClampedArray]", yu = "[object Uint16Array]", bu = "[object Uint32Array]", xu = {};
xu[fu] = xu[pu] = xu[mu] = xu[hu] = xu[gu] = xu[_u] = xu[vu] = xu[yu] = xu[bu] = !0, xu[Zl] = xu[Ql] = xu[uu] = xu[$l] = xu[du] = xu[eu] = xu[tu] = xu[nu] = xu[ru] = xu[iu] = xu[au] = xu[ou] = xu[su] = xu[cu] = xu[lu] = !1;
function Su(e) {
	return Mc(e) && Nl(e.length) && !!xu[jc(e)];
}
//#endregion
//#region node_modules/lodash-es/_baseUnary.js
function Cu(e) {
	return function(t) {
		return e(t);
	};
}
//#endregion
//#region node_modules/lodash-es/_nodeUtil.js
var wu = typeof exports == "object" && exports && !exports.nodeType && exports, Tu = wu && typeof module == "object" && module && !module.nodeType && module, Eu = Tu && Tu.exports === wu && _c.process, Du = function() {
	try {
		return Tu && Tu.require && Tu.require("util").types || Eu && Eu.binding && Eu.binding("util");
	} catch {}
}(), Ou = Du && Du.isTypedArray, ku = Ou ? Cu(Ou) : Su, Au = Object.prototype.hasOwnProperty;
function ju(e, t) {
	var n = Ic(e), r = !n && Gl(e), i = !n && !r && Xl(e), a = !n && !r && !i && ku(e), o = n || r || i || a, s = o ? zl(e.length, String) : [], c = s.length;
	for (var l in e) (t || Au.call(e, l)) && !(o && (l == "length" || i && (l == "offset" || l == "parent") || a && (l == "buffer" || l == "byteLength" || l == "byteOffset") || Cl(l, c))) && s.push(l);
	return s;
}
//#endregion
//#region node_modules/lodash-es/_overArg.js
function Mu(e, t) {
	return function(n) {
		return e(t(n));
	};
}
//#endregion
//#region node_modules/lodash-es/_nativeKeys.js
var Nu = Mu(Object.keys, Object), Pu = Object.prototype.hasOwnProperty;
function Fu(e) {
	if (!Rl(e)) return Nu(e);
	var t = [];
	for (var n in Object(e)) Pu.call(e, n) && n != "constructor" && t.push(n);
	return t;
}
//#endregion
//#region node_modules/lodash-es/keys.js
function Iu(e) {
	return Pl(e) ? ju(e) : Fu(e);
}
//#endregion
//#region node_modules/lodash-es/_nativeKeysIn.js
function Lu(e) {
	var t = [];
	if (e != null) for (var n in Object(e)) t.push(n);
	return t;
}
//#endregion
//#region node_modules/lodash-es/_baseKeysIn.js
var Ru = Object.prototype.hasOwnProperty;
function zu(e) {
	if (!Vc(e)) return Lu(e);
	var t = Rl(e), n = [];
	for (var r in e) (r != "constructor" || !t && Ru.call(e, r)) && n.push(r);
	return n;
}
//#endregion
//#region node_modules/lodash-es/keysIn.js
function Bu(e) {
	return Pl(e) ? ju(e, !0) : zu(e);
}
//#endregion
//#region node_modules/lodash-es/_isKey.js
var Vu = /\.|\[(?:[^[\]]*|(["'])(?:(?!\1)[^\\]|\\.)*?\1)\]/, Hu = /^\w*$/;
function Uu(e, t) {
	if (Ic(e)) return !1;
	var n = typeof e;
	return n == "number" || n == "symbol" || n == "boolean" || e == null || Pc(e) ? !0 : Hu.test(e) || !Vu.test(e) || t != null && e in Object(t);
}
//#endregion
//#region node_modules/lodash-es/_nativeCreate.js
var Wu = cl(Object, "create");
//#endregion
//#region node_modules/lodash-es/_hashClear.js
function Gu() {
	this.__data__ = Wu ? Wu(null) : {}, this.size = 0;
}
//#endregion
//#region node_modules/lodash-es/_hashDelete.js
function Ku(e) {
	var t = this.has(e) && delete this.__data__[e];
	return this.size -= +!!t, t;
}
//#endregion
//#region node_modules/lodash-es/_hashGet.js
var qu = "__lodash_hash_undefined__", Ju = Object.prototype.hasOwnProperty;
function Yu(e) {
	var t = this.__data__;
	if (Wu) {
		var n = t[e];
		return n === qu ? void 0 : n;
	}
	return Ju.call(t, e) ? t[e] : void 0;
}
//#endregion
//#region node_modules/lodash-es/_hashHas.js
var Xu = Object.prototype.hasOwnProperty;
function Zu(e) {
	var t = this.__data__;
	return Wu ? t[e] !== void 0 : Xu.call(t, e);
}
//#endregion
//#region node_modules/lodash-es/_hashSet.js
var Qu = "__lodash_hash_undefined__";
function $u(e, t) {
	var n = this.__data__;
	return this.size += +!this.has(e), n[e] = Wu && t === void 0 ? Qu : t, this;
}
//#endregion
//#region node_modules/lodash-es/_Hash.js
function ed(e) {
	var t = -1, n = e == null ? 0 : e.length;
	for (this.clear(); ++t < n;) {
		var r = e[t];
		this.set(r[0], r[1]);
	}
}
ed.prototype.clear = Gu, ed.prototype.delete = Ku, ed.prototype.get = Yu, ed.prototype.has = Zu, ed.prototype.set = $u;
//#endregion
//#region node_modules/lodash-es/_listCacheClear.js
function td() {
	this.__data__ = [], this.size = 0;
}
//#endregion
//#region node_modules/lodash-es/_assocIndexOf.js
function nd(e, t) {
	for (var n = e.length; n--;) if (Tl(e[n][0], t)) return n;
	return -1;
}
//#endregion
//#region node_modules/lodash-es/_listCacheDelete.js
var rd = Array.prototype.splice;
function id(e) {
	var t = this.__data__, n = nd(t, e);
	return n < 0 ? !1 : (n == t.length - 1 ? t.pop() : rd.call(t, n, 1), --this.size, !0);
}
//#endregion
//#region node_modules/lodash-es/_listCacheGet.js
function ad(e) {
	var t = this.__data__, n = nd(t, e);
	return n < 0 ? void 0 : t[n][1];
}
//#endregion
//#region node_modules/lodash-es/_listCacheHas.js
function od(e) {
	return nd(this.__data__, e) > -1;
}
//#endregion
//#region node_modules/lodash-es/_listCacheSet.js
function sd(e, t) {
	var n = this.__data__, r = nd(n, e);
	return r < 0 ? (++this.size, n.push([e, t])) : n[r][1] = t, this;
}
//#endregion
//#region node_modules/lodash-es/_ListCache.js
function cd(e) {
	var t = -1, n = e == null ? 0 : e.length;
	for (this.clear(); ++t < n;) {
		var r = e[t];
		this.set(r[0], r[1]);
	}
}
cd.prototype.clear = td, cd.prototype.delete = id, cd.prototype.get = ad, cd.prototype.has = od, cd.prototype.set = sd;
//#endregion
//#region node_modules/lodash-es/_Map.js
var ld = cl(yc, "Map");
//#endregion
//#region node_modules/lodash-es/_mapCacheClear.js
function ud() {
	this.size = 0, this.__data__ = {
		hash: new ed(),
		map: new (ld || cd)(),
		string: new ed()
	};
}
//#endregion
//#region node_modules/lodash-es/_isKeyable.js
function dd(e) {
	var t = typeof e;
	return t == "string" || t == "number" || t == "symbol" || t == "boolean" ? e !== "__proto__" : e === null;
}
//#endregion
//#region node_modules/lodash-es/_getMapData.js
function fd(e, t) {
	var n = e.__data__;
	return dd(t) ? n[typeof t == "string" ? "string" : "hash"] : n.map;
}
//#endregion
//#region node_modules/lodash-es/_mapCacheDelete.js
function pd(e) {
	var t = fd(this, e).delete(e);
	return this.size -= +!!t, t;
}
//#endregion
//#region node_modules/lodash-es/_mapCacheGet.js
function md(e) {
	return fd(this, e).get(e);
}
//#endregion
//#region node_modules/lodash-es/_mapCacheHas.js
function hd(e) {
	return fd(this, e).has(e);
}
//#endregion
//#region node_modules/lodash-es/_mapCacheSet.js
function gd(e, t) {
	var n = fd(this, e), r = n.size;
	return n.set(e, t), this.size += n.size == r ? 0 : 1, this;
}
//#endregion
//#region node_modules/lodash-es/_MapCache.js
function _d(e) {
	var t = -1, n = e == null ? 0 : e.length;
	for (this.clear(); ++t < n;) {
		var r = e[t];
		this.set(r[0], r[1]);
	}
}
_d.prototype.clear = ud, _d.prototype.delete = pd, _d.prototype.get = md, _d.prototype.has = hd, _d.prototype.set = gd;
//#endregion
//#region node_modules/lodash-es/memoize.js
var vd = "Expected a function";
function yd(e, t) {
	if (typeof e != "function" || t != null && typeof t != "function") throw TypeError(vd);
	var n = function() {
		var r = arguments, i = t ? t.apply(this, r) : r[0], a = n.cache;
		if (a.has(i)) return a.get(i);
		var o = e.apply(this, r);
		return n.cache = a.set(i, o) || a, o;
	};
	return n.cache = new (yd.Cache || _d)(), n;
}
yd.Cache = _d;
//#endregion
//#region node_modules/lodash-es/_memoizeCapped.js
var bd = 500;
function xd(e) {
	var t = yd(e, function(e) {
		return n.size === bd && n.clear(), e;
	}), n = t.cache;
	return t;
}
//#endregion
//#region node_modules/lodash-es/_stringToPath.js
var Sd = /[^.[\]]+|\[(?:(-?\d+(?:\.\d+)?)|(["'])((?:(?!\2)[^\\]|\\.)*?)\2)\]|(?=(?:\.|\[\])(?:\.|\[\]|$))/g, Cd = /\\(\\)?/g, wd = xd(function(e) {
	var t = [];
	return e.charCodeAt(0) === 46 && t.push(""), e.replace(Sd, function(e, n, r, i) {
		t.push(r ? i.replace(Cd, "$1") : n || e);
	}), t;
});
//#endregion
//#region node_modules/lodash-es/toString.js
function Td(e) {
	return e == null ? "" : Bc(e);
}
//#endregion
//#region node_modules/lodash-es/_castPath.js
function Ed(e, t) {
	return Ic(e) ? e : Uu(e, t) ? [e] : wd(Td(e));
}
//#endregion
//#region node_modules/lodash-es/_toKey.js
var Dd = 1 / 0;
function Od(e) {
	if (typeof e == "string" || Pc(e)) return e;
	var t = e + "";
	return t == "0" && 1 / e == -Dd ? "-0" : t;
}
//#endregion
//#region node_modules/lodash-es/_baseGet.js
function kd(e, t) {
	t = Ed(t, e);
	for (var n = 0, r = t.length; e != null && n < r;) e = e[Od(t[n++])];
	return n && n == r ? e : void 0;
}
//#endregion
//#region node_modules/lodash-es/get.js
function Ad(e, t, n) {
	var r = e == null ? void 0 : kd(e, t);
	return r === void 0 ? n : r;
}
//#endregion
//#region node_modules/lodash-es/_arrayPush.js
function jd(e, t) {
	for (var n = -1, r = t.length, i = e.length; ++n < r;) e[i + n] = t[n];
	return e;
}
//#endregion
//#region node_modules/lodash-es/_getPrototype.js
var Md = Mu(Object.getPrototypeOf, Object), Nd = "[object Object]", Pd = Function.prototype, Fd = Object.prototype, Id = Pd.toString, Ld = Fd.hasOwnProperty, Rd = Id.call(Object);
function zd(e) {
	if (!Mc(e) || jc(e) != Nd) return !1;
	var t = Md(e);
	if (t === null) return !0;
	var n = Ld.call(t, "constructor") && t.constructor;
	return typeof n == "function" && n instanceof n && Id.call(n) == Rd;
}
//#endregion
//#region node_modules/lodash-es/_baseSlice.js
function Bd(e, t, n) {
	var r = -1, i = e.length;
	t < 0 && (t = -t > i ? 0 : i + t), n = n > i ? i : n, n < 0 && (n += i), i = t > n ? 0 : n - t >>> 0, t >>>= 0;
	for (var a = Array(i); ++r < i;) a[r] = e[r + t];
	return a;
}
//#endregion
//#region node_modules/lodash-es/_castSlice.js
function Vd(e, t, n) {
	var r = e.length;
	return n = n === void 0 ? r : n, !t && n >= r ? e : Bd(e, t, n);
}
//#endregion
//#region node_modules/lodash-es/_hasUnicode.js
var Hd = RegExp("[\\u200d\\ud800-\\udfff\\u0300-\\u036f\\ufe20-\\ufe2f\\u20d0-\\u20ff\\ufe0e\\ufe0f]");
function Ud(e) {
	return Hd.test(e);
}
//#endregion
//#region node_modules/lodash-es/_asciiToArray.js
function Wd(e) {
	return e.split("");
}
//#endregion
//#region node_modules/lodash-es/_unicodeToArray.js
var Gd = "\\ud800-\\udfff", Kd = "\\u0300-\\u036f\\ufe20-\\ufe2f\\u20d0-\\u20ff", qd = "\\ufe0e\\ufe0f", Jd = "[" + Gd + "]", Yd = "[" + Kd + "]", Xd = "\\ud83c[\\udffb-\\udfff]", Zd = "(?:" + Yd + "|" + Xd + ")", Qd = "[^" + Gd + "]", $d = "(?:\\ud83c[\\udde6-\\uddff]){2}", ef = "[\\ud800-\\udbff][\\udc00-\\udfff]", tf = "\\u200d", nf = Zd + "?", rf = "[" + qd + "]?", af = "(?:" + tf + "(?:" + [
	Qd,
	$d,
	ef
].join("|") + ")" + rf + nf + ")*", of = rf + nf + af, sf = "(?:" + [
	Qd + Yd + "?",
	Yd,
	$d,
	ef,
	Jd
].join("|") + ")", cf = RegExp(Xd + "(?=" + Xd + ")|" + sf + of, "g");
function lf(e) {
	return e.match(cf) || [];
}
//#endregion
//#region node_modules/lodash-es/_stringToArray.js
function uf(e) {
	return Ud(e) ? lf(e) : Wd(e);
}
//#endregion
//#region node_modules/lodash-es/_createCaseFirst.js
function df(e) {
	return function(t) {
		t = Td(t);
		var n = Ud(t) ? uf(t) : void 0, r = n ? n[0] : t.charAt(0), i = n ? Vd(n, 1).join("") : t.slice(1);
		return r[e]() + i;
	};
}
//#endregion
//#region node_modules/lodash-es/upperFirst.js
var ff = df("toUpperCase");
//#endregion
//#region node_modules/lodash-es/_stackClear.js
function pf() {
	this.__data__ = new cd(), this.size = 0;
}
//#endregion
//#region node_modules/lodash-es/_stackDelete.js
function mf(e) {
	var t = this.__data__, n = t.delete(e);
	return this.size = t.size, n;
}
//#endregion
//#region node_modules/lodash-es/_stackGet.js
function hf(e) {
	return this.__data__.get(e);
}
//#endregion
//#region node_modules/lodash-es/_stackHas.js
function gf(e) {
	return this.__data__.has(e);
}
//#endregion
//#region node_modules/lodash-es/_stackSet.js
var _f = 200;
function vf(e, t) {
	var n = this.__data__;
	if (n instanceof cd) {
		var r = n.__data__;
		if (!ld || r.length < _f - 1) return r.push([e, t]), this.size = ++n.size, this;
		n = this.__data__ = new _d(r);
	}
	return n.set(e, t), this.size = n.size, this;
}
//#endregion
//#region node_modules/lodash-es/_Stack.js
function yf(e) {
	var t = this.__data__ = new cd(e);
	this.size = t.size;
}
yf.prototype.clear = pf, yf.prototype.delete = mf, yf.prototype.get = hf, yf.prototype.has = gf, yf.prototype.set = vf;
//#endregion
//#region node_modules/lodash-es/_cloneBuffer.js
var bf = typeof exports == "object" && exports && !exports.nodeType && exports, xf = bf && typeof module == "object" && module && !module.nodeType && module, Sf = xf && xf.exports === bf ? yc.Buffer : void 0, Cf = Sf ? Sf.allocUnsafe : void 0;
function wf(e, t) {
	if (t) return e.slice();
	var n = e.length, r = Cf ? Cf(n) : new e.constructor(n);
	return e.copy(r), r;
}
//#endregion
//#region node_modules/lodash-es/_arrayFilter.js
function Tf(e, t) {
	for (var n = -1, r = e == null ? 0 : e.length, i = 0, a = []; ++n < r;) {
		var o = e[n];
		t(o, n, e) && (a[i++] = o);
	}
	return a;
}
//#endregion
//#region node_modules/lodash-es/stubArray.js
function Ef() {
	return [];
}
//#endregion
//#region node_modules/lodash-es/_getSymbols.js
var Df = Object.prototype.propertyIsEnumerable, Of = Object.getOwnPropertySymbols, kf = Of ? function(e) {
	return e == null ? [] : (e = Object(e), Tf(Of(e), function(t) {
		return Df.call(e, t);
	}));
} : Ef;
//#endregion
//#region node_modules/lodash-es/_baseGetAllKeys.js
function Af(e, t, n) {
	var r = t(e);
	return Ic(e) ? r : jd(r, n(e));
}
//#endregion
//#region node_modules/lodash-es/_getAllKeys.js
function jf(e) {
	return Af(e, Iu, kf);
}
//#endregion
//#region node_modules/lodash-es/_DataView.js
var Mf = cl(yc, "DataView"), Nf = cl(yc, "Promise"), Pf = cl(yc, "Set"), Ff = "[object Map]", If = "[object Object]", Lf = "[object Promise]", Rf = "[object Set]", zf = "[object WeakMap]", Bf = "[object DataView]", Vf = Qc(Mf), Hf = Qc(ld), Uf = Qc(Nf), Wf = Qc(Pf), Gf = Qc(ll), Kf = jc;
(Mf && Kf(new Mf(/* @__PURE__ */ new ArrayBuffer(1))) != Bf || ld && Kf(new ld()) != Ff || Nf && Kf(Nf.resolve()) != Lf || Pf && Kf(new Pf()) != Rf || ll && Kf(new ll()) != zf) && (Kf = function(e) {
	var t = jc(e), n = t == If ? e.constructor : void 0, r = n ? Qc(n) : "";
	if (r) switch (r) {
		case Vf: return Bf;
		case Hf: return Ff;
		case Uf: return Lf;
		case Wf: return Rf;
		case Gf: return zf;
	}
	return t;
});
var qf = Kf, Jf = yc.Uint8Array;
//#endregion
//#region node_modules/lodash-es/_cloneArrayBuffer.js
function Yf(e) {
	var t = new e.constructor(e.byteLength);
	return new Jf(t).set(new Jf(e)), t;
}
//#endregion
//#region node_modules/lodash-es/_cloneTypedArray.js
function Xf(e, t) {
	var n = t ? Yf(e.buffer) : e.buffer;
	return new e.constructor(n, e.byteOffset, e.length);
}
//#endregion
//#region node_modules/lodash-es/_initCloneObject.js
function Zf(e) {
	return typeof e.constructor == "function" && !Rl(e) ? dl(Md(e)) : {};
}
//#endregion
//#region node_modules/lodash-es/_setCacheAdd.js
var Qf = "__lodash_hash_undefined__";
function $f(e) {
	return this.__data__.set(e, Qf), this;
}
//#endregion
//#region node_modules/lodash-es/_setCacheHas.js
function ep(e) {
	return this.__data__.has(e);
}
//#endregion
//#region node_modules/lodash-es/_SetCache.js
function tp(e) {
	var t = -1, n = e == null ? 0 : e.length;
	for (this.__data__ = new _d(); ++t < n;) this.add(e[t]);
}
tp.prototype.add = tp.prototype.push = $f, tp.prototype.has = ep;
//#endregion
//#region node_modules/lodash-es/_arraySome.js
function np(e, t) {
	for (var n = -1, r = e == null ? 0 : e.length; ++n < r;) if (t(e[n], n, e)) return !0;
	return !1;
}
//#endregion
//#region node_modules/lodash-es/_cacheHas.js
function rp(e, t) {
	return e.has(t);
}
//#endregion
//#region node_modules/lodash-es/_equalArrays.js
var ip = 1, ap = 2;
function op(e, t, n, r, i, a) {
	var o = n & ip, s = e.length, c = t.length;
	if (s != c && !(o && c > s)) return !1;
	var l = a.get(e), u = a.get(t);
	if (l && u) return l == t && u == e;
	var d = -1, f = !0, p = n & ap ? new tp() : void 0;
	for (a.set(e, t), a.set(t, e); ++d < s;) {
		var m = e[d], h = t[d];
		if (r) var g = o ? r(h, m, d, t, e, a) : r(m, h, d, e, t, a);
		if (g !== void 0) {
			if (g) continue;
			f = !1;
			break;
		}
		if (p) {
			if (!np(t, function(e, t) {
				if (!rp(p, t) && (m === e || i(m, e, n, r, a))) return p.push(t);
			})) {
				f = !1;
				break;
			}
		} else if (!(m === h || i(m, h, n, r, a))) {
			f = !1;
			break;
		}
	}
	return a.delete(e), a.delete(t), f;
}
//#endregion
//#region node_modules/lodash-es/_mapToArray.js
function sp(e) {
	var t = -1, n = Array(e.size);
	return e.forEach(function(e, r) {
		n[++t] = [r, e];
	}), n;
}
//#endregion
//#region node_modules/lodash-es/_setToArray.js
function cp(e) {
	var t = -1, n = Array(e.size);
	return e.forEach(function(e) {
		n[++t] = e;
	}), n;
}
//#endregion
//#region node_modules/lodash-es/_equalByTag.js
var lp = 1, up = 2, dp = "[object Boolean]", fp = "[object Date]", pp = "[object Error]", mp = "[object Map]", hp = "[object Number]", gp = "[object RegExp]", _p = "[object Set]", vp = "[object String]", yp = "[object Symbol]", bp = "[object ArrayBuffer]", xp = "[object DataView]", Sp = bc ? bc.prototype : void 0, Cp = Sp ? Sp.valueOf : void 0;
function wp(e, t, n, r, i, a, o) {
	switch (n) {
		case xp:
			if (e.byteLength != t.byteLength || e.byteOffset != t.byteOffset) return !1;
			e = e.buffer, t = t.buffer;
		case bp: return !(e.byteLength != t.byteLength || !a(new Jf(e), new Jf(t)));
		case dp:
		case fp:
		case hp: return Tl(+e, +t);
		case pp: return e.name == t.name && e.message == t.message;
		case gp:
		case vp: return e == t + "";
		case mp: var s = sp;
		case _p:
			var c = r & lp;
			if (s ||= cp, e.size != t.size && !c) return !1;
			var l = o.get(e);
			if (l) return l == t;
			r |= up, o.set(e, t);
			var u = op(s(e), s(t), r, i, a, o);
			return o.delete(e), u;
		case yp: if (Cp) return Cp.call(e) == Cp.call(t);
	}
	return !1;
}
//#endregion
//#region node_modules/lodash-es/_equalObjects.js
var Tp = 1, Ep = Object.prototype.hasOwnProperty;
function Dp(e, t, n, r, i, a) {
	var o = n & Tp, s = jf(e), c = s.length;
	if (c != jf(t).length && !o) return !1;
	for (var l = c; l--;) {
		var u = s[l];
		if (!(o ? u in t : Ep.call(t, u))) return !1;
	}
	var d = a.get(e), f = a.get(t);
	if (d && f) return d == t && f == e;
	var p = !0;
	a.set(e, t), a.set(t, e);
	for (var m = o; ++l < c;) {
		u = s[l];
		var h = e[u], g = t[u];
		if (r) var _ = o ? r(g, h, u, t, e, a) : r(h, g, u, e, t, a);
		if (!(_ === void 0 ? h === g || i(h, g, n, r, a) : _)) {
			p = !1;
			break;
		}
		m ||= u == "constructor";
	}
	if (p && !m) {
		var v = e.constructor, y = t.constructor;
		v != y && "constructor" in e && "constructor" in t && !(typeof v == "function" && v instanceof v && typeof y == "function" && y instanceof y) && (p = !1);
	}
	return a.delete(e), a.delete(t), p;
}
//#endregion
//#region node_modules/lodash-es/_baseIsEqualDeep.js
var Op = 1, kp = "[object Arguments]", Ap = "[object Array]", jp = "[object Object]", Mp = Object.prototype.hasOwnProperty;
function Np(e, t, n, r, i, a) {
	var o = Ic(e), s = Ic(t), c = o ? Ap : qf(e), l = s ? Ap : qf(t);
	c = c == kp ? jp : c, l = l == kp ? jp : l;
	var u = c == jp, d = l == jp, f = c == l;
	if (f && Xl(e)) {
		if (!Xl(t)) return !1;
		o = !0, u = !1;
	}
	if (f && !u) return a ||= new yf(), o || ku(e) ? op(e, t, n, r, i, a) : wp(e, t, c, n, r, i, a);
	if (!(n & Op)) {
		var p = u && Mp.call(e, "__wrapped__"), m = d && Mp.call(t, "__wrapped__");
		if (p || m) {
			var h = p ? e.value() : e, g = m ? t.value() : t;
			return a ||= new yf(), i(h, g, n, r, a);
		}
	}
	return f ? (a ||= new yf(), Dp(e, t, n, r, i, a)) : !1;
}
//#endregion
//#region node_modules/lodash-es/_baseIsEqual.js
function Pp(e, t, n, r, i) {
	return e === t ? !0 : e == null || t == null || !Mc(e) && !Mc(t) ? e !== e && t !== t : Np(e, t, n, r, Pp, i);
}
//#endregion
//#region node_modules/lodash-es/_baseIsMatch.js
var Fp = 1, Ip = 2;
function Lp(e, t, n, r) {
	var i = n.length, a = i, o = !r;
	if (e == null) return !a;
	for (e = Object(e); i--;) {
		var s = n[i];
		if (o && s[2] ? s[1] !== e[s[0]] : !(s[0] in e)) return !1;
	}
	for (; ++i < a;) {
		s = n[i];
		var c = s[0], l = e[c], u = s[1];
		if (o && s[2]) {
			if (l === void 0 && !(c in e)) return !1;
		} else {
			var d = new yf();
			if (r) var f = r(l, u, c, e, t, d);
			if (!(f === void 0 ? Pp(u, l, Fp | Ip, r, d) : f)) return !1;
		}
	}
	return !0;
}
//#endregion
//#region node_modules/lodash-es/_isStrictComparable.js
function Rp(e) {
	return e === e && !Vc(e);
}
//#endregion
//#region node_modules/lodash-es/_getMatchData.js
function zp(e) {
	for (var t = Iu(e), n = t.length; n--;) {
		var r = t[n], i = e[r];
		t[n] = [
			r,
			i,
			Rp(i)
		];
	}
	return t;
}
//#endregion
//#region node_modules/lodash-es/_matchesStrictComparable.js
function Bp(e, t) {
	return function(n) {
		return n != null && n[e] === t && (t !== void 0 || e in Object(n));
	};
}
//#endregion
//#region node_modules/lodash-es/_baseMatches.js
function Vp(e) {
	var t = zp(e);
	return t.length == 1 && t[0][2] ? Bp(t[0][0], t[0][1]) : function(n) {
		return n === e || Lp(n, e, t);
	};
}
//#endregion
//#region node_modules/lodash-es/_baseHasIn.js
function Hp(e, t) {
	return e != null && t in Object(e);
}
//#endregion
//#region node_modules/lodash-es/_hasPath.js
function Up(e, t, n) {
	t = Ed(t, e);
	for (var r = -1, i = t.length, a = !1; ++r < i;) {
		var o = Od(t[r]);
		if (!(a = e != null && n(e, o))) break;
		e = e[o];
	}
	return a || ++r != i ? a : (i = e == null ? 0 : e.length, !!i && Nl(i) && Cl(o, i) && (Ic(e) || Gl(e)));
}
//#endregion
//#region node_modules/lodash-es/hasIn.js
function Wp(e, t) {
	return e != null && Up(e, t, Hp);
}
//#endregion
//#region node_modules/lodash-es/_baseMatchesProperty.js
var Gp = 1, Kp = 2;
function qp(e, t) {
	return Uu(e) && Rp(t) ? Bp(Od(e), t) : function(n) {
		var r = Ad(n, e);
		return r === void 0 && r === t ? Wp(n, e) : Pp(t, r, Gp | Kp);
	};
}
//#endregion
//#region node_modules/lodash-es/_baseProperty.js
function Jp(e) {
	return function(t) {
		return t?.[e];
	};
}
//#endregion
//#region node_modules/lodash-es/_basePropertyDeep.js
function Yp(e) {
	return function(t) {
		return kd(t, e);
	};
}
//#endregion
//#region node_modules/lodash-es/property.js
function Xp(e) {
	return Uu(e) ? Jp(Od(e)) : Yp(e);
}
//#endregion
//#region node_modules/lodash-es/_baseIteratee.js
function Zp(e) {
	return typeof e == "function" ? e : e == null ? Hc : typeof e == "object" ? Ic(e) ? qp(e[0], e[1]) : Vp(e) : Xp(e);
}
//#endregion
//#region node_modules/lodash-es/_createBaseFor.js
function Qp(e) {
	return function(t, n, r) {
		for (var i = -1, a = Object(t), o = r(t), s = o.length; s--;) {
			var c = o[e ? s : ++i];
			if (n(a[c], c, a) === !1) break;
		}
		return t;
	};
}
//#endregion
//#region node_modules/lodash-es/_baseFor.js
var $p = Qp();
//#endregion
//#region node_modules/lodash-es/_baseForOwn.js
function em(e, t) {
	return e && $p(e, t, Iu);
}
//#endregion
//#region node_modules/lodash-es/_createBaseEach.js
function tm(e, t) {
	return function(n, r) {
		if (n == null) return n;
		if (!Pl(n)) return e(n, r);
		for (var i = n.length, a = t ? i : -1, o = Object(n); (t ? a-- : ++a < i) && r(o[a], a, o) !== !1;);
		return n;
	};
}
//#endregion
//#region node_modules/lodash-es/_baseEach.js
var nm = tm(em);
//#endregion
//#region node_modules/lodash-es/_assignMergeValue.js
function rm(e, t, n) {
	(n !== void 0 && !Tl(e[t], n) || n === void 0 && !(t in e)) && wl(e, t, n);
}
//#endregion
//#region node_modules/lodash-es/isArrayLikeObject.js
function im(e) {
	return Mc(e) && Pl(e);
}
//#endregion
//#region node_modules/lodash-es/_safeGet.js
function am(e, t) {
	if ((t !== "constructor" || typeof e[t] != "function") && t != "__proto__") return e[t];
}
//#endregion
//#region node_modules/lodash-es/toPlainObject.js
function om(e) {
	return Ol(e, Bu(e));
}
//#endregion
//#region node_modules/lodash-es/_baseMergeDeep.js
function sm(e, t, n, r, i, a, o) {
	var s = am(e, n), c = am(t, n), l = o.get(c);
	if (l) {
		rm(e, n, l);
		return;
	}
	var u = a ? a(s, c, n + "", e, t, o) : void 0, d = u === void 0;
	if (d) {
		var f = Ic(c), p = !f && Xl(c), m = !f && !p && ku(c);
		u = c, f || p || m ? Ic(s) ? u = s : im(s) ? u = pl(s) : p ? (d = !1, u = wf(c, !0)) : m ? (d = !1, u = Xf(c, !0)) : u = [] : zd(c) || Gl(c) ? (u = s, Gl(s) ? u = om(s) : (!Vc(s) || qc(s)) && (u = Zf(c))) : d = !1;
	}
	d && (o.set(c, u), i(u, c, r, a, o), o.delete(c)), rm(e, n, u);
}
//#endregion
//#region node_modules/lodash-es/_baseMerge.js
function cm(e, t, n, r, i) {
	e !== t && $p(t, function(a, o) {
		if (i ||= new yf(), Vc(a)) sm(e, t, o, n, cm, r, i);
		else {
			var s = r ? r(am(e, o), a, o + "", e, t, i) : void 0;
			s === void 0 && (s = a), rm(e, o, s);
		}
	}, Bu);
}
//#endregion
//#region node_modules/lodash-es/_baseMap.js
function lm(e, t) {
	var n = -1, r = Pl(e) ? Array(e.length) : [];
	return nm(e, function(e, i, a) {
		r[++n] = t(e, i, a);
	}), r;
}
//#endregion
//#region node_modules/lodash-es/map.js
function um(e, t) {
	return (Ic(e) ? Fc : lm)(e, Zp(t, 3));
}
//#endregion
//#region node_modules/lodash-es/merge.js
var dm = Il(function(e, t, n) {
	cm(e, t, n);
});
//#endregion
//#region node_modules/naive-ui/es/_utils/naive/warn.mjs
function fm(e, t) {
	console.error(`[naive/${e}]: ${t}`);
}
function pm(e, t) {
	throw Error(`[naive/${e}]: ${t}`);
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/keysOf.mjs
function mm(e) {
	return Object.keys(e);
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/create-injection-key.mjs
function hm(e) {
	return e;
}
//#endregion
//#region node_modules/naive-ui/es/config-provider/src/context.mjs
var gm = hm("n-config-provider");
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-config.mjs
function _m(e = {}, t = { defaultBordered: !0 }) {
	let n = Bn(gm, null);
	return {
		inlineThemeDisabled: n?.inlineThemeDisabled,
		mergedRtlRef: n?.mergedRtlRef,
		mergedComponentPropsRef: n?.mergedComponentPropsRef,
		mergedBreakpointsRef: n?.mergedBreakpointsRef,
		mergedBorderedRef: z(() => {
			let { bordered: r } = e;
			return r === void 0 ? n?.mergedBorderedRef.value ?? t.defaultBordered ?? !0 : r;
		}),
		mergedClsPrefixRef: n ? n.mergedClsPrefixRef : /* @__PURE__ */ Yt("n"),
		namespaceRef: z(() => n?.mergedNamespaceRef.value)
	};
}
//#endregion
//#region node_modules/naive-ui/es/_mixins/common.mjs
var vm = "naive-ui-style", ym = {
	fontFamily: "v-sans, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif, \"Apple Color Emoji\", \"Segoe UI Emoji\", \"Segoe UI Symbol\"",
	fontFamilyMono: "v-mono, SFMono-Regular, Menlo, Consolas, Courier, monospace",
	fontWeight: "400",
	fontWeightStrong: "500",
	cubicBezierEaseInOut: "cubic-bezier(.4, 0, .2, 1)",
	cubicBezierEaseOut: "cubic-bezier(0, 0, .2, 1)",
	cubicBezierEaseIn: "cubic-bezier(.4, 0, 1, 1)",
	borderRadius: "3px",
	borderRadiusSmall: "2px",
	fontSize: "14px",
	fontSizeMini: "12px",
	fontSizeTiny: "12px",
	fontSizeSmall: "14px",
	fontSizeMedium: "14px",
	fontSizeLarge: "15px",
	fontSizeHuge: "16px",
	lineHeight: "1.6",
	heightMini: "16px",
	heightTiny: "22px",
	heightSmall: "28px",
	heightMedium: "34px",
	heightLarge: "40px",
	heightHuge: "46px"
}, { fontSize: bm, fontFamily: xm, lineHeight: Sm } = ym, Cm = B("body", `
 margin: 0;
 font-size: ${bm};
 font-family: ${xm};
 line-height: ${Sm};
 -webkit-text-size-adjust: 100%;
 -webkit-tap-highlight-color: transparent;
`, [B("input", "\n font-family: inherit;\n font-size: inherit;\n ")]), wm = "@css-render/vue3-ssr";
function Tm(e, t) {
	return `<style cssr-id="${e}">\n${t}\n</style>`;
}
function Em(e, t, n) {
	let { styles: r, ids: i } = n;
	i.has(e) || r !== null && (i.add(e), r.push(Tm(e, t)));
}
var Dm = typeof document < "u";
function Om() {
	if (Dm) return;
	let e = Bn(wm, null);
	if (e !== null) return {
		adapter: (t, n) => Em(t, n, e),
		context: e
	};
}
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-style.mjs
function km(e, t, n) {
	if (!t) return;
	let r = Om(), i = Bn(gm, null), a = () => {
		let a = n.value;
		t.mount({
			id: a === void 0 ? e : a + e,
			head: !0,
			anchorMetaName: vm,
			props: { bPrefix: a ? `.${a}-` : void 0 },
			ssr: r,
			parent: i?.styleMountTarget
		}), i?.preflightStyleDisabled || Cm.mount({
			id: "n-global",
			head: !0,
			anchorMetaName: vm,
			ssr: r,
			parent: i?.styleMountTarget
		});
	};
	r ? a() : Pr(a);
}
//#endregion
//#region node_modules/naive-ui/es/vue-jsx-vapor/vdom.mjs
var Am = /* @__PURE__ */ new WeakMap();
function jm(e) {
	let t = da();
	if (t) {
		Am.has(t) || Am.set(t, {});
		let n = Am.get(t);
		return n[e] || (n[e] = []);
	}
	return [];
}
function G(e, t = 1) {
	let n = Xi, r = !1;
	return typeof e == "function" && (r = !0, F(), n = L, e = e()), Ki(e) ? r ? L(Mm(e)) : Mm(e) : Array.isArray(e) ? r ? I(P, null, e.map((e) => G(() => e)), -2) : R(P, null, e.slice()) : e == null || typeof e == "boolean" ? n(Ri) : n(Li, null, String(e), t);
}
function Mm(e) {
	return e.el === null && e.patchFlag !== -1 || e.memo ? e : $i(e);
}
var Nm = (e) => Array.isArray(e) ? e.map((e) => G(e)) : [G(e)], Pm = (e) => e._n ? e : In((...t) => Nm(e(...t))), Fm = (e) => typeof e == "function" || Object.prototype.toString.call(e) === "[object Object]" && !Ki(e) ? e : { default: In(() => [G(() => e)]) }, K = (e) => he(e) || null, Im = [], Lm = /* @__PURE__ */ new WeakMap();
function Rm() {
	Im.forEach((e) => e(...Lm.get(e))), Im = [];
}
function zm(e, ...t) {
	Lm.set(e, t), !Im.includes(e) && Im.push(e) === 1 && requestAnimationFrame(Rm);
}
//#endregion
//#region node_modules/seemly/es/dom/happens-in.js
function Bm(e, t) {
	let { target: n } = e;
	for (; n;) {
		if (n.dataset && n.dataset[t] !== void 0) return !0;
		n = n.parentElement;
	}
	return !1;
}
//#endregion
//#region node_modules/seemly/es/dom/get-precise-event-target.js
function Vm(e) {
	return e.composedPath()[0] || null;
}
//#endregion
//#region node_modules/seemly/es/css/index.js
function Hm(e) {
	return typeof e == "string" ? e.endsWith("px") ? Number(e.slice(0, e.length - 2)) : Number(e) : e;
}
function Um(e) {
	if (e != null) return typeof e == "number" ? `${e}px` : e.endsWith("px") ? e : `${e}px`;
}
function Wm(e, t) {
	let n = e.trim().split(/\s+/g), r = { top: n[0] };
	switch (n.length) {
		case 1:
			r.right = n[0], r.bottom = n[0], r.left = n[0];
			break;
		case 2:
			r.right = n[1], r.left = n[1], r.bottom = n[0];
			break;
		case 3:
			r.right = n[1], r.bottom = n[2], r.left = n[1];
			break;
		case 4:
			r.right = n[1], r.bottom = n[2], r.left = n[3];
			break;
		default: throw Error("[seemly/getMargin]:" + e + " is not a valid value.");
	}
	return t === void 0 ? r : r[t];
}
function Gm(e, t) {
	let [n, r] = e.split(" ");
	return t ? t === "row" ? n : r : {
		row: n,
		col: r || n
	};
}
//#endregion
//#region node_modules/seemly/es/color/colors.js
var Km = {
	aliceblue: "#F0F8FF",
	antiquewhite: "#FAEBD7",
	aqua: "#0FF",
	aquamarine: "#7FFFD4",
	azure: "#F0FFFF",
	beige: "#F5F5DC",
	bisque: "#FFE4C4",
	black: "#000",
	blanchedalmond: "#FFEBCD",
	blue: "#00F",
	blueviolet: "#8A2BE2",
	brown: "#A52A2A",
	burlywood: "#DEB887",
	cadetblue: "#5F9EA0",
	chartreuse: "#7FFF00",
	chocolate: "#D2691E",
	coral: "#FF7F50",
	cornflowerblue: "#6495ED",
	cornsilk: "#FFF8DC",
	crimson: "#DC143C",
	cyan: "#0FF",
	darkblue: "#00008B",
	darkcyan: "#008B8B",
	darkgoldenrod: "#B8860B",
	darkgray: "#A9A9A9",
	darkgrey: "#A9A9A9",
	darkgreen: "#006400",
	darkkhaki: "#BDB76B",
	darkmagenta: "#8B008B",
	darkolivegreen: "#556B2F",
	darkorange: "#FF8C00",
	darkorchid: "#9932CC",
	darkred: "#8B0000",
	darksalmon: "#E9967A",
	darkseagreen: "#8FBC8F",
	darkslateblue: "#483D8B",
	darkslategray: "#2F4F4F",
	darkslategrey: "#2F4F4F",
	darkturquoise: "#00CED1",
	darkviolet: "#9400D3",
	deeppink: "#FF1493",
	deepskyblue: "#00BFFF",
	dimgray: "#696969",
	dimgrey: "#696969",
	dodgerblue: "#1E90FF",
	firebrick: "#B22222",
	floralwhite: "#FFFAF0",
	forestgreen: "#228B22",
	fuchsia: "#F0F",
	gainsboro: "#DCDCDC",
	ghostwhite: "#F8F8FF",
	gold: "#FFD700",
	goldenrod: "#DAA520",
	gray: "#808080",
	grey: "#808080",
	green: "#008000",
	greenyellow: "#ADFF2F",
	honeydew: "#F0FFF0",
	hotpink: "#FF69B4",
	indianred: "#CD5C5C",
	indigo: "#4B0082",
	ivory: "#FFFFF0",
	khaki: "#F0E68C",
	lavender: "#E6E6FA",
	lavenderblush: "#FFF0F5",
	lawngreen: "#7CFC00",
	lemonchiffon: "#FFFACD",
	lightblue: "#ADD8E6",
	lightcoral: "#F08080",
	lightcyan: "#E0FFFF",
	lightgoldenrodyellow: "#FAFAD2",
	lightgray: "#D3D3D3",
	lightgrey: "#D3D3D3",
	lightgreen: "#90EE90",
	lightpink: "#FFB6C1",
	lightsalmon: "#FFA07A",
	lightseagreen: "#20B2AA",
	lightskyblue: "#87CEFA",
	lightslategray: "#778899",
	lightslategrey: "#778899",
	lightsteelblue: "#B0C4DE",
	lightyellow: "#FFFFE0",
	lime: "#0F0",
	limegreen: "#32CD32",
	linen: "#FAF0E6",
	magenta: "#F0F",
	maroon: "#800000",
	mediumaquamarine: "#66CDAA",
	mediumblue: "#0000CD",
	mediumorchid: "#BA55D3",
	mediumpurple: "#9370DB",
	mediumseagreen: "#3CB371",
	mediumslateblue: "#7B68EE",
	mediumspringgreen: "#00FA9A",
	mediumturquoise: "#48D1CC",
	mediumvioletred: "#C71585",
	midnightblue: "#191970",
	mintcream: "#F5FFFA",
	mistyrose: "#FFE4E1",
	moccasin: "#FFE4B5",
	navajowhite: "#FFDEAD",
	navy: "#000080",
	oldlace: "#FDF5E6",
	olive: "#808000",
	olivedrab: "#6B8E23",
	orange: "#FFA500",
	orangered: "#FF4500",
	orchid: "#DA70D6",
	palegoldenrod: "#EEE8AA",
	palegreen: "#98FB98",
	paleturquoise: "#AFEEEE",
	palevioletred: "#DB7093",
	papayawhip: "#FFEFD5",
	peachpuff: "#FFDAB9",
	peru: "#CD853F",
	pink: "#FFC0CB",
	plum: "#DDA0DD",
	powderblue: "#B0E0E6",
	purple: "#800080",
	rebeccapurple: "#663399",
	red: "#F00",
	rosybrown: "#BC8F8F",
	royalblue: "#4169E1",
	saddlebrown: "#8B4513",
	salmon: "#FA8072",
	sandybrown: "#F4A460",
	seagreen: "#2E8B57",
	seashell: "#FFF5EE",
	sienna: "#A0522D",
	silver: "#C0C0C0",
	skyblue: "#87CEEB",
	slateblue: "#6A5ACD",
	slategray: "#708090",
	slategrey: "#708090",
	snow: "#FFFAFA",
	springgreen: "#00FF7F",
	steelblue: "#4682B4",
	tan: "#D2B48C",
	teal: "#008080",
	thistle: "#D8BFD8",
	tomato: "#FF6347",
	turquoise: "#40E0D0",
	violet: "#EE82EE",
	wheat: "#F5DEB3",
	white: "#FFF",
	whitesmoke: "#F5F5F5",
	yellow: "#FF0",
	yellowgreen: "#9ACD32",
	transparent: "#0000"
};
//#endregion
//#region node_modules/seemly/es/color/convert.js
function qm(e, t, n) {
	t /= 100, n /= 100;
	let r = (r, i = (r + e / 60) % 6) => n - n * t * Math.max(Math.min(i, 4 - i, 1), 0);
	return [
		r(5) * 255,
		r(3) * 255,
		r(1) * 255
	];
}
function Jm(e, t, n) {
	t /= 100, n /= 100;
	let r = t * Math.min(n, 1 - n), i = (t, i = (t + e / 30) % 12) => n - r * Math.max(Math.min(i - 3, 9 - i, 1), -1);
	return [
		i(0) * 255,
		i(8) * 255,
		i(4) * 255
	];
}
//#endregion
//#region node_modules/seemly/es/color/index.js
var Ym = "^\\s*", Xm = "\\s*$", Zm = "\\s*((\\.\\d+)|(\\d+(\\.\\d*)?))%\\s*", Qm = "\\s*((\\.\\d+)|(\\d+(\\.\\d*)?))\\s*", $m = "([0-9A-Fa-f])", eh = "([0-9A-Fa-f]{2})", th = RegExp(`${Ym}hsl\\s*\\(${Qm},${Zm},${Zm}\\)${Xm}`), nh = RegExp(`${Ym}hsv\\s*\\(${Qm},${Zm},${Zm}\\)${Xm}`), rh = RegExp(`${Ym}hsla\\s*\\(${Qm},${Zm},${Zm},${Qm}\\)${Xm}`), ih = RegExp(`${Ym}hsva\\s*\\(${Qm},${Zm},${Zm},${Qm}\\)${Xm}`), ah = RegExp(`${Ym}rgb\\s*\\(${Qm},${Qm},${Qm}\\)${Xm}`), oh = RegExp(`${Ym}rgba\\s*\\(${Qm},${Qm},${Qm},${Qm}\\)${Xm}`), sh = RegExp(`${Ym}#${$m}${$m}${$m}${Xm}`), ch = RegExp(`${Ym}#${eh}${eh}${eh}${Xm}`), lh = RegExp(`${Ym}#${$m}${$m}${$m}${$m}${Xm}`), uh = RegExp(`${Ym}#${eh}${eh}${eh}${eh}${Xm}`);
function dh(e) {
	return parseInt(e, 16);
}
function fh(e) {
	try {
		let t;
		if (t = rh.exec(e)) return [
			bh(t[1]),
			Sh(t[5]),
			Sh(t[9]),
			yh(t[13])
		];
		if (t = th.exec(e)) return [
			bh(t[1]),
			Sh(t[5]),
			Sh(t[9]),
			1
		];
		throw Error(`[seemly/hsla]: Invalid color value ${e}.`);
	} catch (e) {
		throw e;
	}
}
function ph(e) {
	try {
		let t;
		if (t = ih.exec(e)) return [
			bh(t[1]),
			Sh(t[5]),
			Sh(t[9]),
			yh(t[13])
		];
		if (t = nh.exec(e)) return [
			bh(t[1]),
			Sh(t[5]),
			Sh(t[9]),
			1
		];
		throw Error(`[seemly/hsva]: Invalid color value ${e}.`);
	} catch (e) {
		throw e;
	}
}
function mh(e) {
	try {
		let t;
		if (t = ch.exec(e)) return [
			dh(t[1]),
			dh(t[2]),
			dh(t[3]),
			1
		];
		if (t = ah.exec(e)) return [
			xh(t[1]),
			xh(t[5]),
			xh(t[9]),
			1
		];
		if (t = oh.exec(e)) return [
			xh(t[1]),
			xh(t[5]),
			xh(t[9]),
			yh(t[13])
		];
		if (t = sh.exec(e)) return [
			dh(t[1] + t[1]),
			dh(t[2] + t[2]),
			dh(t[3] + t[3]),
			1
		];
		if (t = uh.exec(e)) return [
			dh(t[1]),
			dh(t[2]),
			dh(t[3]),
			yh(dh(t[4]) / 255)
		];
		if (t = lh.exec(e)) return [
			dh(t[1] + t[1]),
			dh(t[2] + t[2]),
			dh(t[3] + t[3]),
			yh(dh(t[4] + t[4]) / 255)
		];
		if (e in Km) return mh(Km[e]);
		if (th.test(e) || rh.test(e)) {
			let [t, n, r, i] = fh(e);
			return [...Jm(t, n, r), i];
		}
		if (nh.test(e) || ih.test(e)) {
			let [t, n, r, i] = ph(e);
			return [...qm(t, n, r), i];
		}
		throw Error(`[seemly/rgba]: Invalid color value ${e}.`);
	} catch (e) {
		throw e;
	}
}
function hh(e) {
	return e > 1 ? 1 : e < 0 ? 0 : e;
}
function gh(e, t, n, r) {
	return `rgba(${xh(e)}, ${xh(t)}, ${xh(n)}, ${hh(r)})`;
}
function _h(e, t, n, r, i) {
	return xh((e * t * (1 - r) + n * r) / i);
}
function q(e, t) {
	Array.isArray(e) || (e = mh(e)), Array.isArray(t) || (t = mh(t));
	let n = e[3], r = t[3], i = yh(n + r - n * r);
	return gh(_h(e[0], n, t[0], r, i), _h(e[1], n, t[1], r, i), _h(e[2], n, t[2], r, i), i);
}
function J(e, t) {
	let [n, r, i, a = 1] = Array.isArray(e) ? e : mh(e);
	return typeof t.alpha == "number" ? gh(n, r, i, t.alpha) : gh(n, r, i, a);
}
function vh(e, t) {
	let [n, r, i, a = 1] = Array.isArray(e) ? e : mh(e), { lightness: o = 1, alpha: s = 1 } = t;
	return Ch([
		n * o,
		r * o,
		i * o,
		a * s
	]);
}
function yh(e) {
	let t = Math.round(Number(e) * 100) / 100;
	return t > 1 ? 1 : t < 0 ? 0 : t;
}
function bh(e) {
	let t = Math.round(Number(e));
	return t >= 360 || t < 0 ? 0 : t;
}
function xh(e) {
	let t = Math.round(Number(e));
	return t > 255 ? 255 : t < 0 ? 0 : t;
}
function Sh(e) {
	let t = Math.round(Number(e));
	return t > 100 ? 100 : t < 0 ? 0 : t;
}
function Ch(e) {
	let [t, n, r] = e;
	return 3 in e ? `rgba(${xh(t)}, ${xh(n)}, ${xh(r)}, ${yh(e[3])})` : `rgba(${xh(t)}, ${xh(n)}, ${xh(r)}, 1)`;
}
//#endregion
//#region node_modules/seemly/es/misc/index.js
function wh(e = 8) {
	return Math.random().toString(16).slice(2, 2 + e);
}
//#endregion
//#region node_modules/naive-ui/es/_styles/common/dark.mjs
var Y = {
	neutralBase: "#000",
	neutralInvertBase: "#fff",
	neutralTextBase: "#fff",
	neutralPopover: "rgb(72, 72, 78)",
	neutralCard: "rgb(24, 24, 28)",
	neutralModal: "rgb(44, 44, 50)",
	neutralBody: "rgb(16, 16, 20)",
	alpha1: "0.9",
	alpha2: "0.82",
	alpha3: "0.52",
	alpha4: "0.38",
	alpha5: "0.28",
	alphaClose: "0.52",
	alphaDisabled: "0.38",
	alphaDisabledInput: "0.06",
	alphaPending: "0.09",
	alphaTablePending: "0.06",
	alphaTableStriped: "0.05",
	alphaPressed: "0.05",
	alphaAvatar: "0.18",
	alphaRail: "0.2",
	alphaProgressRail: "0.12",
	alphaBorder: "0.24",
	alphaDivider: "0.09",
	alphaInput: "0.1",
	alphaAction: "0.06",
	alphaTab: "0.04",
	alphaScrollbar: "0.2",
	alphaScrollbarHover: "0.3",
	alphaCode: "0.12",
	alphaTag: "0.2",
	primaryHover: "#7fe7c4",
	primaryDefault: "#63e2b7",
	primaryActive: "#5acea7",
	primarySuppl: "rgb(42, 148, 125)",
	infoHover: "#8acbec",
	infoDefault: "#70c0e8",
	infoActive: "#66afd3",
	infoSuppl: "rgb(56, 137, 197)",
	errorHover: "#e98b8b",
	errorDefault: "#e88080",
	errorActive: "#e57272",
	errorSuppl: "rgb(208, 58, 82)",
	warningHover: "#f5d599",
	warningDefault: "#f2c97d",
	warningActive: "#e6c260",
	warningSuppl: "rgb(240, 138, 0)",
	successHover: "#7fe7c4",
	successDefault: "#63e2b7",
	successActive: "#5acea7",
	successSuppl: "rgb(42, 148, 125)"
}, Th = mh(Y.neutralBase), Eh = mh(Y.neutralInvertBase), Dh = `rgba(${Eh.slice(0, 3).join(", ")}, `;
function X(e) {
	return `${Dh + String(e)})`;
}
function Oh(e) {
	let t = Array.from(Eh);
	return t[3] = Number(e), q(Th, t);
}
var Z = {
	name: "common",
	...ym,
	baseColor: Y.neutralBase,
	primaryColor: Y.primaryDefault,
	primaryColorHover: Y.primaryHover,
	primaryColorPressed: Y.primaryActive,
	primaryColorSuppl: Y.primarySuppl,
	infoColor: Y.infoDefault,
	infoColorHover: Y.infoHover,
	infoColorPressed: Y.infoActive,
	infoColorSuppl: Y.infoSuppl,
	successColor: Y.successDefault,
	successColorHover: Y.successHover,
	successColorPressed: Y.successActive,
	successColorSuppl: Y.successSuppl,
	warningColor: Y.warningDefault,
	warningColorHover: Y.warningHover,
	warningColorPressed: Y.warningActive,
	warningColorSuppl: Y.warningSuppl,
	errorColor: Y.errorDefault,
	errorColorHover: Y.errorHover,
	errorColorPressed: Y.errorActive,
	errorColorSuppl: Y.errorSuppl,
	textColorBase: Y.neutralTextBase,
	textColor1: X(Y.alpha1),
	textColor2: X(Y.alpha2),
	textColor3: X(Y.alpha3),
	textColorDisabled: X(Y.alpha4),
	placeholderColor: X(Y.alpha4),
	placeholderColorDisabled: X(Y.alpha5),
	iconColor: X(Y.alpha4),
	iconColorDisabled: X(Y.alpha5),
	iconColorHover: X(Number(Y.alpha4) * 1.25),
	iconColorPressed: X(Number(Y.alpha4) * .8),
	opacity1: Y.alpha1,
	opacity2: Y.alpha2,
	opacity3: Y.alpha3,
	opacity4: Y.alpha4,
	opacity5: Y.alpha5,
	dividerColor: X(Y.alphaDivider),
	borderColor: X(Y.alphaBorder),
	closeIconColorHover: X(Number(Y.alphaClose)),
	closeIconColor: X(Number(Y.alphaClose)),
	closeIconColorPressed: X(Number(Y.alphaClose)),
	closeColorHover: "rgba(255, 255, 255, .12)",
	closeColorPressed: "rgba(255, 255, 255, .08)",
	clearColor: X(Y.alpha4),
	clearColorHover: vh(X(Y.alpha4), { alpha: 1.25 }),
	clearColorPressed: vh(X(Y.alpha4), { alpha: .8 }),
	scrollbarColor: X(Y.alphaScrollbar),
	scrollbarColorHover: X(Y.alphaScrollbarHover),
	scrollbarWidth: "5px",
	scrollbarHeight: "5px",
	scrollbarBorderRadius: "5px",
	progressRailColor: X(Y.alphaProgressRail),
	railColor: X(Y.alphaRail),
	popoverColor: Y.neutralPopover,
	tableColor: Y.neutralCard,
	cardColor: Y.neutralCard,
	modalColor: Y.neutralModal,
	bodyColor: Y.neutralBody,
	tagColor: Oh(Y.alphaTag),
	avatarColor: X(Y.alphaAvatar),
	invertedColor: Y.neutralBase,
	inputColor: X(Y.alphaInput),
	codeColor: X(Y.alphaCode),
	tabColor: X(Y.alphaTab),
	actionColor: X(Y.alphaAction),
	tableHeaderColor: X(Y.alphaAction),
	hoverColor: X(Y.alphaPending),
	tableColorHover: X(Y.alphaTablePending),
	tableColorStriped: X(Y.alphaTableStriped),
	pressedColor: X(Y.alphaPressed),
	opacityDisabled: Y.alphaDisabled,
	inputColorDisabled: X(Y.alphaDisabledInput),
	buttonColor2: "rgba(255, 255, 255, .08)",
	buttonColor2Hover: "rgba(255, 255, 255, .12)",
	buttonColor2Pressed: "rgba(255, 255, 255, .08)",
	boxShadow1: "0 1px 2px -2px rgba(0, 0, 0, .24), 0 3px 6px 0 rgba(0, 0, 0, .18), 0 5px 12px 4px rgba(0, 0, 0, .12)",
	boxShadow2: "0 3px 6px -4px rgba(0, 0, 0, .24), 0 6px 12px 0 rgba(0, 0, 0, .16), 0 9px 18px 8px rgba(0, 0, 0, .10)",
	boxShadow3: "0 6px 16px -9px rgba(0, 0, 0, .08), 0 9px 28px 0 rgba(0, 0, 0, .05), 0 12px 48px 16px rgba(0, 0, 0, .03)"
}, Q = {
	neutralBase: "#FFF",
	neutralInvertBase: "#000",
	neutralTextBase: "#000",
	neutralPopover: "#fff",
	neutralCard: "#fff",
	neutralModal: "#fff",
	neutralBody: "#fff",
	alpha1: "0.82",
	alpha2: "0.72",
	alpha3: "0.38",
	alpha4: "0.24",
	alpha5: "0.18",
	alphaClose: "0.6",
	alphaDisabled: "0.5",
	alphaDisabledInput: "0.02",
	alphaPending: "0.05",
	alphaTablePending: "0.02",
	alphaPressed: "0.07",
	alphaAvatar: "0.2",
	alphaRail: "0.14",
	alphaProgressRail: ".08",
	alphaBorder: "0.12",
	alphaDivider: "0.06",
	alphaInput: "0",
	alphaAction: "0.02",
	alphaTab: "0.04",
	alphaScrollbar: "0.25",
	alphaScrollbarHover: "0.4",
	alphaCode: "0.05",
	alphaTag: "0.02",
	primaryHover: "#36ad6a",
	primaryDefault: "#18a058",
	primaryActive: "#0c7a43",
	primarySuppl: "#36ad6a",
	infoHover: "#4098fc",
	infoDefault: "#2080f0",
	infoActive: "#1060c9",
	infoSuppl: "#4098fc",
	errorHover: "#de576d",
	errorDefault: "#d03050",
	errorActive: "#ab1f3f",
	errorSuppl: "#de576d",
	warningHover: "#fcb040",
	warningDefault: "#f0a020",
	warningActive: "#c97c10",
	warningSuppl: "#fcb040",
	successHover: "#36ad6a",
	successDefault: "#18a058",
	successActive: "#0c7a43",
	successSuppl: "#36ad6a"
}, kh = mh(Q.neutralBase), Ah = mh(Q.neutralInvertBase), jh = `rgba(${Ah.slice(0, 3).join(", ")}, `;
function Mh(e) {
	return `${jh + String(e)})`;
}
function Nh(e) {
	let t = Array.from(Ah);
	return t[3] = Number(e), q(kh, t);
}
var Ph = {
	name: "common",
	...ym,
	baseColor: Q.neutralBase,
	primaryColor: Q.primaryDefault,
	primaryColorHover: Q.primaryHover,
	primaryColorPressed: Q.primaryActive,
	primaryColorSuppl: Q.primarySuppl,
	infoColor: Q.infoDefault,
	infoColorHover: Q.infoHover,
	infoColorPressed: Q.infoActive,
	infoColorSuppl: Q.infoSuppl,
	successColor: Q.successDefault,
	successColorHover: Q.successHover,
	successColorPressed: Q.successActive,
	successColorSuppl: Q.successSuppl,
	warningColor: Q.warningDefault,
	warningColorHover: Q.warningHover,
	warningColorPressed: Q.warningActive,
	warningColorSuppl: Q.warningSuppl,
	errorColor: Q.errorDefault,
	errorColorHover: Q.errorHover,
	errorColorPressed: Q.errorActive,
	errorColorSuppl: Q.errorSuppl,
	textColorBase: Q.neutralTextBase,
	textColor1: "rgb(31, 34, 37)",
	textColor2: "rgb(51, 54, 57)",
	textColor3: "rgb(118, 124, 130)",
	textColorDisabled: Nh(Q.alpha4),
	placeholderColor: Nh(Q.alpha4),
	placeholderColorDisabled: Nh(Q.alpha5),
	iconColor: Nh(Q.alpha4),
	iconColorHover: vh(Nh(Q.alpha4), { lightness: .75 }),
	iconColorPressed: vh(Nh(Q.alpha4), { lightness: .9 }),
	iconColorDisabled: Nh(Q.alpha5),
	opacity1: Q.alpha1,
	opacity2: Q.alpha2,
	opacity3: Q.alpha3,
	opacity4: Q.alpha4,
	opacity5: Q.alpha5,
	dividerColor: "rgb(239, 239, 245)",
	borderColor: "rgb(224, 224, 230)",
	closeIconColor: Nh(Number(Q.alphaClose)),
	closeIconColorHover: Nh(Number(Q.alphaClose)),
	closeIconColorPressed: Nh(Number(Q.alphaClose)),
	closeColorHover: "rgba(0, 0, 0, .09)",
	closeColorPressed: "rgba(0, 0, 0, .13)",
	clearColor: Nh(Q.alpha4),
	clearColorHover: vh(Nh(Q.alpha4), { lightness: .75 }),
	clearColorPressed: vh(Nh(Q.alpha4), { lightness: .9 }),
	scrollbarColor: Mh(Q.alphaScrollbar),
	scrollbarColorHover: Mh(Q.alphaScrollbarHover),
	scrollbarWidth: "5px",
	scrollbarHeight: "5px",
	scrollbarBorderRadius: "5px",
	progressRailColor: Nh(Q.alphaProgressRail),
	railColor: "rgb(219, 219, 223)",
	popoverColor: Q.neutralPopover,
	tableColor: Q.neutralCard,
	cardColor: Q.neutralCard,
	modalColor: Q.neutralModal,
	bodyColor: Q.neutralBody,
	tagColor: "#eee",
	avatarColor: Nh(Q.alphaAvatar),
	invertedColor: "rgb(0, 20, 40)",
	inputColor: Nh(Q.alphaInput),
	codeColor: "rgb(244, 244, 248)",
	tabColor: "rgb(247, 247, 250)",
	actionColor: "rgb(250, 250, 252)",
	tableHeaderColor: "rgb(250, 250, 252)",
	hoverColor: "rgb(243, 243, 245)",
	tableColorHover: "rgba(0, 0, 100, 0.03)",
	tableColorStriped: "rgba(0, 0, 100, 0.02)",
	pressedColor: "rgb(237, 237, 239)",
	opacityDisabled: Q.alphaDisabled,
	inputColorDisabled: "rgb(250, 250, 252)",
	buttonColor2: "rgba(46, 51, 56, .05)",
	buttonColor2Hover: "rgba(46, 51, 56, .09)",
	buttonColor2Pressed: "rgba(46, 51, 56, .13)",
	boxShadow1: "0 1px 2px -2px rgba(0, 0, 0, .08), 0 3px 6px 0 rgba(0, 0, 0, .06), 0 5px 12px 4px rgba(0, 0, 0, .04)",
	boxShadow2: "0 3px 6px -4px rgba(0, 0, 0, .12), 0 6px 16px 0 rgba(0, 0, 0, .08), 0 9px 28px 8px rgba(0, 0, 0, .05)",
	boxShadow3: "0 6px 16px -9px rgba(0, 0, 0, .08), 0 9px 28px 0 rgba(0, 0, 0, .05), 0 12px 48px 16px rgba(0, 0, 0, .03)"
}, Fh = {
	railInsetHorizontalBottom: "auto 2px 4px 2px",
	railInsetHorizontalTop: "4px 2px auto 2px",
	railInsetVerticalRight: "2px 4px 2px auto",
	railInsetVerticalLeft: "2px auto 2px 4px",
	railColor: "transparent"
};
//#endregion
//#region node_modules/naive-ui/es/_internal/scrollbar/styles/light.mjs
function Ih(e) {
	let { scrollbarColor: t, scrollbarColorHover: n, scrollbarHeight: r, scrollbarWidth: i, scrollbarBorderRadius: a } = e;
	return {
		...Fh,
		height: r,
		width: i,
		borderRadius: a,
		color: t,
		colorHover: n
	};
}
var Lh = {
	name: "Scrollbar",
	common: Ph,
	self: Ih
}, Rh = {
	name: "Scrollbar",
	common: Z,
	self: Ih
}, zh = {
	iconSizeTiny: "28px",
	iconSizeSmall: "34px",
	iconSizeMedium: "40px",
	iconSizeLarge: "46px",
	iconSizeHuge: "52px"
};
//#endregion
//#region node_modules/naive-ui/es/empty/styles/light.mjs
function Bh(e) {
	let { textColorDisabled: t, iconColor: n, textColor2: r, fontSizeTiny: i, fontSizeSmall: a, fontSizeMedium: o, fontSizeLarge: s, fontSizeHuge: c } = e;
	return {
		...zh,
		fontSizeTiny: i,
		fontSizeSmall: a,
		fontSizeMedium: o,
		fontSizeLarge: s,
		fontSizeHuge: c,
		textColor: t,
		iconColor: n,
		extraTextColor: r
	};
}
var Vh = {
	name: "Empty",
	common: Ph,
	self: Bh
}, Hh = {
	name: "Empty",
	common: Z,
	self: Bh
};
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-css-vars-class.mjs
function Uh(e, t, n, r) {
	n || pm("useThemeClass", "cssVarsRef is not passed");
	let i = Bn(gm, null), a = i?.mergedThemeHashRef, o = i?.styleMountTarget, s = /* @__PURE__ */ j(""), c = Om(), l, u = `__${e}`, d = () => {
		let e = u, i = t ? t.value : void 0, d = a?.value;
		d && (e += `-${d}`), i && (e += `-${i}`);
		let { themeOverrides: f, builtinThemeOverrides: p } = r;
		f && (e += `-${gs(JSON.stringify(f))}`), p && (e += `-${gs(JSON.stringify(p))}`), s.value = e, l = () => {
			let t = n.value, r = "";
			for (let e in t) r += `${e}: ${t[e]};`;
			B(`.${e}`, r).mount({
				id: e,
				ssr: c,
				parent: o
			}), l = void 0;
		};
	};
	return Un(() => {
		d();
	}), {
		themeClass: s,
		onRender: () => {
			l?.();
		}
	};
}
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-locale.mjs
function Wh(e) {
	let { mergedLocaleRef: t, mergedDateLocaleRef: n } = Bn(gm, null) || {}, r = z(() => t?.value?.[e] ?? Rs[e]);
	return {
		dateLocaleRef: z(() => n?.value ?? hc),
		localeRef: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-theme.mjs
function Gh(e) {
	return e;
}
function Kh(e, t, n, r, i, a) {
	let o = Om(), s = Bn(gm, null);
	if (n) {
		let e = () => {
			let e = a?.value;
			n.mount({
				id: e === void 0 ? t : e + t,
				head: !0,
				props: { bPrefix: e ? `.${e}-` : void 0 },
				anchorMetaName: vm,
				ssr: o,
				parent: s?.styleMountTarget
			}), s?.preflightStyleDisabled || Cm.mount({
				id: "n-global",
				head: !0,
				anchorMetaName: vm,
				ssr: o,
				parent: s?.styleMountTarget
			});
		};
		o ? e() : Pr(e);
	}
	return z(() => {
		let { theme: { common: t, self: n, peers: a = {} } = {}, themeOverrides: o = {}, builtinThemeOverrides: c = {} } = i, { common: l, peers: u } = o, { common: d = void 0, [e]: { common: f = void 0, self: p = void 0, peers: m = {} } = {} } = s?.mergedThemeRef.value || {}, { common: h = void 0, [e]: g = {} } = s?.mergedThemeOverridesRef.value || {}, { common: _, peers: v = {} } = g, y = dm({}, t || f || d || r.common, h, _, l);
		return {
			common: y,
			self: dm((n || p || r.self)?.(y), c, g, o),
			peers: dm({}, r.peers, m, a),
			peerOverrides: dm({}, c.peers, v, u)
		};
	});
}
Kh.props = {
	theme: Object,
	themeOverrides: Object,
	builtinThemeOverrides: Object
};
//#endregion
//#region node_modules/naive-ui/es/_internal/icon/src/styles/index.cssr.mjs
var qh = V("base-icon", "\n height: 1em;\n width: 1em;\n line-height: 1em;\n text-align: center;\n display: inline-block;\n position: relative;\n fill: currentColor;\n", [B("svg", "\n height: 1em;\n width: 1em;\n ")]), Jh = [
	"onClick",
	"onMousedown",
	"onMouseup",
	"role",
	"aria-label",
	"aria-hidden",
	"aria-disabled"
], Yh = /* @__PURE__ */ N({
	name: "BaseIcon",
	props: {
		role: String,
		ariaLabel: String,
		ariaDisabled: {
			type: Boolean,
			default: void 0
		},
		ariaHidden: {
			type: Boolean,
			default: void 0
		},
		clsPrefix: {
			type: String,
			required: !0
		},
		onClick: Function,
		onMousedown: Function,
		onMouseup: Function
	},
	setup(e) {
		km("-base-icon", qh, /* @__PURE__ */ M(e, "clsPrefix"));
	},
	render() {
		return F(), I("i", {
			class: K(`${this.clsPrefix}-base-icon`),
			onClick: this.onClick,
			onMousedown: this.onMousedown,
			onMouseup: this.onMouseup,
			role: this.role,
			"aria-label": this.ariaLabel,
			"aria-hidden": this.ariaHidden,
			"aria-disabled": this.ariaDisabled
		}, [G(() => this.$slots.default?.())], 42, Jh);
	}
}), Xh = /* @__PURE__ */ N({
	name: "Empty",
	render() {
		return (() => {
			let e = jm("15c1a247ae156450");
			return e[0] ||= R("svg", {
				viewBox: "0 0 28 28",
				fill: "none",
				xmlns: "http://www.w3.org/2000/svg"
			}, [R("path", {
				d: "M26 7.5C26 11.0899 23.0899 14 19.5 14C15.9101 14 13 11.0899 13 7.5C13 3.91015 15.9101 1 19.5 1C23.0899 1 26 3.91015 26 7.5ZM16.8536 4.14645C16.6583 3.95118 16.3417 3.95118 16.1464 4.14645C15.9512 4.34171 15.9512 4.65829 16.1464 4.85355L18.7929 7.5L16.1464 10.1464C15.9512 10.3417 15.9512 10.6583 16.1464 10.8536C16.3417 11.0488 16.6583 11.0488 16.8536 10.8536L19.5 8.20711L22.1464 10.8536C22.3417 11.0488 22.6583 11.0488 22.8536 10.8536C23.0488 10.6583 23.0488 10.3417 22.8536 10.1464L20.2071 7.5L22.8536 4.85355C23.0488 4.65829 23.0488 4.34171 22.8536 4.14645C22.6583 3.95118 22.3417 3.95118 22.1464 4.14645L19.5 6.79289L16.8536 4.14645Z",
				fill: "currentColor"
			}), R("path", {
				d: "M25 22.75V12.5991C24.5572 13.0765 24.053 13.4961 23.5 13.8454V16H17.5L17.3982 16.0068C17.0322 16.0565 16.75 16.3703 16.75 16.75C16.75 18.2688 15.5188 19.5 14 19.5C12.4812 19.5 11.25 18.2688 11.25 16.75L11.2432 16.6482C11.1935 16.2822 10.8797 16 10.5 16H4.5V7.25C4.5 6.2835 5.2835 5.5 6.25 5.5H12.2696C12.4146 4.97463 12.6153 4.47237 12.865 4H6.25C4.45507 4 3 5.45507 3 7.25V22.75C3 24.5449 4.45507 26 6.25 26H21.75C23.5449 26 25 24.5449 25 22.75ZM4.5 22.75V17.5H9.81597L9.85751 17.7041C10.2905 19.5919 11.9808 21 14 21L14.215 20.9947C16.2095 20.8953 17.842 19.4209 18.184 17.5H23.5V22.75C23.5 23.7165 22.7165 24.5 21.75 24.5H6.25C5.2835 24.5 4.5 23.7165 4.5 22.75Z",
				fill: "currentColor"
			})], -1);
		})();
	}
}), Zh = V("empty", "\n display: flex;\n flex-direction: column;\n align-items: center;\n font-size: var(--n-font-size);\n", [
	H("icon", "\n width: var(--n-icon-size);\n height: var(--n-icon-size);\n font-size: var(--n-icon-size);\n line-height: var(--n-icon-size);\n color: var(--n-icon-color);\n transition:\n color .3s var(--n-bezier);\n ", [B("+", [H("description", "\n margin-top: 8px;\n ")])]),
	H("description", "\n transition: color .3s var(--n-bezier);\n color: var(--n-text-color);\n "),
	H("extra", "\n text-align: center;\n transition: color .3s var(--n-bezier);\n margin-top: 12px;\n color: var(--n-extra-text-color);\n ")
]), Qh = /* @__PURE__ */ N({
	name: "Empty",
	props: {
		...Kh.props,
		description: String,
		showDescription: {
			type: Boolean,
			default: !0
		},
		showIcon: {
			type: Boolean,
			default: !0
		},
		size: {
			type: String,
			default: "medium"
		},
		renderIcon: Function
	},
	slots: Object,
	setup(e) {
		let { mergedClsPrefixRef: t, inlineThemeDisabled: n, mergedComponentPropsRef: r } = _m(e), i = Kh("Empty", "-empty", Zh, Vh, e, t), { localeRef: a } = Wh("Empty"), o = z(() => e.description ?? r?.value?.Empty?.description), s = z(() => r?.value?.Empty?.renderIcon || (() => (F(), L(Xh)))), c = z(() => {
			let { size: t } = e, { common: { cubicBezierEaseInOut: n }, self: { [W("iconSize", t)]: r, [W("fontSize", t)]: a, textColor: o, iconColor: s, extraTextColor: c } } = i.value;
			return {
				"--n-icon-size": r,
				"--n-font-size": a,
				"--n-bezier": n,
				"--n-text-color": o,
				"--n-icon-color": s,
				"--n-extra-text-color": c
			};
		}), l = n ? Uh("empty", z(() => {
			let t = "", { size: n } = e;
			return t += n[0], t;
		}), c, e) : void 0;
		return {
			mergedClsPrefix: t,
			mergedRenderIcon: s,
			localizedDescription: z(() => o.value || a.value.description),
			cssVars: n ? void 0 : c,
			themeClass: l?.themeClass,
			onRender: l?.onRender
		};
	},
	render() {
		let { $slots: e, mergedClsPrefix: t, onRender: n } = this;
		return n?.(), F(), I("div", {
			class: K([`${t}-empty`, this.themeClass]),
			style: k(this.cssVars)
		}, [
			this.showIcon ? (F(), I("div", {
				key: 0,
				class: K(`${t}-empty__icon`)
			}, [e.icon ? (F(), I(P, { key: 0 }, [G(() => e.icon())], 64)) : (F(), L(Yh, {
				key: 1,
				clsPrefix: t
			}, { default: this.mergedRenderIcon }, 1032, ["clsPrefix"]))], 2)) : G(() => null),
			this.showDescription ? (F(), I("div", {
				key: 2,
				class: K(`${t}-empty__description`)
			}, [e.default ? (F(), I(P, { key: 0 }, [G(() => e.default())], 64)) : (F(), I(P, { key: 1 }, [G(() => this.localizedDescription)], 64))], 2)) : G(() => null),
			e.extra ? (F(), I("div", {
				key: 4,
				class: K(`${t}-empty__extra`)
			}, [G(() => e.extra())], 2)) : G(() => null)
		], 6);
	}
}), $h = {
	height: "calc(var(--n-option-height) * 7.6)",
	paddingTiny: "4px 0",
	paddingSmall: "4px 0",
	paddingMedium: "4px 0",
	paddingLarge: "4px 0",
	paddingHuge: "4px 0",
	optionPaddingTiny: "0 12px",
	optionPaddingSmall: "0 12px",
	optionPaddingMedium: "0 12px",
	optionPaddingLarge: "0 12px",
	optionPaddingHuge: "0 12px",
	loadingSize: "18px"
};
//#endregion
//#region node_modules/naive-ui/es/_internal/select-menu/styles/light.mjs
function eg(e) {
	let { borderRadius: t, popoverColor: n, textColor3: r, dividerColor: i, textColor2: a, primaryColorPressed: o, textColorDisabled: s, primaryColor: c, opacityDisabled: l, hoverColor: u, fontSizeTiny: d, fontSizeSmall: f, fontSizeMedium: p, fontSizeLarge: m, fontSizeHuge: h, heightTiny: g, heightSmall: _, heightMedium: v, heightLarge: y, heightHuge: b } = e;
	return {
		...$h,
		optionFontSizeTiny: d,
		optionFontSizeSmall: f,
		optionFontSizeMedium: p,
		optionFontSizeLarge: m,
		optionFontSizeHuge: h,
		optionHeightTiny: g,
		optionHeightSmall: _,
		optionHeightMedium: v,
		optionHeightLarge: y,
		optionHeightHuge: b,
		borderRadius: t,
		color: n,
		groupHeaderTextColor: r,
		actionDividerColor: i,
		optionTextColor: a,
		optionTextColorPressed: o,
		optionTextColorDisabled: s,
		optionTextColorActive: c,
		optionOpacityDisabled: l,
		optionCheckColor: c,
		optionColorPending: u,
		optionColorActive: "rgba(0, 0, 0, 0)",
		optionColorActivePending: u,
		actionTextColor: a,
		loadingColor: c
	};
}
var tg = Gh({
	name: "InternalSelectMenu",
	common: Ph,
	peers: {
		Scrollbar: Lh,
		Empty: Vh
	},
	self: eg
}), ng = {
	name: "InternalSelectMenu",
	common: Z,
	peers: {
		Scrollbar: Rh,
		Empty: Hh
	},
	self: eg
}, rg = {
	space: "6px",
	spaceArrow: "10px",
	arrowOffset: "10px",
	arrowOffsetVertical: "10px",
	arrowHeight: "6px",
	padding: "8px 14px"
};
//#endregion
//#region node_modules/naive-ui/es/popover/styles/light.mjs
function ig(e) {
	let { boxShadow2: t, popoverColor: n, textColor2: r, borderRadius: i, fontSize: a, dividerColor: o } = e;
	return {
		...rg,
		fontSize: a,
		borderRadius: i,
		color: n,
		dividerColor: o,
		textColor: r,
		boxShadow: t
	};
}
var ag = Gh({
	name: "Popover",
	common: Ph,
	peers: { Scrollbar: Lh },
	self: ig
}), og = {
	name: "Popover",
	common: Z,
	peers: { Scrollbar: Rh },
	self: ig
}, sg = hm("n-internal-select-menu"), cg = hm("n-internal-select-menu-body"), lg = hm("n-drawer-body"), ug = hm("n-modal-body"), dg = hm("n-popover-body");
//#endregion
//#region node_modules/evtd/es/utils.js
function fg(e) {
	return e.composedPath()[0];
}
//#endregion
//#region node_modules/evtd/es/traps.js
var pg = {
	mousemoveoutside: /* @__PURE__ */ new WeakMap(),
	clickoutside: /* @__PURE__ */ new WeakMap()
};
function mg(e, t, n) {
	if (e === "mousemoveoutside") {
		let e = (e) => {
			t.contains(fg(e)) || n(e);
		};
		return {
			mousemove: e,
			touchstart: e
		};
	}
	if (e === "clickoutside") {
		let e = !1, r = (n) => {
			e = !t.contains(fg(n));
		}, i = (r) => {
			e && (t.contains(fg(r)) || n(r));
		};
		return {
			mousedown: r,
			mouseup: i,
			touchstart: r,
			touchend: i
		};
	}
	return console.error(`[evtd/create-trap-handler]: name \`${e}\` is invalid. This could be a bug of evtd.`), {};
}
function hg(e, t, n) {
	let r = pg[e], i = r.get(t);
	i === void 0 && r.set(t, i = /* @__PURE__ */ new WeakMap());
	let a = i.get(n);
	return a === void 0 && i.set(n, a = mg(e, t, n)), a;
}
function gg(e, t, n, r) {
	if (e === "mousemoveoutside" || e === "clickoutside") {
		let i = hg(e, t, n);
		return Object.keys(i).forEach((e) => {
			yg(e, document, i[e], r);
		}), !0;
	}
	return !1;
}
function _g(e, t, n, r) {
	if (e === "mousemoveoutside" || e === "clickoutside") {
		let i = hg(e, t, n);
		return Object.keys(i).forEach((e) => {
			bg(e, document, i[e], r);
		}), !0;
	}
	return !1;
}
//#endregion
//#region node_modules/evtd/es/delegate.js
function vg() {
	if (typeof window > "u") return {
		on: () => {},
		off: () => {}
	};
	let e = /* @__PURE__ */ new WeakMap(), t = /* @__PURE__ */ new WeakMap();
	function n() {
		e.set(this, !0);
	}
	function r() {
		e.set(this, !0), t.set(this, !0);
	}
	function i(e, t, n) {
		let r = e[t];
		return e[t] = function() {
			return n.apply(e, arguments), r.apply(e, arguments);
		}, e;
	}
	function a(e, t) {
		e[t] = Event.prototype[t];
	}
	let o = /* @__PURE__ */ new WeakMap(), s = Object.getOwnPropertyDescriptor(Event.prototype, "currentTarget");
	function c() {
		return o.get(this) ?? null;
	}
	function l(e, t) {
		s !== void 0 && Object.defineProperty(e, "currentTarget", {
			configurable: !0,
			enumerable: !0,
			get: t ?? s.get
		});
	}
	let u = {
		bubble: {},
		capture: {}
	}, d = {};
	function f() {
		let s = function(s) {
			let { type: d, eventPhase: f, bubbles: p } = s, m = fg(s);
			if (f === 2) return;
			let h = f === 1 ? "capture" : "bubble", g = m, _ = [];
			for (; g === null && (g = window), _.push(g), g !== window;) g = g.parentNode || null;
			let v = u.capture[d], y = u.bubble[d];
			if (i(s, "stopPropagation", n), i(s, "stopImmediatePropagation", r), l(s, c), h === "capture") {
				if (v === void 0) return;
				for (let n = _.length - 1; n >= 0 && !e.has(s); --n) {
					let e = _[n], r = v.get(e);
					if (r !== void 0) {
						o.set(s, e);
						for (let e of r) {
							if (t.has(s)) break;
							e(s);
						}
					}
					if (n === 0 && !p && y !== void 0) {
						let n = y.get(e);
						if (n !== void 0) for (let e of n) {
							if (t.has(s)) break;
							e(s);
						}
					}
				}
			} else if (h === "bubble") {
				if (y === void 0) return;
				for (let n = 0; n < _.length && !e.has(s); ++n) {
					let e = _[n], r = y.get(e);
					if (r !== void 0) {
						o.set(s, e);
						for (let e of r) {
							if (t.has(s)) break;
							e(s);
						}
					}
				}
			}
			a(s, "stopPropagation"), a(s, "stopImmediatePropagation"), l(s);
		};
		return s.displayName = "evtdUnifiedHandler", s;
	}
	function p() {
		let e = function(e) {
			let { type: t, eventPhase: n } = e;
			if (n !== 2) return;
			let r = d[t];
			r !== void 0 && r.forEach((t) => t(e));
		};
		return e.displayName = "evtdUnifiedWindowEventHandler", e;
	}
	let m = f(), h = p();
	function g(e, t) {
		let n = u[e];
		return n[t] === void 0 && (n[t] = /* @__PURE__ */ new Map(), window.addEventListener(t, m, e === "capture")), n[t];
	}
	function _(e) {
		return d[e] === void 0 && (d[e] = /* @__PURE__ */ new Set(), window.addEventListener(e, h)), d[e];
	}
	function v(e, t) {
		let n = e.get(t);
		return n === void 0 && e.set(t, n = /* @__PURE__ */ new Set()), n;
	}
	function y(e, t, n, r) {
		let i = u[t][n];
		if (i !== void 0) {
			let t = i.get(e);
			if (t !== void 0 && t.has(r)) return !0;
		}
		return !1;
	}
	function b(e, t) {
		let n = d[e];
		return !!(n !== void 0 && n.has(t));
	}
	function x(e, t, n, r) {
		let i;
		if (i = typeof r == "object" && r.once === !0 ? (a) => {
			S(e, t, i, r), n(a);
		} : n, gg(e, t, i, r)) return;
		let a = v(g(r === !0 || typeof r == "object" && r.capture === !0 ? "capture" : "bubble", e), t);
		if (a.has(i) || a.add(i), t === window) {
			let t = _(e);
			t.has(i) || t.add(i);
		}
	}
	function S(e, t, n, r) {
		if (_g(e, t, n, r)) return;
		let i = r === !0 || typeof r == "object" && r.capture === !0, a = i ? "capture" : "bubble", o = g(a, e), s = v(o, t);
		if (t === window && !y(t, i ? "bubble" : "capture", e, n) && b(e, n)) {
			let t = d[e];
			t.delete(n), t.size === 0 && (window.removeEventListener(e, h), d[e] = void 0);
		}
		s.has(n) && s.delete(n), s.size === 0 && o.delete(t), o.size === 0 && (window.removeEventListener(e, m, a === "capture"), u[a][e] = void 0);
	}
	return {
		on: x,
		off: S
	};
}
var { on: yg, off: bg } = vg();
//#endregion
//#region node_modules/vooks/es/use-false-until-truthy.js
function xg(e) {
	let t = /* @__PURE__ */ j(!!e.value);
	if (t.value) return /* @__PURE__ */ zt(t);
	let n = Wn(e, (e) => {
		e && (t.value = !0, n());
	});
	return /* @__PURE__ */ zt(t);
}
//#endregion
//#region node_modules/vooks/es/use-memo.js
function Sg(e) {
	let t = z(e), n = /* @__PURE__ */ j(t.value);
	return Wn(t, (e) => {
		n.value = e;
	}), typeof e == "function" ? n : {
		__v_isRef: !0,
		get value() {
			return n.value;
		},
		set value(t) {
			e.set(t);
		}
	};
}
//#endregion
//#region node_modules/vooks/es/utils.js
var Cg = typeof window < "u" ? document?.fonts?.ready : void 0, wg = !1;
Cg === void 0 ? wg = !0 : Cg.then(() => {
	wg = !0;
});
function Tg(e) {
	/* istanbul ignore next */
	if (wg) return;
	let t = !1;
	Fr(() => {
		/* istanbul ignore next */
		wg || Cg?.then(() => {
			t || e();
		});
	}), Lr(() => {
		t = !0;
	});
}
//#endregion
//#region node_modules/vooks/es/use-merged-state.js
function Eg(e, t) {
	return Wn(e, (e) => {
		e !== void 0 && (t.value = e);
	}), z(() => e.value === void 0 ? t.value : e.value);
}
//#endregion
//#region node_modules/vooks/es/life-cycle/use-is-mounted.js
function Dg() {
	let e = /* @__PURE__ */ j(!1);
	return Fr(() => {
		e.value = !0;
	}), /* @__PURE__ */ zt(e);
}
//#endregion
//#region node_modules/vooks/es/use-compitable.js
function Og(e, t) {
	return z(() => {
		for (let n of t) if (e[n] !== void 0) return e[n];
		return e[t[t.length - 1]];
	});
}
//#endregion
//#region node_modules/vooks/es/use-is-ios.js
var kg = (typeof window > "u" ? !1 : /iPad|iPhone|iPod/.test(navigator.platform) || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) && !window.MSStream;
function Ag() {
	return kg;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/composable/use-adjusted-to.mjs
var jg = "__disabled__";
function Mg(e) {
	let t = Bn(ug, null), n = Bn(lg, null), r = Bn(dg, null), i = Bn(cg, null), a = /* @__PURE__ */ j();
	if (typeof document < "u") {
		a.value = document.fullscreenElement;
		let e = () => {
			a.value = document.fullscreenElement;
		};
		Fr(() => {
			yg("fullscreenchange", document, e);
		}), Lr(() => {
			bg("fullscreenchange", document, e);
		});
	}
	return Sg(() => {
		let { to: o } = e;
		return o === void 0 ? t?.value ? t.value.$el ?? t.value : n?.value ? n.value : r?.value ? r.value : i?.value ? i.value : o ?? (a.value || "body") : o === !1 ? jg : o === !0 ? a.value || "body" : o;
	});
}
Mg.tdkey = jg, Mg.propTo = {
	type: [
		String,
		Object,
		Boolean
	],
	default: void 0
};
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/call.mjs
function $(e, ...t) {
	if (Array.isArray(e)) e.forEach((e) => $(e, ...t));
	else return e(...t);
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/flatten.mjs
function Ng(e, t = !0, n = []) {
	return e.forEach((e) => {
		if (e !== null) {
			if (typeof e != "object") {
				(typeof e == "string" || typeof e == "number") && n.push(ea(String(e)));
				return;
			}
			if (Array.isArray(e)) {
				Ng(e, t, n);
				return;
			}
			if (e.type === P) {
				if (e.children === null) return;
				Array.isArray(e.children) && Ng(e.children, t, n);
			} else {
				if (e.type === Ri && t) return;
				n.push(e);
			}
		}
	}), n;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/get-first-slot-vnode.mjs
function Pg(e, t = "default", n = void 0) {
	let r = e[t];
	if (!r) return fm("getFirstSlotVNode", `slot[${t}] is empty`), null;
	let i = Ng(r(n));
	return i.length === 1 ? i[0] : (fm("getFirstSlotVNode", `slot[${t}] should have exactly one child`), null);
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/keep.mjs
function Fg(e, t = [], n) {
	let r = {};
	return t.forEach((t) => {
		r[t] = e[t];
	}), Object.assign(r, n);
}
//#endregion
//#region node_modules/naive-ui/es/_utils/css/format-length.mjs
var Ig = /^(\d|\.)+$/, Lg = /(\d|\.)+/;
function Rg(e, { c: t = 1, offset: n = 0, attachPx: r = !0 } = {}) {
	if (typeof e == "number") {
		let r = (e + n) * t;
		return r === 0 ? "0" : `${r}px`;
	}
	if (typeof e == "string") {
		if (Ig.test(e)) {
			let i = (Number(e) + n) * t;
			return r ? i === 0 ? "0" : `${i}px` : `${i}`;
		}
		{
			let r = Lg.exec(e);
			return r ? e.replace(Lg, String((Number(r[0]) + n) * t)) : e;
		}
	}
	return e;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/env/is-jsdom.mjs
var zg;
function Bg() {
	return zg === void 0 && (zg = navigator.userAgent.includes("Node.js") || navigator.userAgent.includes("jsdom")), zg;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/resolve-slot.mjs
function Vg(e) {
	return e.some((e) => !Ki(e) || !(e.type === Ri || e.type === P && !Vg(e.children))) ? e : null;
}
function Hg(e, t) {
	return e && Vg(e()) || t();
}
function Ug(e, t, n) {
	return e && Vg(e(t)) || n(t);
}
function Wg(e, t) {
	return t(e && Vg(e()) || null);
}
function Gg(e) {
	return !(e && Vg(e()));
}
//#endregion
//#region node_modules/naive-ui/es/_mixins/use-rtl.mjs
function Kg(e, t, n) {
	if (!t) return;
	let r = Om(), i = z(() => {
		let { value: n } = t;
		if (!n) return;
		let r = n[e];
		if (r) return r;
	}), a = Bn(gm, null), o = () => {
		Un(() => {
			let { value: t } = n, o = `${t}${e}Rtl`;
			if (Es(o, r)) return;
			let { value: s } = i;
			s && s.style.mount({
				id: o,
				head: !0,
				anchorMetaName: vm,
				props: { bPrefix: t ? `.${t}-` : void 0 },
				ssr: r,
				parent: a?.styleMountTarget
			});
		});
	};
	return r ? o() : Pr(o), i;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/composable/use-reactivated.mjs
function qg(e) {
	let t = { isDeactivated: !1 }, n = !1;
	return Or(() => {
		if (t.isDeactivated = !1, !n) {
			n = !0;
			return;
		}
		e();
	}), kr(() => {
		t.isDeactivated = !0, n ||= !0;
	}), t;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/css/rtl-inset.mjs
function Jg(e) {
	let { left: t, right: n, top: r, bottom: i } = Wm(e);
	return `${r} ${t} ${i} ${n}`;
}
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/wrapper.mjs
var Yg = /* @__PURE__ */ N({ render() {
	return this.$slots.default?.();
} }), { cubicBezierEaseInOut: Xg } = ym;
function Zg({ name: e = "fade-in", enterDuration: t = "0.2s", leaveDuration: n = "0.2s", enterCubicBezier: r = Xg, leaveCubicBezier: i = Xg } = {}) {
	return [
		B(`&.${e}-transition-enter-active`, { transition: `all ${t} ${r}!important` }),
		B(`&.${e}-transition-leave-active`, { transition: `all ${n} ${i}!important` }),
		B(`&.${e}-transition-enter-from, &.${e}-transition-leave-to`, { opacity: 0 }),
		B(`&.${e}-transition-leave-from, &.${e}-transition-enter-to`, { opacity: 1 })
	];
}
//#endregion
//#region node_modules/naive-ui/es/_internal/scrollbar/src/styles/index.cssr.mjs
var Qg = V("scrollbar", "\n overflow: hidden;\n position: relative;\n z-index: auto;\n height: 100%;\n width: 100%;\n", [B(">", [V("scrollbar-container", "\n width: 100%;\n overflow: scroll;\n height: 100%;\n min-height: inherit;\n max-height: inherit;\n scrollbar-width: none;\n ", [B("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb", "\n width: 0;\n height: 0;\n display: none;\n "), B(">", [V("scrollbar-content", "\n box-sizing: border-box;\n min-width: 100%;\n ")])])]), B(">, +", [V("scrollbar-rail", "\n position: absolute;\n pointer-events: none;\n user-select: none;\n background: var(--n-scrollbar-rail-color);\n -webkit-user-select: none;\n ", [
	U("horizontal", "\n height: var(--n-scrollbar-height);\n ", [B(">", [H("scrollbar", "\n height: var(--n-scrollbar-height);\n border-radius: var(--n-scrollbar-border-radius);\n right: 0;\n ")])]),
	U("horizontal--top", "\n top: var(--n-scrollbar-rail-top-horizontal-top); \n right: var(--n-scrollbar-rail-right-horizontal-top); \n bottom: var(--n-scrollbar-rail-bottom-horizontal-top); \n left: var(--n-scrollbar-rail-left-horizontal-top); \n "),
	U("horizontal--bottom", "\n top: var(--n-scrollbar-rail-top-horizontal-bottom); \n right: var(--n-scrollbar-rail-right-horizontal-bottom); \n bottom: var(--n-scrollbar-rail-bottom-horizontal-bottom); \n left: var(--n-scrollbar-rail-left-horizontal-bottom); \n "),
	U("vertical", "\n width: var(--n-scrollbar-width);\n ", [B(">", [H("scrollbar", "\n width: var(--n-scrollbar-width);\n border-radius: var(--n-scrollbar-border-radius);\n bottom: 0;\n ")])]),
	U("vertical--left", "\n top: var(--n-scrollbar-rail-top-vertical-left); \n right: var(--n-scrollbar-rail-right-vertical-left); \n bottom: var(--n-scrollbar-rail-bottom-vertical-left); \n left: var(--n-scrollbar-rail-left-vertical-left); \n "),
	U("vertical--right", "\n top: var(--n-scrollbar-rail-top-vertical-right); \n right: var(--n-scrollbar-rail-right-vertical-right); \n bottom: var(--n-scrollbar-rail-bottom-vertical-right); \n left: var(--n-scrollbar-rail-left-vertical-right); \n "),
	U("disabled", [B(">", [H("scrollbar", "pointer-events: none;")])]),
	B(">", [H("scrollbar", "\n z-index: 1;\n position: absolute;\n cursor: pointer;\n pointer-events: all;\n background-color: var(--n-scrollbar-color);\n transition: background-color .2s var(--n-scrollbar-bezier);\n ", [Zg(), B("&:hover", "background-color: var(--n-scrollbar-color-hover);")])])
])])]);
//#endregion
//#region node_modules/vueuc/es/shared/v-node.js
function $g(e, t, n = "default") {
	let r = t[n];
	if (r === void 0) throw Error(`[vueuc/${e}]: slot[${n}] is empty.`);
	return r();
}
function e_(e, t = !0, n = []) {
	return e.forEach((e) => {
		if (e !== null) {
			if (typeof e != "object") {
				(typeof e == "string" || typeof e == "number") && n.push(ea(String(e)));
				return;
			}
			if (Array.isArray(e)) {
				e_(e, t, n);
				return;
			}
			if (e.type === P) {
				if (e.children === null) return;
				Array.isArray(e.children) && e_(e.children, t, n);
			} else (e.type !== Ri || !t) && n.push(e);
		}
	}), n;
}
function t_(e, t, n = "default") {
	let r = t[n];
	if (r === void 0) throw Error(`[vueuc/${e}]: slot[${n}] is empty.`);
	let i = e_(r());
	if (i.length === 1) return i[0];
	throw Error(`[vueuc/${e}]: slot[${n}] should have exactly one child.`);
}
//#endregion
//#region node_modules/vueuc/es/binder/src/utils.js
var n_ = null;
function r_() {
	if (n_ === null && (n_ = document.getElementById("v-binder-view-measurer"), n_ === null)) {
		n_ = document.createElement("div"), n_.id = "v-binder-view-measurer";
		let { style: e } = n_;
		e.position = "fixed", e.left = "0", e.right = "0", e.top = "0", e.bottom = "0", e.pointerEvents = "none", e.visibility = "hidden", document.body.appendChild(n_);
	}
	return n_.getBoundingClientRect();
}
function i_(e, t) {
	let n = r_();
	return {
		top: t,
		left: e,
		height: 0,
		width: 0,
		right: n.width - e,
		bottom: n.height - t
	};
}
function a_(e) {
	let t = e.getBoundingClientRect(), n = r_();
	return {
		left: t.left - n.left,
		top: t.top - n.top,
		bottom: n.height + n.top - t.bottom,
		right: n.width + n.left - t.right,
		width: t.width,
		height: t.height
	};
}
function o_(e) {
	return e.nodeType === 9 ? null : e.parentNode;
}
function s_(e) {
	if (e === null) return null;
	let t = o_(e);
	if (t === null) return null;
	if (t.nodeType === 9) return document;
	if (t.nodeType === 1) {
		let { overflow: e, overflowX: n, overflowY: r } = getComputedStyle(t);
		if (/(auto|scroll|overlay)/.test(e + r + n)) return t;
	}
	return s_(t);
}
//#endregion
//#region node_modules/vueuc/es/binder/src/Binder.js
var c_ = /* @__PURE__ */ N({
	name: "Binder",
	props: {
		syncTargetWithParent: Boolean,
		syncTarget: {
			type: Boolean,
			default: !0
		}
	},
	setup(e) {
		zn("VBinder", da()?.proxy);
		let t = Bn("VBinder", null), n = /* @__PURE__ */ j(null), r = (r) => {
			n.value = r, t && e.syncTargetWithParent && t.setTargetRef(r);
		}, i = [], a = () => {
			let e = n.value;
			for (; e = s_(e), e !== null;) i.push(e);
			for (let e of i) yg("scroll", e, u, !0);
		}, o = () => {
			for (let e of i) bg("scroll", e, u, !0);
			i = [];
		}, s = /* @__PURE__ */ new Set(), c = (e) => {
			s.size === 0 && a(), s.has(e) || s.add(e);
		}, l = (e) => {
			s.has(e) && s.delete(e), s.size === 0 && o();
		}, u = () => {
			zm(d);
		}, d = () => {
			s.forEach((e) => e());
		}, f = /* @__PURE__ */ new Set(), p = (e) => {
			f.size === 0 && yg("resize", window, h), f.has(e) || f.add(e);
		}, m = (e) => {
			f.has(e) && f.delete(e), f.size === 0 && bg("resize", window, h);
		}, h = () => {
			f.forEach((e) => e());
		};
		return Lr(() => {
			bg("resize", window, h), o();
		}), {
			targetRef: n,
			setTargetRef: r,
			addScrollListener: c,
			removeScrollListener: l,
			addResizeListener: p,
			removeResizeListener: m
		};
	},
	render() {
		return $g("binder", this.$slots);
	}
}), l_ = /* @__PURE__ */ N({
	name: "Target",
	setup() {
		let { setTargetRef: e, syncTarget: t } = Bn("VBinder");
		return {
			syncTarget: t,
			setTargetDirective: {
				mounted: e,
				updated: e
			}
		};
	},
	render() {
		let { syncTarget: e, setTargetDirective: t } = this;
		return e ? Ln(t_("follower", this.$slots), [[t]]) : t_("follower", this.$slots);
	}
}), u_ = "@@mmoContext", d_ = {
	mounted(e, { value: t }) {
		e[u_] = { handler: void 0 }, typeof t == "function" && (e[u_].handler = t, yg("mousemoveoutside", e, t));
	},
	updated(e, { value: t }) {
		let n = e[u_];
		typeof t == "function" ? n.handler ? n.handler !== t && (bg("mousemoveoutside", e, n.handler), n.handler = t, yg("mousemoveoutside", e, t)) : (e[u_].handler = t, yg("mousemoveoutside", e, t)) : n.handler &&= (bg("mousemoveoutside", e, n.handler), void 0);
	},
	unmounted(e) {
		let { handler: t } = e[u_];
		t && bg("mousemoveoutside", e, t), e[u_].handler = void 0;
	}
}, f_ = "@@coContext", p_ = {
	mounted(e, { value: t, modifiers: n }) {
		e[f_] = { handler: void 0 }, typeof t == "function" && (e[f_].handler = t, yg("clickoutside", e, t, { capture: n.capture }));
	},
	updated(e, { value: t, modifiers: n }) {
		let r = e[f_];
		typeof t == "function" ? r.handler ? r.handler !== t && (bg("clickoutside", e, r.handler, { capture: n.capture }), r.handler = t, yg("clickoutside", e, t, { capture: n.capture })) : (e[f_].handler = t, yg("clickoutside", e, t, { capture: n.capture })) : r.handler &&= (bg("clickoutside", e, r.handler, { capture: n.capture }), void 0);
	},
	unmounted(e, { modifiers: t }) {
		let { handler: n } = e[f_];
		n && bg("clickoutside", e, n, { capture: t.capture }), e[f_].handler = void 0;
	}
};
//#endregion
//#region node_modules/vdirs/es/utils.js
function m_(e, t) {
	console.error(`[vdirs/${e}]: ${t}`);
}
var h_ = new class {
	constructor() {
		this.elementZIndex = /* @__PURE__ */ new Map(), this.nextZIndex = 2e3;
	}
	get elementCount() {
		return this.elementZIndex.size;
	}
	ensureZIndex(e, t) {
		let { elementZIndex: n } = this;
		if (t !== void 0) {
			e.style.zIndex = `${t}`, n.delete(e);
			return;
		}
		let { nextZIndex: r } = this;
		n.has(e) && n.get(e) + 1 === this.nextZIndex || (e.style.zIndex = `${r}`, n.set(e, r), this.nextZIndex = r + 1, this.squashState());
	}
	unregister(e, t) {
		let { elementZIndex: n } = this;
		n.has(e) ? n.delete(e) : t === void 0 && m_("z-index-manager/unregister-element", "Element not found when unregistering."), this.squashState();
	}
	squashState() {
		let { elementCount: e } = this;
		e || (this.nextZIndex = 2e3), this.nextZIndex - e > 2500 && this.rearrange();
	}
	rearrange() {
		let e = Array.from(this.elementZIndex.entries());
		e.sort((e, t) => e[1] - t[1]), this.nextZIndex = 2e3, e.forEach((e) => {
			let t = e[0], n = this.nextZIndex++;
			`${n}` !== t.style.zIndex && (t.style.zIndex = `${n}`);
		});
	}
}(), g_ = "@@ziContext", __ = {
	mounted(e, t) {
		let { value: n = {} } = t, { zIndex: r, enabled: i } = n;
		e[g_] = {
			enabled: !!i,
			initialized: !1
		}, i && (h_.ensureZIndex(e, r), e[g_].initialized = !0);
	},
	updated(e, t) {
		let { value: n = {} } = t, { zIndex: r, enabled: i } = n, a = e[g_].enabled;
		i && !a && (h_.ensureZIndex(e, r), e[g_].initialized = !0), e[g_].enabled = !!i;
	},
	unmounted(e, t) {
		if (!e[g_].initialized) return;
		let { value: n = {} } = t, { zIndex: r } = n;
		h_.unregister(e, r);
	}
};
//#endregion
//#region node_modules/vueuc/es/shared/warn.js
function v_(e, t) {
	console.error(`[vueuc/${e}]: ${t}`);
}
//#endregion
//#region node_modules/vueuc/es/shared/cssr.js
var { c: y_ } = Ts(), b_ = "vueuc-style";
//#endregion
//#region node_modules/vueuc/es/shared/finweck-tree.js
function x_(e) {
	return e & -e;
}
var S_ = class {
	constructor(e, t) {
		this.l = e, this.min = t;
		let n = Array(e + 1);
		for (let t = 0; t < e + 1; ++t) n[t] = 0;
		this.ft = n;
	}
	add(e, t) {
		if (t === 0) return;
		let { l: n, ft: r } = this;
		for (e += 1; e <= n;) r[e] += t, e += x_(e);
	}
	get(e) {
		return this.sum(e + 1) - this.sum(e);
	}
	sum(e) {
		if (e === void 0 && (e = this.l), e <= 0) return 0;
		let { ft: t, min: n, l: r } = this;
		if (e > r) throw Error("[FinweckTree.sum]: `i` is larger than length.");
		let i = e * n;
		for (; e > 0;) i += t[e], e -= x_(e);
		return i;
	}
	getBound(e) {
		let t = 0, n = this.l;
		for (; n > t;) {
			let r = Math.floor((t + n) / 2), i = this.sum(r);
			if (i > e) {
				n = r;
				continue;
			}
			if (i < e) {
				if (t === r) return this.sum(t + 1) <= e ? t + 1 : r;
				t = r;
			} else return r;
		}
		return t;
	}
};
//#endregion
//#region node_modules/vueuc/es/shared/resolve-to.js
function C_(e) {
	return typeof e == "string" ? document.querySelector(e) : e() ?? null;
}
//#endregion
//#region node_modules/vueuc/es/lazy-teleport/src/index.js
var w_ = /* @__PURE__ */ N({
	name: "LazyTeleport",
	props: {
		to: {
			type: [String, Object],
			default: void 0
		},
		disabled: Boolean,
		show: {
			type: Boolean,
			required: !0
		}
	},
	setup(e) {
		return {
			showTeleport: xg(/* @__PURE__ */ M(e, "show")),
			mergedTo: z(() => {
				let { to: t } = e;
				return t ?? "body";
			})
		};
	},
	render() {
		return this.showTeleport ? this.disabled ? $g("lazy-teleport", this.$slots) : Ea(rr, {
			disabled: this.disabled,
			to: this.mergedTo
		}, $g("lazy-teleport", this.$slots)) : null;
	}
}), T_ = {
	top: "bottom",
	bottom: "top",
	left: "right",
	right: "left"
}, E_ = {
	start: "end",
	center: "center",
	end: "start"
}, D_ = {
	top: "height",
	bottom: "height",
	left: "width",
	right: "width"
}, O_ = {
	"bottom-start": "top left",
	bottom: "top center",
	"bottom-end": "top right",
	"top-start": "bottom left",
	top: "bottom center",
	"top-end": "bottom right",
	"right-start": "top left",
	right: "center left",
	"right-end": "bottom left",
	"left-start": "top right",
	left: "center right",
	"left-end": "bottom right"
}, k_ = {
	"bottom-start": "bottom left",
	bottom: "bottom center",
	"bottom-end": "bottom right",
	"top-start": "top left",
	top: "top center",
	"top-end": "top right",
	"right-start": "top right",
	right: "center right",
	"right-end": "bottom right",
	"left-start": "top left",
	left: "center left",
	"left-end": "bottom left"
}, A_ = {
	"bottom-start": "right",
	"bottom-end": "left",
	"top-start": "right",
	"top-end": "left",
	"right-start": "bottom",
	"right-end": "top",
	"left-start": "bottom",
	"left-end": "top"
}, j_ = {
	top: !0,
	bottom: !1,
	left: !0,
	right: !1
}, M_ = {
	top: "end",
	bottom: "start",
	left: "end",
	right: "start"
};
function N_(e, t, n, r, i, a) {
	if (!i || a) return {
		placement: e,
		top: 0,
		left: 0
	};
	let [o, s] = e.split("-"), c = s ?? "center", l = {
		top: 0,
		left: 0
	}, u = (e, i, a) => {
		let o = 0, s = 0, c = n[e] - t[i] - t[e];
		return c > 0 && r && (a ? s = j_[i] ? c : -c : o = j_[i] ? c : -c), {
			left: o,
			top: s
		};
	}, d = o === "left" || o === "right";
	if (c !== "center") {
		let r = A_[e], i = T_[r], a = D_[r];
		if (n[a] > t[a]) {
			if (t[r] + t[a] < n[a]) {
				let e = (n[a] - t[a]) / 2;
				t[r] < e || t[i] < e ? t[r] < t[i] ? (c = E_[s], l = u(a, i, d)) : l = u(a, r, d) : c = "center";
			}
		} else n[a] < t[a] && t[i] < 0 && t[r] > t[i] && (c = E_[s]);
	} else {
		let e = o === "bottom" || o === "top" ? "left" : "top", r = T_[e], i = D_[e], a = (n[i] - t[i]) / 2;
		(t[e] < a || t[r] < a) && (t[e] > t[r] ? (c = M_[e], l = u(i, e, d)) : (c = M_[r], l = u(i, r, d)));
	}
	let f = o;
	return t[o] < n[D_[o]] && t[o] < t[T_[o]] && (f = T_[o]), {
		placement: c === "center" ? f : `${f}-${c}`,
		left: l.left,
		top: l.top
	};
}
function P_(e, t) {
	return t ? k_[e] : O_[e];
}
function F_(e, t, n, r, i, a) {
	if (a) switch (e) {
		case "bottom-start": return {
			top: `${Math.round(n.top - t.top + n.height)}px`,
			left: `${Math.round(n.left - t.left)}px`,
			transform: "translateY(-100%)"
		};
		case "bottom-end": return {
			top: `${Math.round(n.top - t.top + n.height)}px`,
			left: `${Math.round(n.left - t.left + n.width)}px`,
			transform: "translateX(-100%) translateY(-100%)"
		};
		case "top-start": return {
			top: `${Math.round(n.top - t.top)}px`,
			left: `${Math.round(n.left - t.left)}px`,
			transform: ""
		};
		case "top-end": return {
			top: `${Math.round(n.top - t.top)}px`,
			left: `${Math.round(n.left - t.left + n.width)}px`,
			transform: "translateX(-100%)"
		};
		case "right-start": return {
			top: `${Math.round(n.top - t.top)}px`,
			left: `${Math.round(n.left - t.left + n.width)}px`,
			transform: "translateX(-100%)"
		};
		case "right-end": return {
			top: `${Math.round(n.top - t.top + n.height)}px`,
			left: `${Math.round(n.left - t.left + n.width)}px`,
			transform: "translateX(-100%) translateY(-100%)"
		};
		case "left-start": return {
			top: `${Math.round(n.top - t.top)}px`,
			left: `${Math.round(n.left - t.left)}px`,
			transform: ""
		};
		case "left-end": return {
			top: `${Math.round(n.top - t.top + n.height)}px`,
			left: `${Math.round(n.left - t.left)}px`,
			transform: "translateY(-100%)"
		};
		case "top": return {
			top: `${Math.round(n.top - t.top)}px`,
			left: `${Math.round(n.left - t.left + n.width / 2)}px`,
			transform: "translateX(-50%)"
		};
		case "right": return {
			top: `${Math.round(n.top - t.top + n.height / 2)}px`,
			left: `${Math.round(n.left - t.left + n.width)}px`,
			transform: "translateX(-100%) translateY(-50%)"
		};
		case "left": return {
			top: `${Math.round(n.top - t.top + n.height / 2)}px`,
			left: `${Math.round(n.left - t.left)}px`,
			transform: "translateY(-50%)"
		};
		default: return {
			top: `${Math.round(n.top - t.top + n.height)}px`,
			left: `${Math.round(n.left - t.left + n.width / 2)}px`,
			transform: "translateX(-50%) translateY(-100%)"
		};
	}
	switch (e) {
		case "bottom-start": return {
			top: `${Math.round(n.top - t.top + n.height + r)}px`,
			left: `${Math.round(n.left - t.left + i)}px`,
			transform: ""
		};
		case "bottom-end": return {
			top: `${Math.round(n.top - t.top + n.height + r)}px`,
			left: `${Math.round(n.left - t.left + n.width + i)}px`,
			transform: "translateX(-100%)"
		};
		case "top-start": return {
			top: `${Math.round(n.top - t.top + r)}px`,
			left: `${Math.round(n.left - t.left + i)}px`,
			transform: "translateY(-100%)"
		};
		case "top-end": return {
			top: `${Math.round(n.top - t.top + r)}px`,
			left: `${Math.round(n.left - t.left + n.width + i)}px`,
			transform: "translateX(-100%) translateY(-100%)"
		};
		case "right-start": return {
			top: `${Math.round(n.top - t.top + r)}px`,
			left: `${Math.round(n.left - t.left + n.width + i)}px`,
			transform: ""
		};
		case "right-end": return {
			top: `${Math.round(n.top - t.top + n.height + r)}px`,
			left: `${Math.round(n.left - t.left + n.width + i)}px`,
			transform: "translateY(-100%)"
		};
		case "left-start": return {
			top: `${Math.round(n.top - t.top + r)}px`,
			left: `${Math.round(n.left - t.left + i)}px`,
			transform: "translateX(-100%)"
		};
		case "left-end": return {
			top: `${Math.round(n.top - t.top + n.height + r)}px`,
			left: `${Math.round(n.left - t.left + i)}px`,
			transform: "translateX(-100%) translateY(-100%)"
		};
		case "top": return {
			top: `${Math.round(n.top - t.top + r)}px`,
			left: `${Math.round(n.left - t.left + n.width / 2 + i)}px`,
			transform: "translateY(-100%) translateX(-50%)"
		};
		case "right": return {
			top: `${Math.round(n.top - t.top + n.height / 2 + r)}px`,
			left: `${Math.round(n.left - t.left + n.width + i)}px`,
			transform: "translateY(-50%)"
		};
		case "left": return {
			top: `${Math.round(n.top - t.top + n.height / 2 + r)}px`,
			left: `${Math.round(n.left - t.left + i)}px`,
			transform: "translateY(-50%) translateX(-100%)"
		};
		default: return {
			top: `${Math.round(n.top - t.top + n.height + r)}px`,
			left: `${Math.round(n.left - t.left + n.width / 2 + i)}px`,
			transform: "translateX(-50%)"
		};
	}
}
//#endregion
//#region node_modules/vueuc/es/binder/src/Follower.js
var I_ = y_([y_(".v-binder-follower-container", {
	position: "absolute",
	left: "0",
	right: "0",
	top: "0",
	height: "0",
	pointerEvents: "none",
	zIndex: "auto"
}), y_(".v-binder-follower-content", {
	position: "absolute",
	zIndex: "auto"
}, [y_("> *", { pointerEvents: "all" })])]), L_ = /* @__PURE__ */ N({
	name: "Follower",
	inheritAttrs: !1,
	props: {
		show: Boolean,
		enabled: {
			type: Boolean,
			default: void 0
		},
		placement: {
			type: String,
			default: "bottom"
		},
		syncTrigger: {
			type: Array,
			default: ["resize", "scroll"]
		},
		to: [String, Object],
		flip: {
			type: Boolean,
			default: !0
		},
		internalShift: Boolean,
		x: Number,
		y: Number,
		width: String,
		minWidth: String,
		containerClass: String,
		teleportDisabled: Boolean,
		zindexable: {
			type: Boolean,
			default: !0
		},
		zIndex: Number,
		overlap: Boolean
	},
	setup(e) {
		let t = Bn("VBinder"), n = Sg(() => e.enabled === void 0 ? e.show : e.enabled), r = /* @__PURE__ */ j(null), i = /* @__PURE__ */ j(null), a = () => {
			let { syncTrigger: n } = e;
			n.includes("scroll") && t.addScrollListener(c), n.includes("resize") && t.addResizeListener(c);
		}, o = () => {
			t.removeScrollListener(c), t.removeResizeListener(c);
		};
		Fr(() => {
			n.value && (c(), a());
		});
		let s = Om();
		I_.mount({
			id: "vueuc/binder",
			head: !0,
			anchorMetaName: b_,
			ssr: s
		}), Lr(() => {
			o();
		}), Tg(() => {
			n.value && c();
		});
		let c = () => {
			if (!n.value) return;
			let a = r.value;
			if (a === null) return;
			let o = t.targetRef, { x: s, y: c, overlap: l } = e, u = s !== void 0 && c !== void 0 ? i_(s, c) : a_(o);
			a.style.setProperty("--v-target-width", `${Math.round(u.width)}px`), a.style.setProperty("--v-target-height", `${Math.round(u.height)}px`);
			let { width: d, minWidth: f, placement: p, internalShift: m, flip: h } = e;
			a.setAttribute("v-placement", p), l ? a.setAttribute("v-overlap", "") : a.removeAttribute("v-overlap");
			let { style: g } = a;
			g.width = d === "target" ? `${u.width}px` : d === void 0 ? "" : d, g.minWidth = f === "target" ? `${u.width}px` : f === void 0 ? "" : f;
			let _ = a_(a), v = a_(i.value), { left: y, top: b, placement: x } = N_(p, u, _, m, h, l), S = P_(x, l), { left: C, top: w, transform: T } = F_(x, v, u, b, y, l);
			a.setAttribute("v-placement", x), a.style.setProperty("--v-offset-left", `${Math.round(y)}px`), a.style.setProperty("--v-offset-top", `${Math.round(b)}px`), a.style.transform = `translateX(${C}) translateY(${w}) ${T}`, a.style.setProperty("--v-transform-origin", S), a.style.transformOrigin = S;
		};
		Wn(n, (e) => {
			e ? (a(), l()) : o();
		});
		let l = () => {
			wn().then(c).catch((e) => console.error(e));
		};
		[
			"placement",
			"x",
			"y",
			"internalShift",
			"flip",
			"width",
			"overlap",
			"minWidth"
		].forEach((t) => {
			Wn(/* @__PURE__ */ M(e, t), c);
		}), ["teleportDisabled"].forEach((t) => {
			Wn(/* @__PURE__ */ M(e, t), l);
		}), Wn(/* @__PURE__ */ M(e, "syncTrigger"), (e) => {
			e.includes("resize") ? t.addResizeListener(c) : t.removeResizeListener(c), e.includes("scroll") ? t.addScrollListener(c) : t.removeScrollListener(c);
		});
		let u = Dg();
		return {
			VBinder: t,
			mergedEnabled: n,
			offsetContainerRef: i,
			followerRef: r,
			mergedTo: Sg(() => {
				let { to: t } = e;
				if (t !== void 0) return t;
				u.value;
			}),
			syncPosition: c
		};
	},
	render() {
		return Ea(w_, {
			show: this.show,
			to: this.mergedTo,
			disabled: this.teleportDisabled
		}, { default: () => {
			var e;
			let t = Ea("div", {
				class: ["v-binder-follower-container", this.containerClass],
				ref: "offsetContainerRef"
			}, [Ea("div", {
				class: "v-binder-follower-content",
				ref: "followerRef"
			}, (e = this.$slots).default?.call(e))]);
			return this.zindexable ? Ln(t, [[__, {
				enabled: this.mergedEnabled,
				zIndex: this.zIndex
			}]]) : t;
		} });
	}
}), R_ = [], z_ = function() {
	return R_.some(function(e) {
		return e.activeTargets.length > 0;
	});
}, B_ = function() {
	return R_.some(function(e) {
		return e.skippedTargets.length > 0;
	});
}, V_ = "ResizeObserver loop completed with undelivered notifications.", H_ = function() {
	var e;
	typeof ErrorEvent == "function" ? e = new ErrorEvent("error", { message: V_ }) : (e = document.createEvent("Event"), e.initEvent("error", !1, !1), e.message = V_), window.dispatchEvent(e);
}, U_;
(function(e) {
	e.BORDER_BOX = "border-box", e.CONTENT_BOX = "content-box", e.DEVICE_PIXEL_CONTENT_BOX = "device-pixel-content-box";
})(U_ ||= {});
//#endregion
//#region node_modules/@juggle/resize-observer/lib/utils/freeze.js
var W_ = function(e) {
	return Object.freeze(e);
}, G_ = function() {
	function e(e, t) {
		this.inlineSize = e, this.blockSize = t, W_(this);
	}
	return e;
}(), K_ = function() {
	function e(e, t, n, r) {
		return this.x = e, this.y = t, this.width = n, this.height = r, this.top = this.y, this.left = this.x, this.bottom = this.top + this.height, this.right = this.left + this.width, W_(this);
	}
	return e.prototype.toJSON = function() {
		var e = this;
		return {
			x: e.x,
			y: e.y,
			top: e.top,
			right: e.right,
			bottom: e.bottom,
			left: e.left,
			width: e.width,
			height: e.height
		};
	}, e.fromRect = function(t) {
		return new e(t.x, t.y, t.width, t.height);
	}, e;
}(), q_ = function(e) {
	return e instanceof SVGElement && "getBBox" in e;
}, J_ = function(e) {
	if (q_(e)) {
		var t = e.getBBox(), n = t.width, r = t.height;
		return !n && !r;
	}
	var i = e, a = i.offsetWidth, o = i.offsetHeight;
	return !(a || o || e.getClientRects().length);
}, Y_ = function(e) {
	if (e instanceof Element) return !0;
	var t = e?.ownerDocument?.defaultView;
	return !!(t && e instanceof t.Element);
}, X_ = function(e) {
	switch (e.tagName) {
		case "INPUT": if (e.type !== "image") break;
		case "VIDEO":
		case "AUDIO":
		case "EMBED":
		case "OBJECT":
		case "CANVAS":
		case "IFRAME":
		case "IMG": return !0;
	}
	return !1;
}, Z_ = typeof window < "u" ? window : {}, Q_ = /* @__PURE__ */ new WeakMap(), $_ = /auto|scroll/, ev = /^tb|vertical/, tv = /msie|trident/i.test(Z_.navigator && Z_.navigator.userAgent), nv = function(e) {
	return parseFloat(e || "0");
}, rv = function(e, t, n) {
	return e === void 0 && (e = 0), t === void 0 && (t = 0), n === void 0 && (n = !1), new G_((n ? t : e) || 0, (n ? e : t) || 0);
}, iv = W_({
	devicePixelContentBoxSize: rv(),
	borderBoxSize: rv(),
	contentBoxSize: rv(),
	contentRect: new K_(0, 0, 0, 0)
}), av = function(e, t) {
	if (t === void 0 && (t = !1), Q_.has(e) && !t) return Q_.get(e);
	if (J_(e)) return Q_.set(e, iv), iv;
	var n = getComputedStyle(e), r = q_(e) && e.ownerSVGElement && e.getBBox(), i = !tv && n.boxSizing === "border-box", a = ev.test(n.writingMode || ""), o = !r && $_.test(n.overflowY || ""), s = !r && $_.test(n.overflowX || ""), c = r ? 0 : nv(n.paddingTop), l = r ? 0 : nv(n.paddingRight), u = r ? 0 : nv(n.paddingBottom), d = r ? 0 : nv(n.paddingLeft), f = r ? 0 : nv(n.borderTopWidth), p = r ? 0 : nv(n.borderRightWidth), m = r ? 0 : nv(n.borderBottomWidth), h = r ? 0 : nv(n.borderLeftWidth), g = d + l, _ = c + u, v = h + p, y = f + m, b = s ? e.offsetHeight - y - e.clientHeight : 0, x = o ? e.offsetWidth - v - e.clientWidth : 0, S = i ? g + v : 0, C = i ? _ + y : 0, w = r ? r.width : nv(n.width) - S - x, T = r ? r.height : nv(n.height) - C - b, E = w + g + x + v, D = T + _ + b + y, O = W_({
		devicePixelContentBoxSize: rv(Math.round(w * devicePixelRatio), Math.round(T * devicePixelRatio), a),
		borderBoxSize: rv(E, D, a),
		contentBoxSize: rv(w, T, a),
		contentRect: new K_(d, c, w, T)
	});
	return Q_.set(e, O), O;
}, ov = function(e, t, n) {
	var r = av(e, n), i = r.borderBoxSize, a = r.contentBoxSize, o = r.devicePixelContentBoxSize;
	switch (t) {
		case U_.DEVICE_PIXEL_CONTENT_BOX: return o;
		case U_.BORDER_BOX: return i;
		default: return a;
	}
}, sv = function() {
	function e(e) {
		var t = av(e);
		this.target = e, this.contentRect = t.contentRect, this.borderBoxSize = W_([t.borderBoxSize]), this.contentBoxSize = W_([t.contentBoxSize]), this.devicePixelContentBoxSize = W_([t.devicePixelContentBoxSize]);
	}
	return e;
}(), cv = function(e) {
	if (J_(e)) return Infinity;
	for (var t = 0, n = e.parentNode; n;) t += 1, n = n.parentNode;
	return t;
}, lv = function() {
	var e = Infinity, t = [];
	R_.forEach(function(n) {
		if (n.activeTargets.length !== 0) {
			var r = [];
			n.activeTargets.forEach(function(t) {
				var n = new sv(t.target), i = cv(t.target);
				r.push(n), t.lastReportedSize = ov(t.target, t.observedBox), i < e && (e = i);
			}), t.push(function() {
				n.callback.call(n.observer, r, n.observer);
			}), n.activeTargets.splice(0, n.activeTargets.length);
		}
	});
	for (var n = 0, r = t; n < r.length; n++) {
		var i = r[n];
		i();
	}
	return e;
}, uv = function(e) {
	R_.forEach(function(t) {
		t.activeTargets.splice(0, t.activeTargets.length), t.skippedTargets.splice(0, t.skippedTargets.length), t.observationTargets.forEach(function(n) {
			n.isActive() && (cv(n.target) > e ? t.activeTargets.push(n) : t.skippedTargets.push(n));
		});
	});
}, dv = function() {
	var e = 0;
	for (uv(e); z_();) e = lv(), uv(e);
	return B_() && H_(), e > 0;
}, fv, pv = [], mv = function() {
	return pv.splice(0).forEach(function(e) {
		return e();
	});
}, hv = function(e) {
	if (!fv) {
		var t = 0, n = document.createTextNode("");
		new MutationObserver(function() {
			return mv();
		}).observe(n, { characterData: !0 }), fv = function() {
			n.textContent = `${t ? t-- : t++}`;
		};
	}
	pv.push(e), fv();
}, gv = function(e) {
	hv(function() {
		requestAnimationFrame(e);
	});
}, _v = 0, vv = function() {
	return !!_v;
}, yv = 250, bv = {
	attributes: !0,
	characterData: !0,
	childList: !0,
	subtree: !0
}, xv = [
	"resize",
	"load",
	"transitionend",
	"animationend",
	"animationstart",
	"animationiteration",
	"keyup",
	"keydown",
	"mouseup",
	"mousedown",
	"mouseover",
	"mouseout",
	"blur",
	"focus"
], Sv = function(e) {
	return e === void 0 && (e = 0), Date.now() + e;
}, Cv = !1, wv = new (function() {
	function e() {
		var e = this;
		this.stopped = !0, this.listener = function() {
			return e.schedule();
		};
	}
	return e.prototype.run = function(e) {
		var t = this;
		if (e === void 0 && (e = yv), !Cv) {
			Cv = !0;
			var n = Sv(e);
			gv(function() {
				var r = !1;
				try {
					r = dv();
				} finally {
					if (Cv = !1, e = n - Sv(), !vv()) return;
					r ? t.run(1e3) : e > 0 ? t.run(e) : t.start();
				}
			});
		}
	}, e.prototype.schedule = function() {
		this.stop(), this.run();
	}, e.prototype.observe = function() {
		var e = this, t = function() {
			return e.observer && e.observer.observe(document.body, bv);
		};
		document.body ? t() : Z_.addEventListener("DOMContentLoaded", t);
	}, e.prototype.start = function() {
		var e = this;
		this.stopped && (this.stopped = !1, this.observer = new MutationObserver(this.listener), this.observe(), xv.forEach(function(t) {
			return Z_.addEventListener(t, e.listener, !0);
		}));
	}, e.prototype.stop = function() {
		var e = this;
		this.stopped ||= (this.observer && this.observer.disconnect(), xv.forEach(function(t) {
			return Z_.removeEventListener(t, e.listener, !0);
		}), !0);
	}, e;
}())(), Tv = function(e) {
	!_v && e > 0 && wv.start(), _v += e, !_v && wv.stop();
}, Ev = function(e) {
	return !q_(e) && !X_(e) && getComputedStyle(e).display === "inline";
}, Dv = function() {
	function e(e, t) {
		this.target = e, this.observedBox = t || U_.CONTENT_BOX, this.lastReportedSize = {
			inlineSize: 0,
			blockSize: 0
		};
	}
	return e.prototype.isActive = function() {
		var e = ov(this.target, this.observedBox, !0);
		return Ev(this.target) && (this.lastReportedSize = e), this.lastReportedSize.inlineSize !== e.inlineSize || this.lastReportedSize.blockSize !== e.blockSize;
	}, e;
}(), Ov = function() {
	function e(e, t) {
		this.activeTargets = [], this.skippedTargets = [], this.observationTargets = [], this.observer = e, this.callback = t;
	}
	return e;
}(), kv = /* @__PURE__ */ new WeakMap(), Av = function(e, t) {
	for (var n = 0; n < e.length; n += 1) if (e[n].target === t) return n;
	return -1;
}, jv = function() {
	function e() {}
	return e.connect = function(e, t) {
		var n = new Ov(e, t);
		kv.set(e, n);
	}, e.observe = function(e, t, n) {
		var r = kv.get(e), i = r.observationTargets.length === 0;
		Av(r.observationTargets, t) < 0 && (i && R_.push(r), r.observationTargets.push(new Dv(t, n && n.box)), Tv(1), wv.schedule());
	}, e.unobserve = function(e, t) {
		var n = kv.get(e), r = Av(n.observationTargets, t), i = n.observationTargets.length === 1;
		r >= 0 && (i && R_.splice(R_.indexOf(n), 1), n.observationTargets.splice(r, 1), Tv(-1));
	}, e.disconnect = function(e) {
		var t = this, n = kv.get(e);
		n.observationTargets.slice().forEach(function(n) {
			return t.unobserve(e, n.target);
		}), n.activeTargets.splice(0, n.activeTargets.length);
	}, e;
}(), Mv = function() {
	function e(e) {
		if (arguments.length === 0) throw TypeError("Failed to construct 'ResizeObserver': 1 argument required, but only 0 present.");
		if (typeof e != "function") throw TypeError("Failed to construct 'ResizeObserver': The callback provided as parameter 1 is not a function.");
		jv.connect(this, e);
	}
	return e.prototype.observe = function(e, t) {
		if (arguments.length === 0) throw TypeError("Failed to execute 'observe' on 'ResizeObserver': 1 argument required, but only 0 present.");
		if (!Y_(e)) throw TypeError("Failed to execute 'observe' on 'ResizeObserver': parameter 1 is not of type 'Element");
		jv.observe(this, e, t);
	}, e.prototype.unobserve = function(e) {
		if (arguments.length === 0) throw TypeError("Failed to execute 'unobserve' on 'ResizeObserver': 1 argument required, but only 0 present.");
		if (!Y_(e)) throw TypeError("Failed to execute 'unobserve' on 'ResizeObserver': parameter 1 is not of type 'Element");
		jv.unobserve(this, e);
	}, e.prototype.disconnect = function() {
		jv.disconnect(this);
	}, e.toString = function() {
		return "function ResizeObserver () { [polyfill code] }";
	}, e;
}(), Nv = new class {
	constructor() {
		this.handleResize = this.handleResize.bind(this), this.observer = new (typeof window < "u" && window.ResizeObserver || Mv)(this.handleResize), this.elHandlersMap = /* @__PURE__ */ new Map();
	}
	handleResize(e) {
		for (let t of e) {
			let e = this.elHandlersMap.get(t.target);
			e !== void 0 && e(t);
		}
	}
	registerHandler(e, t) {
		this.elHandlersMap.set(e, t), this.observer.observe(e);
	}
	unregisterHandler(e) {
		this.elHandlersMap.has(e) && (this.elHandlersMap.delete(e), this.observer.unobserve(e));
	}
}(), Pv = /* @__PURE__ */ N({
	name: "ResizeObserver",
	props: { onResize: Function },
	setup(e) {
		let t = !1, n = da().proxy;
		function r(t) {
			let { onResize: n } = e;
			n !== void 0 && n(t);
		}
		Fr(() => {
			let e = n.$el;
			if (e === void 0) {
				v_("resize-observer", "$el does not exist.");
				return;
			}
			if (e.nextElementSibling !== e.nextSibling && e.nodeType === 3 && e.nodeValue !== "") {
				v_("resize-observer", "$el can not be observed (it may be a text node).");
				return;
			}
			e.nextElementSibling !== null && (Nv.registerHandler(e.nextElementSibling, r), t = !0);
		}), Lr(() => {
			t && Nv.unregisterHandler(n.$el.nextElementSibling);
		});
	},
	render() {
		return Br(this.$slots, "default");
	}
}), Fv;
function Iv() {
	return typeof document > "u" ? !1 : (Fv === void 0 && (Fv = "matchMedia" in window && window.matchMedia("(pointer:coarse)").matches), Fv);
}
var Lv;
function Rv() {
	return typeof document > "u" ? 1 : (Lv === void 0 && (Lv = "chrome" in window ? window.devicePixelRatio : 1), Lv);
}
//#endregion
//#region node_modules/vueuc/es/virtual-list/src/context.js
var zv = "VVirtualListXScroll";
//#endregion
//#region node_modules/vueuc/es/virtual-list/src/xScroll.js
function Bv({ columnsRef: e, renderColRef: t, renderItemWithColsRef: n }) {
	let r = /* @__PURE__ */ j(0), i = /* @__PURE__ */ j(0), a = z(() => {
		let t = e.value;
		if (t.length === 0) return null;
		let n = new S_(t.length, 0);
		return t.forEach((e, t) => {
			n.add(t, e.width);
		}), n;
	});
	return zn(zv, {
		startIndexRef: Sg(() => {
			let e = a.value;
			return e === null ? 0 : Math.max(e.getBound(i.value) - 1, 0);
		}),
		endIndexRef: Sg(() => {
			let t = a.value;
			return t === null ? 0 : Math.min(t.getBound(i.value + r.value) + 1, e.value.length - 1);
		}),
		columnsRef: e,
		renderColRef: t,
		renderItemWithColsRef: n,
		getLeft: (e) => {
			let t = a.value;
			return t === null ? 0 : t.sum(e);
		}
	}), {
		listWidthRef: r,
		scrollLeftRef: i
	};
}
//#endregion
//#region node_modules/vueuc/es/virtual-list/src/VirtualListRow.js
var Vv = /* @__PURE__ */ N({
	name: "VirtualListRow",
	props: {
		index: {
			type: Number,
			required: !0
		},
		item: {
			type: Object,
			required: !0
		}
	},
	setup() {
		let { startIndexRef: e, endIndexRef: t, columnsRef: n, getLeft: r, renderColRef: i, renderItemWithColsRef: a } = Bn(zv);
		return {
			startIndex: e,
			endIndex: t,
			columns: n,
			renderCol: i,
			renderItemWithCols: a,
			getLeft: r
		};
	},
	render() {
		let { startIndex: e, endIndex: t, columns: n, renderCol: r, renderItemWithCols: i, getLeft: a, item: o } = this;
		if (i != null) return i({
			itemIndex: this.index,
			startColIndex: e,
			endColIndex: t,
			allColumns: n,
			item: o,
			getLeft: a
		});
		if (r != null) {
			let i = [];
			for (let s = e; s <= t; ++s) {
				let e = n[s];
				i.push(r({
					column: e,
					left: a(s),
					item: o
				}));
			}
			return i;
		}
		return null;
	}
}), Hv = y_(".v-vl", {
	maxHeight: "inherit",
	height: "100%",
	overflow: "auto",
	minWidth: "1px"
}, [y_("&:not(.v-vl--show-scrollbar)", { scrollbarWidth: "none" }, [y_("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb", {
	width: 0,
	height: 0,
	display: "none"
})])]), Uv = /* @__PURE__ */ N({
	name: "VirtualList",
	inheritAttrs: !1,
	props: {
		showScrollbar: {
			type: Boolean,
			default: !0
		},
		columns: {
			type: Array,
			default: () => []
		},
		renderCol: Function,
		renderItemWithCols: Function,
		items: {
			type: Array,
			default: () => []
		},
		itemSize: {
			type: Number,
			required: !0
		},
		itemResizable: Boolean,
		itemsStyle: [String, Object],
		visibleItemsTag: {
			type: [String, Object],
			default: "div"
		},
		visibleItemsProps: Object,
		ignoreItemResize: Boolean,
		onScroll: Function,
		onWheel: Function,
		onResize: Function,
		defaultScrollKey: [Number, String],
		defaultScrollIndex: Number,
		keyField: {
			type: String,
			default: "key"
		},
		paddingTop: {
			type: [Number, String],
			default: 0
		},
		paddingBottom: {
			type: [Number, String],
			default: 0
		}
	},
	setup(e) {
		let t = Om();
		Hv.mount({
			id: "vueuc/virtual-list",
			head: !0,
			anchorMetaName: b_,
			ssr: t
		}), Fr(() => {
			let { defaultScrollIndex: t, defaultScrollKey: n } = e;
			t == null ? n != null && g({ key: n }) : g({ index: t });
		});
		let n = !1, r = !1;
		Or(() => {
			if (n = !1, !r) {
				r = !0;
				return;
			}
			g({
				top: p.value,
				left: o.value
			});
		}), kr(() => {
			n = !0, r ||= !0;
		});
		let i = Sg(() => {
			if (e.renderCol == null && e.renderItemWithCols == null || e.columns.length === 0) return;
			let t = 0;
			return e.columns.forEach((e) => {
				t += e.width;
			}), t;
		}), a = z(() => {
			let t = /* @__PURE__ */ new Map(), { keyField: n } = e;
			return e.items.forEach((e, r) => {
				t.set(e[n], r);
			}), t;
		}), { scrollLeftRef: o, listWidthRef: s } = Bv({
			columnsRef: /* @__PURE__ */ M(e, "columns"),
			renderColRef: /* @__PURE__ */ M(e, "renderCol"),
			renderItemWithColsRef: /* @__PURE__ */ M(e, "renderItemWithCols")
		}), c = /* @__PURE__ */ j(null), l = /* @__PURE__ */ j(void 0), u = /* @__PURE__ */ new Map(), d = z(() => {
			let { items: t, itemSize: n, keyField: r } = e, i = new S_(t.length, n);
			return t.forEach((e, t) => {
				let n = e[r], a = u.get(n);
				a !== void 0 && i.add(t, a);
			}), i;
		}), f = /* @__PURE__ */ j(0), p = /* @__PURE__ */ j(0), m = Sg(() => Math.max(d.value.getBound(p.value - Hm(e.paddingTop)) - 1, 0)), h = z(() => {
			let { value: t } = l;
			if (t === void 0) return [];
			let { items: n, itemSize: r } = e, i = m.value, a = Math.min(i + Math.ceil(t / r + 1), n.length - 1), o = [];
			for (let e = i; e <= a; ++e) o.push(n[e]);
			return o;
		}), g = (e, t) => {
			if (typeof e == "number") {
				b(e, t, "auto");
				return;
			}
			let { left: n, top: r, index: i, key: o, position: s, behavior: c, debounce: l = !0 } = e;
			if (n !== void 0 || r !== void 0) b(n, r, c);
			else if (i !== void 0) y(i, c, l);
			else if (o !== void 0) {
				let e = a.value.get(o);
				e !== void 0 && y(e, c, l);
			} else s === "bottom" ? b(0, 2 ** 53 - 1, c) : s === "top" && b(0, 0, c);
		}, _, v = null;
		function y(t, n, r) {
			let i = c.value;
			if (i == null) return;
			let { value: a } = d, o = a.sum(t) + Hm(e.paddingTop);
			if (!r) i.scrollTo({
				left: 0,
				top: o,
				behavior: n
			});
			else {
				_ = t, v !== null && window.clearTimeout(v), v = window.setTimeout(() => {
					_ = void 0, v = null;
				}, 16);
				let { scrollTop: e, offsetHeight: r } = i;
				if (o > e) {
					let s = a.get(t);
					o + s <= e + r || i.scrollTo({
						left: 0,
						top: o + s - r,
						behavior: n
					});
				} else i.scrollTo({
					left: 0,
					top: o,
					behavior: n
				});
			}
		}
		function b(e, t, n) {
			c.value?.scrollTo({
				left: e,
				top: t,
				behavior: n
			});
		}
		function x(t, r) {
			if (n || e.ignoreItemResize || O(r.target)) return;
			let { value: i } = d, o = a.value.get(t), s = i.get(o), l = r.borderBoxSize?.[0]?.blockSize ?? r.contentRect.height;
			if (l === s) return;
			l - e.itemSize === 0 ? u.delete(t) : u.set(t, l - e.itemSize);
			let p = l - s;
			if (p === 0) return;
			i.add(o, p);
			let m = c.value;
			if (m != null) {
				if (_ === void 0) {
					let e = i.sum(o);
					m.scrollTop > e && m.scrollBy(0, p);
				} else (o < _ || o === _ && l + i.sum(o) > m.scrollTop + m.offsetHeight) && m.scrollBy(0, p);
				D();
			}
			f.value++;
		}
		let S = !Iv(), C = !1;
		function w(t) {
			var n;
			(n = e.onScroll) == null || n.call(e, t), (!S || !C) && D();
		}
		function T(t) {
			var n;
			if ((n = e.onWheel) == null || n.call(e, t), S) {
				let e = c.value;
				if (e != null) {
					if (t.deltaX === 0 && (e.scrollTop === 0 && t.deltaY <= 0 || e.scrollTop + e.offsetHeight >= e.scrollHeight && t.deltaY >= 0)) return;
					t.preventDefault(), e.scrollTop += t.deltaY / Rv(), e.scrollLeft += t.deltaX / Rv(), D(), C = !0, zm(() => {
						C = !1;
					});
				}
			}
		}
		function E(t) {
			if (n || O(t.target)) return;
			if (e.renderCol == null && e.renderItemWithCols == null) {
				if (t.contentRect.height === l.value) return;
			} else if (t.contentRect.height === l.value && t.contentRect.width === s.value) return;
			l.value = t.contentRect.height, s.value = t.contentRect.width;
			let { onResize: r } = e;
			r !== void 0 && r(t);
		}
		function D() {
			let { value: e } = c;
			e != null && (p.value = e.scrollTop, o.value = e.scrollLeft);
		}
		function O(e) {
			let t = e;
			for (; t !== null;) {
				if (t.style.display === "none") return !0;
				t = t.parentElement;
			}
			return !1;
		}
		return {
			listHeight: l,
			listStyle: { overflow: "auto" },
			keyToIndex: a,
			itemsStyle: z(() => {
				let { itemResizable: t } = e, n = Um(d.value.sum());
				return f.value, [e.itemsStyle, {
					boxSizing: "content-box",
					width: Um(i.value),
					height: t ? "" : n,
					minHeight: t ? n : "",
					paddingTop: Um(e.paddingTop),
					paddingBottom: Um(e.paddingBottom)
				}];
			}),
			visibleItemsStyle: z(() => (f.value, { transform: `translateY(${Um(d.value.sum(m.value))})` })),
			viewportItems: h,
			listElRef: c,
			itemsElRef: /* @__PURE__ */ j(null),
			scrollTo: g,
			handleListResize: E,
			handleListScroll: w,
			handleListWheel: T,
			handleItemResize: x
		};
	},
	render() {
		let { itemResizable: e, keyField: t, keyToIndex: n, visibleItemsTag: r } = this;
		return Ea(Pv, { onResize: this.handleListResize }, { default: () => {
			var i;
			return Ea("div", aa(this.$attrs, {
				class: ["v-vl", this.showScrollbar && "v-vl--show-scrollbar"],
				onScroll: this.handleListScroll,
				onWheel: this.handleListWheel,
				ref: "listElRef"
			}), [this.items.length === 0 ? (i = this.$slots).empty?.call(i) : Ea("div", {
				ref: "itemsElRef",
				class: "v-vl-items",
				style: this.itemsStyle
			}, [Ea(r, Object.assign({
				class: "v-vl-visible-items",
				style: this.visibleItemsStyle
			}, this.visibleItemsProps), { default: () => {
				let { renderCol: r, renderItemWithCols: i } = this;
				return this.viewportItems.map((a) => {
					let o = a[t], s = n.get(o), c = r == null ? void 0 : Ea(Vv, {
						index: s,
						item: a
					}), l = i == null ? void 0 : Ea(Vv, {
						index: s,
						item: a
					}), u = this.$slots.default({
						item: a,
						renderedCols: c,
						renderedItemWithCols: l,
						index: s
					})[0];
					return e ? Ea(Pv, {
						key: o,
						onResize: (e) => this.handleItemResize(o, e)
					}, { default: () => u }) : (u.key = o, u);
				});
			} })])]);
		} });
	}
}), Wv = "v-hidden", Gv = y_("[v-hidden]", { display: "none!important" }), Kv = /* @__PURE__ */ N({
	name: "Overflow",
	props: {
		getCounter: Function,
		getTail: Function,
		updateCounter: Function,
		onUpdateCount: Function,
		onUpdateOverflow: Function
	},
	setup(e, { slots: t }) {
		let n = /* @__PURE__ */ j(null), r = /* @__PURE__ */ j(null);
		function i(i) {
			let { value: a } = n, { getCounter: o, getTail: s } = e, c;
			if (c = o === void 0 ? r.value : o(), !a || !c) return;
			c.hasAttribute(Wv) && c.removeAttribute(Wv);
			let { children: l } = a;
			if (i.showAllItemsBeforeCalculate) for (let e of l) e.hasAttribute(Wv) && e.removeAttribute(Wv);
			let u = a.offsetWidth, d = [], f = t.tail ? s?.() : null, p = f ? f.offsetWidth : 0, m = !1, h = a.children.length - +!!t.tail;
			for (let t = 0; t < h - 1; ++t) {
				if (t < 0) continue;
				let n = l[t];
				if (m) {
					n.hasAttribute(Wv) || n.setAttribute(Wv, "");
					continue;
				}
				n.hasAttribute(Wv) && n.removeAttribute(Wv);
				let r = n.offsetWidth;
				if (p += r, d[t] = r, p > u) {
					let { updateCounter: n } = e;
					for (let r = t; r >= 0; --r) {
						let i = h - 1 - r;
						n === void 0 ? c.textContent = `${i}` : n(i);
						let a = c.offsetWidth;
						if (p -= d[r], p + a <= u || r === 0) {
							m = !0, t = r - 1, f && (t === -1 ? (f.style.maxWidth = `${u - a}px`, f.style.boxSizing = "border-box") : f.style.maxWidth = "");
							let { onUpdateCount: n } = e;
							n && n(i);
							break;
						}
					}
				}
			}
			let { onUpdateOverflow: g } = e;
			m ? g !== void 0 && g(!0) : (g !== void 0 && g(!1), c.setAttribute(Wv, ""));
		}
		let a = Om();
		return Gv.mount({
			id: "vueuc/overflow",
			head: !0,
			anchorMetaName: b_,
			ssr: a
		}), Fr(() => i({ showAllItemsBeforeCalculate: !1 })), {
			selfRef: n,
			counterRef: r,
			sync: i
		};
	},
	render() {
		let { $slots: e } = this;
		return wn(() => this.sync({ showAllItemsBeforeCalculate: !1 })), Ea("div", {
			class: "v-overflow",
			ref: "selfRef"
		}, [
			Br(e, "default"),
			e.counter ? e.counter() : Ea("span", {
				style: { display: "inline-block" },
				ref: "counterRef"
			}),
			e.tail ? e.tail() : null
		]);
	}
});
//#endregion
//#region node_modules/vueuc/es/focus-trap/src/utils.js
function qv(e) {
	return e instanceof HTMLElement;
}
function Jv(e) {
	for (let t = 0; t < e.childNodes.length; t++) {
		let n = e.childNodes[t];
		if (qv(n) && (Xv(n) || Jv(n))) return !0;
	}
	return !1;
}
function Yv(e) {
	for (let t = e.childNodes.length - 1; t >= 0; t--) {
		let n = e.childNodes[t];
		if (qv(n) && (Xv(n) || Yv(n))) return !0;
	}
	return !1;
}
function Xv(e) {
	if (!Zv(e)) return !1;
	try {
		e.focus({ preventScroll: !0 });
	} catch {}
	return document.activeElement === e;
}
function Zv(e) {
	if (e.tabIndex > 0 || e.tabIndex === 0 && e.getAttribute("tabIndex") !== null) return !0;
	if (e.getAttribute("disabled")) return !1;
	switch (e.nodeName) {
		case "A": return !!e.href && e.rel !== "ignore";
		case "INPUT": return e.type !== "hidden" && e.type !== "file";
		case "SELECT":
		case "TEXTAREA": return !0;
		default: return !1;
	}
}
//#endregion
//#region node_modules/vueuc/es/focus-trap/src/index.js
var Qv = [], $v = /* @__PURE__ */ N({
	name: "FocusTrap",
	props: {
		disabled: Boolean,
		active: Boolean,
		autoFocus: {
			type: Boolean,
			default: !0
		},
		onEsc: Function,
		initialFocusTo: [String, Function],
		finalFocusTo: [String, Function],
		returnFocusOnDeactivated: {
			type: Boolean,
			default: !0
		}
	},
	setup(e) {
		let t = wh(), n = /* @__PURE__ */ j(null), r = /* @__PURE__ */ j(null), i = !1, a = !1, o = typeof document > "u" ? null : document.activeElement;
		function s() {
			return Qv[Qv.length - 1] === t;
		}
		function c(t) {
			var n;
			t.code === "Escape" && s() && ((n = e.onEsc) == null || n.call(e, t));
		}
		Fr(() => {
			Wn(() => e.active, (e) => {
				e ? (d(), yg("keydown", document, c)) : (bg("keydown", document, c), i && f());
			}, { immediate: !0 });
		}), Lr(() => {
			bg("keydown", document, c), i && f();
		});
		function l(e) {
			if (!a && s()) {
				let t = u();
				if (t === null || t.contains(Vm(e))) return;
				p("first");
			}
		}
		function u() {
			let e = n.value;
			if (e === null) return null;
			let t = e;
			for (; t = t.nextSibling, !(t === null || t instanceof Element && t.tagName === "DIV"););
			return t;
		}
		function d() {
			var n;
			if (!e.disabled) {
				if (Qv.push(t), e.autoFocus) {
					let { initialFocusTo: t } = e;
					t === void 0 ? p("first") : (n = C_(t)) == null || n.focus({ preventScroll: !0 });
				}
				i = !0, document.addEventListener("focus", l, !0);
			}
		}
		function f() {
			var n;
			if (e.disabled || (document.removeEventListener("focus", l, !0), Qv = Qv.filter((e) => e !== t), s())) return;
			let { finalFocusTo: r } = e;
			r === void 0 ? e.returnFocusOnDeactivated && o instanceof HTMLElement && (a = !0, o.focus({ preventScroll: !0 }), a = !1) : (n = C_(r)) == null || n.focus({ preventScroll: !0 });
		}
		function p(t) {
			if (s() && e.active) {
				let e = n.value, i = r.value;
				if (e !== null && i !== null) {
					let n = u();
					if (n == null || n === i) {
						a = !0, e.focus({ preventScroll: !0 }), a = !1;
						return;
					}
					a = !0;
					let r = t === "first" ? Jv(n) : Yv(n);
					a = !1, r || (a = !0, e.focus({ preventScroll: !0 }), a = !1);
				}
			}
		}
		function m(e) {
			if (a) return;
			let t = u();
			t !== null && (e.relatedTarget !== null && t.contains(e.relatedTarget) ? p("last") : p("first"));
		}
		function h(e) {
			a || (e.relatedTarget !== null && e.relatedTarget === n.value ? p("last") : p("first"));
		}
		return {
			focusableStartRef: n,
			focusableEndRef: r,
			focusableStyle: "position: absolute; height: 0; width: 0;",
			handleStartFocus: m,
			handleEndFocus: h
		};
	},
	render() {
		let { default: e } = this.$slots;
		if (e === void 0) return null;
		if (this.disabled) return e();
		let { active: t, focusableStyle: n } = this;
		return Ea(P, null, [
			Ea("div", {
				"aria-hidden": "true",
				tabindex: t ? "0" : "-1",
				ref: "focusableStartRef",
				style: n,
				onFocus: this.handleStartFocus
			}),
			e(),
			Ea("div", {
				"aria-hidden": "true",
				style: n,
				ref: "focusableEndRef",
				tabindex: t ? "0" : "-1",
				onFocus: this.handleEndFocus
			})
		]);
	}
}), ey = ["onMousedown"], ty = ["onScroll", "onWheel"], ny = ["onMousedown"], ry = /* @__PURE__ */ N({
	name: "Scrollbar",
	props: {
		...Kh.props,
		duration: {
			type: Number,
			default: 0
		},
		scrollable: {
			type: Boolean,
			default: !0
		},
		xScrollable: Boolean,
		trigger: {
			type: String,
			default: "hover"
		},
		useUnifiedContainer: Boolean,
		triggerDisplayManually: Boolean,
		container: Function,
		content: Function,
		containerClass: String,
		containerStyle: [String, Object],
		contentClass: [String, Array],
		contentStyle: [String, Object],
		horizontalRailStyle: [String, Object],
		verticalRailStyle: [String, Object],
		onScroll: Function,
		onWheel: Function,
		onResize: Function,
		internalOnUpdateScrollLeft: Function,
		internalHoistYRail: Boolean,
		internalExposeWidthCssVar: Boolean,
		yPlacement: {
			type: String,
			default: "right"
		},
		xPlacement: {
			type: String,
			default: "bottom"
		}
	},
	inheritAttrs: !1,
	setup(e) {
		let { mergedClsPrefixRef: t, inlineThemeDisabled: n, mergedRtlRef: r } = _m(e), i = Kg("Scrollbar", r, t), a = /* @__PURE__ */ j(null), o = /* @__PURE__ */ j(null), s = /* @__PURE__ */ j(null), c = /* @__PURE__ */ j(null), l = /* @__PURE__ */ j(null), u = /* @__PURE__ */ j(null), d = /* @__PURE__ */ j(null), f = /* @__PURE__ */ j(null), p = /* @__PURE__ */ j(null), m = /* @__PURE__ */ j(null), h = /* @__PURE__ */ j(null), g = /* @__PURE__ */ j(0), _ = /* @__PURE__ */ j(0), v = /* @__PURE__ */ j(!1), y = /* @__PURE__ */ j(!1), b = !1, x = !1, S, C, w = 0, T = 0, E = 0, D = 0, O = Ag(), ee = Kh("Scrollbar", "-scrollbar", Qg, Lh, e, t), te = z(() => {
			let { value: e } = f, { value: t } = u, { value: n } = m;
			return e === null || t === null || n === null ? 0 : Math.min(e, n * e / t + Hm(ee.value.self.width) * 1.5);
		}), ne = z(() => `${te.value}px`), re = z(() => {
			let { value: e } = p, { value: t } = d, { value: n } = h;
			return e === null || t === null || n === null ? 0 : n * e / t + Hm(ee.value.self.height) * 1.5;
		}), ie = z(() => `${re.value}px`), ae = z(() => {
			let { value: e } = f, { value: t } = g, { value: n } = u, { value: r } = m;
			if (e === null || n === null || r === null) return 0;
			{
				let i = n - e;
				return i ? t / i * (r - te.value) : 0;
			}
		}), oe = z(() => `${ae.value}px`), se = z(() => {
			let { value: e } = p, { value: t } = _, { value: n } = d, { value: r } = h;
			if (e === null || n === null || r === null) return 0;
			{
				let i = n - e;
				return i ? t / i * (r - re.value) : 0;
			}
		}), ce = z(() => `${se.value}px`), le = z(() => {
			let { value: e } = f, { value: t } = u;
			return e !== null && t !== null && t > e;
		}), ue = z(() => {
			let { value: e } = p, { value: t } = d;
			return e !== null && t !== null && t > e;
		}), k = z(() => {
			let { trigger: t } = e;
			return t === "none" || v.value;
		}), de = z(() => {
			let { trigger: t } = e;
			return t === "none" || y.value;
		}), fe = z(() => {
			let { container: t } = e;
			return t ? t() : o.value;
		}), pe = z(() => {
			let { content: t } = e;
			return t ? t() : s.value;
		}), me = (t, n) => {
			if (!e.scrollable) return;
			if (typeof t == "number") {
				ye(t, n ?? 0, 0, !1, "auto");
				return;
			}
			let { left: r, top: i, index: a, elSize: o, position: s, behavior: c, el: l, debounce: u = !0 } = t;
			(r !== void 0 || i !== void 0) && ye(r ?? 0, i ?? 0, 0, !1, c), l === void 0 ? a !== void 0 && o !== void 0 ? ye(0, a * o, o, u, c) : s === "bottom" ? ye(0, 2 ** 53 - 1, 0, !1, c) : s === "top" && ye(0, 0, 0, !1, c) : ye(0, l.offsetTop, l.offsetHeight, u, c);
		}, he = qg(() => {
			e.container || me({
				top: g.value,
				left: _.value
			});
		}), ge = () => {
			he.isDeactivated || je();
		}, _e = (t) => {
			if (he.isDeactivated) return;
			let { onResize: n } = e;
			n && n(t), je();
		}, ve = (t, n) => {
			if (!e.scrollable) return;
			let { value: r } = fe;
			r && (typeof t == "object" ? r.scrollBy(t) : r.scrollBy(t, n || 0));
		};
		function ye(e, t, n, r, i) {
			let { value: a } = fe;
			if (a) {
				if (r) {
					let { scrollTop: r, offsetHeight: o } = a;
					if (t > r) {
						t + n <= r + o || a.scrollTo({
							left: e,
							top: t + n - o,
							behavior: i
						});
						return;
					}
				}
				a.scrollTo({
					left: e,
					top: t,
					behavior: i
				});
			}
		}
		function be() {
			Te(), Ee(), je();
		}
		function xe() {
			Se();
		}
		function Se() {
			Ce(), we();
		}
		function Ce() {
			C !== void 0 && window.clearTimeout(C), C = window.setTimeout(() => {
				y.value = !1;
			}, e.duration);
		}
		function we() {
			S !== void 0 && window.clearTimeout(S), S = window.setTimeout(() => {
				v.value = !1;
			}, e.duration);
		}
		function Te() {
			S !== void 0 && window.clearTimeout(S), v.value = !0;
		}
		function Ee() {
			C !== void 0 && window.clearTimeout(C), y.value = !0;
		}
		function De(t) {
			let { onScroll: n } = e;
			n && n(t), Oe();
		}
		function Oe() {
			let { value: e } = fe;
			e && (g.value = e.scrollTop, _.value = e.scrollLeft * (i?.value ? -1 : 1));
		}
		function ke() {
			let { value: e } = pe;
			e && (u.value = e.offsetHeight, d.value = e.offsetWidth);
			let { value: t } = fe;
			t && (f.value = t.offsetHeight, p.value = t.offsetWidth);
			let { value: n } = l, { value: r } = c;
			n && (h.value = n.offsetWidth), r && (m.value = r.offsetHeight);
		}
		function Ae() {
			let { value: e } = fe;
			e && (g.value = e.scrollTop, _.value = e.scrollLeft * (i?.value ? -1 : 1), f.value = e.offsetHeight, p.value = e.offsetWidth, u.value = e.scrollHeight, d.value = e.scrollWidth);
			let { value: t } = l, { value: n } = c;
			t && (h.value = t.offsetWidth), n && (m.value = n.offsetHeight);
		}
		function je() {
			e.scrollable && (e.useUnifiedContainer ? Ae() : (ke(), Oe()));
		}
		function Me(e) {
			return !a.value?.contains(Vm(e));
		}
		function Ne(e) {
			e.preventDefault(), e.stopPropagation(), x = !0, yg("mousemove", window, Pe, !0), yg("mouseup", window, Fe, !0), T = _.value, E = i?.value ? window.innerWidth - e.clientX : e.clientX;
		}
		function Pe(t) {
			if (!x) return;
			S !== void 0 && window.clearTimeout(S), C !== void 0 && window.clearTimeout(C);
			let { value: n } = p, { value: r } = d, { value: a } = re;
			if (n === null || r === null) return;
			let o = (i?.value ? window.innerWidth - t.clientX - E : t.clientX - E) * (r - n) / (n - a), s = r - n, c = T + o;
			c = Math.min(s, c), c = Math.max(c, 0);
			let { value: l } = fe;
			if (l) {
				l.scrollLeft = c * (i?.value ? -1 : 1);
				let { internalOnUpdateScrollLeft: t } = e;
				t && t(c);
			}
		}
		function Fe(e) {
			e.preventDefault(), e.stopPropagation(), bg("mousemove", window, Pe, !0), bg("mouseup", window, Fe, !0), x = !1, je(), Me(e) && Se();
		}
		function Ie(e) {
			e.preventDefault(), e.stopPropagation(), b = !0, yg("mousemove", window, Le, !0), yg("mouseup", window, Re, !0), w = g.value, D = e.clientY;
		}
		function Le(e) {
			if (!b) return;
			S !== void 0 && window.clearTimeout(S), C !== void 0 && window.clearTimeout(C);
			let { value: t } = f, { value: n } = u, { value: r } = te;
			if (t === null || n === null) return;
			let i = (e.clientY - D) * (n - t) / (t - r), a = n - t, o = w + i;
			o = Math.min(a, o), o = Math.max(o, 0);
			let { value: s } = fe;
			s && (s.scrollTop = o);
		}
		function Re(e) {
			e.preventDefault(), e.stopPropagation(), bg("mousemove", window, Le, !0), bg("mouseup", window, Re, !0), b = !1, je(), Me(e) && Se();
		}
		Un(() => {
			let { value: e } = ue, { value: n } = le, { value: r } = t, { value: i } = l, { value: a } = c;
			i && (e ? i.classList.remove(`${r}-scrollbar-rail--disabled`) : i.classList.add(`${r}-scrollbar-rail--disabled`)), a && (n ? a.classList.remove(`${r}-scrollbar-rail--disabled`) : a.classList.add(`${r}-scrollbar-rail--disabled`));
		}), Fr(() => {
			e.container || je();
		}), Lr(() => {
			S !== void 0 && window.clearTimeout(S), C !== void 0 && window.clearTimeout(C), bg("mousemove", window, Le, !0), bg("mouseup", window, Re, !0);
		});
		let ze = z(() => {
			let { common: { cubicBezierEaseInOut: e }, self: { color: t, colorHover: n, height: r, width: a, borderRadius: o, railInsetHorizontalTop: s, railInsetHorizontalBottom: c, railInsetVerticalRight: l, railInsetVerticalLeft: u, railColor: d } } = ee.value, { top: f, right: p, bottom: m, left: h } = Wm(s), { top: g, right: _, bottom: v, left: y } = Wm(c), { top: b, right: x, bottom: S, left: C } = Wm(i?.value ? Jg(l) : l), { top: w, right: T, bottom: E, left: D } = Wm(i?.value ? Jg(u) : u);
			return {
				"--n-scrollbar-bezier": e,
				"--n-scrollbar-color": t,
				"--n-scrollbar-color-hover": n,
				"--n-scrollbar-border-radius": o,
				"--n-scrollbar-width": a,
				"--n-scrollbar-height": r,
				"--n-scrollbar-rail-top-horizontal-top": f,
				"--n-scrollbar-rail-right-horizontal-top": p,
				"--n-scrollbar-rail-bottom-horizontal-top": m,
				"--n-scrollbar-rail-left-horizontal-top": h,
				"--n-scrollbar-rail-top-horizontal-bottom": g,
				"--n-scrollbar-rail-right-horizontal-bottom": _,
				"--n-scrollbar-rail-bottom-horizontal-bottom": v,
				"--n-scrollbar-rail-left-horizontal-bottom": y,
				"--n-scrollbar-rail-top-vertical-right": b,
				"--n-scrollbar-rail-right-vertical-right": x,
				"--n-scrollbar-rail-bottom-vertical-right": S,
				"--n-scrollbar-rail-left-vertical-right": C,
				"--n-scrollbar-rail-top-vertical-left": w,
				"--n-scrollbar-rail-right-vertical-left": T,
				"--n-scrollbar-rail-bottom-vertical-left": E,
				"--n-scrollbar-rail-left-vertical-left": D,
				"--n-scrollbar-rail-color": d
			};
		}), Be = n ? Uh("scrollbar", void 0, ze, e) : void 0;
		return {
			scrollTo: me,
			scrollBy: ve,
			sync: je,
			syncUnifiedContainer: Ae,
			handleMouseEnterWrapper: be,
			handleMouseLeaveWrapper: xe,
			mergedClsPrefix: t,
			rtlEnabled: i,
			containerScrollTop: g,
			wrapperRef: a,
			containerRef: o,
			contentRef: s,
			yRailRef: c,
			xRailRef: l,
			needYBar: le,
			needXBar: ue,
			yBarSizePx: ne,
			xBarSizePx: ie,
			yBarTopPx: oe,
			xBarLeftPx: ce,
			isShowXBar: k,
			isShowYBar: de,
			isIos: O,
			handleScroll: De,
			handleContentResize: ge,
			handleContainerResize: _e,
			handleYScrollMouseDown: Ie,
			handleXScrollMouseDown: Ne,
			containerWidth: p,
			cssVars: n ? void 0 : ze,
			themeClass: Be?.themeClass,
			onRender: Be?.onRender
		};
	},
	render() {
		let { $slots: e, mergedClsPrefix: t, triggerDisplayManually: n, rtlEnabled: r, internalHoistYRail: i, yPlacement: a, xPlacement: o, xScrollable: s } = this;
		if (!this.scrollable) return e.default?.();
		let c = this.trigger === "none", l = (e, n) => (F(), I("div", {
			ref: "yRailRef",
			class: K([
				`${t}-scrollbar-rail`,
				`${t}-scrollbar-rail--vertical`,
				`${t}-scrollbar-rail--vertical--${a}`,
				e
			]),
			"data-scrollbar-rail": !0,
			style: k([n || "", this.verticalRailStyle]),
			"aria-hidden": !0
		}, [G(() => Ea(c ? Yg : Va, c ? null : { name: "fade-in-transition" }, { default: () => this.needYBar && this.isShowYBar && !this.isIos ? (F(), I("div", {
			key: 1,
			class: K(`${t}-scrollbar-rail__scrollbar`),
			style: k({
				height: this.yBarSizePx,
				top: this.yBarTopPx
			}),
			onMousedown: this.handleYScrollMouseDown
		}, null, 46, ey)) : null }))], 6)), u = () => (this.onRender?.(), Ea("div", aa(this.$attrs, {
			role: "none",
			ref: "wrapperRef",
			class: [
				`${t}-scrollbar`,
				this.themeClass,
				r && `${t}-scrollbar--rtl`
			],
			style: this.cssVars,
			onMouseenter: n ? void 0 : this.handleMouseEnterWrapper,
			onMouseleave: n ? void 0 : this.handleMouseLeaveWrapper
		}), [
			this.container ? e.default?.() : (F(), I("div", {
				key: 2,
				role: "none",
				ref: "containerRef",
				class: K([`${t}-scrollbar-container`, this.containerClass]),
				style: k([this.containerStyle, this.internalExposeWidthCssVar ? { "--n-scrollbar-current-width": Um(this.containerWidth) } : void 0]),
				onScroll: this.handleScroll,
				onWheel: this.onWheel
			}, [(F(), L(Pv, { onResize: this.handleContentResize }, { default: () => (F(), I("div", {
				ref: "contentRef",
				role: "none",
				style: k([{ width: this.xScrollable ? "fit-content" : null }, this.contentStyle]),
				class: K([`${t}-scrollbar-content`, this.contentClass])
			}, [G(() => e.default?.())], 6)) }, 1032, ["onResize"]))], 46, ty)),
			i ? null : l(void 0, void 0),
			s && (F(), I("div", {
				ref: "xRailRef",
				class: K([
					`${t}-scrollbar-rail`,
					`${t}-scrollbar-rail--horizontal`,
					`${t}-scrollbar-rail--horizontal--${o}`
				]),
				style: k(this.horizontalRailStyle),
				"data-scrollbar-rail": !0,
				"aria-hidden": !0
			}, [G(() => Ea(c ? Yg : Va, c ? null : { name: "fade-in-transition" }, { default: () => this.needXBar && this.isShowXBar && !this.isIos ? (F(), I("div", {
				key: 3,
				class: K(`${t}-scrollbar-rail__scrollbar`),
				style: k({
					width: this.xBarSizePx,
					right: r ? this.xBarLeftPx : void 0,
					left: r ? void 0 : this.xBarLeftPx
				}),
				onMousedown: this.handleXScrollMouseDown
			}, null, 46, ny)) : null }))], 6))
		])), d = this.container ? u() : (F(), L(Pv, {
			key: 4,
			onResize: this.handleContainerResize
		}, { default: u }, 1032, ["onResize"]));
		return i ? (F(), I(P, { key: 5 }, [G(() => d), G(() => l(this.themeClass, this.cssVars))], 64)) : d;
	}
}), iy = ry, ay = {
	top: "bottom",
	bottom: "top",
	left: "right",
	right: "left"
}, oy = "var(--n-arrow-height) * 1.414", sy = B([
	V("popover", "\n transition:\n box-shadow .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier);\n position: relative;\n font-size: var(--n-font-size);\n color: var(--n-text-color);\n box-shadow: var(--n-box-shadow);\n word-break: break-word;\n ", [
		B(">", [V("scrollbar", "\n height: inherit;\n max-height: inherit;\n ")]),
		Ns("raw", "\n background-color: var(--n-color);\n border-radius: var(--n-border-radius);\n ", [Ns("scrollable", [Ns("show-header-or-footer", "padding: var(--n-padding);")])]),
		H("header", "\n padding: var(--n-padding);\n border-bottom: 1px solid var(--n-divider-color);\n transition: border-color .3s var(--n-bezier);\n "),
		H("footer", "\n padding: var(--n-padding);\n border-top: 1px solid var(--n-divider-color);\n transition: border-color .3s var(--n-bezier);\n "),
		U("scrollable, show-header-or-footer", [H("content", "\n padding: var(--n-padding);\n ")])
	]),
	V("popover-shared", "\n transform-origin: inherit;\n ", [
		V("popover-arrow-wrapper", "\n position: absolute;\n overflow: hidden;\n pointer-events: none;\n ", [V("popover-arrow", `
 transition: background-color .3s var(--n-bezier);
 position: absolute;
 display: block;
 width: calc(${oy});
 height: calc(${oy});
 box-shadow: 0 0 8px 0 rgba(0, 0, 0, .12);
 transform: rotate(45deg);
 background-color: var(--n-color);
 pointer-events: all;
 `)]),
		B("&.popover-transition-enter-from, &.popover-transition-leave-to", "\n opacity: 0;\n transform: scale(.85);\n "),
		B("&.popover-transition-enter-to, &.popover-transition-leave-from", "\n transform: scale(1);\n opacity: 1;\n "),
		B("&.popover-transition-enter-active", "\n transition:\n box-shadow .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier),\n opacity .15s var(--n-bezier-ease-out),\n transform .15s var(--n-bezier-ease-out);\n "),
		B("&.popover-transition-leave-active", "\n transition:\n box-shadow .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier),\n opacity .15s var(--n-bezier-ease-in),\n transform .15s var(--n-bezier-ease-in);\n ")
	]),
	ly("top-start", `
 top: calc(${oy} / -2);
 left: calc(${cy("top-start")} - var(--v-offset-left));
 `),
	ly("top", `
 top: calc(${oy} / -2);
 transform: translateX(calc(${oy} / -2)) rotate(45deg);
 left: 50%;
 `),
	ly("top-end", `
 top: calc(${oy} / -2);
 right: calc(${cy("top-end")} + var(--v-offset-left));
 `),
	ly("bottom-start", `
 bottom: calc(${oy} / -2);
 left: calc(${cy("bottom-start")} - var(--v-offset-left));
 `),
	ly("bottom", `
 bottom: calc(${oy} / -2);
 transform: translateX(calc(${oy} / -2)) rotate(45deg);
 left: 50%;
 `),
	ly("bottom-end", `
 bottom: calc(${oy} / -2);
 right: calc(${cy("bottom-end")} + var(--v-offset-left));
 `),
	ly("left-start", `
 left: calc(${oy} / -2);
 top: calc(${cy("left-start")} - var(--v-offset-top));
 `),
	ly("left", `
 left: calc(${oy} / -2);
 transform: translateY(calc(${oy} / -2)) rotate(45deg);
 top: 50%;
 `),
	ly("left-end", `
 left: calc(${oy} / -2);
 bottom: calc(${cy("left-end")} + var(--v-offset-top));
 `),
	ly("right-start", `
 right: calc(${oy} / -2);
 top: calc(${cy("right-start")} - var(--v-offset-top));
 `),
	ly("right", `
 right: calc(${oy} / -2);
 transform: translateY(calc(${oy} / -2)) rotate(45deg);
 top: 50%;
 `),
	ly("right-end", `
 right: calc(${oy} / -2);
 bottom: calc(${cy("right-end")} + var(--v-offset-top));
 `),
	...um({
		top: ["right-start", "left-start"],
		right: ["top-end", "bottom-end"],
		bottom: ["right-end", "left-end"],
		left: ["top-start", "bottom-start"]
	}, (e, t) => {
		let n = ["right", "left"].includes(t), r = n ? "width" : "height";
		return e.map((e) => {
			let i = e.split("-")[1] === "end", a = `calc((${`var(--v-target-${r}, 0px)`} - ${oy}) / 2)`, o = cy(e);
			return B(`[v-placement="${e}"] >`, [V("popover-shared", [U("center-arrow", [V("popover-arrow", `${t}: calc(max(${a}, ${o}) ${i ? "+" : "-"} var(--v-offset-${n ? "left" : "top"}));`)])])]);
		});
	})
]);
function cy(e) {
	return ["top", "bottom"].includes(e.split("-")[0]) ? "var(--n-arrow-offset)" : "var(--n-arrow-offset-vertical)";
}
function ly(e, t) {
	let n = e.split("-")[0], r = ["top", "bottom"].includes(n) ? "height: var(--n-space-arrow);" : "width: var(--n-space-arrow);";
	return B(`[v-placement="${e}"] >`, [V("popover-shared", `
 margin-${ay[n]}: var(--n-space);
 `, [
		U("show-arrow", `
 margin-${ay[n]}: var(--n-space-arrow);
 `),
		U("overlap", "\n margin: 0;\n "),
		Ls("popover-arrow-wrapper", `
 right: 0;
 left: 0;
 top: 0;
 bottom: 0;
 ${n}: 100%;
 ${ay[n]}: auto;
 ${r}
 `, [V("popover-arrow", t)])
	])]);
}
//#endregion
//#region node_modules/naive-ui/es/popover/src/PopoverBody.mjs
var uy = {
	...Kh.props,
	to: Mg.propTo,
	show: Boolean,
	trigger: String,
	showArrow: Boolean,
	delay: Number,
	duration: Number,
	raw: Boolean,
	arrowPointToCenter: Boolean,
	arrowClass: String,
	arrowStyle: [String, Object],
	arrowWrapperClass: String,
	arrowWrapperStyle: [String, Object],
	displayDirective: String,
	x: Number,
	y: Number,
	flip: Boolean,
	overlap: Boolean,
	placement: String,
	width: [Number, String],
	keepAliveOnHover: Boolean,
	scrollable: Boolean,
	contentClass: String,
	contentStyle: [Object, String],
	headerClass: String,
	headerStyle: [Object, String],
	footerClass: String,
	footerStyle: [Object, String],
	internalDeactivateImmediately: Boolean,
	animated: Boolean,
	onClickoutside: Function,
	internalTrapFocus: Boolean,
	internalOnAfterLeave: Function,
	minWidth: Number,
	maxWidth: Number
};
function dy({ arrowClass: e, arrowStyle: t, arrowWrapperClass: n, arrowWrapperStyle: r, clsPrefix: i }) {
	return F(), I("div", {
		key: "__popover-arrow__",
		style: k(r),
		class: K([`${i}-popover-arrow-wrapper`, n])
	}, [R("div", {
		class: K([`${i}-popover-arrow`, e]),
		style: k(t)
	}, null, 6)], 6);
}
var fy = /* @__PURE__ */ N({
	name: "PopoverBody",
	inheritAttrs: !1,
	props: uy,
	setup(e, { slots: t, attrs: n }) {
		let { namespaceRef: r, mergedClsPrefixRef: i, inlineThemeDisabled: a, mergedRtlRef: o } = _m(e), s = Kh("Popover", "-popover", sy, ag, e, i), c = Kg("Popover", o, i), l = /* @__PURE__ */ j(null), u = Bn("NPopover"), d = /* @__PURE__ */ j(null), f = /* @__PURE__ */ j(e.show), p = /* @__PURE__ */ j(!1);
		Un(() => {
			let { show: t } = e;
			t && !Bg() && !e.internalDeactivateImmediately && (p.value = !0);
		});
		let m = z(() => {
			let { trigger: t, onClickoutside: n } = e, r = [], { positionManuallyRef: { value: i } } = u;
			return i || (t === "click" && !n && r.push([
				p_,
				S,
				void 0,
				{ capture: !0 }
			]), t === "hover" && r.push([d_, x])), n && r.push([
				p_,
				S,
				void 0,
				{ capture: !0 }
			]), (e.displayDirective === "show" || e.animated && p.value) && r.push([ao, e.show]), r;
		}), h = z(() => {
			let { common: { cubicBezierEaseInOut: e, cubicBezierEaseIn: t, cubicBezierEaseOut: n }, self: { space: r, spaceArrow: i, padding: a, fontSize: o, textColor: c, dividerColor: l, color: u, boxShadow: d, borderRadius: f, arrowHeight: p, arrowOffset: m, arrowOffsetVertical: h } } = s.value;
			return {
				"--n-box-shadow": d,
				"--n-bezier": e,
				"--n-bezier-ease-in": t,
				"--n-bezier-ease-out": n,
				"--n-font-size": o,
				"--n-text-color": c,
				"--n-color": u,
				"--n-divider-color": l,
				"--n-border-radius": f,
				"--n-arrow-height": p,
				"--n-arrow-offset": m,
				"--n-arrow-offset-vertical": h,
				"--n-padding": a,
				"--n-space": r,
				"--n-space-arrow": i
			};
		}), g = z(() => {
			let t = e.width === "trigger" ? void 0 : Rg(e.width), n = [];
			t && n.push({ width: t });
			let { maxWidth: r, minWidth: i } = e;
			return r && n.push({ maxWidth: Rg(r) }), i && n.push({ maxWidth: Rg(i) }), a || n.push(h.value), n;
		}), _ = a ? Uh("popover", void 0, h, e) : void 0;
		u.setBodyInstance({ syncPosition: v }), Lr(() => {
			u.setBodyInstance(null);
		}), Wn(/* @__PURE__ */ M(e, "show"), (t) => {
			e.animated || (t ? f.value = !0 : f.value = !1);
		});
		function v() {
			l.value?.syncPosition();
		}
		function y(t) {
			e.trigger === "hover" && e.keepAliveOnHover && e.show && u.handleMouseEnter(t);
		}
		function b(t) {
			e.trigger === "hover" && e.keepAliveOnHover && u.handleMouseLeave(t);
		}
		function x(t) {
			e.trigger === "hover" && !C().contains(Vm(t)) && u.handleMouseMoveOutside(t);
		}
		function S(t) {
			(e.trigger === "click" && !C().contains(Vm(t)) || e.onClickoutside) && u.handleClickOutside(t);
		}
		function C() {
			return u.getTriggerElement();
		}
		zn(dg, d), zn(lg, null), zn(ug, null);
		function w() {
			if (_?.onRender(), !(e.displayDirective === "show" || e.show || e.animated && p.value)) return null;
			let r, a = u.internalRenderBodyRef.value, { value: o } = i;
			if (a) r = a([
				`${o}-popover-shared`,
				c?.value && `${o}-popover--rtl`,
				_?.themeClass.value,
				e.overlap && `${o}-popover-shared--overlap`,
				e.showArrow && `${o}-popover-shared--show-arrow`,
				e.arrowPointToCenter && `${o}-popover-shared--center-arrow`
			], d, g.value, y, b);
			else {
				let { value: i } = u.extraClassRef, { internalTrapFocus: a } = e, l = !Gg(t.header) || !Gg(t.footer), f = () => {
					let n = l ? (F(), I(P, { key: 1 }, [
						G(() => Wg(t.header, (t) => t ? (F(), I("div", {
							key: 2,
							class: K([`${o}-popover__header`, e.headerClass]),
							style: k(e.headerStyle)
						}, [G(() => t)], 6)) : null)),
						G(() => Wg(t.default, (n) => n ? (F(), I("div", {
							key: 3,
							class: K([`${o}-popover__content`, e.contentClass]),
							style: k(e.contentStyle)
						}, [G(() => t.default?.())], 6)) : null)),
						G(() => Wg(t.footer, (t) => t ? (F(), I("div", {
							key: 4,
							class: K([`${o}-popover__footer`, e.footerClass]),
							style: k(e.footerStyle)
						}, [G(() => t)], 6)) : null))
					], 64)) : e.scrollable ? t.default?.() : (F(), I("div", {
						key: 5,
						class: K([`${o}-popover__content`, e.contentClass]),
						style: k(e.contentStyle)
					}, [G(() => t.default?.())], 6));
					return [e.scrollable ? (F(), L(iy, {
						key: 6,
						themeOverrides: s.value.peerOverrides.Scrollbar,
						theme: s.value.peers.Scrollbar,
						contentClass: l ? void 0 : `${o}-popover__content ${e.contentClass ?? ""}`,
						contentStyle: l ? void 0 : e.contentStyle
					}, { default: () => n }, 1032, [
						"themeOverrides",
						"theme",
						"contentClass",
						"contentStyle"
					])) : n, e.showArrow ? dy({
						arrowClass: e.arrowClass,
						arrowStyle: e.arrowStyle,
						arrowWrapperClass: e.arrowWrapperClass,
						arrowWrapperStyle: e.arrowWrapperStyle,
						clsPrefix: o
					}) : null];
				};
				r = Ea("div", aa({
					class: [
						`${o}-popover`,
						`${o}-popover-shared`,
						c?.value && `${o}-popover--rtl`,
						_?.themeClass.value,
						i.map((e) => `${o}-${e}`),
						{
							[`${o}-popover--scrollable`]: e.scrollable,
							[`${o}-popover--show-header-or-footer`]: l,
							[`${o}-popover--raw`]: e.raw,
							[`${o}-popover-shared--overlap`]: e.overlap,
							[`${o}-popover-shared--show-arrow`]: e.showArrow,
							[`${o}-popover-shared--center-arrow`]: e.arrowPointToCenter
						}
					],
					ref: d,
					style: g.value,
					onKeydown: u.handleKeydown,
					onMouseenter: y,
					onMouseleave: b
				}, n), a ? (F(), L($v, {
					key: 7,
					active: e.show,
					autoFocus: !0
				}, { default: f }, 1032, ["active"])) : f());
			}
			return Ln(r, m.value);
		}
		return {
			displayed: p,
			namespace: r,
			isMounted: u.isMountedRef,
			zIndex: u.zIndexRef,
			followerRef: l,
			adjustedTo: Mg(e),
			followerEnabled: f,
			renderContentNode: w
		};
	},
	render() {
		return F(), L(L_, {
			ref: "followerRef",
			zIndex: this.zIndex,
			show: this.show,
			enabled: this.followerEnabled,
			to: this.adjustedTo,
			x: this.x,
			y: this.y,
			flip: this.flip,
			placement: this.placement,
			containerClass: this.namespace,
			overlap: this.overlap,
			width: this.width === "trigger" ? "target" : void 0,
			teleportDisabled: this.adjustedTo === Mg.tdkey
		}, {
			_: 1,
			default: Pm(() => this.animated ? (F(), L(Va, {
				key: 8,
				name: "popover-transition",
				appear: this.isMounted,
				onEnter: () => {
					this.followerEnabled = !0;
				},
				onAfterLeave: () => {
					this.internalOnAfterLeave?.(), this.followerEnabled = !1, this.displayed = !1;
				}
			}, { default: this.renderContentNode }, 1032, [
				"appear",
				"onEnter",
				"onAfterLeave"
			])) : this.renderContentNode())
		}, 8, [
			"zIndex",
			"show",
			"enabled",
			"to",
			"x",
			"y",
			"flip",
			"placement",
			"containerClass",
			"overlap",
			"width",
			"teleportDisabled"
		]);
	}
}), py = {
	key: 1,
	style: {
		position: "fixed",
		top: 0,
		right: 0,
		bottom: 0,
		left: 0
	}
}, my = Object.keys(uy), hy = {
	focus: ["onFocus", "onBlur"],
	click: ["onClick"],
	hover: ["onMouseenter", "onMouseleave"],
	manual: [],
	nested: [
		"onFocus",
		"onBlur",
		"onMouseenter",
		"onMouseleave",
		"onClick"
	]
};
function gy(e, t, n) {
	hy[t].forEach((t) => {
		e.props = e.props ? Object.assign({}, e.props) : {};
		let r = e.props[t], i = n[t];
		r ? e.props[t] = (...e) => {
			r(...e), i(...e);
		} : e.props[t] = i;
	});
}
var _y = {
	show: {
		type: Boolean,
		default: void 0
	},
	defaultShow: Boolean,
	showArrow: {
		type: Boolean,
		default: !0
	},
	trigger: {
		type: String,
		default: "hover"
	},
	delay: {
		type: Number,
		default: 100
	},
	duration: {
		type: Number,
		default: 100
	},
	raw: Boolean,
	placement: {
		type: String,
		default: "top"
	},
	x: Number,
	y: Number,
	arrowPointToCenter: Boolean,
	disabled: Boolean,
	getDisabled: Function,
	displayDirective: {
		type: String,
		default: "if"
	},
	arrowClass: String,
	arrowStyle: [String, Object],
	arrowWrapperClass: String,
	arrowWrapperStyle: [String, Object],
	flip: {
		type: Boolean,
		default: !0
	},
	animated: {
		type: Boolean,
		default: !0
	},
	width: {
		type: [Number, String],
		default: void 0
	},
	overlap: Boolean,
	keepAliveOnHover: {
		type: Boolean,
		default: !0
	},
	zIndex: Number,
	to: Mg.propTo,
	scrollable: Boolean,
	contentClass: String,
	contentStyle: [Object, String],
	headerClass: String,
	headerStyle: [Object, String],
	footerClass: String,
	footerStyle: [Object, String],
	onClickoutside: Function,
	"onUpdate:show": [Function, Array],
	onUpdateShow: [Function, Array],
	internalDeactivateImmediately: Boolean,
	internalSyncTargetWithParent: Boolean,
	internalInheritedEventHandlers: {
		type: Array,
		default: () => []
	},
	internalTrapFocus: Boolean,
	internalExtraClass: {
		type: Array,
		default: () => []
	},
	onShow: [Function, Array],
	onHide: [Function, Array],
	arrow: {
		type: Boolean,
		default: void 0
	},
	minWidth: Number,
	maxWidth: Number
}, vy = /* @__PURE__ */ N({
	name: "Popover",
	inheritAttrs: !1,
	props: {
		...Kh.props,
		..._y,
		internalOnAfterLeave: Function,
		internalRenderBody: Function
	},
	slots: Object,
	__popover__: !0,
	setup(e) {
		let t = Dg(), n = /* @__PURE__ */ j(null), r = z(() => e.show), i = /* @__PURE__ */ j(e.defaultShow), a = Eg(r, i), o = Sg(() => !e.disabled && a.value), s = () => {
			if (e.disabled) return !0;
			let { getDisabled: t } = e;
			return !!t?.();
		}, c = () => !s() && a.value, l = Og(e, ["arrow", "showArrow"]), u = z(() => !e.overlap && l.value), d = null, f = /* @__PURE__ */ j(null), p = /* @__PURE__ */ j(null), m = Sg(() => e.x !== void 0 && e.y !== void 0);
		function h(t) {
			let { "onUpdate:show": n, onUpdateShow: r, onShow: a, onHide: o } = e;
			i.value = t, n && $(n, t), r && $(r, t), t && a && $(a, !0), t && o && $(o, !1);
		}
		function g() {
			d && d.syncPosition();
		}
		function _() {
			let { value: e } = f;
			e && (window.clearTimeout(e), f.value = null);
		}
		function v() {
			let { value: e } = p;
			e && (window.clearTimeout(e), p.value = null);
		}
		function y() {
			let t = s();
			if (e.trigger === "focus" && !t) {
				if (c()) return;
				h(!0);
			}
		}
		function b() {
			let t = s();
			if (e.trigger === "focus" && !t) {
				if (!c()) return;
				h(!1);
			}
		}
		function x() {
			let t = s();
			if (e.trigger === "hover" && !t) {
				if (v(), f.value !== null || c()) return;
				let t = () => {
					h(!0), f.value = null;
				}, { delay: n } = e;
				n === 0 ? t() : f.value = window.setTimeout(t, n);
			}
		}
		function S() {
			let t = s();
			if (e.trigger === "hover" && !t) {
				if (_(), p.value !== null || !c()) return;
				let t = () => {
					h(!1), p.value = null;
				}, { duration: n } = e;
				n === 0 ? t() : p.value = window.setTimeout(t, n);
			}
		}
		function C() {
			S();
		}
		function w(t) {
			c() && (e.trigger === "click" && (_(), v(), h(!1)), e.onClickoutside?.(t));
		}
		function T() {
			e.trigger === "click" && !s() && (_(), v(), h(!c()));
		}
		function E(t) {
			e.internalTrapFocus && t.key === "Escape" && (_(), v(), h(!1));
		}
		function D(e) {
			i.value = e;
		}
		function O() {
			return n.value?.targetRef;
		}
		function ee(e) {
			d = e;
		}
		return zn("NPopover", {
			getTriggerElement: O,
			handleKeydown: E,
			handleMouseEnter: x,
			handleMouseLeave: S,
			handleClickOutside: w,
			handleMouseMoveOutside: C,
			setBodyInstance: ee,
			positionManuallyRef: m,
			isMountedRef: t,
			zIndexRef: /* @__PURE__ */ M(e, "zIndex"),
			extraClassRef: /* @__PURE__ */ M(e, "internalExtraClass"),
			internalRenderBodyRef: /* @__PURE__ */ M(e, "internalRenderBody")
		}), Un(() => {
			a.value && s() && h(!1);
		}), {
			binderInstRef: n,
			positionManually: m,
			mergedShowConsideringDisabledProp: o,
			uncontrolledShow: i,
			mergedShowArrow: u,
			getMergedShow: c,
			setShow: D,
			handleClick: T,
			handleMouseEnter: x,
			handleMouseLeave: S,
			handleFocus: y,
			handleBlur: b,
			syncPosition: g
		};
	},
	render() {
		let { positionManually: e, $slots: t } = this, n, r = !1;
		if (!e && (n = Pg(t, "trigger"), n)) {
			n = $i(n), n = n.type === Li ? Ea("span", [n]) : n;
			let t = {
				onClick: this.handleClick,
				onMouseenter: this.handleMouseEnter,
				onMouseleave: this.handleMouseLeave,
				onFocus: this.handleFocus,
				onBlur: this.handleBlur
			};
			if (n.type?.__popover__) r = !0, n.props || (n.props = {
				internalSyncTargetWithParent: !0,
				internalInheritedEventHandlers: []
			}), n.props.internalSyncTargetWithParent = !0, n.props.internalInheritedEventHandlers ? n.props.internalInheritedEventHandlers = [t, ...n.props.internalInheritedEventHandlers] : n.props.internalInheritedEventHandlers = [t];
			else {
				let { internalInheritedEventHandlers: r } = this, i = [t, ...r];
				gy(n, r ? "nested" : e ? "manual" : this.trigger, {
					onBlur: (e) => {
						i.forEach((t) => {
							t.onBlur(e);
						});
					},
					onFocus: (e) => {
						i.forEach((t) => {
							t.onFocus(e);
						});
					},
					onClick: (e) => {
						i.forEach((t) => {
							t.onClick(e);
						});
					},
					onMouseenter: (e) => {
						i.forEach((t) => {
							t.onMouseenter(e);
						});
					},
					onMouseleave: (e) => {
						i.forEach((t) => {
							t.onMouseleave(e);
						});
					}
				});
			}
		}
		return F(), L(c_, {
			ref: "binderInstRef",
			syncTarget: !r,
			syncTargetWithParent: this.internalSyncTargetWithParent
		}, { default: () => {
			this.mergedShowConsideringDisabledProp;
			let t = this.getMergedShow();
			return [
				this.internalTrapFocus && t ? Ln((F(), I("div", py)), [[__, {
					enabled: t,
					zIndex: this.zIndex
				}]]) : null,
				e ? null : Ea(l_, null, { default: () => n }),
				Ea(fy, Fg(this.$props, my, {
					...this.$attrs,
					showArrow: this.mergedShowArrow,
					show: t
				}), {
					default: () => this.$slots.default?.(),
					header: () => this.$slots.header?.(),
					footer: () => this.$slots.footer?.()
				})
			];
		} }, 1032, ["syncTarget", "syncTargetWithParent"]);
	}
}), yy = {
	closeIconSizeTiny: "12px",
	closeIconSizeSmall: "12px",
	closeIconSizeMedium: "14px",
	closeIconSizeLarge: "14px",
	closeSizeTiny: "16px",
	closeSizeSmall: "16px",
	closeSizeMedium: "18px",
	closeSizeLarge: "18px",
	padding: "0 7px",
	closeMargin: "0 0 0 4px"
}, by = {
	name: "Tag",
	common: Z,
	self(e) {
		let { textColor2: t, primaryColorHover: n, primaryColorPressed: r, primaryColor: i, infoColor: a, successColor: o, warningColor: s, errorColor: c, baseColor: l, borderColor: u, tagColor: d, opacityDisabled: f, closeIconColor: p, closeIconColorHover: m, closeIconColorPressed: h, closeColorHover: g, closeColorPressed: _, borderRadiusSmall: v, fontSizeMini: y, fontSizeTiny: b, fontSizeSmall: x, fontSizeMedium: S, heightMini: C, heightTiny: w, heightSmall: T, heightMedium: E, buttonColor2Hover: D, buttonColor2Pressed: O, fontWeightStrong: ee } = e;
		return {
			...yy,
			closeBorderRadius: v,
			heightTiny: C,
			heightSmall: w,
			heightMedium: T,
			heightLarge: E,
			borderRadius: v,
			opacityDisabled: f,
			fontSizeTiny: y,
			fontSizeSmall: b,
			fontSizeMedium: x,
			fontSizeLarge: S,
			fontWeightStrong: ee,
			textColorCheckable: t,
			textColorHoverCheckable: t,
			textColorPressedCheckable: t,
			textColorChecked: l,
			colorCheckable: "#0000",
			colorHoverCheckable: D,
			colorPressedCheckable: O,
			colorChecked: i,
			colorCheckedHover: n,
			colorCheckedPressed: r,
			border: `1px solid ${u}`,
			textColor: t,
			color: d,
			colorBordered: "#0000",
			closeIconColor: p,
			closeIconColorHover: m,
			closeIconColorPressed: h,
			closeColorHover: g,
			closeColorPressed: _,
			borderPrimary: `1px solid ${J(i, { alpha: .3 })}`,
			textColorPrimary: i,
			colorPrimary: J(i, { alpha: .16 }),
			colorBorderedPrimary: "#0000",
			closeIconColorPrimary: vh(i, { lightness: .7 }),
			closeIconColorHoverPrimary: vh(i, { lightness: .7 }),
			closeIconColorPressedPrimary: vh(i, { lightness: .7 }),
			closeColorHoverPrimary: J(i, { alpha: .16 }),
			closeColorPressedPrimary: J(i, { alpha: .12 }),
			borderInfo: `1px solid ${J(a, { alpha: .3 })}`,
			textColorInfo: a,
			colorInfo: J(a, { alpha: .16 }),
			colorBorderedInfo: "#0000",
			closeIconColorInfo: vh(a, { alpha: .7 }),
			closeIconColorHoverInfo: vh(a, { alpha: .7 }),
			closeIconColorPressedInfo: vh(a, { alpha: .7 }),
			closeColorHoverInfo: J(a, { alpha: .16 }),
			closeColorPressedInfo: J(a, { alpha: .12 }),
			borderSuccess: `1px solid ${J(o, { alpha: .3 })}`,
			textColorSuccess: o,
			colorSuccess: J(o, { alpha: .16 }),
			colorBorderedSuccess: "#0000",
			closeIconColorSuccess: vh(o, { alpha: .7 }),
			closeIconColorHoverSuccess: vh(o, { alpha: .7 }),
			closeIconColorPressedSuccess: vh(o, { alpha: .7 }),
			closeColorHoverSuccess: J(o, { alpha: .16 }),
			closeColorPressedSuccess: J(o, { alpha: .12 }),
			borderWarning: `1px solid ${J(s, { alpha: .3 })}`,
			textColorWarning: s,
			colorWarning: J(s, { alpha: .16 }),
			colorBorderedWarning: "#0000",
			closeIconColorWarning: vh(s, { alpha: .7 }),
			closeIconColorHoverWarning: vh(s, { alpha: .7 }),
			closeIconColorPressedWarning: vh(s, { alpha: .7 }),
			closeColorHoverWarning: J(s, { alpha: .16 }),
			closeColorPressedWarning: J(s, { alpha: .11 }),
			borderError: `1px solid ${J(c, { alpha: .3 })}`,
			textColorError: c,
			colorError: J(c, { alpha: .16 }),
			colorBorderedError: "#0000",
			closeIconColorError: vh(c, { alpha: .7 }),
			closeIconColorHoverError: vh(c, { alpha: .7 }),
			closeIconColorPressedError: vh(c, { alpha: .7 }),
			closeColorHoverError: J(c, { alpha: .16 }),
			closeColorPressedError: J(c, { alpha: .12 })
		};
	}
};
//#endregion
//#region node_modules/naive-ui/es/_utils/css/color-to-class.mjs
function xy(e) {
	return e.replace(/#|\(|\)|,|\s|\./g, "_");
}
//#endregion
//#region node_modules/naive-ui/es/_internal/icons/replaceable.mjs
function Sy(e, t) {
	let n = /* @__PURE__ */ N({ render() {
		return t();
	} });
	return /* @__PURE__ */ N({
		name: ff(e),
		setup() {
			let t = Bn(gm, null)?.mergedIconsRef;
			return () => {
				let r = t?.value?.[e];
				return r ? r() : (F(), L(n, { key: 1 }));
			};
		}
	});
}
//#endregion
//#region node_modules/naive-ui/es/_internal/icons/Close.mjs
var Cy = Sy("close", () => (() => {
	let e = jm("6b30a2290cd08d4");
	return e[0] ||= R("svg", {
		viewBox: "0 0 12 12",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg",
		"aria-hidden": !0
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		fill: "none",
		"fill-rule": "evenodd"
	}, [R("g", {
		fill: "currentColor",
		"fill-rule": "nonzero"
	}, [R("path", { d: "M2.08859116,2.2156945 L2.14644661,2.14644661 C2.32001296,1.97288026 2.58943736,1.95359511 2.7843055,2.08859116 L2.85355339,2.14644661 L6,5.293 L9.14644661,2.14644661 C9.34170876,1.95118446 9.65829124,1.95118446 9.85355339,2.14644661 C10.0488155,2.34170876 10.0488155,2.65829124 9.85355339,2.85355339 L6.707,6 L9.85355339,9.14644661 C10.0271197,9.32001296 10.0464049,9.58943736 9.91140884,9.7843055 L9.85355339,9.85355339 C9.67998704,10.0271197 9.41056264,10.0464049 9.2156945,9.91140884 L9.14644661,9.85355339 L6,6.707 L2.85355339,9.85355339 C2.65829124,10.0488155 2.34170876,10.0488155 2.14644661,9.85355339 C1.95118446,9.65829124 1.95118446,9.34170876 2.14644661,9.14644661 L5.293,6 L2.14644661,2.85355339 C1.97288026,2.67998704 1.95359511,2.41056264 2.08859116,2.2156945 L2.14644661,2.14644661 L2.08859116,2.2156945 Z" })])])], -1);
})()), wy = V("base-close", "\n display: flex;\n align-items: center;\n justify-content: center;\n cursor: pointer;\n background-color: transparent;\n color: var(--n-close-icon-color);\n border-radius: var(--n-close-border-radius);\n height: var(--n-close-size);\n width: var(--n-close-size);\n font-size: var(--n-close-icon-size);\n outline: none;\n border: none;\n position: relative;\n padding: 0;\n", [
	U("absolute", "\n height: var(--n-close-icon-size);\n width: var(--n-close-icon-size);\n "),
	B("&::before", "\n content: \"\";\n position: absolute;\n width: var(--n-close-size);\n height: var(--n-close-size);\n left: 50%;\n top: 50%;\n transform: translateY(-50%) translateX(-50%);\n transition: inherit;\n border-radius: inherit;\n "),
	Ns("disabled", [
		B("&:hover", "\n color: var(--n-close-icon-color-hover);\n "),
		B("&:hover::before", "\n background-color: var(--n-close-color-hover);\n "),
		B("&:focus::before", "\n background-color: var(--n-close-color-hover);\n "),
		B("&:active", "\n color: var(--n-close-icon-color-pressed);\n "),
		B("&:active::before", "\n background-color: var(--n-close-color-pressed);\n ")
	]),
	U("disabled", "\n cursor: not-allowed;\n color: var(--n-close-icon-color-disabled);\n background-color: transparent;\n "),
	U("round", [B("&::before", "\n border-radius: 50%;\n ")])
]), Ty = /* @__PURE__ */ N({
	name: "BaseClose",
	props: {
		isButtonTag: {
			type: Boolean,
			default: !0
		},
		clsPrefix: {
			type: String,
			required: !0
		},
		disabled: {
			type: Boolean,
			default: void 0
		},
		focusable: {
			type: Boolean,
			default: !0
		},
		round: Boolean,
		onClick: Function,
		absolute: Boolean
	},
	setup(e) {
		return km("-base-close", wy, /* @__PURE__ */ M(e, "clsPrefix")), () => {
			let { clsPrefix: t, disabled: n, absolute: r, round: i, isButtonTag: a } = e, o = a ? "button" : "div";
			return (() => {
				let s = jm("b5bdc9fe09f5ae00");
				return F(), L(o, {
					type: a ? "button" : void 0,
					tabindex: n || !e.focusable ? -1 : 0,
					"aria-disabled": n,
					"aria-label": "close",
					role: a ? void 0 : "button",
					disabled: n,
					class: K([
						`${t}-base-close`,
						r && `${t}-base-close--absolute`,
						n && `${t}-base-close--disabled`,
						i && `${t}-base-close--round`
					]),
					onMousedown: s[0] ||= (t) => {
						e.focusable || t.preventDefault();
					},
					onClick: e.onClick
				}, {
					default: In(() => [(F(), L(Yh, { clsPrefix: t }, { default: () => (F(), L(Cy)) }, 1032, ["clsPrefix"]))]),
					_: 2
				}, 1032, [
					"type",
					"tabindex",
					"aria-disabled",
					"role",
					"disabled",
					"class",
					"onClick"
				]);
			})();
		};
	}
});
//#endregion
//#region node_modules/naive-ui/es/tag/styles/light.mjs
function Ey(e) {
	let { textColor2: t, primaryColorHover: n, primaryColorPressed: r, primaryColor: i, infoColor: a, successColor: o, warningColor: s, errorColor: c, baseColor: l, borderColor: u, opacityDisabled: d, tagColor: f, closeIconColor: p, closeIconColorHover: m, closeIconColorPressed: h, borderRadiusSmall: g, fontSizeMini: _, fontSizeTiny: v, fontSizeSmall: y, fontSizeMedium: b, heightMini: x, heightTiny: S, heightSmall: C, heightMedium: w, closeColorHover: T, closeColorPressed: E, buttonColor2Hover: D, buttonColor2Pressed: O, fontWeightStrong: ee } = e;
	return {
		...yy,
		closeBorderRadius: g,
		heightTiny: x,
		heightSmall: S,
		heightMedium: C,
		heightLarge: w,
		borderRadius: g,
		opacityDisabled: d,
		fontSizeTiny: _,
		fontSizeSmall: v,
		fontSizeMedium: y,
		fontSizeLarge: b,
		fontWeightStrong: ee,
		textColorCheckable: t,
		textColorHoverCheckable: t,
		textColorPressedCheckable: t,
		textColorChecked: l,
		colorCheckable: "#0000",
		colorHoverCheckable: D,
		colorPressedCheckable: O,
		colorChecked: i,
		colorCheckedHover: n,
		colorCheckedPressed: r,
		border: `1px solid ${u}`,
		textColor: t,
		color: f,
		colorBordered: "rgb(250, 250, 252)",
		closeIconColor: p,
		closeIconColorHover: m,
		closeIconColorPressed: h,
		closeColorHover: T,
		closeColorPressed: E,
		borderPrimary: `1px solid ${J(i, { alpha: .3 })}`,
		textColorPrimary: i,
		colorPrimary: J(i, { alpha: .12 }),
		colorBorderedPrimary: J(i, { alpha: .1 }),
		closeIconColorPrimary: i,
		closeIconColorHoverPrimary: i,
		closeIconColorPressedPrimary: i,
		closeColorHoverPrimary: J(i, { alpha: .12 }),
		closeColorPressedPrimary: J(i, { alpha: .18 }),
		borderInfo: `1px solid ${J(a, { alpha: .3 })}`,
		textColorInfo: a,
		colorInfo: J(a, { alpha: .12 }),
		colorBorderedInfo: J(a, { alpha: .1 }),
		closeIconColorInfo: a,
		closeIconColorHoverInfo: a,
		closeIconColorPressedInfo: a,
		closeColorHoverInfo: J(a, { alpha: .12 }),
		closeColorPressedInfo: J(a, { alpha: .18 }),
		borderSuccess: `1px solid ${J(o, { alpha: .3 })}`,
		textColorSuccess: o,
		colorSuccess: J(o, { alpha: .12 }),
		colorBorderedSuccess: J(o, { alpha: .1 }),
		closeIconColorSuccess: o,
		closeIconColorHoverSuccess: o,
		closeIconColorPressedSuccess: o,
		closeColorHoverSuccess: J(o, { alpha: .12 }),
		closeColorPressedSuccess: J(o, { alpha: .18 }),
		borderWarning: `1px solid ${J(s, { alpha: .35 })}`,
		textColorWarning: s,
		colorWarning: J(s, { alpha: .15 }),
		colorBorderedWarning: J(s, { alpha: .12 }),
		closeIconColorWarning: s,
		closeIconColorHoverWarning: s,
		closeIconColorPressedWarning: s,
		closeColorHoverWarning: J(s, { alpha: .12 }),
		closeColorPressedWarning: J(s, { alpha: .18 }),
		borderError: `1px solid ${J(c, { alpha: .23 })}`,
		textColorError: c,
		colorError: J(c, { alpha: .1 }),
		colorBorderedError: J(c, { alpha: .08 }),
		closeIconColorError: c,
		closeIconColorHoverError: c,
		closeIconColorPressedError: c,
		closeColorHoverError: J(c, { alpha: .12 }),
		closeColorPressedError: J(c, { alpha: .18 })
	};
}
var Dy = {
	name: "Tag",
	common: Ph,
	self: Ey
}, Oy = {
	color: Object,
	type: {
		type: String,
		default: "default"
	},
	round: Boolean,
	size: String,
	closable: Boolean,
	disabled: {
		type: Boolean,
		default: void 0
	}
}, ky = V("tag", "\n --n-close-margin: var(--n-close-margin-top) var(--n-close-margin-right) var(--n-close-margin-bottom) var(--n-close-margin-left);\n white-space: nowrap;\n position: relative;\n box-sizing: border-box;\n cursor: default;\n display: inline-flex;\n align-items: center;\n flex-wrap: nowrap;\n padding: var(--n-padding);\n border-radius: var(--n-border-radius);\n color: var(--n-text-color);\n background-color: var(--n-color);\n transition: \n border-color .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier),\n box-shadow .3s var(--n-bezier),\n opacity .3s var(--n-bezier);\n line-height: 1;\n height: var(--n-height);\n font-size: var(--n-font-size);\n", [
	U("strong", "\n font-weight: var(--n-font-weight-strong);\n "),
	H("border", "\n pointer-events: none;\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n border-radius: inherit;\n border: var(--n-border);\n transition: border-color .3s var(--n-bezier);\n "),
	H("icon", "\n display: flex;\n margin: 0 4px 0 0;\n color: var(--n-text-color);\n transition: color .3s var(--n-bezier);\n font-size: var(--n-avatar-size-override);\n "),
	H("avatar", "\n display: flex;\n margin: 0 6px 0 0;\n "),
	H("close", "\n margin: var(--n-close-margin);\n transition:\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier);\n "),
	U("round", "\n padding: 0 calc(var(--n-height) / 3);\n border-radius: calc(var(--n-height) / 2);\n ", [
		H("icon", "\n margin: 0 4px 0 calc((var(--n-height) - 8px) / -2);\n "),
		H("avatar", "\n margin: 0 6px 0 calc((var(--n-height) - 8px) / -2);\n "),
		U("closable", "\n padding: 0 calc(var(--n-height) / 4) 0 calc(var(--n-height) / 3);\n ")
	]),
	U("icon, avatar", [U("round", "\n padding: 0 calc(var(--n-height) / 3) 0 calc(var(--n-height) / 2);\n ")]),
	U("disabled", "\n cursor: not-allowed !important;\n opacity: var(--n-opacity-disabled);\n "),
	U("checkable", "\n cursor: pointer;\n box-shadow: none;\n color: var(--n-text-color-checkable);\n background-color: var(--n-color-checkable);\n ", [Ns("disabled", [B("&:hover", "background-color: var(--n-color-hover-checkable);", [Ns("checked", "color: var(--n-text-color-hover-checkable);")]), B("&:active", "background-color: var(--n-color-pressed-checkable);", [Ns("checked", "color: var(--n-text-color-pressed-checkable);")])]), U("checked", "\n color: var(--n-text-color-checked);\n background-color: var(--n-color-checked);\n ", [Ns("disabled", [B("&:hover", "background-color: var(--n-color-checked-hover);"), B("&:active", "background-color: var(--n-color-checked-pressed);")])])])
]), Ay = [
	"onClick",
	"onMouseenter",
	"onMouseleave"
], jy = {
	...Kh.props,
	...Oy,
	bordered: {
		type: Boolean,
		default: void 0
	},
	checked: Boolean,
	checkable: Boolean,
	strong: Boolean,
	triggerClickOnClose: Boolean,
	onClose: [Array, Function],
	onMouseenter: Function,
	onMouseleave: Function,
	"onUpdate:checked": Function,
	onUpdateChecked: Function,
	internalCloseFocusable: {
		type: Boolean,
		default: !0
	},
	internalCloseIsButtonTag: {
		type: Boolean,
		default: !0
	},
	onCheckedChange: Function
}, My = hm("n-tag"), Ny = /* @__PURE__ */ N({
	name: "Tag",
	props: jy,
	slots: Object,
	setup(e) {
		let t = /* @__PURE__ */ j(null), { mergedBorderedRef: n, mergedClsPrefixRef: r, inlineThemeDisabled: i, mergedRtlRef: a, mergedComponentPropsRef: o } = _m(e), s = z(() => e.size || o?.value?.Tag?.size || "medium"), c = Kh("Tag", "-tag", ky, Dy, e, r);
		zn(My, { roundRef: /* @__PURE__ */ M(e, "round") });
		function l() {
			if (!e.disabled && e.checkable) {
				let { checked: t, onCheckedChange: n, onUpdateChecked: r, "onUpdate:checked": i } = e;
				r && r(!t), i && i(!t), n && n(!t);
			}
		}
		function u(t) {
			if (e.triggerClickOnClose || t.stopPropagation(), !e.disabled) {
				let { onClose: n } = e;
				n && $(n, t);
			}
		}
		let d = { setTextContent(e) {
			let { value: n } = t;
			n && (n.textContent = e);
		} }, f = Kg("Tag", a, r), p = z(() => {
			let { type: t, color: { color: r, textColor: i } = {} } = e, a = s.value, { common: { cubicBezierEaseInOut: o }, self: { padding: l, closeMargin: u, borderRadius: d, opacityDisabled: f, textColorCheckable: p, textColorHoverCheckable: m, textColorPressedCheckable: h, textColorChecked: g, colorCheckable: _, colorHoverCheckable: v, colorPressedCheckable: y, colorChecked: b, colorCheckedHover: x, colorCheckedPressed: S, closeBorderRadius: C, fontWeightStrong: w, [W("colorBordered", t)]: T, [W("closeSize", a)]: E, [W("closeIconSize", a)]: D, [W("fontSize", a)]: O, [W("height", a)]: ee, [W("color", t)]: te, [W("textColor", t)]: ne, [W("border", t)]: re, [W("closeIconColor", t)]: ie, [W("closeIconColorHover", t)]: ae, [W("closeIconColorPressed", t)]: oe, [W("closeColorHover", t)]: se, [W("closeColorPressed", t)]: ce } } = c.value, le = Wm(u);
			return {
				"--n-font-weight-strong": w,
				"--n-avatar-size-override": `calc(${ee} - 8px)`,
				"--n-bezier": o,
				"--n-border-radius": d,
				"--n-border": re,
				"--n-close-icon-size": D,
				"--n-close-color-pressed": ce,
				"--n-close-color-hover": se,
				"--n-close-border-radius": C,
				"--n-close-icon-color": ie,
				"--n-close-icon-color-hover": ae,
				"--n-close-icon-color-pressed": oe,
				"--n-close-icon-color-disabled": ie,
				"--n-close-margin-top": le.top,
				"--n-close-margin-right": le.right,
				"--n-close-margin-bottom": le.bottom,
				"--n-close-margin-left": le.left,
				"--n-close-size": E,
				"--n-color": r || (n.value ? T : te),
				"--n-color-checkable": _,
				"--n-color-checked": b,
				"--n-color-checked-hover": x,
				"--n-color-checked-pressed": S,
				"--n-color-hover-checkable": v,
				"--n-color-pressed-checkable": y,
				"--n-font-size": O,
				"--n-height": ee,
				"--n-opacity-disabled": f,
				"--n-padding": l,
				"--n-text-color": i || ne,
				"--n-text-color-checkable": p,
				"--n-text-color-checked": g,
				"--n-text-color-hover-checkable": m,
				"--n-text-color-pressed-checkable": h
			};
		}), m = i ? Uh("tag", z(() => {
			let t = "", { type: r, color: { color: i, textColor: a } = {} } = e;
			return t += r[0], t += s.value[0], i && (t += `a${xy(i)}`), a && (t += `b${xy(a)}`), n.value && (t += "c"), t;
		}), p, e) : void 0;
		return {
			...d,
			rtlEnabled: f,
			mergedClsPrefix: r,
			contentRef: t,
			mergedBordered: n,
			handleClick: l,
			handleCloseClick: u,
			cssVars: i ? void 0 : p,
			themeClass: m?.themeClass,
			onRender: m?.onRender
		};
	},
	render() {
		let { mergedClsPrefix: e, rtlEnabled: t, closable: n, color: { borderColor: r } = {}, round: i, onRender: a, $slots: o } = this;
		a?.();
		let s = Wg(o.avatar, (t) => t && (F(), I("div", { class: K(`${e}-tag__avatar`) }, [G(() => t)], 2))), c = Wg(o.icon, (t) => t && (F(), I("div", { class: K(`${e}-tag__icon`) }, [G(() => t)], 2)));
		return F(), I("div", {
			class: K([
				`${e}-tag`,
				this.themeClass,
				{
					[`${e}-tag--rtl`]: t,
					[`${e}-tag--strong`]: this.strong,
					[`${e}-tag--disabled`]: this.disabled,
					[`${e}-tag--checkable`]: this.checkable,
					[`${e}-tag--checked`]: this.checkable && this.checked,
					[`${e}-tag--round`]: i,
					[`${e}-tag--avatar`]: s,
					[`${e}-tag--icon`]: c,
					[`${e}-tag--closable`]: n
				}
			]),
			style: k(this.cssVars),
			onClick: this.handleClick,
			onMouseenter: this.onMouseenter,
			onMouseleave: this.onMouseleave
		}, [
			G(() => c || s),
			R("span", {
				class: K(`${e}-tag__content`),
				ref: "contentRef"
			}, [G(() => this.$slots.default?.())], 2),
			!this.checkable && n ? (F(), L(Ty, {
				key: 0,
				clsPrefix: e,
				class: K(`${e}-tag__close`),
				disabled: this.disabled,
				onClick: this.handleCloseClick,
				focusable: this.internalCloseFocusable,
				round: i,
				isButtonTag: this.internalCloseIsButtonTag,
				absolute: !0
			}, null, 8, [
				"clsPrefix",
				"class",
				"disabled",
				"onClick",
				"focusable",
				"round",
				"isButtonTag"
			])) : G(() => null),
			!this.checkable && this.mergedBordered ? (F(), I("div", {
				key: 2,
				class: K(`${e}-tag__border`),
				style: k({ borderColor: r })
			}, null, 6)) : G(() => null)
		], 46, Ay);
	}
}), Py = {
	paddingSingle: "0 26px 0 12px",
	paddingMultiple: "3px 26px 0 12px",
	clearSize: "16px",
	arrowSize: "16px"
}, Fy = {
	name: "InternalSelection",
	common: Z,
	peers: { Popover: og },
	self(e) {
		let { borderRadius: t, textColor2: n, textColorDisabled: r, inputColor: i, inputColorDisabled: a, primaryColor: o, primaryColorHover: s, warningColor: c, warningColorHover: l, errorColor: u, errorColorHover: d, iconColor: f, iconColorDisabled: p, clearColor: m, clearColorHover: h, clearColorPressed: g, placeholderColor: _, placeholderColorDisabled: v, fontSizeTiny: y, fontSizeSmall: b, fontSizeMedium: x, fontSizeLarge: S, heightTiny: C, heightSmall: w, heightMedium: T, heightLarge: E, fontWeight: D } = e;
		return {
			...Py,
			fontWeight: D,
			fontSizeTiny: y,
			fontSizeSmall: b,
			fontSizeMedium: x,
			fontSizeLarge: S,
			heightTiny: C,
			heightSmall: w,
			heightMedium: T,
			heightLarge: E,
			borderRadius: t,
			textColor: n,
			textColorDisabled: r,
			placeholderColor: _,
			placeholderColorDisabled: v,
			color: i,
			colorDisabled: a,
			colorActive: J(o, { alpha: .1 }),
			border: "1px solid #0000",
			borderHover: `1px solid ${s}`,
			borderActive: `1px solid ${o}`,
			borderFocus: `1px solid ${s}`,
			boxShadowHover: "none",
			boxShadowActive: `0 0 8px 0 ${J(o, { alpha: .4 })}`,
			boxShadowFocus: `0 0 8px 0 ${J(o, { alpha: .4 })}`,
			caretColor: o,
			arrowColor: f,
			arrowColorDisabled: p,
			loadingColor: o,
			borderWarning: `1px solid ${c}`,
			borderHoverWarning: `1px solid ${l}`,
			borderActiveWarning: `1px solid ${c}`,
			borderFocusWarning: `1px solid ${l}`,
			boxShadowHoverWarning: "none",
			boxShadowActiveWarning: `0 0 8px 0 ${J(c, { alpha: .4 })}`,
			boxShadowFocusWarning: `0 0 8px 0 ${J(c, { alpha: .4 })}`,
			colorActiveWarning: J(c, { alpha: .1 }),
			caretColorWarning: c,
			borderError: `1px solid ${u}`,
			borderHoverError: `1px solid ${d}`,
			borderActiveError: `1px solid ${u}`,
			borderFocusError: `1px solid ${d}`,
			boxShadowHoverError: "none",
			boxShadowActiveError: `0 0 8px 0 ${J(u, { alpha: .4 })}`,
			boxShadowFocusError: `0 0 8px 0 ${J(u, { alpha: .4 })}`,
			colorActiveError: J(u, { alpha: .1 }),
			caretColorError: u,
			clearColor: m,
			clearColorHover: h,
			clearColorPressed: g
		};
	}
}, Iy = {
	iconMargin: "11px 8px 0 12px",
	iconMarginRtl: "11px 12px 0 8px",
	iconSize: "24px",
	closeIconSize: "16px",
	closeSize: "20px",
	closeMargin: "13px 14px 0 0",
	closeMarginRtl: "13px 0 0 14px",
	padding: "13px"
}, Ly = {
	name: "Alert",
	common: Z,
	self(e) {
		let { lineHeight: t, borderRadius: n, fontWeightStrong: r, dividerColor: i, inputColor: a, textColor1: o, textColor2: s, closeColorHover: c, closeColorPressed: l, closeIconColor: u, closeIconColorHover: d, closeIconColorPressed: f, infoColorSuppl: p, successColorSuppl: m, warningColorSuppl: h, errorColorSuppl: g, fontSize: _ } = e;
		return {
			...Iy,
			fontSize: _,
			lineHeight: t,
			titleFontWeight: r,
			borderRadius: n,
			border: `1px solid ${i}`,
			color: a,
			titleTextColor: o,
			iconColor: s,
			contentTextColor: s,
			closeBorderRadius: n,
			closeColorHover: c,
			closeColorPressed: l,
			closeIconColor: u,
			closeIconColorHover: d,
			closeIconColorPressed: f,
			borderInfo: `1px solid ${J(p, { alpha: .35 })}`,
			colorInfo: J(p, { alpha: .25 }),
			titleTextColorInfo: o,
			iconColorInfo: p,
			contentTextColorInfo: s,
			closeColorHoverInfo: c,
			closeColorPressedInfo: l,
			closeIconColorInfo: u,
			closeIconColorHoverInfo: d,
			closeIconColorPressedInfo: f,
			borderSuccess: `1px solid ${J(m, { alpha: .35 })}`,
			colorSuccess: J(m, { alpha: .25 }),
			titleTextColorSuccess: o,
			iconColorSuccess: m,
			contentTextColorSuccess: s,
			closeColorHoverSuccess: c,
			closeColorPressedSuccess: l,
			closeIconColorSuccess: u,
			closeIconColorHoverSuccess: d,
			closeIconColorPressedSuccess: f,
			borderWarning: `1px solid ${J(h, { alpha: .35 })}`,
			colorWarning: J(h, { alpha: .25 }),
			titleTextColorWarning: o,
			iconColorWarning: h,
			contentTextColorWarning: s,
			closeColorHoverWarning: c,
			closeColorPressedWarning: l,
			closeIconColorWarning: u,
			closeIconColorHoverWarning: d,
			closeIconColorPressedWarning: f,
			borderError: `1px solid ${J(g, { alpha: .35 })}`,
			colorError: J(g, { alpha: .25 }),
			titleTextColorError: o,
			iconColorError: g,
			contentTextColorError: s,
			closeColorHoverError: c,
			closeColorPressedError: l,
			closeIconColorError: u,
			closeIconColorHoverError: d,
			closeIconColorPressedError: f
		};
	}
}, Ry = Sy("error", () => (() => {
	let e = jm("550229f72e94547c");
	return e[0] ||= R("svg", {
		viewBox: "0 0 48 48",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg"
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		"fill-rule": "evenodd"
	}, [R("g", { "fill-rule": "nonzero" }, [R("path", { d: "M24,4 C35.045695,4 44,12.954305 44,24 C44,35.045695 35.045695,44 24,44 C12.954305,44 4,35.045695 4,24 C4,12.954305 12.954305,4 24,4 Z M17.8838835,16.1161165 L17.7823881,16.0249942 C17.3266086,15.6583353 16.6733914,15.6583353 16.2176119,16.0249942 L16.1161165,16.1161165 L16.0249942,16.2176119 C15.6583353,16.6733914 15.6583353,17.3266086 16.0249942,17.7823881 L16.1161165,17.8838835 L22.233,24 L16.1161165,30.1161165 L16.0249942,30.2176119 C15.6583353,30.6733914 15.6583353,31.3266086 16.0249942,31.7823881 L16.1161165,31.8838835 L16.2176119,31.9750058 C16.6733914,32.3416647 17.3266086,32.3416647 17.7823881,31.9750058 L17.8838835,31.8838835 L24,25.767 L30.1161165,31.8838835 L30.2176119,31.9750058 C30.6733914,32.3416647 31.3266086,32.3416647 31.7823881,31.9750058 L31.8838835,31.8838835 L31.9750058,31.7823881 C32.3416647,31.3266086 32.3416647,30.6733914 31.9750058,30.2176119 L31.8838835,30.1161165 L25.767,24 L31.8838835,17.8838835 L31.9750058,17.7823881 C32.3416647,17.3266086 32.3416647,16.6733914 31.9750058,16.2176119 L31.8838835,16.1161165 L31.7823881,16.0249942 C31.3266086,15.6583353 30.6733914,15.6583353 30.2176119,16.0249942 L30.1161165,16.1161165 L24,22.233 L17.8838835,16.1161165 L17.7823881,16.0249942 L17.8838835,16.1161165 Z" })])])], -1);
})()), zy = Sy("info", () => (() => {
	let e = jm("1d7d3032c5ab60");
	return e[0] ||= R("svg", {
		viewBox: "0 0 28 28",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg"
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		"fill-rule": "evenodd"
	}, [R("g", { "fill-rule": "nonzero" }, [R("path", { d: "M14,2 C20.6274,2 26,7.37258 26,14 C26,20.6274 20.6274,26 14,26 C7.37258,26 2,20.6274 2,14 C2,7.37258 7.37258,2 14,2 Z M14,11 C13.4477,11 13,11.4477 13,12 L13,12 L13,20 C13,20.5523 13.4477,21 14,21 C14.5523,21 15,20.5523 15,20 L15,20 L15,12 C15,11.4477 14.5523,11 14,11 Z M14,6.75 C13.3096,6.75 12.75,7.30964 12.75,8 C12.75,8.69036 13.3096,9.25 14,9.25 C14.6904,9.25 15.25,8.69036 15.25,8 C15.25,7.30964 14.6904,6.75 14,6.75 Z" })])])], -1);
})()), By = Sy("success", () => (() => {
	let e = jm("2d4548faff86b4af");
	return e[0] ||= R("svg", {
		viewBox: "0 0 48 48",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg"
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		"fill-rule": "evenodd"
	}, [R("g", { "fill-rule": "nonzero" }, [R("path", { d: "M24,4 C35.045695,4 44,12.954305 44,24 C44,35.045695 35.045695,44 24,44 C12.954305,44 4,35.045695 4,24 C4,12.954305 12.954305,4 24,4 Z M32.6338835,17.6161165 C32.1782718,17.1605048 31.4584514,17.1301307 30.9676119,17.5249942 L30.8661165,17.6161165 L20.75,27.732233 L17.1338835,24.1161165 C16.6457281,23.6279612 15.8542719,23.6279612 15.3661165,24.1161165 C14.9105048,24.5717282 14.8801307,25.2915486 15.2749942,25.7823881 L15.3661165,25.8838835 L19.8661165,30.3838835 C20.3217282,30.8394952 21.0415486,30.8698693 21.5323881,30.4750058 L21.6338835,30.3838835 L32.6338835,19.3838835 C33.1220388,18.8957281 33.1220388,18.1042719 32.6338835,17.6161165 Z" })])])], -1);
})()), Vy = Sy("warning", () => (() => {
	let e = jm("eb9505c3181fdf04");
	return e[0] ||= R("svg", {
		viewBox: "0 0 24 24",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg"
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		"fill-rule": "evenodd"
	}, [R("g", { "fill-rule": "nonzero" }, [R("path", { d: "M12,2 C17.523,2 22,6.478 22,12 C22,17.522 17.523,22 12,22 C6.477,22 2,17.522 2,12 C2,6.478 6.477,2 12,2 Z M12.0018002,15.0037242 C11.450254,15.0037242 11.0031376,15.4508407 11.0031376,16.0023869 C11.0031376,16.553933 11.450254,17.0010495 12.0018002,17.0010495 C12.5533463,17.0010495 13.0004628,16.553933 13.0004628,16.0023869 C13.0004628,15.4508407 12.5533463,15.0037242 12.0018002,15.0037242 Z M11.99964,7 C11.4868042,7.00018474 11.0642719,7.38637706 11.0066858,7.8837365 L11,8.00036004 L11.0018003,13.0012393 L11.00857,13.117858 C11.0665141,13.6151758 11.4893244,14.0010638 12.0021602,14.0008793 C12.514996,14.0006946 12.9375283,13.6145023 12.9951144,13.1171428 L13.0018002,13.0005193 L13,7.99964009 L12.9932303,7.8830214 C12.9352861,7.38570354 12.5124758,6.99981552 11.99964,7 Z" })])])], -1);
})()), Hy = /* @__PURE__ */ N({
	name: "FadeInExpandTransition",
	props: {
		appear: Boolean,
		group: Boolean,
		mode: String,
		onLeave: Function,
		onAfterLeave: Function,
		onAfterEnter: Function,
		width: Boolean,
		reverse: Boolean
	},
	setup(e, { slots: t }) {
		function n(t) {
			e.width ? t.style.maxWidth = `${t.offsetWidth}px` : t.style.maxHeight = `${t.offsetHeight}px`, t.offsetWidth;
		}
		function r(t) {
			e.width ? t.style.maxWidth = "0" : t.style.maxHeight = "0", t.offsetWidth;
			let { onLeave: n } = e;
			n && n();
		}
		function i(t) {
			e.width ? t.style.maxWidth = "" : t.style.maxHeight = "";
			let { onAfterLeave: n } = e;
			n && n();
		}
		function a(t) {
			if (t.style.transition = "none", e.width) {
				let e = t.offsetWidth;
				t.style.maxWidth = "0", t.offsetWidth, t.style.transition = "", t.style.maxWidth = `${e}px`;
			} else if (e.reverse) t.style.maxHeight = `${t.offsetHeight}px`, t.offsetHeight, t.style.transition = "", t.style.maxHeight = "0";
			else {
				let e = t.offsetHeight;
				t.style.maxHeight = "0", t.offsetWidth, t.style.transition = "", t.style.maxHeight = `${e}px`;
			}
			t.offsetWidth;
		}
		function o(t) {
			e.width ? t.style.maxWidth = "" : e.reverse || (t.style.maxHeight = ""), e.onAfterEnter?.();
		}
		return () => {
			let { group: s, width: c, appear: l, mode: u } = e, d = s ? zo : Va, f = {
				name: c ? "fade-in-width-expand-transition" : "fade-in-height-expand-transition",
				appear: l,
				onEnter: a,
				onAfterEnter: o,
				onBeforeLeave: n,
				onLeave: r,
				onAfterLeave: i
			};
			return s || (f.mode = u), Ea(d, f, t);
		};
	}
});
//#endregion
//#region node_modules/naive-ui/es/alert/styles/light.mjs
function Uy(e) {
	let { lineHeight: t, borderRadius: n, fontWeightStrong: r, baseColor: i, dividerColor: a, actionColor: o, textColor1: s, textColor2: c, closeColorHover: l, closeColorPressed: u, closeIconColor: d, closeIconColorHover: f, closeIconColorPressed: p, infoColor: m, successColor: h, warningColor: g, errorColor: _, fontSize: v } = e;
	return {
		...Iy,
		fontSize: v,
		lineHeight: t,
		titleFontWeight: r,
		borderRadius: n,
		border: `1px solid ${a}`,
		color: o,
		titleTextColor: s,
		iconColor: c,
		contentTextColor: c,
		closeBorderRadius: n,
		closeColorHover: l,
		closeColorPressed: u,
		closeIconColor: d,
		closeIconColorHover: f,
		closeIconColorPressed: p,
		borderInfo: `1px solid ${q(i, J(m, { alpha: .25 }))}`,
		colorInfo: q(i, J(m, { alpha: .08 })),
		titleTextColorInfo: s,
		iconColorInfo: m,
		contentTextColorInfo: c,
		closeColorHoverInfo: l,
		closeColorPressedInfo: u,
		closeIconColorInfo: d,
		closeIconColorHoverInfo: f,
		closeIconColorPressedInfo: p,
		borderSuccess: `1px solid ${q(i, J(h, { alpha: .25 }))}`,
		colorSuccess: q(i, J(h, { alpha: .08 })),
		titleTextColorSuccess: s,
		iconColorSuccess: h,
		contentTextColorSuccess: c,
		closeColorHoverSuccess: l,
		closeColorPressedSuccess: u,
		closeIconColorSuccess: d,
		closeIconColorHoverSuccess: f,
		closeIconColorPressedSuccess: p,
		borderWarning: `1px solid ${q(i, J(g, { alpha: .33 }))}`,
		colorWarning: q(i, J(g, { alpha: .08 })),
		titleTextColorWarning: s,
		iconColorWarning: g,
		contentTextColorWarning: c,
		closeColorHoverWarning: l,
		closeColorPressedWarning: u,
		closeIconColorWarning: d,
		closeIconColorHoverWarning: f,
		closeIconColorPressedWarning: p,
		borderError: `1px solid ${q(i, J(_, { alpha: .25 }))}`,
		colorError: q(i, J(_, { alpha: .08 })),
		titleTextColorError: s,
		iconColorError: _,
		contentTextColorError: c,
		closeColorHoverError: l,
		closeColorPressedError: u,
		closeIconColorError: d,
		closeIconColorHoverError: f,
		closeIconColorPressedError: p
	};
}
var Wy = {
	name: "Alert",
	common: Ph,
	self: Uy
}, { cubicBezierEaseInOut: Gy, cubicBezierEaseOut: Ky, cubicBezierEaseIn: qy } = ym;
function Jy({ overflow: e = "hidden", duration: t = ".3s", originalTransition: n = "", leavingDelay: r = "0s", foldPadding: i = !1, enterToProps: a = void 0, leaveToProps: o = void 0, reverse: s = !1 } = {}) {
	let c = s ? "leave" : "enter", l = s ? "enter" : "leave";
	return [
		B(`&.fade-in-height-expand-transition-${l}-from,
 &.fade-in-height-expand-transition-${c}-to`, {
			...a,
			opacity: 1
		}),
		B(`&.fade-in-height-expand-transition-${l}-to,
 &.fade-in-height-expand-transition-${c}-from`, {
			...o,
			opacity: 0,
			marginTop: "0 !important",
			marginBottom: "0 !important",
			paddingTop: i ? "0 !important" : void 0,
			paddingBottom: i ? "0 !important" : void 0
		}),
		B(`&.fade-in-height-expand-transition-${l}-active`, `
 overflow: ${e};
 transition:
 max-height ${t} ${Gy} ${r},
 opacity ${t} ${Ky} ${r},
 margin-top ${t} ${Gy} ${r},
 margin-bottom ${t} ${Gy} ${r},
 padding-top ${t} ${Gy} ${r},
 padding-bottom ${t} ${Gy} ${r}
 ${n ? `,${n}` : ""}
 `),
		B(`&.fade-in-height-expand-transition-${c}-active`, `
 overflow: ${e};
 transition:
 max-height ${t} ${Gy},
 opacity ${t} ${qy},
 margin-top ${t} ${Gy},
 margin-bottom ${t} ${Gy},
 padding-top ${t} ${Gy},
 padding-bottom ${t} ${Gy}
 ${n ? `,${n}` : ""}
 `)
	];
}
//#endregion
//#region node_modules/naive-ui/es/alert/src/styles/index.cssr.mjs
var Yy = V("alert", "\n line-height: var(--n-line-height);\n border-radius: var(--n-border-radius);\n position: relative;\n transition: background-color .3s var(--n-bezier);\n background-color: var(--n-color);\n text-align: start;\n word-break: break-word;\n", [
	H("border", "\n border-radius: inherit;\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n transition: border-color .3s var(--n-bezier);\n border: var(--n-border);\n pointer-events: none;\n "),
	U("closable", [V("alert-body", [H("title", "\n padding-right: 24px;\n ")])]),
	H("icon", { color: "var(--n-icon-color)" }),
	V("alert-body", { padding: "var(--n-padding)" }, [H("title", { color: "var(--n-title-text-color)" }), H("content", { color: "var(--n-content-text-color)" })]),
	Jy({
		originalTransition: "transform .3s var(--n-bezier)",
		enterToProps: { transform: "scale(1)" },
		leaveToProps: { transform: "scale(0.9)" }
	}),
	H("icon", "\n position: absolute;\n left: 0;\n top: 0;\n align-items: center;\n justify-content: center;\n display: flex;\n width: var(--n-icon-size);\n height: var(--n-icon-size);\n font-size: var(--n-icon-size);\n margin: var(--n-icon-margin);\n "),
	H("close", "\n transition:\n color .3s var(--n-bezier),\n background-color .3s var(--n-bezier);\n position: absolute;\n right: 0;\n top: 0;\n margin: var(--n-close-margin);\n "),
	U("show-icon", [V("alert-body", { paddingLeft: "calc(var(--n-icon-margin-left) + var(--n-icon-size) + var(--n-icon-margin-right))" })]),
	U("right-adjust", [V("alert-body", { paddingRight: "calc(var(--n-close-size) + var(--n-padding) + 2px)" })]),
	V("alert-body", "\n border-radius: var(--n-border-radius);\n transition: border-color .3s var(--n-bezier);\n ", [H("title", "\n transition: color .3s var(--n-bezier);\n font-size: 16px;\n line-height: 19px;\n font-weight: var(--n-title-font-weight);\n ", [B("& +", [H("content", { marginTop: "9px" })])]), H("content", {
		transition: "color .3s var(--n-bezier)",
		fontSize: "var(--n-font-size)"
	})]),
	H("icon", { transition: "color .3s var(--n-bezier)" })
]), Xy = /* @__PURE__ */ N({
	name: "Alert",
	inheritAttrs: !1,
	props: {
		...Kh.props,
		title: String,
		showIcon: {
			type: Boolean,
			default: !0
		},
		type: {
			type: String,
			default: "default"
		},
		bordered: {
			type: Boolean,
			default: !0
		},
		closable: Boolean,
		onClose: Function,
		onAfterLeave: Function,
		onAfterHide: Function
	},
	slots: Object,
	setup(e) {
		let { mergedClsPrefixRef: t, mergedBorderedRef: n, inlineThemeDisabled: r, mergedRtlRef: i } = _m(e), a = Kh("Alert", "-alert", Yy, Wy, e, t), o = Kg("Alert", i, t), s = z(() => {
			let { common: { cubicBezierEaseInOut: t }, self: n } = a.value, { fontSize: r, borderRadius: i, titleFontWeight: o, lineHeight: s, iconSize: c, iconMargin: l, iconMarginRtl: u, closeIconSize: d, closeBorderRadius: f, closeSize: p, closeMargin: m, closeMarginRtl: h, padding: g } = n, { type: _ } = e, { left: v, right: y } = Wm(l);
			return {
				"--n-bezier": t,
				"--n-color": n[W("color", _)],
				"--n-close-icon-size": d,
				"--n-close-border-radius": f,
				"--n-close-color-hover": n[W("closeColorHover", _)],
				"--n-close-color-pressed": n[W("closeColorPressed", _)],
				"--n-close-icon-color": n[W("closeIconColor", _)],
				"--n-close-icon-color-hover": n[W("closeIconColorHover", _)],
				"--n-close-icon-color-pressed": n[W("closeIconColorPressed", _)],
				"--n-icon-color": n[W("iconColor", _)],
				"--n-border": n[W("border", _)],
				"--n-title-text-color": n[W("titleTextColor", _)],
				"--n-content-text-color": n[W("contentTextColor", _)],
				"--n-line-height": s,
				"--n-border-radius": i,
				"--n-font-size": r,
				"--n-title-font-weight": o,
				"--n-icon-size": c,
				"--n-icon-margin": l,
				"--n-icon-margin-rtl": u,
				"--n-close-size": p,
				"--n-close-margin": m,
				"--n-close-margin-rtl": h,
				"--n-padding": g,
				"--n-icon-margin-left": v,
				"--n-icon-margin-right": y
			};
		}), c = r ? Uh("alert", z(() => e.type[0]), s, e) : void 0, l = /* @__PURE__ */ j(!0), u = () => {
			let { onAfterLeave: t, onAfterHide: n } = e;
			t && t(), n && n();
		};
		return {
			rtlEnabled: o,
			mergedClsPrefix: t,
			mergedBordered: n,
			visible: l,
			handleCloseClick: () => {
				Promise.resolve(e.onClose?.()).then((e) => {
					e !== !1 && (l.value = !1);
				});
			},
			handleAfterLeave: () => {
				u();
			},
			mergedTheme: a,
			cssVars: r ? void 0 : s,
			themeClass: c?.themeClass,
			onRender: c?.onRender
		};
	},
	render() {
		return this.onRender?.(), F(), L(Hy, { onAfterLeave: this.handleAfterLeave }, { default: () => {
			let { mergedClsPrefix: e, $slots: t } = this, n = {
				class: [
					`${e}-alert`,
					this.themeClass,
					this.closable && `${e}-alert--closable`,
					this.showIcon && `${e}-alert--show-icon`,
					!this.title && this.closable && `${e}-alert--right-adjust`,
					this.rtlEnabled && `${e}-alert--rtl`
				],
				style: this.cssVars,
				role: "alert"
			};
			return this.visible ? (F(), I("div", aa({ key: 1 }, aa(this.$attrs, n)), [
				G(() => this.closable && (F(), L(Ty, {
					clsPrefix: e,
					class: K(`${e}-alert__close`),
					onClick: this.handleCloseClick
				}, null, 8, [
					"clsPrefix",
					"class",
					"onClick"
				]))),
				G(() => this.bordered && (F(), I("div", { class: K(`${e}-alert__border`) }, null, 2))),
				G(() => this.showIcon && (F(), I("div", {
					class: K(`${e}-alert__icon`),
					"aria-hidden": "true"
				}, [G(() => Hg(t.icon, () => [(F(), L(Yh, { clsPrefix: e }, { default: () => {
					switch (this.type) {
						case "success": return F(), L(By, { key: 3 });
						case "info": return F(), L(zy, { key: 4 });
						case "warning": return F(), L(Vy, { key: 5 });
						case "error": return F(), L(Ry, { key: 6 });
						default: return null;
					}
				} }, 1032, ["clsPrefix"]))]))], 2))),
				R("div", { class: K([`${e}-alert-body`, this.mergedBordered && `${e}-alert-body--bordered`]) }, [G(() => Wg(t.header, (t) => {
					let n = t || this.title;
					return n ? (F(), I("div", {
						key: 2,
						class: K(`${e}-alert-body__title`)
					}, [G(() => n)], 2)) : null;
				})), G(() => t.default && (F(), I("div", { class: K(`${e}-alert-body__content`) }, [G(() => t.default())], 2)))], 2)
			], 16)) : null;
		} }, 1032, ["onAfterLeave"]);
	}
}), Zy = {
	linkFontSize: "13px",
	linkPadding: "0 0 0 16px",
	railWidth: "4px"
};
//#endregion
//#region node_modules/naive-ui/es/anchor/styles/light.mjs
function Qy(e) {
	let { borderRadius: t, railColor: n, primaryColor: r, primaryColorHover: i, primaryColorPressed: a, textColor2: o } = e;
	return {
		...Zy,
		borderRadius: t,
		railColor: n,
		railColorActive: r,
		linkColor: J(r, { alpha: .15 }),
		linkTextColor: o,
		linkTextColorHover: i,
		linkTextColorPressed: a,
		linkTextColorActive: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/anchor/styles/dark.mjs
var $y = {
	name: "Anchor",
	common: Z,
	self: Qy
};
//#endregion
//#region node_modules/naive-ui/es/_utils/naive/attribute.mjs
function eb(e) {
	switch (typeof e) {
		case "string": return e || void 0;
		case "number": return String(e);
		default: return;
	}
}
//#endregion
//#region node_modules/naive-ui/es/input/styles/_common.mjs
var tb = {
	paddingTiny: "0 8px",
	paddingSmall: "0 10px",
	paddingMedium: "0 12px",
	paddingLarge: "0 14px",
	clearSize: "16px"
};
//#endregion
//#region node_modules/naive-ui/es/input/styles/dark.mjs
function nb(e) {
	let { textColor2: t, textColor3: n, textColorDisabled: r, primaryColor: i, primaryColorHover: a, inputColor: o, inputColorDisabled: s, warningColor: c, warningColorHover: l, errorColor: u, errorColorHover: d, borderRadius: f, lineHeight: p, fontSizeTiny: m, fontSizeSmall: h, fontSizeMedium: g, fontSizeLarge: _, heightTiny: v, heightSmall: y, heightMedium: b, heightLarge: x, clearColor: S, clearColorHover: C, clearColorPressed: w, placeholderColor: T, placeholderColorDisabled: E, iconColor: D, iconColorDisabled: O, iconColorHover: ee, iconColorPressed: te, fontWeight: ne } = e;
	return {
		...tb,
		fontWeight: ne,
		countTextColorDisabled: r,
		countTextColor: n,
		heightTiny: v,
		heightSmall: y,
		heightMedium: b,
		heightLarge: x,
		fontSizeTiny: m,
		fontSizeSmall: h,
		fontSizeMedium: g,
		fontSizeLarge: _,
		lineHeight: p,
		lineHeightTextarea: p,
		borderRadius: f,
		iconSize: "16px",
		groupLabelColor: o,
		textColor: t,
		textColorDisabled: r,
		textDecorationColor: t,
		groupLabelTextColor: t,
		caretColor: i,
		placeholderColor: T,
		placeholderColorDisabled: E,
		color: o,
		colorHover: o,
		colorDisabled: s,
		colorFocus: J(i, { alpha: .1 }),
		groupLabelBorder: "1px solid #0000",
		border: "1px solid #0000",
		borderHover: `1px solid ${a}`,
		borderDisabled: "1px solid #0000",
		borderFocus: `1px solid ${a}`,
		boxShadowFocus: `0 0 8px 0 ${J(i, { alpha: .3 })}`,
		loadingColor: i,
		loadingColorWarning: c,
		borderWarning: `1px solid ${c}`,
		borderHoverWarning: `1px solid ${l}`,
		colorFocusWarning: J(c, { alpha: .1 }),
		borderFocusWarning: `1px solid ${l}`,
		boxShadowFocusWarning: `0 0 8px 0 ${J(c, { alpha: .3 })}`,
		caretColorWarning: c,
		loadingColorError: u,
		borderError: `1px solid ${u}`,
		borderHoverError: `1px solid ${d}`,
		colorFocusError: J(u, { alpha: .1 }),
		borderFocusError: `1px solid ${d}`,
		boxShadowFocusError: `0 0 8px 0 ${J(u, { alpha: .3 })}`,
		caretColorError: u,
		clearColor: S,
		clearColorHover: C,
		clearColorPressed: w,
		iconColor: D,
		iconColorDisabled: O,
		iconColorHover: ee,
		iconColorPressed: te,
		suffixTextColor: t
	};
}
var rb = Gh({
	name: "Input",
	common: Z,
	peers: { Scrollbar: Rh },
	self: nb
}), ib = hm("n-form-item");
function ab(e, { defaultSize: t = "medium", mergedSize: n, mergedDisabled: r } = {}) {
	let i = Bn(ib, null);
	zn(ib, null);
	let a = z(n ? () => n(i) : () => {
		let { size: n } = e;
		if (n) return n;
		if (i) {
			let { mergedSize: e } = i;
			if (e.value !== void 0) return e.value;
		}
		return t;
	}), o = z(r ? () => r(i) : () => {
		let { disabled: t } = e;
		return t === void 0 ? i ? i.disabled.value : !1 : t;
	}), s = z(() => {
		let { status: t } = e;
		return t || i?.mergedValidationStatus.value;
	});
	return Lr(() => {
		i && i.restoreValidation();
	}), {
		mergedSizeRef: a,
		mergedDisabledRef: o,
		mergedStatusRef: s,
		nTriggerFormBlur() {
			i && i.handleContentBlur();
		},
		nTriggerFormChange() {
			i && i.handleContentChange();
		},
		nTriggerFormFocus() {
			i && i.handleContentFocus();
		},
		nTriggerFormInput() {
			i && i.handleContentInput();
		}
	};
}
//#endregion
//#region node_modules/naive-ui/es/_internal/icons/Eye.mjs
var ob = /* @__PURE__ */ N({
	name: "Eye",
	render() {
		return (() => {
			let e = jm("ae479a1970012861");
			return e[0] ||= R("svg", {
				xmlns: "http://www.w3.org/2000/svg",
				viewBox: "0 0 512 512"
			}, [R("path", {
				d: "M255.66 112c-77.94 0-157.89 45.11-220.83 135.33a16 16 0 0 0-.27 17.77C82.92 340.8 161.8 400 255.66 400c92.84 0 173.34-59.38 221.79-135.25a16.14 16.14 0 0 0 0-17.47C428.89 172.28 347.8 112 255.66 112z",
				fill: "none",
				stroke: "currentColor",
				"stroke-linecap": "round",
				"stroke-linejoin": "round",
				"stroke-width": "32"
			}), R("circle", {
				cx: "256",
				cy: "256",
				r: "80",
				fill: "none",
				stroke: "currentColor",
				"stroke-miterlimit": "10",
				"stroke-width": "32"
			})], -1);
		})();
	}
}), sb = /* @__PURE__ */ N({
	name: "EyeOff",
	render() {
		return (() => {
			let e = jm("2c06203b450ce879");
			return e[0] ||= R("svg", {
				xmlns: "http://www.w3.org/2000/svg",
				viewBox: "0 0 512 512"
			}, [
				R("path", {
					d: "M432 448a15.92 15.92 0 0 1-11.31-4.69l-352-352a16 16 0 0 1 22.62-22.62l352 352A16 16 0 0 1 432 448z",
					fill: "currentColor"
				}),
				R("path", {
					d: "M255.66 384c-41.49 0-81.5-12.28-118.92-36.5c-34.07-22-64.74-53.51-88.7-91v-.08c19.94-28.57 41.78-52.73 65.24-72.21a2 2 0 0 0 .14-2.94L93.5 161.38a2 2 0 0 0-2.71-.12c-24.92 21-48.05 46.76-69.08 76.92a31.92 31.92 0 0 0-.64 35.54c26.41 41.33 60.4 76.14 98.28 100.65C162 402 207.9 416 255.66 416a239.13 239.13 0 0 0 75.8-12.58a2 2 0 0 0 .77-3.31l-21.58-21.58a4 4 0 0 0-3.83-1a204.8 204.8 0 0 1-51.16 6.47z",
					fill: "currentColor"
				}),
				R("path", {
					d: "M490.84 238.6c-26.46-40.92-60.79-75.68-99.27-100.53C349 110.55 302 96 255.66 96a227.34 227.34 0 0 0-74.89 12.83a2 2 0 0 0-.75 3.31l21.55 21.55a4 4 0 0 0 3.88 1a192.82 192.82 0 0 1 50.21-6.69c40.69 0 80.58 12.43 118.55 37c34.71 22.4 65.74 53.88 89.76 91a.13.13 0 0 1 0 .16a310.72 310.72 0 0 1-64.12 72.73a2 2 0 0 0-.15 2.95l19.9 19.89a2 2 0 0 0 2.7.13a343.49 343.49 0 0 0 68.64-78.48a32.2 32.2 0 0 0-.1-34.78z",
					fill: "currentColor"
				}),
				R("path", {
					d: "M256 160a95.88 95.88 0 0 0-21.37 2.4a2 2 0 0 0-1 3.38l112.59 112.56a2 2 0 0 0 3.38-1A96 96 0 0 0 256 160z",
					fill: "currentColor"
				}),
				R("path", {
					d: "M165.78 233.66a2 2 0 0 0-3.38 1a96 96 0 0 0 115 115a2 2 0 0 0 1-3.38z",
					fill: "currentColor"
				})
			], -1);
		})();
	}
}), cb = /* @__PURE__ */ N({
	name: "BaseIconSwitchTransition",
	setup(e, { slots: t }) {
		let n = Dg();
		return () => (F(), L(Va, {
			name: "icon-switch-transition",
			appear: n.value
		}, Fm(t), 1032, ["appear"]));
	}
}), lb = Sy("clear", () => (() => {
	let e = jm("c93f8499adf26ca3");
	return e[0] ||= R("svg", {
		viewBox: "0 0 16 16",
		version: "1.1",
		xmlns: "http://www.w3.org/2000/svg"
	}, [R("g", {
		stroke: "none",
		"stroke-width": "1",
		fill: "none",
		"fill-rule": "evenodd"
	}, [R("g", {
		fill: "currentColor",
		"fill-rule": "nonzero"
	}, [R("path", { d: "M8,2 C11.3137085,2 14,4.6862915 14,8 C14,11.3137085 11.3137085,14 8,14 C4.6862915,14 2,11.3137085 2,8 C2,4.6862915 4.6862915,2 8,2 Z M6.5343055,5.83859116 C6.33943736,5.70359511 6.07001296,5.72288026 5.89644661,5.89644661 L5.89644661,5.89644661 L5.83859116,5.9656945 C5.70359511,6.16056264 5.72288026,6.42998704 5.89644661,6.60355339 L5.89644661,6.60355339 L7.293,8 L5.89644661,9.39644661 L5.83859116,9.4656945 C5.70359511,9.66056264 5.72288026,9.92998704 5.89644661,10.1035534 L5.89644661,10.1035534 L5.9656945,10.1614088 C6.16056264,10.2964049 6.42998704,10.2771197 6.60355339,10.1035534 L6.60355339,10.1035534 L8,8.707 L9.39644661,10.1035534 L9.4656945,10.1614088 C9.66056264,10.2964049 9.92998704,10.2771197 10.1035534,10.1035534 L10.1035534,10.1035534 L10.1614088,10.0343055 C10.2964049,9.83943736 10.2771197,9.57001296 10.1035534,9.39644661 L10.1035534,9.39644661 L8.707,8 L10.1035534,6.60355339 L10.1614088,6.5343055 C10.2964049,6.33943736 10.2771197,6.07001296 10.1035534,5.89644661 L10.1035534,5.89644661 L10.0343055,5.83859116 C9.83943736,5.70359511 9.57001296,5.72288026 9.39644661,5.89644661 L9.39644661,5.89644661 L8,7.293 L6.60355339,5.89644661 Z" })])])], -1);
})()), { cubicBezierEaseInOut: ub } = ym;
function db({ originalTransform: e = "", left: t = 0, top: n = 0, transition: r = `all .3s ${ub} !important` } = {}) {
	return [
		B("&.icon-switch-transition-enter-from, &.icon-switch-transition-leave-to", {
			transform: `${e} scale(0.75)`,
			left: t,
			top: n,
			opacity: 0
		}),
		B("&.icon-switch-transition-enter-to, &.icon-switch-transition-leave-from", {
			transform: `scale(1) ${e}`,
			left: t,
			top: n,
			opacity: 1
		}),
		B("&.icon-switch-transition-enter-active, &.icon-switch-transition-leave-active", {
			transformOrigin: "center",
			position: "absolute",
			left: t,
			top: n,
			transition: r
		})
	];
}
//#endregion
//#region node_modules/naive-ui/es/_internal/clear/src/styles/index.cssr.mjs
var fb = V("base-clear", "\n flex-shrink: 0;\n height: 1em;\n width: 1em;\n position: relative;\n", [B(">", [
	H("clear", "\n font-size: var(--n-clear-size);\n height: 1em;\n width: 1em;\n cursor: pointer;\n color: var(--n-clear-color);\n transition: color .3s var(--n-bezier);\n display: flex;\n ", [B("&:hover", "\n color: var(--n-clear-color-hover)!important;\n "), B("&:active", "\n color: var(--n-clear-color-pressed)!important;\n ")]),
	H("placeholder", "\n display: flex;\n "),
	H("clear, placeholder", "\n position: absolute;\n left: 50%;\n top: 50%;\n transform: translateX(-50%) translateY(-50%);\n ", [db({
		originalTransform: "translateX(-50%) translateY(-50%)",
		left: "50%",
		top: "50%"
	})])
])]), pb = ["onClick", "onMousedown"], mb = /* @__PURE__ */ N({
	name: "BaseClear",
	props: {
		clsPrefix: {
			type: String,
			required: !0
		},
		show: Boolean,
		onClear: Function
	},
	setup(e) {
		return km("-base-clear", fb, /* @__PURE__ */ M(e, "clsPrefix")), { handleMouseDown(e) {
			e.preventDefault();
		} };
	},
	render() {
		let { clsPrefix: e } = this;
		return F(), I("div", { class: K(`${e}-base-clear`) }, [Xi(cb, null, { default: () => this.show ? (F(), I("div", {
			key: "dismiss",
			class: K(`${e}-base-clear__clear`),
			onClick: this.onClear,
			onMousedown: this.handleMouseDown,
			"data-clear": !0
		}, [G(() => Hg(this.$slots.icon, () => [(F(), L(Yh, { clsPrefix: e }, { default: () => (F(), L(lb)) }, 1032, ["clsPrefix"]))]))], 42, pb)) : (F(), I("div", {
			key: "icon",
			class: K(`${e}-base-clear__placeholder`)
		}, [G(() => this.$slots.placeholder?.())], 2)) }, 1024)], 2);
	}
}), hb = /* @__PURE__ */ N({
	name: "ChevronDown",
	render() {
		return (() => {
			let e = jm("ae90ecf811a811ac");
			return e[0] ||= R("svg", {
				viewBox: "0 0 16 16",
				fill: "none",
				xmlns: "http://www.w3.org/2000/svg"
			}, [R("path", {
				d: "M3.14645 5.64645C3.34171 5.45118 3.65829 5.45118 3.85355 5.64645L8 9.79289L12.1464 5.64645C12.3417 5.45118 12.6583 5.45118 12.8536 5.64645C13.0488 5.84171 13.0488 6.15829 12.8536 6.35355L8.35355 10.8536C8.15829 11.0488 7.84171 11.0488 7.64645 10.8536L3.14645 6.35355C2.95118 6.15829 2.95118 5.84171 3.14645 5.64645Z",
				fill: "currentColor"
			})], -1);
		})();
	}
}), gb = B([B("@keyframes rotator", "\n 0% {\n -webkit-transform: rotate(0deg);\n transform: rotate(0deg);\n }\n 100% {\n -webkit-transform: rotate(360deg);\n transform: rotate(360deg);\n }"), V("base-loading", "\n position: relative;\n line-height: 0;\n width: 1em;\n height: 1em;\n ", [
	H("transition-wrapper", "\n position: absolute;\n width: 100%;\n height: 100%;\n ", [db()]),
	H("placeholder", "\n position: absolute;\n left: 50%;\n top: 50%;\n transform: translateX(-50%) translateY(-50%);\n ", [db({
		left: "50%",
		top: "50%",
		originalTransform: "translateX(-50%) translateY(-50%)"
	})]),
	H("container", "\n animation: rotator 3s linear infinite both;\n ", [H("icon", "\n height: 1em;\n width: 1em;\n ")])
])]), _b = ["viewBox"], vb = ["values", "dur"], yb = [
	"stroke-width",
	"cx",
	"cy",
	"r",
	"stroke-dasharray",
	"stroke-dashoffset"
], bb = ["values", "dur"], xb = ["values", "dur"], Sb = "1.6s", Cb = /* @__PURE__ */ N({
	name: "BaseLoading",
	props: {
		clsPrefix: {
			type: String,
			required: !0
		},
		show: {
			type: Boolean,
			default: !0
		},
		strokeWidth: {
			type: Number,
			default: 28
		},
		stroke: {
			type: String,
			default: void 0
		},
		scale: {
			type: Number,
			default: 1
		},
		radius: {
			type: Number,
			default: 100
		}
	},
	setup(e) {
		km("-base-loading", gb, /* @__PURE__ */ M(e, "clsPrefix"));
	},
	render() {
		let { clsPrefix: e, radius: t, strokeWidth: n, stroke: r, scale: i } = this, a = t / i;
		return F(), I("div", {
			class: K(`${e}-base-loading`),
			role: "img",
			"aria-label": "loading"
		}, [Xi(cb, null, { default: () => this.show ? (F(), I("div", {
			key: "icon",
			class: K(`${e}-base-loading__transition-wrapper`)
		}, [R("div", { class: K(`${e}-base-loading__container`) }, [(F(), I("svg", {
			class: K(`${e}-base-loading__icon`),
			viewBox: `0 0 ${2 * a} ${2 * a}`,
			xmlns: "http://www.w3.org/2000/svg",
			style: k({ color: r })
		}, [R("g", null, [R("animateTransform", {
			attributeName: "transform",
			type: "rotate",
			values: `0 ${a} ${a};270 ${a} ${a}`,
			begin: "0s",
			dur: Sb,
			fill: "freeze",
			repeatCount: "indefinite"
		}, null, 8, vb), R("circle", {
			class: K(`${e}-base-loading__icon`),
			fill: "none",
			stroke: "currentColor",
			"stroke-width": n,
			"stroke-linecap": "round",
			cx: a,
			cy: a,
			r: t - n / 2,
			"stroke-dasharray": 5.67 * t,
			"stroke-dashoffset": 18.48 * t
		}, [R("animateTransform", {
			attributeName: "transform",
			type: "rotate",
			values: `0 ${a} ${a};135 ${a} ${a};450 ${a} ${a}`,
			begin: "0s",
			dur: Sb,
			fill: "freeze",
			repeatCount: "indefinite"
		}, null, 8, bb), R("animate", {
			attributeName: "stroke-dashoffset",
			values: `${5.67 * t};${1.42 * t};${5.67 * t}`,
			begin: "0s",
			dur: Sb,
			fill: "freeze",
			repeatCount: "indefinite"
		}, null, 8, xb)], 10, yb)])], 14, _b))], 2)], 2)) : (F(), I("div", {
			key: "placeholder",
			class: K(`${e}-base-loading__placeholder`)
		}, [G(() => this.$slots.default?.())], 2)) }, 1024)], 2);
	}
}), wb = /* @__PURE__ */ N({
	name: "InternalSelectionSuffix",
	props: {
		clsPrefix: {
			type: String,
			required: !0
		},
		showArrow: {
			type: Boolean,
			default: void 0
		},
		showClear: {
			type: Boolean,
			default: void 0
		},
		loading: Boolean,
		onClear: Function
	},
	setup(e, { slots: t }) {
		return () => {
			let { clsPrefix: n } = e;
			return F(), L(Cb, {
				clsPrefix: n,
				class: K(`${n}-base-suffix`),
				strokeWidth: 24,
				scale: .85,
				show: e.loading
			}, { default: () => e.showArrow ? (F(), L(mb, {
				key: 1,
				clsPrefix: n,
				show: e.showClear,
				onClear: e.onClear
			}, { placeholder: () => (F(), L(Yh, {
				clsPrefix: n,
				class: K(`${n}-base-suffix__arrow`)
			}, { default: () => Hg(t.default, () => [(F(), L(hb))]) }, 1032, ["clsPrefix", "class"])) }, 1032, [
				"clsPrefix",
				"show",
				"onClear"
			])) : null }, 1032, [
				"clsPrefix",
				"class",
				"show"
			]);
		};
	}
}), Tb = typeof document < "u" && typeof window < "u", Eb = Tb && "chrome" in window;
Tb && navigator.userAgent.includes("Firefox");
var Db = Tb && navigator.userAgent.includes("Safari") && !Eb;
//#endregion
//#region node_modules/naive-ui/es/input/styles/light.mjs
function Ob(e) {
	let { textColor2: t, textColor3: n, textColorDisabled: r, primaryColor: i, primaryColorHover: a, inputColor: o, inputColorDisabled: s, borderColor: c, warningColor: l, warningColorHover: u, errorColor: d, errorColorHover: f, borderRadius: p, lineHeight: m, fontSizeTiny: h, fontSizeSmall: g, fontSizeMedium: _, fontSizeLarge: v, heightTiny: y, heightSmall: b, heightMedium: x, heightLarge: S, actionColor: C, clearColor: w, clearColorHover: T, clearColorPressed: E, placeholderColor: D, placeholderColorDisabled: O, iconColor: ee, iconColorDisabled: te, iconColorHover: ne, iconColorPressed: re, fontWeight: ie } = e;
	return {
		...tb,
		fontWeight: ie,
		countTextColorDisabled: r,
		countTextColor: n,
		heightTiny: y,
		heightSmall: b,
		heightMedium: x,
		heightLarge: S,
		fontSizeTiny: h,
		fontSizeSmall: g,
		fontSizeMedium: _,
		fontSizeLarge: v,
		lineHeight: m,
		lineHeightTextarea: m,
		borderRadius: p,
		iconSize: "16px",
		groupLabelColor: C,
		groupLabelTextColor: t,
		textColor: t,
		textColorDisabled: r,
		textDecorationColor: t,
		caretColor: i,
		placeholderColor: D,
		placeholderColorDisabled: O,
		color: o,
		colorHover: o,
		colorDisabled: s,
		colorFocus: o,
		groupLabelBorder: `1px solid ${c}`,
		border: `1px solid ${c}`,
		borderHover: `1px solid ${a}`,
		borderDisabled: `1px solid ${c}`,
		borderFocus: `1px solid ${a}`,
		boxShadowFocus: `0 0 0 2px ${J(i, { alpha: .2 })}`,
		loadingColor: i,
		loadingColorWarning: l,
		borderWarning: `1px solid ${l}`,
		borderHoverWarning: `1px solid ${u}`,
		colorFocusWarning: o,
		borderFocusWarning: `1px solid ${u}`,
		boxShadowFocusWarning: `0 0 0 2px ${J(l, { alpha: .2 })}`,
		caretColorWarning: l,
		loadingColorError: d,
		borderError: `1px solid ${d}`,
		borderHoverError: `1px solid ${f}`,
		colorFocusError: o,
		borderFocusError: `1px solid ${f}`,
		boxShadowFocusError: `0 0 0 2px ${J(d, { alpha: .2 })}`,
		caretColorError: d,
		clearColor: w,
		clearColorHover: T,
		clearColorPressed: E,
		iconColor: ee,
		iconColorDisabled: te,
		iconColorHover: ne,
		iconColorPressed: re,
		suffixTextColor: t
	};
}
var kb = Gh({
	name: "Input",
	common: Ph,
	peers: { Scrollbar: Lh },
	self: Ob
}), Ab = hm("n-input"), jb = V("input", "\n max-width: 100%;\n cursor: text;\n line-height: 1.5;\n z-index: auto;\n outline: none;\n box-sizing: border-box;\n position: relative;\n display: inline-flex;\n border-radius: var(--n-border-radius);\n background-color: var(--n-color);\n transition: background-color .3s var(--n-bezier);\n font-size: var(--n-font-size);\n font-weight: var(--n-font-weight);\n --n-padding-vertical: calc((var(--n-height) - 1.5 * var(--n-font-size)) / 2);\n", [
	H("input, textarea", "\n overflow: hidden;\n flex-grow: 1;\n position: relative;\n "),
	H("input-el, textarea-el, input-mirror, textarea-mirror, separator, placeholder", "\n box-sizing: border-box;\n font-size: inherit;\n line-height: 1.5;\n font-family: inherit;\n border: none;\n outline: none;\n background-color: #0000;\n text-align: inherit;\n transition:\n -webkit-text-fill-color .3s var(--n-bezier),\n caret-color .3s var(--n-bezier),\n color .3s var(--n-bezier),\n text-decoration-color .3s var(--n-bezier);\n "),
	H("input-el, textarea-el", "\n -webkit-appearance: none;\n scrollbar-width: none;\n width: 100%;\n min-width: 0;\n text-decoration-color: var(--n-text-decoration-color);\n color: var(--n-text-color);\n caret-color: var(--n-caret-color);\n background-color: transparent;\n ", [
		B("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb", "\n width: 0;\n height: 0;\n display: none;\n "),
		B("&::placeholder", "\n color: #0000;\n -webkit-text-fill-color: transparent !important;\n "),
		B("&:-webkit-autofill ~", [H("placeholder", "display: none;")])
	]),
	U("round", [Ns("textarea", "border-radius: calc(var(--n-height) / 2);")]),
	H("placeholder", "\n pointer-events: none;\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n overflow: hidden;\n color: var(--n-placeholder-color);\n ", [B("span", "\n width: 100%;\n display: inline-block;\n ")]),
	U("textarea", [H("placeholder", "overflow: visible;")]),
	Ns("autosize", "width: 100%;"),
	U("autosize", [H("textarea-el, input-el", "\n position: absolute;\n top: 0;\n left: 0;\n height: 100%;\n ")]),
	V("input-wrapper", "\n overflow: hidden;\n display: inline-flex;\n flex-grow: 1;\n position: relative;\n padding-left: var(--n-padding-left);\n padding-right: var(--n-padding-right);\n "),
	H("input-mirror", "\n padding: 0;\n height: var(--n-height);\n line-height: var(--n-height);\n overflow: hidden;\n visibility: hidden;\n position: static;\n white-space: pre;\n pointer-events: none;\n "),
	H("input-el", "\n padding: 0;\n height: var(--n-height);\n line-height: var(--n-height);\n ", [B("&[type=password]::-ms-reveal", "display: none;"), B("+", [H("placeholder", "\n display: flex;\n align-items: center; \n ")])]),
	Ns("textarea", [H("placeholder", "white-space: nowrap;")]),
	H("eye", "\n display: flex;\n align-items: center;\n justify-content: center;\n transition: color .3s var(--n-bezier);\n "),
	U("textarea", "width: 100%;", [
		V("input-word-count", "\n position: absolute;\n right: var(--n-padding-right);\n bottom: var(--n-padding-vertical);\n "),
		U("resizable", [V("input-wrapper", "\n resize: vertical;\n min-height: var(--n-height);\n ")]),
		H("textarea-el, textarea-mirror, placeholder", "\n height: 100%;\n padding-left: 0;\n padding-right: 0;\n padding-top: var(--n-padding-vertical);\n padding-bottom: var(--n-padding-vertical);\n word-break: break-word;\n display: inline-block;\n vertical-align: bottom;\n box-sizing: border-box;\n line-height: var(--n-line-height-textarea);\n margin: 0;\n resize: none;\n white-space: pre-wrap;\n scroll-padding-block-end: var(--n-padding-vertical);\n "),
		H("textarea-mirror", "\n width: 100%;\n pointer-events: none;\n overflow: hidden;\n visibility: hidden;\n position: static;\n white-space: pre-wrap;\n overflow-wrap: break-word;\n ")
	]),
	U("pair", [H("input-el, placeholder", "text-align: center;"), H("separator", "\n display: flex;\n align-items: center;\n transition: color .3s var(--n-bezier);\n color: var(--n-text-color);\n white-space: nowrap;\n ", [V("icon", "\n color: var(--n-icon-color);\n "), V("base-icon", "\n color: var(--n-icon-color);\n ")])]),
	U("disabled", "\n cursor: not-allowed;\n background-color: var(--n-color-disabled);\n ", [
		H("border", "border: var(--n-border-disabled);"),
		H("input-el, textarea-el", "\n cursor: not-allowed;\n color: var(--n-text-color-disabled);\n text-decoration-color: var(--n-text-color-disabled);\n "),
		H("placeholder", "color: var(--n-placeholder-color-disabled);"),
		H("separator", "color: var(--n-text-color-disabled);", [V("icon", "\n color: var(--n-icon-color-disabled);\n "), V("base-icon", "\n color: var(--n-icon-color-disabled);\n ")]),
		V("input-word-count", "\n color: var(--n-count-text-color-disabled);\n "),
		H("suffix, prefix", "color: var(--n-text-color-disabled);", [V("icon", "\n color: var(--n-icon-color-disabled);\n "), V("internal-icon", "\n color: var(--n-icon-color-disabled);\n ")])
	]),
	Ns("disabled", [
		H("eye", "\n color: var(--n-icon-color);\n cursor: pointer;\n ", [B("&:hover", "\n color: var(--n-icon-color-hover);\n "), B("&:active", "\n color: var(--n-icon-color-pressed);\n ")]),
		B("&:hover", "background-color: var(--n-color-hover);", [H("state-border", "border: var(--n-border-hover);")]),
		U("focus", "background-color: var(--n-color-focus);", [H("state-border", "\n border: var(--n-border-focus);\n box-shadow: var(--n-box-shadow-focus);\n ")])
	]),
	H("border, state-border", "\n box-sizing: border-box;\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n pointer-events: none;\n border-radius: inherit;\n border: var(--n-border);\n transition:\n box-shadow .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n "),
	H("state-border", "\n border-color: #0000;\n z-index: 1;\n "),
	H("prefix", "margin-right: 4px;"),
	H("suffix", "\n margin-left: 4px;\n "),
	H("suffix, prefix", "\n transition: color .3s var(--n-bezier);\n flex-wrap: nowrap;\n flex-shrink: 0;\n line-height: var(--n-height);\n white-space: nowrap;\n display: inline-flex;\n align-items: center;\n justify-content: center;\n color: var(--n-suffix-text-color);\n ", [
		V("base-loading", "\n font-size: var(--n-icon-size);\n margin: 0 2px;\n color: var(--n-loading-color);\n "),
		V("base-clear", "\n font-size: var(--n-icon-size);\n ", [H("placeholder", [V("base-icon", "\n transition: color .3s var(--n-bezier);\n color: var(--n-icon-color);\n font-size: var(--n-icon-size);\n ")])]),
		B(">", [V("icon", "\n transition: color .3s var(--n-bezier);\n color: var(--n-icon-color);\n font-size: var(--n-icon-size);\n ")]),
		V("base-icon", "\n font-size: var(--n-icon-size);\n ")
	]),
	V("input-word-count", "\n pointer-events: none;\n line-height: 1.5;\n font-size: .85em;\n color: var(--n-count-text-color);\n transition: color .3s var(--n-bezier);\n margin-left: 4px;\n font-variant: tabular-nums;\n "),
	["warning", "error"].map((e) => U(`${e}-status`, [Ns("disabled", [
		V("base-loading", `
 color: var(--n-loading-color-${e})
 `),
		H("input-el, textarea-el", `
 caret-color: var(--n-caret-color-${e});
 `),
		H("state-border", `
 border: var(--n-border-${e});
 `),
		B("&:hover", [H("state-border", `
 border: var(--n-border-hover-${e});
 `)]),
		B("&:focus", `
 background-color: var(--n-color-focus-${e});
 `, [H("state-border", `
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)]),
		U("focus", `
 background-color: var(--n-color-focus-${e});
 `, [H("state-border", `
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)])
	])]))
]), Mb = V("input", [U("disabled", [H("input-el, textarea-el", "\n -webkit-text-fill-color: var(--n-text-color-disabled);\n ")])]);
//#endregion
//#region node_modules/naive-ui/es/input/src/utils.mjs
function Nb(e) {
	let t = 0;
	for (let n of e) t++;
	return t;
}
function Pb(e) {
	return e === "" || e == null;
}
function Fb(e) {
	let t = /* @__PURE__ */ j(null);
	function n() {
		let { value: n } = e;
		if (!n?.focus) {
			i();
			return;
		}
		let { selectionStart: r, selectionEnd: a, value: o } = n;
		if (r == null || a == null) {
			i();
			return;
		}
		t.value = {
			start: r,
			end: a,
			beforeText: o.slice(0, r),
			afterText: o.slice(a)
		};
	}
	function r() {
		let { value: n } = t, { value: r } = e;
		if (!n || !r) return;
		let { value: i } = r, { start: a, beforeText: o, afterText: s } = n, c = i.length;
		if (i.endsWith(s)) c = i.length - s.length;
		else if (i.startsWith(o)) c = o.length;
		else {
			let e = o[a - 1], t = i.indexOf(e, a - 1);
			t !== -1 && (c = t + 1);
		}
		r.setSelectionRange?.(c, c);
	}
	function i() {
		t.value = null;
	}
	return Wn(e, i), {
		recordCursor: n,
		restoreCursor: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/input/src/WordCount.mjs
var Ib = /* @__PURE__ */ N({
	name: "InputWordCount",
	setup(e, { slots: t }) {
		let { mergedValueRef: n, maxlengthRef: r, mergedClsPrefixRef: i, countGraphemesRef: a } = Bn(Ab), o = z(() => {
			let { value: e } = n;
			return e === null || Array.isArray(e) ? 0 : (a.value || Nb)(e);
		});
		return () => {
			let { value: e } = r, { value: a } = n;
			return F(), I("span", { class: K(`${i.value}-input-word-count`) }, [G(() => Ug(t.default, { value: a === null || Array.isArray(a) ? "" : a }, () => [e === void 0 ? o.value : `${o.value} / ${e}`]))], 2);
		};
	}
}), Lb = [
	"autofocus",
	"rows",
	"placeholder",
	"value",
	"disabled",
	"maxlength",
	"minlength",
	"readonly",
	"tabindex",
	"onBlur",
	"onFocus",
	"onInput",
	"onChange",
	"onScroll"
], Rb = [
	"type",
	"tabindex",
	"placeholder",
	"disabled",
	"maxlength",
	"minlength",
	"value",
	"readonly",
	"autofocus",
	"size",
	"onBlur",
	"onFocus",
	"onInput",
	"onChange"
], zb = ["onMousedown", "onClick"], Bb = [
	"type",
	"tabindex",
	"placeholder",
	"disabled",
	"maxlength",
	"minlength",
	"value",
	"readonly",
	"onBlur",
	"onFocus",
	"onInput",
	"onChange"
], Vb = [
	"tabindex",
	"onFocus",
	"onBlur",
	"onClick",
	"onMousedown",
	"onMouseenter",
	"onMouseleave",
	"onCompositionstart",
	"onCompositionend",
	"onKeyup",
	"onKeydown"
], Hb = /* @__PURE__ */ N({
	name: "Input",
	props: {
		...Kh.props,
		bordered: {
			type: Boolean,
			default: void 0
		},
		type: {
			type: String,
			default: "text"
		},
		placeholder: [Array, String],
		defaultValue: {
			type: [String, Array],
			default: null
		},
		value: [String, Array],
		disabled: {
			type: Boolean,
			default: void 0
		},
		size: String,
		rows: {
			type: [Number, String],
			default: 3
		},
		round: Boolean,
		minlength: [String, Number],
		maxlength: [String, Number],
		clearable: Boolean,
		autosize: {
			type: [Boolean, Object],
			default: !1
		},
		pair: Boolean,
		separator: String,
		readonly: {
			type: [String, Boolean],
			default: !1
		},
		passivelyActivated: Boolean,
		showPasswordOn: String,
		stateful: {
			type: Boolean,
			default: !0
		},
		autofocus: Boolean,
		inputProps: Object,
		resizable: {
			type: Boolean,
			default: !0
		},
		showCount: Boolean,
		loading: {
			type: Boolean,
			default: void 0
		},
		allowInput: Function,
		renderCount: Function,
		onMousedown: Function,
		onKeydown: Function,
		onKeyup: [Function, Array],
		onInput: [Function, Array],
		onFocus: [Function, Array],
		onBlur: [Function, Array],
		onClick: [Function, Array],
		onChange: [Function, Array],
		onClear: [Function, Array],
		countGraphemes: Function,
		status: String,
		"onUpdate:value": [Function, Array],
		onUpdateValue: [Function, Array],
		textDecoration: [String, Array],
		attrSize: {
			type: Number,
			default: 20
		},
		onInputBlur: [Function, Array],
		onInputFocus: [Function, Array],
		onDeactivate: [Function, Array],
		onActivate: [Function, Array],
		onWrapperFocus: [Function, Array],
		onWrapperBlur: [Function, Array],
		internalDeactivateOnEnter: Boolean,
		internalForceFocus: Boolean,
		internalLoadingBeforeSuffix: {
			type: Boolean,
			default: !0
		},
		showPasswordToggle: Boolean
	},
	slots: Object,
	setup(e) {
		let { mergedClsPrefixRef: t, mergedBorderedRef: n, inlineThemeDisabled: r, mergedRtlRef: i, mergedComponentPropsRef: a } = _m(e), o = Kh("Input", "-input", jb, kb, e, t);
		Db && km("-input-safari", Mb, t);
		let s = /* @__PURE__ */ j(null), c = /* @__PURE__ */ j(null), l = /* @__PURE__ */ j(null), u = /* @__PURE__ */ j(null), d = /* @__PURE__ */ j(null), f = /* @__PURE__ */ j(null), p = /* @__PURE__ */ j(null), m = Fb(p), h = /* @__PURE__ */ j(null), { localeRef: g } = Wh("Input"), _ = /* @__PURE__ */ j(e.defaultValue), v = Eg(/* @__PURE__ */ M(e, "value"), _), y = ab(e, { mergedSize: (t) => {
			let { size: n } = e;
			if (n) return n;
			let { mergedSize: r } = t || {};
			return r?.value ? r.value : a?.value?.Input?.size || "medium";
		} }), { mergedSizeRef: b, mergedDisabledRef: x, mergedStatusRef: S } = y, C = /* @__PURE__ */ j(!1), w = /* @__PURE__ */ j(!1), T = /* @__PURE__ */ j(!1), E = /* @__PURE__ */ j(!1), D = null, O = z(() => {
			let { placeholder: t, pair: n } = e;
			return n ? Array.isArray(t) ? t : t === void 0 ? ["", ""] : [t, t] : t === void 0 ? [g.value.placeholder] : [t];
		}), ee = z(() => {
			let { value: e } = T, { value: t } = v, { value: n } = O;
			return !e && (Pb(t) || Array.isArray(t) && Pb(t[0])) && n[0];
		}), te = z(() => {
			let { value: e } = T, { value: t } = v, { value: n } = O;
			return !e && n[1] && (Pb(t) || Array.isArray(t) && Pb(t[1]));
		}), ne = Sg(() => e.internalForceFocus || C.value), re = Sg(() => {
			if (x.value || e.readonly || !e.clearable || !ne.value && !w.value) return !1;
			let { value: t } = v, { value: n } = ne;
			return e.pair ? !!(Array.isArray(t) && (t[0] || t[1])) && (w.value || n) : !!t && (w.value || n);
		}), ie = z(() => {
			let { showPasswordOn: t } = e;
			if (t) return t;
			if (e.showPasswordToggle) return "click";
		}), ae = /* @__PURE__ */ j(!1), oe = z(() => {
			let { textDecoration: t } = e;
			return t ? Array.isArray(t) ? t.map((e) => ({ textDecoration: e })) : [{ textDecoration: t }] : ["", ""];
		}), se = /* @__PURE__ */ j(void 0), ce = () => {
			if (e.type === "textarea") {
				let { autosize: t } = e;
				if (t && (se.value = h.value?.$el?.offsetWidth), !c.value || typeof t == "boolean") return;
				let { paddingTop: n, paddingBottom: r, lineHeight: i } = window.getComputedStyle(c.value), a = Number(n.slice(0, -2)), o = Number(r.slice(0, -2)), s = Number(i.slice(0, -2)), { value: u } = l;
				if (!u) return;
				if (t.minRows) {
					let e = Math.max(t.minRows, 1), n = `${a + o + s * e}px`;
					u.style.minHeight = n;
				}
				if (t.maxRows) {
					let e = `${a + o + s * t.maxRows}px`;
					u.style.maxHeight = e;
				}
			}
		}, le = z(() => {
			let { maxlength: t } = e;
			return t === void 0 ? void 0 : Number(t);
		});
		Fr(() => {
			let { value: e } = v;
			Array.isArray(e) || Xe(e);
		});
		let ue = da().proxy;
		function k(t, n) {
			let { onUpdateValue: r, "onUpdate:value": i, onInput: a } = e, { nTriggerFormInput: o } = y;
			r && $(r, t, n), i && $(i, t, n), a && $(a, t, n), _.value = t, o();
		}
		function de(t, n) {
			let { onChange: r } = e, { nTriggerFormChange: i } = y;
			r && $(r, t, n), _.value = t, i();
		}
		function fe(t) {
			let { onBlur: n } = e, { nTriggerFormBlur: r } = y;
			n && $(n, t), r();
		}
		function pe(t) {
			let { onFocus: n } = e, { nTriggerFormFocus: r } = y;
			n && $(n, t), r();
		}
		function me(t) {
			let { onClear: n } = e;
			n && $(n, t);
		}
		function he(t) {
			let { onInputBlur: n } = e;
			n && $(n, t);
		}
		function ge(t) {
			let { onInputFocus: n } = e;
			n && $(n, t);
		}
		function _e() {
			let { onDeactivate: t } = e;
			t && $(t);
		}
		function ve() {
			let { onActivate: t } = e;
			t && $(t);
		}
		function ye(t) {
			let { onClick: n } = e;
			n && $(n, t);
		}
		function be(t) {
			let { onWrapperFocus: n } = e;
			n && $(n, t);
		}
		function xe(t) {
			let { onWrapperBlur: n } = e;
			n && $(n, t);
		}
		function Se() {
			T.value = !0;
		}
		function Ce(e) {
			T.value = !1, e.target === f.value ? we(e, 1) : we(e, 0);
		}
		function we(t, n = 0, r = "input") {
			let i = t.target.value;
			if (Xe(i), t instanceof InputEvent && !t.isComposing && (T.value = !1), e.type === "textarea") {
				let { value: e } = h;
				e && e.syncUnifiedContainer();
			}
			if (D = i, T.value) return;
			m.recordCursor();
			let a = Te(i);
			if (a) {
				if (!e.pair) r === "input" ? k(i, { source: n }) : de(i, { source: n });
				else {
					let { value: e } = v;
					e = Array.isArray(e) ? [e[0], e[1]] : ["", ""], e[n] = i, r === "input" ? k(e, { source: n }) : de(e, { source: n });
				}
			}
			ue.$forceUpdate(), a || wn(m.restoreCursor);
		}
		function Te(t) {
			let { countGraphemes: n, maxlength: r, minlength: i } = e;
			if (n) {
				let e;
				if (r !== void 0 && (e === void 0 && (e = n(t)), e > Number(r)) || i !== void 0 && (e === void 0 && (e = n(t)), e < Number(r))) return !1;
			}
			let { allowInput: a } = e;
			return typeof a != "function" || a(t);
		}
		function Ee(e) {
			he(e), e.relatedTarget === s.value && _e(), (e.relatedTarget === null || e.relatedTarget !== d.value && e.relatedTarget !== f.value && e.relatedTarget !== c.value) && (E.value = !1), Ae(e, "blur"), p.value = null;
		}
		function De(e, t) {
			ge(e), C.value = !0, E.value = !0, ve(), Ae(e, "focus"), t === 0 ? p.value = d.value : t === 1 ? p.value = f.value : t === 2 && (p.value = c.value);
		}
		function Oe(t) {
			e.passivelyActivated && (xe(t), Ae(t, "blur"));
		}
		function ke(t) {
			e.passivelyActivated && (C.value = !0, be(t), Ae(t, "focus"));
		}
		function Ae(e, t) {
			e.relatedTarget !== null && (e.relatedTarget === d.value || e.relatedTarget === f.value || e.relatedTarget === c.value || e.relatedTarget === s.value) || (t === "focus" ? (pe(e), C.value = !0) : t === "blur" && (fe(e), C.value = !1));
		}
		function je(e, t) {
			we(e, t, "change");
		}
		function Me(e) {
			ye(e);
		}
		function Ne(e) {
			me(e), Pe();
		}
		function Pe() {
			e.pair ? (k(["", ""], { source: "clear" }), de(["", ""], { source: "clear" })) : (k("", { source: "clear" }), de("", { source: "clear" }));
		}
		function Fe(t) {
			let { onMousedown: n } = e;
			n && n(t);
			let { tagName: r } = t.target;
			if (r !== "INPUT" && r !== "TEXTAREA") {
				if (e.resizable) {
					let { value: e } = s;
					if (e) {
						let { left: n, top: r, width: i, height: a } = e.getBoundingClientRect();
						if (n + i - 14 < t.clientX && t.clientX < n + i && r + a - 14 < t.clientY && t.clientY < r + a) return;
					}
				}
				t.preventDefault(), C.value || We();
			}
		}
		function Ie() {
			w.value = !0, e.type === "textarea" && h.value?.handleMouseEnterWrapper();
		}
		function Le() {
			w.value = !1, e.type === "textarea" && h.value?.handleMouseLeaveWrapper();
		}
		function Re() {
			x.value || ie.value === "click" && (ae.value = !ae.value);
		}
		function ze(e) {
			if (x.value) return;
			e.preventDefault();
			let t = (e) => {
				e.preventDefault(), bg("mouseup", document, t);
			};
			if (yg("mouseup", document, t), ie.value !== "mousedown") return;
			ae.value = !0;
			let n = () => {
				ae.value = !1, bg("mouseup", document, n);
			};
			yg("mouseup", document, n);
		}
		function Be(t) {
			e.onKeyup && $(e.onKeyup, t);
		}
		function Ve(t) {
			switch (e.onKeydown && $(e.onKeydown, t), t.key) {
				case "Escape":
					Ue();
					break;
				case "Enter": He(t);
			}
		}
		function He(t) {
			if (e.passivelyActivated) {
				let { value: n } = E;
				if (n) {
					e.internalDeactivateOnEnter && Ue();
					return;
				}
				t.preventDefault(), e.type === "textarea" ? c.value?.focus() : d.value?.focus();
			}
		}
		function Ue() {
			e.passivelyActivated && (E.value = !1, wn(() => {
				s.value?.focus();
			}));
		}
		function We() {
			x.value || (e.passivelyActivated ? s.value?.focus() : (c.value?.focus(), d.value?.focus()));
		}
		function Ge() {
			s.value?.contains(document.activeElement) && document.activeElement.blur();
		}
		function Ke() {
			c.value?.select(), d.value?.select();
		}
		function qe() {
			x.value || (c.value ? c.value.focus() : d.value && d.value.focus());
		}
		function Je() {
			let { value: e } = s;
			e?.contains(document.activeElement) && e !== document.activeElement && Ue();
		}
		function Ye(t) {
			if (e.type === "textarea") {
				let { value: e } = c;
				e?.scrollTo(t);
			} else {
				let { value: e } = d;
				e?.scrollTo(t);
			}
		}
		function Xe(t) {
			let { type: n, pair: r, autosize: i } = e;
			if (!r && i) {
				if (n === "textarea") {
					let { value: e } = l;
					e && (e.textContent = `${t ?? ""}\r\n`);
				} else {
					let { value: e } = u;
					e && (t ? e.textContent = t : e.innerHTML = "&nbsp;");
				}
			}
		}
		function Ze() {
			ce();
		}
		let Qe = /* @__PURE__ */ j({ top: "0" });
		function $e(e) {
			let { scrollTop: t } = e.target;
			Qe.value.top = `${-t}px`, h.value?.syncUnifiedContainer();
		}
		let et = null;
		Un(() => {
			let { autosize: t, type: n } = e;
			t && n === "textarea" ? et = Wn(v, (e) => {
				!Array.isArray(e) && e !== D && Xe(e);
			}) : et?.();
		});
		let tt = null;
		Un(() => {
			e.type === "textarea" ? tt = Wn(v, (e) => {
				!Array.isArray(e) && e !== D && h.value?.syncUnifiedContainer();
			}) : tt?.();
		}), zn(Ab, {
			mergedValueRef: v,
			maxlengthRef: le,
			mergedClsPrefixRef: t,
			countGraphemesRef: /* @__PURE__ */ M(e, "countGraphemes")
		});
		let nt = {
			wrapperElRef: s,
			inputElRef: d,
			textareaElRef: c,
			isCompositing: T,
			clear: Pe,
			focus: We,
			blur: Ge,
			select: Ke,
			deactivate: Je,
			activate: qe,
			scrollTo: Ye
		}, rt = Kg("Input", i, t), it = z(() => {
			let { value: e } = b, { common: { cubicBezierEaseInOut: t }, self: { color: n, colorHover: r, borderRadius: i, textColor: a, caretColor: s, caretColorError: c, caretColorWarning: l, textDecorationColor: u, border: d, borderDisabled: f, borderHover: p, borderFocus: m, placeholderColor: h, placeholderColorDisabled: g, lineHeightTextarea: _, colorDisabled: v, colorFocus: y, textColorDisabled: x, boxShadowFocus: S, iconSize: C, colorFocusWarning: w, boxShadowFocusWarning: T, borderWarning: E, borderFocusWarning: D, borderHoverWarning: O, colorFocusError: ee, boxShadowFocusError: te, borderError: ne, borderFocusError: re, borderHoverError: ie, clearSize: ae, clearColor: oe, clearColorHover: se, clearColorPressed: ce, iconColor: le, iconColorDisabled: ue, suffixTextColor: k, countTextColor: de, countTextColorDisabled: fe, iconColorHover: pe, iconColorPressed: me, loadingColor: he, loadingColorError: ge, loadingColorWarning: _e, fontWeight: ve, [W("padding", e)]: ye, [W("fontSize", e)]: be, [W("height", e)]: xe } } = o.value, { left: Se, right: Ce } = Wm(ye);
			return {
				"--n-bezier": t,
				"--n-count-text-color": de,
				"--n-count-text-color-disabled": fe,
				"--n-color": n,
				"--n-color-hover": r,
				"--n-font-size": be,
				"--n-font-weight": ve,
				"--n-border-radius": i,
				"--n-height": xe,
				"--n-padding-left": Se,
				"--n-padding-right": Ce,
				"--n-text-color": a,
				"--n-caret-color": s,
				"--n-text-decoration-color": u,
				"--n-border": d,
				"--n-border-disabled": f,
				"--n-border-hover": p,
				"--n-border-focus": m,
				"--n-placeholder-color": h,
				"--n-placeholder-color-disabled": g,
				"--n-icon-size": C,
				"--n-line-height-textarea": _,
				"--n-color-disabled": v,
				"--n-color-focus": y,
				"--n-text-color-disabled": x,
				"--n-box-shadow-focus": S,
				"--n-loading-color": he,
				"--n-caret-color-warning": l,
				"--n-color-focus-warning": w,
				"--n-box-shadow-focus-warning": T,
				"--n-border-warning": E,
				"--n-border-focus-warning": D,
				"--n-border-hover-warning": O,
				"--n-loading-color-warning": _e,
				"--n-caret-color-error": c,
				"--n-color-focus-error": ee,
				"--n-box-shadow-focus-error": te,
				"--n-border-error": ne,
				"--n-border-focus-error": re,
				"--n-border-hover-error": ie,
				"--n-loading-color-error": ge,
				"--n-clear-color": oe,
				"--n-clear-size": ae,
				"--n-clear-color-hover": se,
				"--n-clear-color-pressed": ce,
				"--n-icon-color": le,
				"--n-icon-color-hover": pe,
				"--n-icon-color-pressed": me,
				"--n-icon-color-disabled": ue,
				"--n-suffix-text-color": k
			};
		}), at = r ? Uh("input", z(() => {
			let { value: e } = b;
			return e[0];
		}), it, e) : void 0;
		return {
			...nt,
			wrapperElRef: s,
			inputElRef: d,
			inputMirrorElRef: u,
			inputEl2Ref: f,
			textareaElRef: c,
			textareaMirrorElRef: l,
			textareaScrollbarInstRef: h,
			rtlEnabled: rt,
			uncontrolledValue: _,
			mergedValue: v,
			passwordVisible: ae,
			mergedPlaceholder: O,
			showPlaceholder1: ee,
			showPlaceholder2: te,
			mergedFocus: ne,
			isComposing: T,
			activated: E,
			showClearButton: re,
			mergedSize: b,
			mergedDisabled: x,
			textDecorationStyle: oe,
			mergedClsPrefix: t,
			mergedBordered: n,
			mergedShowPasswordOn: ie,
			placeholderStyle: Qe,
			mergedStatus: S,
			textAreaScrollContainerWidth: se,
			handleTextAreaScroll: $e,
			handleCompositionStart: Se,
			handleCompositionEnd: Ce,
			handleInput: we,
			handleInputBlur: Ee,
			handleInputFocus: De,
			handleWrapperBlur: Oe,
			handleWrapperFocus: ke,
			handleMouseEnter: Ie,
			handleMouseLeave: Le,
			handleMouseDown: Fe,
			handleChange: je,
			handleClick: Me,
			handleClear: Ne,
			handlePasswordToggleClick: Re,
			handlePasswordToggleMousedown: ze,
			handleWrapperKeydown: Ve,
			handleWrapperKeyup: Be,
			handleTextAreaMirrorResize: Ze,
			getTextareaScrollContainer: () => c.value,
			mergedTheme: o,
			cssVars: r ? void 0 : it,
			themeClass: at?.themeClass,
			onRender: at?.onRender
		};
	},
	render() {
		let { mergedClsPrefix: e, mergedStatus: t, themeClass: n, type: r, countGraphemes: i, onRender: a } = this, o = this.$slots;
		return a?.(), F(), I("div", {
			ref: "wrapperElRef",
			class: K([
				`${e}-input`,
				`${e}-input--${this.mergedSize}-size`,
				n,
				t && `${e}-input--${t}-status`,
				{
					[`${e}-input--rtl`]: this.rtlEnabled,
					[`${e}-input--disabled`]: this.mergedDisabled,
					[`${e}-input--textarea`]: r === "textarea",
					[`${e}-input--resizable`]: this.resizable && !this.autosize,
					[`${e}-input--autosize`]: this.autosize,
					[`${e}-input--round`]: this.round && r !== "textarea",
					[`${e}-input--pair`]: this.pair,
					[`${e}-input--focus`]: this.mergedFocus,
					[`${e}-input--stateful`]: this.stateful
				}
			]),
			style: k(this.cssVars),
			tabindex: !this.mergedDisabled && this.passivelyActivated && !this.activated ? 0 : void 0,
			onFocus: this.handleWrapperFocus,
			onBlur: this.handleWrapperBlur,
			onClick: this.handleClick,
			onMousedown: this.handleMouseDown,
			onMouseenter: this.handleMouseEnter,
			onMouseleave: this.handleMouseLeave,
			onCompositionstart: this.handleCompositionStart,
			onCompositionend: this.handleCompositionEnd,
			onKeyup: this.handleWrapperKeyup,
			onKeydown: this.handleWrapperKeydown
		}, [
			R("div", { class: K(`${e}-input-wrapper`) }, [
				G(() => Wg(o.prefix, (t) => t && (F(), I("div", { class: K(`${e}-input__prefix`) }, [G(() => t)], 2)))),
				r === "textarea" ? (F(), L(ry, {
					key: 0,
					ref: "textareaScrollbarInstRef",
					class: K(`${e}-input__textarea`),
					container: this.getTextareaScrollContainer,
					theme: this.theme?.peers?.Scrollbar,
					themeOverrides: this.themeOverrides?.peers?.Scrollbar,
					triggerDisplayManually: !0,
					useUnifiedContainer: !0,
					internalHoistYRail: !0
				}, { default: () => {
					let { textAreaScrollContainerWidth: t } = this, n = { width: this.autosize && t && `${t}px` };
					return F(), I(P, null, [
						R("textarea", aa(this.inputProps, {
							ref: "textareaElRef",
							class: [`${e}-input__textarea-el`, this.inputProps?.class],
							autofocus: this.autofocus,
							rows: Number(this.rows),
							placeholder: this.placeholder,
							value: this.mergedValue,
							disabled: this.mergedDisabled,
							maxlength: i ? void 0 : this.maxlength,
							minlength: i ? void 0 : this.minlength,
							readonly: this.readonly,
							tabindex: this.passivelyActivated && !this.activated ? -1 : void 0,
							style: [
								this.textDecorationStyle[0],
								this.inputProps?.style,
								n
							],
							onBlur: this.handleInputBlur,
							onFocus: (e) => {
								this.handleInputFocus(e, 2);
							},
							onInput: this.handleInput,
							onChange: this.handleChange,
							onScroll: this.handleTextAreaScroll
						}), null, 16, Lb),
						this.showPlaceholder1 ? (F(), I("div", {
							class: K(`${e}-input__placeholder`),
							style: k([this.placeholderStyle, n]),
							key: "placeholder"
						}, [G(() => this.mergedPlaceholder[0])], 6)) : G(() => null),
						this.autosize ? (F(), L(Pv, {
							key: 2,
							onResize: this.handleTextAreaMirrorResize
						}, { default: () => (F(), I("div", {
							ref: "textareaMirrorElRef",
							class: K(`${e}-input__textarea-mirror`),
							key: "mirror"
						}, null, 2)) }, 1032, ["onResize"])) : G(() => null)
					], 64);
				} }, 1032, [
					"class",
					"container",
					"theme",
					"themeOverrides"
				])) : (F(), I("div", {
					key: 1,
					class: K(`${e}-input__input`)
				}, [
					R("input", aa({ type: r === "password" && this.mergedShowPasswordOn && this.passwordVisible ? "text" : r }, this.inputProps, {
						ref: "inputElRef",
						class: [`${e}-input__input-el`, this.inputProps?.class],
						style: [this.textDecorationStyle[0], this.inputProps?.style],
						tabindex: this.passivelyActivated && !this.activated ? -1 : this.inputProps?.tabindex,
						placeholder: this.mergedPlaceholder[0],
						disabled: this.mergedDisabled,
						maxlength: i ? void 0 : this.maxlength,
						minlength: i ? void 0 : this.minlength,
						value: Array.isArray(this.mergedValue) ? this.mergedValue[0] : this.mergedValue,
						readonly: this.readonly,
						autofocus: this.autofocus,
						size: this.attrSize,
						onBlur: this.handleInputBlur,
						onFocus: (e) => {
							this.handleInputFocus(e, 0);
						},
						onInput: (e) => {
							this.handleInput(e, 0);
						},
						onChange: (e) => {
							this.handleChange(e, 0);
						}
					}), null, 16, Rb),
					this.showPlaceholder1 ? (F(), I("div", {
						key: 0,
						class: K(`${e}-input__placeholder`)
					}, [R("span", null, [G(() => this.mergedPlaceholder[0])])], 2)) : G(() => null),
					this.autosize ? (F(), I("div", {
						class: K(`${e}-input__input-mirror`),
						key: "mirror",
						ref: "inputMirrorElRef"
					}, "\xA0", 2)) : G(() => null)
				], 2)),
				G(() => !this.pair && Wg(o.suffix, (t) => t || this.clearable || this.showCount || this.mergedShowPasswordOn || this.loading !== void 0 ? (F(), I("div", {
					key: 1,
					class: K(`${e}-input__suffix`)
				}, [G(() => [
					Wg(o["clear-icon-placeholder"], (t) => (this.clearable || t) && (F(), L(mb, {
						clsPrefix: e,
						show: this.showClearButton,
						onClear: this.handleClear
					}, {
						placeholder: () => t,
						icon: () => this.$slots["clear-icon"]?.()
					}, 1032, [
						"clsPrefix",
						"show",
						"onClear"
					]))),
					this.internalLoadingBeforeSuffix ? null : t,
					this.loading === void 0 ? null : (F(), L(wb, {
						key: 2,
						clsPrefix: e,
						loading: this.loading,
						showArrow: !1,
						showClear: !1,
						style: k(this.cssVars)
					}, null, 8, [
						"clsPrefix",
						"loading",
						"style"
					])),
					this.internalLoadingBeforeSuffix ? t : null,
					this.showCount && this.type !== "textarea" ? (F(), L(Ib, { key: 3 }, { default: (e) => {
						let { renderCount: t } = this;
						return t ? t(e) : o.count?.(e);
					} }, 1024)) : null,
					this.mergedShowPasswordOn && this.type === "password" ? (F(), I("div", {
						key: 4,
						class: K(`${e}-input__eye`),
						onMousedown: this.handlePasswordToggleMousedown,
						onClick: this.handlePasswordToggleClick
					}, [this.passwordVisible ? (F(), I(P, { key: 0 }, [G(() => Hg(o["password-visible-icon"], () => [(F(), L(Yh, { clsPrefix: e }, { default: () => (F(), L(ob)) }, 1032, ["clsPrefix"]))]))], 64)) : (F(), I(P, { key: 1 }, [G(() => Hg(o["password-invisible-icon"], () => [(F(), L(Yh, { clsPrefix: e }, { default: () => (F(), L(sb)) }, 1032, ["clsPrefix"]))]))], 64))], 42, zb)) : null
				])], 2)) : null))
			], 2),
			this.pair ? (F(), I("span", {
				key: 0,
				class: K(`${e}-input__separator`)
			}, [G(() => Hg(o.separator, () => [this.separator]))], 2)) : G(() => null),
			this.pair ? (F(), I("div", {
				key: 2,
				class: K(`${e}-input-wrapper`)
			}, [R("div", { class: K(`${e}-input__input`) }, [R("input", {
				ref: "inputEl2Ref",
				type: this.type,
				class: K(`${e}-input__input-el`),
				tabindex: this.passivelyActivated && !this.activated ? -1 : void 0,
				placeholder: this.mergedPlaceholder[1],
				disabled: this.mergedDisabled,
				maxlength: i ? void 0 : this.maxlength,
				minlength: i ? void 0 : this.minlength,
				value: Array.isArray(this.mergedValue) ? this.mergedValue[1] : void 0,
				readonly: this.readonly,
				style: k(this.textDecorationStyle[1]),
				onBlur: this.handleInputBlur,
				onFocus: (e) => {
					this.handleInputFocus(e, 1);
				},
				onInput: (e) => {
					this.handleInput(e, 1);
				},
				onChange: (e) => {
					this.handleChange(e, 1);
				}
			}, null, 46, Bb), this.showPlaceholder2 ? (F(), I("div", {
				key: 0,
				class: K(`${e}-input__placeholder`)
			}, [R("span", null, [G(() => this.mergedPlaceholder[1])])], 2)) : G(() => null)], 2), G(() => Wg(o.suffix, (t) => (this.clearable || t) && (F(), I("div", { class: K(`${e}-input__suffix`) }, [G(() => [this.clearable && (F(), L(mb, {
				clsPrefix: e,
				show: this.showClearButton,
				onClear: this.handleClear
			}, {
				icon: () => o["clear-icon"]?.(),
				placeholder: () => o["clear-icon-placeholder"]?.()
			}, 1032, [
				"clsPrefix",
				"show",
				"onClear"
			])), t])], 2))))], 2)) : G(() => null),
			this.mergedBordered ? (F(), I("div", {
				key: 4,
				class: K(`${e}-input__border`)
			}, null, 2)) : G(() => null),
			this.mergedBordered ? (F(), I("div", {
				key: 6,
				class: K(`${e}-input__state-border`)
			}, null, 2)) : G(() => null),
			this.showCount && r === "textarea" ? (F(), L(Ib, { key: 8 }, { default: (e) => {
				let { renderCount: t } = this;
				return t ? t(e) : o.count?.(e);
			} }, 1024)) : G(() => null)
		], 46, Vb);
	}
});
//#endregion
//#region node_modules/naive-ui/es/auto-complete/styles/light.mjs
function Ub(e) {
	let { boxShadow2: t } = e;
	return { menuBoxShadow: t };
}
//#endregion
//#region node_modules/naive-ui/es/auto-complete/styles/dark.mjs
var Wb = {
	name: "AutoComplete",
	common: Z,
	peers: {
		InternalSelectMenu: ng,
		Input: rb
	},
	self: Ub
};
//#endregion
//#region node_modules/naive-ui/es/_utils/composable/use-resize.mjs
function Gb(e, t) {
	t && (Fr(() => {
		let { value: n } = e;
		n && Nv.registerHandler(n, t);
	}), Wn(e, (e, t) => {
		t && Nv.unregisterHandler(t);
	}, { deep: !1 }), Lr(() => {
		let { value: t } = e;
		t && Nv.unregisterHandler(t);
	}));
}
//#endregion
//#region node_modules/naive-ui/es/_internal/focus-detector/index.mjs
var Kb = /* @__PURE__ */ N({
	props: {
		onFocus: Function,
		onBlur: Function
	},
	setup(e) {
		return () => (() => {
			let t = jm("d16ead82505dc285");
			return F(), I("div", {
				style: "width: 0; height: 0",
				tabindex: 0,
				onFocus: t[0] ||= (t) => e.onFocus?.(t),
				onBlur: t[1] ||= (t) => e.onBlur?.(t)
			}, null, 32);
		})();
	}
});
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/render.mjs
function qb(e, ...t) {
	return typeof e == "function" ? e(...t) : typeof e == "string" ? ea(e) : typeof e == "number" ? ea(String(e)) : null;
}
//#endregion
//#region node_modules/naive-ui/es/_internal/select-menu/src/SelectGroupHeader.mjs
var Jb = /* @__PURE__ */ N({
	name: "NBaseSelectGroupHeader",
	props: {
		clsPrefix: {
			type: String,
			required: !0
		},
		tmNode: {
			type: Object,
			required: !0
		}
	},
	setup() {
		let { renderLabelRef: e, renderOptionRef: t, labelFieldRef: n, nodePropsRef: r } = Bn(sg);
		return {
			labelField: n,
			nodeProps: r,
			renderLabel: e,
			renderOption: t
		};
	},
	render() {
		let { clsPrefix: e, renderLabel: t, renderOption: n, nodeProps: r, tmNode: { rawNode: i } } = this, a = r?.(i), o = t ? t(i, !1) : qb(i[this.labelField], i, !1), s = (F(), I("div", aa(a, { class: [`${e}-base-select-group-header`, a?.class] }), [G(() => o)], 16));
		return i.render ? i.render({
			node: s,
			option: i
		}) : n ? n({
			node: s,
			option: i,
			selected: !1
		}) : s;
	}
});
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/merge-handlers.mjs
function Yb(e) {
	let t = e.filter((e) => e !== void 0);
	if (t.length !== 0) return t.length === 1 ? t[0] : (t) => {
		e.forEach((e) => {
			e && e(t);
		});
	};
}
//#endregion
//#region node_modules/naive-ui/es/_internal/icons/Checkmark.mjs
var Xb = /* @__PURE__ */ N({
	name: "Checkmark",
	render() {
		return (() => {
			let e = jm("3c84eac8ae4e1f96");
			return e[0] ||= R("svg", {
				xmlns: "http://www.w3.org/2000/svg",
				viewBox: "0 0 16 16"
			}, [R("g", { fill: "none" }, [R("path", {
				d: "M14.046 3.486a.75.75 0 0 1-.032 1.06l-7.93 7.474a.85.85 0 0 1-1.188-.022l-2.68-2.72a.75.75 0 1 1 1.068-1.053l2.234 2.267l7.468-7.038a.75.75 0 0 1 1.06.032z",
				fill: "currentColor"
			})])], -1);
		})();
	}
}), Zb = [
	"onClick",
	"onMouseenter",
	"onMousemove"
];
function Qb(e, t) {
	return F(), L(Va, { name: "fade-in-scale-up-transition" }, { default: () => e ? (F(), L(Yh, {
		key: 1,
		clsPrefix: t,
		class: K(`${t}-base-select-option__check`)
	}, { default: () => Ea(Xb) }, 1032, ["clsPrefix", "class"])) : null }, 1024);
}
var $b = /* @__PURE__ */ N({
	name: "NBaseSelectOption",
	props: {
		clsPrefix: {
			type: String,
			required: !0
		},
		tmNode: {
			type: Object,
			required: !0
		}
	},
	setup(e) {
		let { valueRef: t, pendingTmNodeRef: n, multipleRef: r, valueSetRef: i, renderLabelRef: a, renderOptionRef: o, labelFieldRef: s, valueFieldRef: c, showCheckmarkRef: l, nodePropsRef: u, handleOptionClick: d, handleOptionMouseEnter: f } = Bn(sg), p = Sg(() => {
			let { value: t } = n;
			return t ? e.tmNode.key === t.key : !1;
		});
		function m(t) {
			let { tmNode: n } = e;
			n.disabled || d(t, n);
		}
		function h(t) {
			let { tmNode: n } = e;
			n.disabled || f(t, n);
		}
		function g(t) {
			let { tmNode: n } = e, { value: r } = p;
			n.disabled || r || f(t, n);
		}
		return {
			multiple: r,
			isGrouped: Sg(() => {
				let { tmNode: t } = e, { parent: n } = t;
				return n && n.rawNode.type === "group";
			}),
			showCheckmark: l,
			nodeProps: u,
			isPending: p,
			isSelected: Sg(() => {
				let { value: n } = t, { value: a } = r;
				if (n === null) return !1;
				let o = e.tmNode.rawNode[c.value];
				if (a) {
					let { value: e } = i;
					return e.has(o);
				}
				return n === o;
			}),
			labelField: s,
			renderLabel: a,
			renderOption: o,
			handleMouseMove: g,
			handleMouseEnter: h,
			handleClick: m
		};
	},
	render() {
		let { clsPrefix: e, tmNode: { rawNode: t }, isSelected: n, isPending: r, isGrouped: i, showCheckmark: a, nodeProps: o, renderOption: s, renderLabel: c, handleClick: l, handleMouseEnter: u, handleMouseMove: d } = this, f = Qb(n, e), p = c ? [c(t, n), a && f] : [qb(t[this.labelField], t, n), a && f], m = o?.(t), h = (F(), I("div", aa(m, {
			class: [
				`${e}-base-select-option`,
				t.class,
				m?.class,
				{
					[`${e}-base-select-option--disabled`]: t.disabled,
					[`${e}-base-select-option--selected`]: n,
					[`${e}-base-select-option--grouped`]: i,
					[`${e}-base-select-option--pending`]: r,
					[`${e}-base-select-option--show-checkmark`]: a
				}
			],
			style: [m?.style || "", t.style || ""],
			onClick: Yb([l, m?.onClick]),
			onMouseenter: Yb([u, m?.onMouseenter]),
			onMousemove: Yb([d, m?.onMousemove])
		}), [R("div", { class: K(`${e}-base-select-option__content`) }, [G(() => p)], 2)], 16, Zb));
		return t.render ? t.render({
			node: h,
			option: t,
			selected: n
		}) : s ? s({
			node: h,
			option: t,
			selected: n
		}) : h;
	}
}), { cubicBezierEaseIn: ex, cubicBezierEaseOut: tx } = ym;
function nx({ transformOrigin: e = "inherit", duration: t = ".2s", enterScale: n = ".9", originalTransform: r = "", originalTransition: i = "" } = {}) {
	return [
		B("&.fade-in-scale-up-transition-leave-active", {
			transformOrigin: e,
			transition: `opacity ${t} ${ex}, transform ${t} ${ex} ${i && `,${i}`}`
		}),
		B("&.fade-in-scale-up-transition-enter-active", {
			transformOrigin: e,
			transition: `opacity ${t} ${tx}, transform ${t} ${tx} ${i && `,${i}`}`
		}),
		B("&.fade-in-scale-up-transition-enter-from, &.fade-in-scale-up-transition-leave-to", {
			opacity: 0,
			transform: `${r} scale(${n})`
		}),
		B("&.fade-in-scale-up-transition-leave-from, &.fade-in-scale-up-transition-enter-to", {
			opacity: 1,
			transform: `${r} scale(1)`
		})
	];
}
//#endregion
//#region node_modules/naive-ui/es/_internal/select-menu/src/styles/index.cssr.mjs
var rx = V("base-select-menu", "\n line-height: 1.5;\n outline: none;\n z-index: 0;\n position: relative;\n border-radius: var(--n-border-radius);\n transition:\n background-color .3s var(--n-bezier),\n box-shadow .3s var(--n-bezier);\n background-color: var(--n-color);\n", [
	V("scrollbar", "\n max-height: var(--n-height);\n "),
	V("virtual-list", "\n max-height: var(--n-height);\n "),
	V("base-select-option", "\n min-height: var(--n-option-height);\n font-size: var(--n-option-font-size);\n display: flex;\n align-items: center;\n ", [H("content", "\n z-index: 1;\n white-space: nowrap;\n text-overflow: ellipsis;\n overflow: hidden;\n ")]),
	V("base-select-group-header", "\n min-height: var(--n-option-height);\n font-size: .93em;\n display: flex;\n align-items: center;\n "),
	V("base-select-menu-option-wrapper", "\n position: relative;\n width: 100%;\n "),
	H("loading, empty", "\n display: flex;\n padding: 12px 32px;\n flex: 1;\n justify-content: center;\n "),
	H("loading", "\n color: var(--n-loading-color);\n font-size: var(--n-loading-size);\n "),
	H("header", "\n padding: 8px var(--n-option-padding-left);\n font-size: var(--n-option-font-size);\n transition: \n color .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n border-bottom: 1px solid var(--n-action-divider-color);\n color: var(--n-action-text-color);\n "),
	H("action", "\n padding: 8px var(--n-option-padding-left);\n font-size: var(--n-option-font-size);\n transition: \n color .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n border-top: 1px solid var(--n-action-divider-color);\n color: var(--n-action-text-color);\n "),
	V("base-select-group-header", "\n position: relative;\n cursor: default;\n padding: var(--n-option-padding);\n color: var(--n-group-header-text-color);\n "),
	V("base-select-option", "\n cursor: pointer;\n position: relative;\n padding: var(--n-option-padding);\n transition:\n color .3s var(--n-bezier),\n opacity .3s var(--n-bezier);\n box-sizing: border-box;\n color: var(--n-option-text-color);\n opacity: 1;\n ", [
		U("show-checkmark", "\n padding-right: calc(var(--n-option-padding-right) + 20px);\n "),
		B("&::before", "\n content: \"\";\n position: absolute;\n left: 4px;\n right: 4px;\n top: 0;\n bottom: 0;\n border-radius: var(--n-border-radius);\n transition: background-color .3s var(--n-bezier);\n "),
		B("&:active", "\n color: var(--n-option-text-color-pressed);\n "),
		U("grouped", "\n padding-left: calc(var(--n-option-padding-left) * 1.5);\n "),
		U("pending", [B("&::before", "\n background-color: var(--n-option-color-pending);\n ")]),
		U("selected", "\n color: var(--n-option-text-color-active);\n ", [B("&::before", "\n background-color: var(--n-option-color-active);\n "), U("pending", [B("&::before", "\n background-color: var(--n-option-color-active-pending);\n ")])]),
		U("disabled", "\n cursor: not-allowed;\n ", [Ns("selected", "\n color: var(--n-option-text-color-disabled);\n "), U("selected", "\n opacity: var(--n-option-opacity-disabled);\n ")]),
		H("check", "\n font-size: 16px;\n position: absolute;\n right: calc(var(--n-option-padding-right) - 4px);\n top: calc(50% - 7px);\n color: var(--n-option-check-color);\n transition: color .3s var(--n-bezier);\n ", [nx({ enterScale: "0.5" })])
	])
]);
//#endregion
//#region node_modules/treemate/es/utils.js
function ix(e) {
	return Array.isArray(e) ? e : [e];
}
var ax = { STOP: "STOP" };
function ox(e, t) {
	let n = t(e);
	e.children !== void 0 && n !== ax.STOP && e.children.forEach((e) => ox(e, t));
}
function sx(e, t = {}) {
	let { preserveGroup: n = !1 } = t, r = [], i = n ? (e) => {
		e.isLeaf || (r.push(e.key), a(e.children));
	} : (e) => {
		e.isLeaf || (e.isGroup || r.push(e.key), a(e.children));
	};
	function a(e) {
		e.forEach(i);
	}
	return a(e), r;
}
function cx(e, t) {
	let { isLeaf: n } = e;
	return n === void 0 ? !t(e) : n;
}
function lx(e) {
	return e.children;
}
function ux(e) {
	return e.key;
}
function dx() {
	return !1;
}
function fx(e, t) {
	let { isLeaf: n } = e;
	return !(n === !1 && !Array.isArray(t(e)));
}
function px(e) {
	return e.disabled === !0;
}
function mx(e, t) {
	return e.isLeaf === !1 && !Array.isArray(t(e));
}
function hx(e) {
	return e == null ? [] : Array.isArray(e) ? e : e.checkedKeys ?? [];
}
function gx(e) {
	return e == null || Array.isArray(e) ? [] : e.indeterminateKeys ?? [];
}
function _x(e, t) {
	let n = new Set(e);
	return t.forEach((e) => {
		n.has(e) || n.add(e);
	}), Array.from(n);
}
function vx(e, t) {
	let n = new Set(e);
	return t.forEach((e) => {
		n.has(e) && n.delete(e);
	}), Array.from(n);
}
function yx(e) {
	return e?.type === "group";
}
function bx(e) {
	let t = /* @__PURE__ */ new Map();
	return e.forEach((e, n) => {
		t.set(e.key, n);
	}), (e) => t.get(e) ?? null;
}
//#endregion
//#region node_modules/treemate/es/check.js
var xx = class extends Error {
	constructor() {
		super(), this.message = "SubtreeNotLoadedError: checking a subtree whose required nodes are not fully loaded.";
	}
};
function Sx(e, t, n, r) {
	return Ex(t.concat(e), n, r, !1);
}
function Cx(e, t) {
	let n = /* @__PURE__ */ new Set();
	return e.forEach((e) => {
		let r = t.treeNodeMap.get(e);
		if (r !== void 0) {
			let e = r.parent;
			for (; e !== null && !(e.disabled || n.has(e.key));) n.add(e.key), e = e.parent;
		}
	}), n;
}
function wx(e, t, n, r) {
	let i = Ex(t, n, r, !1), a = Ex(e, n, r, !0), o = Cx(e, n), s = [];
	return i.forEach((e) => {
		(a.has(e) || o.has(e)) && s.push(e);
	}), s.forEach((e) => i.delete(e)), i;
}
function Tx(e, t) {
	let { checkedKeys: n, keysToCheck: r, keysToUncheck: i, indeterminateKeys: a, cascade: o, leafOnly: s, checkStrategy: c, allowNotLoaded: l } = e;
	if (!o) return r === void 0 ? i === void 0 ? {
		checkedKeys: Array.from(n),
		indeterminateKeys: Array.from(a)
	} : {
		checkedKeys: vx(n, i),
		indeterminateKeys: Array.from(a)
	} : {
		checkedKeys: _x(n, r),
		indeterminateKeys: Array.from(a)
	};
	let { levelTreeNodeMap: u } = t, d;
	d = i === void 0 ? r === void 0 ? Ex(n, t, l, !1) : Sx(r, n, t, l) : wx(i, n, t, l);
	let f = c === "parent", p = c === "child" || s, m = d, h = /* @__PURE__ */ new Set(), g = Math.max.apply(null, Array.from(u.keys()));
	for (let e = g; e >= 0; --e) {
		let t = e === 0, n = u.get(e);
		for (let e of n) {
			if (e.isLeaf) continue;
			let { key: n, shallowLoaded: r } = e;
			if (p && r && e.children.forEach((e) => {
				!e.disabled && !e.isLeaf && e.shallowLoaded && m.has(e.key) && m.delete(e.key);
			}), e.disabled || !r) continue;
			let i = !0, a = !1, o = !0;
			for (let t of e.children) {
				let e = t.key;
				if (!t.disabled) {
					if (o &&= !1, m.has(e)) a = !0;
					else if (h.has(e)) {
						a = !0, i = !1;
						break;
					} else if (i = !1, a) break;
				}
			}
			i && !o ? (f && e.children.forEach((e) => {
				!e.disabled && m.has(e.key) && m.delete(e.key);
			}), m.add(n)) : a && h.add(n), t && p && m.has(n) && m.delete(n);
		}
	}
	return {
		checkedKeys: Array.from(m),
		indeterminateKeys: Array.from(h)
	};
}
function Ex(e, t, n, r) {
	let { treeNodeMap: i, getChildren: a } = t, o = /* @__PURE__ */ new Set(), s = new Set(e);
	return e.forEach((e) => {
		let t = i.get(e);
		t !== void 0 && ox(t, (e) => {
			if (e.disabled) return ax.STOP;
			let { key: t } = e;
			if (!o.has(t) && (o.add(t), s.add(t), mx(e.rawNode, a))) {
				if (r) return ax.STOP;
				if (!n) throw new xx();
			}
		});
	}), s;
}
//#endregion
//#region node_modules/treemate/es/path.js
function Dx(e, { includeGroup: t = !1, includeSelf: n = !0 }, r) {
	let i = r.treeNodeMap, a = e == null ? null : i.get(e) ?? null, o = {
		keyPath: [],
		treeNodePath: [],
		treeNode: a
	};
	if (a?.ignored) return o.treeNode = null, o;
	for (; a;) !a.ignored && (t || !a.isGroup) && o.treeNodePath.push(a), a = a.parent;
	return o.treeNodePath.reverse(), n || o.treeNodePath.pop(), o.keyPath = o.treeNodePath.map((e) => e.key), o;
}
//#endregion
//#region node_modules/treemate/es/move.js
function Ox(e) {
	if (e.length === 0) return null;
	let t = e[0];
	return t.isGroup || t.ignored || t.disabled ? t.getNext() : t;
}
function kx(e, t) {
	let n = e.siblings, r = n.length, { index: i } = e;
	return t ? n[(i + 1) % r] : i === n.length - 1 ? null : n[i + 1];
}
function Ax(e, t, { loop: n = !1, includeDisabled: r = !1 } = {}) {
	let i = t === "prev" ? jx : kx, a = { reverse: t === "prev" }, o = !1, s = null;
	function c(t) {
		if (t !== null) {
			if (t === e) {
				if (!o) o = !0;
				else if (!e.disabled && !e.isGroup) {
					s = e;
					return;
				}
			} else if ((!t.disabled || r) && !t.ignored && !t.isGroup) {
				s = t;
				return;
			}
			if (t.isGroup) {
				let e = Nx(t, a);
				e === null ? c(i(t, n)) : s = e;
			} else {
				let e = i(t, !1);
				if (e !== null) c(e);
				else {
					let e = Mx(t);
					e?.isGroup ? c(i(e, n)) : n && c(i(t, !0));
				}
			}
		}
	}
	return c(e), s;
}
function jx(e, t) {
	let n = e.siblings, r = n.length, { index: i } = e;
	return t ? n[(i - 1 + r) % r] : i === 0 ? null : n[i - 1];
}
function Mx(e) {
	return e.parent;
}
function Nx(e, t = {}) {
	let { reverse: n = !1 } = t, { children: r } = e;
	if (r) {
		let { length: e } = r, i = n ? e - 1 : 0, a = n ? -1 : e, o = n ? -1 : 1;
		for (let e = i; e !== a; e += o) {
			let n = r[e];
			if (!n.disabled && !n.ignored) {
				if (n.isGroup) {
					let e = Nx(n, t);
					if (e !== null) return e;
				} else return n;
			}
		}
	}
	return null;
}
var Px = {
	getChild() {
		return this.ignored ? null : Nx(this);
	},
	getParent() {
		let { parent: e } = this;
		return e?.isGroup ? e.getParent() : e;
	},
	getNext(e = {}) {
		return Ax(this, "next", e);
	},
	getPrev(e = {}) {
		return Ax(this, "prev", e);
	}
};
//#endregion
//#region node_modules/treemate/es/flatten.js
function Fx(e, t) {
	let n = t ? new Set(t) : void 0, r = [];
	function i(e) {
		e.forEach((e) => {
			r.push(e), !(e.isLeaf || !e.children || e.ignored) && (e.isGroup || n === void 0 || n.has(e.key)) && i(e.children);
		});
	}
	return i(e), r;
}
//#endregion
//#region node_modules/treemate/es/contains.js
function Ix(e, t) {
	let n = e.key;
	for (; t;) {
		if (t.key === n) return !0;
		t = t.parent;
	}
	return !1;
}
//#endregion
//#region node_modules/treemate/es/create.js
function Lx(e, t, n, r, i, a = null, o = 0) {
	let s = [];
	return e.forEach((c, l) => {
		var u;
		let d = Object.create(r);
		if (d.rawNode = c, d.siblings = s, d.level = o, d.index = l, d.isFirstChild = l === 0, d.isLastChild = l + 1 === e.length, d.parent = a, !d.ignored) {
			let e = i(c);
			Array.isArray(e) && (d.children = Lx(e, t, n, r, i, d, o + 1));
		}
		s.push(d), t.set(d.key, d), n.has(o) || n.set(o, []), (u = n.get(o)) == null || u.push(d);
	}), s;
}
function Rx(e, t = {}) {
	let n = /* @__PURE__ */ new Map(), r = /* @__PURE__ */ new Map(), { getDisabled: i = px, getIgnored: a = dx, getIsGroup: o = yx, getKey: s = ux } = t, c = t.getChildren ?? lx, l = t.ignoreEmptyChildren ? (e) => {
		let t = c(e);
		return Array.isArray(t) ? t.length ? t : null : t;
	} : c, u = Lx(e, n, r, Object.assign({
		get key() {
			return s(this.rawNode);
		},
		get disabled() {
			return i(this.rawNode);
		},
		get isGroup() {
			return o(this.rawNode);
		},
		get isLeaf() {
			return cx(this.rawNode, l);
		},
		get shallowLoaded() {
			return fx(this.rawNode, l);
		},
		get ignored() {
			return a(this.rawNode);
		},
		contains(e) {
			return Ix(this, e);
		}
	}, Px), l);
	function d(e) {
		if (e == null) return null;
		let t = n.get(e);
		return t && !t.isGroup && !t.ignored ? t : null;
	}
	function f(e) {
		if (e == null) return null;
		let t = n.get(e);
		return t && !t.ignored ? t : null;
	}
	function p(e, t) {
		let n = f(e);
		return n ? n.getPrev(t) : null;
	}
	function m(e, t) {
		let n = f(e);
		return n ? n.getNext(t) : null;
	}
	function h(e) {
		let t = f(e);
		return t ? t.getParent() : null;
	}
	function g(e) {
		let t = f(e);
		return t ? t.getChild() : null;
	}
	let _ = {
		treeNodes: u,
		treeNodeMap: n,
		levelTreeNodeMap: r,
		maxLevel: Math.max(...r.keys()),
		getChildren: l,
		getFlattenedNodes(e) {
			return Fx(u, e);
		},
		getNode: d,
		getPrev: p,
		getNext: m,
		getParent: h,
		getChild: g,
		getFirstAvailableNode() {
			return Ox(u);
		},
		getPath(e, t = {}) {
			return Dx(e, t, _);
		},
		getCheckedKeys(e, t = {}) {
			let { cascade: n = !0, leafOnly: r = !1, checkStrategy: i = "all", allowNotLoaded: a = !1 } = t;
			return Tx({
				checkedKeys: hx(e),
				indeterminateKeys: gx(e),
				cascade: n,
				leafOnly: r,
				checkStrategy: i,
				allowNotLoaded: a
			}, _);
		},
		check(e, t, n = {}) {
			let { cascade: r = !0, leafOnly: i = !1, checkStrategy: a = "all", allowNotLoaded: o = !1 } = n;
			return Tx({
				checkedKeys: hx(t),
				indeterminateKeys: gx(t),
				keysToCheck: e == null ? [] : ix(e),
				cascade: r,
				leafOnly: i,
				checkStrategy: a,
				allowNotLoaded: o
			}, _);
		},
		uncheck(e, t, n = {}) {
			let { cascade: r = !0, leafOnly: i = !1, checkStrategy: a = "all", allowNotLoaded: o = !1 } = n;
			return Tx({
				checkedKeys: hx(t),
				indeterminateKeys: gx(t),
				keysToUncheck: e == null ? [] : ix(e),
				cascade: r,
				leafOnly: i,
				checkStrategy: a,
				allowNotLoaded: o
			}, _);
		},
		getNonLeafKeys(e = {}) {
			return sx(u, e);
		}
	};
	return _;
}
//#endregion
//#region node_modules/naive-ui/es/_internal/select-menu/src/SelectMenu.mjs
var zx = [
	"tabindex",
	"onFocusin",
	"onFocusout",
	"onKeyup",
	"onKeydown",
	"onMousedown",
	"onMouseenter",
	"onMouseleave"
], Bx = /* @__PURE__ */ N({
	name: "InternalSelectMenu",
	props: {
		...Kh.props,
		clsPrefix: {
			type: String,
			required: !0
		},
		scrollable: {
			type: Boolean,
			default: !0
		},
		treeMate: {
			type: Object,
			required: !0
		},
		multiple: Boolean,
		size: {
			type: String,
			default: "medium"
		},
		value: {
			type: [
				String,
				Number,
				Array
			],
			default: null
		},
		autoPending: Boolean,
		virtualScroll: {
			type: Boolean,
			default: !0
		},
		show: {
			type: Boolean,
			default: !0
		},
		labelField: {
			type: String,
			default: "label"
		},
		valueField: {
			type: String,
			default: "value"
		},
		loading: Boolean,
		focusable: Boolean,
		renderLabel: Function,
		renderOption: Function,
		nodeProps: Function,
		showCheckmark: {
			type: Boolean,
			default: !0
		},
		onMousedown: Function,
		onScroll: Function,
		onFocus: Function,
		onBlur: Function,
		onKeyup: Function,
		onKeydown: Function,
		onTabOut: Function,
		onMouseenter: Function,
		onMouseleave: Function,
		onResize: Function,
		resetMenuOnOptionsChange: {
			type: Boolean,
			default: !0
		},
		inlineThemeDisabled: Boolean,
		scrollbarProps: Object,
		onToggle: Function
	},
	setup(e) {
		let { mergedClsPrefixRef: t, mergedRtlRef: n, mergedComponentPropsRef: r } = _m(e), i = Kg("InternalSelectMenu", n, t), a = Kh("InternalSelectMenu", "-internal-select-menu", rx, tg, e, /* @__PURE__ */ M(e, "clsPrefix")), o = /* @__PURE__ */ j(null), s = /* @__PURE__ */ j(null), c = /* @__PURE__ */ j(null), l = z(() => e.treeMate.getFlattenedNodes()), u = z(() => bx(l.value)), d = /* @__PURE__ */ j(null);
		function f() {
			let { treeMate: t } = e, n = null, { value: r } = e;
			r === null ? n = t.getFirstAvailableNode() : (n = e.multiple ? t.getNode((r || [])[(r || []).length - 1]) : t.getNode(r), (!n || n.disabled) && (n = t.getFirstAvailableNode())), re(n || null);
		}
		function p() {
			let { value: t } = d;
			t && !e.treeMate.getNode(t.key) && (d.value = null);
		}
		let m;
		Wn(() => e.show, (t) => {
			t ? m = Wn(() => e.treeMate, () => {
				e.resetMenuOnOptionsChange ? (e.autoPending ? f() : p(), wn(ie)) : p();
			}, { immediate: !0 }) : m?.();
		}, { immediate: !0 }), Lr(() => {
			m?.();
		});
		let h = z(() => Hm(a.value.self[W("optionHeight", e.size)])), g = z(() => Wm(a.value.self[W("padding", e.size)])), _ = z(() => e.multiple && Array.isArray(e.value) ? new Set(e.value) : /* @__PURE__ */ new Set()), v = z(() => {
			let e = l.value;
			return e && e.length === 0;
		}), y = z(() => r?.value?.Select?.renderEmpty);
		function b(t) {
			let { onToggle: n } = e;
			n && n(t);
		}
		function x(t) {
			let { onScroll: n } = e;
			n && n(t);
		}
		function S(e) {
			c.value?.sync(), x(e);
		}
		function C() {
			c.value?.sync();
		}
		function w() {
			let { value: e } = d;
			return e || null;
		}
		function T(e, t) {
			t.disabled || re(t, !1);
		}
		function E(e, t) {
			t.disabled || b(t);
		}
		function D(t) {
			Bm(t, "action") || e.onKeyup?.(t);
		}
		function O(t) {
			Bm(t, "action") || e.onKeydown?.(t);
		}
		function ee(t) {
			e.onMousedown?.(t), !e.focusable && t.preventDefault();
		}
		function te() {
			let { value: e } = d;
			e && re(e.getNext({ loop: !0 }), !0);
		}
		function ne() {
			let { value: e } = d;
			e && re(e.getPrev({ loop: !0 }), !0);
		}
		function re(e, t = !1) {
			d.value = e, t && ie();
		}
		function ie() {
			let t = d.value;
			if (!t) return;
			let n = u.value(t.key);
			n !== null && (e.virtualScroll ? s.value?.scrollTo({ index: n }) : c.value?.scrollTo({
				index: n,
				elSize: h.value
			}));
		}
		function ae(t) {
			o.value?.contains(t.target) && e.onFocus?.(t);
		}
		function oe(t) {
			o.value?.contains(t.relatedTarget) || e.onBlur?.(t);
		}
		zn(sg, {
			handleOptionMouseEnter: T,
			handleOptionClick: E,
			valueSetRef: _,
			pendingTmNodeRef: d,
			nodePropsRef: /* @__PURE__ */ M(e, "nodeProps"),
			showCheckmarkRef: /* @__PURE__ */ M(e, "showCheckmark"),
			multipleRef: /* @__PURE__ */ M(e, "multiple"),
			valueRef: /* @__PURE__ */ M(e, "value"),
			renderLabelRef: /* @__PURE__ */ M(e, "renderLabel"),
			renderOptionRef: /* @__PURE__ */ M(e, "renderOption"),
			labelFieldRef: /* @__PURE__ */ M(e, "labelField"),
			valueFieldRef: /* @__PURE__ */ M(e, "valueField")
		}), zn(cg, o), Fr(() => {
			let { value: e } = c;
			e && e.sync();
		});
		let se = z(() => {
			let { size: t } = e, { common: { cubicBezierEaseInOut: n }, self: { height: r, borderRadius: i, color: o, groupHeaderTextColor: s, actionDividerColor: c, optionTextColorPressed: l, optionTextColor: u, optionTextColorDisabled: d, optionTextColorActive: f, optionOpacityDisabled: p, optionCheckColor: m, actionTextColor: h, optionColorPending: g, optionColorActive: _, loadingColor: v, loadingSize: y, optionColorActivePending: b, [W("optionFontSize", t)]: x, [W("optionHeight", t)]: S, [W("optionPadding", t)]: C } } = a.value;
			return {
				"--n-height": r,
				"--n-action-divider-color": c,
				"--n-action-text-color": h,
				"--n-bezier": n,
				"--n-border-radius": i,
				"--n-color": o,
				"--n-option-font-size": x,
				"--n-group-header-text-color": s,
				"--n-option-check-color": m,
				"--n-option-color-pending": g,
				"--n-option-color-active": _,
				"--n-option-color-active-pending": b,
				"--n-option-height": S,
				"--n-option-opacity-disabled": p,
				"--n-option-text-color": u,
				"--n-option-text-color-active": f,
				"--n-option-text-color-disabled": d,
				"--n-option-text-color-pressed": l,
				"--n-option-padding": C,
				"--n-option-padding-left": Wm(C, "left"),
				"--n-option-padding-right": Wm(C, "right"),
				"--n-loading-color": v,
				"--n-loading-size": y
			};
		}), { inlineThemeDisabled: ce } = e, le = ce ? Uh("internal-select-menu", z(() => e.size[0]), se, e) : void 0, ue = {
			selfRef: o,
			next: te,
			prev: ne,
			getPendingTmNode: w
		};
		return Gb(o, e.onResize), {
			mergedTheme: a,
			mergedClsPrefix: t,
			rtlEnabled: i,
			virtualListRef: s,
			scrollbarRef: c,
			itemSize: h,
			padding: g,
			flattenedNodes: l,
			empty: v,
			mergedRenderEmpty: y,
			virtualListContainer() {
				let { value: e } = s;
				return e?.listElRef;
			},
			virtualListContent() {
				let { value: e } = s;
				return e?.itemsElRef;
			},
			doScroll: x,
			handleFocusin: ae,
			handleFocusout: oe,
			handleKeyUp: D,
			handleKeyDown: O,
			handleMouseDown: ee,
			handleVirtualListResize: C,
			handleVirtualListScroll: S,
			cssVars: ce ? void 0 : se,
			themeClass: le?.themeClass,
			onRender: le?.onRender,
			...ue
		};
	},
	render() {
		let { $slots: e, virtualScroll: t, clsPrefix: n, mergedTheme: r, themeClass: i, onRender: a } = this;
		return a?.(), F(), I("div", {
			ref: "selfRef",
			tabindex: this.focusable ? 0 : -1,
			class: K([
				`${n}-base-select-menu`,
				`${n}-base-select-menu--${this.size}-size`,
				this.rtlEnabled && `${n}-base-select-menu--rtl`,
				i,
				this.multiple && `${n}-base-select-menu--multiple`
			]),
			style: k(this.cssVars),
			onFocusin: this.handleFocusin,
			onFocusout: this.handleFocusout,
			onKeyup: this.handleKeyUp,
			onKeydown: this.handleKeyDown,
			onMousedown: this.handleMouseDown,
			onMouseenter: this.onMouseenter,
			onMouseleave: this.onMouseleave
		}, [
			G(() => Wg(e.header, (e) => e && (F(), I("div", {
				class: K(`${n}-base-select-menu__header`),
				"data-header": !0,
				key: "header"
			}, [G(() => e)], 2)))),
			this.loading ? (F(), I("div", {
				key: 0,
				class: K(`${n}-base-select-menu__loading`)
			}, [(F(), L(Cb, {
				clsPrefix: n,
				strokeWidth: 20
			}, null, 8, ["clsPrefix"]))], 2)) : (F(), I(P, { key: 1 }, [this.empty ? (F(), I("div", {
				key: 1,
				class: K(`${n}-base-select-menu__empty`),
				"data-empty": !0
			}, [G(() => Hg(e.empty, () => [this.mergedRenderEmpty?.() || (F(), L(Qh, {
				theme: r.peers.Empty,
				themeOverrides: r.peerOverrides.Empty,
				size: this.size
			}, null, 8, [
				"theme",
				"themeOverrides",
				"size"
			]))]))], 2)) : (F(), L(ry, aa({
				key: 0,
				ref: "scrollbarRef",
				theme: r.peers.Scrollbar,
				themeOverrides: r.peerOverrides.Scrollbar,
				scrollable: this.scrollable,
				container: t ? this.virtualListContainer : void 0,
				content: t ? this.virtualListContent : void 0,
				onScroll: t ? void 0 : this.doScroll
			}, this.scrollbarProps), { default: () => t ? (F(), L(Uv, {
				key: 1,
				ref: "virtualListRef",
				class: K(`${n}-virtual-list`),
				items: this.flattenedNodes,
				itemSize: this.itemSize,
				showScrollbar: !1,
				paddingTop: this.padding.top,
				paddingBottom: this.padding.bottom,
				onResize: this.handleVirtualListResize,
				onScroll: this.handleVirtualListScroll,
				itemResizable: !0
			}, { default: ({ item: e }) => e.isGroup ? (F(), L(Jb, {
				key: e.key,
				clsPrefix: n,
				tmNode: e
			}, null, 8, ["clsPrefix", "tmNode"])) : e.ignored ? null : (F(), L($b, {
				clsPrefix: n,
				key: e.key,
				tmNode: e
			}, null, 8, ["clsPrefix", "tmNode"])) }, 1032, [
				"class",
				"items",
				"itemSize",
				"paddingTop",
				"paddingBottom",
				"onResize",
				"onScroll"
			])) : (F(), I("div", {
				key: 4,
				class: K(`${n}-base-select-menu-option-wrapper`),
				style: k({
					paddingTop: this.padding.top,
					paddingBottom: this.padding.bottom
				})
			}, [G(() => this.flattenedNodes.map((e) => e.isGroup ? (F(), L(Jb, {
				key: e.key,
				clsPrefix: n,
				tmNode: e
			}, null, 8, ["clsPrefix", "tmNode"])) : (F(), L($b, {
				clsPrefix: n,
				key: e.key,
				tmNode: e
			}, null, 8, ["clsPrefix", "tmNode"]))))], 6)) }, 1040, [
				"theme",
				"themeOverrides",
				"scrollable",
				"container",
				"content",
				"onScroll"
			]))], 64)),
			G(() => Wg(e.action, (e) => e && [(F(), I("div", {
				class: K(`${n}-base-select-menu__action`),
				"data-action": !0,
				key: "action"
			}, [G(() => e)], 2)), (F(), L(Kb, {
				onFocus: this.onTabOut,
				key: "focus-detector"
			}, null, 8, ["onFocus"]))]))
		], 46, zx);
	}
});
//#endregion
//#region node_modules/naive-ui/es/select/src/utils.mjs
function Vx(e) {
	return e.type === "group";
}
function Hx(e) {
	return e.type === "ignored";
}
function Ux(e, t) {
	try {
		return !!(1 + t.toString().toLowerCase().indexOf(e.trim().toLowerCase()));
	} catch {
		return !1;
	}
}
function Wx(e, t) {
	return {
		getIsGroup: Vx,
		getIgnored: Hx,
		getKey(t) {
			return Vx(t) ? t.name || t.key || "key-required" : t[e];
		},
		getChildren(e) {
			return e[t];
		}
	};
}
function Gx(e, t, n, r) {
	if (!t) return e;
	function i(e) {
		if (!Array.isArray(e)) return [];
		let a = [];
		for (let o of e) if (Vx(o)) {
			let e = i(o[r]);
			e.length && a.push(Object.assign({}, o, { [r]: e }));
		} else if (Hx(o)) continue;
		else t(n, o) && a.push(o);
		return a;
	}
	return i(e);
}
function Kx(e, t, n) {
	let r = /* @__PURE__ */ new Map();
	return e.forEach((e) => {
		Vx(e) ? e[n].forEach((e) => {
			r.set(e[t], e);
		}) : r.set(e[t], e);
	}), r;
}
//#endregion
//#region node_modules/naive-ui/es/avatar/styles/light.mjs
function qx(e) {
	let { borderRadius: t, avatarColor: n, cardColor: r, fontSize: i, heightTiny: a, heightSmall: o, heightMedium: s, heightLarge: c, heightHuge: l, modalColor: u, popoverColor: d } = e;
	return {
		borderRadius: t,
		fontSize: i,
		border: `2px solid ${r}`,
		heightTiny: a,
		heightSmall: o,
		heightMedium: s,
		heightLarge: c,
		heightHuge: l,
		color: q(r, n),
		colorModal: q(u, n),
		colorPopover: q(d, n)
	};
}
var Jx = {
	name: "Avatar",
	common: Z,
	self: qx
};
//#endregion
//#region node_modules/naive-ui/es/avatar-group/styles/light.mjs
function Yx() {
	return { gap: "-12px" };
}
//#endregion
//#region node_modules/naive-ui/es/back-top/styles/_common.mjs
var Xx = {
	width: "44px",
	height: "44px",
	borderRadius: "22px",
	iconSize: "26px"
}, Zx = {
	name: "BackTop",
	common: Z,
	self(e) {
		let { popoverColor: t, textColor2: n, primaryColorHover: r, primaryColorPressed: i } = e;
		return {
			...Xx,
			color: t,
			textColor: n,
			iconColor: n,
			iconColorHover: r,
			iconColorPressed: i,
			boxShadow: "0 2px 8px 0px rgba(0, 0, 0, .12)",
			boxShadowHover: "0 2px 12px 0px rgba(0, 0, 0, .18)",
			boxShadowPressed: "0 2px 12px 0px rgba(0, 0, 0, .18)"
		};
	}
}, Qx = {
	name: "Badge",
	common: Z,
	self(e) {
		let { errorColorSuppl: t, infoColorSuppl: n, successColorSuppl: r, warningColorSuppl: i, fontFamily: a } = e;
		return {
			color: t,
			colorInfo: n,
			colorSuccess: r,
			colorError: t,
			colorWarning: i,
			fontSize: "12px",
			fontFamily: a
		};
	}
}, { cubicBezierEaseInOut: $x } = ym;
function eS({ duration: e = ".2s", delay: t = ".1s" } = {}) {
	return [
		B("&.fade-in-width-expand-transition-leave-from, &.fade-in-width-expand-transition-enter-to", { opacity: 1 }),
		B("&.fade-in-width-expand-transition-leave-to, &.fade-in-width-expand-transition-enter-from", "\n opacity: 0!important;\n margin-left: 0!important;\n margin-right: 0!important;\n "),
		B("&.fade-in-width-expand-transition-leave-active", `
 overflow: hidden;
 transition:
 opacity ${e} ${$x},
 max-width ${e} ${$x} ${t},
 margin-left ${e} ${$x} ${t},
 margin-right ${e} ${$x} ${t};
 `),
		B("&.fade-in-width-expand-transition-enter-active", `
 overflow: hidden;
 transition:
 opacity ${e} ${$x} ${t},
 max-width ${e} ${$x},
 margin-left ${e} ${$x},
 margin-right ${e} ${$x};
 `)
	];
}
//#endregion
//#region node_modules/naive-ui/es/_internal/wave/src/styles/index.cssr.mjs
var tS = V("base-wave", "\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n border-radius: inherit;\n"), nS = /* @__PURE__ */ N({
	name: "BaseWave",
	props: { clsPrefix: {
		type: String,
		required: !0
	} },
	setup(e) {
		km("-base-wave", tS, /* @__PURE__ */ M(e, "clsPrefix"));
		let t = /* @__PURE__ */ j(null), n = /* @__PURE__ */ j(!1), r = null;
		return Lr(() => {
			r !== null && window.clearTimeout(r);
		}), {
			active: n,
			selfRef: t,
			play() {
				r !== null && (window.clearTimeout(r), n.value = !1, r = null), wn(() => {
					t.value?.offsetHeight, n.value = !0, r = window.setTimeout(() => {
						n.value = !1, r = null;
					}, 1e3);
				});
			}
		};
	},
	render() {
		let { clsPrefix: e } = this;
		return F(), I("div", {
			ref: "selfRef",
			"aria-hidden": !0,
			class: K([`${e}-base-wave`, this.active && `${e}-base-wave--active`])
		}, null, 2);
	}
}), rS = { fontWeightActive: "400" };
//#endregion
//#region node_modules/naive-ui/es/breadcrumb/styles/light.mjs
function iS(e) {
	let { fontSize: t, textColor3: n, textColor2: r, borderRadius: i, buttonColor2Hover: a, buttonColor2Pressed: o } = e;
	return {
		...rS,
		fontSize: t,
		itemLineHeight: "1.25",
		itemTextColor: n,
		itemTextColorHover: r,
		itemTextColorPressed: r,
		itemTextColorActive: r,
		itemBorderRadius: i,
		itemColorHover: a,
		itemColorPressed: o,
		separatorColor: n
	};
}
//#endregion
//#region node_modules/naive-ui/es/breadcrumb/styles/dark.mjs
var aS = {
	name: "Breadcrumb",
	common: Z,
	self: iS
}, oS = {
	paddingTiny: "0 6px",
	paddingSmall: "0 10px",
	paddingMedium: "0 14px",
	paddingLarge: "0 18px",
	paddingRoundTiny: "0 10px",
	paddingRoundSmall: "0 14px",
	paddingRoundMedium: "0 18px",
	paddingRoundLarge: "0 22px",
	iconMarginTiny: "6px",
	iconMarginSmall: "6px",
	iconMarginMedium: "6px",
	iconMarginLarge: "6px",
	iconSizeTiny: "14px",
	iconSizeSmall: "18px",
	iconSizeMedium: "18px",
	iconSizeLarge: "20px",
	rippleDuration: ".6s"
};
//#endregion
//#region node_modules/naive-ui/es/button/styles/light.mjs
function sS(e) {
	let { heightTiny: t, heightSmall: n, heightMedium: r, heightLarge: i, borderRadius: a, fontSizeTiny: o, fontSizeSmall: s, fontSizeMedium: c, fontSizeLarge: l, opacityDisabled: u, textColor2: d, textColor3: f, primaryColorHover: p, primaryColorPressed: m, borderColor: h, primaryColor: g, baseColor: _, infoColor: v, infoColorHover: y, infoColorPressed: b, successColor: x, successColorHover: S, successColorPressed: C, warningColor: w, warningColorHover: T, warningColorPressed: E, errorColor: D, errorColorHover: O, errorColorPressed: ee, fontWeight: te, buttonColor2: ne, buttonColor2Hover: re, buttonColor2Pressed: ie, fontWeightStrong: ae } = e;
	return {
		...oS,
		heightTiny: t,
		heightSmall: n,
		heightMedium: r,
		heightLarge: i,
		borderRadiusTiny: a,
		borderRadiusSmall: a,
		borderRadiusMedium: a,
		borderRadiusLarge: a,
		fontSizeTiny: o,
		fontSizeSmall: s,
		fontSizeMedium: c,
		fontSizeLarge: l,
		opacityDisabled: u,
		colorOpacitySecondary: "0.16",
		colorOpacitySecondaryHover: "0.22",
		colorOpacitySecondaryPressed: "0.28",
		colorSecondary: ne,
		colorSecondaryHover: re,
		colorSecondaryPressed: ie,
		colorTertiary: ne,
		colorTertiaryHover: re,
		colorTertiaryPressed: ie,
		colorQuaternary: "#0000",
		colorQuaternaryHover: re,
		colorQuaternaryPressed: ie,
		color: "#0000",
		colorHover: "#0000",
		colorPressed: "#0000",
		colorFocus: "#0000",
		colorDisabled: "#0000",
		textColor: d,
		textColorTertiary: f,
		textColorHover: p,
		textColorPressed: m,
		textColorFocus: p,
		textColorDisabled: d,
		textColorText: d,
		textColorTextHover: p,
		textColorTextPressed: m,
		textColorTextFocus: p,
		textColorTextDisabled: d,
		textColorGhost: d,
		textColorGhostHover: p,
		textColorGhostPressed: m,
		textColorGhostFocus: p,
		textColorGhostDisabled: d,
		border: `1px solid ${h}`,
		borderHover: `1px solid ${p}`,
		borderPressed: `1px solid ${m}`,
		borderFocus: `1px solid ${p}`,
		borderDisabled: `1px solid ${h}`,
		rippleColor: g,
		colorPrimary: g,
		colorHoverPrimary: p,
		colorPressedPrimary: m,
		colorFocusPrimary: p,
		colorDisabledPrimary: g,
		textColorPrimary: _,
		textColorHoverPrimary: _,
		textColorPressedPrimary: _,
		textColorFocusPrimary: _,
		textColorDisabledPrimary: _,
		textColorTextPrimary: g,
		textColorTextHoverPrimary: p,
		textColorTextPressedPrimary: m,
		textColorTextFocusPrimary: p,
		textColorTextDisabledPrimary: d,
		textColorGhostPrimary: g,
		textColorGhostHoverPrimary: p,
		textColorGhostPressedPrimary: m,
		textColorGhostFocusPrimary: p,
		textColorGhostDisabledPrimary: g,
		borderPrimary: `1px solid ${g}`,
		borderHoverPrimary: `1px solid ${p}`,
		borderPressedPrimary: `1px solid ${m}`,
		borderFocusPrimary: `1px solid ${p}`,
		borderDisabledPrimary: `1px solid ${g}`,
		rippleColorPrimary: g,
		colorInfo: v,
		colorHoverInfo: y,
		colorPressedInfo: b,
		colorFocusInfo: y,
		colorDisabledInfo: v,
		textColorInfo: _,
		textColorHoverInfo: _,
		textColorPressedInfo: _,
		textColorFocusInfo: _,
		textColorDisabledInfo: _,
		textColorTextInfo: v,
		textColorTextHoverInfo: y,
		textColorTextPressedInfo: b,
		textColorTextFocusInfo: y,
		textColorTextDisabledInfo: d,
		textColorGhostInfo: v,
		textColorGhostHoverInfo: y,
		textColorGhostPressedInfo: b,
		textColorGhostFocusInfo: y,
		textColorGhostDisabledInfo: v,
		borderInfo: `1px solid ${v}`,
		borderHoverInfo: `1px solid ${y}`,
		borderPressedInfo: `1px solid ${b}`,
		borderFocusInfo: `1px solid ${y}`,
		borderDisabledInfo: `1px solid ${v}`,
		rippleColorInfo: v,
		colorSuccess: x,
		colorHoverSuccess: S,
		colorPressedSuccess: C,
		colorFocusSuccess: S,
		colorDisabledSuccess: x,
		textColorSuccess: _,
		textColorHoverSuccess: _,
		textColorPressedSuccess: _,
		textColorFocusSuccess: _,
		textColorDisabledSuccess: _,
		textColorTextSuccess: x,
		textColorTextHoverSuccess: S,
		textColorTextPressedSuccess: C,
		textColorTextFocusSuccess: S,
		textColorTextDisabledSuccess: d,
		textColorGhostSuccess: x,
		textColorGhostHoverSuccess: S,
		textColorGhostPressedSuccess: C,
		textColorGhostFocusSuccess: S,
		textColorGhostDisabledSuccess: x,
		borderSuccess: `1px solid ${x}`,
		borderHoverSuccess: `1px solid ${S}`,
		borderPressedSuccess: `1px solid ${C}`,
		borderFocusSuccess: `1px solid ${S}`,
		borderDisabledSuccess: `1px solid ${x}`,
		rippleColorSuccess: x,
		colorWarning: w,
		colorHoverWarning: T,
		colorPressedWarning: E,
		colorFocusWarning: T,
		colorDisabledWarning: w,
		textColorWarning: _,
		textColorHoverWarning: _,
		textColorPressedWarning: _,
		textColorFocusWarning: _,
		textColorDisabledWarning: _,
		textColorTextWarning: w,
		textColorTextHoverWarning: T,
		textColorTextPressedWarning: E,
		textColorTextFocusWarning: T,
		textColorTextDisabledWarning: d,
		textColorGhostWarning: w,
		textColorGhostHoverWarning: T,
		textColorGhostPressedWarning: E,
		textColorGhostFocusWarning: T,
		textColorGhostDisabledWarning: w,
		borderWarning: `1px solid ${w}`,
		borderHoverWarning: `1px solid ${T}`,
		borderPressedWarning: `1px solid ${E}`,
		borderFocusWarning: `1px solid ${T}`,
		borderDisabledWarning: `1px solid ${w}`,
		rippleColorWarning: w,
		colorError: D,
		colorHoverError: O,
		colorPressedError: ee,
		colorFocusError: O,
		colorDisabledError: D,
		textColorError: _,
		textColorHoverError: _,
		textColorPressedError: _,
		textColorFocusError: _,
		textColorDisabledError: _,
		textColorTextError: D,
		textColorTextHoverError: O,
		textColorTextPressedError: ee,
		textColorTextFocusError: O,
		textColorTextDisabledError: d,
		textColorGhostError: D,
		textColorGhostHoverError: O,
		textColorGhostPressedError: ee,
		textColorGhostFocusError: O,
		textColorGhostDisabledError: D,
		borderError: `1px solid ${D}`,
		borderHoverError: `1px solid ${O}`,
		borderPressedError: `1px solid ${ee}`,
		borderFocusError: `1px solid ${O}`,
		borderDisabledError: `1px solid ${D}`,
		rippleColorError: D,
		waveOpacity: "0.6",
		fontWeight: te,
		fontWeightStrong: ae
	};
}
var cS = {
	name: "Button",
	common: Ph,
	self: sS
}, lS = {
	name: "Button",
	common: Z,
	self(e) {
		let t = sS(e);
		return t.waveOpacity = "0.8", t.colorOpacitySecondary = "0.16", t.colorOpacitySecondaryHover = "0.2", t.colorOpacitySecondaryPressed = "0.12", t;
	}
};
//#endregion
//#region node_modules/naive-ui/es/_utils/color/index.mjs
function uS(e) {
	return q(e, [
		255,
		255,
		255,
		.16
	]);
}
function dS(e) {
	return q(e, [
		0,
		0,
		0,
		.12
	]);
}
//#endregion
//#region node_modules/naive-ui/es/button-group/src/context.mjs
var fS = hm("n-button-group"), pS = B([
	V("button", "\n margin: 0;\n font-weight: var(--n-font-weight);\n line-height: 1;\n font-family: inherit;\n padding: var(--n-padding);\n height: var(--n-height);\n font-size: var(--n-font-size);\n border-radius: var(--n-border-radius);\n color: var(--n-text-color);\n background-color: var(--n-color);\n width: var(--n-width);\n white-space: nowrap;\n outline: none;\n position: relative;\n z-index: auto;\n border: none;\n display: inline-flex;\n flex-wrap: nowrap;\n flex-shrink: 0;\n align-items: center;\n justify-content: center;\n user-select: none;\n -webkit-user-select: none;\n text-align: center;\n cursor: pointer;\n text-decoration: none;\n transition:\n color .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n opacity .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n ", [
		U("color", [
			H("border", { borderColor: "var(--n-border-color)" }),
			U("disabled", [H("border", { borderColor: "var(--n-border-color-disabled)" })]),
			Ns("disabled", [
				B("&:focus", [H("state-border", { borderColor: "var(--n-border-color-focus)" })]),
				B("&:hover", [H("state-border", { borderColor: "var(--n-border-color-hover)" })]),
				B("&:active", [H("state-border", { borderColor: "var(--n-border-color-pressed)" })]),
				U("pressed", [H("state-border", { borderColor: "var(--n-border-color-pressed)" })])
			])
		]),
		U("disabled", {
			backgroundColor: "var(--n-color-disabled)",
			color: "var(--n-text-color-disabled)"
		}, [H("border", { border: "var(--n-border-disabled)" })]),
		Ns("disabled", [
			B("&:focus", {
				backgroundColor: "var(--n-color-focus)",
				color: "var(--n-text-color-focus)"
			}, [H("state-border", { border: "var(--n-border-focus)" })]),
			B("&:hover", {
				backgroundColor: "var(--n-color-hover)",
				color: "var(--n-text-color-hover)"
			}, [H("state-border", { border: "var(--n-border-hover)" })]),
			B("&:active", {
				backgroundColor: "var(--n-color-pressed)",
				color: "var(--n-text-color-pressed)"
			}, [H("state-border", { border: "var(--n-border-pressed)" })]),
			U("pressed", {
				backgroundColor: "var(--n-color-pressed)",
				color: "var(--n-text-color-pressed)"
			}, [H("state-border", { border: "var(--n-border-pressed)" })])
		]),
		U("loading", "cursor: wait;"),
		V("base-wave", "\n pointer-events: none;\n top: 0;\n right: 0;\n bottom: 0;\n left: 0;\n animation-iteration-count: 1;\n animation-duration: var(--n-ripple-duration);\n animation-timing-function: var(--n-bezier-ease-out), var(--n-bezier-ease-out);\n ", [U("active", {
			zIndex: 1,
			animationName: "button-wave-spread, button-wave-opacity"
		})]),
		Tb && "MozBoxSizing" in document.createElement("div").style ? B("&::moz-focus-inner", { border: 0 }) : null,
		H("border, state-border", "\n position: absolute;\n left: 0;\n top: 0;\n right: 0;\n bottom: 0;\n border-radius: inherit;\n transition: border-color .3s var(--n-bezier);\n pointer-events: none;\n "),
		H("border", "\n border: var(--n-border);\n "),
		H("state-border", "\n border: var(--n-border);\n border-color: #0000;\n z-index: 1;\n "),
		H("icon", "\n margin: var(--n-icon-margin);\n margin-left: 0;\n height: var(--n-icon-size);\n width: var(--n-icon-size);\n max-width: var(--n-icon-size);\n font-size: var(--n-icon-size);\n position: relative;\n flex-shrink: 0;\n ", [V("icon-slot", "\n height: var(--n-icon-size);\n width: var(--n-icon-size);\n position: absolute;\n left: 0;\n top: 50%;\n transform: translateY(-50%);\n display: flex;\n align-items: center;\n justify-content: center;\n ", [db({
			top: "50%",
			originalTransform: "translateY(-50%)"
		})]), eS()]),
		H("content", "\n display: flex;\n align-items: center;\n flex-wrap: nowrap;\n min-width: 0;\n ", [B("~", [H("icon", {
			margin: "var(--n-icon-margin)",
			marginRight: 0
		})])]),
		U("block", "\n display: flex;\n width: 100%;\n "),
		U("dashed", [H("border, state-border", { borderStyle: "dashed !important" })]),
		U("disabled", {
			cursor: "not-allowed",
			opacity: "var(--n-opacity-disabled)"
		})
	]),
	B("@keyframes button-wave-spread", {
		from: { boxShadow: "0 0 0.5px 0 var(--n-ripple-color)" },
		to: { boxShadow: "0 0 0.5px 4.5px var(--n-ripple-color)" }
	}),
	B("@keyframes button-wave-opacity", {
		from: { opacity: "var(--n-wave-opacity)" },
		to: { opacity: 0 }
	})
]), mS = /* @__PURE__ */ N({
	name: "Button",
	props: {
		...Kh.props,
		color: String,
		textColor: String,
		text: Boolean,
		block: Boolean,
		loading: Boolean,
		disabled: Boolean,
		circle: Boolean,
		size: String,
		ghost: Boolean,
		round: Boolean,
		secondary: Boolean,
		tertiary: Boolean,
		quaternary: Boolean,
		strong: Boolean,
		focusable: {
			type: Boolean,
			default: !0
		},
		keyboard: {
			type: Boolean,
			default: !0
		},
		tag: {
			type: String,
			default: "button"
		},
		type: {
			type: String,
			default: "default"
		},
		dashed: Boolean,
		renderIcon: Function,
		iconPlacement: {
			type: String,
			default: "left"
		},
		attrType: {
			type: String,
			default: "button"
		},
		bordered: {
			type: Boolean,
			default: !0
		},
		onClick: [Function, Array],
		nativeFocusBehavior: {
			type: Boolean,
			default: !Db
		},
		spinProps: Object
	},
	slots: Object,
	setup(e) {
		let t = /* @__PURE__ */ j(null), n = /* @__PURE__ */ j(null), r = /* @__PURE__ */ j(!1), i = Sg(() => !e.quaternary && !e.tertiary && !e.secondary && !e.text && (!e.color || e.ghost || e.dashed) && e.bordered), a = Bn(fS, {}), { inlineThemeDisabled: o, mergedClsPrefixRef: s, mergedRtlRef: c, mergedComponentPropsRef: l } = _m(e), { mergedSizeRef: u } = ab({}, {
			defaultSize: "medium",
			mergedSize: (t) => {
				let { size: n } = e;
				if (n) return n;
				let { size: r } = a;
				if (r) return r;
				let { mergedSize: i } = t || {};
				return i ? i.value : l?.value?.Button?.size || "medium";
			}
		}), d = z(() => e.focusable && !e.disabled), f = (n) => {
			d.value || n.preventDefault(), !e.nativeFocusBehavior && (n.preventDefault(), !e.disabled && d.value && t.value?.focus({ preventScroll: !0 }));
		}, p = (t) => {
			if (!e.disabled && !e.loading) {
				let { onClick: r } = e;
				r && $(r, t), e.text || n.value?.play();
			}
		}, m = (t) => {
			if (t.key === "Enter") {
				if (!e.keyboard) return;
				r.value = !1;
			}
		}, h = (t) => {
			if (t.key === "Enter") {
				if (!e.keyboard || e.loading) {
					t.preventDefault();
					return;
				}
				r.value = !0;
			}
		}, g = () => {
			r.value = !1;
		}, _ = Kh("Button", "-button", pS, cS, e, s), v = Kg("Button", c, s), y = z(() => {
			let { common: { cubicBezierEaseInOut: t, cubicBezierEaseOut: n }, self: r } = _.value, { rippleDuration: i, opacityDisabled: a, fontWeight: o, fontWeightStrong: s } = r, c = u.value, { dashed: l, type: d, ghost: f, text: p, color: m, round: h, circle: g, textColor: v, secondary: y, tertiary: b, quaternary: x, strong: S } = e, C = { "--n-font-weight": S ? s : o }, w = {
				"--n-color": "initial",
				"--n-color-hover": "initial",
				"--n-color-pressed": "initial",
				"--n-color-focus": "initial",
				"--n-color-disabled": "initial",
				"--n-ripple-color": "initial",
				"--n-text-color": "initial",
				"--n-text-color-hover": "initial",
				"--n-text-color-pressed": "initial",
				"--n-text-color-focus": "initial",
				"--n-text-color-disabled": "initial"
			}, T = d === "tertiary", E = d === "default", D = T ? "default" : d;
			if (p) {
				let e = v || m;
				w = {
					"--n-color": "#0000",
					"--n-color-hover": "#0000",
					"--n-color-pressed": "#0000",
					"--n-color-focus": "#0000",
					"--n-color-disabled": "#0000",
					"--n-ripple-color": "#0000",
					"--n-text-color": e || r[W("textColorText", D)],
					"--n-text-color-hover": e ? uS(e) : r[W("textColorTextHover", D)],
					"--n-text-color-pressed": e ? dS(e) : r[W("textColorTextPressed", D)],
					"--n-text-color-focus": e ? uS(e) : r[W("textColorTextHover", D)],
					"--n-text-color-disabled": e || r[W("textColorTextDisabled", D)]
				};
			} else if (f || l) {
				let e = v || m;
				w = {
					"--n-color": "#0000",
					"--n-color-hover": "#0000",
					"--n-color-pressed": "#0000",
					"--n-color-focus": "#0000",
					"--n-color-disabled": "#0000",
					"--n-ripple-color": m || r[W("rippleColor", D)],
					"--n-text-color": e || r[W("textColorGhost", D)],
					"--n-text-color-hover": e ? uS(e) : r[W("textColorGhostHover", D)],
					"--n-text-color-pressed": e ? dS(e) : r[W("textColorGhostPressed", D)],
					"--n-text-color-focus": e ? uS(e) : r[W("textColorGhostHover", D)],
					"--n-text-color-disabled": e || r[W("textColorGhostDisabled", D)]
				};
			} else if (y) {
				let e = E ? r.textColor : T ? r.textColorTertiary : r[W("color", D)], t = m || e, n = d !== "default" && d !== "tertiary";
				w = {
					"--n-color": n ? J(t, { alpha: Number(r.colorOpacitySecondary) }) : r.colorSecondary,
					"--n-color-hover": n ? J(t, { alpha: Number(r.colorOpacitySecondaryHover) }) : r.colorSecondaryHover,
					"--n-color-pressed": n ? J(t, { alpha: Number(r.colorOpacitySecondaryPressed) }) : r.colorSecondaryPressed,
					"--n-color-focus": n ? J(t, { alpha: Number(r.colorOpacitySecondaryHover) }) : r.colorSecondaryHover,
					"--n-color-disabled": r.colorSecondary,
					"--n-ripple-color": "#0000",
					"--n-text-color": t,
					"--n-text-color-hover": t,
					"--n-text-color-pressed": t,
					"--n-text-color-focus": t,
					"--n-text-color-disabled": t
				};
			} else if (b || x) {
				let e = E ? r.textColor : T ? r.textColorTertiary : r[W("color", D)], t = m || e;
				b ? (w["--n-color"] = r.colorTertiary, w["--n-color-hover"] = r.colorTertiaryHover, w["--n-color-pressed"] = r.colorTertiaryPressed, w["--n-color-focus"] = r.colorSecondaryHover, w["--n-color-disabled"] = r.colorTertiary) : (w["--n-color"] = r.colorQuaternary, w["--n-color-hover"] = r.colorQuaternaryHover, w["--n-color-pressed"] = r.colorQuaternaryPressed, w["--n-color-focus"] = r.colorQuaternaryHover, w["--n-color-disabled"] = r.colorQuaternary), w["--n-ripple-color"] = "#0000", w["--n-text-color"] = t, w["--n-text-color-hover"] = t, w["--n-text-color-pressed"] = t, w["--n-text-color-focus"] = t, w["--n-text-color-disabled"] = t;
			} else w = {
				"--n-color": m || r[W("color", D)],
				"--n-color-hover": m ? uS(m) : r[W("colorHover", D)],
				"--n-color-pressed": m ? dS(m) : r[W("colorPressed", D)],
				"--n-color-focus": m ? uS(m) : r[W("colorFocus", D)],
				"--n-color-disabled": m || r[W("colorDisabled", D)],
				"--n-ripple-color": m || r[W("rippleColor", D)],
				"--n-text-color": v || (m ? r.textColorPrimary : T ? r.textColorTertiary : r[W("textColor", D)]),
				"--n-text-color-hover": v || (m ? r.textColorHoverPrimary : r[W("textColorHover", D)]),
				"--n-text-color-pressed": v || (m ? r.textColorPressedPrimary : r[W("textColorPressed", D)]),
				"--n-text-color-focus": v || (m ? r.textColorFocusPrimary : r[W("textColorFocus", D)]),
				"--n-text-color-disabled": v || (m ? r.textColorDisabledPrimary : r[W("textColorDisabled", D)])
			};
			let O = {
				"--n-border": "initial",
				"--n-border-hover": "initial",
				"--n-border-pressed": "initial",
				"--n-border-focus": "initial",
				"--n-border-disabled": "initial"
			};
			O = p ? {
				"--n-border": "none",
				"--n-border-hover": "none",
				"--n-border-pressed": "none",
				"--n-border-focus": "none",
				"--n-border-disabled": "none"
			} : {
				"--n-border": r[W("border", D)],
				"--n-border-hover": r[W("borderHover", D)],
				"--n-border-pressed": r[W("borderPressed", D)],
				"--n-border-focus": r[W("borderFocus", D)],
				"--n-border-disabled": r[W("borderDisabled", D)]
			};
			let { [W("height", c)]: ee, [W("fontSize", c)]: te, [W("padding", c)]: ne, [W("paddingRound", c)]: re, [W("iconSize", c)]: ie, [W("borderRadius", c)]: ae, [W("iconMargin", c)]: oe, waveOpacity: se } = r, ce = {
				"--n-width": g && !p ? ee : "initial",
				"--n-height": p ? "initial" : ee,
				"--n-font-size": te,
				"--n-padding": g || p ? "initial" : h ? re : ne,
				"--n-icon-size": ie,
				"--n-icon-margin": oe,
				"--n-border-radius": p ? "initial" : g || h ? ee : ae
			};
			return {
				"--n-bezier": t,
				"--n-bezier-ease-out": n,
				"--n-ripple-duration": i,
				"--n-opacity-disabled": a,
				"--n-wave-opacity": se,
				...C,
				...w,
				...O,
				...ce
			};
		}), b = o ? Uh("button", z(() => {
			let t = "", { dashed: n, type: r, ghost: i, text: a, color: o, round: s, circle: c, textColor: l, secondary: d, tertiary: f, quaternary: p, strong: m } = e;
			n && (t += "a"), i && (t += "b"), a && (t += "c"), s && (t += "d"), c && (t += "e"), d && (t += "f"), f && (t += "g"), p && (t += "h"), m && (t += "i"), o && (t += `j${xy(o)}`), l && (t += `k${xy(l)}`);
			let { value: h } = u;
			return t += `l${h[0]}`, t += `m${r[0]}`, t;
		}), y, e) : void 0;
		return {
			selfElRef: t,
			waveElRef: n,
			mergedClsPrefix: s,
			mergedFocusable: d,
			mergedSize: u,
			showBorder: i,
			enterPressed: r,
			rtlEnabled: v,
			handleMousedown: f,
			handleKeydown: h,
			handleBlur: g,
			handleKeyup: m,
			handleClick: p,
			customColorCssVars: z(() => {
				let { color: t } = e;
				if (!t) return null;
				let n = uS(t);
				return {
					"--n-border-color": t,
					"--n-border-color-hover": n,
					"--n-border-color-pressed": dS(t),
					"--n-border-color-focus": n,
					"--n-border-color-disabled": t
				};
			}),
			cssVars: o ? void 0 : y,
			themeClass: b?.themeClass,
			onRender: b?.onRender
		};
	},
	render() {
		let { mergedClsPrefix: e, tag: t, onRender: n } = this;
		n?.();
		let r = Wg(this.$slots.default, (t) => t && (F(), I("span", { class: K(`${e}-button__content`) }, [G(() => t)], 2)));
		return F(), L(t, {
			ref: "selfElRef",
			class: K([
				this.themeClass,
				`${e}-button`,
				`${e}-button--${this.type}-type`,
				`${e}-button--${this.mergedSize}-type`,
				this.rtlEnabled && `${e}-button--rtl`,
				this.disabled && `${e}-button--disabled`,
				this.block && `${e}-button--block`,
				this.enterPressed && `${e}-button--pressed`,
				!this.text && this.dashed && `${e}-button--dashed`,
				this.color && `${e}-button--color`,
				this.secondary && `${e}-button--secondary`,
				this.loading && `${e}-button--loading`,
				this.ghost && `${e}-button--ghost`
			]),
			tabindex: this.mergedFocusable ? 0 : -1,
			type: this.attrType,
			style: k(this.cssVars),
			disabled: this.disabled,
			onClick: this.handleClick,
			onBlur: this.handleBlur,
			onMousedown: this.handleMousedown,
			onKeyup: this.handleKeyup,
			onKeydown: this.handleKeydown
		}, {
			default: In(() => [
				G(() => this.iconPlacement === "right" && r),
				Xi(Hy, { width: !0 }, { default: () => Wg(this.$slots.icon, (t) => (this.loading || this.renderIcon || t) && (F(), I("span", {
					class: K(`${e}-button__icon`),
					style: k({ margin: Gg(this.$slots.default) ? "0" : "" })
				}, [Xi(cb, null, { default: () => this.loading ? (F(), L(Cb, aa({
					clsPrefix: e,
					key: "loading",
					class: `${e}-icon-slot`,
					strokeWidth: 20
				}, this.spinProps), null, 16, ["clsPrefix", "class"])) : (F(), I("div", {
					key: "icon",
					class: K(`${e}-icon-slot`),
					role: "none"
				}, [this.renderIcon ? (F(), I(P, { key: 0 }, [G(() => this.renderIcon())], 64)) : (F(), I(P, { key: 1 }, [G(() => t)], 64))], 2)) }, 1024)], 6))) }, 1024),
				G(() => this.iconPlacement === "left" && r),
				this.text ? G(() => null) : (F(), L(nS, {
					key: 0,
					ref: "waveElRef",
					clsPrefix: e
				}, null, 8, ["clsPrefix"])),
				this.showBorder ? (F(), I("div", {
					key: 2,
					"aria-hidden": !0,
					class: K(`${e}-button__border`),
					style: k(this.customColorCssVars)
				}, null, 6)) : G(() => null),
				this.showBorder ? (F(), I("div", {
					key: 4,
					"aria-hidden": !0,
					class: K(`${e}-button__state-border`),
					style: k(this.customColorCssVars)
				}, null, 6)) : G(() => null)
			]),
			_: 2
		}, 1032, [
			"class",
			"tabindex",
			"type",
			"style",
			"disabled",
			"onClick",
			"onBlur",
			"onMousedown",
			"onKeyup",
			"onKeydown"
		]);
	}
}), hS = { titleFontSize: "22px" };
//#endregion
//#region node_modules/naive-ui/es/calendar/styles/light.mjs
function gS(e) {
	let { borderRadius: t, fontSize: n, lineHeight: r, textColor2: i, textColor1: a, textColorDisabled: o, dividerColor: s, fontWeightStrong: c, primaryColor: l, baseColor: u, hoverColor: d, cardColor: f, modalColor: p, popoverColor: m } = e;
	return {
		...hS,
		borderRadius: t,
		borderColor: q(f, s),
		borderColorModal: q(p, s),
		borderColorPopover: q(m, s),
		textColor: i,
		titleFontWeight: c,
		titleTextColor: a,
		dayTextColor: o,
		fontSize: n,
		lineHeight: r,
		dateColorCurrent: l,
		dateTextColorCurrent: u,
		cellColorHover: q(f, d),
		cellColorHoverModal: q(p, d),
		cellColorHoverPopover: q(m, d),
		cellColor: f,
		cellColorModal: p,
		cellColorPopover: m,
		barColor: l
	};
}
//#endregion
//#region node_modules/naive-ui/es/card/styles/_common.mjs
var _S = {
	paddingSmall: "12px 16px 12px",
	paddingMedium: "19px 24px 20px",
	paddingLarge: "23px 32px 24px",
	paddingHuge: "27px 40px 28px",
	titleFontSizeSmall: "16px",
	titleFontSizeMedium: "18px",
	titleFontSizeLarge: "18px",
	titleFontSizeHuge: "18px",
	closeIconSize: "18px",
	closeSize: "22px"
};
//#endregion
//#region node_modules/naive-ui/es/card/styles/light.mjs
function vS(e) {
	let { primaryColor: t, borderRadius: n, lineHeight: r, fontSize: i, cardColor: a, textColor2: o, textColor1: s, dividerColor: c, fontWeightStrong: l, closeIconColor: u, closeIconColorHover: d, closeIconColorPressed: f, closeColorHover: p, closeColorPressed: m, modalColor: h, boxShadow1: g, popoverColor: _, actionColor: v } = e;
	return {
		..._S,
		lineHeight: r,
		color: a,
		colorModal: h,
		colorPopover: _,
		colorTarget: t,
		colorEmbedded: v,
		colorEmbeddedModal: v,
		colorEmbeddedPopover: v,
		textColor: o,
		titleTextColor: s,
		borderColor: c,
		actionColor: v,
		titleFontWeight: l,
		closeColorHover: p,
		closeColorPressed: m,
		closeBorderRadius: n,
		closeIconColor: u,
		closeIconColorHover: d,
		closeIconColorPressed: f,
		fontSizeSmall: i,
		fontSizeMedium: i,
		fontSizeLarge: i,
		fontSizeHuge: i,
		boxShadow: g,
		borderRadius: n
	};
}
var yS = {
	name: "Card",
	common: Ph,
	self: vS
}, bS = {
	name: "Card",
	common: Z,
	self(e) {
		let t = vS(e), { cardColor: n, modalColor: r, popoverColor: i } = e;
		return t.colorEmbedded = n, t.colorEmbeddedModal = r, t.colorEmbeddedPopover = i, t;
	}
}, xS = V("card-content", "\n flex: 1;\n min-width: 0;\n box-sizing: border-box;\n padding: 0 var(--n-padding-left) var(--n-padding-bottom) var(--n-padding-left);\n font-size: var(--n-font-size);\n"), SS = B([
	V("card", "\n font-size: var(--n-font-size);\n line-height: var(--n-line-height);\n display: flex;\n flex-direction: column;\n width: 100%;\n box-sizing: border-box;\n position: relative;\n border-radius: var(--n-border-radius);\n background-color: var(--n-color);\n color: var(--n-text-color);\n word-break: break-word;\n transition: \n color .3s var(--n-bezier),\n background-color .3s var(--n-bezier),\n box-shadow .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n ", [
		Is({ background: "var(--n-color-modal)" }),
		U("hoverable", [B("&:hover", "box-shadow: var(--n-box-shadow);")]),
		U("content-segmented", [B(">", [V("card-content", "\n padding-top: var(--n-padding-bottom);\n "), H("content-scrollbar", [B(">", [V("scrollbar-container", [B(">", [V("card-content", "\n padding-top: var(--n-padding-bottom);\n ")])])])])])]),
		U("content-soft-segmented", [B(">", [V("card-content", "\n margin: 0 var(--n-padding-left);\n padding: var(--n-padding-bottom) 0;\n "), H("content-scrollbar", [B(">", [V("scrollbar-container", [B(">", [V("card-content", "\n margin: 0 var(--n-padding-left);\n padding: var(--n-padding-bottom) 0;\n ")])])])])])]),
		U("footer-segmented", [B(">", [H("footer", "\n padding-top: var(--n-padding-bottom);\n ")])]),
		U("footer-soft-segmented", [B(">", [H("footer", "\n padding: var(--n-padding-bottom) 0;\n margin: 0 var(--n-padding-left);\n ")])]),
		B(">", [
			V("card-header", "\n box-sizing: border-box;\n display: flex;\n align-items: center;\n font-size: var(--n-title-font-size);\n padding:\n var(--n-padding-top)\n var(--n-padding-left)\n var(--n-padding-bottom)\n var(--n-padding-left);\n ", [
				H("main", "\n font-weight: var(--n-title-font-weight);\n transition: color .3s var(--n-bezier);\n flex: 1;\n min-width: 0;\n color: var(--n-title-text-color);\n "),
				H("extra", "\n display: flex;\n align-items: center;\n font-size: var(--n-font-size);\n font-weight: 400;\n transition: color .3s var(--n-bezier);\n color: var(--n-text-color);\n "),
				H("close", "\n margin: 0 0 0 8px;\n transition:\n background-color .3s var(--n-bezier),\n color .3s var(--n-bezier);\n ")
			]),
			H("action", "\n box-sizing: border-box;\n transition:\n background-color .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n background-clip: padding-box;\n background-color: var(--n-action-color);\n "),
			xS,
			V("card-content", [B("&:first-child", "\n padding-top: var(--n-padding-bottom);\n ")]),
			H("content-scrollbar", "\n display: flex;\n flex-direction: column;\n ", [B(">", [V("scrollbar-container", [B(">", [xS])])]), B("&:first-child >", [V("scrollbar-container", [B(">", [V("card-content", "\n padding-top: var(--n-padding-bottom);\n ")])])])]),
			H("footer", "\n box-sizing: border-box;\n padding: 0 var(--n-padding-left) var(--n-padding-bottom) var(--n-padding-left);\n font-size: var(--n-font-size);\n ", [B("&:first-child", "\n padding-top: var(--n-padding-bottom);\n ")]),
			H("action", "\n background-color: var(--n-action-color);\n padding: var(--n-padding-bottom) var(--n-padding-left);\n border-bottom-left-radius: var(--n-border-radius);\n border-bottom-right-radius: var(--n-border-radius);\n ")
		]),
		V("card-cover", "\n overflow: hidden;\n width: 100%;\n border-radius: var(--n-border-radius) var(--n-border-radius) 0 0;\n ", [B("img", "\n display: block;\n width: 100%;\n ")]),
		U("bordered", "\n border: 1px solid var(--n-border-color);\n ", [B("&:target", "border-color: var(--n-color-target);")]),
		U("action-segmented", [B(">", [H("action", [B("&:not(:first-child)", "\n border-top: 1px solid var(--n-border-color);\n ")])])]),
		U("content-segmented, content-soft-segmented", [B(">", [V("card-content", "\n transition: border-color 0.3s var(--n-bezier);\n ", [B("&:not(:first-child)", "\n border-top: 1px solid var(--n-border-color);\n ")]), H("content-scrollbar", "\n transition: border-color 0.3s var(--n-bezier);\n ", [B("&:not(:first-child)", "\n border-top: 1px solid var(--n-border-color);\n ")])])]),
		U("footer-segmented, footer-soft-segmented", [B(">", [H("footer", "\n transition: border-color 0.3s var(--n-bezier);\n ", [B("&:not(:first-child)", "\n border-top: 1px solid var(--n-border-color);\n ")])])]),
		U("embedded", "\n background-color: var(--n-color-embedded);\n ")
	]),
	Ps(V("card", "\n background: var(--n-color-modal);\n ", [U("embedded", "\n background-color: var(--n-color-embedded-modal);\n ")])),
	Fs(V("card", "\n background: var(--n-color-popover);\n ", [U("embedded", "\n background-color: var(--n-color-embedded-popover);\n ")]))
]), CS = {
	title: [String, Function],
	contentClass: String,
	contentStyle: [Object, String],
	contentScrollable: Boolean,
	headerClass: String,
	headerStyle: [Object, String],
	headerExtraClass: String,
	headerExtraStyle: [Object, String],
	footerClass: String,
	footerStyle: [Object, String],
	embedded: Boolean,
	segmented: {
		type: [Boolean, Object],
		default: !1
	},
	size: String,
	bordered: {
		type: Boolean,
		default: !0
	},
	closable: Boolean,
	hoverable: Boolean,
	role: String,
	onClose: [Function, Array],
	tag: {
		type: String,
		default: "div"
	},
	cover: Function,
	content: [String, Function],
	footer: Function,
	action: Function,
	headerExtra: Function,
	closeFocusable: Boolean
};
mm(CS);
var wS = /* @__PURE__ */ N({
	name: "Card",
	props: {
		...Kh.props,
		...CS
	},
	slots: Object,
	setup(e) {
		let t = () => {
			let { onClose: t } = e;
			t && $(t);
		}, { inlineThemeDisabled: n, mergedClsPrefixRef: r, mergedRtlRef: i, mergedComponentPropsRef: a } = _m(e), o = Kh("Card", "-card", SS, yS, e, r), s = Kg("Card", i, r), c = z(() => e.size || a?.value?.Card?.size || "medium"), l = z(() => {
			let e = c.value, { self: { color: t, colorModal: n, colorTarget: r, textColor: i, titleTextColor: a, titleFontWeight: s, borderColor: l, actionColor: u, borderRadius: d, lineHeight: f, closeIconColor: p, closeIconColorHover: m, closeIconColorPressed: h, closeColorHover: g, closeColorPressed: _, closeBorderRadius: v, closeIconSize: y, closeSize: b, boxShadow: x, colorPopover: S, colorEmbedded: C, colorEmbeddedModal: w, colorEmbeddedPopover: T, [W("padding", e)]: E, [W("fontSize", e)]: D, [W("titleFontSize", e)]: O }, common: { cubicBezierEaseInOut: ee } } = o.value, { top: te, left: ne, bottom: re } = Wm(E);
			return {
				"--n-bezier": ee,
				"--n-border-radius": d,
				"--n-color": t,
				"--n-color-modal": n,
				"--n-color-popover": S,
				"--n-color-embedded": C,
				"--n-color-embedded-modal": w,
				"--n-color-embedded-popover": T,
				"--n-color-target": r,
				"--n-text-color": i,
				"--n-line-height": f,
				"--n-action-color": u,
				"--n-title-text-color": a,
				"--n-title-font-weight": s,
				"--n-close-icon-color": p,
				"--n-close-icon-color-hover": m,
				"--n-close-icon-color-pressed": h,
				"--n-close-color-hover": g,
				"--n-close-color-pressed": _,
				"--n-border-color": l,
				"--n-box-shadow": x,
				"--n-padding-top": te,
				"--n-padding-bottom": re,
				"--n-padding-left": ne,
				"--n-font-size": D,
				"--n-title-font-size": O,
				"--n-close-size": b,
				"--n-close-icon-size": y,
				"--n-close-border-radius": v
			};
		}), u = n ? Uh("card", z(() => c.value[0]), l, e) : void 0;
		return {
			rtlEnabled: s,
			mergedClsPrefix: r,
			mergedTheme: o,
			handleCloseClick: t,
			cssVars: n ? void 0 : l,
			themeClass: u?.themeClass,
			onRender: u?.onRender
		};
	},
	render() {
		let { segmented: e, bordered: t, hoverable: n, mergedClsPrefix: r, rtlEnabled: i, onRender: a, embedded: o, tag: s, $slots: c } = this;
		return a?.(), F(), L(s, {
			class: K([
				`${r}-card`,
				this.themeClass,
				o && `${r}-card--embedded`,
				{
					[`${r}-card--rtl`]: i,
					[`${r}-card--content-scrollable`]: this.contentScrollable,
					[`${r}-card--content${typeof e != "boolean" && e.content === "soft" ? "-soft" : ""}-segmented`]: e === !0 || e !== !1 && e.content,
					[`${r}-card--footer${typeof e != "boolean" && e.footer === "soft" ? "-soft" : ""}-segmented`]: e === !0 || e !== !1 && e.footer,
					[`${r}-card--action-segmented`]: e === !0 || e !== !1 && e.action,
					[`${r}-card--bordered`]: t,
					[`${r}-card--hoverable`]: n
				}
			]),
			style: k(this.cssVars),
			role: this.role
		}, {
			default: In(() => [
				G(() => Wg(c.cover, (e) => {
					let t = this.cover ? Vg([this.cover()]) : e;
					return t && (F(), I("div", {
						class: K(`${r}-card-cover`),
						role: "none"
					}, [G(() => t)], 2));
				})),
				G(() => Wg(c.header, (e) => {
					let { title: t } = this, n = t ? Vg(typeof t == "function" ? [t()] : [t]) : e;
					return n || this.closable ? (F(), I("div", {
						key: 1,
						class: K([`${r}-card-header`, this.headerClass]),
						style: k(this.headerStyle),
						role: "heading"
					}, [
						R("div", {
							class: K(`${r}-card-header__main`),
							role: "heading"
						}, [G(() => n)], 2),
						G(() => Wg(c["header-extra"], (e) => {
							let t = this.headerExtra ? Vg([this.headerExtra()]) : e;
							return t && (F(), I("div", {
								class: K([`${r}-card-header__extra`, this.headerExtraClass]),
								style: k(this.headerExtraStyle)
							}, [G(() => t)], 6));
						})),
						G(() => this.closable && (F(), L(Ty, {
							clsPrefix: r,
							class: K(`${r}-card-header__close`),
							onClick: this.handleCloseClick,
							focusable: this.closeFocusable,
							absolute: !0
						}, null, 8, [
							"clsPrefix",
							"class",
							"onClick",
							"focusable"
						])))
					], 6)) : null;
				})),
				G(() => Wg(c.default, (e) => {
					let { content: t } = this, n = t ? Vg(typeof t == "function" ? [t()] : [t]) : e;
					return n ? this.contentScrollable ? (F(), L(ry, {
						key: 2,
						class: K(`${r}-card__content-scrollbar`),
						contentClass: [`${r}-card-content`, this.contentClass],
						contentStyle: this.contentStyle
					}, { default: () => n }, 1032, [
						"class",
						"contentClass",
						"contentStyle"
					])) : (F(), I("div", {
						key: 3,
						class: K([`${r}-card-content`, this.contentClass]),
						style: k(this.contentStyle),
						role: "none"
					}, [G(() => n)], 6)) : null;
				})),
				G(() => Wg(c.footer, (e) => {
					let t = this.footer ? Vg([this.footer()]) : e;
					return t && (F(), I("div", {
						class: K([`${r}-card__footer`, this.footerClass]),
						style: k(this.footerStyle),
						role: "none"
					}, [G(() => t)], 6));
				})),
				G(() => Wg(c.action, (e) => {
					let t = this.action ? Vg([this.action()]) : e;
					return t && (F(), I("div", {
						class: K(`${r}-card__action`),
						role: "none"
					}, [G(() => t)], 2));
				}))
			]),
			_: 2
		}, 1032, [
			"class",
			"style",
			"role"
		]);
	}
});
//#endregion
//#region node_modules/naive-ui/es/carousel/styles/light.mjs
function TS() {
	return {
		dotSize: "8px",
		dotColor: "rgba(255, 255, 255, .3)",
		dotColorActive: "rgba(255, 255, 255, 1)",
		dotColorFocus: "rgba(255, 255, 255, .5)",
		dotLineWidth: "16px",
		dotLineWidthActive: "24px",
		arrowColor: "#eee"
	};
}
//#endregion
//#region node_modules/naive-ui/es/checkbox/styles/_common.mjs
var ES = {
	sizeSmall: "14px",
	sizeMedium: "16px",
	sizeLarge: "18px",
	labelPadding: "0 8px",
	labelFontWeight: "400"
};
//#endregion
//#region node_modules/naive-ui/es/checkbox/styles/light.mjs
function DS(e) {
	let { baseColor: t, inputColorDisabled: n, cardColor: r, modalColor: i, popoverColor: a, textColorDisabled: o, borderColor: s, primaryColor: c, textColor2: l, fontSizeSmall: u, fontSizeMedium: d, fontSizeLarge: f, borderRadiusSmall: p, lineHeight: m } = e;
	return {
		...ES,
		labelLineHeight: m,
		fontSizeSmall: u,
		fontSizeMedium: d,
		fontSizeLarge: f,
		borderRadius: p,
		color: t,
		colorChecked: c,
		colorDisabled: n,
		colorDisabledChecked: n,
		colorTableHeader: r,
		colorTableHeaderModal: i,
		colorTableHeaderPopover: a,
		checkMarkColor: t,
		checkMarkColorDisabled: o,
		checkMarkColorDisabledChecked: o,
		border: `1px solid ${s}`,
		borderDisabled: `1px solid ${s}`,
		borderDisabledChecked: `1px solid ${s}`,
		borderChecked: `1px solid ${c}`,
		borderFocus: `1px solid ${c}`,
		boxShadowFocus: `0 0 0 2px ${J(c, { alpha: .3 })}`,
		textColor: l,
		textColorDisabled: o
	};
}
var OS = {
	name: "Checkbox",
	common: Z,
	self(e) {
		let { cardColor: t } = e, n = DS(e);
		return n.color = "#0000", n.checkMarkColor = t, n;
	}
};
//#endregion
//#region node_modules/naive-ui/es/_internal/selection/styles/light.mjs
function kS(e) {
	let { borderRadius: t, textColor2: n, textColorDisabled: r, inputColor: i, inputColorDisabled: a, primaryColor: o, primaryColorHover: s, warningColor: c, warningColorHover: l, errorColor: u, errorColorHover: d, borderColor: f, iconColor: p, iconColorDisabled: m, clearColor: h, clearColorHover: g, clearColorPressed: _, placeholderColor: v, placeholderColorDisabled: y, fontSizeTiny: b, fontSizeSmall: x, fontSizeMedium: S, fontSizeLarge: C, heightTiny: w, heightSmall: T, heightMedium: E, heightLarge: D, fontWeight: O } = e;
	return {
		...Py,
		fontSizeTiny: b,
		fontSizeSmall: x,
		fontSizeMedium: S,
		fontSizeLarge: C,
		heightTiny: w,
		heightSmall: T,
		heightMedium: E,
		heightLarge: D,
		borderRadius: t,
		fontWeight: O,
		textColor: n,
		textColorDisabled: r,
		placeholderColor: v,
		placeholderColorDisabled: y,
		color: i,
		colorDisabled: a,
		colorActive: i,
		border: `1px solid ${f}`,
		borderHover: `1px solid ${s}`,
		borderActive: `1px solid ${o}`,
		borderFocus: `1px solid ${s}`,
		boxShadowHover: "none",
		boxShadowActive: `0 0 0 2px ${J(o, { alpha: .2 })}`,
		boxShadowFocus: `0 0 0 2px ${J(o, { alpha: .2 })}`,
		caretColor: o,
		arrowColor: p,
		arrowColorDisabled: m,
		loadingColor: o,
		borderWarning: `1px solid ${c}`,
		borderHoverWarning: `1px solid ${l}`,
		borderActiveWarning: `1px solid ${c}`,
		borderFocusWarning: `1px solid ${l}`,
		boxShadowHoverWarning: "none",
		boxShadowActiveWarning: `0 0 0 2px ${J(c, { alpha: .2 })}`,
		boxShadowFocusWarning: `0 0 0 2px ${J(c, { alpha: .2 })}`,
		colorActiveWarning: i,
		caretColorWarning: c,
		borderError: `1px solid ${u}`,
		borderHoverError: `1px solid ${d}`,
		borderActiveError: `1px solid ${u}`,
		borderFocusError: `1px solid ${d}`,
		boxShadowHoverError: "none",
		boxShadowActiveError: `0 0 0 2px ${J(u, { alpha: .2 })}`,
		boxShadowFocusError: `0 0 0 2px ${J(u, { alpha: .2 })}`,
		colorActiveError: i,
		caretColorError: u,
		clearColor: h,
		clearColorHover: g,
		clearColorPressed: _
	};
}
var AS = Gh({
	name: "InternalSelection",
	common: Ph,
	peers: { Popover: ag },
	self: kS
});
//#endregion
//#region node_modules/naive-ui/es/cascader/styles/light.mjs
function jS(e) {
	let { borderRadius: t, boxShadow2: n, popoverColor: r, textColor2: i, textColor3: a, primaryColor: o, textColorDisabled: s, dividerColor: c, hoverColor: l, fontSizeMedium: u, heightMedium: d } = e;
	return {
		menuBorderRadius: t,
		menuColor: r,
		menuBoxShadow: n,
		menuDividerColor: c,
		menuHeight: "calc(var(--n-option-height) * 6.6)",
		optionArrowColor: a,
		optionHeight: d,
		optionFontSize: u,
		optionColorHover: l,
		optionTextColor: i,
		optionTextColorActive: o,
		optionTextColorDisabled: s,
		optionCheckMarkColor: o,
		loadingColor: o,
		columnWidth: "180px"
	};
}
//#endregion
//#region node_modules/naive-ui/es/cascader/styles/dark.mjs
var MS = {
	name: "Cascader",
	common: Z,
	peers: {
		InternalSelectMenu: ng,
		InternalSelection: Fy,
		Scrollbar: Rh,
		Checkbox: OS,
		Empty: Vh
	},
	self: jS
}, NS = /* @__PURE__ */ new WeakSet();
function PS(e) {
	NS.add(e);
}
//#endregion
//#region node_modules/naive-ui/es/_internal/selection/src/styles/index.cssr.mjs
var FS = B([
	V("base-selection", "\n --n-padding-single: var(--n-padding-single-top) var(--n-padding-single-right) var(--n-padding-single-bottom) var(--n-padding-single-left);\n --n-padding-multiple: var(--n-padding-multiple-top) var(--n-padding-multiple-right) var(--n-padding-multiple-bottom) var(--n-padding-multiple-left);\n position: relative;\n z-index: auto;\n box-shadow: none;\n width: 100%;\n max-width: 100%;\n display: inline-block;\n vertical-align: bottom;\n border-radius: var(--n-border-radius);\n min-height: var(--n-height);\n line-height: 1.5;\n font-size: var(--n-font-size);\n ", [
		V("base-loading", "\n color: var(--n-loading-color);\n "),
		V("base-selection-tags", "min-height: var(--n-height);"),
		H("border, state-border", "\n position: absolute;\n left: 0;\n right: 0;\n top: 0;\n bottom: 0;\n pointer-events: none;\n border: var(--n-border);\n border-radius: inherit;\n transition:\n box-shadow .3s var(--n-bezier),\n border-color .3s var(--n-bezier);\n "),
		H("state-border", "\n z-index: 1;\n border-color: #0000;\n "),
		V("base-suffix", "\n cursor: pointer;\n position: absolute;\n top: 50%;\n transform: translateY(-50%);\n right: 10px;\n ", [H("arrow", "\n font-size: var(--n-arrow-size);\n color: var(--n-arrow-color);\n transition: color .3s var(--n-bezier);\n ")]),
		V("base-selection-overlay", "\n display: flex;\n align-items: center;\n white-space: nowrap;\n pointer-events: none;\n position: absolute;\n top: 0;\n right: 0;\n bottom: 0;\n left: 0;\n padding: var(--n-padding-single);\n transition: color .3s var(--n-bezier);\n ", [H("wrapper", "\n flex-basis: 0;\n flex-grow: 1;\n overflow: hidden;\n text-overflow: ellipsis;\n ")]),
		V("base-selection-placeholder", "\n color: var(--n-placeholder-color);\n ", [H("inner", "\n max-width: 100%;\n overflow: hidden;\n ")]),
		V("base-selection-tags", "\n cursor: pointer;\n outline: none;\n box-sizing: border-box;\n position: relative;\n z-index: auto;\n display: flex;\n padding: var(--n-padding-multiple);\n flex-wrap: wrap;\n align-items: center;\n width: 100%;\n vertical-align: bottom;\n background-color: var(--n-color);\n border-radius: inherit;\n transition:\n color .3s var(--n-bezier),\n box-shadow .3s var(--n-bezier),\n background-color .3s var(--n-bezier);\n "),
		V("base-selection-label", "\n height: var(--n-height);\n display: inline-flex;\n width: 100%;\n vertical-align: bottom;\n cursor: pointer;\n outline: none;\n z-index: auto;\n box-sizing: border-box;\n position: relative;\n transition:\n color .3s var(--n-bezier),\n box-shadow .3s var(--n-bezier),\n background-color .3s var(--n-bezier);\n border-radius: inherit;\n background-color: var(--n-color);\n align-items: center;\n ", [V("base-selection-input", "\n font-size: inherit;\n line-height: inherit;\n outline: none;\n cursor: pointer;\n box-sizing: border-box;\n border:none;\n width: 100%;\n padding: var(--n-padding-single);\n background-color: #0000;\n color: var(--n-text-color);\n transition: color .3s var(--n-bezier);\n caret-color: var(--n-caret-color);\n ", [H("content", "\n text-overflow: ellipsis;\n overflow: hidden;\n white-space: nowrap; \n ")]), H("render-label", "\n color: var(--n-text-color);\n ")]),
		Ns("disabled", [
			B("&:hover", [H("state-border", "\n box-shadow: var(--n-box-shadow-hover);\n border: var(--n-border-hover);\n ")]),
			U("focus", [H("state-border", "\n box-shadow: var(--n-box-shadow-focus);\n border: var(--n-border-focus);\n ")]),
			U("active", [
				H("state-border", "\n box-shadow: var(--n-box-shadow-active);\n border: var(--n-border-active);\n "),
				V("base-selection-label", "background-color: var(--n-color-active);"),
				V("base-selection-tags", "background-color: var(--n-color-active);")
			])
		]),
		U("disabled", "cursor: not-allowed;", [
			H("arrow", "\n color: var(--n-arrow-color-disabled);\n "),
			V("base-selection-label", "\n cursor: not-allowed;\n background-color: var(--n-color-disabled);\n ", [V("base-selection-input", "\n cursor: not-allowed;\n color: var(--n-text-color-disabled);\n "), H("render-label", "\n color: var(--n-text-color-disabled);\n ")]),
			V("base-selection-tags", "\n cursor: not-allowed;\n background-color: var(--n-color-disabled);\n "),
			V("base-selection-placeholder", "\n cursor: not-allowed;\n color: var(--n-placeholder-color-disabled);\n ")
		]),
		V("base-selection-input-tag", "\n height: calc(var(--n-height) - 6px);\n line-height: calc(var(--n-height) - 6px);\n outline: none;\n display: none;\n position: relative;\n margin-bottom: 3px;\n max-width: 100%;\n vertical-align: bottom;\n ", [H("input", "\n font-size: inherit;\n font-family: inherit;\n min-width: 1px;\n padding: 0;\n background-color: #0000;\n outline: none;\n border: none;\n max-width: 100%;\n overflow: hidden;\n width: 1em;\n line-height: inherit;\n cursor: pointer;\n color: var(--n-text-color);\n caret-color: var(--n-caret-color);\n "), H("mirror", "\n position: absolute;\n left: 0;\n top: 0;\n white-space: pre;\n visibility: hidden;\n user-select: none;\n -webkit-user-select: none;\n opacity: 0;\n ")]),
		["warning", "error"].map((e) => U(`${e}-status`, [H("state-border", `border: var(--n-border-${e});`), Ns("disabled", [
			B("&:hover", [H("state-border", `
 box-shadow: var(--n-box-shadow-hover-${e});
 border: var(--n-border-hover-${e});
 `)]),
			U("active", [
				H("state-border", `
 box-shadow: var(--n-box-shadow-active-${e});
 border: var(--n-border-active-${e});
 `),
				V("base-selection-label", `background-color: var(--n-color-active-${e});`),
				V("base-selection-tags", `background-color: var(--n-color-active-${e});`)
			]),
			U("focus", [H("state-border", `
 box-shadow: var(--n-box-shadow-focus-${e});
 border: var(--n-border-focus-${e});
 `)])
		])]))
	]),
	V("base-selection-popover", "\n margin-bottom: -3px;\n display: flex;\n flex-wrap: wrap;\n margin-right: -8px;\n "),
	V("base-selection-tag-wrapper", "\n max-width: 100%;\n display: inline-flex;\n padding: 0 7px 3px 0;\n ", [B("&:last-child", "padding-right: 0;"), V("tag", "\n font-size: 14px;\n max-width: 100%;\n ", [H("content", "\n line-height: 1.25;\n text-overflow: ellipsis;\n overflow: hidden;\n ")])])
]), IS = [
	"disabled",
	"value",
	"autofocus",
	"onBlur",
	"onFocus",
	"onKeydown",
	"onInput",
	"onCompositionstart",
	"onCompositionend"
], LS = ["tabindex"], RS = ["title"], zS = [
	"value",
	"readonly",
	"disabled",
	"autofocus",
	"onFocus",
	"onBlur",
	"onInput",
	"onCompositionstart",
	"onCompositionend"
], BS = ["tabindex"], VS = [
	"onClick",
	"onMouseenter",
	"onMouseleave",
	"onKeydown",
	"onFocusin",
	"onFocusout",
	"onMousedown"
], HS = /* @__PURE__ */ N({
	name: "InternalSelection",
	props: {
		...Kh.props,
		clsPrefix: {
			type: String,
			required: !0
		},
		bordered: {
			type: Boolean,
			default: void 0
		},
		active: Boolean,
		pattern: {
			type: String,
			default: ""
		},
		placeholder: String,
		selectedOption: {
			type: Object,
			default: null
		},
		selectedOptions: {
			type: Array,
			default: null
		},
		labelField: {
			type: String,
			default: "label"
		},
		valueField: {
			type: String,
			default: "value"
		},
		multiple: Boolean,
		filterable: Boolean,
		clearable: Boolean,
		disabled: Boolean,
		size: {
			type: String,
			default: "medium"
		},
		loading: Boolean,
		autofocus: Boolean,
		showArrow: {
			type: Boolean,
			default: !0
		},
		inputProps: Object,
		focused: Boolean,
		renderTag: Function,
		onKeydown: Function,
		onClick: Function,
		onBlur: Function,
		onFocus: Function,
		onDeleteOption: Function,
		maxTagCount: [String, Number],
		ellipsisTagPopoverProps: Object,
		onClear: Function,
		onPatternInput: Function,
		onPatternFocus: Function,
		onPatternBlur: Function,
		renderLabel: Function,
		status: String,
		inlineThemeDisabled: Boolean,
		ignoreComposition: {
			type: Boolean,
			default: !0
		},
		onResize: Function
	},
	setup(e) {
		let { mergedClsPrefixRef: t, mergedRtlRef: n } = _m(e), r = Kg("InternalSelection", n, t), i = /* @__PURE__ */ j(null), a = /* @__PURE__ */ j(null), o = /* @__PURE__ */ j(null), s = /* @__PURE__ */ j(null), c = /* @__PURE__ */ j(null), l = /* @__PURE__ */ j(null), u = /* @__PURE__ */ j(null), d = /* @__PURE__ */ j(null), f = /* @__PURE__ */ j(null), p = /* @__PURE__ */ j(null), m = /* @__PURE__ */ j(!1), h = /* @__PURE__ */ j(!1), g = /* @__PURE__ */ j(!1), _ = Kh("InternalSelection", "-internal-selection", FS, AS, e, /* @__PURE__ */ M(e, "clsPrefix")), v = z(() => e.clearable && !e.disabled && (g.value || e.active)), y = z(() => e.selectedOption ? e.renderTag ? e.renderTag({
			option: e.selectedOption,
			handleClose: () => {}
		}) : e.renderLabel ? e.renderLabel(e.selectedOption, !0) : qb(e.selectedOption[e.labelField], e.selectedOption, !0) : e.placeholder), b = z(() => {
			let t = e.selectedOption;
			if (t) return t[e.labelField];
		}), x = z(() => e.multiple ? !!(Array.isArray(e.selectedOptions) && e.selectedOptions.length) : e.selectedOption !== null);
		function S() {
			let { value: t } = i;
			if (t) {
				let { value: n } = a;
				n && (n.style.width = `${t.offsetWidth}px`, e.maxTagCount !== "responsive" && f.value?.sync({ showAllItemsBeforeCalculate: !1 }));
			}
		}
		function C() {
			let { value: e } = p;
			e && (e.style.display = "none");
		}
		function w() {
			let { value: e } = p;
			e && (e.style.display = "inline-block");
		}
		Wn(/* @__PURE__ */ M(e, "active"), (e) => {
			e || C();
		}), Wn(/* @__PURE__ */ M(e, "pattern"), () => {
			e.multiple && wn(S);
		});
		function T(t) {
			let { onFocus: n } = e;
			n && n(t);
		}
		function E(t) {
			let { onBlur: n } = e;
			n && n(t);
		}
		function D(t) {
			let { onDeleteOption: n } = e;
			n && n(t);
		}
		function O(t) {
			let { onClear: n } = e;
			n && n(t);
		}
		function ee(t) {
			let { onPatternInput: n } = e;
			n && n(t);
		}
		function te(e) {
			(!e.relatedTarget || !o.value?.contains(e.relatedTarget)) && T(e);
		}
		function ne(e) {
			o.value?.contains(e.relatedTarget) || E(e);
		}
		function re(e) {
			O(e);
		}
		function ie() {
			g.value = !0;
		}
		function ae() {
			g.value = !1;
		}
		function oe(t) {
			e.active && e.filterable && t.target !== a.value && t.preventDefault();
		}
		function se(e) {
			D(e);
		}
		let ce = /* @__PURE__ */ j(!1);
		function le(t) {
			if (t.key === "Backspace" && !ce.value && !e.pattern.length) {
				let { selectedOptions: t } = e;
				t?.length && se(t[t.length - 1]);
			}
		}
		let ue = null;
		function k(t) {
			let { value: n } = i;
			n && (n.textContent = t.target.value, S()), e.ignoreComposition && ce.value ? ue = t : ee(t);
		}
		function de() {
			ce.value = !0;
		}
		function fe() {
			ce.value = !1, e.ignoreComposition && ee(ue), ue = null;
		}
		function pe(t) {
			h.value = !0, e.onPatternFocus?.(t);
		}
		function me(t) {
			h.value = !1, e.onPatternBlur?.(t);
		}
		function he() {
			if (e.filterable) h.value = !1, l.value?.blur(), a.value?.blur();
			else if (e.multiple) {
				let { value: e } = s;
				e?.blur();
			} else {
				let { value: e } = c;
				e?.blur();
			}
		}
		function ge() {
			e.filterable ? (h.value = !1, l.value?.focus()) : e.multiple ? s.value?.focus() : c.value?.focus();
		}
		function _e() {
			let { value: e } = a;
			e && (w(), e.focus());
		}
		function ve() {
			let { value: e } = a;
			e && e.blur();
		}
		function ye(e) {
			let { value: t } = u;
			t && t.setTextContent(`+${e}`);
		}
		function be() {
			let { value: e } = d;
			return e;
		}
		function xe() {
			return a.value;
		}
		let Se = null;
		function Ce() {
			Se !== null && window.clearTimeout(Se);
		}
		function we() {
			e.active || (Ce(), Se = window.setTimeout(() => {
				x.value && (m.value = !0);
			}, 100));
		}
		function Te() {
			Ce();
		}
		function Ee(e) {
			e || (Ce(), m.value = !1);
		}
		Wn(x, (e) => {
			e || (m.value = !1);
		}), Fr(() => {
			Un(() => {
				let t = l.value;
				t && (e.disabled ? t.removeAttribute("tabindex") : t.tabIndex = h.value ? -1 : 0);
			});
		}), Gb(o, e.onResize);
		let { inlineThemeDisabled: De } = e, Oe = z(() => {
			let { size: t } = e, { common: { cubicBezierEaseInOut: n }, self: { fontWeight: r, borderRadius: i, color: a, placeholderColor: o, textColor: s, paddingSingle: c, paddingMultiple: l, caretColor: u, colorDisabled: d, textColorDisabled: f, placeholderColorDisabled: p, colorActive: m, boxShadowFocus: h, boxShadowActive: g, boxShadowHover: v, border: y, borderFocus: b, borderHover: x, borderActive: S, arrowColor: C, arrowColorDisabled: w, loadingColor: T, colorActiveWarning: E, boxShadowFocusWarning: D, boxShadowActiveWarning: O, boxShadowHoverWarning: ee, borderWarning: te, borderFocusWarning: ne, borderHoverWarning: re, borderActiveWarning: ie, colorActiveError: ae, boxShadowFocusError: oe, boxShadowActiveError: se, boxShadowHoverError: ce, borderError: le, borderFocusError: ue, borderHoverError: k, borderActiveError: de, clearColor: fe, clearColorHover: pe, clearColorPressed: me, clearSize: he, arrowSize: ge, [W("height", t)]: _e, [W("fontSize", t)]: ve } } = _.value, ye = Wm(c), be = Wm(l);
			return {
				"--n-bezier": n,
				"--n-border": y,
				"--n-border-active": S,
				"--n-border-focus": b,
				"--n-border-hover": x,
				"--n-border-radius": i,
				"--n-box-shadow-active": g,
				"--n-box-shadow-focus": h,
				"--n-box-shadow-hover": v,
				"--n-caret-color": u,
				"--n-color": a,
				"--n-color-active": m,
				"--n-color-disabled": d,
				"--n-font-size": ve,
				"--n-height": _e,
				"--n-padding-single-top": ye.top,
				"--n-padding-multiple-top": be.top,
				"--n-padding-single-right": ye.right,
				"--n-padding-multiple-right": be.right,
				"--n-padding-single-left": ye.left,
				"--n-padding-multiple-left": be.left,
				"--n-padding-single-bottom": ye.bottom,
				"--n-padding-multiple-bottom": be.bottom,
				"--n-placeholder-color": o,
				"--n-placeholder-color-disabled": p,
				"--n-text-color": s,
				"--n-text-color-disabled": f,
				"--n-arrow-color": C,
				"--n-arrow-color-disabled": w,
				"--n-loading-color": T,
				"--n-color-active-warning": E,
				"--n-box-shadow-focus-warning": D,
				"--n-box-shadow-active-warning": O,
				"--n-box-shadow-hover-warning": ee,
				"--n-border-warning": te,
				"--n-border-focus-warning": ne,
				"--n-border-hover-warning": re,
				"--n-border-active-warning": ie,
				"--n-color-active-error": ae,
				"--n-box-shadow-focus-error": oe,
				"--n-box-shadow-active-error": se,
				"--n-box-shadow-hover-error": ce,
				"--n-border-error": le,
				"--n-border-focus-error": ue,
				"--n-border-hover-error": k,
				"--n-border-active-error": de,
				"--n-clear-size": he,
				"--n-clear-color": fe,
				"--n-clear-color-hover": pe,
				"--n-clear-color-pressed": me,
				"--n-arrow-size": ge,
				"--n-font-weight": r
			};
		}), ke = De ? Uh("internal-selection", z(() => e.size[0]), Oe, e) : void 0;
		return {
			mergedTheme: _,
			mergedClearable: v,
			mergedClsPrefix: t,
			rtlEnabled: r,
			patternInputFocused: h,
			filterablePlaceholder: y,
			label: b,
			selected: x,
			showTagsPanel: m,
			isComposing: ce,
			counterRef: u,
			counterWrapperRef: d,
			patternInputMirrorRef: i,
			patternInputRef: a,
			selfRef: o,
			multipleElRef: s,
			singleElRef: c,
			patternInputWrapperRef: l,
			overflowRef: f,
			inputTagElRef: p,
			handleMouseDown: oe,
			handleFocusin: te,
			handleClear: re,
			handleMouseEnter: ie,
			handleMouseLeave: ae,
			handleDeleteOption: se,
			handlePatternKeyDown: le,
			handlePatternInputInput: k,
			handlePatternInputBlur: me,
			handlePatternInputFocus: pe,
			handleMouseEnterCounter: we,
			handleMouseLeaveCounter: Te,
			handleFocusout: ne,
			handleCompositionEnd: fe,
			handleCompositionStart: de,
			onPopoverUpdateShow: Ee,
			focus: ge,
			focusInput: _e,
			blur: he,
			blurInput: ve,
			updateCounter: ye,
			getCounter: be,
			getTail: xe,
			renderLabel: e.renderLabel,
			cssVars: De ? void 0 : Oe,
			themeClass: ke?.themeClass,
			onRender: ke?.onRender
		};
	},
	render() {
		let { status: e, multiple: t, size: n, disabled: r, filterable: i, maxTagCount: a, bordered: o, clsPrefix: s, ellipsisTagPopoverProps: c, onRender: l, renderTag: u, renderLabel: d } = this;
		l?.();
		let f = a === "responsive", p = typeof a == "number", m = f || p, h = (F(), L(Yg, null, { default: () => (F(), L(wb, {
			clsPrefix: s,
			loading: this.loading,
			showArrow: this.showArrow,
			showClear: this.mergedClearable && this.selected,
			onClear: this.handleClear
		}, { default: () => this.$slots.arrow?.() }, 1032, [
			"clsPrefix",
			"loading",
			"showArrow",
			"showClear",
			"onClear"
		])) }, 1024)), g;
		if (t) {
			let { labelField: e } = this, t = (t) => (F(), I("div", {
				class: K(`${s}-base-selection-tag-wrapper`),
				key: t.value
			}, [u ? (F(), I(P, { key: 0 }, [G(() => u({
				option: t,
				handleClose: () => {
					this.handleDeleteOption(t);
				}
			}))], 64)) : (F(), L(Ny, {
				key: 1,
				size: n,
				closable: !t.disabled,
				disabled: r,
				onClose: () => {
					this.handleDeleteOption(t);
				},
				internalCloseIsButtonTag: !1,
				internalCloseFocusable: !1
			}, { default: () => d ? d(t, !0) : qb(t[e], t, !0) }, 1032, [
				"size",
				"closable",
				"disabled",
				"onClose"
			]))], 2)), o = () => (p ? this.selectedOptions.slice(0, a) : this.selectedOptions).map(t), l = i ? (F(), I("div", {
				class: K(`${s}-base-selection-input-tag`),
				ref: "inputTagElRef",
				key: "__input-tag__"
			}, [R("input", aa(this.inputProps, {
				ref: "patternInputRef",
				tabindex: -1,
				disabled: r,
				value: this.pattern,
				autofocus: this.autofocus,
				class: `${s}-base-selection-input-tag__input`,
				onBlur: this.handlePatternInputBlur,
				onFocus: this.handlePatternInputFocus,
				onKeydown: this.handlePatternKeyDown,
				onInput: this.handlePatternInputInput,
				onCompositionstart: this.handleCompositionStart,
				onCompositionend: this.handleCompositionEnd
			}), null, 16, IS), R("span", {
				ref: "patternInputMirrorRef",
				class: K(`${s}-base-selection-input-tag__mirror`)
			}, [G(() => this.pattern)], 2)], 2)) : null, _ = f ? () => (F(), I("div", {
				class: K(`${s}-base-selection-tag-wrapper`),
				ref: "counterWrapperRef"
			}, [(F(), L(Ny, {
				size: n,
				ref: "counterRef",
				onMouseenter: this.handleMouseEnterCounter,
				onMouseleave: this.handleMouseLeaveCounter,
				disabled: r
			}, null, 8, [
				"size",
				"onMouseenter",
				"onMouseleave",
				"disabled"
			]))], 2)) : void 0, v;
			if (p) {
				let e = this.selectedOptions.length - a;
				e > 0 && (v = ((t) => (F(), I("div", {
					class: K(`${s}-base-selection-tag-wrapper`),
					key: "__counter__"
				}, [(F(), L(Ny, {
					size: n,
					ref: "counterRef",
					onMouseenter: this.handleMouseEnterCounter,
					disabled: r
				}, { default: () => `+${e}` }, 1032, [
					"size",
					"onMouseenter",
					"disabled"
				]))], 2)))(v));
			}
			let y = f ? i ? (F(), L(Kv, {
				key: 3,
				ref: "overflowRef",
				updateCounter: this.updateCounter,
				getCounter: this.getCounter,
				getTail: this.getTail,
				style: {
					width: "100%",
					display: "flex",
					overflow: "hidden"
				}
			}, {
				default: o,
				counter: _,
				tail: () => l
			}, 1032, [
				"updateCounter",
				"getCounter",
				"getTail"
			])) : (F(), L(Kv, {
				key: 4,
				ref: "overflowRef",
				updateCounter: this.updateCounter,
				getCounter: this.getCounter,
				style: {
					width: "100%",
					display: "flex",
					overflow: "hidden"
				}
			}, {
				default: o,
				counter: _
			}, 1032, ["updateCounter", "getCounter"])) : p && v ? o().concat(v) : o(), b = m ? () => (F(), I("div", { class: K(`${s}-base-selection-popover`) }, [f ? (F(), I(P, { key: 0 }, [G(() => o())], 64)) : (F(), I(P, { key: 1 }, [G(() => this.selectedOptions.map(t))], 64))], 2)) : void 0, x = m ? {
				show: this.showTagsPanel,
				trigger: "hover",
				overlap: !0,
				placement: "top",
				width: "trigger",
				onUpdateShow: this.onPopoverUpdateShow,
				theme: this.mergedTheme.peers.Popover,
				themeOverrides: this.mergedTheme.peerOverrides.Popover,
				...c
			} : null, S = !this.selected && (!this.active || !this.pattern && !this.isComposing) ? (F(), I("div", {
				key: 5,
				class: K(`${s}-base-selection-placeholder ${s}-base-selection-overlay`)
			}, [R("div", { class: K(`${s}-base-selection-placeholder__inner`) }, [G(() => this.placeholder)], 2)], 2)) : null, C = i ? (F(), I("div", {
				key: 6,
				ref: "patternInputWrapperRef",
				class: K(`${s}-base-selection-tags`)
			}, [
				G(() => y),
				f ? G(() => null) : (F(), I(P, { key: 1 }, [G(() => l)], 64)),
				G(() => h)
			], 2)) : (F(), I("div", {
				key: 7,
				ref: "multipleElRef",
				class: K(`${s}-base-selection-tags`),
				tabindex: r ? void 0 : 0
			}, [G(() => y), G(() => h)], 10, LS));
			g = ((e) => (F(), I(P, { key: 8 }, [m ? (F(), L(vy, aa({ key: 0 }, x, {
				scrollable: !0,
				style: "max-height: calc(var(--v-target-height) * 6.6);"
			}), {
				trigger: () => C,
				default: b
			}, 1040)) : (F(), I(P, { key: 1 }, [G(() => C)], 64)), G(() => S)], 64)))(g);
		} else if (i) {
			let e = this.pattern || this.isComposing, t = this.active ? !e : !this.selected, n = !this.active && this.selected;
			g = ((e) => (F(), I("div", {
				key: 9,
				ref: "patternInputWrapperRef",
				class: K(`${s}-base-selection-label`),
				title: this.patternInputFocused ? void 0 : eb(this.label)
			}, [
				R("input", aa(this.inputProps, {
					ref: "patternInputRef",
					class: `${s}-base-selection-input`,
					value: this.active ? this.pattern : "",
					placeholder: "",
					readonly: r,
					disabled: r,
					tabindex: -1,
					autofocus: this.autofocus,
					onFocus: this.handlePatternInputFocus,
					onBlur: this.handlePatternInputBlur,
					onInput: this.handlePatternInputInput,
					onCompositionstart: this.handleCompositionStart,
					onCompositionend: this.handleCompositionEnd
				}), null, 16, zS),
				n ? (F(), I("div", {
					class: K(`${s}-base-selection-label__render-label ${s}-base-selection-overlay`),
					key: "input"
				}, [R("div", { class: K(`${s}-base-selection-overlay__wrapper`) }, [u ? (F(), I(P, { key: 0 }, [G(() => u({
					option: this.selectedOption,
					handleClose: () => {}
				}))], 64)) : (F(), I(P, { key: 1 }, [d ? (F(), I(P, { key: 0 }, [G(() => d(this.selectedOption, !0))], 64)) : (F(), I(P, { key: 1 }, [G(() => qb(this.label, this.selectedOption, !0))], 64))], 64))], 2)], 2)) : G(() => null),
				t ? (F(), I("div", {
					class: K(`${s}-base-selection-placeholder ${s}-base-selection-overlay`),
					key: "placeholder"
				}, [R("div", { class: K(`${s}-base-selection-overlay__wrapper`) }, [G(() => this.filterablePlaceholder)], 2)], 2)) : G(() => null),
				G(() => h)
			], 10, RS)))(g);
		} else g = ((e) => (F(), I("div", {
			key: 10,
			ref: "singleElRef",
			class: K(`${s}-base-selection-label`),
			tabindex: this.disabled ? void 0 : 0
		}, [this.label === void 0 ? (F(), I("div", {
			class: K(`${s}-base-selection-placeholder ${s}-base-selection-overlay`),
			key: "placeholder"
		}, [R("div", { class: K(`${s}-base-selection-placeholder__inner`) }, [G(() => this.placeholder)], 2)], 2)) : (F(), I("div", {
			class: K(`${s}-base-selection-input`),
			title: eb(this.label),
			key: "input"
		}, [R("div", { class: K(`${s}-base-selection-input__content`) }, [u ? (F(), I(P, { key: 0 }, [G(() => u({
			option: this.selectedOption,
			handleClose: () => {}
		}))], 64)) : (F(), I(P, { key: 1 }, [d ? (F(), I(P, { key: 0 }, [G(() => d(this.selectedOption, !0))], 64)) : (F(), I(P, { key: 1 }, [G(() => qb(this.label, this.selectedOption, !0))], 64))], 64))], 2)], 10, ["title"])), G(() => h)], 10, BS)))(g);
		return F(), I("div", {
			ref: "selfRef",
			class: K([
				`${s}-base-selection`,
				this.rtlEnabled && `${s}-base-selection--rtl`,
				this.themeClass,
				e && `${s}-base-selection--${e}-status`,
				{
					[`${s}-base-selection--active`]: this.active,
					[`${s}-base-selection--selected`]: this.selected || this.active && this.pattern,
					[`${s}-base-selection--disabled`]: this.disabled,
					[`${s}-base-selection--multiple`]: this.multiple,
					[`${s}-base-selection--focus`]: this.focused
				}
			]),
			style: k(this.cssVars),
			onClick: this.onClick,
			onMouseenter: this.handleMouseEnter,
			onMouseleave: this.handleMouseLeave,
			onKeydown: this.onKeydown,
			onFocusin: this.handleFocusin,
			onFocusout: this.handleFocusout,
			onMousedown: this.handleMouseDown
		}, [
			G(() => g),
			o ? (F(), I("div", {
				key: 0,
				class: K(`${s}-base-selection__border`)
			}, null, 2)) : G(() => null),
			o ? (F(), I("div", {
				key: 2,
				class: K(`${s}-base-selection__state-border`)
			}, null, 2)) : G(() => null)
		], 46, VS);
	}
}), US = {
	name: "Code",
	common: Z,
	self(e) {
		let { textColor2: t, fontSize: n, fontWeightStrong: r, textColor3: i } = e;
		return {
			textColor: t,
			fontSize: n,
			fontWeightStrong: r,
			"mono-3": "#5c6370",
			"hue-1": "#56b6c2",
			"hue-2": "#61aeee",
			"hue-3": "#c678dd",
			"hue-4": "#98c379",
			"hue-5": "#e06c75",
			"hue-5-2": "#be5046",
			"hue-6": "#d19a66",
			"hue-6-2": "#e6c07b",
			lineNumberTextColor: i
		};
	}
};
//#endregion
//#region node_modules/naive-ui/es/collapse/styles/light.mjs
function WS(e) {
	let { fontWeight: t, textColor1: n, textColor2: r, textColorDisabled: i, dividerColor: a, fontSize: o } = e;
	return {
		titleFontSize: o,
		titleFontWeight: t,
		dividerColor: a,
		titleTextColor: n,
		titleTextColorDisabled: i,
		fontSize: o,
		textColor: r,
		arrowColor: r,
		arrowColorDisabled: i,
		itemMargin: "16px 0 0 0",
		titlePadding: "16px 0 0 0"
	};
}
//#endregion
//#region node_modules/naive-ui/es/collapse/styles/dark.mjs
var GS = {
	name: "Collapse",
	common: Z,
	self: WS
};
//#endregion
//#region node_modules/naive-ui/es/collapse-transition/styles/light.mjs
function KS(e) {
	let { cubicBezierEaseInOut: t } = e;
	return { bezier: t };
}
//#endregion
//#region node_modules/naive-ui/es/color-picker/styles/light.mjs
function qS(e) {
	let { fontSize: t, boxShadow2: n, popoverColor: r, textColor2: i, borderRadius: a, borderColor: o, heightSmall: s, heightMedium: c, heightLarge: l, fontSizeSmall: u, fontSizeMedium: d, fontSizeLarge: f, dividerColor: p } = e;
	return {
		panelFontSize: t,
		boxShadow: n,
		color: r,
		textColor: i,
		borderRadius: a,
		border: `1px solid ${o}`,
		heightSmall: s,
		heightMedium: c,
		heightLarge: l,
		fontSizeSmall: u,
		fontSizeMedium: d,
		fontSizeLarge: f,
		dividerColor: p
	};
}
var JS = /* @__PURE__ */ N({
	name: "ConfigProvider",
	alias: ["App"],
	props: {
		abstract: Boolean,
		bordered: {
			type: Boolean,
			default: void 0
		},
		clsPrefix: String,
		locale: Object,
		dateLocale: Object,
		namespace: String,
		rtl: Array,
		tag: {
			type: String,
			default: "div"
		},
		hljs: Object,
		katex: Object,
		theme: Object,
		themeOverrides: Object,
		componentOptions: Object,
		icons: Object,
		breakpoints: Object,
		preflightStyleDisabled: Boolean,
		styleMountTarget: Object,
		inlineThemeDisabled: {
			type: Boolean,
			default: void 0
		},
		as: {
			type: String,
			validator: () => (fm("config-provider", "`as` is deprecated, please use `tag` instead."), !0),
			default: void 0
		}
	},
	setup(e) {
		let t = Bn(gm, null), n = z(() => {
			let { theme: n } = e;
			if (n === null) return;
			let r = t?.mergedThemeRef.value;
			return n === void 0 ? r : r === void 0 ? n : Object.assign({}, r, n);
		}), r = z(() => {
			let { themeOverrides: n } = e;
			if (n !== null) {
				if (n === void 0) return t?.mergedThemeOverridesRef.value;
				{
					let e = t?.mergedThemeOverridesRef.value;
					return e === void 0 ? n : dm({}, e, n);
				}
			}
		}), i = Sg(() => {
			let { namespace: n } = e;
			return n === void 0 ? t?.mergedNamespaceRef.value : n;
		}), a = Sg(() => {
			let { bordered: n } = e;
			return n === void 0 ? t?.mergedBorderedRef.value : n;
		}), o = z(() => {
			let { icons: n } = e;
			return n === void 0 ? t?.mergedIconsRef.value : n;
		}), s = z(() => {
			let { componentOptions: n } = e;
			return n === void 0 ? t?.mergedComponentPropsRef.value : n;
		}), c = z(() => {
			let { clsPrefix: n } = e;
			return n === void 0 ? t ? t.mergedClsPrefixRef.value : "n" : n;
		}), l = z(() => {
			let { rtl: n } = e;
			if (n === void 0) return t?.mergedRtlRef.value;
			let r = {};
			for (let e of n) r[e.name] = Gt(e), e.peers?.forEach((e) => {
				e.name in r || (r[e.name] = Gt(e));
			});
			return r;
		}), u = z(() => e.breakpoints || t?.mergedBreakpointsRef.value), d = e.inlineThemeDisabled || t?.inlineThemeDisabled, f = e.preflightStyleDisabled || t?.preflightStyleDisabled, p = e.styleMountTarget || t?.styleMountTarget;
		return zn(gm, {
			mergedThemeHashRef: z(() => {
				let { value: e } = n, { value: t } = r, i = t && Object.keys(t).length !== 0, a = e?.name;
				return a ? i ? `${a}-${gs(JSON.stringify(r.value))}` : a : i ? gs(JSON.stringify(r.value)) : "";
			}),
			mergedBreakpointsRef: u,
			mergedRtlRef: l,
			mergedIconsRef: o,
			mergedComponentPropsRef: s,
			mergedBorderedRef: a,
			mergedNamespaceRef: i,
			mergedClsPrefixRef: c,
			mergedLocaleRef: z(() => {
				let { locale: n } = e;
				if (n !== null) return n === void 0 ? t?.mergedLocaleRef.value : n;
			}),
			mergedDateLocaleRef: z(() => {
				let { dateLocale: n } = e;
				if (n !== null) return n === void 0 ? t?.mergedDateLocaleRef.value : n;
			}),
			mergedHljsRef: z(() => {
				let { hljs: n } = e;
				return n === void 0 ? t?.mergedHljsRef.value : n;
			}),
			mergedKatexRef: z(() => {
				let { katex: n } = e;
				return n === void 0 ? t?.mergedKatexRef.value : n;
			}),
			mergedThemeRef: n,
			mergedThemeOverridesRef: r,
			inlineThemeDisabled: d || !1,
			preflightStyleDisabled: f || !1,
			styleMountTarget: p
		}), {
			mergedClsPrefix: c,
			mergedBordered: a,
			mergedNamespace: i,
			mergedTheme: n,
			mergedThemeOverrides: r
		};
	},
	render() {
		return this.abstract ? this.$slots.default?.() : Ea(this.as || this.tag, { class: `${this.mergedClsPrefix || "n"}-config-provider` }, this.$slots.default?.());
	}
}), YS = {
	name: "Popselect",
	common: Z,
	peers: {
		Popover: og,
		InternalSelectMenu: ng
	}
};
//#endregion
//#region node_modules/naive-ui/es/select/styles/light.mjs
function XS(e) {
	let { boxShadow2: t } = e;
	return { menuBoxShadow: t };
}
var ZS = Gh({
	name: "Select",
	common: Ph,
	peers: {
		InternalSelection: AS,
		InternalSelectMenu: tg
	},
	self: XS
}), QS = {
	name: "Select",
	common: Z,
	peers: {
		InternalSelection: Fy,
		InternalSelectMenu: ng
	},
	self: XS
}, $S = B([V("select", "\n z-index: auto;\n outline: none;\n width: 100%;\n position: relative;\n font-weight: var(--n-font-weight);\n "), V("select-menu", "\n margin: 4px 0;\n box-shadow: var(--n-menu-box-shadow);\n ", [nx({ originalTransition: "background-color .3s var(--n-bezier), box-shadow .3s var(--n-bezier)" })])]), eC = /* @__PURE__ */ N({
	name: "Select",
	props: {
		...Kh.props,
		to: Mg.propTo,
		bordered: {
			type: Boolean,
			default: void 0
		},
		clearable: Boolean,
		clearCreatedOptionsOnClear: {
			type: Boolean,
			default: !0
		},
		clearFilterAfterSelect: {
			type: Boolean,
			default: !0
		},
		options: {
			type: Array,
			default: () => []
		},
		defaultValue: {
			type: [
				String,
				Number,
				Array
			],
			default: null
		},
		keyboard: {
			type: Boolean,
			default: !0
		},
		value: [
			String,
			Number,
			Array
		],
		placeholder: String,
		menuProps: Object,
		multiple: Boolean,
		size: String,
		menuSize: { type: String },
		filterable: Boolean,
		disabled: {
			type: Boolean,
			default: void 0
		},
		remote: Boolean,
		loading: Boolean,
		filter: Function,
		placement: {
			type: String,
			default: "bottom-start"
		},
		widthMode: {
			type: String,
			default: "trigger"
		},
		tag: Boolean,
		onCreate: Function,
		fallbackOption: {
			type: [Function, Boolean],
			default: void 0
		},
		show: {
			type: Boolean,
			default: void 0
		},
		showArrow: {
			type: Boolean,
			default: !0
		},
		maxTagCount: [Number, String],
		ellipsisTagPopoverProps: Object,
		consistentMenuWidth: {
			type: Boolean,
			default: !0
		},
		virtualScroll: {
			type: Boolean,
			default: !0
		},
		labelField: {
			type: String,
			default: "label"
		},
		valueField: {
			type: String,
			default: "value"
		},
		childrenField: {
			type: String,
			default: "children"
		},
		renderLabel: Function,
		renderOption: Function,
		renderTag: Function,
		"onUpdate:value": [Function, Array],
		inputProps: Object,
		nodeProps: Function,
		ignoreComposition: {
			type: Boolean,
			default: !0
		},
		showOnFocus: Boolean,
		onUpdateValue: [Function, Array],
		onBlur: [Function, Array],
		onClear: [Function, Array],
		onFocus: [Function, Array],
		onScroll: [Function, Array],
		onSearch: [Function, Array],
		onUpdateShow: [Function, Array],
		"onUpdate:show": [Function, Array],
		displayDirective: {
			type: String,
			default: "show"
		},
		resetMenuOnOptionsChange: {
			type: Boolean,
			default: !0
		},
		status: String,
		showCheckmark: {
			type: Boolean,
			default: !0
		},
		scrollbarProps: Object,
		onChange: [Function, Array],
		items: Array
	},
	slots: Object,
	setup(e) {
		let { mergedClsPrefixRef: t, mergedBorderedRef: n, namespaceRef: r, inlineThemeDisabled: i, mergedComponentPropsRef: a } = _m(e), o = Kh("Select", "-select", $S, ZS, e, t), s = /* @__PURE__ */ j(e.defaultValue), c = Eg(/* @__PURE__ */ M(e, "value"), s), l = /* @__PURE__ */ j(!1), u = /* @__PURE__ */ j(""), d = Og(e, ["items", "options"]), f = /* @__PURE__ */ j([]), p = /* @__PURE__ */ j([]), m = z(() => p.value.concat(f.value).concat(d.value)), h = z(() => {
			let { filter: t } = e;
			if (t) return t;
			let { labelField: n, valueField: r } = e;
			return (e, t) => {
				if (!t) return !1;
				let i = t[n];
				if (typeof i == "string") return Ux(e, i);
				let a = t[r];
				return typeof a == "string" ? Ux(e, a) : typeof a == "number" && Ux(e, String(a));
			};
		}), g = z(() => {
			if (e.remote) return d.value;
			{
				let { value: t } = m, { value: n } = u;
				return !n.length || !e.filterable ? t : Gx(t, h.value, n, e.childrenField);
			}
		}), _ = z(() => {
			let { valueField: t, childrenField: n } = e, r = Wx(t, n);
			return Rx(g.value, r);
		}), v = z(() => Kx(m.value, e.valueField, e.childrenField)), y = /* @__PURE__ */ j(!1), b = Eg(/* @__PURE__ */ M(e, "show"), y), x = /* @__PURE__ */ j(null), S = /* @__PURE__ */ j(null), C = /* @__PURE__ */ j(null), { localeRef: w } = Wh("Select"), T = z(() => e.placeholder ?? w.value.placeholder), E = [], D = /* @__PURE__ */ j(/* @__PURE__ */ new Map()), O = z(() => {
			let { fallbackOption: t } = e;
			if (t === void 0) {
				let { labelField: t, valueField: n } = e;
				return (e) => ({
					[t]: String(e),
					[n]: e
				});
			}
			return t === !1 ? !1 : (e) => Object.assign(t(e), { value: e });
		});
		function ee(t) {
			let n = e.remote, { value: r } = D, { value: i } = v, { value: a } = O, o = [];
			return t.forEach((e) => {
				if (i.has(e)) o.push(i.get(e));
				else if (n && r.has(e)) o.push(r.get(e));
				else if (a) {
					let t = a(e);
					t && o.push(t);
				}
			}), o;
		}
		let te = z(() => {
			if (e.multiple) {
				let { value: e } = c;
				return Array.isArray(e) ? ee(e) : [];
			}
			return null;
		}), ne = z(() => {
			let { value: t } = c;
			return !e.multiple && !Array.isArray(t) ? t === null ? null : ee([t])[0] || null : null;
		}), re = ab(e, { mergedSize: (t) => {
			let { size: n } = e;
			if (n) return n;
			let { mergedSize: r } = t || {};
			return r?.value ? r.value : a?.value?.Select?.size || "medium";
		} }), { mergedSizeRef: ie, mergedDisabledRef: ae, mergedStatusRef: oe } = re;
		function se(t, n) {
			let { onChange: r, "onUpdate:value": i, onUpdateValue: a } = e, { nTriggerFormChange: o, nTriggerFormInput: c } = re;
			r && $(r, t, n), a && $(a, t, n), i && $(i, t, n), s.value = t, o(), c();
		}
		function ce(t) {
			let { onBlur: n } = e, { nTriggerFormBlur: r } = re;
			n && $(n, t), r();
		}
		function le() {
			let { onClear: t } = e;
			t && $(t);
		}
		function ue(t) {
			let { onFocus: n, showOnFocus: r } = e, { nTriggerFormFocus: i } = re;
			n && $(n, t), i(), r && me();
		}
		function k(t) {
			let { onSearch: n } = e;
			n && $(n, t);
		}
		function de(t) {
			let { onScroll: n } = e;
			n && $(n, t);
		}
		function fe() {
			let { remote: t, multiple: n } = e;
			if (t) {
				let { value: t } = D;
				if (n) {
					let { valueField: n } = e;
					te.value?.forEach((e) => {
						t.set(e[n], e);
					});
				} else {
					let n = ne.value;
					n && t.set(n[e.valueField], n);
				}
			}
		}
		function pe(t) {
			let { onUpdateShow: n, "onUpdate:show": r } = e;
			n && $(n, t), r && $(r, t), y.value = t;
		}
		function me() {
			ae.value || (pe(!0), y.value = !0, e.filterable && Le());
		}
		function he() {
			pe(!1);
		}
		function ge() {
			u.value = "", p.value = E;
		}
		let _e = /* @__PURE__ */ j(!1);
		function ve() {
			e.filterable && (_e.value = !0);
		}
		function ye() {
			e.filterable && (_e.value = !1, b.value || ge());
		}
		function be() {
			ae.value || (b.value ? e.filterable ? Le() : he() : me());
		}
		function xe(e) {
			C.value?.selfRef?.contains(e.relatedTarget) || (l.value = !1, ce(e), he());
		}
		function Se(e) {
			ue(e), l.value = !0;
		}
		function Ce() {
			l.value = !0;
		}
		function we(e) {
			x.value?.$el.contains(e.relatedTarget) || (l.value = !1, ce(e), he());
		}
		function Te() {
			x.value?.focus(), he();
		}
		function Ee(e) {
			b.value && (x.value?.$el.contains(Vm(e)) || he());
		}
		function De(t) {
			if (!Array.isArray(t)) return [];
			if (O.value) return Array.from(t);
			{
				let { remote: n } = e, { value: r } = v;
				if (n) {
					let { value: e } = D;
					return t.filter((t) => r.has(t) || e.has(t));
				}
				return t.filter((e) => r.has(e));
			}
		}
		function Oe(e) {
			ke(e.rawNode);
		}
		function ke(t) {
			if (ae.value) return;
			let { tag: n, remote: r, clearFilterAfterSelect: i, valueField: a } = e;
			if (n && !r) {
				let { value: e } = p, t = e[0] || null;
				if (t) {
					let e = f.value;
					e.length ? e.push(t) : f.value = [t], p.value = E;
				}
			}
			if (r && D.value.set(t[a], t), e.multiple) {
				let e = De(c.value), o = e.findIndex((e) => e === t[a]);
				if (~o) {
					if (e.splice(o, 1), n && !r) {
						let e = Ae(t[a]);
						~e && (f.value.splice(e, 1), i && (u.value = ""));
					}
				} else e.push(t[a]), i && (u.value = "");
				se(e, ee(e));
			} else {
				if (n && !r) {
					let e = Ae(t[a]);
					~e ? f.value = [f.value[e]] : f.value = E;
				}
				Ie(), he(), se(t[a], t);
			}
		}
		function Ae(t) {
			return f.value.findIndex((n) => n[e.valueField] === t);
		}
		function je(t) {
			b.value || me();
			let { value: n } = t.target;
			u.value = n;
			let { tag: r, remote: i } = e;
			if (k(n), r && !i) {
				if (!n) {
					p.value = E;
					return;
				}
				let { onCreate: t } = e, r = t ? t(n) : {
					[e.labelField]: n,
					[e.valueField]: n
				}, { valueField: i, labelField: a } = e;
				d.value.some((e) => e[i] === r[i] || e[a] === r[a]) || f.value.some((e) => e[i] === r[i] || e[a] === r[a]) ? p.value = E : p.value = [r];
			}
		}
		function Me(t) {
			t.stopPropagation();
			let { multiple: n, tag: r, remote: i, clearCreatedOptionsOnClear: a } = e;
			!n && e.filterable && he(), r && !i && a && (f.value = E), le(), n ? se([], []) : se(null, null);
		}
		function Ne(e) {
			!Bm(e, "action") && !Bm(e, "empty") && !Bm(e, "header") && e.preventDefault();
		}
		function Pe(e) {
			de(e);
		}
		function Fe(t) {
			if (!e.keyboard) {
				t.preventDefault();
				return;
			}
			switch (t.key) {
				case " ":
					if (e.filterable) break;
					t.preventDefault();
				case "Enter":
					if (!x.value?.isComposing) {
						if (b.value) {
							let t = C.value?.getPendingTmNode();
							t ? Oe(t) : e.filterable || (he(), Ie());
						} else if (me(), e.tag && _e.value) {
							let t = p.value[0];
							if (t) {
								let n = t[e.valueField], { value: r } = c;
								e.multiple && Array.isArray(r) && r.includes(n) || ke(t);
							}
						}
					}
					t.preventDefault();
					break;
				case "ArrowUp":
					if (t.preventDefault(), e.loading) return;
					b.value && C.value?.prev();
					break;
				case "ArrowDown":
					if (t.preventDefault(), e.loading) return;
					b.value ? C.value?.next() : me();
					break;
				case "Escape": b.value && (PS(t), he()), x.value?.focus();
			}
		}
		function Ie() {
			x.value?.focus();
		}
		function Le() {
			x.value?.focusInput();
		}
		function Re() {
			b.value && S.value?.syncPosition();
		}
		fe(), Wn(/* @__PURE__ */ M(e, "options"), fe);
		let ze = {
			focus: () => {
				x.value?.focus();
			},
			focusInput: () => {
				x.value?.focusInput();
			},
			blur: () => {
				x.value?.blur();
			},
			blurInput: () => {
				x.value?.blurInput();
			}
		}, Be = z(() => {
			let { self: { menuBoxShadow: e } } = o.value;
			return { "--n-menu-box-shadow": e };
		}), Ve = i ? Uh("select", void 0, Be, e) : void 0;
		return {
			...ze,
			mergedStatus: oe,
			mergedClsPrefix: t,
			mergedBordered: n,
			namespace: r,
			treeMate: _,
			isMounted: Dg(),
			triggerRef: x,
			menuRef: C,
			pattern: u,
			uncontrolledShow: y,
			mergedShow: b,
			adjustedTo: Mg(e),
			uncontrolledValue: s,
			mergedValue: c,
			followerRef: S,
			localizedPlaceholder: T,
			selectedOption: ne,
			selectedOptions: te,
			mergedSize: ie,
			mergedDisabled: ae,
			focused: l,
			activeWithoutMenuOpen: _e,
			inlineThemeDisabled: i,
			onTriggerInputFocus: ve,
			onTriggerInputBlur: ye,
			handleTriggerOrMenuResize: Re,
			handleMenuFocus: Ce,
			handleMenuBlur: we,
			handleMenuTabOut: Te,
			handleTriggerClick: be,
			handleToggle: Oe,
			handleDeleteOption: ke,
			handlePatternInput: je,
			handleClear: Me,
			handleTriggerBlur: xe,
			handleTriggerFocus: Se,
			handleKeydown: Fe,
			handleMenuAfterLeave: ge,
			handleMenuClickOutside: Ee,
			handleMenuScroll: Pe,
			handleMenuKeydown: Fe,
			handleMenuMousedown: Ne,
			mergedTheme: o,
			cssVars: i ? void 0 : Be,
			themeClass: Ve?.themeClass,
			onRender: Ve?.onRender
		};
	},
	render() {
		return F(), I("div", { class: K(`${this.mergedClsPrefix}-select`) }, [Xi(c_, null, {
			_: 1,
			default: Pm(() => [(F(), L(l_, null, {
				_: 1,
				default: Pm(() => (F(), L(HS, {
					ref: "triggerRef",
					inlineThemeDisabled: this.inlineThemeDisabled,
					status: this.mergedStatus,
					inputProps: this.inputProps,
					clsPrefix: this.mergedClsPrefix,
					showArrow: this.showArrow,
					maxTagCount: this.maxTagCount,
					ellipsisTagPopoverProps: this.ellipsisTagPopoverProps,
					bordered: this.mergedBordered,
					active: this.activeWithoutMenuOpen || this.mergedShow,
					pattern: this.pattern,
					placeholder: this.localizedPlaceholder,
					selectedOption: this.selectedOption,
					selectedOptions: this.selectedOptions,
					multiple: this.multiple,
					renderTag: this.renderTag,
					renderLabel: this.renderLabel,
					filterable: this.filterable,
					clearable: this.clearable,
					disabled: this.mergedDisabled,
					size: this.mergedSize,
					theme: this.mergedTheme.peers.InternalSelection,
					labelField: this.labelField,
					valueField: this.valueField,
					themeOverrides: this.mergedTheme.peerOverrides.InternalSelection,
					loading: this.loading,
					focused: this.focused,
					onClick: this.handleTriggerClick,
					onDeleteOption: this.handleDeleteOption,
					onPatternInput: this.handlePatternInput,
					onClear: this.handleClear,
					onBlur: this.handleTriggerBlur,
					onFocus: this.handleTriggerFocus,
					onKeydown: this.handleKeydown,
					onPatternBlur: this.onTriggerInputBlur,
					onPatternFocus: this.onTriggerInputFocus,
					onResize: this.handleTriggerOrMenuResize,
					ignoreComposition: this.ignoreComposition
				}, {
					_: 1,
					arrow: Pm(() => [this.$slots.arrow?.()])
				}, 8, /* @__PURE__ */ "inlineThemeDisabled.status.inputProps.clsPrefix.showArrow.maxTagCount.ellipsisTagPopoverProps.bordered.active.pattern.placeholder.selectedOption.selectedOptions.multiple.renderTag.renderLabel.filterable.clearable.disabled.size.theme.labelField.valueField.themeOverrides.loading.focused.onClick.onDeleteOption.onPatternInput.onClear.onBlur.onFocus.onKeydown.onPatternBlur.onPatternFocus.onResize.ignoreComposition".split("."))))
			})), (F(), L(L_, {
				ref: "followerRef",
				show: this.mergedShow,
				to: this.adjustedTo,
				teleportDisabled: this.adjustedTo === Mg.tdkey,
				containerClass: this.namespace,
				width: this.consistentMenuWidth ? "target" : void 0,
				minWidth: "target",
				placement: this.placement
			}, {
				_: 1,
				default: Pm(() => (F(), L(Va, {
					name: "fade-in-scale-up-transition",
					appear: this.isMounted,
					onAfterLeave: this.handleMenuAfterLeave
				}, {
					_: 1,
					default: Pm(() => this.mergedShow || this.displayDirective === "show" ? (this.onRender?.(), Ln((F(), L(Bx, aa(this.menuProps, {
						ref: "menuRef",
						onResize: this.handleTriggerOrMenuResize,
						inlineThemeDisabled: this.inlineThemeDisabled,
						virtualScroll: this.consistentMenuWidth && this.virtualScroll,
						class: [
							`${this.mergedClsPrefix}-select-menu`,
							this.themeClass,
							this.menuProps?.class
						],
						clsPrefix: this.mergedClsPrefix,
						focusable: !0,
						labelField: this.labelField,
						valueField: this.valueField,
						autoPending: !0,
						nodeProps: this.nodeProps,
						theme: this.mergedTheme.peers.InternalSelectMenu,
						themeOverrides: this.mergedTheme.peerOverrides.InternalSelectMenu,
						treeMate: this.treeMate,
						multiple: this.multiple,
						size: this.menuSize,
						renderOption: this.renderOption,
						renderLabel: this.renderLabel,
						value: this.mergedValue,
						style: [this.menuProps?.style, this.cssVars],
						onToggle: this.handleToggle,
						onScroll: this.handleMenuScroll,
						onFocus: this.handleMenuFocus,
						onBlur: this.handleMenuBlur,
						onKeydown: this.handleMenuKeydown,
						onTabOut: this.handleMenuTabOut,
						onMousedown: this.handleMenuMousedown,
						show: this.mergedShow,
						showCheckmark: this.showCheckmark,
						resetMenuOnOptionsChange: this.resetMenuOnOptionsChange,
						scrollbarProps: this.scrollbarProps
					}), {
						_: 1,
						empty: Pm(() => [this.$slots.empty?.()]),
						header: Pm(() => [this.$slots.header?.()]),
						action: Pm(() => [this.$slots.action?.()])
					}, 16, /* @__PURE__ */ "onResize.inlineThemeDisabled.virtualScroll.class.clsPrefix.labelField.valueField.nodeProps.theme.themeOverrides.treeMate.multiple.size.renderOption.renderLabel.value.style.onToggle.onScroll.onFocus.onBlur.onKeydown.onTabOut.onMousedown.show.showCheckmark.resetMenuOnOptionsChange.scrollbarProps".split("."))), this.displayDirective === "show" ? [[ao, this.mergedShow], [
						p_,
						this.handleMenuClickOutside,
						void 0,
						{ capture: !0 }
					]] : [[
						p_,
						this.handleMenuClickOutside,
						void 0,
						{ capture: !0 }
					]])) : null)
				}, 8, ["appear", "onAfterLeave"])))
			}, 8, [
				"show",
				"to",
				"teleportDisabled",
				"containerClass",
				"width",
				"placement"
			]))])
		})], 2);
	}
}), tC = {
	itemPaddingSmall: "0 4px",
	itemMarginSmall: "0 0 0 8px",
	itemMarginSmallRtl: "0 8px 0 0",
	itemPaddingMedium: "0 4px",
	itemMarginMedium: "0 0 0 8px",
	itemMarginMediumRtl: "0 8px 0 0",
	itemPaddingLarge: "0 4px",
	itemMarginLarge: "0 0 0 8px",
	itemMarginLargeRtl: "0 8px 0 0",
	buttonIconSizeSmall: "14px",
	buttonIconSizeMedium: "16px",
	buttonIconSizeLarge: "18px",
	inputWidthSmall: "60px",
	selectWidthSmall: "unset",
	inputMarginSmall: "0 0 0 8px",
	inputMarginSmallRtl: "0 8px 0 0",
	selectMarginSmall: "0 0 0 8px",
	prefixMarginSmall: "0 8px 0 0",
	suffixMarginSmall: "0 0 0 8px",
	inputWidthMedium: "60px",
	selectWidthMedium: "unset",
	inputMarginMedium: "0 0 0 8px",
	inputMarginMediumRtl: "0 8px 0 0",
	selectMarginMedium: "0 0 0 8px",
	prefixMarginMedium: "0 8px 0 0",
	suffixMarginMedium: "0 0 0 8px",
	inputWidthLarge: "60px",
	selectWidthLarge: "unset",
	inputMarginLarge: "0 0 0 8px",
	inputMarginLargeRtl: "0 8px 0 0",
	selectMarginLarge: "0 0 0 8px",
	prefixMarginLarge: "0 8px 0 0",
	suffixMarginLarge: "0 0 0 8px"
};
//#endregion
//#region node_modules/naive-ui/es/pagination/styles/light.mjs
function nC(e) {
	let { textColor2: t, primaryColor: n, primaryColorHover: r, primaryColorPressed: i, inputColorDisabled: a, textColorDisabled: o, borderColor: s, borderRadius: c, fontSizeTiny: l, fontSizeSmall: u, fontSizeMedium: d, heightTiny: f, heightSmall: p, heightMedium: m } = e;
	return {
		...tC,
		buttonColor: "#0000",
		buttonColorHover: "#0000",
		buttonColorPressed: "#0000",
		buttonBorder: `1px solid ${s}`,
		buttonBorderHover: `1px solid ${s}`,
		buttonBorderPressed: `1px solid ${s}`,
		buttonIconColor: t,
		buttonIconColorHover: t,
		buttonIconColorPressed: t,
		itemTextColor: t,
		itemTextColorHover: r,
		itemTextColorPressed: i,
		itemTextColorActive: n,
		itemTextColorDisabled: o,
		itemColor: "#0000",
		itemColorHover: "#0000",
		itemColorPressed: "#0000",
		itemColorActive: "#0000",
		itemColorActiveHover: "#0000",
		itemColorDisabled: a,
		itemBorder: "1px solid #0000",
		itemBorderHover: "1px solid #0000",
		itemBorderPressed: "1px solid #0000",
		itemBorderActive: `1px solid ${n}`,
		itemBorderDisabled: `1px solid ${s}`,
		itemBorderRadius: c,
		itemSizeSmall: f,
		itemSizeMedium: p,
		itemSizeLarge: m,
		itemFontSizeSmall: l,
		itemFontSizeMedium: u,
		itemFontSizeLarge: d,
		jumperFontSizeSmall: l,
		jumperFontSizeMedium: u,
		jumperFontSizeLarge: d,
		jumperTextColor: t,
		jumperTextColorDisabled: o
	};
}
var rC = {
	name: "Pagination",
	common: Z,
	peers: {
		Select: QS,
		Input: rb,
		Popselect: YS
	},
	self(e) {
		let { primaryColor: t, opacity3: n } = e, r = J(t, { alpha: Number(n) }), i = nC(e);
		return i.itemBorderActive = `1px solid ${r}`, i.itemBorderDisabled = "1px solid #0000", i;
	}
}, iC = {
	padding: "4px 0",
	optionIconSizeSmall: "14px",
	optionIconSizeMedium: "16px",
	optionIconSizeLarge: "16px",
	optionIconSizeHuge: "18px",
	optionSuffixWidthSmall: "14px",
	optionSuffixWidthMedium: "14px",
	optionSuffixWidthLarge: "16px",
	optionSuffixWidthHuge: "16px",
	optionIconSuffixWidthSmall: "32px",
	optionIconSuffixWidthMedium: "32px",
	optionIconSuffixWidthLarge: "36px",
	optionIconSuffixWidthHuge: "36px",
	optionPrefixWidthSmall: "14px",
	optionPrefixWidthMedium: "14px",
	optionPrefixWidthLarge: "16px",
	optionPrefixWidthHuge: "16px",
	optionIconPrefixWidthSmall: "36px",
	optionIconPrefixWidthMedium: "36px",
	optionIconPrefixWidthLarge: "40px",
	optionIconPrefixWidthHuge: "40px"
};
//#endregion
//#region node_modules/naive-ui/es/dropdown/styles/light.mjs
function aC(e) {
	let { primaryColor: t, textColor2: n, dividerColor: r, hoverColor: i, popoverColor: a, invertedColor: o, borderRadius: s, fontSizeSmall: c, fontSizeMedium: l, fontSizeLarge: u, fontSizeHuge: d, heightSmall: f, heightMedium: p, heightLarge: m, heightHuge: h, textColor3: g, opacityDisabled: _ } = e;
	return {
		...iC,
		optionHeightSmall: f,
		optionHeightMedium: p,
		optionHeightLarge: m,
		optionHeightHuge: h,
		borderRadius: s,
		fontSizeSmall: c,
		fontSizeMedium: l,
		fontSizeLarge: u,
		fontSizeHuge: d,
		optionTextColor: n,
		optionTextColorHover: n,
		optionTextColorActive: t,
		optionTextColorChildActive: t,
		color: a,
		dividerColor: r,
		suffixColor: n,
		prefixColor: n,
		optionColorHover: i,
		optionColorActive: J(t, { alpha: .1 }),
		groupHeaderTextColor: g,
		optionTextColorInverted: "#BBB",
		optionTextColorHoverInverted: "#FFF",
		optionTextColorActiveInverted: "#FFF",
		optionTextColorChildActiveInverted: "#FFF",
		colorInverted: o,
		dividerColorInverted: "#BBB",
		suffixColorInverted: "#BBB",
		prefixColorInverted: "#BBB",
		optionColorHoverInverted: t,
		optionColorActiveInverted: t,
		groupHeaderTextColorInverted: "#AAA",
		optionOpacityDisabled: _
	};
}
var oC = {
	name: "Dropdown",
	common: Z,
	peers: { Popover: og },
	self(e) {
		let { primaryColorSuppl: t, primaryColor: n, popoverColor: r } = e, i = aC(e);
		return i.colorInverted = r, i.optionColorActive = J(n, { alpha: .15 }), i.optionColorActiveInverted = t, i.optionColorHoverInverted = t, i;
	}
}, sC = { padding: "8px 14px" }, cC = {
	name: "Tooltip",
	common: Z,
	peers: { Popover: og },
	self(e) {
		let { borderRadius: t, boxShadow2: n, popoverColor: r, textColor2: i } = e;
		return {
			...sC,
			borderRadius: t,
			boxShadow: n,
			color: r,
			textColor: i
		};
	}
}, lC = {
	radioSizeSmall: "14px",
	radioSizeMedium: "16px",
	radioSizeLarge: "18px",
	labelPadding: "0 8px",
	labelFontWeight: "400"
}, uC = {
	name: "Radio",
	common: Z,
	self(e) {
		let { borderColor: t, primaryColor: n, baseColor: r, textColorDisabled: i, inputColorDisabled: a, textColor2: o, opacityDisabled: s, borderRadius: c, fontSizeSmall: l, fontSizeMedium: u, fontSizeLarge: d, heightSmall: f, heightMedium: p, heightLarge: m, lineHeight: h } = e;
		return {
			...lC,
			labelLineHeight: h,
			buttonHeightSmall: f,
			buttonHeightMedium: p,
			buttonHeightLarge: m,
			fontSizeSmall: l,
			fontSizeMedium: u,
			fontSizeLarge: d,
			boxShadow: `inset 0 0 0 1px ${t}`,
			boxShadowActive: `inset 0 0 0 1px ${n}`,
			boxShadowFocus: `inset 0 0 0 1px ${n}, 0 0 0 2px ${J(n, { alpha: .3 })}`,
			boxShadowHover: `inset 0 0 0 1px ${n}`,
			boxShadowDisabled: `inset 0 0 0 1px ${t}`,
			color: "#0000",
			colorDisabled: a,
			colorActive: "#0000",
			textColor: o,
			textColorDisabled: i,
			dotColorActive: n,
			dotColorDisabled: t,
			buttonBorderColor: t,
			buttonBorderColorActive: n,
			buttonBorderColorHover: n,
			buttonColor: "#0000",
			buttonColorActive: n,
			buttonTextColor: o,
			buttonTextColorActive: r,
			buttonTextColorHover: n,
			opacityDisabled: s,
			buttonBoxShadowFocus: `inset 0 0 0 1px ${n}, 0 0 0 2px ${J(n, { alpha: .3 })}`,
			buttonBoxShadowHover: `inset 0 0 0 1px ${n}`,
			buttonBoxShadow: "inset 0 0 0 1px #0000",
			buttonBorderRadius: c
		};
	}
}, dC = {
	name: "Ellipsis",
	common: Z,
	peers: { Tooltip: cC }
}, fC = {
	thPaddingSmall: "8px",
	thPaddingMedium: "12px",
	thPaddingLarge: "12px",
	tdPaddingSmall: "8px",
	tdPaddingMedium: "12px",
	tdPaddingLarge: "12px",
	sorterSize: "15px",
	resizableContainerSize: "8px",
	resizableSize: "2px",
	filterSize: "15px",
	paginationMargin: "12px 0 0 0",
	emptyPadding: "48px 0",
	actionPadding: "8px 12px",
	actionButtonMargin: "0 8px 0 0"
};
//#endregion
//#region node_modules/naive-ui/es/data-table/styles/light.mjs
function pC(e) {
	let { cardColor: t, modalColor: n, popoverColor: r, textColor2: i, textColor1: a, tableHeaderColor: o, tableColorHover: s, iconColor: c, primaryColor: l, fontWeightStrong: u, borderRadius: d, lineHeight: f, fontSizeSmall: p, fontSizeMedium: m, fontSizeLarge: h, dividerColor: g, heightSmall: _, opacityDisabled: v, tableColorStriped: y } = e;
	return {
		...fC,
		actionDividerColor: g,
		lineHeight: f,
		borderRadius: d,
		fontSizeSmall: p,
		fontSizeMedium: m,
		fontSizeLarge: h,
		borderColor: q(t, g),
		tdColorHover: q(t, s),
		tdColorSorting: q(t, s),
		tdColorStriped: q(t, y),
		thColor: q(t, o),
		thColorHover: q(q(t, o), s),
		thColorSorting: q(q(t, o), s),
		tdColor: t,
		tdTextColor: i,
		thTextColor: a,
		thFontWeight: u,
		thButtonColorHover: s,
		thIconColor: c,
		thIconColorActive: l,
		borderColorModal: q(n, g),
		tdColorHoverModal: q(n, s),
		tdColorSortingModal: q(n, s),
		tdColorStripedModal: q(n, y),
		thColorModal: q(n, o),
		thColorHoverModal: q(q(n, o), s),
		thColorSortingModal: q(q(n, o), s),
		tdColorModal: n,
		borderColorPopover: q(r, g),
		tdColorHoverPopover: q(r, s),
		tdColorSortingPopover: q(r, s),
		tdColorStripedPopover: q(r, y),
		thColorPopover: q(r, o),
		thColorHoverPopover: q(q(r, o), s),
		thColorSortingPopover: q(q(r, o), s),
		tdColorPopover: r,
		boxShadowBefore: "inset -12px 0 8px -12px rgba(0, 0, 0, .18)",
		boxShadowAfter: "inset 12px 0 8px -12px rgba(0, 0, 0, .18)",
		loadingColor: l,
		loadingSize: _,
		opacityLoading: v
	};
}
//#endregion
//#region node_modules/naive-ui/es/data-table/styles/dark.mjs
var mC = {
	name: "DataTable",
	common: Z,
	peers: {
		Button: lS,
		Checkbox: OS,
		Radio: uC,
		Pagination: rC,
		Scrollbar: Rh,
		Empty: Hh,
		Popover: og,
		Ellipsis: dC,
		Dropdown: oC
	},
	self(e) {
		let t = pC(e);
		return t.boxShadowAfter = "inset 12px 0 8px -12px rgba(0, 0, 0, .36)", t.boxShadowBefore = "inset -12px 0 8px -12px rgba(0, 0, 0, .36)", t;
	}
};
//#endregion
//#region node_modules/naive-ui/es/_utils/vue/get-slot.mjs
function hC(e, t = "default", n = []) {
	let r = e.$slots[t];
	return r === void 0 ? n : r();
}
//#endregion
//#region node_modules/naive-ui/es/icon/styles/light.mjs
function gC(e) {
	let { textColorBase: t, opacity1: n, opacity2: r, opacity3: i, opacity4: a, opacity5: o } = e;
	return {
		color: t,
		opacity1Depth: n,
		opacity2Depth: r,
		opacity3Depth: i,
		opacity4Depth: a,
		opacity5Depth: o
	};
}
//#endregion
//#region node_modules/naive-ui/es/icon/styles/dark.mjs
var _C = {
	name: "Icon",
	common: Z,
	self: gC
}, vC = {
	itemFontSize: "12px",
	itemHeight: "36px",
	itemWidth: "52px",
	panelActionPadding: "8px 0"
};
//#endregion
//#region node_modules/naive-ui/es/time-picker/styles/light.mjs
function yC(e) {
	let { popoverColor: t, textColor2: n, primaryColor: r, hoverColor: i, dividerColor: a, opacityDisabled: o, boxShadow2: s, borderRadius: c, iconColor: l, iconColorDisabled: u } = e;
	return {
		...vC,
		panelColor: t,
		panelBoxShadow: s,
		panelDividerColor: a,
		itemTextColor: n,
		itemTextColorActive: r,
		itemColorHover: i,
		itemOpacityDisabled: o,
		itemBorderRadius: c,
		borderRadius: c,
		iconColor: l,
		iconColorDisabled: u
	};
}
var bC = {
	name: "TimePicker",
	common: Z,
	peers: {
		Scrollbar: Rh,
		Button: lS,
		Input: rb
	},
	self: yC
}, xC = {
	itemSize: "24px",
	itemCellWidth: "38px",
	itemCellHeight: "32px",
	scrollItemWidth: "80px",
	scrollItemHeight: "40px",
	panelExtraFooterPadding: "8px 12px",
	panelActionPadding: "8px 12px",
	calendarTitlePadding: "0",
	calendarTitleHeight: "28px",
	arrowSize: "14px",
	panelHeaderPadding: "8px 12px",
	calendarDaysHeight: "32px",
	calendarTitleGridTempateColumns: "28px 28px 1fr 28px 28px",
	calendarLeftPaddingDate: "6px 12px 4px 12px",
	calendarLeftPaddingDatetime: "4px 12px",
	calendarLeftPaddingDaterange: "6px 12px 4px 12px",
	calendarLeftPaddingDatetimerange: "4px 12px",
	calendarLeftPaddingMonth: "0",
	calendarLeftPaddingYear: "0",
	calendarLeftPaddingQuarter: "0",
	calendarLeftPaddingMonthrange: "0",
	calendarLeftPaddingQuarterrange: "0",
	calendarLeftPaddingYearrange: "0",
	calendarLeftPaddingWeek: "6px 12px 4px 12px",
	calendarRightPaddingDate: "6px 12px 4px 12px",
	calendarRightPaddingDatetime: "4px 12px",
	calendarRightPaddingDaterange: "6px 12px 4px 12px",
	calendarRightPaddingDatetimerange: "4px 12px",
	calendarRightPaddingMonth: "0",
	calendarRightPaddingYear: "0",
	calendarRightPaddingQuarter: "0",
	calendarRightPaddingMonthrange: "0",
	calendarRightPaddingQuarterrange: "0",
	calendarRightPaddingYearrange: "0",
	calendarRightPaddingWeek: "0"
};
//#endregion
//#region node_modules/naive-ui/es/date-picker/styles/light.mjs
function SC(e) {
	let { hoverColor: t, fontSize: n, textColor2: r, textColorDisabled: i, popoverColor: a, primaryColor: o, borderRadiusSmall: s, iconColor: c, iconColorDisabled: l, textColor1: u, dividerColor: d, boxShadow2: f, borderRadius: p, fontWeightStrong: m } = e;
	return {
		...xC,
		itemFontSize: n,
		calendarDaysFontSize: n,
		calendarTitleFontSize: n,
		itemTextColor: r,
		itemTextColorDisabled: i,
		itemTextColorActive: a,
		itemTextColorCurrent: o,
		itemColorIncluded: J(o, { alpha: .1 }),
		itemColorHover: t,
		itemColorDisabled: t,
		itemColorActive: o,
		itemBorderRadius: s,
		panelColor: a,
		panelTextColor: r,
		arrowColor: c,
		calendarTitleTextColor: u,
		calendarTitleColorHover: t,
		calendarDaysTextColor: r,
		panelHeaderDividerColor: d,
		calendarDaysDividerColor: d,
		calendarDividerColor: d,
		panelActionDividerColor: d,
		panelBoxShadow: f,
		panelBorderRadius: p,
		calendarTitleFontWeight: m,
		scrollItemBorderRadius: p,
		iconColor: c,
		iconColorDisabled: l
	};
}
//#endregion
//#region node_modules/naive-ui/es/date-picker/styles/dark.mjs
var CC = {
	name: "DatePicker",
	common: Z,
	peers: {
		Input: rb,
		Button: lS,
		TimePicker: bC,
		Scrollbar: Rh
	},
	self(e) {
		let { popoverColor: t, hoverColor: n, primaryColor: r } = e, i = SC(e);
		return i.itemColorDisabled = q(t, n), i.itemColorIncluded = J(r, { alpha: .15 }), i.itemColorHover = q(t, n), i;
	}
}, wC = {
	thPaddingBorderedSmall: "8px 12px",
	thPaddingBorderedMedium: "12px 16px",
	thPaddingBorderedLarge: "16px 24px",
	thPaddingSmall: "0",
	thPaddingMedium: "0",
	thPaddingLarge: "0",
	tdPaddingBorderedSmall: "8px 12px",
	tdPaddingBorderedMedium: "12px 16px",
	tdPaddingBorderedLarge: "16px 24px",
	tdPaddingSmall: "0 0 8px 0",
	tdPaddingMedium: "0 0 12px 0",
	tdPaddingLarge: "0 0 16px 0"
};
//#endregion
//#region node_modules/naive-ui/es/descriptions/styles/light.mjs
function TC(e) {
	let { tableHeaderColor: t, textColor2: n, textColor1: r, cardColor: i, modalColor: a, popoverColor: o, dividerColor: s, borderRadius: c, fontWeightStrong: l, lineHeight: u, fontSizeSmall: d, fontSizeMedium: f, fontSizeLarge: p } = e;
	return {
		...wC,
		lineHeight: u,
		fontSizeSmall: d,
		fontSizeMedium: f,
		fontSizeLarge: p,
		titleTextColor: r,
		thColor: q(i, t),
		thColorModal: q(a, t),
		thColorPopover: q(o, t),
		thTextColor: r,
		thFontWeight: l,
		tdTextColor: n,
		tdColor: i,
		tdColorModal: a,
		tdColorPopover: o,
		borderColor: q(i, s),
		borderColorModal: q(a, s),
		borderColorPopover: q(o, s),
		borderRadius: c
	};
}
//#endregion
//#region node_modules/naive-ui/es/descriptions/styles/dark.mjs
var EC = {
	name: "Descriptions",
	common: Z,
	self: TC
}, DC = {
	titleFontSize: "18px",
	padding: "16px 28px 20px 28px",
	iconSize: "28px",
	actionSpace: "12px",
	contentMargin: "8px 0 16px 0",
	iconMargin: "0 4px 0 0",
	iconMarginIconTop: "4px 0 8px 0",
	closeSize: "22px",
	closeIconSize: "18px",
	closeMargin: "20px 26px 0 0",
	closeMarginIconTop: "10px 16px 0 0"
};
//#endregion
//#region node_modules/naive-ui/es/dialog/styles/light.mjs
function OC(e) {
	let { textColor1: t, textColor2: n, modalColor: r, closeIconColor: i, closeIconColorHover: a, closeIconColorPressed: o, closeColorHover: s, closeColorPressed: c, infoColor: l, successColor: u, warningColor: d, errorColor: f, primaryColor: p, dividerColor: m, borderRadius: h, fontWeightStrong: g, lineHeight: _, fontSize: v } = e;
	return {
		...DC,
		fontSize: v,
		lineHeight: _,
		border: `1px solid ${m}`,
		titleTextColor: t,
		textColor: n,
		color: r,
		closeColorHover: s,
		closeColorPressed: c,
		closeIconColor: i,
		closeIconColorHover: a,
		closeIconColorPressed: o,
		closeBorderRadius: h,
		iconColor: p,
		iconColorInfo: l,
		iconColorSuccess: u,
		iconColorWarning: d,
		iconColorError: f,
		borderRadius: h,
		titleFontWeight: g
	};
}
var kC = {
	name: "Dialog",
	common: Z,
	peers: { Button: lS },
	self: OC
};
//#endregion
//#region node_modules/naive-ui/es/modal/styles/light.mjs
function AC(e) {
	let { modalColor: t, textColor2: n, boxShadow3: r } = e;
	return {
		color: t,
		textColor: n,
		boxShadow: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/modal/styles/dark.mjs
var jC = {
	name: "Modal",
	common: Z,
	peers: {
		Scrollbar: Rh,
		Dialog: kC,
		Card: bS
	},
	self: AC
}, MC = {
	name: "LoadingBar",
	common: Z,
	self(e) {
		let { primaryColor: t } = e;
		return {
			colorError: "red",
			colorLoading: t,
			height: "2px"
		};
	}
}, NC = {
	margin: "0 0 8px 0",
	padding: "10px 20px",
	maxWidth: "720px",
	minWidth: "420px",
	iconMargin: "0 10px 0 0",
	closeMargin: "0 0 0 10px",
	closeSize: "20px",
	closeIconSize: "16px",
	iconSize: "20px",
	fontSize: "14px"
};
//#endregion
//#region node_modules/naive-ui/es/message/styles/light.mjs
function PC(e) {
	let { textColor2: t, closeIconColor: n, closeIconColorHover: r, closeIconColorPressed: i, infoColor: a, successColor: o, errorColor: s, warningColor: c, popoverColor: l, boxShadow2: u, primaryColor: d, lineHeight: f, borderRadius: p, closeColorHover: m, closeColorPressed: h } = e;
	return {
		...NC,
		closeBorderRadius: p,
		textColor: t,
		textColorInfo: t,
		textColorSuccess: t,
		textColorError: t,
		textColorWarning: t,
		textColorLoading: t,
		color: l,
		colorInfo: l,
		colorSuccess: l,
		colorError: l,
		colorWarning: l,
		colorLoading: l,
		boxShadow: u,
		boxShadowInfo: u,
		boxShadowSuccess: u,
		boxShadowError: u,
		boxShadowWarning: u,
		boxShadowLoading: u,
		iconColor: t,
		iconColorInfo: a,
		iconColorSuccess: o,
		iconColorWarning: c,
		iconColorError: s,
		iconColorLoading: d,
		closeColorHover: m,
		closeColorPressed: h,
		closeIconColor: n,
		closeIconColorHover: r,
		closeIconColorPressed: i,
		closeColorHoverInfo: m,
		closeColorPressedInfo: h,
		closeIconColorInfo: n,
		closeIconColorHoverInfo: r,
		closeIconColorPressedInfo: i,
		closeColorHoverSuccess: m,
		closeColorPressedSuccess: h,
		closeIconColorSuccess: n,
		closeIconColorHoverSuccess: r,
		closeIconColorPressedSuccess: i,
		closeColorHoverError: m,
		closeColorPressedError: h,
		closeIconColorError: n,
		closeIconColorHoverError: r,
		closeIconColorPressedError: i,
		closeColorHoverWarning: m,
		closeColorPressedWarning: h,
		closeIconColorWarning: n,
		closeIconColorHoverWarning: r,
		closeIconColorPressedWarning: i,
		closeColorHoverLoading: m,
		closeColorPressedLoading: h,
		closeIconColorLoading: n,
		closeIconColorHoverLoading: r,
		closeIconColorPressedLoading: i,
		loadingColor: d,
		lineHeight: f,
		borderRadius: p,
		border: "0"
	};
}
//#endregion
//#region node_modules/naive-ui/es/message/styles/dark.mjs
var FC = {
	name: "Message",
	common: Z,
	self: PC
}, IC = {
	closeMargin: "16px 12px",
	closeSize: "20px",
	closeIconSize: "16px",
	width: "365px",
	padding: "16px",
	titleFontSize: "16px",
	metaFontSize: "12px",
	descriptionFontSize: "12px"
};
//#endregion
//#region node_modules/naive-ui/es/notification/styles/light.mjs
function LC(e) {
	let { textColor2: t, successColor: n, infoColor: r, warningColor: i, errorColor: a, popoverColor: o, closeIconColor: s, closeIconColorHover: c, closeIconColorPressed: l, closeColorHover: u, closeColorPressed: d, textColor1: f, textColor3: p, borderRadius: m, fontWeightStrong: h, boxShadow2: g, lineHeight: _, fontSize: v } = e;
	return {
		...IC,
		borderRadius: m,
		lineHeight: _,
		fontSize: v,
		headerFontWeight: h,
		iconColor: t,
		iconColorSuccess: n,
		iconColorInfo: r,
		iconColorWarning: i,
		iconColorError: a,
		color: o,
		textColor: t,
		closeIconColor: s,
		closeIconColorHover: c,
		closeIconColorPressed: l,
		closeBorderRadius: m,
		closeColorHover: u,
		closeColorPressed: d,
		headerTextColor: f,
		descriptionTextColor: p,
		actionTextColor: t,
		boxShadow: g
	};
}
//#endregion
//#region node_modules/naive-ui/es/notification/styles/dark.mjs
var RC = {
	name: "Notification",
	common: Z,
	peers: { Scrollbar: Rh },
	self: LC
};
//#endregion
//#region node_modules/naive-ui/es/divider/styles/light.mjs
function zC(e) {
	let { textColor1: t, dividerColor: n, fontWeightStrong: r } = e;
	return {
		textColor: t,
		color: n,
		fontWeight: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/divider/styles/dark.mjs
var BC = {
	name: "Divider",
	common: Z,
	self: zC
};
//#endregion
//#region node_modules/naive-ui/es/drawer/styles/light.mjs
function VC(e) {
	let { modalColor: t, textColor1: n, textColor2: r, boxShadow3: i, lineHeight: a, fontWeightStrong: o, dividerColor: s, closeColorHover: c, closeColorPressed: l, closeIconColor: u, closeIconColorHover: d, closeIconColorPressed: f, borderRadius: p, primaryColorHover: m } = e;
	return {
		bodyPadding: "16px 24px",
		borderRadius: p,
		headerPadding: "16px 24px",
		footerPadding: "16px 24px",
		color: t,
		textColor: r,
		titleTextColor: n,
		titleFontSize: "18px",
		titleFontWeight: o,
		boxShadow: i,
		lineHeight: a,
		headerBorderBottom: `1px solid ${s}`,
		footerBorderTop: `1px solid ${s}`,
		closeIconColor: u,
		closeIconColorHover: d,
		closeIconColorPressed: f,
		closeSize: "22px",
		closeIconSize: "18px",
		closeColorHover: c,
		closeColorPressed: l,
		closeBorderRadius: p,
		resizableTriggerColorHover: m
	};
}
//#endregion
//#region node_modules/naive-ui/es/drawer/styles/dark.mjs
var HC = {
	name: "Drawer",
	common: Z,
	peers: { Scrollbar: Rh },
	self: VC
}, UC = {
	actionMargin: "0 0 0 20px",
	actionMarginRtl: "0 20px 0 0"
}, WC = {
	name: "DynamicInput",
	common: Z,
	peers: {
		Input: rb,
		Button: lS
	},
	self() {
		return UC;
	}
}, GC = {
	gapSmall: "4px 8px",
	gapMedium: "8px 12px",
	gapLarge: "12px 16px"
}, KC = {
	name: "Space",
	self() {
		return GC;
	}
};
//#endregion
//#region node_modules/naive-ui/es/space/styles/light.mjs
function qC() {
	return GC;
}
var JC = {
	name: "Space",
	self: qC
}, YC;
function XC() {
	if (!Tb) return !0;
	if (YC === void 0) {
		let e = document.createElement("div");
		e.style.display = "flex", e.style.flexDirection = "column", e.style.rowGap = "1px", e.appendChild(document.createElement("div")), e.appendChild(document.createElement("div")), document.body.appendChild(e);
		let t = e.scrollHeight === 1;
		return document.body.removeChild(e), YC = t;
	}
	return YC;
}
var ZC = /* @__PURE__ */ N({
	name: "Space",
	props: {
		...Kh.props,
		align: String,
		justify: {
			type: String,
			default: "start"
		},
		inline: Boolean,
		vertical: Boolean,
		reverse: Boolean,
		size: [
			String,
			Number,
			Array
		],
		wrapItem: {
			type: Boolean,
			default: !0
		},
		itemClass: String,
		itemStyle: [String, Object],
		wrap: {
			type: Boolean,
			default: !0
		},
		internalUseGap: {
			type: Boolean,
			default: void 0
		}
	},
	setup(e) {
		let { mergedClsPrefixRef: t, mergedRtlRef: n, mergedComponentPropsRef: r } = _m(e), i = z(() => e.size ?? r?.value?.Space?.size ?? "medium"), a = Kh("Space", "-space", void 0, JC, e, t), o = Kg("Space", n, t);
		return {
			useGap: XC(),
			rtlEnabled: o,
			mergedClsPrefix: t,
			margin: z(() => {
				let e = i.value;
				if (Array.isArray(e)) return {
					horizontal: e[0],
					vertical: e[1]
				};
				if (typeof e == "number") return {
					horizontal: e,
					vertical: e
				};
				let { self: { [W("gap", e)]: t } } = a.value, { row: n, col: r } = Gm(t);
				return {
					horizontal: Hm(r),
					vertical: Hm(n)
				};
			})
		};
	},
	render() {
		let { vertical: e, reverse: t, align: n, inline: r, justify: i, itemClass: a, itemStyle: o, margin: s, wrap: c, mergedClsPrefix: l, rtlEnabled: u, useGap: d, wrapItem: f, internalUseGap: p } = this, m = Ng(hC(this), !1);
		if (!m.length) return null;
		let h = `${s.horizontal}px`, g = `${s.horizontal / 2}px`, _ = `${s.vertical}px`, v = `${s.vertical / 2}px`, y = m.length - 1, b = i.startsWith("space-");
		return F(), I("div", {
			role: "none",
			class: K([`${l}-space`, u && `${l}-space--rtl`]),
			style: k({
				display: r ? "inline-flex" : "flex",
				flexDirection: e && !t ? "column" : e && t ? "column-reverse" : !e && t ? "row-reverse" : "row",
				justifyContent: ["start", "end"].includes(i) ? `flex-${i}` : i,
				flexWrap: !c || e ? "nowrap" : "wrap",
				marginTop: d || e ? "" : `-${v}`,
				marginBottom: d || e ? "" : `-${v}`,
				alignItems: n,
				gap: d ? `${s.vertical}px ${s.horizontal}px` : ""
			})
		}, [!f && (d || p) ? (F(), I(P, { key: 0 }, [G(() => m)], 64)) : (F(), I(P, { key: 1 }, [G(() => m.map((t, n) => t.type === Ri ? t : (F(), I("div", {
			key: 1,
			role: "none",
			class: K(a),
			style: k([
				o,
				{ maxWidth: "100%" },
				d ? "" : e ? { marginBottom: n === y ? "" : _ } : u ? {
					marginLeft: b ? i === "space-between" && n === y ? "" : g : n === y ? "" : h,
					marginRight: b ? i === "space-between" && n === 0 ? "" : g : "",
					paddingTop: v,
					paddingBottom: v
				} : {
					marginRight: b ? i === "space-between" && n === y ? "" : g : n === y ? "" : h,
					marginLeft: b ? i === "space-between" && n === 0 ? "" : g : "",
					paddingTop: v,
					paddingBottom: v
				}
			])
		}, [G(() => t)], 6))))], 64))], 6);
	}
}), QC = {
	name: "DynamicTags",
	common: Z,
	peers: {
		Input: rb,
		Button: lS,
		Tag: by,
		Space: KC
	},
	self() {
		return { inputWidth: "64px" };
	}
}, $C = {
	name: "Element",
	common: Z
}, ew = {
	gapSmall: "4px 8px",
	gapMedium: "8px 12px",
	gapLarge: "12px 16px"
}, tw = {
	name: "Flex",
	self() {
		return ew;
	}
}, nw = {
	name: "ButtonGroup",
	common: Z
}, rw = {
	feedbackPadding: "4px 0 0 2px",
	feedbackHeightSmall: "24px",
	feedbackHeightMedium: "24px",
	feedbackHeightLarge: "26px",
	feedbackFontSizeSmall: "13px",
	feedbackFontSizeMedium: "14px",
	feedbackFontSizeLarge: "14px",
	labelFontSizeLeftSmall: "14px",
	labelFontSizeLeftMedium: "14px",
	labelFontSizeLeftLarge: "15px",
	labelFontSizeTopSmall: "13px",
	labelFontSizeTopMedium: "14px",
	labelFontSizeTopLarge: "14px",
	labelHeightSmall: "24px",
	labelHeightMedium: "26px",
	labelHeightLarge: "28px",
	labelPaddingVertical: "0 0 6px 2px",
	labelPaddingHorizontal: "0 12px 0 0",
	labelTextAlignVertical: "left",
	labelTextAlignHorizontal: "right",
	labelFontWeight: "400"
};
//#endregion
//#region node_modules/naive-ui/es/form/styles/light.mjs
function iw(e) {
	let { heightSmall: t, heightMedium: n, heightLarge: r, textColor1: i, errorColor: a, warningColor: o, lineHeight: s, textColor3: c } = e;
	return {
		...rw,
		blankHeightSmall: t,
		blankHeightMedium: n,
		blankHeightLarge: r,
		lineHeight: s,
		labelTextColor: i,
		asteriskColor: a,
		feedbackTextColorError: a,
		feedbackTextColorWarning: o,
		feedbackTextColor: c
	};
}
//#endregion
//#region node_modules/naive-ui/es/form/styles/dark.mjs
var aw = {
	name: "Form",
	common: Z,
	self: iw
}, ow = {
	name: "GradientText",
	common: Z,
	self(e) {
		let { primaryColor: t, successColor: n, warningColor: r, errorColor: i, infoColor: a, primaryColorSuppl: o, successColorSuppl: s, warningColorSuppl: c, errorColorSuppl: l, infoColorSuppl: u, fontWeightStrong: d } = e;
		return {
			fontWeight: d,
			rotate: "252deg",
			colorStartPrimary: t,
			colorEndPrimary: o,
			colorStartInfo: a,
			colorEndInfo: u,
			colorStartWarning: r,
			colorEndWarning: c,
			colorStartError: i,
			colorEndError: l,
			colorStartSuccess: n,
			colorEndSuccess: s
		};
	}
}, sw = {
	name: "InputNumber",
	common: Z,
	peers: {
		Button: lS,
		Input: rb
	},
	self(e) {
		let { textColorDisabled: t } = e;
		return { iconColorDisabled: t };
	}
};
//#endregion
//#region node_modules/naive-ui/es/input-otp/styles/light.mjs
function cw() {
	return {
		inputWidthSmall: "24px",
		inputWidthMedium: "30px",
		inputWidthLarge: "36px",
		gapSmall: "8px",
		gapMedium: "8px",
		gapLarge: "8px"
	};
}
//#endregion
//#region node_modules/naive-ui/es/input-otp/styles/dark.mjs
var lw = {
	name: "InputOtp",
	common: Z,
	peers: { Input: rb },
	self: cw
}, uw = {
	name: "Layout",
	common: Z,
	peers: { Scrollbar: Rh },
	self(e) {
		let { textColor2: t, bodyColor: n, popoverColor: r, cardColor: i, dividerColor: a, scrollbarColor: o, scrollbarColorHover: s } = e;
		return {
			textColor: t,
			textColorInverted: t,
			color: n,
			colorEmbedded: n,
			headerColor: i,
			headerColorInverted: i,
			footerColor: i,
			footerColorInverted: i,
			headerBorderColor: a,
			headerBorderColorInverted: a,
			footerBorderColor: a,
			footerBorderColorInverted: a,
			siderBorderColor: a,
			siderBorderColorInverted: a,
			siderColor: i,
			siderColorInverted: i,
			siderToggleButtonBorder: "1px solid transparent",
			siderToggleButtonColor: r,
			siderToggleButtonIconColor: t,
			siderToggleButtonIconColorInverted: t,
			siderToggleBarColor: q(n, o),
			siderToggleBarColorHover: q(n, s),
			__invertScrollbar: "false"
		};
	}
};
//#endregion
//#region node_modules/naive-ui/es/list/styles/light.mjs
function dw(e) {
	let { textColor2: t, cardColor: n, modalColor: r, popoverColor: i, dividerColor: a, borderRadius: o, fontSize: s, hoverColor: c } = e;
	return {
		textColor: t,
		color: n,
		colorHover: c,
		colorModal: r,
		colorHoverModal: q(r, c),
		colorPopover: i,
		colorHoverPopover: q(i, c),
		borderColor: a,
		borderColorModal: q(r, a),
		borderColorPopover: q(i, a),
		borderRadius: o,
		fontSize: s
	};
}
//#endregion
//#region node_modules/naive-ui/es/list/styles/dark.mjs
var fw = {
	name: "List",
	common: Z,
	self: dw
}, pw = {
	name: "Log",
	common: Z,
	peers: {
		Scrollbar: Rh,
		Code: US
	},
	self(e) {
		let { textColor2: t, inputColor: n, fontSize: r, primaryColor: i } = e;
		return {
			loaderFontSize: r,
			loaderTextColor: t,
			loaderColor: n,
			loaderBorder: "1px solid #0000",
			loadingColor: i
		};
	}
}, mw = {
	name: "Mention",
	common: Z,
	peers: {
		InternalSelectMenu: ng,
		Input: rb
	},
	self(e) {
		let { boxShadow2: t } = e;
		return { menuBoxShadow: t };
	}
};
//#endregion
//#region node_modules/naive-ui/es/menu/styles/light.mjs
function hw(e, t, n, r) {
	return {
		itemColorHoverInverted: "#0000",
		itemColorActiveInverted: t,
		itemColorActiveHoverInverted: t,
		itemColorActiveCollapsedInverted: t,
		itemTextColorInverted: e,
		itemTextColorHoverInverted: n,
		itemTextColorChildActiveInverted: n,
		itemTextColorChildActiveHoverInverted: n,
		itemTextColorActiveInverted: n,
		itemTextColorActiveHoverInverted: n,
		itemTextColorHorizontalInverted: e,
		itemTextColorHoverHorizontalInverted: n,
		itemTextColorChildActiveHorizontalInverted: n,
		itemTextColorChildActiveHoverHorizontalInverted: n,
		itemTextColorActiveHorizontalInverted: n,
		itemTextColorActiveHoverHorizontalInverted: n,
		itemIconColorInverted: e,
		itemIconColorHoverInverted: n,
		itemIconColorActiveInverted: n,
		itemIconColorActiveHoverInverted: n,
		itemIconColorChildActiveInverted: n,
		itemIconColorChildActiveHoverInverted: n,
		itemIconColorCollapsedInverted: e,
		itemIconColorHorizontalInverted: e,
		itemIconColorHoverHorizontalInverted: n,
		itemIconColorActiveHorizontalInverted: n,
		itemIconColorActiveHoverHorizontalInverted: n,
		itemIconColorChildActiveHorizontalInverted: n,
		itemIconColorChildActiveHoverHorizontalInverted: n,
		arrowColorInverted: e,
		arrowColorHoverInverted: n,
		arrowColorActiveInverted: n,
		arrowColorActiveHoverInverted: n,
		arrowColorChildActiveInverted: n,
		arrowColorChildActiveHoverInverted: n,
		groupTextColorInverted: r
	};
}
function gw(e) {
	let { borderRadius: t, textColor3: n, primaryColor: r, textColor2: i, textColor1: a, fontSize: o, dividerColor: s, hoverColor: c, primaryColorHover: l } = e;
	return {
		borderRadius: t,
		color: "#0000",
		groupTextColor: n,
		itemColorHover: c,
		itemColorActive: J(r, { alpha: .1 }),
		itemColorActiveHover: J(r, { alpha: .1 }),
		itemColorActiveCollapsed: J(r, { alpha: .1 }),
		itemTextColor: i,
		itemTextColorHover: i,
		itemTextColorActive: r,
		itemTextColorActiveHover: r,
		itemTextColorChildActive: r,
		itemTextColorChildActiveHover: r,
		itemTextColorHorizontal: i,
		itemTextColorHoverHorizontal: l,
		itemTextColorActiveHorizontal: r,
		itemTextColorActiveHoverHorizontal: r,
		itemTextColorChildActiveHorizontal: r,
		itemTextColorChildActiveHoverHorizontal: r,
		itemIconColor: a,
		itemIconColorHover: a,
		itemIconColorActive: r,
		itemIconColorActiveHover: r,
		itemIconColorChildActive: r,
		itemIconColorChildActiveHover: r,
		itemIconColorCollapsed: a,
		itemIconColorHorizontal: a,
		itemIconColorHoverHorizontal: l,
		itemIconColorActiveHorizontal: r,
		itemIconColorActiveHoverHorizontal: r,
		itemIconColorChildActiveHorizontal: r,
		itemIconColorChildActiveHoverHorizontal: r,
		itemHeight: "42px",
		arrowColor: i,
		arrowColorHover: i,
		arrowColorActive: r,
		arrowColorActiveHover: r,
		arrowColorChildActive: r,
		arrowColorChildActiveHover: r,
		colorInverted: "#0000",
		borderColorHorizontal: "#0000",
		fontSize: o,
		dividerColor: s,
		...hw("#BBB", r, "#FFF", "#AAA")
	};
}
//#endregion
//#region node_modules/naive-ui/es/menu/styles/dark.mjs
var _w = {
	name: "Menu",
	common: Z,
	peers: {
		Tooltip: cC,
		Dropdown: oC
	},
	self(e) {
		let { primaryColor: t, primaryColorSuppl: n } = e, r = gw(e);
		return r.itemColorActive = J(t, { alpha: .15 }), r.itemColorActiveHover = J(t, { alpha: .15 }), r.itemColorActiveCollapsed = J(t, { alpha: .15 }), r.itemColorActiveInverted = n, r.itemColorActiveHoverInverted = n, r.itemColorActiveCollapsedInverted = n, r;
	}
}, vw = { iconSize: "22px" };
//#endregion
//#region node_modules/naive-ui/es/popconfirm/styles/light.mjs
function yw(e) {
	let { fontSize: t, warningColor: n } = e;
	return {
		...vw,
		fontSize: t,
		iconColor: n
	};
}
//#endregion
//#region node_modules/naive-ui/es/popconfirm/styles/dark.mjs
var bw = {
	name: "Popconfirm",
	common: Z,
	peers: {
		Button: lS,
		Popover: og
	},
	self: yw
};
//#endregion
//#region node_modules/naive-ui/es/progress/styles/light.mjs
function xw(e) {
	let { infoColor: t, successColor: n, warningColor: r, errorColor: i, textColor2: a, progressRailColor: o, fontSize: s, fontWeight: c } = e;
	return {
		fontSize: s,
		fontSizeCircle: "28px",
		fontWeightCircle: c,
		railColor: o,
		railHeight: "8px",
		iconSizeCircle: "36px",
		iconSizeLine: "18px",
		iconColor: t,
		iconColorInfo: t,
		iconColorSuccess: n,
		iconColorWarning: r,
		iconColorError: i,
		textColorCircle: a,
		textColorLineInner: "rgb(255, 255, 255)",
		textColorLineOuter: a,
		fillColor: t,
		fillColorInfo: t,
		fillColorSuccess: n,
		fillColorWarning: r,
		fillColorError: i,
		lineBgProcessing: "linear-gradient(90deg, rgba(255, 255, 255, .3) 0%, rgba(255, 255, 255, .5) 100%)"
	};
}
var Sw = {
	name: "Progress",
	common: Z,
	self(e) {
		let t = xw(e);
		return t.textColorLineInner = "rgb(0, 0, 0)", t.lineBgProcessing = "linear-gradient(90deg, rgba(255, 255, 255, .3) 0%, rgba(255, 255, 255, .5) 100%)", t;
	}
}, Cw = {
	name: "Rate",
	common: Z,
	self(e) {
		let { railColor: t } = e;
		return {
			itemColor: t,
			itemColorActive: "#CCAA33",
			itemSize: "20px",
			sizeSmall: "16px",
			sizeMedium: "20px",
			sizeLarge: "24px"
		};
	}
}, ww = {
	titleFontSizeSmall: "26px",
	titleFontSizeMedium: "32px",
	titleFontSizeLarge: "40px",
	titleFontSizeHuge: "48px",
	fontSizeSmall: "14px",
	fontSizeMedium: "14px",
	fontSizeLarge: "15px",
	fontSizeHuge: "16px",
	iconSizeSmall: "64px",
	iconSizeMedium: "80px",
	iconSizeLarge: "100px",
	iconSizeHuge: "125px",
	iconColor418: void 0,
	iconColor404: void 0,
	iconColor403: void 0,
	iconColor500: void 0
};
//#endregion
//#region node_modules/naive-ui/es/result/styles/light.mjs
function Tw(e) {
	let { textColor2: t, textColor1: n, errorColor: r, successColor: i, infoColor: a, warningColor: o, lineHeight: s, fontWeightStrong: c } = e;
	return {
		...ww,
		lineHeight: s,
		titleFontWeight: c,
		titleTextColor: n,
		textColor: t,
		iconColorError: r,
		iconColorSuccess: i,
		iconColorInfo: a,
		iconColorWarning: o
	};
}
//#endregion
//#region node_modules/naive-ui/es/result/styles/dark.mjs
var Ew = {
	name: "Result",
	common: Z,
	self: Tw
}, Dw = {
	railHeight: "4px",
	railWidthVertical: "4px",
	handleSize: "18px",
	dotHeight: "8px",
	dotWidth: "8px",
	dotBorderRadius: "4px"
}, Ow = {
	name: "Slider",
	common: Z,
	self(e) {
		let { railColor: t, modalColor: n, primaryColorSuppl: r, popoverColor: i, textColor2: a, cardColor: o, borderRadius: s, fontSize: c, opacityDisabled: l } = e;
		return {
			...Dw,
			fontSize: c,
			markFontSize: c,
			railColor: t,
			railColorHover: t,
			fillColor: r,
			fillColorHover: r,
			opacityDisabled: l,
			handleColor: "#FFF",
			dotColor: o,
			dotColorModal: n,
			dotColorPopover: i,
			handleBoxShadow: "0px 2px 4px 0 rgba(0, 0, 0, 0.4)",
			handleBoxShadowHover: "0px 2px 4px 0 rgba(0, 0, 0, 0.4)",
			handleBoxShadowActive: "0px 2px 4px 0 rgba(0, 0, 0, 0.4)",
			handleBoxShadowFocus: "0px 2px 4px 0 rgba(0, 0, 0, 0.4)",
			indicatorColor: i,
			indicatorBoxShadow: "0 2px 8px 0 rgba(0, 0, 0, 0.12)",
			indicatorTextColor: a,
			indicatorBorderRadius: s,
			dotBorder: `2px solid ${t}`,
			dotBorderActive: `2px solid ${r}`,
			dotBoxShadow: ""
		};
	}
};
//#endregion
//#region node_modules/naive-ui/es/spin/styles/light.mjs
function kw(e) {
	let { opacityDisabled: t, heightTiny: n, heightSmall: r, heightMedium: i, heightLarge: a, heightHuge: o, primaryColor: s, fontSize: c } = e;
	return {
		fontSize: c,
		textColor: s,
		sizeTiny: n,
		sizeSmall: r,
		sizeMedium: i,
		sizeLarge: a,
		sizeHuge: o,
		color: s,
		opacitySpinning: t
	};
}
//#endregion
//#region node_modules/naive-ui/es/spin/styles/dark.mjs
var Aw = {
	name: "Spin",
	common: Z,
	self: kw
};
//#endregion
//#region node_modules/naive-ui/es/statistic/styles/light.mjs
function jw(e) {
	let { textColor2: t, textColor3: n, fontSize: r, fontWeight: i } = e;
	return {
		labelFontSize: r,
		labelFontWeight: i,
		valueFontWeight: i,
		valueFontSize: "24px",
		labelTextColor: n,
		valuePrefixTextColor: t,
		valueSuffixTextColor: t,
		valueTextColor: t
	};
}
//#endregion
//#region node_modules/naive-ui/es/statistic/styles/dark.mjs
var Mw = {
	name: "Statistic",
	common: Z,
	self: jw
}, Nw = {
	stepHeaderFontSizeSmall: "14px",
	stepHeaderFontSizeMedium: "16px",
	indicatorIndexFontSizeSmall: "14px",
	indicatorIndexFontSizeMedium: "16px",
	indicatorSizeSmall: "22px",
	indicatorSizeMedium: "28px",
	indicatorIconSizeSmall: "14px",
	indicatorIconSizeMedium: "18px"
};
//#endregion
//#region node_modules/naive-ui/es/steps/styles/light.mjs
function Pw(e) {
	let { fontWeightStrong: t, baseColor: n, textColorDisabled: r, primaryColor: i, errorColor: a, textColor1: o, textColor2: s } = e;
	return {
		...Nw,
		stepHeaderFontWeight: t,
		indicatorTextColorProcess: n,
		indicatorTextColorWait: r,
		indicatorTextColorFinish: i,
		indicatorTextColorError: a,
		indicatorBorderColorProcess: i,
		indicatorBorderColorWait: r,
		indicatorBorderColorFinish: i,
		indicatorBorderColorError: a,
		indicatorColorProcess: i,
		indicatorColorWait: "#0000",
		indicatorColorFinish: "#0000",
		indicatorColorError: "#0000",
		splitorColorProcess: r,
		splitorColorWait: r,
		splitorColorFinish: i,
		splitorColorError: r,
		headerTextColorProcess: o,
		headerTextColorWait: r,
		headerTextColorFinish: r,
		headerTextColorError: a,
		descriptionTextColorProcess: s,
		descriptionTextColorWait: r,
		descriptionTextColorFinish: r,
		descriptionTextColorError: a
	};
}
//#endregion
//#region node_modules/naive-ui/es/steps/styles/dark.mjs
var Fw = {
	name: "Steps",
	common: Z,
	self: Pw
}, Iw = {
	buttonHeightSmall: "14px",
	buttonHeightMedium: "18px",
	buttonHeightLarge: "22px",
	buttonWidthSmall: "14px",
	buttonWidthMedium: "18px",
	buttonWidthLarge: "22px",
	buttonWidthPressedSmall: "20px",
	buttonWidthPressedMedium: "24px",
	buttonWidthPressedLarge: "28px",
	railHeightSmall: "18px",
	railHeightMedium: "22px",
	railHeightLarge: "26px",
	railWidthSmall: "32px",
	railWidthMedium: "40px",
	railWidthLarge: "48px"
}, Lw = {
	name: "Switch",
	common: Z,
	self(e) {
		let { primaryColorSuppl: t, opacityDisabled: n, borderRadius: r, primaryColor: i, textColor2: a, baseColor: o } = e;
		return {
			...Iw,
			iconColor: o,
			textColor: a,
			loadingColor: t,
			opacityDisabled: n,
			railColor: "rgba(255, 255, 255, .20)",
			railColorActive: t,
			buttonBoxShadow: "0px 2px 4px 0 rgba(0, 0, 0, 0.4)",
			buttonColor: "#FFF",
			railBorderRadiusSmall: r,
			railBorderRadiusMedium: r,
			railBorderRadiusLarge: r,
			buttonBorderRadiusSmall: r,
			buttonBorderRadiusMedium: r,
			buttonBorderRadiusLarge: r,
			boxShadowFocus: `0 0 8px 0 ${J(i, { alpha: .3 })}`
		};
	}
}, Rw = {
	thPaddingSmall: "6px",
	thPaddingMedium: "12px",
	thPaddingLarge: "12px",
	tdPaddingSmall: "6px",
	tdPaddingMedium: "12px",
	tdPaddingLarge: "12px"
};
//#endregion
//#region node_modules/naive-ui/es/table/styles/light.mjs
function zw(e) {
	let { dividerColor: t, cardColor: n, modalColor: r, popoverColor: i, tableHeaderColor: a, tableColorStriped: o, textColor1: s, textColor2: c, borderRadius: l, fontWeightStrong: u, lineHeight: d, fontSizeSmall: f, fontSizeMedium: p, fontSizeLarge: m } = e;
	return {
		...Rw,
		fontSizeSmall: f,
		fontSizeMedium: p,
		fontSizeLarge: m,
		lineHeight: d,
		borderRadius: l,
		borderColor: q(n, t),
		borderColorModal: q(r, t),
		borderColorPopover: q(i, t),
		tdColor: n,
		tdColorModal: r,
		tdColorPopover: i,
		tdColorStriped: q(n, o),
		tdColorStripedModal: q(r, o),
		tdColorStripedPopover: q(i, o),
		thColor: q(n, a),
		thColorModal: q(r, a),
		thColorPopover: q(i, a),
		thTextColor: s,
		tdTextColor: c,
		thFontWeight: u
	};
}
//#endregion
//#region node_modules/naive-ui/es/table/styles/dark.mjs
var Bw = {
	name: "Table",
	common: Z,
	self: zw
}, Vw = {
	tabFontSizeSmall: "14px",
	tabFontSizeMedium: "14px",
	tabFontSizeLarge: "16px",
	tabGapSmallLine: "36px",
	tabGapMediumLine: "36px",
	tabGapLargeLine: "36px",
	tabGapSmallLineVertical: "8px",
	tabGapMediumLineVertical: "8px",
	tabGapLargeLineVertical: "8px",
	tabPaddingSmallLine: "6px 0",
	tabPaddingMediumLine: "10px 0",
	tabPaddingLargeLine: "14px 0",
	tabPaddingVerticalSmallLine: "6px 12px",
	tabPaddingVerticalMediumLine: "8px 16px",
	tabPaddingVerticalLargeLine: "10px 20px",
	tabGapSmallBar: "36px",
	tabGapMediumBar: "36px",
	tabGapLargeBar: "36px",
	tabGapSmallBarVertical: "8px",
	tabGapMediumBarVertical: "8px",
	tabGapLargeBarVertical: "8px",
	tabPaddingSmallBar: "4px 0",
	tabPaddingMediumBar: "6px 0",
	tabPaddingLargeBar: "10px 0",
	tabPaddingVerticalSmallBar: "6px 12px",
	tabPaddingVerticalMediumBar: "8px 16px",
	tabPaddingVerticalLargeBar: "10px 20px",
	tabGapSmallCard: "4px",
	tabGapMediumCard: "4px",
	tabGapLargeCard: "4px",
	tabGapSmallCardVertical: "4px",
	tabGapMediumCardVertical: "4px",
	tabGapLargeCardVertical: "4px",
	tabPaddingSmallCard: "8px 16px",
	tabPaddingMediumCard: "10px 20px",
	tabPaddingLargeCard: "12px 24px",
	tabPaddingSmallSegment: "4px 0",
	tabPaddingMediumSegment: "6px 0",
	tabPaddingLargeSegment: "8px 0",
	tabPaddingVerticalLargeSegment: "0 8px",
	tabPaddingVerticalSmallCard: "8px 12px",
	tabPaddingVerticalMediumCard: "10px 16px",
	tabPaddingVerticalLargeCard: "12px 20px",
	tabPaddingVerticalSmallSegment: "0 4px",
	tabPaddingVerticalMediumSegment: "0 6px",
	tabGapSmallSegment: "0",
	tabGapMediumSegment: "0",
	tabGapLargeSegment: "0",
	tabGapSmallSegmentVertical: "0",
	tabGapMediumSegmentVertical: "0",
	tabGapLargeSegmentVertical: "0",
	panePaddingSmall: "8px 0 0 0",
	panePaddingMedium: "12px 0 0 0",
	panePaddingLarge: "16px 0 0 0",
	closeSize: "18px",
	closeIconSize: "14px"
};
//#endregion
//#region node_modules/naive-ui/es/tabs/styles/light.mjs
function Hw(e) {
	let { textColor2: t, primaryColor: n, textColorDisabled: r, closeIconColor: i, closeIconColorHover: a, closeIconColorPressed: o, closeColorHover: s, closeColorPressed: c, tabColor: l, baseColor: u, dividerColor: d, fontWeight: f, textColor1: p, borderRadius: m, fontSize: h, fontWeightStrong: g } = e;
	return {
		...Vw,
		colorSegment: l,
		tabFontSizeCard: h,
		tabTextColorLine: p,
		tabTextColorActiveLine: n,
		tabTextColorHoverLine: n,
		tabTextColorDisabledLine: r,
		tabTextColorSegment: p,
		tabTextColorActiveSegment: t,
		tabTextColorHoverSegment: t,
		tabTextColorDisabledSegment: r,
		tabTextColorBar: p,
		tabTextColorActiveBar: n,
		tabTextColorHoverBar: n,
		tabTextColorDisabledBar: r,
		tabTextColorCard: p,
		tabTextColorHoverCard: p,
		tabTextColorActiveCard: n,
		tabTextColorDisabledCard: r,
		barColor: n,
		closeIconColor: i,
		closeIconColorHover: a,
		closeIconColorPressed: o,
		closeColorHover: s,
		closeColorPressed: c,
		closeBorderRadius: m,
		tabColor: l,
		tabColorSegment: u,
		tabBorderColor: d,
		tabFontWeightActive: f,
		tabFontWeight: f,
		tabBorderRadius: m,
		paneTextColor: t,
		fontWeightStrong: g
	};
}
//#endregion
//#region node_modules/naive-ui/es/tabs/styles/dark.mjs
var Uw = {
	name: "Tabs",
	common: Z,
	peers: { Button: lS },
	self(e) {
		let t = Hw(e), { inputColor: n } = e;
		return t.colorSegment = n, t.tabColorSegment = n, t;
	}
};
//#endregion
//#region node_modules/naive-ui/es/thing/styles/light.mjs
function Ww(e) {
	let { textColor1: t, textColor2: n, fontWeightStrong: r, fontSize: i } = e;
	return {
		fontSize: i,
		titleTextColor: t,
		textColor: n,
		titleFontWeight: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/thing/styles/dark.mjs
var Gw = {
	name: "Thing",
	common: Z,
	self: Ww
}, Kw = {
	titleMarginMedium: "0 0 6px 0",
	titleMarginLarge: "-2px 0 6px 0",
	titleFontSizeMedium: "14px",
	titleFontSizeLarge: "16px",
	iconSizeMedium: "14px",
	iconSizeLarge: "14px"
}, qw = {
	name: "Timeline",
	common: Z,
	self(e) {
		let { textColor3: t, infoColorSuppl: n, errorColorSuppl: r, successColorSuppl: i, warningColorSuppl: a, textColor1: o, textColor2: s, railColor: c, fontWeightStrong: l, fontSize: u } = e;
		return {
			...Kw,
			contentFontSize: u,
			titleFontWeight: l,
			circleBorder: `2px solid ${t}`,
			circleBorderInfo: `2px solid ${n}`,
			circleBorderError: `2px solid ${r}`,
			circleBorderSuccess: `2px solid ${i}`,
			circleBorderWarning: `2px solid ${a}`,
			iconColor: t,
			iconColorInfo: n,
			iconColorError: r,
			iconColorSuccess: i,
			iconColorWarning: a,
			titleTextColor: o,
			contentTextColor: s,
			metaTextColor: t,
			lineColor: c
		};
	}
}, Jw = {
	extraFontSizeSmall: "12px",
	extraFontSizeMedium: "12px",
	extraFontSizeLarge: "14px",
	titleFontSizeSmall: "14px",
	titleFontSizeMedium: "16px",
	titleFontSizeLarge: "16px",
	closeSize: "20px",
	closeIconSize: "16px",
	headerHeightSmall: "44px",
	headerHeightMedium: "44px",
	headerHeightLarge: "50px"
}, Yw = {
	name: "Transfer",
	common: Z,
	peers: {
		Checkbox: OS,
		Scrollbar: Rh,
		Input: rb,
		Empty: Hh,
		Button: lS
	},
	self(e) {
		let { fontWeight: t, fontSizeLarge: n, fontSizeMedium: r, fontSizeSmall: i, heightLarge: a, heightMedium: o, borderRadius: s, inputColor: c, tableHeaderColor: l, textColor1: u, textColorDisabled: d, textColor2: f, textColor3: p, hoverColor: m, closeColorHover: h, closeColorPressed: g, closeIconColor: _, closeIconColorHover: v, closeIconColorPressed: y, dividerColor: b } = e;
		return {
			...Jw,
			itemHeightSmall: o,
			itemHeightMedium: o,
			itemHeightLarge: a,
			fontSizeSmall: i,
			fontSizeMedium: r,
			fontSizeLarge: n,
			borderRadius: s,
			dividerColor: b,
			borderColor: "#0000",
			listColor: c,
			headerColor: l,
			titleTextColor: u,
			titleTextColorDisabled: d,
			extraTextColor: p,
			extraTextColorDisabled: d,
			itemTextColor: f,
			itemTextColorDisabled: d,
			itemColorPending: m,
			titleFontWeight: t,
			closeColorHover: h,
			closeColorPressed: g,
			closeIconColor: _,
			closeIconColorHover: v,
			closeIconColorPressed: y
		};
	}
};
//#endregion
//#region node_modules/naive-ui/es/tree/styles/light.mjs
function Xw(e) {
	let { borderRadiusSmall: t, dividerColor: n, hoverColor: r, pressedColor: i, primaryColor: a, textColor3: o, textColor2: s, textColorDisabled: c, fontSize: l } = e;
	return {
		fontSize: l,
		lineHeight: "1.5",
		nodeHeight: "30px",
		nodeWrapperPadding: "3px 0",
		nodeBorderRadius: t,
		nodeColorHover: r,
		nodeColorPressed: i,
		nodeColorActive: J(a, { alpha: .1 }),
		arrowColor: o,
		nodeTextColor: s,
		nodeTextColorDisabled: c,
		loadingColor: a,
		dropMarkColor: a,
		lineColor: n
	};
}
//#endregion
//#region node_modules/naive-ui/es/tree/styles/dark.mjs
var Zw = {
	name: "Tree",
	common: Z,
	peers: {
		Checkbox: OS,
		Scrollbar: Rh,
		Empty: Hh
	},
	self(e) {
		let { primaryColor: t } = e, n = Xw(e);
		return n.nodeColorActive = J(t, { alpha: .15 }), n;
	}
}, Qw = {
	name: "TreeSelect",
	common: Z,
	peers: {
		Tree: Zw,
		Empty: Hh,
		InternalSelection: Fy
	}
}, $w = {
	headerFontSize1: "30px",
	headerFontSize2: "22px",
	headerFontSize3: "18px",
	headerFontSize4: "16px",
	headerFontSize5: "16px",
	headerFontSize6: "16px",
	headerMargin1: "28px 0 20px 0",
	headerMargin2: "28px 0 20px 0",
	headerMargin3: "28px 0 20px 0",
	headerMargin4: "28px 0 18px 0",
	headerMargin5: "28px 0 18px 0",
	headerMargin6: "28px 0 18px 0",
	headerPrefixWidth1: "16px",
	headerPrefixWidth2: "16px",
	headerPrefixWidth3: "12px",
	headerPrefixWidth4: "12px",
	headerPrefixWidth5: "12px",
	headerPrefixWidth6: "12px",
	headerBarWidth1: "4px",
	headerBarWidth2: "4px",
	headerBarWidth3: "3px",
	headerBarWidth4: "3px",
	headerBarWidth5: "3px",
	headerBarWidth6: "3px",
	pMargin: "16px 0 16px 0",
	liMargin: ".25em 0 0 0",
	olPadding: "0 0 0 2em",
	ulPadding: "0 0 0 2em"
};
//#endregion
//#region node_modules/naive-ui/es/typography/styles/light.mjs
function eT(e) {
	let { primaryColor: t, textColor2: n, borderColor: r, lineHeight: i, fontSize: a, borderRadiusSmall: o, dividerColor: s, fontWeightStrong: c, textColor1: l, textColor3: u, infoColor: d, warningColor: f, errorColor: p, successColor: m, codeColor: h } = e;
	return {
		...$w,
		aTextColor: t,
		blockquoteTextColor: n,
		blockquotePrefixColor: r,
		blockquoteLineHeight: i,
		blockquoteFontSize: a,
		codeBorderRadius: o,
		liTextColor: n,
		liLineHeight: i,
		liFontSize: a,
		hrColor: s,
		headerFontWeight: c,
		headerTextColor: l,
		pTextColor: n,
		pTextColor1Depth: l,
		pTextColor2Depth: n,
		pTextColor3Depth: u,
		pLineHeight: i,
		pFontSize: a,
		headerBarColor: t,
		headerBarColorPrimary: t,
		headerBarColorInfo: d,
		headerBarColorError: p,
		headerBarColorWarning: f,
		headerBarColorSuccess: m,
		textColor: n,
		textColor1Depth: l,
		textColor2Depth: n,
		textColor3Depth: u,
		textColorPrimary: t,
		textColorInfo: d,
		textColorSuccess: m,
		textColorWarning: f,
		textColorError: p,
		codeTextColor: n,
		codeColor: h,
		codeBorder: "1px solid #0000"
	};
}
//#endregion
//#region node_modules/naive-ui/es/typography/styles/dark.mjs
var tT = {
	name: "Typography",
	common: Z,
	self: eT
};
//#endregion
//#region node_modules/naive-ui/es/upload/styles/light.mjs
function nT(e) {
	let { iconColor: t, primaryColor: n, errorColor: r, textColor2: i, successColor: a, opacityDisabled: o, actionColor: s, borderColor: c, hoverColor: l, lineHeight: u, borderRadius: d, fontSize: f } = e;
	return {
		fontSize: f,
		lineHeight: u,
		borderRadius: d,
		draggerColor: s,
		draggerBorder: `1px dashed ${c}`,
		draggerBorderHover: `1px dashed ${n}`,
		itemColorHover: l,
		itemColorHoverError: J(r, { alpha: .06 }),
		itemTextColor: i,
		itemTextColorError: r,
		itemTextColorSuccess: a,
		itemIconColor: t,
		itemDisabledOpacity: o,
		itemBorderImageCardError: `1px solid ${r}`,
		itemBorderImageCard: `1px solid ${c}`
	};
}
//#endregion
//#region node_modules/naive-ui/es/upload/styles/dark.mjs
var rT = {
	name: "Upload",
	common: Z,
	peers: {
		Button: lS,
		Progress: Sw
	},
	self(e) {
		let { errorColor: t } = e, n = nT(e);
		return n.itemColorHoverError = J(t, { alpha: .09 }), n;
	}
}, iT = {
	name: "Watermark",
	common: Z,
	self(e) {
		let { fontFamily: t } = e;
		return { fontFamily: t };
	}
};
//#endregion
//#region node_modules/naive-ui/es/heatmap/styles/light.mjs
function aT(e) {
	let { borderRadius: t, fontSizeMini: n, fontSizeTiny: r, fontSizeSmall: i, fontWeight: a, textColor2: o, cardColor: s, buttonColor2Hover: c } = e;
	return {
		activeColors: [
			"#9be9a8",
			"#40c463",
			"#30a14e",
			"#216e39"
		],
		borderRadius: t,
		borderColor: s,
		textColor: o,
		mininumColor: c,
		fontWeight: a,
		loadingColorStart: "rgba(0, 0, 0, 0.06)",
		loadingColorEnd: "rgba(0, 0, 0, 0.12)",
		rectSizeSmall: "10px",
		rectSizeMedium: "11px",
		rectSizeLarge: "12px",
		borderRadiusSmall: "2px",
		borderRadiusMedium: "2px",
		borderRadiusLarge: "2px",
		xGapSmall: "2px",
		xGapMedium: "3px",
		xGapLarge: "3px",
		yGapSmall: "2px",
		yGapMedium: "3px",
		yGapLarge: "3px",
		fontSizeSmall: r,
		fontSizeMedium: n,
		fontSizeLarge: i
	};
}
//#endregion
//#region node_modules/naive-ui/es/icon-wrapper/styles/light.mjs
function oT(e) {
	let { primaryColor: t, baseColor: n } = e;
	return {
		color: t,
		iconColor: n
	};
}
//#endregion
//#region node_modules/naive-ui/es/legacy-transfer/styles/_common.mjs
var sT = {
	extraFontSize: "12px",
	width: "440px"
};
//#endregion
//#region node_modules/naive-ui/es/marquee/styles/light.mjs
function cT() {
	return {};
}
//#endregion
//#region node_modules/naive-ui/es/page-header/styles/_common.mjs
var lT = {
	titleFontSize: "18px",
	backSize: "22px"
};
//#endregion
//#region node_modules/naive-ui/es/page-header/styles/light.mjs
function uT(e) {
	let { textColor1: t, textColor2: n, textColor3: r, fontSize: i, fontWeightStrong: a, primaryColorHover: o, primaryColorPressed: s } = e;
	return {
		...lT,
		titleFontWeight: a,
		fontSize: i,
		titleTextColor: t,
		backColor: n,
		backColorHover: o,
		backColorPressed: s,
		subtitleTextColor: r
	};
}
//#endregion
//#region node_modules/naive-ui/es/themes/dark.mjs
var dT = {
	name: "dark",
	common: Z,
	Alert: Ly,
	Anchor: $y,
	AutoComplete: Wb,
	Avatar: Jx,
	AvatarGroup: {
		name: "AvatarGroup",
		common: Z,
		peers: { Avatar: Jx },
		self: Yx
	},
	BackTop: Zx,
	Badge: Qx,
	Breadcrumb: aS,
	Button: lS,
	ButtonGroup: nw,
	Calendar: {
		name: "Calendar",
		common: Z,
		peers: { Button: lS },
		self: gS
	},
	Card: bS,
	Carousel: {
		name: "Carousel",
		common: Z,
		self: TS
	},
	Cascader: MS,
	Checkbox: OS,
	Code: US,
	Collapse: GS,
	CollapseTransition: {
		name: "CollapseTransition",
		common: Z,
		self: KS
	},
	ColorPicker: {
		name: "ColorPicker",
		common: Z,
		peers: {
			Input: rb,
			Button: lS
		},
		self: qS
	},
	DataTable: mC,
	DatePicker: CC,
	Descriptions: EC,
	Dialog: kC,
	Divider: BC,
	Drawer: HC,
	Dropdown: oC,
	DynamicInput: WC,
	DynamicTags: QC,
	Element: $C,
	Empty: Hh,
	Ellipsis: dC,
	Equation: {
		name: "Equation",
		common: Z,
		self: () => ({})
	},
	Flex: tw,
	Form: aw,
	GradientText: ow,
	Heatmap: {
		name: "Heatmap",
		common: Z,
		self(e) {
			return {
				...aT(e),
				activeColors: [
					"#0d4429",
					"#006d32",
					"#26a641",
					"#39d353"
				],
				mininumColor: "rgba(255, 255, 255, 0.1)",
				loadingColorStart: "rgba(255, 255, 255, 0.12)",
				loadingColorEnd: "rgba(255, 255, 255, 0.18)"
			};
		}
	},
	Icon: _C,
	IconWrapper: {
		name: "IconWrapper",
		common: Z,
		self: oT
	},
	Image: {
		name: "Image",
		common: Z,
		peers: { Tooltip: cC },
		self: (e) => {
			let { textColor2: t } = e;
			return {
				toolbarIconColor: t,
				toolbarColor: "rgba(0, 0, 0, .35)",
				toolbarBoxShadow: "none",
				toolbarBorderRadius: "24px"
			};
		}
	},
	Input: rb,
	InputNumber: sw,
	InputOtp: lw,
	LegacyTransfer: {
		name: "Transfer",
		common: Z,
		peers: {
			Checkbox: OS,
			Scrollbar: Rh,
			Input: rb,
			Empty: Hh,
			Button: lS
		},
		self(e) {
			let { iconColorDisabled: t, iconColor: n, fontWeight: r, fontSizeLarge: i, fontSizeMedium: a, fontSizeSmall: o, heightLarge: s, heightMedium: c, heightSmall: l, borderRadius: u, inputColor: d, tableHeaderColor: f, textColor1: p, textColorDisabled: m, textColor2: h, hoverColor: g } = e;
			return {
				...sT,
				itemHeightSmall: l,
				itemHeightMedium: c,
				itemHeightLarge: s,
				fontSizeSmall: o,
				fontSizeMedium: a,
				fontSizeLarge: i,
				borderRadius: u,
				borderColor: "#0000",
				listColor: d,
				headerColor: f,
				titleTextColor: p,
				titleTextColorDisabled: m,
				extraTextColor: h,
				filterDividerColor: "#0000",
				itemTextColor: h,
				itemTextColorDisabled: m,
				itemColorPending: g,
				titleFontWeight: r,
				iconColor: n,
				iconColorDisabled: t
			};
		}
	},
	Layout: uw,
	List: fw,
	LoadingBar: MC,
	Log: pw,
	Menu: _w,
	Mention: mw,
	Message: FC,
	Modal: jC,
	Notification: RC,
	PageHeader: {
		name: "PageHeader",
		common: Z,
		self: uT
	},
	Pagination: rC,
	Popconfirm: bw,
	Popover: og,
	Popselect: YS,
	Progress: Sw,
	QrCode: {
		name: "QrCode",
		common: Z,
		self: (e) => ({ borderRadius: e.borderRadius })
	},
	Radio: uC,
	Rate: Cw,
	Result: Ew,
	Row: {
		name: "Row",
		common: Z
	},
	Scrollbar: Rh,
	Select: QS,
	Skeleton: {
		name: "Skeleton",
		common: Z,
		self(e) {
			let { heightSmall: t, heightMedium: n, heightLarge: r, borderRadius: i } = e;
			return {
				color: "rgba(255, 255, 255, 0.12)",
				colorEnd: "rgba(255, 255, 255, 0.18)",
				borderRadius: i,
				heightSmall: t,
				heightMedium: n,
				heightLarge: r
			};
		}
	},
	Slider: Ow,
	Space: KC,
	Spin: Aw,
	Statistic: Mw,
	Steps: Fw,
	Switch: Lw,
	Table: Bw,
	Tabs: Uw,
	Tag: by,
	Thing: Gw,
	TimePicker: bC,
	Timeline: qw,
	Tooltip: cC,
	Transfer: Yw,
	Tree: Zw,
	TreeSelect: Qw,
	Typography: tT,
	Upload: rT,
	Watermark: iT,
	Split: {
		name: "Split",
		common: Z
	},
	FloatButton: {
		name: "FloatButton",
		common: Z,
		self(e) {
			let { popoverColor: t, textColor2: n, buttonColor2Hover: r, buttonColor2Pressed: i, primaryColor: a, primaryColorHover: o, primaryColorPressed: s, baseColor: c, borderRadius: l } = e;
			return {
				color: t,
				textColor: n,
				boxShadow: "0 2px 8px 0px rgba(0, 0, 0, .12)",
				boxShadowHover: "0 2px 12px 0px rgba(0, 0, 0, .18)",
				boxShadowPressed: "0 2px 12px 0px rgba(0, 0, 0, .18)",
				colorHover: r,
				colorPressed: i,
				colorPrimary: a,
				colorPrimaryHover: o,
				colorPrimaryPressed: s,
				textColorPrimary: c,
				borderRadiusSquare: l
			};
		}
	},
	FloatButtonGroup: {
		name: "FloatButtonGroup",
		common: Z,
		self(e) {
			let { popoverColor: t, dividerColor: n, borderRadius: r } = e;
			return {
				color: t,
				buttonBorderColor: n,
				borderRadiusSquare: r,
				boxShadow: "0 2px 8px 0px rgba(0, 0, 0, .12)"
			};
		}
	},
	Marquee: {
		name: "Marquee",
		common: Z,
		self: cT
	}
};
//#endregion
export { Xy as NAlert, mS as NButton, wS as NCard, JS as NConfigProvider, Hb as NInput, eC as NSelect, ZC as NSpace, Ny as NTag, z as computed, Jo as createApp, dT as darkTheme, hc as dateEnUS, gc as dateZhCN, N as defineComponent, Rs as enUS, Ea as h, wn as nextTick, Lr as onBeforeUnmount, Fr as onMounted, Lt as reactive, j as ref, Yt as shallowRef, Wn as watch, Ln as withDirectives, zs as zhCN };
