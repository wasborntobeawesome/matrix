// Bold — high contrast, very large type, punchy. A "full" template: replaces hero, chips, stat, list and
// cta, draws moving colour bands + an outlined brand-name watermark behind everything (setup/frame),
// and adds two transitions (punch, swipe). Every frame is a pure function of t.

const BOX = { "9:16": [80, 330, 1000, 1260], "1:1": [70, 108, 1010, 895], "16:9": [150, 140, 1770, 905] };

function wrap(ctx, cls) {
  const [x0, y0, x1, y1] = BOX[ctx.format];
  const w = ctx.h("div", `bd ${cls}`);
  Object.assign(w.style, { position: "absolute", left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
  return w;
}
const local = (ctx, abs) => abs - ctx.scene.start;
const upper = ctx => (ctx.options.case || "upper") === "upper";

// Stacked headline: one line per word (two short words share a line), sized so the longest line fits
// the box width and the whole stack fits `maxH`. Returns {box, lines: [{el, words}]}.
function stack(ctx, text, parent, { maxW, maxH, max = 230, min = 54, lines: maxLines = 4 } = {}) {
  const box = parent.appendChild(ctx.h("div", "headline bd-stack"));
  box.dataset.fit = "off";                       // sized here, not by the renderer's generic fit
  if (ctx.isArabic(text || "")) box.dir = "rtl";
  const ws = String(text || "").split(/\s+/).filter(Boolean);
  // split the words into n balanced lines (by characters)
  const split = n => {
    const total = ws.reduce((a, w) => a + w.length, 0) + ws.length - 1;
    const out = [[]];
    let acc = 0;
    ws.forEach((w, i) => {
      const cur = out[out.length - 1];
      const left = ws.length - i;
      if (cur.length && out.length < n && (acc + w.length / 2 > (total / n) * out.length || left <= n - out.length)) out.push([w]);
      else out[out.length - 1].push(w);
      acc += w.length + 1;
    });
    return out;
  };
  const build = groups => {
    box.textContent = "";
    return groups.map(g => {
      const el = box.appendChild(ctx.h("div", "bd-line"));
      const words = g.map((w, i) => {
        if (i) el.appendChild(document.createTextNode(" "));
        const s = el.appendChild(ctx.h("span", "bd-w", w));
        const bare = w.replace(/[^\p{L}\p{N}]/gu, "").toLowerCase();
        if ((ctx.brand.name && bare.startsWith(ctx.brand.name.toLowerCase())) || /\d/.test(w)) s.classList.add("mark");
        return s;
      });
      return { el, words };
    });
  };
  // try 1..maxLines lines and keep the one that allows the biggest type
  box.style.fontSize = "100px";
  let best = null;
  for (let n = 1; n <= Math.min(maxLines, ws.length || 1); n++) {
    const groups = split(n);
    const lines = build(groups);
    const widest = Math.max(1, ...lines.map(l => l.el.scrollWidth));
    const lh = box.offsetHeight / 100;
    const fs = Math.min(max, (maxW / widest) * 100 * 0.97, maxH / Math.max(1e-3, lh));
    if (!best || fs > best.fs + 2) best = { fs, groups };
  }
  const lines = build(best ? best.groups : [ws]);
  box.style.fontSize = `${Math.max(min, best ? best.fs : min).toFixed(1)}px`;
  return { box, lines };
}
// shrink a one-line label until it fits its box
function shrink(el, box, minPx = 24) {
  let fs = parseFloat(getComputedStyle(el).fontSize);
  for (let i = 0; i < 16 && el.scrollWidth > box.clientWidth - 36 && fs > minPx; i++) { fs *= 0.92; el.style.fontSize = `${fs.toFixed(1)}px`; }
}
function slam(ctx, lines, lt, t0, gap = 0.1) {
  let k = 0;
  for (const l of lines) for (const w of l.words) {
    const ti = t0 + k++ * gap;
    const p = ctx.prog(lt, ti, 0.42, ctx.ease.outExpo);
    const o = ctx.clamp((lt - ti) / 0.06);
    ctx.style(w, { s: ctx.lerp(1.9, 1, p), r: ctx.lerp(-6, 0, p), o, y: ctx.lerp(-30, 0, p) });
  }
}
function pop(ctx, el, lt, ti, { s0 = 0.3, r0 = 0, d = 0.5 } = {}) {
  const p = ctx.prog(lt, ti, d, ctx.ease.outBackBig || ctx.ease.outBack);
  ctx.style(el, { s: ctx.lerp(s0, 1, p), r: ctx.lerp(r0 - 8, r0, p), o: ctx.clamp((lt - ti) / 0.08) });
}

export function setup(ctx) {
  const L = ctx.layers.bg;
  if (!L) return;
  const mark = L.appendChild(ctx.h("div", "bd-mark"));
  const name = (ctx.brand.name || "").trim() || "•";
  for (let i = 0; i < 3; i++) mark.appendChild(ctx.h("div", "bd-mark-row", `${name} ${name} ${name} ${name} ${name} ${name}`));
  ctx._bd = { mark, bands: [] };
  if (ctx.options.stripes !== false) {
    ctx._bd.bands = [L.appendChild(ctx.h("div", "bd-band a")), L.appendChild(ctx.h("div", "bd-band b"))];
  }
}

// band positions per scene (fractions of the stage height), blended across cuts
const SPOTS = [[0.16, 0.86], [0.78, 0.1], [0.3, 0.92], [0.9, 0.22], [0.12, 0.66], [0.62, 0.04]];
export function frame(t, ctx) {
  const B = ctx._bd;
  if (!B) return;
  const H = ctx.H, W = ctx.W;
  let pos = SPOTS[0].slice();
  ctx.scenes.forEach((s, i) => {
    if (!i) return;
    const p = ctx.prog(t, s.start - 0.18, 0.5, ctx.ease.outExpo);
    if (p <= 0) return;
    const nx = SPOTS[i % SPOTS.length];
    pos = pos.map((v, k) => ctx.lerp(v, nx[k], p));
  });
  B.bands.forEach((b, k) => {
    const y = pos[k] * H + 26 * Math.sin(t * 0.7 + k * 2);
    const x = -0.25 * W + (k ? -1 : 1) * ((t * 40) % 200);
    b.style.transform = `translate3d(${x.toFixed(1)}px, ${(y - 0.5 * b.offsetHeight).toFixed(1)}px, 0) rotate(-12deg)`;
  });
  [...B.mark.children].forEach((row, i) => {
    const dir = i % 2 ? 1 : -1;
    const span = row.scrollWidth / 2 || 1000;
    row.style.transform = `translate3d(${(dir * ((t * 55) % span) - (dir > 0 ? span : 0)).toFixed(1)}px, 0, 0)`;
  });
}

export const layouts = {
  hero: {
    build(sc, ctx) {
      const w = wrap(ctx, "bd-hero");
      ctx.el.appendChild(w);                       // in the DOM now, so the stack can be measured
      const kicker = sc.kicker ? w.appendChild(ctx.h("div", "bd-tag", sc.kicker)) : null;
      const sub = sc.sub ? ctx.h("div", "bd-sub", sc.sub) : null;
      const [x0, y0, x1, y1] = BOX[ctx.format];
      const st = stack(ctx, sc.headline, w, { maxW: x1 - x0, maxH: (y1 - y0) * (ctx.land ? 0.62 : 0.66), max: ctx.land ? 220 : ctx.square ? 190 : 240 });
      if (sub) w.appendChild(sub);
      w._ = { kicker, st, sub };
      return w;
    },
    seek(w, lt, ctx) {
      const { kicker, st, sub } = w._;
      const t0 = ctx.scene.start === 0 ? -0.2 : 0.08;
      if (kicker) pop(ctx, kicker, lt, t0, { s0: 0.5, r0: -2.5 });
      slam(ctx, st.lines, lt, t0 + 0.12, 0.11);
      if (sub) { const p = ctx.prog(lt, t0 + 0.7, 0.5, ctx.ease.outExpo); ctx.style(sub, { x: (ctx.rtl ? 1 : -1) * 60 * (1 - p), o: ctx.clamp((lt - t0 - 0.7) / 0.1) }); }
    },
  },

  chips: {
    build(sc, ctx) {
      const w = wrap(ctx, "bd-chips");
      ctx.el.appendChild(w);
      const [x0, y0, x1, y1] = BOX[ctx.format];
      const st = stack(ctx, sc.headline, w, { maxW: x1 - x0, maxH: (y1 - y0) * (ctx.land ? 0.4 : 0.36), max: ctx.land ? 170 : 200, lines: 2 });
      const grid = w.appendChild(ctx.h("div", "bd-grid"));
      const items = sc.items || [];
      grid.classList.add(`n${items.length}`);
      const chips = items.map((txt, i) => {
        const c = grid.appendChild(ctx.h("div", `bd-chip c${i % 3}`));
        const s = c.appendChild(ctx.h("span", "", txt));
        if (ctx.isArabic(txt)) { s.lang = "ar"; s.dir = "rtl"; }
        return c;
      });
      chips.forEach(c => shrink(c.firstChild, c));
      w._ = { st, chips, times: ctx.itemTimes(sc, items).map(x => local(ctx, x)) };
      return w;
    },
    seek(w, lt, ctx) {
      const { st, chips, times } = w._;
      slam(ctx, st.lines, lt, 0.05, 0.1);
      chips.forEach((c, i) => pop(ctx, c, lt, times[i] - 0.12, { s0: 0.2, r0: (i % 2 ? 2.5 : -2.5) }));
    },
  },

  stat: {
    build(sc, ctx) {
      const w = wrap(ctx, "bd-stat");
      ctx.el.appendChild(w);
      const statStr = String(sc.stat ?? "");
      const m = statStr.match(/^([^\d]*)([\d.,]+)(.*)$/);
      const num = w.appendChild(ctx.h("div", "bd-num"));
      const ghost = num.appendChild(ctx.h("span", "bd-ghost", statStr));
      const fill = num.appendChild(ctx.h("span", "bd-fill", statStr));
      const [x0, y0, x1, y1] = BOX[ctx.format];
      num.style.fontSize = "100px";
      const k = Math.min((ctx.land ? 0.48 : 0.96) * (x1 - x0) / Math.max(1, ghost.offsetWidth), (ctx.land ? 0.58 : 0.4) * (y1 - y0) / 100);
      num.style.fontSize = `${Math.min(ctx.land ? 520 : ctx.square ? 380 : 560, 100 * k).toFixed(0)}px`;
      const side = w.appendChild(ctx.h("div", "bd-side"));
      const hl = side.appendChild(ctx.h("div", "headline bd-marker", sc.headline || ""));
      hl.dataset.lines = "2";
      const sub = sc.sub ? side.appendChild(ctx.h("div", "bd-sub", sc.sub)) : null;
      w._ = { num, fill, hl, sub, m, statStr, target: m ? parseFloat(m[2].replace(",", ".")) : NaN };
      return w;
    },
    seek(w, lt, ctx) {
      const { num, fill, hl, sub, m, statStr, target } = w._;
      pop(ctx, num, lt, 0.05, { s0: 0.6 });
      const p = ctx.prog(lt, 0.3, 1.2, ctx.ease.outCubic);
      const txt = Number.isFinite(target) && p < 1 ? `${m[1]}${Math.floor(target * p)}${m[3]}` : statStr;
      if (fill.textContent !== txt) { fill.textContent = txt; fill.previousSibling.textContent = txt; }
      fill.style.clipPath = `inset(${(100 * (1 - p)).toFixed(2)}% 0 0 0)`;
      const ph = ctx.prog(lt, 0.9, 0.45, ctx.ease.outExpo);
      ctx.style(hl, { x: (ctx.rtl ? 1 : -1) * 80 * (1 - ph), o: ctx.clamp((lt - 0.9) / 0.08), r: -1.5 });
      if (sub) ctx.style(sub, { y: 20 * (1 - ctx.prog(lt, 1.2, 0.4, ctx.ease.outExpo)), o: ctx.clamp((lt - 1.2) / 0.1) });
    },
  },

  list: {
    build(sc, ctx) {
      const w = wrap(ctx, "bd-list");
      ctx.el.appendChild(w);
      const [x0, y0, x1, y1] = BOX[ctx.format];
      const st = stack(ctx, sc.headline, w, { maxW: ctx.land ? (x1 - x0) * 0.42 : x1 - x0, maxH: (y1 - y0) * (ctx.land ? 0.8 : 0.34), max: ctx.land ? 160 : 170, lines: ctx.land ? 4 : 2 });
      const box = w.appendChild(ctx.h("div", "bd-rows"));
      const items = sc.items || [];
      const rows = items.map((txt, i) => {
        const m = String(txt).match(/^((?:\p{Extended_Pictographic}|\p{Emoji_Presentation})(?:️|‍\p{Extended_Pictographic})*)\s*(.*)$/u);
        const row = box.appendChild(ctx.h("div", "bd-row"));
        row.appendChild(ctx.h("div", "bd-n", String(i + 1)));
        const lab = row.appendChild(ctx.h("div", "bd-label", m ? m[2] : txt));
        if (m) row.appendChild(ctx.h("div", "bd-emo", m[1]));
        if (ctx.isArabic(String(txt))) { lab.lang = "ar"; lab.dir = "rtl"; }
        return row;
      });
      w._ = { st, rows, times: ctx.itemTimes(sc, items.map(x => String(x).replace(/^[^\p{L}\p{N}]+/u, ""))).map(x => local(ctx, x)) };
      return w;
    },
    seek(w, lt, ctx) {
      const { st, rows, times } = w._;
      slam(ctx, st.lines, lt, 0.05, 0.09);
      const end = local(ctx, ctx.scene.speech_end);
      rows.forEach((row, i) => {
        const ti = times[i] - 0.18;
        const p = ctx.prog(lt, ti, 0.45, ctx.ease.outExpo);
        ctx.style(row, { x: (ctx.rtl ? -1 : 1) * 260 * (1 - p), o: ctx.clamp((lt - ti) / 0.08) });
        const nx = i + 1 < times.length ? times[i + 1] - 0.18 : end + 0.1;
        row.classList.toggle("on", lt >= ti && lt < nx);
      });
    },
  },

  cta: {
    build(sc, ctx) {
      const w = wrap(ctx, "bd-cta");
      ctx.el.appendChild(w);
      const [x0, y0, x1, y1] = BOX[ctx.format];
      const st = stack(ctx, sc.headline, w, { maxW: x1 - x0, maxH: (y1 - y0) * (ctx.land ? 0.5 : 0.48), max: ctx.land ? 190 : 210, lines: 3 });
      const sub = sc.sub ? w.appendChild(ctx.h("div", "bd-sub", sc.sub)) : null;
      const btn = w.appendChild(ctx.h("div", "bd-btn"));
      btn.appendChild(ctx.h("span", "", sc.button || ctx.brand.name));
      btn.appendChild(ctx.h("b", "", ctx.rtl ? "←" : "→"));
      const url = sc.url ? w.appendChild(ctx.h("div", "bd-url", sc.url)) : null;
      w._ = { st, sub, btn, url };
      return w;
    },
    seek(w, lt, ctx) {
      const { st, sub, btn, url } = w._;
      slam(ctx, st.lines, lt, 0.05, 0.1);
      if (sub) ctx.style(sub, { o: ctx.clamp((lt - 0.55) / 0.1), y: 20 * (1 - ctx.prog(lt, 0.55, 0.4, ctx.ease.outExpo)) });
      const pin = ctx.prog(lt, 0.75, 0.55, ctx.ease.outBackBig || ctx.ease.outBack);
      const beat = lt > 1.4 ? Math.pow(Math.max(0, Math.sin((lt - 1.4) * Math.PI * 1.6)), 8) : 0;
      ctx.style(btn, { s: ctx.lerp(0.2, 1, pin) * (1 + 0.06 * beat), r: ctx.lerp(-10, -2, pin), o: ctx.clamp((lt - 0.75) / 0.08) });
      const arrow = btn.lastChild;
      arrow.style.transform = `translateX(${((ctx.rtl ? -1 : 1) * 14 * beat).toFixed(1)}px)`;
      if (url) ctx.style(url, { o: ctx.clamp((lt - 1.1) / 0.1) });
    },
  },
};

// transitions: a hard punch-in with a flash, and a fast tilted swipe
const exp = p => (p >= 1 ? 1 : 1 - Math.pow(2, -10 * p));
const cub = p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
export const transitions = {
  punch: {
    name: "Punch", lead: 0.14, tail: 0.32,
    // the cut is at p0 = lead / (lead + tail)
    out: p => { const p0 = 0.14 / 0.46; return p < p0 ? { s: 1 + 0.1 * (p / p0) * (p / p0) } : { o: 0 }; },
    in: p => {
      const p0 = 0.14 / 0.46;
      if (p < p0) return { o: 0 };
      const k = (p - p0) / (1 - p0);
      return { s: 1.28 - 0.28 * exp(k), flash: 0.55 * Math.pow(1 - k, 3) };
    },
  },
  swipe: {
    name: "Swipe", lead: 0.22, tail: 0.24,
    out: (p, c) => { const k = cub(p); return { x: -(c.rtl ? -1 : 1) * c.W * 1.1 * k, r: -4 * k, blur: 14 * Math.sin(Math.PI * k) }; },
    in: (p, c) => { const k = cub(p); return { x: (c.rtl ? -1 : 1) * c.W * 1.1 * (1 - k), r: 4 * (1 - k), blur: 14 * Math.sin(Math.PI * k) }; },
  },
};
