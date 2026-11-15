/* Video Studio — live system monitor (plain JS, hand-written canvas charts, no libraries).
 * - Header widget on every page: GPU %, VRAM, temperature + power, CPU %, RAM (1 s updates over SSE).
 * - #/monitor page: charts of the last 30 minutes (GPU, VRAM by owner, CPU, RAM incl. warm AI models,
 *   temperature, power) with job / "Make video" stage markers, a process table and the warm workers.
 * Data: GET /api/monitor (history) + EventSource /api/monitor/stream (one sample per second).
 */
(() => {
  "use strict";
  const U = () => window.SUI;
  const el = (...a) => U().el(...a);
  const t = (...a) => U().t(...a);
  const api = (...a) => U().api(...a);

  // owner colours (validated for the dark surface: lightness band, chroma, CVD separation); "other" is a neutral
  const C = { ovoz: "#b87d15", ai: "#8b6cff", studio: "#1aa883", other: "#5a6488", line: "#8b6cff", limit: "#8b94b2",
    grid: "rgba(139,148,178,.14)", axis: "#8b94b2", text: "#e8ecf8", surface: "#121725" };
  const MARK = { "stage-voice": "rgba(184,125,21,.16)", "stage-unload": "rgba(139,148,178,.16)", "stage-ai": "rgba(139,108,255,.16)",
    "stage-render": "rgba(26,168,131,.16)", render: "rgba(26,168,131,.10)", voice: "rgba(184,125,21,.10)" };
  const markFill = cat => MARK[cat] || (String(cat).startsWith("ai-") ? "rgba(139,108,255,.10)" : "rgba(139,148,178,.10)");

  const M = { es: null, subs: new Set(), last: null, connected: false };
  M.connect = () => {
    if (M.es) return;
    try {
      M.es = new EventSource("/api/monitor/stream?procs=1");
      M.es.onmessage = e => {
        let d; try { d = JSON.parse(e.data); } catch { return; }
        M.last = d; M.connected = true;
        for (const f of M.subs) { try { f(d); } catch (err) { console.error(err); } }
      };
      M.es.onerror = () => { M.connected = false; widget(null); };
    } catch { /* EventSource unsupported: the widget stays empty */ }
  };
  M.on = f => { M.subs.add(f); M.connect(); return () => M.subs.delete(f); };

  // ---------------------------------------------------------------- header widget
  const lvl = (v, warn, bad) => (v == null ? "" : v >= bad ? "hi" : v >= warn ? "mid" : "lo");
  const gb = mb => (mb / 1024).toFixed(1);
  function widget(d) {
    const w = document.getElementById("sysmon");
    if (!w) return;
    const s = d && d.s;
    if (!s) { w.classList.add("off"); w.querySelector(".sm-body").textContent = t("mon.offline"); return; }
    w.classList.remove("off");
    const parts = [];
    const item = (cls, label, value, level, title) => el("span", { class: `sm-item ${level ? "lv-" + level : ""} ${cls}`, title },
      el("span", { class: "sm-l" }, label), el("b", {}, value));
    if (s.vt) {
      parts.push(item("sm-gpu", "GPU", `${s.gu}%`, lvl(s.gu, 60, 90), t("mon.tipGpu")));
      parts.push(item("sm-vram", "VRAM", `${gb(s.vu)}/${gb(s.vt)}`, lvl(s.vu / s.vt * 100, 75, 92), t("mon.tipVram",
        { o: gb(s.vo || 0), a: gb(s.va || 0), s: gb(s.vs || 0) })));
      parts.push(item("sm-temp", "", `${s.tc}° ${Math.round(s.pw)}W`, lvl(s.tc, 70, 82), t("mon.tipTemp")));
    }
    parts.push(item("sm-cpu", "CPU", `${Math.round(s.cpu)}%`, lvl(s.cpu, 60, 90), t("mon.tipCpu")));
    parts.push(item("sm-ram", "RAM", `${Math.round(s.ru)}/${Math.round(s.rt)}`, lvl(s.ru / s.rt * 100, 75, 92),
      t("mon.tipRam", { w: (s.rw || 0).toFixed(1) })));
    w.querySelector(".sm-body").replaceChildren(...parts);
    w.setAttribute("aria-label", t("mon.widgetAria", { g: s.gu ?? "?", c: Math.round(s.cpu) }));
  }
  M.initWidget = () => {
    const w = document.getElementById("sysmon");
    if (!w || w.dataset.ready) return;
    w.dataset.ready = "1";
    w.replaceChildren(el("span", { class: "sm-body" }, "…"));
    w.title = t("mon.openMonitor");
    M.on(widget);
  };

  // ---------------------------------------------------------------- charts
  function fmtClock(ts) { const d = new Date(ts * 1000); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }

  /** One chart: series = [{key, label, color, get(s) -> number|null}], stacked?, max (fixed or auto), unit. */
  class Chart {
    constructor(opts) {
      this.o = opts;
      this.canvas = el("canvas", { class: "mon-canvas", role: "img", "aria-label": opts.title });
      this.tip = el("div", { class: "mon-tip", hidden: true });
      this.legend = opts.series.length > 1 ? el("div", { class: "mon-legend small" }, opts.series.map(s =>
        el("span", {}, el("i", { style: { background: s.color } }), s.label))) : null;
      this.head = el("div", { class: "row between mon-head" }, el("h3", {}, opts.title), el("span", { class: "muted small mono mon-now" }));
      this.box = el("div", { class: "mon-chart panel" }, this.head, this.legend, el("div", { class: "mon-plot" }, this.canvas, this.tip));
      this.hoverT = null;
      this.canvas.addEventListener("mousemove", e => { const r = this.canvas.getBoundingClientRect(); opts.onHover(this.tAt(e.clientX - r.left)); });
      this.canvas.addEventListener("mouseleave", () => opts.onHover(null));
    }
    tAt(x) { const g = this.g; if (!g) return null; return g.t0 + (x - g.l) / (g.w - g.l - g.r) * (g.t1 - g.t0); }
    draw(samples, markers, range, hoverT) {
      const o = this.o, cv = this.canvas, dpr = window.devicePixelRatio || 1;
      const W = cv.clientWidth || 600, H = cv.clientHeight || 150;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
      const x = cv.getContext("2d");
      x.setTransform(dpr, 0, 0, dpr, 0, 0);
      x.clearRect(0, 0, W, H);
      const l = 40, r = 8, top = 6, bot = 18;
      const t1 = samples.length ? samples[samples.length - 1].t : Date.now() / 1000, t0 = t1 - range;
      this.g = { l, r, w: W, t0, t1 };
      const vis = samples.filter(s => s.t >= t0 - 2);
      // y scale
      let max = typeof o.max === "function" ? o.max(vis) : o.max;
      if (!max) {
        max = 1;
        for (const s of vis) {
          const v = o.stacked ? o.series.reduce((a, se) => a + (se.get(s) || 0), 0) : Math.max(...o.series.map(se => se.get(s) || 0));
          if (v > max) max = v;
        }
        max = niceMax(max * 1.1);
      }
      const X = tt => l + (tt - t0) / range * (W - l - r);
      const Y = v => top + (1 - Math.min(1, Math.max(0, v / max))) * (H - top - bot);
      // stage markers (bands)
      for (const m of markers) {
        const a = Math.max(t0, m.t0), b = Math.min(t1, m.t1 || t1);
        if (b <= a) continue;
        x.fillStyle = markFill(m.cat);
        x.fillRect(X(a), top, Math.max(1, X(b) - X(a)), H - top - bot);
      }
      // grid + axis labels (recessive)
      x.strokeStyle = C.grid; x.lineWidth = 1; x.fillStyle = C.axis; x.font = "11px Inter, system-ui, sans-serif";
      x.textAlign = "right"; x.textBaseline = "middle";
      for (let i = 0; i <= 4; i++) {
        const v = max * i / 4, y = Math.round(Y(v)) + 0.5;
        x.beginPath(); x.moveTo(l, y); x.lineTo(W - r, y); x.stroke();
        x.fillText(o.fmt ? o.fmt(v) : String(Math.round(v)), l - 6, y);
      }
      x.textAlign = "center"; x.textBaseline = "alphabetic";
      const step = range > 1200 ? 300 : range > 600 ? 120 : 60;
      for (let tt = Math.ceil(t0 / step) * step; tt <= t1; tt += step) if (X(tt) < W - r - 16 && X(tt) > l + 12) x.fillText(fmtClock(tt), X(tt), H - 4);
      // reference line (e.g. power limit)
      if (o.ref) {
        const v = o.ref(); if (v) {
          x.save(); x.setLineDash([4, 4]); x.strokeStyle = C.limit; x.beginPath(); x.moveTo(l, Y(v)); x.lineTo(W - r, Y(v)); x.stroke(); x.restore();
        }
      }
      // data: stacked areas (2 px surface gap between bands) or lines; gaps where samples are missing
      const segs = splitGaps(vis);
      if (o.stacked) {
        for (const seg of segs) {
          let base = seg.map(() => 0);
          for (const se of o.series) {
            const top2 = seg.map((s, i) => base[i] + Math.max(0, se.get(s) || 0));
            x.beginPath();
            seg.forEach((s, i) => { const px = X(s.t), py = Y(top2[i]); i ? x.lineTo(px, py) : x.moveTo(px, py); });
            for (let i = seg.length - 1; i >= 0; i--) x.lineTo(X(seg[i].t), Y(base[i]));
            x.closePath(); x.fillStyle = se.color; x.globalAlpha = 0.85; x.fill(); x.globalAlpha = 1;
            x.strokeStyle = C.surface; x.lineWidth = 2;   // spacer between stacked bands
            x.beginPath(); seg.forEach((s, i) => { const px = X(s.t), py = Y(top2[i]); i ? x.lineTo(px, py) : x.moveTo(px, py); }); x.stroke();
            base = top2;
          }
        }
        if (o.total) {      // the total (e.g. VRAM used) as a thin line over the stack
          x.strokeStyle = C.text; x.globalAlpha = 0.5; x.lineWidth = 1;
          for (const seg of segs) { x.beginPath(); seg.forEach((s, i) => { const v = o.total(s); if (v == null) return; const px = X(s.t), py = Y(v); i ? x.lineTo(px, py) : x.moveTo(px, py); }); x.stroke(); }
          x.globalAlpha = 1;
        }
      } else {
        for (const se of o.series) {
          x.strokeStyle = se.color; x.lineWidth = 2; x.lineJoin = "round";
          for (const seg of segs) {
            x.beginPath(); let on = false;
            for (const s of seg) { const v = se.get(s); if (v == null) { on = false; continue; } const px = X(s.t), py = Y(v); on ? x.lineTo(px, py) : x.moveTo(px, py); on = true; }
            x.stroke();
          }
        }
      }
      // crosshair + tooltip
      const last = vis[vis.length - 1];
      this.head.querySelector(".mon-now").textContent = last ? o.now(last) : "";
      if (hoverT != null && hoverT >= t0 && hoverT <= t1 && vis.length) {
        const s = nearest(vis, hoverT);
        const px = X(s.t);
        x.strokeStyle = C.axis; x.lineWidth = 1; x.beginPath(); x.moveTo(px + 0.5, top); x.lineTo(px + 0.5, H - bot); x.stroke();
        for (const se of o.series) {
          let v = se.get(s); if (v == null) continue;
          if (o.stacked) v = o.series.slice(0, o.series.indexOf(se) + 1).reduce((a, q) => a + (q.get(s) || 0), 0);
          x.beginPath(); x.arc(px, Y(v), 4, 0, Math.PI * 2); x.fillStyle = se.color; x.fill(); x.lineWidth = 2; x.strokeStyle = C.surface; x.stroke();
        }
        const acts = markers.filter(m => m.t0 <= s.t && (m.t1 || 1e12) >= s.t).map(m => m.label);
        this.tip.replaceChildren(el("div", { class: "mono" }, new Date(s.t * 1000).toLocaleTimeString()),
          ...o.series.map(se => el("div", {}, el("i", { style: { background: se.color } }), `${se.label}: `, el("b", {}, o.fmtTip ? o.fmtTip(se.get(s), se) : se.get(s)))),
          ...(o.total ? [el("div", { class: "muted" }, `${t("mon.total")}: ${o.fmtTip(o.total(s))}`)] : []),
          ...acts.map(a => el("div", { class: "muted tiny" }, "▸ " + a)));
        this.tip.hidden = false;
        const tw = this.tip.offsetWidth || 160;
        this.tip.style.left = `${Math.min(W - tw - 4, Math.max(4, px + 12))}px`;
        this.tip.style.top = "8px";
      } else this.tip.hidden = true;
    }
  }
  function niceMax(v) { const p = 10 ** Math.floor(Math.log10(v)); for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p; return 10 * p; }
  function nearest(arr, tt) { let lo = 0, hi = arr.length - 1; while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (arr[mid].t < tt) lo = mid; else hi = mid; } return Math.abs(arr[lo].t - tt) < Math.abs(arr[hi].t - tt) ? arr[lo] : arr[hi]; }
  function splitGaps(arr) { const out = []; let cur = []; for (const s of arr) { if (cur.length && s.t - cur[cur.length - 1].t > 5) { out.push(cur); cur = []; } cur.push(s); } if (cur.length) out.push(cur); return out; }

  // ---------------------------------------------------------------- the #/monitor page
  M.page = view => {
    document.title = `${t("mon.title")} — ${t("appName")}`;
    let samples = [], markers = [], info = {}, range = 1800, hoverT = null, alive = true, procs = [], workers = null, current = {};
    const redraw = () => { for (const c of charts) c.draw(samples, markers, range, hoverT); };
    const onHover = tt => { hoverT = tt; redraw(); };
    const mb = v => (v == null ? "–" : `${(v / 1024).toFixed(1)} GB`);
    const vramSeries = [
      { key: "vo", label: t("mon.ownOvoz"), color: C.ovoz, get: s => s.vo },
      { key: "va", label: t("mon.ownAi"), color: C.ai, get: s => s.va },
      { key: "vs", label: t("mon.ownStudio"), color: C.studio, get: s => s.vs },
      { key: "vx", label: t("mon.ownOther"), color: C.other, get: s => (s.vu == null ? null : Math.max(0, s.vu - (s.vo || 0) - (s.va || 0) - (s.vs || 0))) }];
    const ramSeries = [
      { key: "rw", label: t("mon.ramWarm"), color: C.ai, get: s => s.rw || 0 },
      { key: "ra", label: t("mon.ramAiOther"), color: "#5b46b8", get: s => Math.max(0, (s.ra || 0) - (s.rw || 0)) },
      { key: "ro", label: t("mon.ownOvoz"), color: C.ovoz, get: s => s.ro || 0 },
      { key: "rs", label: t("mon.ownStudio"), color: C.studio, get: s => s.rs || 0 },
      { key: "rx", label: t("mon.ownOther"), color: C.other, get: s => Math.max(0, s.ru - (s.ra || 0) - (s.ro || 0) - (s.rs || 0)) }];
    const charts = [
      new Chart({ title: t("mon.gpuUtil"), series: [{ label: "GPU", color: C.line, get: s => s.gu }], max: 100, fmt: v => `${v}%`,
        fmtTip: v => `${v}%`, now: s => (s.gu == null ? "" : `${s.gu}%`), onHover }),
      new Chart({ title: t("mon.vram"), series: vramSeries, stacked: true, max: () => samples.length && samples[samples.length - 1].vt || 16384,
        fmt: v => `${Math.round(v / 1024)}`, fmtTip: v => mb(v), total: s => s.vu, now: s => (s.vu == null ? "" : `${mb(s.vu)} / ${mb(s.vt)}`), onHover }),
      new Chart({ title: t("mon.cpu"), series: [{ label: "CPU", color: C.line, get: s => s.cpu }], max: 100, fmt: v => `${v}%`,
        fmtTip: v => `${v}%`, now: s => `${Math.round(s.cpu)}%`, onHover }),
      new Chart({ title: t("mon.ram"), series: ramSeries, stacked: true, max: () => samples.length && samples[samples.length - 1].rt || 128,
        fmt: v => `${Math.round(v)}`, fmtTip: v => `${(v || 0).toFixed(1)} GB`, total: s => s.ru,
        now: s => `${s.ru.toFixed(1)} / ${Math.round(s.rt)} GB · ${t("mon.ramWarm")} ${(s.rw || 0).toFixed(1)} GB`, onHover }),
      new Chart({ title: t("mon.temp"), series: [{ label: t("mon.tempShort"), color: C.line, get: s => s.tc }], max: 100, fmt: v => `${v}°`,
        fmtTip: v => `${v} °C`, now: s => (s.tc == null ? "" : `${s.tc} °C`), onHover }),
      new Chart({ title: t("mon.power"), series: [{ label: t("mon.powerShort"), color: C.line, get: s => s.pw }],
        max: () => niceMax((info.power_limit_w || 300) * 1.05), fmt: v => `${Math.round(v)}`, fmtTip: v => `${v} W`, ref: () => info.power_limit_w,
        now: s => (s.pw == null ? "" : `${Math.round(s.pw)} W / ${info.power_limit_w || "?"} W`), onHover }),
    ];
    const rangeSeg = el("div", { class: "seg", role: "radiogroup", "aria-label": t("mon.range") }, [300, 900, 1800].map(r =>
      el("button", { type: "button", class: `seg-btn ${r === range ? "on" : ""}`, role: "radio", "aria-checked": r === range ? "true" : "false",
        onclick: e => { range = r; [...rangeSeg.children].forEach(b => { b.classList.toggle("on", b === e.currentTarget); b.setAttribute("aria-checked", b === e.currentTarget ? "true" : "false"); }); redraw(); } },
      t("mon.minutes", { n: r / 60 }))));
    const nowBox = el("div", { class: "mon-now-box" });
    const actBox = el("div", { class: "mon-act" });
    const markBox = el("div", { class: "mon-marks" });
    const procBox = el("div", {});
    const workBox = el("div", {});
    const ovBox = el("p", { class: "muted small" });
    view.append(el("section", { class: "page mon-page" },
      el("div", { class: "page-head row between wrap" }, el("div", {}, el("h1", {}, t("mon.title")), el("p", { class: "muted" }, t("mon.intro"))), rangeSeg),
      nowBox, actBox,
      el("div", { class: "mon-grid" }, charts.map(c => c.box)),
      el("section", { class: "panel" }, el("h2", {}, t("mon.markers")), el("p", { class: "muted small" }, t("mon.markersHelp")), markBox),
      el("div", { class: "mon-cols" },
        el("section", { class: "panel" }, el("h2", {}, t("mon.procs")), procBox),
        el("section", { class: "panel" }, el("h2", {}, t("mon.workers")), workBox)),
      ovBox));

    const tile = (label, value, sub) => el("div", { class: "mon-tile" }, el("span", { class: "muted small" }, label), el("b", {}, value), sub ? el("span", { class: "muted tiny" }, sub) : null);
    function drawNow() {
      const s = samples[samples.length - 1];
      if (!s) return;
      nowBox.replaceChildren(
        tile("GPU", s.gu == null ? "–" : `${s.gu}%`, info.gpu || ""),
        tile("VRAM", s.vt ? `${mb(s.vu)} / ${mb(s.vt)}` : "–", s.vt ? `Ovoz ${mb(s.vo)} · AI ${mb(s.va)} · ${t("mon.ownStudio")} ${mb(s.vs)}` : ""),
        tile(t("mon.tempShort"), s.tc == null ? "–" : `${s.tc} °C`, s.pw == null ? "" : `${Math.round(s.pw)} W / ${info.power_limit_w || "?"} W`),
        tile("CPU", `${Math.round(s.cpu)}%`, info.cpu ? `${info.cpu} · ${info.cpu_threads} ${t("mon.threads")}` : ""),
        tile("RAM", `${s.ru.toFixed(1)} / ${Math.round(s.rt)} GB`, `${t("mon.ramWarm")} ${(s.rw || 0).toFixed(1)} GB`));
      const open = markers.filter(m => !m.t1);
      actBox.replaceChildren(el("b", {}, t("mon.now") + ": "), ...(open.length ? open.map(m => el("span", { class: `mon-chip ${m.cat}` }, m.label)) : [el("span", { class: "muted" }, t("mon.idle"))]));
    }
    function drawMarks() {
      const list = markers.slice().reverse().slice(0, 30);
      markBox.replaceChildren(list.length ? el("table", { class: "mon-table" }, el("thead", {}, el("tr", {}, el("th", {}, t("mon.start")), el("th", {}, t("mon.what")), el("th", {}, t("mon.duration")))),
        el("tbody", {}, list.map(m => el("tr", {}, el("td", { class: "mono" }, new Date(m.t0 * 1000).toLocaleTimeString()),
          el("td", {}, el("i", { class: "mk", style: { background: markFill(m.cat).replace(/[\d.]+\)$/, "0.9)") } }), m.label),
          el("td", { class: "mono" }, m.t1 ? window.AIUI.fmtDur(m.t1 - m.t0) : t("mon.running")))))) : el("p", { class: "muted small" }, t("mon.noMarkers")));
    }
    function drawProcs() {
      procBox.replaceChildren(procs.length ? el("table", { class: "mon-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "PID"), el("th", {}, t("mon.process")), el("th", {}, t("mon.owner")), el("th", {}, "VRAM"), el("th", {}, "GPU"), el("th", {}, "RAM"))),
        el("tbody", {}, procs.map(p => el("tr", {}, el("td", { class: "mono" }, p.pid), el("td", { title: p.label }, p.label),
          el("td", {}, el("span", { class: `who who-${p.who}` }, t("mon.own_" + p.who))), el("td", { class: "mono" }, mb(p.vram_mb)),
          el("td", { class: "mono" }, `${p.gpu}%`), el("td", { class: "mono" }, p.ram_gb != null ? `${p.ram_gb.toFixed(1)} GB` : "–"))))) : el("p", { class: "muted small" }, t("mon.noProcs")));
    }
    function drawWorkers() {
      if (!workers) return;
      workBox.replaceChildren(window.AIUI.workersCard(workers, async () => { workers = await api("GET", "/api/ai/workers"); drawWorkers(); }));
    }
    async function loadFull() {
      const d = await api("GET", "/api/monitor?procs=1");
      samples = d.samples || []; markers = d.markers || []; info = d.info || {}; procs = d.procs || []; workers = d.workers; current = d.current || {};
      const o = d.overhead || {};
      ovBox.textContent = t("mon.overhead", { cpu: o.avg_cpu_ms, wall: o.avg_wall_ms, pct: o.cpu_core_pct, n: o.samples });
      drawNow(); drawMarks(); drawProcs(); drawWorkers(); redraw();
    }
    loadFull().catch(e => U().toast(e.message, "error"));
    const off = M.on(d => {
      if (!alive || !d.s) return;
      if (!samples.length || d.s.t > samples[samples.length - 1].t) samples.push(d.s);
      const cut = d.s.t - 1805;
      while (samples.length && samples[0].t < cut) samples.shift();
      if (d.procs) procs = d.procs;
      // markers: open ones come with each message; close the ones that disappeared
      const open = new Map((d.open || []).map(m => [m.key, m]));
      for (const m of markers) if (!m.t1 && !open.has(m.key)) m.t1 = d.s.t;
      for (const [k, m] of open) { const ex = markers.find(x => x.key === k); if (ex) ex.label = m.label; else markers.push(m); }
      drawNow(); redraw();
      if (Math.round(d.s.t) % 2 === 0) drawProcs();
      if (Math.round(d.s.t) % 5 === 0) { drawMarks(); api("GET", "/api/ai/workers").then(w => { workers = w; drawWorkers(); }).catch(() => {}); }
    });
    const onResize = () => redraw();
    addEventListener("resize", onResize);
    return () => { alive = false; off(); removeEventListener("resize", onResize); };
  };

  window.MonUI = M;
})();
