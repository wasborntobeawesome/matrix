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
    let preset = "draft", orient = orientation, prov = provider || (st && st.default_provider) || "fastwan-5b";
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
        p.legacy ? el("span", { class: "legacy-tag", title: t("ai.legacyHelp") }, t("ai.legacy")) : null,
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
    el("td", {}, el("b", {}, p.name), p.legacy ? el("span", { class: "legacy-tag", title: t("ai.legacyHelp") }, t("ai.legacy")) : null,
      el("div", { class: "muted tiny" }, p.model)),
    el("td", {}, el("b", {}, p.licence), el("div", { class: "muted tiny" }, p.licence_note)),
    el("td", { class: "muted small" }, `${p.modes.map(m => m.toUpperCase()).join(" + ")} · ${p.presets.draft.portrait.join("×")} / ${p.presets.final.portrait.join("×")} · ${p.presets.final.fps} fps · ≤ ${p.max_seconds} s · RAM ~${Math.round(p.ram_gb)} GB`)))));

  // ---------------------------------------------------------------- ComfyUI: server, header pill, workflows
  A.comfyState = s => !s ? "off" : (s.state === "external" ? (s.reachable ? (s.busy ? "busy" : "running") : "stopped")
    : s.state === "running" ? (s.busy ? "busy" : "running") : s.state);
  A.initHeader = () => {
    const pill = document.getElementById("comfy-pill");
    if (!pill || pill.dataset.ready) return;
    pill.dataset.ready = "1";
    const tick = async () => {
      let s = null;
      try { s = await api("GET", "/api/comfy/status"); } catch { /* studio down: the Ovoz pill says so */ }
      A.comfy = s;
      const st = A.comfyState(s);
      if (!s || (!s.installed && s.mode === "managed")) { pill.hidden = true; return; }
      pill.hidden = false;
      const q = (s.queue_running || 0) + (s.queue_pending || 0);
      pill.className = `pill comfy-pill cs-${st}`;
      pill.textContent = `ComfyUI · ${t("comfy.state." + st)}${s.reachable ? ` · ${t("comfy.queueN", { n: q })}` : ""}`;
      pill.title = `${s.url}${s.version ? " · v" + s.version : ""}${s.vram_mb != null ? ` · VRAM ${gbOf(s.vram_mb)} GB` : ""}${s.error ? "\n" + s.error : ""}`;
    };
    tick();
    setInterval(tick, 5000);
  };

  A.comfyCard = (s, onChange) => {
    const box = el("div", { class: "comfy-card" });
    if (!s) return box;
    const st = A.comfyState(s);
    const act = (path, msg) => async () => {
      try { const r = await api("POST", path); if (msg) toast(typeof msg === "function" ? msg(r) : msg, "ok"); onChange && onChange(); }
      catch (e) { toast(e.message, "error", 9000); }
    };
    const lf = s.last_free;
    const set = s.settings || {};
    const save = async patch => {
      try { await api("PUT", "/api/comfy/settings", patch); toast(t("ai.wSaved"), "ok"); onChange && onChange(); }
      catch (e) { toast(e.message, "error"); }
    };
    const mode = el("select", { onchange: e => save({ mode: e.target.value }) },
      ["managed", "external"].map(m => el("option", { value: m, selected: set.mode === m }, t("comfy.mode." + m))));
    const url = el("input", { type: "url", value: set.url || "", placeholder: "http://127.0.0.1:8188", onchange: e => save({ url: e.target.value }) });
    const vram = el("select", { onchange: e => save({ vram_mode: e.target.value }) },
      ["auto", "lowvram", "novram", "highvram"].map(m => el("option", { value: m, selected: set.vram_mode === m }, t("comfy.vram." + m))));
    const reserve = el("input", { type: "number", class: "narrow", min: 0, max: 8, step: 0.5, value: set.reserve_vram_gb, onchange: e => save({ reserve_vram_gb: e.target.value }) });
    const auto = el("input", { type: "checkbox", id: "cf-auto", checked: !!set.autostart, onchange: e => save({ autostart: e.target.checked }) });
    const custom = el("input", { type: "checkbox", id: "cf-custom", checked: !!set.custom_nodes, onchange: e => {
      if (e.target.checked && !U().confirmBox(t("comfy.customWarn"))) { e.target.checked = false; return; }
      save({ custom_nodes: e.target.checked }); } });
    const extra = el("input", { type: "text", value: set.extra_args || "", placeholder: "--fast", onchange: e => save({ extra_args: e.target.value }) });
    box.append(...[
      el("div", { class: "row between wrap" },
        el("div", {}, el("span", { class: `pill comfy-pill cs-${st}` }, t("comfy.state." + st)), " ",
          el("span", { class: "muted small" }, `${s.url}${s.version ? ` · ComfyUI ${s.version}` : ""}${s.pytorch ? ` · torch ${s.pytorch}` : ""}`)),
        el("div", { class: "row gap wrap" },
          s.mode === "managed" && !["running", "busy", "starting"].includes(st) ? el("button", { class: "btn small primary", disabled: !s.installed,
            onclick: act("/api/comfy/start", t("comfy.starting")) }, "▶ " + t("comfy.start")) : null,
          s.mode === "managed" && ["running", "busy", "starting", "error"].includes(st) ? el("button", { class: "btn small danger",
            onclick: act("/api/comfy/stop", t("comfy.stopped")) }, "■ " + t("comfy.stop")) : null,
          s.reachable ? el("button", { class: "btn small", title: t("comfy.freeHelp"), onclick: act("/api/comfy/free",
            r => t("comfy.freed", { a: gbOf(r.comfy_mb_before), b: gbOf(r.comfy_mb_after) })) }, "⏏ " + t("comfy.free")) : null,
          s.reachable ? el("a", { class: "btn small", href: s.url, target: "_blank", rel: "noopener" }, t("comfy.openUi") + " ↗") : null)),
      !s.installed && s.mode === "managed" ? el("p", { class: "warn small" }, t("comfy.notInstalled")) : null,
      s.error ? el("p", { class: "error small" }, s.error) : null,
      el("div", { class: "muted small" }, [
        s.reachable ? t("comfy.queue", { r: s.queue_running || 0, p: s.queue_pending || 0 }) : null,
        s.vram_mb ? t("comfy.vramHeld", { gb: gbOf(s.vram_mb) }) : null,
        s.uptime_s != null ? t("comfy.uptime", { t: fmtDur(s.uptime_s) }) : null,
        lf && lf.ok ? t("comfy.lastFree", { a: gbOf(lf.comfy_mb_before), b: gbOf(lf.comfy_mb_after), why: lf.why || "" }) : null].filter(Boolean).join(" · ")),
      el("p", { class: "muted small" }, t("comfy.help")),
      el("details", { class: "small" }, el("summary", { class: "muted" }, t("comfy.settings")),
        el("div", { class: "form comfy-settings" },
          el("label", { class: "row gap" }, el("span", {}, t("comfy.modeL")), mode),
          set.mode === "external" ? el("label", { class: "row gap" }, el("span", {}, "URL"), url) : null,
          el("label", { class: "row gap", for: "cf-auto" }, auto, el("span", {}, t("comfy.autostart"))),
          el("label", { class: "row gap" }, el("span", {}, t("comfy.vramMode")), vram),
          el("label", { class: "row gap" }, el("span", {}, t("comfy.reserve")), reserve),
          el("label", { class: "row gap", for: "cf-custom" }, custom, el("span", {}, t("comfy.customNodes"))),
          el("p", { class: "warn tiny" }, t("comfy.customWarn")),
          el("label", { class: "row gap" }, el("span", {}, t("comfy.extraArgs")), extra),
          el("p", { class: "muted tiny" }, t("comfy.restartNote")),
          s.command ? el("pre", { class: "log" }, s.command) : null)),
      (s.log || []).length ? el("details", { class: "small" }, el("summary", { class: "muted" }, t("comfy.log")),
        el("pre", { class: "log" }, s.log.slice(-30).join("\n"))) : null].filter(Boolean));
    return box;
  };

  const capChip = c => el("span", { class: "chipx" }, t("comfy.cap." + c));
  A.validationLine = v => {
    if (!v) return null;
    if (!v.checked && v.ok) return el("div", { class: "muted small" }, t("comfy.notChecked"));
    if (v.ok) return el("div", { class: "ok-t small" }, "✓ " + t("comfy.valid") + (v.custom_nodes && v.custom_nodes.length ? ` · ${t("comfy.usesCustom", { n: v.custom_nodes.map(c => c.pack).filter((x, i, a) => a.indexOf(x) === i).join(", ") })}` : ""));
    return el("div", { class: "error small" },
      v.missing_nodes.length ? el("div", {}, t("comfy.missingNodes", { n: v.missing_nodes.join(", ") })) : null,
      v.missing_models.length ? el("div", {}, t("comfy.missingModels", { n: v.missing_models.map(m => `${m.file} → models/${m.folder || "?"}`).join(", ") })) : null,
      v.mapping_errors.length ? el("div", {}, t("comfy.mappingErrors", { n: v.mapping_errors.join("; ") })) : null,
      v.missing_nodes.length ? el("div", { class: "warn tiny" }, t("comfy.neverInstall")) : null);
  };

  A.workflowList = (d, onChange) => {
    const list = (d && d.workflows) || [];
    if (!list.length) return el("p", { class: "muted small" }, t("comfy.noWorkflows"));
    return el("div", { class: "wf-grid" }, list.map(w => {
      const pr = (w.presets || {}).draft || {};
      const est = A.status && A.status.providers ? (A.status.providers.find(p => p.id === w.provider) || {}).presets : null;
      return el("div", { class: "wf-card" },
        w.preview_url ? el("img", { src: w.preview_url, alt: "", loading: "lazy" }) : el("div", { class: "wf-noprev" }, "ComfyUI"),
        el("div", { class: "wf-body" },
          el("b", {}, w.name), el("div", { class: "muted tiny mono" }, w.provider),
          el("div", { class: "row gap wrap" }, (w.capabilities || []).map(capChip),
            el("span", { class: `chipx ${w.commercial ? "ok" : "bad"}` }, `${w.licence || t("comfy.noLicence")}${w.commercial ? "" : " · " + t("comfy.notCommercial")}`),
            w.source === "builtin" ? el("span", { class: "chipx" }, t("comfy.sample")) : null),
          el("div", { class: "muted small" }, `${pr.width || "?"}×${pr.height || "?"} · ${pr.length || "?"} ${t("comfy.frames")} · ${pr.fps || "?"} fps` +
            (est && est.draft ? ` · ${t("ai.estimate", { t: fmtDur(est.draft.estimate) })}` : "")),
          A.validationLine(w.validation),
          el("div", { class: "row gap wrap" },
            el("button", { class: "btn small", onclick: () => A.workflowEditor(w.id, onChange) }, "✎ " + t("comfy.editMapping")),
            el("button", { class: "btn small", title: t("comfy.recheckHelp"), onclick: async () => {
              try { const r = await api("POST", `/api/comfy/workflows/${w.id}/validate`); toast(r.validation.ok ? (r.checked ? t("comfy.valid") : t("comfy.notChecked")) : t("comfy.savedInvalid"), r.validation.ok ? "ok" : "warn"); onChange && onChange(); }
              catch (e) { toast(e.message, "error"); } } }, "↻ " + t("comfy.recheck")),
            el("a", { class: "btn small", href: `/api/comfy/workflows/${w.id}/workflow_api.json`, download: "" }, "⇩ JSON"),
            w.source !== "builtin" ? el("button", { class: "btn small danger", onclick: async () => {
              if (!U().confirmBox(t("comfy.confirmDelete", { n: w.name }))) return;
              try { await api("DELETE", `/api/comfy/workflows/${w.id}`); onChange && onChange(); } catch (e) { toast(e.message, "error"); }
            } }, t("delete")) : null)));
    }));
  };

  A.workflowUpload = onDone => {
    const file = el("input", { type: "file", accept: ".json,application/json" });
    const prev = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp" });
    const name = el("input", { type: "text", placeholder: t("comfy.namePh") });
    const lic = el("input", { type: "text", placeholder: "Apache-2.0 (model …), MIT (…)" });
    const com = el("input", { type: "checkbox", id: `wf-com-${Math.random().toString(36).slice(2, 7)}` });
    const notes = el("textarea", { rows: 2, placeholder: t("comfy.notesPh") });
    const out = el("div", {});
    const form = () => {
      const fd = new FormData();
      fd.append("workflow", file.files[0], file.files[0].name);
      if (prev.files.length) fd.append("preview", prev.files[0], prev.files[0].name);
      fd.append("name", name.value.trim() || file.files[0].name.replace(/\.json$/i, ""));
      fd.append("licence", lic.value.trim()); fd.append("notes", notes.value.trim()); fd.append("commercial", com.checked ? "true" : "false");
      return fd;
    };
    const showDetect = r => {
      const m = r.manifest || r;
      const inp = m.inputs || {};
      out.replaceChildren(el("div", { class: "panel inner" },
        el("b", {}, t("comfy.detected")),
        el("table", { class: "mon-table" }, el("tbody", {}, ((A.status && A.status.comfyKeys) || ["prompt", "negative", "seed", "width", "height", "length", "fps", "steps", "image"]).map(k =>
          el("tr", {}, el("td", {}, t("comfy.key." + k)), el("td", { class: "mono small" }, (inp[k] || []).map(x => `#${x.node}.${x.input}`).join(", ") || el("span", { class: "muted" }, "—")))))),
        el("div", { class: "small" }, `${t("comfy.outputs")}: ${(m.outputs || []).map(x => "#" + x).join(", ") || "—"} · ${t("comfy.caps")}: ${(m.capabilities || []).join(", ") || "—"}`),
        ((r.detected || {}).notes || []).length ? el("ul", { class: "warn small" }, r.detected.notes.map(n => el("li", {}, n))) : null,
        A.validationLine(r.validation)));
    };
    file.addEventListener("change", async () => {
      if (!file.files.length) return;
      if (!name.value) name.value = file.files[0].name.replace(/\.json$/i, "").replace(/[_-]+/g, " ");
      try { showDetect(await api("POST", "/api/comfy/workflows/detect", form(), true)); }
      catch (e) { out.replaceChildren(el("p", { class: "error small" }, e.message)); }
    });
    const btn = el("button", { class: "btn primary", onclick: async () => {
      if (!file.files.length) { file.click(); return; }
      btn.disabled = true;
      try {
        const m = await api("POST", "/api/comfy/workflows", form(), true);
        toast(t("comfy.uploaded", { n: m.name }), "ok");
        file.value = ""; prev.value = ""; name.value = ""; out.replaceChildren();
        onDone && onDone(m);
        A.workflowEditor(m.id, onDone);
      } catch (e) { toast(e.message, "error", 9000); }
      finally { btn.disabled = false; }
    } }, "⇪ " + t("comfy.upload"));
    return el("div", { class: "form ai-form" },
      el("p", { class: "muted small" }, t("comfy.uploadHelp")),
      el("p", { class: "warnbox" }, "⚠ " + t("comfy.customWarn")),
      el("div", { class: "field" }, el("label", {}, t("comfy.file")), file),
      el("div", { class: "row gap wrap" }, el("div", { class: "field" }, el("label", {}, t("comfy.name")), name),
        el("div", { class: "field" }, el("label", {}, t("licence")), lic)),
      el("label", { class: "row gap", for: com.id }, com, el("span", {}, t("comfy.commercial"))),
      el("div", { class: "field" }, el("label", {}, t("comfy.notes")), notes),
      el("div", { class: "field" }, el("label", {}, t("comfy.previewImg")), prev),
      out, el("div", {}, btn));
  };

  // mapping editor (modal)
  A.workflowEditor = async (wid, onChange) => {
    let d;
    try { d = await api("GET", `/api/comfy/workflows/${wid}`); } catch (e) { toast(e.message, "error"); return; }
    const nodes = d.nodes_list || [];
    const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
    const keys = ["prompt", "negative", "seed", "width", "height", "length", "fps", "steps", "image"];
    const map = JSON.parse(JSON.stringify(d.inputs || {}));
    let outs = [...(d.outputs || [])];
    const caps = new Set(d.capabilities || []);
    const presets = JSON.parse(JSON.stringify(d.presets || { draft: {}, final: {} }));
    const mapBox = el("div", { class: "wf-map" });
    const nodeLabel = n => `#${n.id} · ${n.class_type}${n.title && n.title !== n.class_type ? " · " + n.title : ""}`;
    const drawMap = () => {
      mapBox.replaceChildren(el("table", { class: "mon-table wf-map-t" },
        el("thead", {}, el("tr", {}, el("th", {}, t("comfy.input")), el("th", {}, t("comfy.node")), el("th", {}, t("comfy.field")), el("th", {}, t("comfy.value")), el("th", {}, ""))),
        el("tbody", {}, keys.flatMap(k => {
          const rows = (map[k] && map[k].length ? map[k] : [null]);
          return rows.map((tg, i) => {
            const nsel = el("select", { "aria-label": `${t("comfy.key." + k)} ${t("comfy.node")}`, onchange: e => {
              const n = byId[e.target.value];
              const first = n ? Object.keys(n.inputs)[0] : "";
              const guess = n && ({ prompt: "text", negative: "text", seed: n.inputs.noise_seed != null ? "noise_seed" : "seed", image: "image", fps: n.inputs.frame_rate != null ? "frame_rate" : "fps" })[k];
              if (!e.target.value) { map[k] = (map[k] || []).filter((_, j) => j !== i); }
              else { map[k] = map[k] || []; map[k][i] = { node: e.target.value, input: guess && n.inputs[guess] !== undefined ? guess : (n.inputs[k] !== undefined ? k : first) }; }
              drawMap();
            } }, el("option", { value: "" }, "—"), nodes.map(n => el("option", { value: n.id, selected: tg && tg.node === n.id }, nodeLabel(n))));
            const n = tg && byId[tg.node];
            const isel = el("select", { disabled: !n, "aria-label": `${t("comfy.key." + k)} ${t("comfy.field")}`, onchange: e => { map[k][i] = { node: tg.node, input: e.target.value }; drawMap(); } },
              n ? Object.keys(n.inputs).map(x => el("option", { value: x, selected: tg.input === x }, x)) : []);
            const val = n ? n.inputs[tg.input] : "";
            return el("tr", {}, el("td", {}, i === 0 ? t("comfy.key." + k) : ""), el("td", {}, nsel), el("td", {}, isel),
              el("td", { class: "mono tiny wf-val", title: String(val ?? "") }, String(val ?? "").slice(0, 60)),
              el("td", {}, i === 0 && tg ? el("button", { class: "icon-btn", title: t("comfy.addTarget"), "aria-label": t("comfy.addTarget"),
                onclick: () => { map[k].push({ node: "", input: "" }); map[k] = map[k].filter(x => x); drawMap(); } }, "+") : null));
          });
        }))));
    };
    drawMap();
    const outBox = el("div", { class: "row gap wrap" }, nodes.filter(n => /^(Save|Preview|VHS_VideoCombine)/.test(n.class_type)).map(n => {
      const cb = el("input", { type: "checkbox", checked: outs.includes(n.id), onchange: e => { outs = e.target.checked ? [...outs, n.id] : outs.filter(x => x !== n.id); } });
      return el("label", { class: "row gap" }, cb, el("span", { class: "small" }, nodeLabel(n)));
    }));
    const capBox = el("div", { class: "row gap wrap" }, ["t2v", "i2v", "image", "upscale", "interpolate"].map(c => {
      const cb = el("input", { type: "checkbox", checked: caps.has(c), onchange: e => { e.target.checked ? caps.add(c) : caps.delete(c); } });
      return el("label", { class: "row gap" }, cb, el("span", { class: "small" }, t("comfy.cap." + c)));
    }));
    const pin = (pn, k, step) => el("input", { type: "number", class: "narrow", step: step || 1, value: (presets[pn] || {})[k] ?? "",
      "aria-label": `${pn} ${k}`, oninput: e => { presets[pn] = presets[pn] || {}; presets[pn][k] = e.target.value; } });
    const preBox = el("table", { class: "mon-table" }, el("thead", {}, el("tr", {}, el("th", {}, ""), ...["width", "height", "length", "fps", "steps"].map(k => el("th", {}, t("comfy.key." + k))))),
      el("tbody", {}, ["draft", "final"].map(pn => el("tr", {}, el("td", {}, t("ai.presets." + pn)),
        el("td", {}, pin(pn, "width", 16)), el("td", {}, pin(pn, "height", 16)), el("td", {}, pin(pn, "length", 4)),
        el("td", {}, pin(pn, "fps", 1)), el("td", {}, pin(pn, "steps", 1))))));
    const name = el("input", { type: "text", value: d.name || "" });
    const lic = el("input", { type: "text", value: d.licence || "" });
    const com = el("input", { type: "checkbox", id: "wfe-com", checked: !!d.commercial });
    const notes = el("textarea", { rows: 3 }); notes.value = d.notes || "";
    const vram = el("input", { type: "number", class: "narrow", min: 1, max: 32, step: 0.5, value: d.min_vram_gb ?? 6 });
    const valBox = el("div", {}, A.validationLine(d.validation));
    const body = el("div", { class: "wf-editor" },
      el("p", { class: "muted small" }, t("comfy.editorHelp")),
      (d.detected && d.detected.notes || []).length ? el("ul", { class: "warn small" }, d.detected.notes.map(n => el("li", {}, n))) : null,
      el("div", { class: "row gap wrap" }, el("div", { class: "field" }, el("label", {}, t("comfy.name")), name),
        el("div", { class: "field" }, el("label", {}, t("licence")), lic)),
      el("label", { class: "row gap", for: "wfe-com" }, com, el("span", {}, t("comfy.commercial"))),
      el("div", { class: "field" }, el("label", {}, t("comfy.notes")), notes),
      el("h3", {}, t("comfy.mapping")), mapBox,
      el("button", { class: "btn small", onclick: () => { Object.keys(map).forEach(k => delete map[k]); Object.assign(map, JSON.parse(JSON.stringify((d.detected || {}).inputs || {}))); drawMap(); } }, "↺ " + t("comfy.redetect")),
      el("h3", {}, t("comfy.outputs")), outBox,
      el("h3", {}, t("comfy.caps")), capBox,
      el("h3", {}, t("comfy.presets")), el("p", { class: "muted tiny" }, t("comfy.presetsHelp")), preBox,
      el("label", { class: "row gap" }, el("span", {}, t("comfy.minVram")), vram),
      valBox);
    U().modal(`${t("comfy.editTitle")} — ${d.name}`, body, [
      { label: t("cancel"), onClick: () => true },
      { label: t("comfy.save"), primary: true, onClick: () => {
        const clean = {};
        for (const k of keys) { const ts = (map[k] || []).filter(x => x && x.node && x.input); if (ts.length) clean[k] = ts; }
        api("PUT", `/api/comfy/workflows/${wid}`, { name: name.value, licence: lic.value, commercial: com.checked, notes: notes.value,
          inputs: clean, outputs: outs, capabilities: [...caps], presets, min_vram_gb: vram.value })
          .then(r => { toast(t("comfy.saved"), "ok"); onChange && onChange(r); if (!r.validation.ok) toast(t("comfy.savedInvalid"), "warn", 8000); })
          .catch(e => toast(e.message, "error", 9000));
        return true;
      } }]);
  };

  // ---------------------------------------------------------------- portraits
  A.portraitGrid =(list, { selected, onPick, onDelete } = {}) => (list && list.length
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
    const comfyBox = el("div", {});
    const wfBox = el("div", {});
    const wfUpBox = el("div", {});
    let images = [];
    const sec = (title, help, ...kids) => el("section", { class: "panel" },
      el("div", { class: "row between" }, el("h2", {}, title), help ? null : null),
      help ? el("p", { class: "muted small" }, help) : null, ...kids);
    view.append(el("section", { class: "page ai-page" },
      el("div", { class: "page-head" }, el("div", {}, el("h1", {}, t("ai.pageTitle"), " ", A.badge()),
        el("p", { class: "muted" }, t("ai.pageIntro")))),
      el("div", { class: "ai-cols" },
        el("div", { class: "ai-col" }, gpuBox, sec(t("ai.workers"), null, workBox), sec(t("ai.queue"), null, jobsBox),
          sec(t("ai.generateClip"), null, formBox),
          el("section", { class: "panel", id: "comfy" }, el("h2", {}, "ComfyUI"), comfyBox),
          sec(t("comfy.workflows"), t("comfy.workflowsHelp"), wfBox, el("details", { class: "wf-up" }, el("summary", {}, "⇪ " + t("comfy.uploadTitle")), wfUpBox)),
          sec(t("ai.musicTitle"), null, musicBox),
          sec(t("ai.providers"), t("ai.providersHelp"), provBox), sec(t("ai.licences"), null, licBox)),
        el("div", { class: "ai-col" }, sec(t("ai.library"), t("ai.libraryHelp"), libBox),
          sec(t("ai.portraits"), t("ai.portraitsHelp"), portraitsBox, pForms)))));
    const loadWorkers = async () => { workBox.replaceChildren(A.workersCard(await api("GET", "/api/ai/workers"), loadWorkers)); };
    let cfSig = "";
    const loadComfy = async () => {
      const s = await api("GET", "/api/comfy/status");
      A.comfy = s;
      const sig = JSON.stringify([s.state, s.reachable, s.busy, s.queue_running, s.queue_pending, s.vram_mb, s.error, s.last_free && s.last_free.t,
        s.settings, (s.log || []).length && s.log[s.log.length - 1]]);
      if (sig !== cfSig) {      // keep open <details> and focus while nothing changed
        const open = [...comfyBox.querySelectorAll("details")].map(x => x.open);
        cfSig = sig; comfyBox.replaceChildren(A.comfyCard(s, () => { cfSig = ""; loadComfy(); loadWf(); }));
        [...comfyBox.querySelectorAll("details")].forEach((x, i) => { if (open[i]) x.open = true; });
      }
    };
    const loadWf = async () => {
      const d = await api("GET", "/api/comfy/workflows");
      wfBox.replaceChildren(A.workflowList(d, async () => { await A.loadStatus(); loadWf(); formBox.replaceChildren(A.brollForm({ images, onQueued: () => loadJobs() })); provBox.replaceChildren(A.providerTable(A.status), el("p", { class: "muted small" }, t("ai.cloudNote"))); }),
        el("p", { class: "muted tiny" }, d.checked ? t("comfy.checkedAgainst") : t("comfy.notCheckedAll")));
    };
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
      loadComfy().catch(() => {});
      loadWf().catch(() => {});
      wfUpBox.replaceChildren(A.workflowUpload(async () => { await A.loadStatus(); loadWf(); formBox.replaceChildren(A.brollForm({ images, onQueued: () => loadJobs() })); }));
      pForms.replaceChildren(A.portraitUpload({ onDone: () => loadLib() }), A.portraitGenerate({ onQueued: () => loadJobs() }));
      loadLib().catch(e => toast(e.message, "error"));
      loadJobs().catch(() => {});
    })();
    const t1 = setInterval(() => { if (alive) loadJobs().catch(() => {}); }, 2000);
    const t2 = setInterval(() => { if (alive) loadStatus().catch(() => {}); }, 3000);
    const t3 = setInterval(() => { if (alive) loadWorkers().catch(() => {}); }, 4000);
    let wfState = "";
    const t4 = setInterval(() => {
      if (!alive) return;
      loadComfy().then(() => {     // re-validate the workflows when ComfyUI comes up or goes down
        const st = A.comfy ? `${A.comfy.reachable}` : "";
        if (st !== wfState) { wfState = st; loadWf().catch(() => {}); }
      }).catch(() => {});
    }, 3000);
    return () => { alive = false; clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4); };
  };

  window.AIUI = A;
})();
