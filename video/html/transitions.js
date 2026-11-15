/* Scene transitions — shared by the renderer (index.html) and the studio UI (tiny previews).
 *
 * A transition plays at the cut between two scenes. Its window is [cut - lead, cut + tail] seconds:
 *   out(p, c) -> state of the outgoing scene, p 0 -> 1 over the window
 *   in(p, c)  -> state of the incoming scene, p 0 -> 1 over the window
 * The outgoing scene stays visible until cut + tail, the incoming one from cut - lead.
 * A state is {x, y (px), s (scale), r (deg), o (opacity), blur (px), clip: [top, right, bottom, left]
 * as fractions 0..1 (CSS inset), flash: 0..1 (a full-frame flash in --tx-flash)}; missing keys = neutral.
 * c = {W, H, rtl}: stage size and direction, so slides move the right way in Arabic.
 * Everything is a pure function of p: no timers, no randomness.
 */
(() => {
  "use strict";
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const E = {
    linear: p => p,
    inCubic: p => p * p * p,
    outCubic: p => 1 - Math.pow(1 - p, 3),
    inOutCubic: p => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    inOutSine: p => -(Math.cos(Math.PI * p) - 1) / 2,
    outQuint: p => 1 - Math.pow(1 - p, 5),
  };
  const T = {
    // the original look: the outgoing scene lifts away and blurs; the layouts' own entrances bring the next one in
    lift: {
      name: "Lift", lead: 0.3, tail: 0.1,
      out: p => { p = E.inCubic(p); return { y: -70 * p, s: 1 - 0.05 * p, o: 1 - p, blur: 14 * p }; },
      in: () => ({}),
    },
    fade: {
      name: "Fade", lead: 0.3, tail: 0.3,
      out: p => ({ o: 1 - E.inOutSine(p) }),
      in: p => ({ o: E.inOutSine(p) }),
    },
    slide: {
      name: "Slide", lead: 0.35, tail: 0.35,
      out: (p, c) => { const k = E.inOutCubic(p); return { x: -(c.rtl ? -1 : 1) * c.W * 0.6 * k, o: 1 - clamp(k * 1.6 - 0.6) }; },
      in: (p, c) => { const k = E.inOutCubic(p); return { x: (c.rtl ? -1 : 1) * c.W * 0.6 * (1 - k), o: clamp(k * 1.6) }; },
    },
    zoom: {
      name: "Zoom", lead: 0.3, tail: 0.35,
      out: p => { const k = E.inCubic(p); return { s: 1 + 0.35 * k, o: 1 - k, blur: 8 * k }; },
      in: p => { const k = E.outCubic(p); return { s: 0.82 + 0.18 * k, o: clamp(p * 1.8) }; },
    },
    wipe: {
      name: "Wipe", lead: 0.25, tail: 0.35,
      out: p => ({ o: p >= 1 ? 0 : 1 }),
      in: (p, c) => { const k = E.inOutCubic(p); return c.rtl ? { clip: [0, 0, 0, 1 - k] } : { clip: [0, 1 - k, 0, 0] }; },
    },
    blur: {
      name: "Blur", lead: 0.3, tail: 0.3,
      out: p => { const k = E.inOutSine(p); return { o: 1 - k, blur: 28 * k, s: 1 + 0.04 * k }; },
      in: p => { const k = E.inOutSine(p); return { o: k, blur: 28 * (1 - k), s: 1.04 - 0.04 * k }; },
    },
    cut: { name: "Cut", lead: 0, tail: 0, out: () => ({}), in: () => ({}) },
  };
  // order shown in the studio UI
  T.order = ["lift", "fade", "slide", "zoom", "wipe", "blur", "cut"];
  // combine states (both windows can overlap on very short scenes)
  T.combine = (a, b) => {
    const o = { x: (a.x || 0) + (b.x || 0), y: (a.y || 0) + (b.y || 0), s: (a.s ?? 1) * (b.s ?? 1), r: (a.r || 0) + (b.r || 0),
                o: (a.o ?? 1) * (b.o ?? 1), blur: Math.max(a.blur || 0, b.blur || 0), flash: Math.max(a.flash || 0, b.flash || 0) };
    if (a.clip || b.clip) {
      const ca = a.clip || [0, 0, 0, 0], cb = b.clip || [0, 0, 0, 0];
      o.clip = ca.map((v, i) => Math.max(v, cb[i]));
    }
    return o;
  };
  T.E = E;
  window.VS_TRANSITIONS = T;
})();
