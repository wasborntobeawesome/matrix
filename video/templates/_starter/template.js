// Starter template.js — an ES module. Export any of: layouts, transitions, setup(ctx), frame(t, ctx).
// Rules: no timers, no Date.now(), no Math.random() for motion — every frame must be a pure function of
// the time the renderer gives you (that is what makes renders exact). See ../TEMPLATES.md.

// Replace the hero layout. Every other layout (chips, stat, list, cta, screen, screens, presenter)
// falls back to the built-in one, styled by template.css.
export const layouts = {
  hero: {
    // build(scene, ctx) -> Element: called once per scene. scene has the (localized) brief fields:
    // kicker, headline, sub, items, ... plus start / end / speech_end in seconds.
    build(scene, ctx) {
      const box = ctx.h("div", "st-hero");
      const A = ctx.area;                       // content area of this format (stage px)
      Object.assign(box.style, { left: `${A.x0}px`, top: `${A.y0}px`, width: `${A.x1 - A.x0}px`, height: `${A.y1 - A.y0}px` });
      box.appendChild(ctx.h("div", "st-ring"));
      const kicker = scene.kicker ? box.appendChild(ctx.h("div", "st-kicker", scene.kicker)) : null;
      const title = ctx.words(scene.headline, "headline");   // .headline is auto-fitted to 3 lines
      box.appendChild(title.box);
      const sub = scene.sub ? box.appendChild(ctx.h("div", "st-sub", scene.sub)) : null;
      box._ = { kicker, title, sub, ring: box.firstChild };   // keep references for seek()
      return box;
    },
    // seek(el, localT, ctx): draw the moment localT seconds after the scene start.
    seek(box, lt, ctx) {
      const { kicker, title, sub, ring } = box._;
      if (kicker) ctx.style(kicker, ctx.rise(lt, 0.1, { dy: 30 }));
      ctx.animWords(title.list, lt, 0.25, 0.1);               // words rise in one after another
      if (sub) ctx.style(sub, ctx.rise(lt, 0.7, { dy: 30 }));
      ring.style.transform = `rotate(${(lt * 12).toFixed(2)}deg) scale(${(0.9 + 0.1 * ctx.prog(lt, 0, 1.2)).toFixed(4)})`;
    },
  },
};

// A custom transition: the incoming scene rises from below while the outgoing one fades.
// p goes 0 -> 1 over [cut - lead, cut + tail]; return {x, y, s, r, o, blur, clip, flash}.
export const transitions = {
  rise: {
    name: "Rise", lead: 0.25, tail: 0.35,
    out: p => ({ o: 1 - p }),
    in: (p, c) => ({ y: c.H * 0.08 * (1 - p) * (1 - p), o: p }),
  },
};
