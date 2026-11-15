/* Video Studio — AI layer UI (plain JS, no build step).
 * GPU status, GPU job queue, b-roll providers + library, image-to-video, music generation, warm workers,
 * presenter portraits, licences, and the #/ai page.
 * Used by app.js (editor AI tab + scene background picker). Helpers come from window.SUI (set by app.js).
 */
(() => {
  "use strict";
  const U = () => window.SUI;
  const el = (...a) => U().el(...a);
  const t = (...a) => U().t(...a);
  const api = (...a) => U().api(...a);
  const toast = (...a) => U().toast(...a);

  const fmtDur = s => {
    s = Math.max(0, Math.round(s || 0));
    if (s < 60) return t("ai.durS", { s });
    const m = Math.floor(s / 60);
    if (m < 60) return t("ai.durMin", { m, s: String(s % 60).padStart(2, "0") });
    return t("ai.durH", { h: Math.floor(m / 60), m: m % 60 });
  };
  const gbOf = mb => (mb == null ? "?" : (mb / 1024).toFixed(1));

  const A = { status: null, fmtDur };

  A.loadStatus = async (fresh = false) => {
    A.status = await api("GET", "/api/ai/status" + (fresh ? "?fresh=1" : ""));
    return A.status;
  };
  A.badge = () => el("span", { class: "badge-ai", title: t("ai.pageIntro") }, t("aiSlow"));

  // ---------------------------------------------------------------- GPU card
  A.gpuCard = st => {
    const box = el("div", { class: "gpu-card" });
    if (!st) return box;
    const g = st.gpu || {};
    if (!st.venv) box.append(el("p", { class: "warn small" }, t("ai.venvMissing")));
    if (!g.ok) { box.append(el("p", { class: "muted small" }, t("ai.gpuUnknown"))); return box; }
    const total = g.total_mb, ov = g.ovoz_mb || 0, ai = g.ai_mb || 0;
    const other = Math.max(0, g.used_mb - ov - ai);
    const pct = mb => `${Math.max(0, Math.min(100, (mb / total) * 100)).toFixed(1)}%`;
    box.append(
      el("div", { class: "row between" },
        el("b", {}, `${t("ai.gpu")} · ${g.name || ""}`),
        el("span", { class: "muted small" }, t("ai.gpuFree", { free: gbOf(g.free_mb), total: gbOf(total) }) +
          (g.util != null ? ` · ${t("ai.gpuUtil", { n: g.util })}` : ""))),
      el("div", { class: "vram", role: "img", "aria-label": t("ai.gpuFree", { free: gbOf(g.free_mb), total: gbOf(total) }) },
        el("i", { class: "v-ovoz", style: { width: pct(ov) } }), el("i", { class: "v-ai", style: { width: pct(ai) } }),
        el("i", { class: "v-other", style: { width: pct(other) } })),
      el("div", { class: "vram-legend small" },
        g.ovoz_mb != null ? el("span", {}, el("i", { class: "v-ovoz" }), t("ai.gpuOvoz", { gb: gbOf(ov) })) : null,
        el("span", {}, el("i", { class: "v-ai" }), t("ai.gpuAi", { gb: gbOf(ai) })),
        el("span", {}, el("i", { class: "v-other" }), t("ai.gpuOther", { gb: gbOf(other) })),
        el("span", {}, el("i", { class: "v-free" }), t("ai.gbFree", { gb: gbOf(g.free_mb) }))),
      g.procs && g.procs.length ? el("details", { class: "small muted" }, el("summary", {}, t("ai.processes")),
        el("ul", { class: "procs" }, g.procs.map(p => el("li", {}, el("span", { class: `who who-${p.who}` }, p.who),
          ` ${p.name} · ${gbOf(p.mb)} GB`)))) : null);
    return box;
  };

  // ---------------------------------------------------------------- jobs
  A.jobRow = (j, onChange) => {
    const pct = Math.round((j.progress || 0) * 100);
    const active = j.status === "queued" || j.status === "running";
    const act = (path, method = "POST") => async () => {
      try { await api(method, `/api/ai/jobs/${j.id}${path}`); onChange && onChange(); } catch (e) { toast(e.message, "error"); }
    };
    let meta = [];
    if (j.status === "running") {
      meta.push(t("ai.elapsed", { t: fmtDur(j.elapsed) }));
      if (j.eta != null) meta.push(t("ai.eta", { t: fmtDur(j.eta) }));
    } else if (j.status === "queued" && j.estimate) meta.push(t("ai.estimate", { t: fmtDur(j.estimate) }));
    else if (j.finished && j.started && j.stage !== "cached") meta.push(fmtDur(j.finished - j.started));
    const stage = t("ai.stages." + (j.stage || j.status));
    const prov = j.kind === "broll" && A.status && A.status.providers ? (A.status.providers.find(p => p.id === (j.provider || (j.params && j.params.provider) || "wan22-5b")) || {}).name : null;
    const head = `${t("ai.kinds." + j.kind)}${prov ? ` · ${prov}` : ""}${j.preset && j.kind !== "portrait" && j.kind !== "music" ? ` · ${t("ai.presets." + j.preset)}` : ""}`;
    return el("div", { class: `job ai-job ${j.status} ${j.waiting ? "waiting" : ""}` },
      el("div", { class: "row between" },
        el("b", {}, head, " ", el("span", { class: `st st-${j.status}` }, `${t("ai.status." + j.status)}${j.status === "queued" && j.position ? ` #${j.position}` : ""} · ${stage}`)),
        el("div", { class: "row gap" },
          j.status === "queued" && j.waiting ? el("button", { class: "btn small", title: t("ai.startAnywayHelp"), onclick: () => {
            if (U().confirmBox(t("ai.startAnywayHelp") + "\n\n" + t("ai.startAnyway") + "?")) act("/start-anyway")(); } }, t("ai.startAnyway")) : null,
          active ? el("button", { class: "btn small danger", onclick: act("/cancel") }, t("cancelJob")) : null,
          ["error", "cancelled", "interrupted"].includes(j.status) ? el("button", { class: "btn small", onclick: act("/retry") }, t("ai.retry")) : null,
          !active ? el("button", { class: "icon-btn", title: t("ai.remove"), "aria-label": t("ai.remove"), onclick: act("", "DELETE") }, "✕") : null)),
      j.label ? el("div", { class: "small job-label", title: j.label }, j.label) : null,
      active ? el("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
        el("i", { style: { width: `${pct}%` } })) : null,
      el("div", { class: "muted small" }, [active ? `${pct}%` : "", j.message, ...meta].filter(Boolean).join(" · ")),
      j.error ? el("div", { class: "error small" }, j.error) : null,
      j.result && j.result.cached ? el("div", { class: "ok-t small" }, t("ai.stages.cached")) : null,
      j.result && !j.result.cached && j.status === "done" && j.result.warm != null ? el("div", { class: "small " + (j.result.warm ? "ok-t" : "muted") },
        j.result.warm ? t("ai.warmRun", { s: (j.result.load_seconds ?? 0).toFixed(1) }) : t("ai.coldRun", { s: (j.result.load_seconds ?? 0).toFixed(1) })) : null,
      j.log && j.log.length ? el("details", { class: "small" }, el("summary", { class: "muted" }, t("ai.log")),
        el("pre", { class: "log" }, j.log.slice(-14).join("\n"))) : null);
  };
  A.jobList = (jobs, onChange) => (jobs && jobs.length
    ? el("div", { class: "jobs" }, jobs.map(j => A.jobRow(j, onChange)))
    : el("p", { class: "muted small" }, t("ai.queueEmpty")));

  // ---------------------------------------------------------------- b-roll library
  A.clipCard = (m, { selected, onPick, onDelete } = {}) => {
    const img = el("img", { src: `/api/library/broll/${m.id}/thumb.jpg`, alt: "", loading: "lazy" });
    const media = el("div", { class: "clip-media" }, img,
      el("span", { class: "badge" }, `${m.preset || "?"} · ${m.w}×${m.h} · ${(m.duration || 0).toFixed(1)}s`),
      el("span", { class: "badge prov" }, (m.provider || "wan22-5b") + (m.mode === "i2v" ? " · img→video" : "")));
    let v = null;
    const start = () => {
      if (v) return;
      v = el("video", { src: `/api/library/broll/${m.id}/clip.webm`, muted: true, loop: true, playsinline: true, autoplay: true });
      v.muted = true;
      media.append(v);
      v.play().catch(() => {});
    };
    const stop = () => { if (v) { v.pause(); v.remove(); v = null; } };
    media.addEventListener("mouseenter", start);
    media.addEventListener("mouseleave", stop);
    const used = m.used_in ? (m.used_in.length ? t("ai.usedIn", { n: m.used_in.length }) : t("ai.notUsed")) : "";
    const body = el("div", { class: "clip-body" },
      el("div", { class: "clip-prompt", title: m.prompt }, m.name ? el("b", {}, m.name + " · ") : null, m.prompt),
      el("div", { class: "muted small" }, [t("ai.seedN", { n: m.seed }), t("ai.stepsN", { n: m.steps }), used].filter(Boolean).join(" · ")),
      onDelete ? el("button", { class: "btn small danger", onclick: e => { e.stopPropagation(); onDelete(m); } }, t("delete")) : null);
    if (onPick) {
      return el("button", { class: `clip pickable ${selected ? "on" : ""}`, role: "radio", "aria-checked": selected ? "true" : "false",
        title: m.prompt, onclick: () => onPick(m), onfocus: start, onblur: stop }, media, body);
    }
    return el("div", { class: "clip" }, media, body);
  };
  A.clipGrid = (clips, opts = {}) => (clips && clips.length
    ? el("div", { class: `clip-grid ${opts.compact ? "compact" : ""}`, role: opts.onPick ? "radiogroup" : null },
      clips.map(m => A.clipCard(m, { ...opts, selected: opts.selected === m.id })))
    : el("p", { class: "muted small" }, t("ai.libraryEmpty")));

  A.deleteClip = async (m, after) => {
    if (!U().confirmBox(t("ai.confirmDeleteClip") + (m.used_in && m.used_in.length ? `\n\n${m.used_in.map(u => `${u.title} / ${u.scene}`).join("\n")}` : ""))) return;
    try { await api("DELETE", `/api/library/broll/${m.id}`); after && after(); } catch (e) { toast(e.message, "error"); }
  };

  // generate-a-clip form; `orientation` from the project format, `assign` = {project, scene, dim, blur},
  // `provider` = the default generator, `images` = [{value, label, ref}] that can be animated (image-to-video)
  A.brollForm = ({ prompt = "", orientation = "portrait", fixedOrientation = false, assign = null, onQueued, provider = null,
    images = null, image = null, onProvider = null } = {}) => {
    const st = A.status;
    const provs = (st && st.providers) || [];
    const pr = el("textarea", { rows: 3, placeholder: t("ai.promptPh") });
    pr.value = prompt;
    const neg = el("textarea", { rows: 2 });
    const seed = el("input", { type: "number", class: "narrow", value: Math.floor(Math.random() * 100000), min: 0, step: 1 });
    let preset = "draft", orient = orientation, prov = provider || (st && st.default_provider) || "wan22-5b";
    let img = image || "";
    const est = el("div", { class: "muted small" });
    const presetBox = el("div", { class: "seg", role: "radiogroup", "aria-label": t("ai.preset") });
    const orientBox = el("div", { class: "seg", role: "radiogroup", "aria-label": t("ai.orientation") });
    const provBox = el("div", { class: "prov-list", role: "radiogroup", "aria-label": t("ai.provider") });
    const imgSel = images && images.length ? el("select", { "aria-label": t("ai.animateImage"), onchange: e => { img = e.target.value; draw(); } },
      el("option", { value: "" }, t("ai.noImage")), images.map(o => el("option", { value: o.value, selected: o.value === img }, o.label))) : null;
    const draw = () => {
      const P = provs.find(p => p.id === prov) || provs[0];
      provBox.replaceChildren(...provs.map(p => el("button", { type: "button", class: `prov ${p.id === prov ? "on" : ""}`, role: "radio",
        "aria-checked": p.id === prov ? "true" : "false", title: `${p.model}\n${t("licence")}: ${p.licence} — ${p.licence_note}`,
        onclick: () => { prov = p.id; onProvider && onProvider(p.id); draw(); } },
        el("b", {}, p.name), el("span", { class: `speed sp-${p.speed}` }, t("ai.speed." + p.speed)),
        el("span", { class: "muted tiny" }, `${p.licence} · ${p.modes.includes("i2v") ? t("ai.t2vI2v") : t("ai.t2v")}`))));
      presetBox.replaceChildren(...["draft", "final"].map(k => el("button", { type: "button", class: `seg-btn ${k === preset ? "on" : ""}`,
        role: "radio", "aria-checked": k === preset ? "true" : "false", onclick: () => { preset = k; draw(); } }, t("ai.presets." + k))));
      orientBox.replaceChildren(...["portrait", "landscape"].map(k => el("button", { type: "button", class: `seg-btn ${k === orient ? "on" : ""}`,
        role: "radio", "aria-checked": k === orient ? "true" : "false", disabled: fixedOrientation && k !== orient,
        onclick: () => { orient = k; draw(); } }, t("ai.orientations." + k))));
      const p = P && P.presets[preset];
      if (p) {
        const [w, hh] = p[orient];
        est.textContent = `${t("ai.presetHelpP", { w, h: hh, s: p.seconds, steps: p.steps, fps: p.fps })} · ` +
          (p.measured ? t("ai.estimated", { t: fmtDur(p.estimate), gb: p.need_gb.toFixed(1) }) : t("ai.estimatedSeed", { t: fmtDur(p.estimate) })) +
          (img ? ` · ${t("ai.i2vNote")}` : "") + (P.ram_gb > 40 ? ` · ${t("ai.bigRam", { gb: Math.round(P.ram_gb) })}` : "");
      }
      if (imgSel) imgSel.disabled = !(P && P.modes.includes("i2v"));
    };
    draw();
    const go = el("button", { class: "btn primary", onclick: async () => {
      const body = { prompt: pr.value.trim(), negative: neg.value.trim() || undefined, seed: parseInt(seed.value, 10) || 0,
        preset, orientation: orient, provider: prov };
      if (img && images) { const o = images.find(x => x.value === img); if (o) body.image = o.ref; }
      if (assign) body.assign = assign;
      go.disabled = true;
      try {
        let j;
        try { j = await api("POST", "/api/ai/broll", body); }
        catch (e) { if (e.status === 409 && e.data && e.data.id) j = e.data; else throw e; }
        toast(j.status === "done" ? t("ai.reused") : t("ai.queued"), "ok");
        onQueued && onQueued(j, { provider: prov, image: img });
      } catch (e) { toast(e.message, "error", 8000); }
      finally { go.disabled = false; }
    } }, "✦ " + t("ai.queueIt"));
    return el("div", { class: "form ai-form" },
      el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.provider")), provBox),
      el("div", { class: "field" }, el("label", {}, img ? t("ai.motionPrompt") : t("ai.prompt")), pr),
      imgSel ? el("div", { class: "field" }, el("label", {}, t("ai.animateImage")), imgSel, el("span", { class: "muted tiny" }, t("ai.animateHelp"))) : null,
      el("details", {}, el("summary", { class: "muted small" }, t("ai.negative")), neg),
      el("div", { class: "row gap wrap" },
        el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.preset")), presetBox),
        el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.orientation")), orientBox),
        el("div", { class: "field narrow" }, el("label", {}, t("ai.seed")),
          el("div", { class: "row gap" }, seed, el("button", { type: "button", class: "icon-btn", title: t("ai.randomSeed"),
            "aria-label": t("ai.randomSeed"), onclick: () => { seed.value = Math.floor(Math.random() * 100000); } }, "⟳")))),
      est,
      assign ? el("p", { class: "muted small" }, t("ai.willAssign")) : null,
      el("div", { class: "row gap" }, go, A.badge()));
  };

  // ---------------------------------------------------------------- music generation (ACE-Step)
  A.GENRES = ["corporate electronic", "ambient", "lo-fi hip hop", "cinematic", "acoustic pop", "synthwave", "house", "piano", "orchestral", "funk"];
  A.musicForm = ({ project = null, duration = 30, prompt = "", onQueued, onSpec = null } = {}) => {
    const st = A.status && A.status.presets && A.status.presets.music;
    const pr = el("textarea", { rows: 2, placeholder: t("ai.musicPromptPh") });
    pr.value = prompt;
    const lid = `genres-${Math.random().toString(36).slice(2, 7)}`;
    const genre = el("input", { type: "text", list: lid, placeholder: "corporate electronic" });
    const mood = el("input", { type: "text", placeholder: t("moodPh") });
    const bpm = el("input", { type: "number", class: "narrow", min: 50, max: 200, step: 1, placeholder: "100" });
    const dur = el("input", { type: "number", class: "narrow", min: 10, max: 240, step: 1, value: Math.round(Math.min(240, Math.max(10, duration))) });
    const seed = el("input", { type: "number", class: "narrow", value: Math.floor(Math.random() * 10000), min: 0 });
    const title = el("input", { type: "text", placeholder: t("musicTitlePh") });
    const est = el("span", { class: "muted small" });
    const upd = () => { if (st) est.textContent = t("ai.musicEst", { t: fmtDur((st.estimate_per_30s || 20) * (parseFloat(dur.value) || 30) / 30 + 4), gb: st.need_gb.toFixed(1) }); };
    dur.addEventListener("input", upd); upd();
    const spec = () => ({ prompt: pr.value.trim() || undefined, genre: genre.value.trim() || undefined, mood: mood.value.trim() || undefined,
      bpm: bpm.value ? parseInt(bpm.value, 10) : undefined, duration: parseFloat(dur.value) || 30, seed: parseInt(seed.value, 10) || 0 });
    const go = el("button", { class: "btn primary", onclick: async () => {
      go.disabled = true;
      try {
        const j = await api("POST", "/api/ai/music", { ...spec(), title: title.value.trim() || undefined, project: project || undefined });
        toast(j.status === "done" ? t("ai.musicReused") : t("ai.queued"), "ok");
        onQueued && onQueued(j);
      } catch (e) { toast(e.message, "error", 8000); }
      finally { go.disabled = false; }
    } }, "♪ " + t("ai.musicGenerate"));
    const later = onSpec ? el("button", { class: "btn", title: t("ai.musicLaterHelp"), onclick: () => onSpec(spec()) }, t("ai.musicLater")) : null;
    return el("div", { class: "form ai-form" },
      el("datalist", { id: lid }, A.GENRES.map(g => el("option", { value: g }))),
      el("div", { class: "field" }, el("label", {}, t("ai.musicPrompt")), pr),
      el("div", { class: "row gap wrap" }, el("div", { class: "field" }, el("label", {}, t("ai.genre")), genre),
        el("div", { class: "field" }, el("label", {}, t("musicMood")), mood)),
      el("div", { class: "row gap wrap" }, el("div", { class: "field narrow" }, el("label", {}, "BPM"), bpm),
        el("div", { class: "field narrow" }, el("label", {}, t("ai.durationS")), dur),
        el("div", { class: "field narrow" }, el("label", {}, t("ai.seed")), seed),
        el("div", { class: "field" }, el("label", {}, t("musicTitle")), title)),
      el("p", { class: "muted small" }, t("ai.musicHelp")), est,
      el("div", { class: "row gap wrap" }, go, later, A.badge()));
  };

  // ---------------------------------------------------------------- warm model workers
  A.workersCard = (v, onChange) => {
    const box = el("div", { class: "workers" });
    if (!v) return box;
    const set = v.settings || {};
    const save = async patch => {
      try { await api("PUT", "/api/ai/workers/settings", patch); toast(t("ai.wSaved"), "ok"); onChange && onChange(); }
      catch (e) { toast(e.message, "error"); }
    };
    const warm = el("input", { type: "checkbox", id: "wk-warm", checked: !!set.warm, onchange: e => save({ warm: e.target.checked }) });
    const idle = el("input", { type: "number", class: "narrow", min: 1, max: 1440, value: set.idle_minutes, onchange: e => save({ idle_minutes: e.target.value }) });
    const budget = el("input", { type: "number", class: "narrow", min: 8, max: 120, value: set.ram_budget_gb, onchange: e => save({ ram_budget_gb: e.target.value }) });
    const rows = (v.workers || []).map(w => el("tr", {},
      el("td", {}, el("b", {}, w.label), el("div", { class: "muted tiny" }, (w.models || []).map(m => `${m.key} ${m.gb != null ? m.gb.toFixed(1) + " GB" : ""}`).join(" · "))),
      el("td", {}, el("span", { class: `st st-${w.busy ? "running" : "done"}` }, w.busy ? t("ai.wBusy") : t("ai.wIdle"))),
      el("td", { class: "mono" }, `${(w.ram_gb || 0).toFixed(1)} GB`),
      el("td", { class: "mono" }, w.vram_gb != null ? `${w.vram_gb.toFixed(1)} GB` : "–"),
      el("td", { class: "mono" }, w.busy ? "–" : fmtDur(w.idle_s)),
      el("td", {}, el("button", { class: "btn small", disabled: w.busy, onclick: async () => {
        try { await api("POST", "/api/ai/workers/release-ram", { family: w.family }); onChange && onChange(); } catch (e) { toast(e.message, "error"); } } }, t("ai.releaseOne")))));
    box.append(...[
      el("p", { class: "muted small" }, t("ai.workersHelp")),
      rows.length ? el("table", { class: "mon-table" }, el("thead", {}, el("tr", {}, el("th", {}, t("ai.wModel")), el("th", {}, t("ai.wState")),
        el("th", {}, "RAM"), el("th", {}, "VRAM"), el("th", {}, t("ai.wIdleFor")), el("th", {}, ""))), el("tbody", {}, rows))
        : el("p", { class: "muted small" }, t("ai.noWorkers")),
      el("div", { class: "row gap wrap" },
        el("b", {}, t("ai.wTotal", { gb: (v.ram_gb || 0).toFixed(1), budget: set.ram_budget_gb })),
        el("button", { class: "btn small danger", disabled: !(v.workers || []).length, onclick: async () => {
          try { const r = await api("POST", "/api/ai/workers/release-ram", {}); toast(t("ai.released", { n: r.stopped.length }), "ok"); onChange && onChange(); }
          catch (e) { toast(e.message, "error"); } } }, "⏏ " + t("ai.releaseRam"))),
      el("div", { class: "row gap wrap wk-settings" },
        el("label", { class: "row gap", for: "wk-warm" }, warm, el("span", {}, t("ai.keepWarm"))),
        el("label", { class: "row gap" }, el("span", {}, t("ai.idleMinutes")), idle),
        el("label", { class: "row gap" }, el("span", {}, t("ai.ramBudget")), budget)),
      (v.events || []).length ? el("details", { class: "small muted" }, el("summary", {}, t("ai.wEvents")),
        el("ul", {}, v.events.slice().reverse().map(e => el("li", {}, `${new Date(e.t * 1000).toLocaleTimeString()} — ${e.text}`)))) : null].filter(Boolean));
    return box;
  };

  // providers table (licences + capabilities)
  A.providerTable = st => el("table", { class: "lic" }, el("tbody", {}, ((st && st.providers) || []).map(p => el("tr", {},
    el("td", {}, el("b", {}, p.name), el("div", { class: "muted tiny" }, p.model)),
    el("td", {}, el("b", {}, p.licence), el("div", { class: "muted tiny" }, p.licence_note)),
    el("td", { class: "muted small" }, `${p.modes.map(m => m.toUpperCase()).join(" + ")} · ${p.presets.draft.portrait.join("×")} / ${p.presets.final.portrait.join("×")} · ${p.presets.final.fps} fps · ≤ ${p.max_seconds} s · RAM ~${Math.round(p.ram_gb)} GB`)))));

  // ---------------------------------------------------------------- portraits
  A.portraitGrid = (list, { selected, onPick, onDelete } = {}) => (list && list.length
    ? el("div", { class: "portrait-grid", role: onPick ? "radiogroup" : null }, list.map(m => {
      const kids = [el("img", { src: m.url || `/api/library/portraits/${m.id}/thumb.jpg`, alt: m.name || m.id, loading: "lazy" }),
        el("span", { class: "pick-name" }, m.name || m.id),
        el("span", { class: "muted tiny" }, m.source === "upload" ? t("ai.sourceUpload") : m.source === "asset" ? "project asset" : t("ai.sourceGenerated"))];
      const del = onDelete && m.source !== "asset" ? el("button", { class: "icon-btn danger pdel", "aria-label": `${t("delete")} ${m.name || m.id}`,
        onclick: e => { e.stopPropagation(); onDelete(m); } }, "✕") : null;
      if (onPick) return el("div", { class: "pwrap" }, el("button", { class: `pick portrait ${selected === m.id ? "on" : ""}`, role: "radio",
        "aria-checked": selected === m.id ? "true" : "false", title: m.prompt || m.name || "", onclick: () => onPick(m) }, ...kids), del);
      return el("div", { class: "pwrap" }, el("div", { class: "pick portrait", title: m.prompt || "" }, ...kids), del);
    }))
    : el("p", { class: "muted small" }, t("ai.portraitsEmpty")));

  A.portraitUpload = ({ onDone } = {}) => {
    const file = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp" });
    const ok = el("input", { type: "checkbox", id: `consent-${Math.random().toString(36).slice(2, 7)}` });
    const btn = el("button", { class: "btn", onclick: async () => {
      if (!file.files.length) { file.click(); return; }
      if (!ok.checked) { toast(t("ai.consentNeeded"), "warn"); ok.focus(); return; }
      const fd = new FormData();
      fd.append("file", file.files[0], file.files[0].name);
      fd.append("consent", "true");
      btn.disabled = true;
      try { const m = await api("POST", "/api/library/portraits", fd, true); file.value = ""; ok.checked = false; onDone && onDone(m); }
      catch (e) { toast(e.message, "error", 8000); }
      finally { btn.disabled = false; }
    } }, "⇪ " + t("ai.uploadPortrait"));
    return el("div", { class: "form ai-form" },
      el("p", { class: "muted small" }, t("ai.portraitHint")), file,
      el("label", { class: "row gap consent", for: ok.id }, ok, el("span", {}, t("ai.consent"))), el("div", {}, btn));
  };

  A.portraitGenerate = ({ prompt = "", project = null, onQueued } = {}) => {
    const pr = el("textarea", { rows: 2, placeholder: t("ai.portraitPromptPh") });
    pr.value = prompt;
    const seed = el("input", { type: "number", class: "narrow", value: Math.floor(Math.random() * 1000), min: 0 });
    const p = A.status && A.status.presets && A.status.presets.portrait;
    const btn = el("button", { class: "btn", onclick: async () => {
      btn.disabled = true;
      try {
        const j = await api("POST", "/api/ai/portrait", { prompt: pr.value.trim(), seed: parseInt(seed.value, 10) || 0, project: project || undefined });
        toast(t("ai.queued"), "ok"); onQueued && onQueued(j);
      } catch (e) { toast(e.message, "error", 8000); }
      finally { btn.disabled = false; }
    } }, "✦ " + t("ai.generatePortrait"));
    return el("div", { class: "form ai-form" },
      el("div", { class: "field" }, el("label", {}, t("ai.prompt")), pr),
      el("div", { class: "row gap wrap" }, el("div", { class: "field narrow" }, el("label", {}, t("ai.seed")), seed),
        p ? el("span", { class: "muted small" }, t("ai.estimated", { t: fmtDur(p.estimate), gb: p.need_gb.toFixed(1) })) : null),
      el("div", { class: "row gap" }, btn, A.badge()));
  };

  // ---------------------------------------------------------------- licences
  A.licences = st => el("div", { class: "licences" },
    el("p", { class: "muted small" }, t("ai.licencesHelp")),
    el("table", { class: "lic" }, el("tbody", {}, ((st && st.licences) || []).map(l => el("tr", {},
      el("td", {}, l.what), el("td", {}, el("b", {}, l.licence)), el("td", { class: "muted small" }, l.note || ""))))),
    st && st.disclosure ? el("p", { class: "warn small" }, el("b", {}, t("ai.disclosure") + ": "), st.disclosure) : null);

  // ---------------------------------------------------------------- the #/ai page
  A.page = view => {
    document.title = `${t("ai.pageTitle")} — ${t("appName")}`;
    const gpuBox = el("div", { class: "panel" });
    const jobsBox = el("div", {});
    const libBox = el("div", {});
    const formBox = el("div", {});
    const portraitsBox = el("div", {});
    const pForms = el("div", { class: "two" });
    const licBox = el("div", {});
    const provBox = el("div", {});
    const musicBox = el("div", {});
    const workBox = el("div", {});
    let images = [];
    const sec = (title, help, ...kids) => el("section", { class: "panel" },
      el("div", { class: "row between" }, el("h2", {}, title), help ? null : null),
      help ? el("p", { class: "muted small" }, help) : null, ...kids);
    view.append(el("section", { class: "page ai-page" },
      el("div", { class: "page-head" }, el("div", {}, el("h1", {}, t("ai.pageTitle"), " ", A.badge()),
        el("p", { class: "muted" }, t("ai.pageIntro")))),
      el("div", { class: "ai-cols" },
        el("div", { class: "ai-col" }, gpuBox, sec(t("ai.workers"), null, workBox), sec(t("ai.queue"), null, jobsBox),
          sec(t("ai.generateClip"), null, formBox), sec(t("ai.musicTitle"), null, musicBox),
          sec(t("ai.providers"), t("ai.providersHelp"), provBox), sec(t("ai.licences"), null, licBox)),
        el("div", { class: "ai-col" }, sec(t("ai.library"), t("ai.libraryHelp"), libBox),
          sec(t("ai.portraits"), t("ai.portraitsHelp"), portraitsBox, pForms)))));
    const loadWorkers = async () => { workBox.replaceChildren(A.workersCard(await api("GET", "/api/ai/workers"), loadWorkers)); };
    let alive = true, prev = {};
    const loadLib = async () => {
      const [clips, ports] = await Promise.all([api("GET", "/api/library/broll"), api("GET", "/api/library/portraits")]);
      libBox.replaceChildren(A.clipGrid(clips, { onDelete: m => A.deleteClip(m, loadLib) }));
      const imgs = ports.map(m => ({ value: m.id, label: `${t("ai.kinds.portrait")}: ${m.name || m.id}`, ref: { portrait: m.id } }));
      if (JSON.stringify(imgs.map(i => i.value)) !== JSON.stringify(images.map(i => i.value))) {
        images = imgs;
        formBox.replaceChildren(A.brollForm({ images, onQueued: () => loadJobs() }));
      }
      portraitsBox.replaceChildren(A.portraitGrid(ports, { onDelete: async m => {
        if (!U().confirmBox(t("ai.confirmDeletePortrait"))) return;
        try { await api("DELETE", `/api/library/portraits/${m.id}`); loadLib(); } catch (e) { toast(e.message, "error"); } } }));
    };
    const loadJobs = async () => {
      const js = await api("GET", "/api/ai/jobs");
      jobsBox.replaceChildren(A.jobList(js, loadJobs));
      let refresh = false;
      for (const j of js) {
        if (prev[j.id] && prev[j.id] !== j.status && j.status === "done") {
          refresh = true;
          toast(j.kind === "broll" ? t("ai.clipReady") : j.kind === "music" ? t("ai.musicReady", { name: (j.result || {}).title || "" }) : `${t("ai.kinds." + j.kind)} ✓`, "ok");
        }
        prev[j.id] = j.status;
      }
      if (refresh) loadLib();
    };
    const loadStatus = async () => { await A.loadStatus(); gpuBox.replaceChildren(A.gpuCard(A.status)); };
    (async () => {
      try { await A.loadStatus(true); } catch (e) { toast(e.message, "error"); }
      gpuBox.replaceChildren(A.gpuCard(A.status));
      licBox.replaceChildren(A.licences(A.status));
      provBox.replaceChildren(A.providerTable(A.status), el("p", { class: "muted small" }, t("ai.cloudNote")));
      formBox.replaceChildren(A.brollForm({ images, onQueued: () => loadJobs() }));
      musicBox.replaceChildren(A.musicForm({ onQueued: () => loadJobs() }));
      loadWorkers().catch(() => {});
      pForms.replaceChildren(A.portraitUpload({ onDone: () => loadLib() }), A.portraitGenerate({ onQueued: () => loadJobs() }));
      loadLib().catch(e => toast(e.message, "error"));
      loadJobs().catch(() => {});
    })();
    const t1 = setInterval(() => { if (alive) loadJobs().catch(() => {}); }, 2000);
    const t2 = setInterval(() => { if (alive) loadStatus().catch(() => {}); }, 3000);
    const t3 = setInterval(() => { if (alive) loadWorkers().catch(() => {}); }, 4000);
    return () => { alive = false; clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  };

  window.AIUI = A;
})();
