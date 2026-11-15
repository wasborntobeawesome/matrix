// Clean — light, minimal, Apple-like. A "full" template: template.css restyles everything, and this
// module replaces the hero, chips, stat, list and cta layouts (screen, screens and presenter use the
// built-in layouts, restyled by CSS). Every frame is a pure function of the scene's local time.

// content box per format (stage px): inside the safe zone, clear of the header and the captions
const BOX = { "9:16": [90, 336, 990, 1250], "1:1": [80, 112, 1000, 890], "16:9": [160, 150, 1760, 900] };

function wrap(ctx, cls) {
  const [x0, y0, x1, y1] = BOX[ctx.format];
  const w = ctx.h("div", `cl ${cls} f-${ctx.format.replace(":", "x")}`);
  Object.assign(w.style, { position: "absolute", left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
  return w;
}
// soft entrance: fade + small rise, no blur (local time lt, start t0)
function enter(ctx, el, lt, t0, { dy = 26, d = 0.75, s0 = 1 } = {}) {
  const p = ctx.prog(lt, t0, d, ctx.ease.outQuint);
  ctx.style(el, { y: dy * (1 - p), s: ctx.lerp(s0, 1, p), o: ctx.clamp((lt - t0) / (d * 0.55)) });
}
function words(ctx, list, lt, t0, gap = 0.06) {
  list.forEach((w, i) => enter(ctx, w, lt, t0 + i * gap, { dy: 34, d: 0.8 }));
}
const local = (ctx, abs) => abs - ctx.scene.start;
// app-icon tile with the brand logo (or animated bars) in the brand gradient
function icon(ctx, cls = "") {
  const tile = ctx.h("div", `cl-icon ${cls}`);
  const bars = ctx.logo(tile, 5);
  return { tile, bars };
}
function animBars(ctx, bars, lt) {
  const a = ctx.audioAt(ctx.scene.start + lt);
  bars.forEach((b, i) => {
    const v = a.bands.length ? a.bands[(i * 3 + 1) % a.bands.length] : 0;
    const idle = 0.35 + 0.25 * Math.sin((ctx.scene.start + lt) * 3.2 + i * 1.3);
    b.style.height = `${(10 + 34 * Math.max(v, idle)).toFixed(1)}px`;
  });
}

export const layouts = {
  hero: {
    build(sc, ctx) {
      const w = wrap(ctx, "cl-hero");
      const text = w.appendChild(ctx.h("div", "cl-text"));
      const ic = icon(ctx, "big");
      (ctx.land ? w : text).appendChild(ic.tile);
      if (ctx.land) w.appendChild(ctx.h("div", "cl-halo"));
      const kicker = sc.kicker ? text.appendChild(ctx.h("div", "cl-kicker", sc.kicker)) : null;
      const hl = ctx.words(sc.headline, "headline");
      hl.box.dataset.lines = "3";
      text.appendChild(hl.box);
      const sub = sc.sub ? text.appendChild(ctx.h("div", "cl-sub", sc.sub)) : null;
      const rule = text.appendChild(ctx.h("div", "cl-rule"));
      w._ = { ic, kicker, hl, sub, rule };
      return w;
    },
    seek(w, lt, ctx) {
      const { ic, kicker, hl, sub, rule } = w._;
      const t0 = ctx.scene.start === 0 ? -0.25 : 0.1;
      enter(ctx, ic.tile, lt, t0, { dy: 18, s0: 0.86, d: 0.9 });
      animBars(ctx, ic.bars, lt);
      if (kicker) enter(ctx, kicker, lt, t0 + 0.15);
      words(ctx, hl.list, lt, t0 + 0.25);
      if (sub) enter(ctx, sub, lt, t0 + 0.55);
      const p = ctx.prog(lt, t0 + 0.75, 1.1, ctx.ease.inOutCubic);
      rule.style.transform = `scaleX(${p.toFixed(4)})`;
    },
  },

  chips: {
    build(sc, ctx) {
      const w = wrap(ctx, "cl-chips");
      const hl = ctx.words(sc.headline, "headline");
      hl.box.dataset.lines = "2";
      w.appendChild(hl.box);
      const grid = w.appendChild(ctx.h("div", "cl-grid"));
      const items = sc.items || [];
      grid.classList.add(`n${Math.min(items.length, 6)}`);
      const cards = items.map(txt => {
        const c = grid.appendChild(ctx.h("div", "cl-chip"));
        const chk = c.appendChild(ctx.h("i", "cl-check"));
        const span = c.appendChild(ctx.h("span", "", txt));
        if (ctx.isArabic(txt)) { span.lang = "ar"; span.dir = "rtl"; }
        return { c, chk };
      });
      w._ = { hl, cards, times: ctx.itemTimes(sc, items).map(x => local(ctx, x)) };
      return w;
    },
    seek(w, lt, ctx) {
      const { hl, cards, times } = w._;
      words(ctx, hl.list, lt, 0.1);
      const end = local(ctx, ctx.scene.speech_end);
      cards.forEach(({ c, chk }, i) => {
        const ti = times[i] - 0.15;
        enter(ctx, c, lt, ti, { dy: 22, s0: 0.96, d: 0.6 });
        const on = ctx.clamp((lt - ti) / 0.2) * (1 - ctx.clamp((lt - (times[i + 1] ?? end) + 0.05) / 0.3));
        c.classList.toggle("on", on > 0.5);
        chk.style.transform = `scale(${(0.6 + 0.4 * ctx.prog(lt, ti + 0.15, 0.4, ctx.ease.outBack)).toFixed(4)})`;
        chk.classList.toggle("done", lt >= ti + 0.15);
      });
    },
  },

  stat: {
    build(sc, ctx) {
      const w = wrap(ctx, "cl-stat");
      const statStr = String(sc.stat ?? "");
      const m = statStr.match(/^([^\d]*)([\d.,]+)(.*)$/);
      const num = w.appendChild(ctx.h("div", "cl-num"));
      const pre = num.appendChild(ctx.h("span", "cl-unit", m ? m[1] : ""));
      const val = num.appendChild(ctx.h("span", "cl-val", m ? "0" : statStr));
      const suf = num.appendChild(ctx.h("span", "cl-unit", m ? m[3] : ""));
      if (statStr.length > 4) num.style.fontSize = `${Math.round((ctx.land ? 300 : ctx.square ? 230 : 300) * 4.2 / (statStr.length + 0.2))}px`;
      const side = w.appendChild(ctx.h("div", "cl-side"));
      const bar = side.appendChild(ctx.h("div", "cl-bar"));
      const fill = bar.appendChild(ctx.h("i"));
      const hl = ctx.words(sc.headline, "headline");
      hl.box.dataset.lines = "2";
      side.appendChild(hl.box);
      const sub = sc.sub ? side.appendChild(ctx.h("div", "cl-sub", sc.sub)) : null;
      const target = m ? parseFloat(m[2].replace(",", ".")) : NaN;
      const pct = m && /%/.test(m[3]) ? Math.min(1, target / 100) : 1;
      w._ = { num, val, pre, suf, fill, hl, sub, target, statStr, pct, digits: m ? m[2] : "" };
      return w;
    },
    seek(w, lt, ctx) {
      const { num, val, fill, hl, sub, target, pct, digits } = w._;
      enter(ctx, num, lt, 0.05, { dy: 30, s0: 0.97, d: 0.9 });
      const p = ctx.prog(lt, 0.35, 1.5, ctx.ease.outCubic);
      if (Number.isFinite(target)) {
        const shown = p >= 1 ? digits : String(Math.floor(target * p));
        if (val.textContent !== shown) val.textContent = shown;
      }
      fill.style.transform = `scaleX(${(pct * p).toFixed(4)})`;
      words(ctx, hl.list, lt, 0.75);
      if (sub) enter(ctx, sub, lt, 1.0);
    },
  },

  list: {
    build(sc, ctx) {
      const w = wrap(ctx, "cl-list");
      const hl = ctx.words(sc.headline, "headline");
      hl.box.dataset.lines = "2";
      w.appendChild(hl.box);
      const card = w.appendChild(ctx.h("div", "cl-card"));
      const items = sc.items || [];
      const rows = items.map((txt, i) => {
        const m = String(txt).match(/^((?:\p{Extended_Pictographic}|\p{Emoji_Presentation})(?:️|‍\p{Extended_Pictographic})*)\s*(.*)$/u);
        const row = card.appendChild(ctx.h("div", "cl-row"));
        row.appendChild(ctx.h("div", "cl-emo", m ? m[1] : String(i + 1)));
        const lab = row.appendChild(ctx.h("div", "cl-label", m ? m[2] : txt));
        if (ctx.isArabic(String(txt))) { lab.lang = "ar"; lab.dir = "rtl"; }
        row.appendChild(ctx.h("div", "cl-idx", String(i + 1).padStart(2, "0")));
        return row;
      });
      const times = ctx.itemTimes(sc, items.map(x => String(x).replace(/^[^\p{L}\p{N}]+/u, ""))).map(x => local(ctx, x));
      w._ = { hl, card, rows, times };
      return w;
    },
    seek(w, lt, ctx) {
      const { hl, card, rows, times } = w._;
      words(ctx, hl.list, lt, 0.1);
      enter(ctx, card, lt, 0.3, { dy: 30, d: 0.8 });
      const end = local(ctx, ctx.scene.speech_end);
      rows.forEach((row, i) => {
        const ti = times[i] - 0.2;
        const p = ctx.prog(lt, ti, 0.6, ctx.ease.outQuint);
        ctx.style(row, { x: (ctx.rtl ? -1 : 1) * 26 * (1 - p), o: 0.22 + 0.78 * ctx.clamp((lt - ti) / 0.35) });
        const nx = i + 1 < times.length ? times[i + 1] - 0.2 : end + 0.1;
        row.classList.toggle("on", lt >= ti + 0.1 && lt < nx);
      });
    },
  },

  cta: {
    build(sc, ctx) {
      const w = wrap(ctx, "cl-cta");
      const ic = icon(ctx, "mid");
      w.appendChild(ic.tile);
      const hl = ctx.words(sc.headline, "headline");
      hl.box.dataset.lines = "2";
      w.appendChild(hl.box);
      const sub = sc.sub ? w.appendChild(ctx.h("div", "cl-sub", sc.sub)) : null;
      const btn = w.appendChild(ctx.h("div", "cl-btn"));
      btn.appendChild(ctx.h("span", "", sc.button || ctx.brand.name));
      btn.appendChild(ctx.h("i", "cl-arrow", ctx.rtl ? "‹" : "›"));
      const url = sc.url ? w.appendChild(ctx.h("div", "cl-url", sc.url)) : null;
      w._ = { ic, hl, sub, btn, url };
      return w;
    },
    seek(w, lt, ctx) {
      const { ic, hl, sub, btn, url } = w._;
      enter(ctx, ic.tile, lt, 0.05, { dy: 16, s0: 0.86, d: 0.9 });
      animBars(ctx, ic.bars, lt);
      words(ctx, hl.list, lt, 0.2);
      if (sub) enter(ctx, sub, lt, 0.5);
      const pin = ctx.prog(lt, 0.7, 0.8, ctx.ease.outBack);
      const breathe = lt > 1.5 ? 0.012 * Math.sin((lt - 1.5) * 2.6) : 0;
      ctx.style(btn, { s: ctx.lerp(0.85, 1, pin) + breathe, o: ctx.clamp((lt - 0.7) / 0.3) });
      if (url) enter(ctx, url, lt, 1.0, { dy: 14 });
    },
  },
};

// a soft cross-fade with a little vertical drift (the default for this template)
const sine = p => -(Math.cos(Math.PI * p) - 1) / 2;
export const transitions = {
  drift: {
    name: "Drift", lead: 0.32, tail: 0.4,
    out: p => { const k = sine(p); return { o: 1 - k, y: -26 * k, s: 1 - 0.012 * k }; },
    in: p => { const k = sine(p); return { o: k, y: 34 * (1 - k), s: 1.012 - 0.012 * k }; },
  },
};
