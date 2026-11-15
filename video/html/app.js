/* Promo renderer — deterministic HTML scene graph.
 *
 * Nothing here uses CSS animations, transitions or timers. window.seek(t) computes
 * every animated property as a pure function of t (seconds), so the renderer can
 * jump to any frame in any order (and in several pages in parallel) and always
 * get the same picture. When a scene shows a screen recording, seek(t) returns a
 * Promise that resolves once every visible <video> has finished seeking.
 *
 * Entry points
 *   window.TIMELINE / window.AUDIO   (from data.js)  -> mounted automatically
 *   window.loadTimeline(tl, audio)   re-mount with a new timeline (studio live preview)
 *   window.seek(t)                   render moment t
 *   window.setPlaying(bool)          preview only: let <video>s play in real time
 *   window.__ready                   true once fonts + images + videos are loaded
 */
(() => {
  "use strict";

  // ---------- math helpers ----------
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const lerp = (a, b, p) => a + (b - a) * p;
  const E = {
    linear: p => p,
    outCubic: p => 1 - Math.pow(1 - p, 3),
    outQuint: p => 1 - Math.pow(1 - p, 5),
    outExpo: p => (p >= 1 ? 1 : 1 - Math.pow(2, -10 * p)),
    inCubic: p => p * p * p,
    inOutCubic: p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    inOutSine: p => -(Math.cos(Math.PI * p) - 1) / 2,
    outBack: p => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
    outBackBig: p => { const c1 = 2.6, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
  };
  const prog = (t, t0, d, e = E.outCubic) => e(clamp((t - t0) / d));

  function h(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }
  function style(el, { x = 0, y = 0, s = 1, r = 0, o = 1, blur = 0 }) {
    el.style.opacity = o.toFixed(4);
    el.style.transform = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,0) scale(${s.toFixed(4)}) rotate(${r.toFixed(2)}deg)`;
    el.style.filter = blur > 0.05 ? `blur(${blur.toFixed(2)}px)` : "none";
  }
  // Standard "rise in" entrance: fade + slide up + de-blur.
  function rise(t, t0, { d = 0.7, dy = 70, s0 = 1, blur = 14, ease = E.outQuint } = {}) {
    const p = prog(t, t0, d, ease);
    const po = clamp((t - t0) / (d * 0.5));
    return { y: dy * (1 - p), s: lerp(s0, 1, p), o: po, blur: blur * (1 - po) };
  }
  const px = v => `${v.toFixed(1)}px`;
  function place(el, x, y, w, hh) {
    el.style.left = px(x); el.style.top = px(y);
    if (w != null) el.style.width = px(w);
    if (hh != null) el.style.height = px(hh);
  }

  const isArabic = s => /[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]/.test(s);
  const norm = s => s.toLowerCase().normalize("NFKD").replace(/[ʻʼ‘’'`ʻ]/g, "").replace(/[^\p{L}\p{N}]+/gu, "");

  // ---------- colours ----------
  function toHex(c, fb = "#7C5CFF") {
    if (typeof c !== "string") return fb;
    c = c.trim();
    if (/^#[0-9a-f]{3}$/i.test(c)) return "#" + [1, 2, 3].map(i => c[i] + c[i]).join("");
    return /^#[0-9a-f]{6}$/i.test(c) ? c : fb;
  }
  const hex = c => [1, 3, 5].map(i => parseInt(c.slice(i, i + 2), 16));
  const mixA = (a, b, p) => { const A = hex(a), Bc = hex(b); return A.map((v, i) => Math.round(lerp(v, Bc[i], p))); };
  const mix = (a, b, p) => `rgb(${mixA(a, b, p).join(",")})`;
  const mixHex = (a, b, p) => "#" + mixA(a, b, p).map(v => v.toString(16).padStart(2, "0")).join("");
  const rgba = (c, a) => `rgba(${hex(c).join(",")},${a})`;
  const lum = c => { const [r, g, b] = hex(c).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };

  function brandOf(b0 = {}) {
    const b = { ...b0 };
    b.name = b0.name || "";
    b.primary = toHex(b0.primary, "#7C5CFF");
    b.accent = toHex(b0.accent, "#22D3A6");
    let bg = toHex(b0.background, "#0B1020");
    // the design is dark-on-light-text; keep the background dark enough to read
    for (let i = 0; i < 12 && lum(bg) > 0.06; i++) bg = mixHex(bg, "#05070F", 0.25);
    b.background = bg;
    b.surface = toHex(b0.surface, mixHex(bg, "#2A3358", 0.45));
    b.text = toHex(b0.text, "#F5F7FF");
    b.muted = toHex(b0.muted, "#9AA3C0");
    b.primaryLight = mixHex(b.primary, "#FFFFFF", 0.3);
    return b;
  }

  // ---------- page state ----------
  const params = new URLSearchParams(location.search);
  const renderMode = params.has("render");
  const embed = params.has("embed");
  if (renderMode) document.body.classList.add("render");
  if (embed) document.body.classList.add("embed");
  const stage = document.getElementById("stage");
  let CUR = null;
  let lastT = 0;
  let playing = false;
  window.__ready = false;

  // ======================================================================
  function mount(TL, AUD) {
    TL = { captions: [], scenes: [], ...TL };
    const B = brandOf(TL.brand);
    const W = TL.width || 1080, H = TL.height || 1920;
    const LAND = W > H;
    const FPS = TL.fps || 30;
    const ASSETS = TL.assets || {};
    const BASE = TL.asset_base || "";
    let seed = 1337;
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);

    stage.style.width = `${W}px`;
    stage.style.height = `${H}px`;
    stage.classList.toggle("land", LAND);
    document.documentElement.lang = TL.language || "en";

    const root = document.documentElement.style;
    root.setProperty("--bg", B.background);
    root.setProperty("--surface", B.surface);
    root.setProperty("--primary", B.primary);
    root.setProperty("--primary-l", B.primaryLight);
    root.setProperty("--accent", B.accent);
    root.setProperty("--text", B.text);
    root.setProperty("--muted", B.muted);

    const assetUrl = name => {
      const m = ASSETS[name];
      if (m && m.url) return m.url;
      return BASE + encodeURIComponent(name);
    };
    const assetMeta = name => ASSETS[name] || null;
    const isVideo = name => {
      const m = assetMeta(name);
      return m ? m.type === "video" : /\.(webm|mp4|mov|m4v)$/i.test(name || "");
    };

    // ---------- audio analysis (from render.py) ----------
    const HAS_AUD = !!(AUD && AUD.rms && AUD.rms.length);
    function audioAt(t) {
      if (!HAS_AUD) return { rms: 0, bands: [] };
      const i = clamp(Math.round(t * AUD.fps), 0, AUD.rms.length - 1);
      return { rms: AUD.rms[i], bands: AUD.bands[i] };
    }

    // ---------- headline split into animated words ----------
    // Words that are the brand name or contain digits get the gradient fill.
    function words(text, cls = "") {
      const box = h("div", cls);
      const parts = String(text || "").split(/(\s+)/).filter(p => p.length);
      const list = [];
      for (const p of parts) {
        if (/^\s+$/.test(p)) { box.appendChild(document.createTextNode(" ")); continue; }
        const w = h("span", "word", p);
        if ((B.name && norm(p) === norm(B.name)) || /\d/.test(p)) w.classList.add("grad");
        if (isArabic(p)) { w.lang = "ar"; w.dir = "rtl"; }
        box.appendChild(w); list.push(w);
      }
      if (isArabic(String(text || ""))) box.dir = "rtl";
      return { box, list };
    }
    // Shrink a headline until it fits its container width and maxLines lines.
    function fitHeadline(box, maxLines) {
      const par = box.parentElement;
      if (!par) return;
      const pcs = getComputedStyle(par);
      const avail = par.clientWidth - parseFloat(pcs.paddingLeft) - parseFloat(pcs.paddingRight);
      const cs = getComputedStyle(box);
      let fs = parseFloat(cs.fontSize);
      const lh = (parseFloat(cs.lineHeight) || fs * 1.05) / fs;
      for (let i = 0; i < 18; i++) {
        const ok = box.offsetWidth <= avail + 1 && box.scrollWidth <= box.clientWidth + fs * 0.12 && box.offsetHeight <= fs * lh * maxLines + 6;
        if (ok || fs < 30) break;
        fs *= 0.92;
        box.style.fontSize = `${fs.toFixed(1)}px`;
      }
    }
    function animWords(list, t, t0, gap = 0.08, opt = {}) {
      list.forEach((w, i) => style(w, rise(t, t0 + i * gap, { dy: 90, blur: 16, s0: 0.92, d: 0.8, ...opt })));
    }

    // ---------- item ↔ spoken-word sync ----------
    // Items pop in when the voice-over says them (matched against caption words);
    // items that can't be matched (e.g. "Русский" vs "rus") follow the previous one.
    function itemTimes(scene, items) {
      const caps = TL.captions.filter(c => c.start >= scene.start - 0.01 && c.start < scene.end);
      const capWords = [];
      for (const c of caps) {
        const ws = c.text.split(/\s+/).filter(Boolean);
        const total = ws.reduce((a, w) => a + w.length, 0) || 1;
        let acc = 0;
        for (const w of ws) { capWords.push({ w: norm(w), t: c.start + (acc / total) * (c.end - c.start), cap: c }); acc += w.length; }
      }
      const times = [];
      let prev = null, cursor = 0;
      items.forEach((it, i) => {
        const toks = String(it || "").split(/\s+/).map(norm).filter(x => x.length >= 3);
        let hit = null;
        for (let k = cursor; k < capWords.length && !hit; k++) {
          const cw = capWords[k].w;
          if (cw.length >= 3 && toks.some(tk => tk.startsWith(cw) || cw.startsWith(tk))) { hit = capWords[k]; cursor = k + 1; }
        }
        let t;
        if (hit) t = hit.t;
        else if (prev != null) t = prev + 0.45;
        else t = scene.start + 0.9 + i * 0.35;
        if (prev != null && t < prev + 0.3) t = prev + 0.3;
        times.push(t); prev = t;
      });
      return times;
    }

    // ---------- logo (brand logo image, or the animated bars) ----------
    function logoInto(box, nbars) {
      box.innerHTML = "";
      if (B.logo) {
        box.classList.add("has-img");
        const img = box.appendChild(h("img"));
        img.src = assetUrl(B.logo); img.alt = "";
        return [];
      }
      box.classList.remove("has-img");
      const lb = box.appendChild(h("div", "logo-bars"));
      return Array.from({ length: nbars }, () => lb.appendChild(h("i")));
    }

    // ---------- media (screenshot <img> or recording <video>) ----------
    const videos = [];        // {el, start, trim, dur}
    function media(name, sceneStart, trim = 0) {
      if (!name) { const ph = h("div", "media missing"); ph.appendChild(h("span", "", "No screenshot")); return { el: ph, aspect: 2.1 }; }
      const meta = assetMeta(name);
      let el;
      if (isVideo(name)) {
        el = h("video", "media");
        el.muted = true; el.playsInline = true; el.preload = "auto";
        el.src = assetUrl(name);
        videos.push({ el, start: sceneStart, trim: +trim || 0, dur: meta && meta.duration ? meta.duration : null });
      } else {
        el = h("img", "media");
        el.decoding = "sync"; el.alt = "";
        el.src = assetUrl(name);
        el.addEventListener("error", () => { el.classList.add("broken"); });
      }
      const aspect = meta && meta.w && meta.h ? meta.h / meta.w : 2.1;
      return { el, aspect, meta };
    }

    // ---------- device frames ----------
    // Geometry in stage pixels; the screen rectangle is relative to the device box.
    function deviceGeom(kind, aspect, box, maxW = Infinity) {
      if (kind === "phone") {
        const a = clamp(aspect, 1.55, 2.25);
        let w = Math.min(box.w, maxW, box.h / (0.93 * a + 0.07));
        const bez = w * 0.035, sw = w - 2 * bez, sh = sw * a, hh = sh + 2 * bez;
        return { kind, w, h: hh, sx: bez, sy: bez, sw, sh, radius: w * 0.15, sr: w * 0.15 - bez,
                 x: box.x + (box.w - w) / 2, y: box.y + (box.h - hh) / 2 };
      }
      if (kind === "browser") {
        const a = clamp(aspect, 0.42, 0.75);
        let w = Math.min(box.w, maxW);
        let bar = Math.max(30, w * 0.045);
        if (w * a + bar > box.h) { w = (box.h) / (a + 0.045); bar = Math.max(30, w * 0.045); }
        const sh = w * a;
        return { kind, w, h: sh + bar, sx: 0, sy: bar, sw: w, sh, bar, radius: Math.max(12, w * 0.016), sr: 0,
                 x: box.x + (box.w - w) / 2, y: box.y + (box.h - sh - bar) / 2 };
      }
      const a = clamp(aspect, 0.4, 2.2);
      let w = Math.min(box.w, maxW, box.h / a);
      return { kind: "none", w, h: w * a, sx: 0, sy: 0, sw: w, sh: w * a, radius: Math.max(16, w * 0.03), sr: Math.max(16, w * 0.03),
               x: box.x + (box.w - w) / 2, y: box.y + (box.h - w * a) / 2 };
    }

    // Builds the frame DOM for geometry g. Returns {dev, screen}.
    function deviceDom(g, url) {
      const dev = h("div", `device dev-${g.kind}`);
      dev.style.width = px(g.w); dev.style.height = px(g.h); dev.style.borderRadius = px(g.radius);
      if (g.kind === "phone") {
        dev.appendChild(h("i", "btn b1")); dev.appendChild(h("i", "btn b2")); dev.appendChild(h("i", "btn b3"));
      }
      if (g.kind === "browser") {
        const bar = dev.appendChild(h("div", "bbar"));
        bar.style.height = px(g.bar);
        bar.style.fontSize = px(g.bar * 0.42);
        const dots = bar.appendChild(h("div", "dots"));
        ["#FF5F57", "#FEBC2E", "#28C840"].forEach(c => { const d = dots.appendChild(h("i")); d.style.background = c; d.style.width = d.style.height = px(g.bar * 0.26); });
        const u = bar.appendChild(h("div", "url"));
        u.style.height = px(g.bar * 0.62);
        u.appendChild(h("span", "lock", "🔒"));
        u.appendChild(h("span", "", url || B.name || ""));
      }
      const screen = dev.appendChild(h("div", "dscreen"));
      place(screen, g.sx, g.sy, g.sw, g.sh);
      screen.style.borderRadius = g.kind === "browser" ? `0 0 ${px(g.radius)} ${px(g.radius)}` : px(g.sr);
      if (g.kind === "phone") {
        const isl = dev.appendChild(h("div", "island"));
        const iw = g.sw * 0.3, ih = g.sw * 0.085;
        place(isl, (g.w - iw) / 2, g.sy + g.sw * 0.03, iw, ih);
      }
      return { dev, screen };
    }

    // ---------- AI layers (only created when the timeline has them) ----------
    // Presenter: talking-head video time-aligned to the whole timeline (t == video time).
    const PT = TL.presenter || null;
    const pUrl = PT && PT.ready && PT.video_url ? PT.video_url : null;
    const pPortrait = PT && PT.portrait_url ? PT.portrait_url : null;
    const pvids = [];      // {el, vis: () => bool}
    const pBubbleIds = new Set(PT && PT.mode !== "full" && pUrl ? (PT.bubble || []) : []);
    // Bubble rectangle (stage px): 9:16 above the caption pill on the left (inside the safe zone,
    // away from the right-hand button rail); 16:9 bottom-right above the captions.
    const RB = pBubbleIds.size ? (LAND ? { x: W - 128 - 250, y: 650, w: 250, h: 250 } : { x: 64, y: 1010, w: 258, h: 258 }) : null;
    const reserveFor = sc => (RB && pBubbleIds.has(sc.id) ? RB : null);
    for (const id of ["bgvideo", "pfull", "pbubble"]) { const old = document.getElementById(id); if (old) old.remove(); }
    let pfull = null;
    if (PT && (PT.full || []).length) {
      pfull = document.getElementById("bg").appendChild(h("div"));
      pfull.id = "pfull";
    }
    stage.classList.toggle("has-pscene", !!pfull);
    function presenterMedia(cls) {
      if (pUrl) {
        const v = h("video", cls);
        v.muted = true; v.playsInline = true; v.preload = "auto"; v.src = pUrl;
        return v;
      }
      if (pPortrait) { const img = h("img", cls); img.decoding = "sync"; img.alt = ""; img.src = pPortrait; return img; }
      return h("div", `${cls} ph`);
    }

    // Fit a scene's content away from the presenter bubble: returns {k, tx, ty} so that
    // X' = k*X + t keeps the content's bounding box `c` out of rectangle R (null = already clear).
    function fitAway(c, R) {
      const m = 22;
      if (c.x1 <= R.x - m || c.x0 >= R.x + R.w + m || c.y1 <= R.y - m || c.y0 >= R.y + R.h + m) return null;
      const areas = LAND
        ? [{ x0: 110, x1: R.x - m, y0: 118, y1: 925 }, { x0: 110, x1: W - 110, y0: 118, y1: R.y - m }]
        : [{ x0: 50, x1: W - 50, y0: 318, y1: R.y - m }, { x0: R.x + R.w + m, x1: W - 50, y0: 318, y1: 1272 }];
      let best = null;
      for (const A of areas) {
        const k = Math.min(1, (A.x1 - A.x0) / (c.x1 - c.x0), (A.y1 - A.y0) / (c.y1 - c.y0));
        if (!best || k > best.k + 0.001) best = { k, A };
      }
      const { k, A } = best;
      const w = (c.x1 - c.x0) * k, hh = (c.y1 - c.y0) * k;
      const cx = (c.x0 + c.x1) / 2, cy = (c.y0 + c.y1) / 2;
      const nx = clamp(cx, A.x0 + w / 2, A.x1 - w / 2), ny = clamp(cy, A.y0 + hh / 2, A.y1 - hh / 2);
      return { k, tx: nx - k * cx, ty: ny - k * cy };
    }

    // ---------- scene builders: each returns update(t) ----------
    const builders = {
      hero(sc, el) {
        let kicker = null;
        if (sc.kicker) { kicker = h("div", "kicker"); kicker.appendChild(h("span", "dot")); kicker.appendChild(h("span", "", sc.kicker)); el.appendChild(kicker); }
        const hl = words(sc.headline, "headline"); el.appendChild(hl.box);
        const sub = sc.sub ? el.appendChild(h("div", "sub", sc.sub)) : null;
        const wave = el.appendChild(h("div", "wave"));
        const N = LAND ? 61 : 41, bars = [];
        for (let i = 0; i < N; i++) {
          const b = h("i"); b.style.background = mix(B.primary, B.accent, i / (N - 1));
          wave.appendChild(b); bars.push(b);
        }
        const t0 = sc.start + (sc.start === 0 ? -0.3 : 0.1);
        const maxH = LAND ? 150 : 190;
        return t => {
          if (kicker) style(kicker, rise(t, t0, { dy: 40, s0: 0.8, d: 0.6, ease: E.outBack }));
          animWords(hl.list, t, t0 + 0.15, 0.12);
          if (sub) style(sub, rise(t, t0 + 0.55, { dy: 50 }));
          style(wave, rise(t, t0 + 0.75, { dy: 40, blur: 0, s0: 0.9 }));
          const a = audioAt(t), nb = a.bands.length;
          bars.forEach((b, i) => {
            const c = Math.abs(i - (N - 1) / 2) / ((N - 1) / 2);           // 0 centre … 1 edge
            const band = nb ? a.bands[Math.min(nb - 1, Math.floor(c * nb * 0.85))] : 0;
            const idle = 0.5 + 0.5 * Math.sin(t * 4.2 - i * 0.45);
            const env = 1 - 0.55 * c * c;
            const hgt = 14 + env * (20 * idle + maxH * band + (HAS_AUD ? 0 : 70 * idle * (1 - c * 0.7)));
            b.style.height = `${hgt.toFixed(1)}px`;
            b.style.opacity = (0.55 + 0.45 * band).toFixed(3);
          });
        };
      },

      chips(sc, el) {
        const hl = words(sc.headline, "headline"); el.appendChild(hl.box);
        const grid = el.appendChild(h("div", "chip-grid"));
        const items = sc.items || [];
        if (LAND) grid.style.gridTemplateColumns = `repeat(${Math.min(Math.max(items.length, 1), items.length === 4 ? 4 : 3)}, 1fr)`;
        const chips = items.map(txt => {
          const c = h("div", "chip");
          const span = h("span", "", txt);
          if (isArabic(txt)) { span.lang = "ar"; span.dir = "rtl"; }
          c.appendChild(span);
          const tick = h("div", "tick", "✓"); c.appendChild(tick);
          grid.appendChild(c);
          return { c, tick };
        });
        const times = itemTimes(sc, items);
        const t0 = sc.start + 0.1;
        return t => {
          animWords(hl.list, t, t0, 0.1, { s0: 0.6, dy: 40, ease: E.outBack, d: 0.7 });
          chips.forEach(({ c, tick }, i) => {
            const ti = times[i] - 0.12;
            const p = prog(t, ti, 0.55, E.outBackBig);
            const o = clamp((t - ti) / 0.15);
            const active = clamp((t - ti) / 0.15) * (1 - clamp((t - (times[i + 1] ?? sc.speech_end) + 0.05) / 0.3));
            style(c, { s: lerp(0.4, 1, p), r: lerp(i % 2 ? 8 : -8, 0, p), y: lerp(60, 0, p), o });
            c.style.borderColor = active > 0.01 ? mix("#2a3150", B.accent, active) : "rgba(255,255,255,.09)";
            c.style.boxShadow = `0 20px 50px rgba(0,0,0,.35), 0 0 ${(50 * active).toFixed(1)}px ${rgba(B.accent, (0.45 * active).toFixed(3))}`;
            const tp = prog(t, ti + 0.25, 0.4, E.outBackBig);
            style(tick, { s: tp, o: clamp((t - ti - 0.25) / 0.1) });
          });
        };
      },

      stat(sc, el) {
        const wrap = el.appendChild(h("div", "stat-wrap"));
        const target = parseFloat(sc.stat);
        const n = Number.isFinite(target) && target >= 1 && target <= 24 ? Math.round(target) : 12;
        const NS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(NS, "svg");
        svg.setAttribute("viewBox", "0 0 560 560");
        const gid = `sg-${sc.id || Math.floor(rnd() * 1e6)}`.replace(/[^\w-]/g, "");
        const defs = document.createElementNS(NS, "defs");
        defs.innerHTML = `<linearGradient id="${gid}" gradientUnits="userSpaceOnUse" x1="60" y1="40" x2="500" y2="520"><stop offset="0" stop-color="${B.primary}"/><stop offset="1" stop-color="${B.accent}"/></linearGradient>`;
        svg.appendChild(defs);
        const outer = document.createElementNS(NS, "circle");
        outer.setAttribute("cx", 280); outer.setAttribute("cy", 280); outer.setAttribute("r", 274);
        outer.setAttribute("fill", "none"); outer.setAttribute("stroke", "rgba(255,255,255,.14)");
        outer.setAttribute("stroke-width", 2); outer.setAttribute("stroke-dasharray", "2 14");
        svg.appendChild(outer);
        const R = 240, segs = [];
        const gapA = 0.09;
        for (let i = 0; i < n; i++) {
          const a0 = -Math.PI / 2 + (i / n) * 2 * Math.PI + gapA / 2;
          const a1 = -Math.PI / 2 + ((i + 1) / n) * 2 * Math.PI - gapA / 2;
          const p = document.createElementNS(NS, "path");
          const x0 = 280 + R * Math.cos(a0), y0 = 280 + R * Math.sin(a0), x1 = 280 + R * Math.cos(a1), y1 = 280 + R * Math.sin(a1);
          p.setAttribute("d", `M${x0} ${y0} A${R} ${R} 0 0 1 ${x1} ${y1}`);
          p.setAttribute("fill", "none"); p.setAttribute("stroke-width", 26); p.setAttribute("stroke-linecap", "round");
          svg.appendChild(p); segs.push(p);
        }
        wrap.appendChild(svg);
        const num = wrap.appendChild(h("div", "stat-num grad", "0"));
        const statStr = String(sc.stat ?? "");
        if (statStr.length > 3) num.style.fontSize = `${Math.round(330 * 3.2 / (statStr.length + 0.2))}px`;
        const hl = words(sc.headline, "headline"); el.appendChild(hl.box);
        const sub = sc.sub ? el.appendChild(h("div", "sub", sc.sub)) : null;
        const t0 = sc.start + 0.1;
        const cStart = t0 + 0.3, cDur = 1.5;
        return t => {
          style(wrap, rise(t, t0, { dy: 0, s0: 0.7, blur: 20, d: 0.9, ease: E.outBack }));
          const p = prog(t, cStart, cDur, E.outCubic);
          const val = Number.isFinite(target) ? target * p : 0;
          const shown = Number.isFinite(target) ? (p >= 1 ? statStr : String(Math.floor(val))) : statStr;
          if (num.textContent !== shown) num.textContent = shown;
          // little punch each time the integer ticks + a big one when it lands
          const frac = val - Math.floor(val);
          const land = prog(t, cStart + cDur - 0.05, 0.5, E.linear);
          const punch = (1 - land) * (land > 0 ? 0.12 * Math.sin(land * Math.PI) : 0);
          style(num, { s: 1 + 0.03 * (1 - frac) * (p < 1 ? 1 : 0) + punch, o: 1 });
          num.style.filter = `drop-shadow(0 0 ${(30 + 50 * punch / 0.12).toFixed(1)}px ${rgba(B.primary, 0.45)})`;
          segs.forEach((s, i) => {
            const lit = clamp((val * n / (Number.isFinite(target) ? target : n)) - i);
            s.setAttribute("stroke", lit > 0.02 ? `url(#${gid})` : "rgba(255,255,255,.08)");
            s.setAttribute("opacity", lit > 0.02 ? (0.35 + 0.65 * lit).toFixed(3) : "1");
          });
          outer.setAttribute("transform", `rotate(${(t * 12).toFixed(2)} 280 280)`);
          animWords(hl.list, t, t0 + 0.7, 0.1);
          if (sub) style(sub, rise(t, t0 + 1.0, { dy: 50 }));
        };
      },

      list(sc, el) {
        const hl = words(sc.headline, "headline"); el.appendChild(hl.box);
        const box = el.appendChild(h("div", "items"));
        const items = sc.items || [];
        const rows = items.map((txt, i) => {
          const m = String(txt).match(/^((?:\p{Extended_Pictographic}|\p{Emoji_Presentation})(?:️|‍\p{Extended_Pictographic})*)\s*(.*)$/u);
          const row = h("div", "item");
          const emo = h("div", "emo", m ? m[1] : String(i + 1));
          const lab = h("div", "label", m ? m[2] : txt);
          if (isArabic(String(txt))) { lab.lang = "ar"; lab.dir = "rtl"; }
          row.append(emo, lab, h("div", "idx", String(i + 1).padStart(2, "0")));
          box.appendChild(row);
          return { row, emo };
        });
        const times = itemTimes(sc, items.map(x => String(x).replace(/^[^\p{L}\p{N}]+/u, "")));
        const t0 = sc.start + 0.1;
        return t => {
          animWords(hl.list, t, t0, 0.1);
          rows.forEach(({ row, emo }, i) => {
            const ti = times[i] - 0.15;
            const p = prog(t, ti, 0.6, E.outQuint);
            const end = i + 1 < times.length ? times[i + 1] - 0.15 : sc.speech_end + 0.1;
            const active = clamp((t - ti) / 0.15) * (1 - clamp((t - end) / 0.3));
            style(row, { x: 140 * (1 - p), o: clamp((t - ti) / 0.25), s: 1 + 0.025 * active, blur: 8 * (1 - clamp((t - ti) / 0.3)) });
            row.style.borderColor = active > 0.01 ? mix("#252b45", B.accent, active) : "rgba(255,255,255,.08)";
            row.style.background = active > 0.01 ? `color-mix(in srgb, ${mix(B.surface, mixHex(B.surface, B.accent, 0.25), active)} 92%, transparent)` : "";
            const ep = prog(t, ti + 0.1, 0.6, E.outBackBig);
            const wob = active * Math.sin((t - ti) * 9) * 6;
            style(emo, { s: lerp(0.3, 1, ep), r: lerp(-30, 0, ep) + wob, o: clamp((t - ti - 0.1) / 0.15) });
          });
        };
      },

      cta(sc, el) {
        const hl = words(sc.headline, "headline"); el.appendChild(hl.box);
        const sub = sc.sub ? el.appendChild(h("div", "sub", sc.sub)) : null;
        const pw = el.appendChild(h("div", "pill-wrap"));
        const rings = [pw.appendChild(h("div", "ring")), pw.appendChild(h("div", "ring"))];
        const pill = pw.appendChild(h("div", "pill"));
        const logo = pill.appendChild(h("div", "logo"));
        const lbars = logoInto(logo, 5);
        pill.appendChild(h("span", "", sc.button || B.name));
        const shine = pill.appendChild(h("div", "shine"));
        const url = sc.url ? el.appendChild(h("div", "cta-url", sc.url)) : null;
        const t0 = sc.start + 0.1;
        return t => {
          animWords(hl.list, t, t0, 0.1);
          if (sub) style(sub, rise(t, t0 + 0.4, { dy: 50 }));
          const pin = prog(t, t0 + 0.6, 0.8, E.outBackBig);
          const lt = Math.max(0, t - (t0 + 1.2));
          const pulse = 0.5 - 0.5 * Math.cos(lt * 2 * Math.PI * 1.1);
          style(pw, { s: lerp(0.3, 1, pin) * (1 + 0.04 * pulse), o: clamp((t - t0 - 0.6) / 0.2) });
          pill.style.boxShadow = `0 24px 60px rgba(0,0,0,.45), 0 0 ${(40 + 60 * pulse).toFixed(1)}px ${rgba(B.primary, (0.35 + 0.35 * pulse).toFixed(3))}`;
          rings.forEach((r, i) => {
            const ph = ((t - t0 - 1.0) / 1.3 + i * 0.5);
            if (ph < 0) { r.style.opacity = 0; return; }
            const f = ph % 1;
            style(r, { s: 1 + 0.35 * E.outCubic(f), o: 0.7 * (1 - f) });
            r.style.transform = `scale(${(1 + 0.18 * E.outCubic(f)).toFixed(4)}, ${(1 + 0.5 * E.outCubic(f)).toFixed(4)})`;
          });
          const sp = ((t - t0 - 1.0) / 1.6) % 1;
          shine.style.transform = `translateX(${lerp(-260, 900, E.inOutSine(clamp(sp / 0.6)))}px) skewX(-20deg)`;
          shine.style.opacity = t - t0 < 1.0 ? 0 : 1;
          if (url) style(url, rise(t, t0 + 1.1, { dy: 30 }));
          const a = audioAt(t);
          lbars.forEach((b, i) => {
            const v = a.bands.length ? a.bands[(i * 3) % a.bands.length] : 0;
            b.style.height = `${(14 + 30 * (0.35 + 0.65 * Math.max(v, 0.3 + 0.3 * Math.sin(t * 5 + i)))).toFixed(1)}px`;
          });
        };
      },

      // One screenshot / recording in a device frame, optional zoom into `focus` with a callout.
      screen(sc, el) {
        const kind = ["phone", "browser", "none"].includes(sc.device) ? sc.device : "phone";
        const txt = el.appendChild(h("div", "scr-text"));
        let kicker = null;
        if (sc.kicker) { kicker = h("div", "kicker"); kicker.appendChild(h("span", "dot")); kicker.appendChild(h("span", "", sc.kicker)); txt.appendChild(kicker); }
        const hl = sc.headline ? words(sc.headline, "headline") : null;
        if (hl) txt.appendChild(hl.box);
        const sub = sc.sub ? txt.appendChild(h("div", "sub", sc.sub)) : null;
        const hasText = !!(kicker || hl || sub);
        if (!hasText) txt.style.display = "none";

        const m = media(sc.asset, sc.start, sc.trim_start);
        const camwrap = el.appendChild(h("div", "camwrap"));
        const cam = camwrap.appendChild(h("div", "cam"));
        const callout = sc.callout ? el.appendChild(h("div", "callout")) : null;
        let calloutArrow = null;
        if (callout) { calloutArrow = callout.appendChild(h("i", "arrow")); callout.appendChild(h("span", "", sc.callout)); if (isArabic(sc.callout)) callout.dir = "rtl"; }
        const f = sc.focus && sc.focus.w > 0 && sc.focus.h > 0 ? {
          x: clamp(+sc.focus.x || 0), y: clamp(+sc.focus.y || 0),
          w: clamp(+sc.focus.w, 0.02, 1), h: clamp(+sc.focus.h, 0.02, 1) } : null;

        let G = null;  // geometry, computed on first update (needs fonts for text height)
        const RV = reserveFor(sc);
        const layout = () => {
          let box;
          const th = hasText ? txt.offsetHeight : 0;
          if (LAND) {
            if (hasText) {
              txt.style.top = px(140 + Math.max(0, (760 - th) / 2));
              box = { x: 770, y: 130, w: 1060, h: 790 };
            } else box = { x: 160, y: 130, w: 1600, h: 790 };
            if (RV) box.w = Math.min(box.w, RV.x - 30 - box.x);   // keep the device left of the bubble
          } else {
            const top = 330 + (hasText ? th + 40 : 20);
            box = { x: 70, y: top, w: 940, h: 1400 - top };
          }
          const g = deviceGeom(kind, m.aspect, box, kind === "phone" ? (LAND ? 520 : 600) : Infinity);
          if (hasText) {
            const mask = LAND ? `linear-gradient(to right, transparent ${px(box.x - 110)}, #000 ${px(box.x - 20)})`
                              : `linear-gradient(to bottom, transparent ${px(box.y - 90)}, #000 ${px(box.y - 12)})`;
            camwrap.style.webkitMaskImage = camwrap.style.maskImage = mask;
          }
          const { dev, screen } = deviceDom(g, sc.url);
          place(dev, g.x, g.y);
          cam.appendChild(dev);
          screen.appendChild(m.el);
          // media size inside the screen: fill width, natural height (tall shots scroll)
          const imgH = g.sw * m.aspect;
          const coverH = Math.max(imgH, g.sh);
          const mw = imgH < g.sh ? g.sh / m.aspect : g.sw;     // wider-than-screen images: fill height
          place(m.el, (g.sw - mw) / 2, 0, mw, coverH);
          const scrollMax = Math.max(0, imgH - g.sh);
          let scroll0 = 0, scroll1 = scrollMax;                 // no focus: slow scroll top -> bottom
          let ring = null, R = null;
          if (f) {
            const fy = f.y * coverH, fh = f.h * coverH;
            const off = clamp(fy + fh / 2 - g.sh / 2, 0, scrollMax);
            scroll0 = scroll1 = off;
            R = { x: (g.sw - mw) / 2 + f.x * mw, y: fy - off, w: f.w * mw, h: fh };
            ring = screen.appendChild(h("div", "focus-ring"));
            place(ring, R.x, R.y, R.w, R.h);
          }
          // focus in stage coordinates (before any camera move)
          const F = R ? { x: g.x + g.sx + R.x + R.w / 2, y: g.y + g.sy + R.y + R.h / 2, w: R.w, h: R.h } : null;
          const P = { x: box.x + box.w / 2, y: box.y + box.h / 2 };
          let kz = 1;
          if (F) {
            const maxW = LAND ? box.w * 0.82 : W * 0.86, maxH = LAND ? box.h * 0.62 : box.h * 0.55;
            kz = clamp(Math.min(maxW / F.w, maxH / F.h), 1, sc.zoom ? clamp(+sc.zoom, 1, 4) : (kind === "browser" ? 1.7 : 2.4));
            if (RV && !LAND) {
              // presenter bubble bottom-left: move the zoom target up so the focus never sits under it
              const lim = RV.y - 18;
              if (P.y + kz * F.h / 2 > lim) P.y = Math.max(box.y + kz * F.h / 2, lim - kz * F.h / 2);
              if (P.y + kz * F.h / 2 > lim) { kz = Math.max(1, (lim - box.y) / F.h); P.y = (box.y + lim) / 2; }
            }
          }
          G = { g, box, dev, screen, ring, R, F, P, kz, scroll0, scroll1, cw: callout ? callout.offsetWidth : 0, ch: callout ? callout.offsetHeight : 0 };
        };

        const D = sc.end - sc.start;
        const t0 = sc.start + 0.1;
        const zs = sc.start + clamp(D * 0.28, 0.8, 2.2), zd = clamp(D * 0.35, 0.9, 1.6);
        return t => {
          if (!G) layout();
          if (kicker) style(kicker, rise(t, t0, { dy: 40, s0: 0.8, d: 0.6, ease: E.outBack }));
          if (hl) animWords(hl.list, t, t0 + 0.1, 0.08, { dy: 60 });
          if (sub) style(sub, rise(t, t0 + 0.4, { dy: 40 }));
          // device entrance
          const ent = rise(t, t0 + 0.05, { dy: 160, s0: 0.9, blur: 10, d: 1.0 });
          style(G.dev, { ...ent, r: lerp(LAND ? -2 : 3, 0, prog(t, t0, 1.0)) });
          // scroll (no focus) or fixed offset
          const sp = prog(t, sc.start + 0.9, Math.max(0.5, D - 1.4), E.inOutSine);
          const scroll = lerp(G.scroll0, G.scroll1, sp);
          m.el.style.transform = `translate3d(0,${(-scroll).toFixed(2)}px,0)`;
          // camera
          let s = 1 + 0.035 * prog(t, sc.start, D, E.linear), tx = 0, ty = 0;
          const k = G.F ? prog(t, zs, zd, E.inOutCubic) : 0;
          if (G.F) {
            s = lerp(1, G.kz, k) * (1 + 0.025 * prog(t, zs + zd, Math.max(0.5, D), E.linear));
            const fx = lerp(G.F.x, G.P.x, k), fy = lerp(G.F.y, G.P.y, k);
            tx = fx - s * G.F.x; ty = fy - s * G.F.y;
          } else {
            const c = { x: G.g.x + G.g.w / 2, y: G.g.y + G.g.h / 2 };
            tx = c.x - s * c.x; ty = c.y - s * c.y;
          }
          cam.style.transform = `translate3d(${tx.toFixed(2)}px,${ty.toFixed(2)}px,0) scale(${s.toFixed(4)})`;
          if (G.ring) {
            const rp = prog(t, zs - 0.35, 0.6, E.outBack);
            const ro = clamp((t - zs + 0.35) / 0.25);
            const pulse = 0.5 + 0.5 * Math.sin((t - zs) * 4.5);
            G.ring.style.opacity = ro.toFixed(3);
            G.ring.style.borderWidth = px(Math.max(2, 5 / s));
            G.ring.style.borderRadius = px(16 / s);
            G.ring.style.transform = `scale(${lerp(1.25, 1, rp).toFixed(4)})`;
            G.ring.style.boxShadow = `0 0 0 4000px rgba(4,6,14,${(0.5 * ro).toFixed(3)}), 0 0 ${px((18 + 18 * pulse) / s)} ${rgba(B.accent, 0.75)}`;
          }
          if (callout && G.F) {
            // ring rectangle in stage coordinates after the camera move
            const rx = s * (G.F.x - G.F.w / 2) + tx, ry = s * (G.F.y - G.F.h / 2) + ty, rw = s * G.F.w, rh = s * G.F.h;
            const lim = LAND ? { x0: G.box.x, x1: G.box.x + G.box.w, y0: 120, y1: 900 } : { x0: 50, x1: W - 50, y0: 330, y1: RV ? RV.y - 14 : 1270 };
            let below = ry + rh + 34 + G.ch <= lim.y1;
            let cy = below ? ry + rh + 34 : ry - 34 - G.ch;
            cy = clamp(cy, lim.y0, lim.y1 - G.ch);
            const cx = clamp(rx + rw / 2 - G.cw / 2, lim.x0, lim.x1 - G.cw);
            const cp = prog(t, zs + zd * 0.75, 0.55, E.outBack);
            place(callout, cx, cy);
            callout.style.opacity = clamp((t - zs - zd * 0.75) / 0.15).toFixed(3);
            callout.style.transform = `translateY(${((below ? 30 : -30) * (1 - cp)).toFixed(1)}px) scale(${lerp(0.6, 1, cp).toFixed(4)})`;
            calloutArrow.className = below ? "arrow up" : "arrow down";
            calloutArrow.style.left = px(clamp(rx + rw / 2 - cx, 30, G.cw - 30) - 13);
          } else if (callout) {
            // no focus: callout sits under the device
            const cp = prog(t, sc.start + 1.0, 0.55, E.outBack);
            place(callout, W / 2 - G.cw / 2, Math.min(G.g.y + G.g.h * s - G.ch * 1.6, LAND ? 860 : (RV ? RV.y - 14 - G.ch : 1240)));
            callout.style.opacity = clamp((t - sc.start - 1.0) / 0.15).toFixed(3);
            callout.style.transform = `scale(${lerp(0.6, 1, cp).toFixed(4)})`;
            calloutArrow.style.display = "none";
          }
        };
      },

      // 2-3 screenshots side by side (phones) or cascaded (browser windows).
      screens(sc, el) {
        const kind = ["phone", "browser", "none"].includes(sc.device) ? sc.device : "phone";
        const txt = el.appendChild(h("div", "scr-text center"));
        const hl = sc.headline ? words(sc.headline, "headline") : null;
        if (hl) txt.appendChild(hl.box);
        const sub = sc.sub ? txt.appendChild(h("div", "sub", sc.sub)) : null;
        if (!hl && !sub) txt.style.display = "none";
        const items = (sc.items || []).filter(it => it && (it.asset || typeof it === "string")).slice(0, 3)
          .map(it => (typeof it === "string" ? { asset: it } : it));
        const ms = items.map(it => media(it.asset, sc.start, it.trim_start));
        const layer = el.appendChild(h("div", "cam"));
        const RV = reserveFor(sc);
        // with a presenter bubble the labels move into the device layer so they follow its fit transform
        const labels = items.map(it => (it.label ? (RV ? layer : el).appendChild(h("div", "dev-label", it.label)) : null));
        const times = itemTimes(sc, items.map(it => it.label || ""));
        let G = null;
        const layout = () => {
          const th = (hl || sub) ? txt.offsetHeight : 0;
          const top = (LAND ? 140 : 330) + (th ? th + (LAND ? 30 : 50) : 10);
          const bottom = LAND ? 930 : 1380;
          const n = Math.max(1, ms.length);
          const boxes = [];
          if (kind === "phone" || kind === "none") {
            const areaW = LAND ? 1640 : 980;
            const gap = LAND ? 70 : (n === 3 ? -40 : 40);
            const wEach = n === 3 && !LAND ? 380 : (areaW - gap * (n - 1)) / n;
            const hh = bottom - top - (labels.some(Boolean) ? 70 : 0);
            const geoms = ms.map(m => deviceGeom(kind, m.aspect, { x: 0, y: 0, w: wEach, h: n === 3 && !LAND ? hh * 0.9 : hh }, LAND ? 420 : 480));
            const total = geoms.reduce((a, g) => a + g.w, 0) + gap * (n - 1);
            let x = (W - total) / 2;
            geoms.forEach((g, i) => {
              const stag = !LAND && n === 2 ? (i ? 50 : -10) : (n === 3 && !LAND ? (i === 1 ? -40 : 30) : 0);
              boxes.push({ g, x, y: top + (hh - g.h) / 2 + stag, z: n === 3 && i === 1 ? 3 : 1, r: n === 3 ? (i - 1) * (LAND ? 0 : 5) : 0 });
              x += g.w + gap;
            });
          } else {
            // browser windows cascade diagonally, the last one in front
            const ww = LAND ? 1100 : 900;
            const dx = LAND ? 170 : 60, dy = LAND ? 70 : 150;
            const geoms = ms.map(m => deviceGeom(kind, Math.min(m.aspect, 0.62), { x: 0, y: 0, w: ww, h: bottom - top - dy * (n - 1) }));
            const totalW = geoms[0] ? geoms[0].w + dx * (n - 1) : 0;
            geoms.forEach((g, i) => boxes.push({ g, x: (W - totalW) / 2 + dx * i, y: top + dy * i, z: i + 1, r: 0 }));
          }
          const devs = boxes.map((b, i) => {
            const { dev, screen } = deviceDom(b.g, items[i].url || sc.url);
            place(dev, b.x, b.y);
            dev.style.zIndex = b.z;
            layer.appendChild(dev);
            const m = ms[i];
            screen.appendChild(m.el);
            const ih = b.g.sw * m.aspect;
            const mw = ih < b.g.sh ? b.g.sh / m.aspect : b.g.sw;
            place(m.el, (b.g.sw - mw) / 2, 0, mw, Math.max(ih, b.g.sh));
            if (labels[i]) {
              const L = labels[i];
              L.style.zIndex = 10;
              place(L, b.x + b.g.w / 2 - L.offsetWidth / 2, b.y + b.g.h * (kind === "browser" ? 0.5 : 0.72) - L.offsetHeight / 2);
            }
            return { dev, b };
          });
          if (RV && devs.length) {
            const c = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
            for (const { b } of devs) {
              c.x0 = Math.min(c.x0, b.x); c.y0 = Math.min(c.y0, b.y);
              c.x1 = Math.max(c.x1, b.x + b.g.w); c.y1 = Math.max(c.y1, b.y + b.g.h);
            }
            const f = fitAway(c, RV);
            if (f) layer.style.transform = `translate(${f.tx.toFixed(1)}px,${f.ty.toFixed(1)}px) scale(${f.k.toFixed(4)})`;
          }
          G = { devs };
        };
        const t0 = sc.start + 0.1;
        return t => {
          if (!G) layout();
          if (hl) animWords(hl.list, t, t0, 0.08, { dy: 60 });
          if (sub) style(sub, rise(t, t0 + 0.35, { dy: 40 }));
          G.devs.forEach(({ dev, b }, i) => {
            const ti = items.some(it => it.label) ? Math.min(times[i] - 0.25, t0 + 0.35 + i * 0.9) : t0 + 0.3 + i * 0.35;
            const p = prog(t, ti, 0.9, E.outBack);
            const o = clamp((t - ti) / 0.3);
            const bob = Math.sin((t - sc.start) * 1.3 + i * 1.7) * 8 * clamp((t - ti - 0.8) / 0.5);
            const curr = items.length > 1 ? clamp((t - ti) / 0.3) * (1 - clamp((t - (times[i + 1] ?? 1e9) + 0.1) / 0.4)) : 0;
            style(dev, { y: lerp(180, 0, p) + bob, s: lerp(0.85, 1, p) * (1 + 0.025 * curr), r: lerp(b.r * 3 + (i - 1) * 4, b.r, p), o, blur: 10 * (1 - o) });
            if (labels[i]) style(labels[i], rise(t, ti + 0.35, { dy: 24, s0: 0.8, ease: E.outBack, d: 0.5 }));
          });
        };
      },

      // Full-frame AI presenter (talking head) with a headline. The media lives in #pfull inside
      // #bg (under the brand header and the text); 16:9 shows it as a tall card on the right.
      presenter(sc, el) {
        const wrap = (pfull || el).appendChild(h("div", "pv-wrap"));
        const card = wrap.appendChild(h("div", "pv-card"));
        const media = card.appendChild(presenterMedia("pv-media"));
        const isLast = sc.end >= (TL.duration || sc.end) - 0.001;
        if (media.tagName === "VIDEO") pvids.push({ el: media, t0: sc.start - 0.3, t1: isLast ? 1e9 : sc.end + 0.1 });
        else if (media.classList.contains("ph")) { const r = media.appendChild(h("div", "ph-ring")); logoInto(r, 5); }
        wrap.appendChild(h("div", "pv-shade"));
        const txt = el.appendChild(h("div", "pv-text"));
        let kicker = null;
        if (sc.kicker) { kicker = h("div", "kicker"); kicker.appendChild(h("span", "dot")); kicker.appendChild(h("span", "", sc.kicker)); txt.appendChild(kicker); }
        const hl = sc.headline ? words(sc.headline, "headline") : null;
        if (hl) txt.appendChild(hl.box);
        const sub = sc.sub ? txt.appendChild(h("div", "sub", sc.sub)) : null;
        if (!kicker && !hl && !sub) txt.style.display = "none";
        const D = sc.end - sc.start;
        const t0 = sc.start + 0.1;
        const first = sc.start <= 0.001;
        const update = t => {
          wrap.style.display = "";
          const pin = first ? 1 : prog(t, sc.start - 0.3, 0.6, E.inOutCubic);
          const pout = prog(t, sc.end - 0.3, 0.4, E.inCubic);
          const kb = prog(t, sc.start, D, E.linear);
          if (LAND) {
            style(card, { y: 120 * (1 - prog(t, sc.start - 0.2, 0.9, E.outQuint)), s: lerp(0.94, 1, pin), o: pin * (1 - pout) });
            media.style.transform = `scale(${(1.02 + 0.05 * kb).toFixed(4)})`;
          } else {
            wrap.style.opacity = (pin * (1 - pout)).toFixed(4);
            media.style.transform = `scale(${(1.03 + 0.06 * kb).toFixed(4)})`;
          }
          if (kicker) style(kicker, rise(t, t0 + 0.2, { dy: 40, s0: 0.8, d: 0.6, ease: E.outBack }));
          if (hl) animWords(hl.list, t, t0 + 0.3, 0.09, { dy: 60 });
          if (sub) style(sub, rise(t, t0 + 0.65, { dy: 40 }));
        };
        update.hide = () => { wrap.style.display = "none"; };
        return update;
      },
    };

    // ---------- reset containers ----------
    const scenesEl = document.getElementById("scenes");
    const capsEl = document.getElementById("captions");
    const progEl = document.getElementById("progress");
    const pEl = document.getElementById("particles");
    scenesEl.innerHTML = ""; capsEl.innerHTML = ""; progEl.innerHTML = ""; pEl.innerHTML = "";

    // ---------- build scenes ----------
    const scenes = TL.scenes.map((sc0, idx) => {
      const sc = { ...sc0 };
      if (sc.speech_end == null) sc.speech_end = sc.end;
      const layout = builders[sc.layout] ? sc.layout : "hero";
      const el = h("div", `scene ${layout}`);
      el.dataset.id = sc.id || idx;
      scenesEl.appendChild(el);
      let update, fit = null;
      try {
        update = builders[layout](sc, el);
        const lines = { hero: 3, chips: 2, stat: 2, list: 2, cta: 3, screen: 2, screens: 2, presenter: 2 }[layout] || 2;
        el.querySelectorAll(".headline").forEach(hb => fitHeadline(hb, lines));
        const RV = reserveFor(sc);
        if (RV && !["screen", "screens", "presenter"].includes(layout)) {
          // centred layouts: scale/move the whole content box so the presenter bubble never covers it
          const c = { x0: 1e9, y0: 1e9, x1: -1e9, y1: -1e9 };
          for (const ch of el.children) {
            if (!ch.offsetWidth && !ch.offsetHeight) continue;
            const x = el.offsetLeft + ch.offsetLeft, y = el.offsetTop + ch.offsetTop;
            c.x0 = Math.min(c.x0, x); c.y0 = Math.min(c.y0, y);
            c.x1 = Math.max(c.x1, x + ch.offsetWidth); c.y1 = Math.max(c.y1, y + ch.offsetHeight);
          }
          const f = c.x1 > c.x0 ? fitAway(c, RV) : null;
          if (f) {
            // style() scales around the element centre E: X' = E + k(X - E) + d  =>  d = t - (1 - k) E
            const ex = el.offsetLeft + el.offsetWidth / 2, ey = el.offsetTop + el.offsetHeight / 2;
            fit = { k: f.k, dx: f.tx - (1 - f.k) * ex, dy: f.ty - (1 - f.k) * ey };
          }
        }
      }
      catch (e) { console.error(e); el.textContent = `scene ${sc.id}: ${e.message}`; update = () => {}; }
      return { sc, el, update, fit, last: idx === TL.scenes.length - 1 };
    });
    const OUT_LEAD = 0.3, OUT_TAIL = 0.1;    // exit transition window around scene end

    // ---------- brand header ----------
    const brand = document.getElementById("brand");
    brand.querySelector(".brand-name").textContent = B.name;
    brand.style.display = B.name || B.logo ? "" : "none";
    const hdrBars = logoInto(brand.querySelector(".logo"), 5);

    // ---------- background particles ----------
    const parts = Array.from({ length: 34 }, () => {
      const d = h("div", "particle");
      const size = 2 + rnd() * 5;
      d.style.width = d.style.height = `${size}px`;
      pEl.appendChild(d);
      return { d, x: rnd() * W, y: rnd() * H, sp: 18 + rnd() * 40, ph: rnd() * 6.28, o: 0.08 + rnd() * 0.25 };
    });

    // background presets per layout (designed for 1080x1920; scaled for other sizes)
    const BGP = {
      hero:    [[-200, 150], [500, 1100], [100, 800]],
      chips:   [[350, -100], [-300, 900], [400, 1000]],
      stat:    [[-100, 300], [400, 200], [-300, 1100]],
      list:    [[300, 1000], [-250, -100], [500, 400]],
      cta:     [[-100, 400], [200, 900], [300, -200]],
      screen:  [[200, 500], [-400, 1100], [500, -100]],
      screens: [[-300, 700], [450, 300], [0, 1200]],
    };
    const sx = W / 1080, sy = H / 1920;
    const blobs = ["blobA", "blobB", "blobC"].map(id => document.getElementById(id));
    const grid = document.getElementById("grid");

    function bgAt(t) {
      const pre = l => (BGP[l] || BGP.hero).map(p => [p[0] * sx, p[1] * sy]);
      const pos = pre(TL.scenes[0] ? TL.scenes[0].layout : "hero");
      for (let i = 1; i < TL.scenes.length; i++) {
        const sc = TL.scenes[i];
        const p = prog(t, sc.start - 0.6, 1.4, E.inOutCubic);
        if (p <= 0) break;
        const nxt = pre(sc.layout);
        for (let k = 0; k < 3; k++) { pos[k][0] = lerp(pos[k][0], nxt[k][0], p); pos[k][1] = lerp(pos[k][1], nxt[k][1], p); }
      }
      return pos;
    }

    // ---------- captions ----------
    const caps = TL.captions.map((c, i) => {
      const el = h("div", "cap");
      const eq = el.appendChild(h("div", "eq"));
      const eqb = [0, 1, 2, 3].map(() => eq.appendChild(h("i")));
      const txt = el.appendChild(h("div", "txt"));
      const ws = c.text.split(/\s+/).filter(Boolean);
      const total = ws.reduce((a, w) => a + w.length, 0) || 1;
      let acc = 0;
      if (isArabic(c.text)) txt.dir = "rtl";
      const wl = ws.map((w, k) => {
        const s = h("span", "w", w + (k < ws.length - 1 ? " " : ""));
        if (isArabic(w)) { s.lang = "ar"; s.dir = "rtl"; }
        txt.appendChild(s);
        const t0 = c.start + (acc / total) * (c.end - c.start);
        acc += w.length;
        return { s, t0, t1: c.start + (acc / total) * (c.end - c.start) };
      });
      capsEl.appendChild(el);
      const prev = TL.captions[i - 1], next = TL.captions[i + 1];
      return {
        c, el, eqb, wl,
        joinedPrev: !!prev && c.start - prev.end < 0.08,
        until: next && next.start - c.end < 0.08 ? next.start : c.end + 0.25,
        fadeOut: !(next && next.start - c.end < 0.08),
      };
    });
    capsEl.style.display = TL.captions_off ? "none" : "";

    // ---------- progress ----------
    const segs = TL.scenes.map(() => { const s = progEl.appendChild(h("div", "seg")); return s.appendChild(h("div", "fill")); });

    // ---------- recordings ----------
    function syncVideos(t) {
      const waits = [];
      for (const S of scenes) S.visible = t >= S.sc.start - 0.3 && (S.last ? true : t < S.sc.end + OUT_TAIL);
      for (const v of videos) {
        const S = scenes.find(S => S.sc.start === v.start);
        const vis = S ? S.visible : false;
        const dur = v.dur || (Number.isFinite(v.el.duration) ? v.el.duration : 0);
        const target = clamp(t - (v.start + 0.25) + v.trim, 0, Math.max(0, dur - 0.04));
        if (renderMode || !playing || !vis) {
          if (!v.el.paused) v.el.pause();
          if (!vis) continue;
          if (Math.abs(v.el.currentTime - target) > 0.0005 && v.el.readyState >= 1) {
            waits.push(new Promise(res => {
              const done = () => { v.el.removeEventListener("seeked", done); res(); };
              v.el.addEventListener("seeked", done);
              setTimeout(done, 3000);
              v.el.currentTime = target;
            }));
          }
        } else {
          if (target >= dur - 0.05) { if (!v.el.paused) v.el.pause(); continue; }
          if (v.el.paused) { v.el.currentTime = target; v.el.play().catch(() => {}); }
          else if (Math.abs(v.el.currentTime - target) > 0.25) v.el.currentTime = target;
        }
      }
      if (!waits.length) return null;
      // after seeking, give the compositor two frames to present the new picture
      return Promise.all(waits).then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    }

    // ---------- AI b-roll backgrounds ----------
    // Consecutive scenes with the same clip share one <video> (no restart at the cut). The clip is
    // slowed down (>= 0.6x) to cover the segment; if that is not enough it ping-pongs with eased
    // turnarounds, so there is never a jump. Dim + blur per scene, brand glow (blobs) on top.
    const bgSegs = [];
    const FADE = 0.35;
    TL.scenes.forEach((sc, i) => {
      const bg = sc.background;
      if (!bg || bg.type !== "broll" || !bg.clip) return;
      const prev = bgSegs[bgSegs.length - 1];
      if (prev && prev.clip === bg.clip && prev.endIdx === i - 1) { prev.end = sc.end; prev.endIdx = i; prev.parts.push(sc); }
      else bgSegs.push({ clip: bg.clip, meta: bg, start: sc.start, end: sc.end, endIdx: i, parts: [sc] });
    });
    if (bgSegs.length) {
      const holder = h("div");
      holder.id = "bgvideo";
      const bgEl = document.getElementById("bg");
      bgEl.insertBefore(holder, bgEl.firstChild);
      const base = TL.broll_base || "";
      for (const S of bgSegs) {
        S.first = S.start <= 0.001;
        S.last = S.endIdx === TL.scenes.length - 1;
        S.t0 = S.first ? S.start : S.start - FADE;
        S.t1 = S.last ? S.end : S.end + FADE;
        const D = Math.max(0.5, (S.meta.duration || 5) - 0.08);
        const need = S.t1 - S.t0;
        S.D = D;
        if (need <= D / 0.6) { S.rate = Math.min(1, D / need); S.loop = false; } else { S.rate = 0.8; S.loop = true; }
        S.el = holder.appendChild(h("div", "bgv"));
        S.v = S.el.appendChild(h("video", "bgv-video"));
        S.v.muted = true; S.v.playsInline = true; S.v.preload = "auto";
        S.v.src = base + encodeURIComponent(S.clip) + "/clip.webm";
        S.dim = S.el.appendChild(h("div", "bgv-dim"));
        S.lastDim = null; S.lastBlur = null;
      }
    }
    // eased ping-pong: linear in the middle, quadratic slow-down/speed-up at each turnaround
    function pingpong(u, D) {
      const k = u / D, n = Math.floor(k), f = k - n, w = 0.15, vm = 1 / (1 - w);
      const e = f < w ? vm * f * f / (2 * w) : f > 1 - w ? 1 - vm * (1 - f) * (1 - f) / (2 * w) : vm * (f - w / 2);
      return (n % 2 === 0 ? e : 1 - e) * D;
    }
    function clipTime(S, t) {
      const u = clamp(t - S.t0, 0, S.t1 - S.t0) * S.rate;
      return S.loop ? pingpong(u, S.D) : clamp(u, 0, S.D);
    }
    function bgOpacity(S, t) {
      const fin = S.first ? 1 : clamp((t - (S.start - FADE)) / (2 * FADE));
      const fout = S.last ? 1 : 1 - clamp((t - (S.end - FADE)) / (2 * FADE));
      return Math.min(fin, fout);
    }
    function bgParams(S, t) {
      // per-scene dim / blur, blended across the cut between two scenes of the same segment
      let d = S.parts[0].background.dim ?? 0.55, b = S.parts[0].background.blur ?? 0;
      for (let i = 1; i < S.parts.length; i++) {
        const p = clamp((t - (S.parts[i].start - FADE)) / (2 * FADE));
        if (p <= 0) break;
        d = lerp(d, S.parts[i].background.dim ?? 0.55, p); b = lerp(b, S.parts[i].background.blur ?? 0, p);
      }
      return { dim: clamp(d, 0, 0.92), blur: Math.max(0, b) };
    }
    const BGRGB = hex(B.background).join(",");
    function syncBg(t) {
      const waits = [];
      let maxO = 0;
      for (const S of bgSegs) {
        const o = bgOpacity(S, t);
        const vis = o > 0.001;
        S.el.style.display = vis ? "" : "none";
        if (!vis) { if (!S.v.paused) S.v.pause(); continue; }
        maxO = Math.max(maxO, o);
        S.el.style.opacity = o.toFixed(4);
        const { dim, blur } = bgParams(S, t);
        const dk = dim.toFixed(3);
        if (dk !== S.lastDim) {
          S.lastDim = dk;
          // darker at the top (brand header) and bottom (captions) so text stays readable
          const top = Math.min(0.96, dim + 0.22), bot = Math.min(0.96, dim + 0.3);
          S.dim.style.background = `linear-gradient(to bottom, rgba(${BGRGB},${top}) 0%, rgba(${BGRGB},${dim}) 24%, ` +
            `rgba(${BGRGB},${dim}) 62%, rgba(${BGRGB},${bot}) 100%)`;
        }
        const kb = prog(t, S.t0, Math.max(1, S.t1 - S.t0), E.linear);
        const sc = 1.03 + 0.05 * kb + (blur > 0 ? Math.min(0.08, blur * 0.006) : 0);
        S.v.style.transform = `scale(${sc.toFixed(4)})`;
        const bk = blur.toFixed(2);
        if (bk !== S.lastBlur) { S.lastBlur = bk; S.v.style.filter = blur > 0.05 ? `blur(${bk}px)` : "none"; }
        const target = clipTime(S, t);
        const v = S.v;
        if (renderMode || !playing) {
          if (!v.paused) v.pause();
          if (Math.abs(v.currentTime - target) > 0.0005 && v.readyState >= 1) {
            waits.push(new Promise(res => {
              const done = () => { v.removeEventListener("seeked", done); res(); };
              v.addEventListener("seeked", done);
              setTimeout(done, 3000);
              v.currentTime = target;
            }));
          }
        } else if (!S.loop) {
          if (Math.abs(v.playbackRate - S.rate) > 0.001) v.playbackRate = S.rate;
          if (v.paused) { v.currentTime = target; v.play().catch(() => {}); }
          else if (Math.abs(v.currentTime - target) > 0.3) v.currentTime = target;
        } else {
          if (!v.paused) v.pause();
          if (Math.abs(v.currentTime - target) > 0.05 && !v.seeking) v.currentTime = target;
        }
      }
      // keep the brand glow on top, a little softer over footage
      blobs[1].style.opacity = blobs[2].style.opacity = (1 - 0.35 * maxO).toFixed(3);
      if (!waits.length) return null;
      return Promise.all(waits).then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    }

    // ---------- AI presenter: corner bubble ----------
    let bubble = null;
    if (RB) {
      const runs = [];
      TL.scenes.forEach((sc, i) => {
        if (!pBubbleIds.has(sc.id)) return;
        const prev = runs[runs.length - 1];
        if (prev && prev.endIdx === i - 1) { prev.end = sc.end; prev.endIdx = i; }
        else runs.push({ start: sc.start, end: sc.end, endIdx: i });
      });
      runs.forEach(r => { r.last = r.endIdx === TL.scenes.length - 1; });
      const el = h("div");
      el.id = "pbubble";
      place(el, RB.x, RB.y, RB.w, RB.h);
      const ring = el.appendChild(h("div", "pb-ring"));
      const clip = el.appendChild(h("div", "pb-clip"));
      const v = clip.appendChild(presenterMedia("pb-media"));
      // frame the face: the portrait is head-and-shoulders, face centre at ~36% of the height
      const inner = RB.w - 2 * 9, zoom = 1.6;
      const vw = inner * zoom, vh = vw * ((PT.h || 800) / (PT.w || 448));
      place(v, (inner - vw) / 2, inner * 0.5 - vh * 0.36, vw, vh);
      stage.insertBefore(el, document.getElementById("captions"));
      bubble = { el, ring, v, runs };
      pvids.push({ el: v, windows: runs.map(r => [r.start - 0.4, r.last ? 1e9 : r.end + 0.1]) });
    }
    function bubbleAt(t, a) {
      if (!bubble) return;
      const r = bubble.runs.find(r => t >= r.start - 0.4 && (r.last || t < r.end + 0.1));
      if (!r) { bubble.el.style.display = "none"; return; }
      bubble.el.style.display = "";
      const tin = r.start <= 0.001 ? -0.4 : r.start + 0.15;
      const pin = prog(t, tin, 0.55, E.outBack);
      const pout = r.last ? 0 : prog(t, r.end - 0.3, 0.35, E.inCubic);
      style(bubble.el, { s: lerp(0.3, 1, pin) * (1 - 0.6 * pout), o: clamp((t - tin) / 0.2) * (1 - pout), y: 20 * (1 - pin) });
      bubble.ring.style.transform = `rotate(${(t * 40).toFixed(2)}deg)`;
      bubble.el.style.filter = `drop-shadow(0 0 ${(16 + 26 * a.rms).toFixed(1)}px ${rgba(B.accent, (0.25 + 0.35 * a.rms).toFixed(3))})`;
    }
    function syncPresenter(t) {
      const waits = [];
      for (const P of pvids) {
        const v = P.el;
        const vis = P.windows ? P.windows.some(([a, b]) => t >= a && t < b) : (t >= P.t0 && t < P.t1);
        const dur = Number.isFinite(v.duration) ? v.duration : (TL.duration || 0);
        const target = clamp(t, 0, Math.max(0, dur - 0.04));
        if (renderMode || !playing || !vis) {
          if (!v.paused) v.pause();
          if (!vis) continue;
          if (Math.abs(v.currentTime - target) > 0.0005 && v.readyState >= 1) {
            waits.push(new Promise(res => {
              const done = () => { v.removeEventListener("seeked", done); res(); };
              v.addEventListener("seeked", done);
              setTimeout(done, 3000);
              v.currentTime = target;
            }));
          }
        } else {
          if (target >= dur - 0.05) { if (!v.paused) v.pause(); continue; }
          if (v.paused) { v.currentTime = target; v.play().catch(() => {}); }
          else if (Math.abs(v.currentTime - target) > 0.2) v.currentTime = target;
        }
      }
      if (!waits.length) return null;
      return Promise.all(waits).then(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    }

    // ---------- the clock ----------
    function seek(t) {
      lastT = t;
      const pos = bgAt(t);
      blobs.forEach((b, k) => {
        const wob = 60;
        const x = pos[k][0] + wob * Math.sin(t * 0.45 + k * 2.1), y = pos[k][1] + wob * Math.cos(t * 0.38 + k * 1.3);
        const s = 1 + 0.06 * Math.sin(t * 0.7 + k);
        b.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0) scale(${s.toFixed(4)})`;
      });
      const a = audioAt(t);
      blobs[0].style.opacity = (0.85 + 0.15 * a.rms).toFixed(3);
      grid.style.backgroundPosition = `0px ${(t * 14).toFixed(2)}px`;
      for (const p of parts) {
        const y = ((p.y - t * p.sp) % H + H) % H;
        const x = p.x + 18 * Math.sin(t * 0.6 + p.ph);
        p.d.style.transform = `translate3d(${x.toFixed(1)}px,${y.toFixed(1)}px,0)`;
        p.d.style.opacity = (p.o * (0.6 + 0.4 * Math.sin(t * 1.3 + p.ph))).toFixed(3);
      }

      // brand header
      style(brand, rise(t, -0.2, { dy: -30, blur: 6, d: 0.6 }));
      hdrBars.forEach((b, i) => {
        const v = a.bands.length ? a.bands[(i * 4 + 1) % a.bands.length] : 0;
        const idle = 0.3 + 0.2 * Math.sin(t * 3.1 + i * 1.7);
        b.style.height = `${(8 + 26 * Math.max(v, idle)).toFixed(1)}px`;
      });

      const vwait = syncVideos(t);
      const bwait = bgSegs.length ? syncBg(t) : null;
      const pwait = pvids.length ? syncPresenter(t) : null;

      // scenes
      for (const S of scenes) {
        const { sc, el } = S;
        if (!S.visible) { el.style.display = "none"; if (S.update.hide) S.update.hide(); continue; }
        el.style.display = "";
        const F = S.fit;
        if (!S.last) {
          const p = prog(t, sc.end - OUT_LEAD, OUT_LEAD + OUT_TAIL, E.inCubic);
          style(el, F ? { x: F.dx, y: F.dy - 70 * p, s: F.k * (1 - 0.05 * p), o: 1 - p, blur: 14 * p }
                      : { y: -70 * p, s: 1 - 0.05 * p, o: 1 - p, blur: 14 * p });
        } else {
          style(el, F ? { x: F.dx, y: F.dy, s: F.k } : {});
        }
        try { S.update(t); } catch (e) { console.error(e); }
      }
      bubbleAt(t, a);

      // captions
      for (const C of caps) {
        const { c, el } = C;
        const on = t >= c.start && t < C.until;
        if (!on) { el.style.display = "none"; continue; }
        el.style.display = "flex";
        const pin = C.joinedPrev ? prog(t, c.start, 0.18, E.outBack) : prog(t, c.start, 0.35, E.outBack);
        const s0 = C.joinedPrev ? 0.95 : 0.8;
        let o = C.joinedPrev ? 1 : clamp((t - c.start) / 0.15);
        if (C.fadeOut) o *= 1 - clamp((t - c.end) / 0.25);
        const s = lerp(s0, 1, pin);
        el.style.opacity = o.toFixed(3);
        el.style.transform = `translate(-50%, -50%) translateY(${(C.joinedPrev ? 0 : 24 * (1 - pin)).toFixed(1)}px) scale(${s.toFixed(4)})`;
        C.eqb.forEach((b, i) => {
          const v = a.bands.length ? a.bands[(i * 5 + 2) % a.bands.length] : 0.25 + 0.25 * Math.sin(t * 7 + i * 2);
          b.style.height = `${(8 + 30 * Math.max(v, 0.08)).toFixed(1)}px`;
        });
        for (const Wd of C.wl) {
          const sp = clamp((t - Wd.t0) / 0.08);
          const speaking = t >= Wd.t0 && t < Wd.t1 + 0.02;
          Wd.s.style.color = speaking ? B.accent : B.text;
          Wd.s.style.opacity = (0.42 + 0.58 * sp).toFixed(3);
        }
      }

      // progress segments
      TL.scenes.forEach((sc, i) => {
        segs[i].style.width = `${(100 * clamp((t - sc.start) / Math.max(0.01, sc.end - sc.start))).toFixed(2)}%`;
      });
      if (bwait || pwait) return Promise.all([vwait, bwait, pwait]);
      return vwait || undefined;
    }

    function ready() {
      const imgs = [...stage.querySelectorAll("img")].map(img =>
        img.complete ? Promise.resolve() : new Promise(r => { img.addEventListener("load", r, { once: true }); img.addEventListener("error", r, { once: true }); }));
      const allv = [...videos.map(v => v.el), ...bgSegs.map(S => S.v), ...pvids.map(P => P.el)];
      const vids = allv.map(el => el.readyState >= 2 ? Promise.resolve() : new Promise(r => {
        el.addEventListener("loadeddata", r, { once: true }); el.addEventListener("error", r, { once: true });
      }));
      const all = Promise.all([...imgs, ...vids]);
      return Promise.race([all, new Promise(r => setTimeout(r, 20000))]);
    }

    return { seek, ready, W, H, duration: TL.duration || (TL.scenes.length ? TL.scenes[TL.scenes.length - 1].end : 0) };
  }
  // ======================================================================

  // ---------- fit the stage into the window (preview / embed) ----------
  function fit() {
    if (renderMode || !CUR) return;
    const k = Math.min(innerWidth / CUR.W, innerHeight / CUR.H);
    stage.style.transformOrigin = "0 0";
    stage.style.transform = `translate(${((innerWidth - CUR.W * k) / 2).toFixed(1)}px, ${((innerHeight - CUR.H * k) / 2).toFixed(1)}px) scale(${k})`;
  }
  addEventListener("resize", fit);

  const fontsReady = Promise.all([
    document.fonts.load('800 100px "Inter"'),
    document.fonts.load('500 100px "Inter"'),
    document.fonts.load('700 100px "Noto Sans Arabic"', "العربية"),
  ]).then(() => document.fonts.ready);

  window.seek = t => (CUR ? CUR.seek(t) : undefined);
  window.setPlaying = p => { playing = !!p; };
  window.loadTimeline = (tl, aud) => {
    window.__ready = false;
    return fontsReady.then(() => {
      CUR = mount(tl, aud || null);
      fit();
      CUR.seek(lastT);
      return CUR.ready();
    }).then(() => { CUR.seek(lastT); window.__ready = true; });
  };

  if (window.TIMELINE) window.loadTimeline(window.TIMELINE, window.AUDIO);

  // ---------- standalone live preview (open index.html in a browser, click to play) ----------
  if (!renderMode && !embed && window.TIMELINE) {
    const audio = new Audio(window.TIMELINE.audio_url || "../shared/build/voiceover.wav");
    document.addEventListener("click", () => { audio.currentTime = 0; playing = true; audio.play(); });
    const loop = () => { if (window.__ready) window.seek(audio.currentTime || 0); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  }
})();
