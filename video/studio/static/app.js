/* Video Studio UI — plain JS, no build step.
 * Routes: #/ (projects)   #/p/<id> (editor)
 */
(() => {
  "use strict";
  const STR = window.STRINGS;
  const t = (key, vars) => {
    let s = key.split(".").reduce((o, k) => (o == null ? o : o[k]), STR);
    if (s == null) s = key;
    if (vars) s = String(s).replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? ""));
    return s;
  };
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  function el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
      else if (v === true) e.setAttribute(k, "");
      else e.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return e;
  }
  const LANGS = ["uz", "ru", "en", "ar"];
  const FORMATS = ["9:16", "16:9"];
  const LAYOUTS = ["hero", "screen", "screens", "chips", "stat", "list", "cta", "presenter"];
  const DEVICES = ["phone", "browser", "none"];
  const fmtBytes = n => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
  const fmtTime = s => { s = Math.max(0, s || 0); return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
  const ago = ts => {
    const d = Date.now() / 1000 - ts;
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.round(d / 60)} min ago`;
    if (d < 86400) return `${Math.round(d / 3600)} h ago`;
    return new Date(ts * 1000).toLocaleDateString();
  };
  document.querySelectorAll("[data-t]").forEach(n => { n.textContent = t(n.dataset.t); });

  // ---------------------------------------------------------------- API
  async function api(method, url, body, isForm) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      if (isForm) opt.body = body;
      else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
    }
    const r = await fetch(url, opt);
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      const msg = data && data.errors ? data.errors.join("; ") : (data && data.detail) || r.statusText;
      const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
      err.status = r.status; err.data = data;
      throw err;
    }
    return data;
  }

  // ---------------------------------------------------------------- toasts, modals
  function toast(msg, kind = "info", ms = 4200) {
    const n = el("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, msg);
    $("#toasts").append(n);
    setTimeout(() => n.remove(), ms);
  }
  function showWarnings(list) {
    if (list && list.length) toast(el("div", {}, el("b", {}, t("warnings") + ": "), list.slice(0, 6).join(" · ")), "warn", 9000);
  }
  function modal(title, body, actions = []) {
    const root = $("#modal-root");
    const prev = document.activeElement;
    const close = () => { wrap.remove(); prev && prev.focus && prev.focus(); };
    const wrap = el("div", { class: "modal-wrap", onclick: e => { if (e.target === wrap) close(); } },
      el("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
        el("div", { class: "modal-head" }, el("h2", {}, title),
          el("button", { class: "icon-btn", "aria-label": t("close"), onclick: close }, "✕")),
        el("div", { class: "modal-body" }, body),
        actions.length ? el("div", { class: "modal-foot" }, actions.map(a => el("button", {
          class: a.primary ? "btn primary" : "btn", onclick: () => { if (a.onClick() !== false) close(); } }, a.label))) : null));
    wrap.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
    root.append(wrap);
    const f = wrap.querySelector("textarea, input, select, .modal-foot .btn, button");
    f && f.focus();
    return close;
  }
  function confirmBox(msg) { return window.confirm(msg); }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); }
    catch { const ta = el("textarea", { style: { position: "fixed", opacity: 0 } }); ta.value = text; document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
    toast(t("copied"), "ok");
  }

  window.SUI = { el, t, api, toast, modal, confirmBox, fmtBytes, ago, $, $$ };

  // ---------------------------------------------------------------- Ovoz status
  const OV = { ok: null, models: [], error: "" };
  async function checkOvoz() {
    try {
      const h = await api("GET", "/api/health");
      const was = OV.ok;
      OV.ok = h.ovoz; OV.error = h.ovoz_error || ""; OV.url = h.ovoz_url;
      if (OV.ok && (!OV.models.length || was === false)) {
        try { OV.models = await api("GET", "/api/voices"); } catch { OV.models = []; }
      }
      if (was !== OV.ok) document.dispatchEvent(new Event("ovoz"));
    } catch { OV.ok = false; OV.error = "studio server not reachable"; }
    const p = $("#ovoz");
    p.className = `pill ${OV.ok ? "ok" : "bad"}`;
    p.textContent = OV.ok ? t("ovozOk") : "Ovoz TTS offline";
    p.title = OV.ok ? OV.url : OV.error;
    const b = $("#banner");
    if (!OV.ok && location.hash.startsWith("#/p/")) { b.hidden = false; b.textContent = t("ovozDown", { url: OV.url || "localhost:8000" }); }
    else b.hidden = true;
  }
  setInterval(checkOvoz, 15000);

  // ---------------------------------------------------------------- router
  let cleanup = null;
  function route() {
    if (cleanup) { cleanup(); cleanup = null; }
    const m = location.hash.match(/^#\/p\/([a-z0-9-]+)/);
    const view = $("#view");
    view.innerHTML = "";
    $("#crumb").innerHTML = "";
    $("#savestate").textContent = "";
    $("#ai-link").classList.toggle("on", location.hash.startsWith("#/ai"));
    if (m) cleanup = Editor(view, m[1]);
    else if (location.hash.startsWith("#/ai")) cleanup = window.AIUI.page(view);
    else cleanup = ProjectsPage(view);
    checkOvoz();
  }
  addEventListener("hashchange", route);

  // ================================================================ projects page
  function ProjectsPage(view) {
    document.title = `${t("projects")} — ${t("appName")}`;
    const fileInput = el("input", { type: "file", multiple: true, accept: ".json,.png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.m4v", hidden: true,
      onchange: () => { if (fileInput.files.length) importFiles([...fileInput.files]); fileInput.value = ""; } });
    const drop = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": t("dropTitle"),
      onclick: () => fileInput.click(), onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } } },
      el("div", { class: "drop-icon", "aria-hidden": "true" }, "⇪"),
      el("div", { class: "drop-title" }, t("dropTitle")),
      el("div", { class: "drop-hint" }, t("dropHint")), fileInput);
    wireDrop(drop, files => importFiles(files));
    const grid = el("div", { class: "cards" });
    const inboxNote = el("p", { class: "muted small" });
    view.append(
      el("section", { class: "page" },
        el("div", { class: "page-head" },
          el("h1", {}, t("projects")),
          el("div", { class: "row gap" },
            el("button", { class: "btn", onclick: aiRequestDialog }, "✦ " + t("copyAiRequest")),
            el("button", { class: "btn", onclick: fromExample }, t("fromExample")),
            el("button", { class: "btn primary", onclick: newProjectDialog }, "+ " + t("newProject")))),
        drop, inboxNote, grid));

    async function load() {
      const list = await api("GET", "/api/projects");
      grid.innerHTML = "";
      if (!list.length) grid.append(el("p", { class: "empty" }, t("noProjects")));
      for (const p of list) {
        const thumb = p.thumb ? el("img", { src: `/api/projects/${p.id}/thumbs/${encodeURIComponent(p.thumb)}`, alt: "", loading: "lazy" }) : el("div", { class: "thumb-ph" }, "▶");
        grid.append(el("article", { class: "card" },
          el("a", { class: "card-link", href: `#/p/${p.id}`, "aria-label": `${t("open")} ${p.title}` },
            el("div", { class: "card-thumb" }, thumb),
            el("div", { class: "card-body" },
              el("h3", {}, p.title || p.id),
              el("div", { class: "muted small" }, `${p.product} · ${t("langs." + p.language)} · ${p.format}`),
              el("div", { class: "muted small" }, [t("scenesCount", { n: p.scenes }), t("assetsCount", { n: p.assets }), t("rendersCount", { n: p.renders })].join(" · ")),
              el("div", { class: "muted small" }, t("updated", { when: ago(p.updated) }) + (p.source !== "ui" ? ` · ${p.source}` : "")))),
          el("button", { class: "icon-btn danger card-del", "aria-label": `${t("delete")} ${p.title}`, title: t("delete"),
            onclick: async () => { if (!confirmBox(t("confirmDeleteProject", { name: p.title }))) return;
              try { await api("DELETE", `/api/projects/${p.id}`); load(); } catch (e) { toast(e.message, "error"); } } }, "🗑")));
      }
    }
    api("GET", "/api/inbox").then(i => { inboxNote.textContent = t("inboxHint", { path: i.path }); }).catch(() => {});
    load().catch(e => toast(e.message, "error"));
    const timer = setInterval(() => load().catch(() => {}), 6000);   // picks up inbox / API imports
    return () => clearInterval(timer);
  }

  function wireDrop(zone, onFiles) {
    let depth = 0;
    zone.addEventListener("dragenter", e => { e.preventDefault(); depth++; zone.classList.add("over"); });
    zone.addEventListener("dragover", e => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; });
    zone.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; zone.classList.remove("over"); } });
    zone.addEventListener("drop", e => {
      e.preventDefault(); depth = 0; zone.classList.remove("over");
      const files = [...(e.dataTransfer.files || [])];
      if (files.length) onFiles(files);
    });
  }

  async function importFiles(files) {
    const fd = new FormData();
    const json = files.find(f => /\.json$/i.test(f.name));
    if (json) fd.append("brief", json, json.name);
    files.filter(f => f !== json).forEach(f => fd.append("files", f, f.name));
    toast(t("uploading", { n: files.length }));
    try {
      const r = await api("POST", "/api/projects?source=ui", fd, true);
      toast(t("created"), "ok");
      showWarnings(r.warnings);
      location.hash = `#/p/${r.id}`;
    } catch (e) { toast(e.message, "error", 9000); }
  }

  async function fromExample() {
    toast(t("creating"));
    try { const r = await api("POST", "/api/projects/example"); location.hash = `#/p/${r.id}`; }
    catch (e) { toast(e.message, "error"); }
  }

  function newProjectDialog() {
    const name = el("input", { type: "text", value: "", placeholder: "Acme Notes", id: "np-name" });
    const lang = el("select", { id: "np-lang" }, LANGS.map(l => el("option", { value: l, selected: l === "en" }, t("langs." + l))));
    const fmt = el("select", { id: "np-fmt" }, FORMATS.map(f => el("option", { value: f }, t("formats." + f))));
    modal(t("newProject"), el("div", { class: "form" },
      el("label", { for: "np-name" }, t("productName")), name,
      el("label", { for: "np-lang" }, t("language")), lang,
      el("label", { for: "np-fmt" }, t("format")), fmt), [
      { label: t("cancel"), onClick: () => {} },
      { label: t("create"), primary: true, onClick: () => {
        api("POST", "/api/projects?source=ui", { blank: true, product: name.value.trim() || "My product", language: lang.value, format: fmt.value })
          .then(r => { location.hash = `#/p/${r.id}`; }).catch(e => toast(e.message, "error"));
      } }]);
  }

  async function aiRequestDialog() {
    let text = "";
    try { text = await api("GET", "/api/ai-request"); } catch (e) { toast(e.message, "error"); return; }
    const ta = el("textarea", { class: "mono tall", spellcheck: "false", "aria-label": t("aiRequestTitle") });
    ta.value = text;
    modal(t("aiRequestTitle"), el("div", {},
      el("p", { class: "muted small" }, t("aiRequestHelp"), el("a", { href: "/BRIEF.md", target: "_blank" }, "BRIEF.md"), " · ",
        el("a", { href: "/api/brief-spec", target: "_blank" }, "JSON Schema")), ta),
      [{ label: t("close"), onClick: () => {} }, { label: t("copy"), primary: true, onClick: () => { copyText(ta.value); } }]);
  }

  // ================================================================ editor
  function Editor(view, pid) {
    const S = {
      pid, project: null, brief: null, assets: {}, renders: [], warnings: [], tl: null,
      sel: 0, tab: "scene", version: 0, savedVersion: 0, saving: false, jobs: {}, alive: true,
    };
    const timers = [];
    let saveTimer = null;

    // ---------- layout skeleton ----------
    const sceneList = el("ol", { class: "scene-list", "aria-label": t("scenes") });
    const addSel = el("select", { "aria-label": t("layout") }, LAYOUTS.map(l => el("option", { value: l }, t("layouts." + l))));
    const left = el("aside", { class: "col col-scenes" },
      el("div", { class: "col-head" }, el("h2", {}, t("scenes")), el("span", { class: "muted small", id: "total-len" })),
      sceneList,
      el("div", { class: "add-scene" }, addSel, el("button", { class: "btn", onclick: () => addScene(addSel.value) }, "+ " + t("addScene"))));
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const panel = el("div", { class: "tab-panel", role: "tabpanel" });
    const mid = el("section", { class: "col col-form" }, tabs, panel);
    const settings = el("div", { class: "settings" });
    const previewBox = el("div", { class: "preview-box" });
    const transport = el("div", { class: "transport" });
    const actions = el("div", { class: "actions" });
    const jobBox = el("div", { class: "jobs", "aria-live": "polite" });
    const makeBox = el("div", { class: "make", "aria-live": "polite" });
    const rendersBox = el("div", { class: "renders" });
    const right = el("section", { class: "col col-preview", "aria-label": t("preview") }, previewBox, transport, actions, makeBox, jobBox, rendersBox);
    view.append(el("div", { class: "editor" }, settings, el("div", { class: "editor-grid" }, left, mid, right)));

    // ---------- save ----------
    function setSave(state) { $("#savestate").textContent = state ? t(state) : ""; $("#savestate").className = `savestate ${state || ""}`; }
    function changed(opts = {}) {
      S.version++;
      setSave("saving");
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, opts.now ? 0 : 600);
      if (opts.list) renderSceneList();
    }
    async function save() {
      if (S.saving) { saveTimer = setTimeout(save, 300); return; }
      S.saving = true;
      const v = S.version;
      try {
        const r = await api("PUT", `/api/projects/${pid}`, { brief: S.brief });
        S.savedVersion = v;
        S.warnings = r.warnings || [];
        // keep the local brief object (form closures reference it); the server copy is normalised
        setSave(S.version === v ? "saved" : "saving");
        renderWarnings();
        await refreshTimeline();
      } catch (e) {
        setSave("saveFailed");
        toast(`${t("saveFailed")}: ${e.message}`, "error");
      } finally { S.saving = false; }
    }

    // ---------- data ----------
    async function loadProject() {
      const d = await api("GET", `/api/projects/${pid}`);
      S.project = d; S.brief = d.brief; S.assets = d.assets; S.renders = d.renders; S.warnings = d.warnings;
      document.title = `${S.brief.title} — ${t("appName")}`;
      for (const j of d.jobs || []) if (j.status === "queued" || j.status === "running") trackJob(j);
      S.aiJobs = d.ai_jobs || [];
      S.make = d.make && d.make.id ? d.make : null;
      for (const j of S.aiJobs) S.aiPrev[j.id] = j.status;
    }
    async function refreshTimeline() {
      try {
        S.tl = await api("GET", `/api/projects/${pid}/timeline`);
        preview.load(S.tl);
        renderSceneList();
        renderActions();
        if (S.tab === "ai") updatePresenterStatus();
      } catch (e) { toast(e.message, "error"); }
    }
    async function refreshAssets(adopt = false) {
      const d = await api("GET", `/api/projects/${pid}`);
      S.assets = d.assets; S.warnings = d.warnings; S.renders = d.renders;
      if (adopt && S.version === S.savedVersion) adoptBrief(d.brief);
    }
    // Replace the whole brief (server changed it) and rebuild every view that holds references.
    function adoptBrief(nb) {
      const selId = S.brief.scenes[S.sel] && S.brief.scenes[S.sel].id;
      S.brief = nb;
      const i = S.brief.scenes.findIndex(x => x.id === selId);
      S.sel = i >= 0 ? i : Math.min(S.sel, S.brief.scenes.length - 1);
      renderCrumb(); renderSettings(); renderSceneList(); renderPanel();
      preview.setFormat(S.brief.format);
    }

    // ---------- crumb + settings bar ----------
    function renderCrumb() {
      const title = el("input", { class: "title-input", value: S.brief.title, "aria-label": t("title"),
        oninput: e => { S.brief.title = e.target.value; document.title = `${e.target.value} — ${t("appName")}`; changed(); } });
      $("#crumb").replaceChildren(el("a", { href: "#/", class: "back" }, "← " + t("back")), title);
    }
    function field(label, input, cls = "") {
      const id = `f-${Math.random().toString(36).slice(2, 8)}`;
      input.id = id;
      return el("div", { class: `field ${cls}` }, el("label", { for: id }, label), input);
    }
    function modelsFor(lang) { return OV.models.filter(m => (m.languages || []).includes(lang)); }
    function voicesFor(m, lang) { return (m && m.voices || []).filter(v => !v.lang || v.lang === lang || v.cross_lingual); }
    function fixVoice() {
      const v = S.brief.voice, lang = S.brief.language;
      if (!OV.models.length) return false;
      let m = OV.models.find(x => x.id === v.model);
      let changedAny = false;
      if (!m || !(m.languages || []).includes(lang) || !m.available) {
        const cands = modelsFor(lang).filter(x => x.available);
        m = cands.find(x => x.id === "edge") || cands[0];
        if (m) { v.model = m.id; changedAny = true; }
      }
      const vs = voicesFor(m, lang);
      if (m && !vs.find(x => x.id === v.voice)) {
        const pick = vs.find(x => x.lang === lang) || vs[0];
        if (pick) { v.voice = pick.id; changedAny = true; }
      }
      if (m && v.style && !(m.styles || []).find(s => s.id === v.style)) { delete v.style; changedAny = true; }
      return changedAny;
    }
    function renderSettings() {
      const b = S.brief;
      const product = el("input", { type: "text", value: b.product, oninput: e => { b.product = e.target.value; changed(); } });
      const lang = el("select", { onchange: e => { b.language = e.target.value; fixVoice(); renderSettings(); changed(); } },
        LANGS.map(l => el("option", { value: l, selected: l === b.language }, t("langs." + l))));
      const fmt = el("select", { onchange: e => { b.format = e.target.value; preview.setFormat(b.format); changed(); } },
        FORMATS.map(f => el("option", { value: f, selected: f === b.format }, t("formats." + f))));
      const ms = modelsFor(b.language);
      const model = el("select", { onchange: e => { b.voice.model = e.target.value; fixVoice(); renderSettings(); changed(); } },
        OV.models.length ? ms.map(m => el("option", { value: m.id, selected: m.id === b.voice.model, disabled: !m.available },
          m.name + (m.available ? "" : " (unavailable)"))) : el("option", { value: b.voice.model }, b.voice.model));
      const cur = OV.models.find(m => m.id === b.voice.model);
      const voice = el("select", { onchange: e => { b.voice.voice = e.target.value; changed(); } },
        cur ? voicesFor(cur, b.language).map(v => el("option", { value: v.id, selected: v.id === b.voice.voice }, v.name))
          : el("option", { value: b.voice.voice }, b.voice.voice));
      const rate = el("input", { type: "number", min: -50, max: 100, step: 5, value: b.voice.rate || 0, class: "narrow",
        onchange: e => { b.voice.rate = Math.max(-50, Math.min(100, parseInt(e.target.value, 10) || 0)); changed(); } });
      const styles = cur && cur.styles && cur.styles.length ? el("select", { onchange: e => { if (e.target.value) b.voice.style = e.target.value; else delete b.voice.style; changed(); } },
        el("option", { value: "" }, t("none")), cur.styles.map(s => el("option", { value: s.id, selected: s.id === b.voice.style }, s.name))) : null;
      const color = k => el("input", { type: "color", value: b.brand[k], "aria-label": t(k), title: t(k),
        oninput: e => { b.brand[k] = e.target.value; changed(); } });
      const imgs = Object.entries(S.assets).filter(([, m]) => m.type === "image").map(([n]) => n);
      const logo = el("select", { onchange: e => { if (e.target.value) b.brand.logo = e.target.value; else delete b.brand.logo; changed(); } },
        el("option", { value: "" }, t("none")), imgs.map(n => el("option", { value: n, selected: n === b.brand.logo }, n)));
      const caps = el("input", { type: "checkbox", checked: b.captions !== false, onchange: e => { b.captions = e.target.checked; changed(); } });
      settings.replaceChildren(...[
        field(t("product"), product), field(t("language"), lang), field(t("format"), fmt),
        field(t("voiceModel"), model), field(t("voice"), voice), field(t("rate"), rate, "narrow"),
        styles ? field(t("style"), styles) : null,
        el("div", { class: "field colours" }, el("span", { class: "lbl" }, t("colours")), el("div", { class: "row" }, color("primary"), color("accent"), color("background"))),
        field(t("logo"), logo), field(t("captions"), caps, "check")].filter(Boolean));
      if (cur && !cur.available) settings.append(el("div", { class: "warn small" }, t("ovozModelUnavailable", { reason: cur.unavailable_reason || "" })));
    }

    // ---------- warnings ----------
    const warnBox = el("div", { class: "warnbox", hidden: true });
    function renderWarnings() {
      warnBox.hidden = !S.warnings.length;
      warnBox.replaceChildren(el("b", {}, t("warnings")), el("ul", {}, S.warnings.map(w => el("li", {}, w))));
    }

    // ---------- scene list ----------
    function sceneTl(id) { return S.tl && S.tl.scenes.find(s => s.id === id); }
    function renderSceneList() {
      sceneList.replaceChildren();
      const scenes = S.brief.scenes;
      scenes.forEach((sc, i) => {
        const st = sceneTl(sc.id);
        const voiced = st && st.voiced;
        const hasSay = !!(sc.say || "").trim();
        const li = el("li", { class: `scene-item ${i === S.sel ? "sel" : ""}`, draggable: "true", "data-i": i },
          el("button", { class: "scene-main", "aria-current": i === S.sel ? "true" : null,
            onclick: () => select(i),
            onkeydown: e => {
              if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); move(i, e.key === "ArrowUp" ? -1 : 1); }
            } },
            el("span", { class: "idx" }, String(i + 1)),
            el("span", { class: "scene-txt" },
              el("span", { class: "scene-top" }, el("span", { class: `lay lay-${sc.layout}` }, t("layouts." + sc.layout)),
                sc.background && sc.background.type === "broll" ? el("span", { class: "lay lay-ai", title: t("ai.bgBroll") }, "✦ bg") : null,
                el("span", { class: "sid" }, sc.id),
                st ? el("span", { class: "dur muted" }, `${(st.end - st.start).toFixed(1)}s`) : null),
              el("span", { class: "scene-say" }, sc.say || sc.headline || "—"))),
          el("div", { class: "scene-tools" },
            hasSay ? el("button", { class: `icon-btn voice-dot ${voiced ? "on" : ""}`, title: voiced ? `${t("listen")} (${t("voiced")})` : t("generateThis"),
              "aria-label": voiced ? `${t("listen")} ${sc.id}` : `${t("generateThis")} ${sc.id}`, onclick: () => listen(sc.id, voiced) }, voiced ? "▶" : "◌") : null,
            el("button", { class: "icon-btn", title: t("moveUp"), "aria-label": `${t("moveUp")} ${sc.id}`, disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
            el("button", { class: "icon-btn", title: t("moveDown"), "aria-label": `${t("moveDown")} ${sc.id}`, disabled: i === scenes.length - 1, onclick: () => move(i, 1) }, "↓"),
            el("button", { class: "icon-btn", title: t("duplicate"), "aria-label": `${t("duplicate")} ${sc.id}`, onclick: () => duplicate(i) }, "⧉"),
            el("button", { class: "icon-btn danger", title: t("delete"), "aria-label": `${t("delete")} ${sc.id}`, disabled: scenes.length <= 1, onclick: () => removeScene(i) }, "✕")));
        li.addEventListener("dragstart", e => { e.dataTransfer.setData("text/scene", String(i)); e.dataTransfer.effectAllowed = "move"; li.classList.add("dragging"); });
        li.addEventListener("dragend", () => li.classList.remove("dragging"));
        li.addEventListener("dragover", e => { if ([...e.dataTransfer.types].includes("text/scene")) { e.preventDefault(); li.classList.add("dropto"); } });
        li.addEventListener("dragleave", () => li.classList.remove("dropto"));
        li.addEventListener("drop", e => {
          li.classList.remove("dropto");
          const from = parseInt(e.dataTransfer.getData("text/scene"), 10);
          if (Number.isNaN(from) || from === i) return;
          e.preventDefault();
          const [sc2] = S.brief.scenes.splice(from, 1);
          S.brief.scenes.splice(i, 0, sc2);
          S.sel = i;
          changed({ list: true, now: true });
        });
        sceneList.append(li);
      });
      $("#total-len", left).textContent = S.tl ? `${S.tl.duration.toFixed(1)} s` : "";
    }
    function select(i, seekTo = true) {
      S.sel = Math.max(0, Math.min(i, S.brief.scenes.length - 1));
      renderSceneList();
      if (S.tab !== "scene") setTab("scene"); else renderPanel();
      const st = sceneTl(S.brief.scenes[S.sel].id);
      if (seekTo && st) preview.seek(Math.min(st.start + 2.4, st.end - 0.35));
      const btn = $$(".scene-main", sceneList)[S.sel];
      btn && btn.focus({ preventScroll: false });
    }
    function move(i, d) {
      const j = i + d;
      if (j < 0 || j >= S.brief.scenes.length) return;
      const a = S.brief.scenes;
      [a[i], a[j]] = [a[j], a[i]];
      S.sel = j;
      changed({ list: true, now: true });
      setTimeout(() => { const b = $$(".scene-main", sceneList)[j]; b && b.focus(); }, 0);
    }
    function uniqueId(base) {
      const ids = new Set(S.brief.scenes.map(s => s.id));
      let id = base, n = 2;
      while (ids.has(id)) id = `${base}-${n++}`;
      return id;
    }
    function duplicate(i) {
      const c = JSON.parse(JSON.stringify(S.brief.scenes[i]));
      c.id = uniqueId(c.id.replace(/-\d+$/, ""));
      S.brief.scenes.splice(i + 1, 0, c);
      S.sel = i + 1;
      changed({ list: true, now: true });
      renderPanel();
    }
    function removeScene(i) {
      if (S.brief.scenes.length <= 1) return;
      if (!confirmBox(t("confirmDeleteScene", { id: S.brief.scenes[i].id }))) return;
      S.brief.scenes.splice(i, 1);
      S.sel = Math.min(S.sel, S.brief.scenes.length - 1);
      changed({ list: true, now: true });
      renderPanel();
    }
    function addScene(layout) {
      const firstImg = Object.entries(S.assets).find(([, m]) => m.type === "image");
      const sc = { id: uniqueId(layout), layout, say: "" };
      if (layout === "hero" || layout === "cta") sc.headline = S.brief.product;
      if (layout === "screen") { sc.device = "phone"; sc.asset = firstImg ? firstImg[0] : ""; sc.headline = "New feature"; }
      if (layout === "screens") { sc.device = "phone"; sc.items = Object.entries(S.assets).filter(([, m]) => m.type === "image").slice(0, 2).map(([n]) => ({ asset: n })); sc.headline = "Feature tour"; }
      if (layout === "chips" || layout === "list") { sc.headline = "Highlights"; sc.items = ["One", "Two", "Three"]; }
      if (layout === "stat") { sc.stat = "3x"; sc.headline = "faster"; }
      if (layout === "presenter") { sc.headline = S.brief.product; }
      const at = S.sel + 1;
      S.brief.scenes.splice(at, 0, sc);
      S.sel = at;
      changed({ list: true, now: true });
      setTab("scene");
    }

    // ---------- tabs ----------
    function setTab(name) {
      S.tab = name;
      tabs.replaceChildren(...["scene", "assets", "ai", "json"].map(k => el("button", {
        class: `tab ${k === name ? "on" : ""}`, role: "tab", "aria-selected": k === name ? "true" : "false",
        onclick: () => setTab(k) }, t({ scene: "tabScene", assets: "tabAssets", ai: "tabAi", json: "tabJson" }[k]) +
        (k === "assets" ? ` (${Object.keys(S.assets).length})` : ""))));
      renderPanel();
      if (name === "ai") pollAi();
    }
    function renderPanel() {
      if (S.tab === "scene") renderSceneForm();
      else if (S.tab === "assets") renderAssets();
      else if (S.tab === "ai") renderAiTab();
      else renderJson();
    }

    // ---------- scene form ----------
    function renderSceneForm() {
      const sc = S.brief.scenes[S.sel];
      if (!sc) { panel.replaceChildren(el("p", { class: "empty" }, t("noScene"))); return; }
      const f = el("div", { class: "form scene-form" });
      const upd = (k, v, opts) => { if (v === "" || v == null) delete sc[k]; else sc[k] = v; changed(opts); };
      const text = (k, label, opts = {}) => {
        const inp = el(opts.area ? "textarea" : "input", { type: "text", rows: opts.rows || 2, placeholder: opts.ph || "",
          oninput: e => upd(k, e.target.value, { list: opts.list }) });
        inp.value = sc[k] || "";
        return field(label, inp, opts.cls);
      };
      const num = (k, label, min, max, step, ph) => {
        const inp = el("input", { type: "number", min, max, step, placeholder: ph || "", class: "narrow",
          onchange: e => upd(k, e.target.value === "" ? null : Math.max(min, Math.min(max, parseFloat(e.target.value)))) });
        inp.value = sc[k] ?? "";
        return field(label, inp, "narrow");
      };
      // layout + id
      const lay = el("select", { onchange: e => { sc.layout = e.target.value;
        if ((sc.layout === "screen" || sc.layout === "screens") && !sc.device) sc.device = "phone";
        if (sc.layout === "screens" && !Array.isArray(sc.items)) sc.items = [];
        if (sc.layout === "screens") sc.items = (sc.items || []).map(x => (typeof x === "string" ? { asset: x } : x)).filter(x => x && x.asset);
        if ((sc.layout === "chips" || sc.layout === "list") && Array.isArray(sc.items)) sc.items = sc.items.map(x => (typeof x === "string" ? x : x.label || x.asset));
        changed({ list: true, now: true }); renderSceneForm(); } },
        LAYOUTS.map(l => el("option", { value: l, selected: l === sc.layout }, t("layouts." + l))));
      const idIn = el("input", { type: "text", value: sc.id, pattern: "[a-z0-9-]+",
        onchange: e => { const v = e.target.value.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "") || sc.id;
          sc.id = v === sc.id ? v : uniqueId(v); e.target.value = sc.id; changed({ list: true }); } });
      f.append(el("div", { class: "row gap wrap" }, field(t("layout"), lay), field(t("sceneId"), idIn)),
        el("p", { class: "muted small help" }, t("layoutHelp." + sc.layout)));
      // say
      const sayIn = el("textarea", { rows: 3, class: "say", dir: S.brief.language === "ar" ? "rtl" : "auto" });
      sayIn.value = sc.say || "";
      const counter = el("div", { class: "muted small counter" });
      const count = () => {
        const n = (sayIn.value.trim().match(/\S+/g) || []).length;
        const est = n ? Math.max(1, sayIn.value.trim().length / 14 + 0.25) : 0;
        counter.textContent = (n > 18 ? t("tooManyWords", { n }) : t("words", { n })) + (n ? ` · ${t("estSeconds", { s: est.toFixed(1) })}` : "");
        counter.classList.toggle("warn", n > 18);
      };
      sayIn.addEventListener("input", () => { sc.say = sayIn.value; count(); changed({ list: true }); });
      count();
      const st = sceneTl(sc.id);
      const vbtn = (sc.say || "").trim() ? el("button", { class: "btn small", onclick: () => listen(sc.id, st && st.voiced) },
        st && st.voiced ? "▶ " + t("listen") : "◌ " + t("generateThis")) : null;
      f.append(el("div", { class: "field" }, el("label", { for: "say-in" }, t("say")), Object.assign(sayIn, { id: "say-in" }),
        el("div", { class: "row between" }, counter, vbtn)));
      // layout fields
      const L = sc.layout;
      if (L === "hero") f.append(text("kicker", t("kicker")), text("headline", t("headline")), text("sub", t("sub")));
      if (L === "chips" || L === "list") {
        f.append(text("headline", t("headline")));
        const items = el("textarea", { rows: 5, oninput: e => { sc.items = e.target.value.split("\n").map(s => s.trim()).filter(Boolean); changed(); } });
        items.value = (sc.items || []).map(x => (typeof x === "string" ? x : x.label || x.asset || "")).join("\n");
        f.append(field(t("items"), items));
      }
      if (L === "stat") f.append(text("stat", t("stat"), { cls: "narrow" }), text("headline", t("headline")), text("sub", t("sub")));
      if (L === "cta") f.append(text("headline", t("headline")), text("sub", t("sub")), text("button", t("button"), { ph: S.brief.product }), text("url", t("url")));
      if (L === "presenter") {
        f.append(text("kicker", t("kicker")), text("headline", t("headline")), text("sub", t("sub")));
        const pst = S.tl && S.tl.presenter;
        f.append(el("p", { class: "muted small" }, !pst || !pst.portrait_url ? t("ai.noPortrait") : pst.ready ? t("ai.presenterReady") : pst.stale ? t("ai.presenterStale") : t("ai.presenterMissing"),
          " ", el("button", { class: "btn small", onclick: () => setTab("ai") }, t("ai.presenter"))));
      }
      if (L === "screen" || L === "screens") {
        const dev = el("div", { class: "seg", role: "radiogroup", "aria-label": t("device") }, DEVICES.map(d => el("button", {
          class: `seg-btn ${sc.device === d ? "on" : ""}`, role: "radio", "aria-checked": sc.device === d ? "true" : "false",
          onclick: () => { sc.device = d; changed(); renderSceneForm(); } }, t("devices." + d))));
        f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("device")), dev));
      }
      if (L === "screen") {
        f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("asset")),
          assetPicker(sc.asset, ["image", "video"], name => { sc.asset = name; delete sc.focus; changed({ list: true }); renderSceneForm(); })));
        if (sc.asset && S.assets[sc.asset]) f.append(focusPicker(sc));
        f.append(text("callout", t("callout")), text("headline", t("headline")), text("kicker", t("kicker")), text("sub", t("sub")));
        if (sc.device === "browser") f.append(text("url", t("urlBar"), { ph: "app.example.com" }));
        const adv = el("div", { class: "row gap wrap" }, num("zoom", t("zoom"), 1, 4, 0.1, "auto"));
        if (S.assets[sc.asset] && S.assets[sc.asset].type === "video") adv.append(num("trim_start", t("trimStart"), 0, 600, 0.1, "0"));
        f.append(adv);
      }
      if (L === "screens") {
        f.append(text("headline", t("headline")), text("sub", t("sub")));
        const box = el("div", { class: "screens-items" });
        (sc.items || []).forEach((it, k) => {
          const lab = el("input", { type: "text", value: it.label || "", placeholder: t("label"), "aria-label": `${t("label")} ${k + 1}`,
            oninput: e => { if (e.target.value) it.label = e.target.value; else delete it.label; changed(); } });
          box.append(el("div", { class: "screens-item" },
            el("div", { class: "row between" }, el("b", {}, `#${k + 1}`), lab,
              el("button", { class: "icon-btn danger", "aria-label": `${t("remove")} ${k + 1}`, onclick: () => { sc.items.splice(k, 1); changed(); renderSceneForm(); } }, "✕")),
            assetPicker(it.asset, ["image", "video"], name => { it.asset = name; changed(); renderSceneForm(); })));
        });
        if ((sc.items || []).length < 3) box.append(el("button", { class: "btn small", onclick: () => {
          const imgs = Object.keys(S.assets);
          sc.items = sc.items || [];
          sc.items.push({ asset: imgs[sc.items.length % Math.max(1, imgs.length)] || "" });
          changed(); renderSceneForm(); } }, "+ " + t("addScreen")));
        f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("screensItems")), box));
      }
      f.append(backgroundSection(sc));
      f.append(el("div", { class: "row gap wrap" }, num("pad", t("pad"), 0, 5, 0.1, S.sel === S.brief.scenes.length - 1 ? "1.0" : "0.4"),
        num("duration", t("minDuration"), 1, 30, 0.5, "auto")));
      panel.replaceChildren(warnBox, f);
      renderWarnings();
    }

    function assetPicker(current, types, onPick) {
      const names = Object.entries(S.assets).filter(([, m]) => types.includes(m.type)).map(([n]) => n);
      if (!names.length) return el("p", { class: "muted small" }, t("noAssets"));
      const box = el("div", { class: "picker", role: "radiogroup", "aria-label": t("pickAsset") });
      if (current && !S.assets[current]) box.append(el("div", { class: "pick missing", title: current }, el("span", {}, "⚠ " + current)));
      for (const n of names) {
        const m = S.assets[n];
        box.append(el("button", { class: `pick ${n === current ? "on" : ""}`, role: "radio", "aria-checked": n === current ? "true" : "false",
          title: `${n} (${m.w}×${m.h}${m.duration ? `, ${m.duration}s` : ""})`, onclick: () => onPick(n) },
          el("img", { src: `/api/projects/${pid}/thumbs/${encodeURIComponent(n)}`, alt: "", loading: "lazy" }),
          m.type === "video" ? el("span", { class: "badge" }, "▶ " + (m.duration ? `${m.duration.toFixed(1)}s` : "")) : null,
          el("span", { class: "pick-name" }, n)));
      }
      return box;
    }

    // focus picker: drag a rectangle on the image (fractions 0..1)
    function focusPicker(sc) {
      const m = S.assets[sc.asset];
      const src = m.type === "video" ? `/api/projects/${pid}/thumbs/${encodeURIComponent(sc.asset)}` : `/api/projects/${pid}/files/${encodeURIComponent(sc.asset)}`;
      const img = el("img", { src, alt: sc.asset, draggable: "false" });
      const rect = el("div", { class: "focus-rect" });
      const stage = el("div", { class: `focus-stage ${m.w > m.h ? "wide" : ""}`, tabindex: "0", "aria-label": t("focusHelp") }, img, rect);
      const show = () => {
        const f = sc.focus;
        rect.hidden = !f;
        if (f) Object.assign(rect.style, { left: `${f.x * 100}%`, top: `${f.y * 100}%`, width: `${f.w * 100}%`, height: `${f.h * 100}%` });
      };
      show();
      let start = null;
      const pos = e => { const r = img.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }; };
      stage.addEventListener("pointerdown", e => { e.preventDefault(); stage.setPointerCapture(e.pointerId); start = pos(e); });
      stage.addEventListener("pointermove", e => {
        if (!start) return;
        const p = pos(e);
        sc.focus = { x: Math.min(start.x, p.x), y: Math.min(start.y, p.y), w: Math.abs(p.x - start.x), h: Math.abs(p.y - start.y) };
        show();
      });
      stage.addEventListener("pointerup", () => {
        if (!start) return;
        start = null;
        if (!sc.focus || sc.focus.w < 0.02 || sc.focus.h < 0.01) delete sc.focus;
        else for (const k of "xywh") sc.focus[k] = Math.round(sc.focus[k] * 10000) / 10000;
        show(); changed(); info();
      });
      const infoEl = el("span", { class: "muted small mono" });
      const info = () => { infoEl.textContent = sc.focus ? `x ${sc.focus.x.toFixed(2)} · y ${sc.focus.y.toFixed(2)} · w ${sc.focus.w.toFixed(2)} · h ${sc.focus.h.toFixed(2)}` : ""; };
      info();
      return el("div", { class: "field" }, el("span", { class: "lbl" }, t("focus")),
        el("p", { class: "muted small" }, t("focusHelp")), stage,
        el("div", { class: "row between" }, infoEl, el("button", { class: "btn small", onclick: () => { delete sc.focus; show(); info(); changed(); } }, t("clearFocus"))));
    }

    // ---------- assets tab ----------
    function usedIn(name) {
      const out = [];
      S.brief.scenes.forEach(s => {
        if (s.asset === name || (s.items || []).some(i => i && i.asset === name)) out.push(s.id);
      });
      if (S.brief.brand.logo === name) out.push("logo");
      return out;
    }
    function renderAssets() {
      const input = el("input", { type: "file", multiple: true, hidden: true, accept: ".png,.jpg,.jpeg,.webp,.mp4,.mov,.webm,.m4v",
        onchange: () => { if (input.files.length) uploadAssets([...input.files]); input.value = ""; } });
      const zone = el("div", { class: "dropzone small", tabindex: "0", role: "button", onclick: () => input.click(),
        onkeydown: e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } } },
        el("b", {}, "⇪ " + t("upload")), el("span", { class: "muted small" }, " — PNG, JPG, WebP, MP4, MOV, WebM"), input);
      wireDrop(zone, uploadAssets);
      const grid = el("div", { class: "asset-grid" });
      for (const [n, m] of Object.entries(S.assets)) {
        const used = usedIn(n);
        grid.append(el("figure", { class: "asset" },
          el("img", { src: `/api/projects/${pid}/thumbs/${encodeURIComponent(n)}`, alt: n, loading: "lazy" }),
          el("figcaption", {},
            el("b", { class: "asset-name", title: n }, n),
            el("span", { class: "muted small" }, `${m.type} · ${m.w}×${m.h}${m.duration ? ` · ${m.duration.toFixed(1)} s` : ""} · ${fmtBytes(m.size)}`),
            el("span", { class: `small ${used.length ? "ok-t" : "muted"}` }, used.length ? t("usedIn", { list: used.join(", ") }) : t("unused")),
            el("button", { class: "btn small danger", onclick: async () => {
              if (!confirmBox(t("confirmDeleteAsset", { name: n }))) return;
              try { const r = await api("DELETE", `/api/projects/${pid}/assets/${encodeURIComponent(n)}`); S.warnings = r.warnings; await refreshAssets(); renderSettings(); setTab("assets"); refreshTimeline(); }
              catch (e) { toast(e.message, "error"); } } }, t("delete")))));
      }
      panel.replaceChildren(zone, grid);
    }
    async function uploadAssets(files) {
      const fd = new FormData();
      files.forEach(f => fd.append("files", f, f.name));
      toast(t("uploading", { n: files.length }));
      try {
        const r = await api("POST", `/api/projects/${pid}/assets`, fd, true);
        if (r.errors && r.errors.length) toast(r.errors.join("; "), "error", 9000);
        await flush();
        await refreshAssets(true);
        renderSettings();
        setTab(S.tab);
        refreshTimeline();
      } catch (e) { toast(e.message, "error", 9000); }
    }

    // ---------- JSON tab ----------
    function renderJson() {
      const ta = el("textarea", { class: "mono json", spellcheck: "false", "aria-label": t("tabJson") });
      ta.value = JSON.stringify(S.brief, null, 2);
      const err = el("div", { class: "error small", role: "alert" });
      panel.replaceChildren(warnBox, el("div", { class: "row gap wrap" },
        el("button", { class: "btn primary", onclick: async () => {
          let raw;
          try { raw = JSON.parse(ta.value); } catch (e) { err.textContent = t("jsonInvalid", { msg: e.message }); return; }
          try {
            const r = await api("PUT", `/api/projects/${pid}`, { brief: raw });
            S.warnings = r.warnings; S.version++; S.savedVersion = S.version;
            err.textContent = ""; toast(t("jsonApplied"), "ok"); showWarnings(r.warnings);
            adoptBrief(r.brief); refreshTimeline();
          } catch (e) { err.textContent = e.message; }
        } }, t("applyJson")),
        el("button", { class: "btn", onclick: () => copyText(ta.value) }, t("copy")),
        el("button", { class: "btn", onclick: () => {
          const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify(S.brief, null, 2)], { type: "application/json" })), download: "brief.json" });
          a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
        } }, t("downloadJson")),
        el("button", { class: "btn", onclick: renderJson }, t("resetJson"))), err, ta);
      renderWarnings();
    }

    // ---------- AI: libraries, jobs, presenter ----------
    const AI = window.AIUI;
    S.aiJobs = []; S.aiPrev = {}; S.lib = null; S.portraits = null; S.pstate = null; S.aiBoxes = null; S.pendBox = null;
    let aiTimer = null;
    async function loadLib() { S.lib = await api("GET", "/api/library/broll"); return S.lib; }
    async function loadPortraits() { S.portraits = await api("GET", "/api/library/portraits"); return S.portraits; }
    const isActive = j => j.status === "queued" || j.status === "running";
    const mine = j => j.project === pid || (j.params && ((j.params.assign && j.params.assign.project === pid) || j.params.assign_project === pid));
    function ensurePres() {
      if (!S.brief.presenter) S.brief.presenter = { enabled: true, mode: "corner", scenes: "all", quality: "draft" };
      return S.brief.presenter;
    }
    function onAiJob(j) {
      S.aiJobs = [j, ...S.aiJobs.filter(x => x.id !== j.id)];
      S.aiPrev[j.id] = j.status;
      if (j.status === "done") handleDone(j);
      refreshAiViews();
      pollAi();
    }
    async function pollAi() {
      clearTimeout(aiTimer);
      if (!S.alive) return;
      try {
        const js = await api("GET", "/api/ai/jobs");
        S.aiJobs = js;
        for (const j of js) {
          const was = S.aiPrev[j.id];
          if (was && was !== j.status && mine(j)) {
            if (j.status === "done") handleDone(j);
            else if (j.status === "error") toast(t("jobFailed", { msg: j.error }), "error", 12000);
          }
          S.aiPrev[j.id] = j.status;
        }
        if (S.tab === "ai") { await AI.loadStatus(); }
        refreshAiViews();
      } catch { /* server restarting */ }
      if (S.alive && (S.tab === "ai" || S.aiJobs.some(j => isActive(j) && mine(j)) || (S.make && S.make.status === "running"))) aiTimer = setTimeout(pollAi, 2000);
    }
    async function handleDone(j) {
      const r = j.result || {};
      if (j.kind === "broll" && r.clip) {
        toast(r.cached ? t("ai.reused") : t("ai.clipReady"), "ok");
        await loadLib().catch(() => {});
        const a = j.params && j.params.assign;
        const sc = a && a.project === pid && S.brief.scenes.find(s => s.id === a.scene);
        if (sc) {   // the server assigned it too; mirror it locally so our next save keeps it
          sc.background = { ...(sc.background || {}), type: "broll", clip: r.clip, dim: (sc.background && sc.background.dim) ?? a.dim ?? 0.55,
            blur: (sc.background && sc.background.blur) ?? a.blur ?? 0 };
          changed({ list: true, now: true });
        }
        if (S.tab === "scene") renderSceneForm();
      } else if (j.kind === "portrait" && r.portrait) {
        toast(`${t("ai.kinds.portrait")} ✓`, "ok");
        await loadPortraits().catch(() => {});
        if (j.params.assign_project === pid) { ensurePres().portrait = r.portrait; changed({ now: true }); }
        if (S.tab === "ai") renderAiTab();
      } else if (j.kind === "presenter" && j.project === pid) {
        toast(t("ai.presenterReady"), "ok");
        await refreshTimeline();
        updatePresenterStatus();
      }
    }
    function refreshAiViews() {
      if (S.aiBoxes && S.tab === "ai") {
        S.aiBoxes.gpu.replaceChildren(AI.gpuCard(AI.status));
        const list = S.aiJobs.filter(j => mine(j) || isActive(j)).slice(0, 12);
        S.aiBoxes.jobs.replaceChildren(AI.jobList(list, pollAi));
        const pj = S.aiJobs.find(j => j.kind === "presenter" && j.project === pid && isActive(j));
        if (S.pstate && (pj || (S.pstate.job && !pj))) { S.pstate.job = pj || null; drawPresenterStatus(); }
      }
      if (S.pendBox && S.pendBox.isConnected) {
        const sc = S.brief.scenes[S.sel];
        const js = S.aiJobs.filter(j => j.kind === "broll" && isActive(j) && j.params.assign && j.params.assign.project === pid && sc && j.params.assign.scene === sc.id);
        S.pendBox.replaceChildren(...js.map(j => AI.jobRow(j, pollAi)));
      }
      renderActions();
      if (S.make && S.make.current === "ai") renderMake();
    }

    // scene form: Background (brand gradient | AI clip from the library)
    function backgroundSection(sc) {
      const box = el("div", { class: "field bg-section" });
      const bg = sc.background && sc.background.type === "broll" ? sc.background : null;
      const set = v => { if (v) sc.background = v; else delete sc.background; changed({ list: true, now: true }); renderSceneForm(); };
      S._lastBg = S._lastBg || {};
      const seg = el("div", { class: "seg", role: "radiogroup", "aria-label": t("ai.background") },
        el("button", { type: "button", class: `seg-btn ${!bg ? "on" : ""}`, role: "radio", "aria-checked": !bg ? "true" : "false",
          onclick: () => { if (bg) S._lastBg[sc.id] = bg; set(null); } }, t("ai.bgBrand")),
        el("button", { type: "button", class: `seg-btn ${bg ? "on" : ""}`, role: "radio", "aria-checked": bg ? "true" : "false",
          onclick: () => { if (!bg) set(S._lastBg[sc.id] || { type: "broll", dim: 0.55, blur: 0 }); } }, "✦ " + t("ai.bgBroll")));
      box.append(el("div", { class: "row between" }, el("span", { class: "lbl" }, t("ai.background")), AI.badge()), seg);
      if (!bg) return box;
      if (!S.lib) { loadLib().then(() => { if (S.tab === "scene" && S.brief.scenes[S.sel] === sc) renderSceneForm(); }).catch(() => {}); }
      const clips = S.lib || [];
      const cur = clips.find(c => c.id === bg.clip);
      if (bg.prompt) box.append(el("p", { class: "muted small" }, t("ai.bgSuggested", { p: bg.prompt })));
      if (bg.clip && S.lib && !cur) box.append(el("p", { class: "warn small" }, t("ai.bgMissing", { id: bg.clip })));
      if (!bg.clip) box.append(el("p", { class: "muted small" }, t("ai.bgNone")));
      if (cur && ((cur.w > cur.h) !== (S.brief.format === "16:9"))) {
        box.append(el("p", { class: "warn small" }, t("ai.bgOrientWarn", { o: cur.w > cur.h ? "horizontal" : "vertical", f: S.brief.format })));
      }
      const slider = (k, min, max, step, fmt) => {
        const out = el("span", { class: "mono small" }, fmt(bg[k] ?? 0));
        const r = el("input", { type: "range", min, max, step, value: bg[k] ?? 0, "aria-label": t("ai." + k),
          oninput: e => { bg[k] = parseFloat(e.target.value); out.textContent = fmt(bg[k]); changed(); } });
        return el("div", { class: "field slider" }, el("span", { class: "lbl" }, t("ai." + k)), el("div", { class: "row gap" }, r, out));
      };
      bg.dim = bg.dim ?? 0.55; bg.blur = bg.blur ?? 0;
      box.append(el("div", { class: "row gap wrap" }, slider("dim", 0, 0.9, 0.05, v => `${Math.round(v * 100)}%`), slider("blur", 0, 20, 1, v => `${v}px`)));
      box.append(el("span", { class: "lbl" }, t("ai.bgPick")),
        AI.clipGrid(clips, { compact: true, selected: bg.clip, onPick: m => { bg.clip = m.id; changed({ list: true, now: true }); renderSceneForm(); } }));
      S.pendBox = el("div", { class: "jobs" });
      box.append(S.pendBox);
      box.append(el("details", { class: "gen", open: !!(bg.prompt && !bg.clip) },
        el("summary", {}, "✦ " + t("ai.generateClip")),
        AI.brollForm({ prompt: bg.prompt || "", orientation: S.brief.format === "16:9" ? "landscape" : "portrait",
          assign: { project: pid, scene: sc.id, dim: bg.dim, blur: bg.blur }, onQueued: j => onAiJob(j) })));
      refreshAiViews();
      return box;
    }

    // AI tab: presenter setup + this project's AI jobs + GPU
    function renderAiTab() {
      const b = S.brief;
      const pres = b.presenter || null;
      const boxes = { gpu: el("div", { class: "panel" }), jobs: el("div", {}), status: el("div", { class: "pstatus" }) };
      S.aiBoxes = boxes;
      if (!S.portraits) loadPortraits().then(() => { if (S.tab === "ai") renderAiTab(); }).catch(() => {});
      const on = !!(pres && pres.enabled);
      const enable = el("input", { type: "checkbox", id: "pres-on", checked: on, onchange: e => {
        ensurePres().enabled = e.target.checked; changed({ now: true }); renderAiTab(); } });
      const f = el("div", { class: "form" },
        el("div", { class: "row between" }, el("h3", {}, t("ai.presenter")), AI.badge()),
        el("p", { class: "muted small" }, t("ai.presenterHelp")),
        el("label", { class: "row gap", for: "pres-on" }, enable, el("span", {}, t("ai.enable"))));
      const hasFull = b.scenes.some(s => s.layout === "presenter");
      if (on || hasFull) {
        const P = ensurePres();
        // portrait
        const imgs = Object.entries(S.assets).filter(([, m]) => m.type === "image").map(([n]) => ({ id: n, name: n, source: "asset",
          url: `/api/projects/${pid}/thumbs/${encodeURIComponent(n)}` }));
        const list = [...(S.portraits || []), ...imgs];
        f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.portrait")),
          AI.portraitGrid(list, { selected: P.portrait, onPick: m => { P.portrait = m.id; changed({ now: true }); renderAiTab(); },
            onDelete: async m => { if (!confirmBox(t("ai.confirmDeletePortrait"))) return;
              try { await api("DELETE", `/api/library/portraits/${m.id}`); await loadPortraits(); renderAiTab(); } catch (e) { toast(e.message, "error"); } } }),
          el("details", {}, el("summary", { class: "small" }, t("ai.uploadPortrait")),
            AI.portraitUpload({ onDone: async m => { await loadPortraits(); P.portrait = m.id; changed({ now: true }); renderAiTab(); } })),
          el("details", {}, el("summary", { class: "small" }, "✦ " + t("ai.generatePortrait")),
            AI.portraitGenerate({ prompt: P.portrait_prompt || "", project: pid, onQueued: j => onAiJob(j) }))));
        // mode
        const segOf = (key, names, vals, cur, onPick) => el("div", { class: "seg", role: "radiogroup", "aria-label": t("ai." + key) },
          vals.map(v => el("button", { type: "button", class: `seg-btn ${v === cur ? "on" : ""}`, role: "radio", "aria-checked": v === cur ? "true" : "false",
            onclick: () => onPick(v) }, t(`ai.${names}.${v}`))));
        f.append(el("div", { class: "row gap wrap" },
          el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.mode")),
            segOf("mode", "modes", ["corner", "full"], P.mode || "corner", v => { P.mode = v; changed({ now: true }); renderAiTab(); })),
          el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.quality")),
            segOf("quality", "qualities", ["draft", "final"], P.quality || "draft", v => { P.quality = v; changed({ now: true }); renderAiTab(); }))));
        if ((P.mode || "corner") === "corner") {
          const all = P.scenes === "all" || P.scenes == null;
          const allBox = el("input", { type: "checkbox", id: "pres-all", checked: all, onchange: e => {
            P.scenes = e.target.checked ? "all" : b.scenes.map(s => s.id); changed({ now: true }); renderAiTab(); } });
          const list2 = all ? null : el("div", { class: "scene-checks" }, b.scenes.map(s => {
            const id = `pres-s-${s.id}`;
            return el("label", { class: "row gap small", for: id }, el("input", { type: "checkbox", id, checked: P.scenes.includes(s.id),
              disabled: s.layout === "presenter",
              onchange: e => { P.scenes = e.target.checked ? [...new Set([...P.scenes, s.id])] : P.scenes.filter(x => x !== s.id); changed({ now: true }); } }),
              el("span", {}, `${s.id}`), el("span", { class: "muted" }, t("layouts." + s.layout)));
          }));
          f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("ai.onScenes")),
            el("label", { class: "row gap", for: "pres-all" }, allBox, el("span", {}, t("ai.allScenes"))), list2));
        }
        f.append(boxes.status);
      }
      panel.replaceChildren(warnBox, f,
        el("h3", { class: "mt" }, t("ai.projectJobs")), boxes.jobs,
        boxes.gpu,
        el("p", {}, el("a", { href: "#/ai" }, t("ai.openAiPage") + " →")),
        el("details", { class: "small" }, el("summary", { class: "muted" }, t("ai.licences")), AI.licences(AI.status)));
      renderWarnings();
      if (!AI.status) AI.loadStatus().then(refreshAiViews).catch(() => {});
      updatePresenterStatus();
      refreshAiViews();
    }
    async function updatePresenterStatus() {
      if (!S.aiBoxes || S.tab !== "ai") return;
      await flush();
      try { S.pstate = await api("GET", `/api/projects/${pid}/presenter`); } catch { S.pstate = null; }
      drawPresenterStatus();
    }
    function drawPresenterStatus() {
      const box = S.aiBoxes && S.aiBoxes.status;
      const st = S.pstate;
      if (!box || !st) return;
      if (st.enabled === false && !st.full) { box.replaceChildren(); return; }
      const q = (S.brief.presenter && S.brief.presenter.quality) || "draft";
      const kids = [];
      if (st.error) kids.push(el("p", { class: "warn small" }, st.error));
      else {
        kids.push(el("p", { class: `small ${st.ready ? "ok-t" : st.stale ? "warn" : "muted"}` },
          st.ready ? "✓ " + t("ai.presenterReady") : st.stale ? "⚠ " + t("ai.presenterStale") : t("ai.presenterMissing")));
        if (st.voiced < st.voice_needed) kids.push(el("p", { class: "muted small" }, t("ai.presenterNeedsVoice", { v: st.voiced, n: st.voice_needed })));
        const e = st.estimate && st.estimate[q];
        if (!st.ready && e) kids.push(el("p", { class: "muted small" }, e.missing_seconds > 0
          ? t("ai.presenterEst", { s: e.missing_seconds.toFixed(1), t: AI.fmtDur(e.seconds) }) + ` · ~${(st.need_gb[q] || 0).toFixed(1)} GB VRAM`
          : t("ai.presenterCached")));
      }
      if (st.job) kids.push(AI.jobRow(st.job, pollAi));
      kids.push(el("div", { class: "row gap" }, el("button", { class: "btn primary", disabled: !!st.error || !!st.job || st.ready,
        onclick: async () => {
          await flush();
          try { const j = await api("POST", `/api/projects/${pid}/presenter`, { quality: q }); onAiJob(j); }
          catch (e2) { if (e2.status === 409 && e2.data && e2.data.id) onAiJob(e2.data); else toast(e2.message, "error", 8000); }
        } }, "✦ " + t("ai.generatePresenter")), AI.badge()));
      box.replaceChildren(...kids);
    }

    // ---------- preview ----------
    const preview = (() => {
      const frame = el("iframe", { class: "pv-frame", src: "/renderer/index.html?embed", title: t("preview"), tabindex: "-1" });
      const frameBox = el("div", { class: "pv-stage" }, frame);
      const audio = new Audio();
      audio.preload = "auto";
      let tl = null, loaded = false, playing = false, cur = 0, t0Perf = 0, t0 = 0, audioKey = null, raf = 0;
      const playBtn = el("button", { class: "btn round", "aria-label": t("play"), onclick: () => toggle() }, "▶");
      const scrub = el("input", { type: "range", min: 0, max: 10, step: 0.01, value: 0, "aria-label": "Time",
        oninput: e => { seek(parseFloat(e.target.value)); } });
      const time = el("span", { class: "mono small time" }, "0:00.0");
      const marks = el("div", { class: "marks", "aria-hidden": "true" });
      const scrubWrap = el("div", { class: "scrub" }, marks, scrub);
      const info = el("div", { class: "muted small pv-info" });
      previewBox.append(frameBox);
      transport.append(playBtn, scrubWrap, time);
      transport.after(info);
      frame.addEventListener("load", () => { loaded = true; if (tl) push(); });
      function win() { return frame.contentWindow; }
      function push() {
        if (!loaded || !win() || !win().loadTimeline) return;
        win().loadTimeline(tl).then(() => draw()).catch(() => {});
      }
      function setFormat(fmt) { frameBox.classList.toggle("land", fmt === "16:9"); }
      function load(newTl) {
        tl = newTl;
        setFormat(tl.format);
        scrub.max = tl.duration.toFixed(2);
        if (cur > tl.duration) cur = 0;
        marks.replaceChildren(...tl.scenes.map((s, i) => el("i", { style: { left: `${(s.start / tl.duration) * 100}%`, width: `${((s.end - s.start) / tl.duration) * 100}%` },
          class: s.voiced ? "v" : "", title: s.id }, String(i + 1))));
        info.textContent = `${tl.duration.toFixed(1)} s · ${tl.width}×${tl.height} · ` + t("voicedCount", { v: tl.voiced, n: tl.voice_needed }) +
          (tl.voiced < tl.voice_needed ? ` · ${t("estimatedTiming")}` : "");
        if (tl.audio_key !== audioKey) {
          audioKey = tl.audio_key;
          const wasPlaying = playing;
          if (playing) pause();
          if (tl.audio_url) { audio.src = tl.audio_url; audio.load(); } else audio.removeAttribute("src");
          if (wasPlaying) play();
        }
        push();
      }
      function now() {
        if (playing) {
          if (audio.src && !audio.paused && !audio.ended) return audio.currentTime;
          return t0 + (performance.now() - t0Perf) / 1000;
        }
        return cur;
      }
      function draw() {
        const w = win();
        if (w && w.seek && w.__ready !== false) { try { w.seek(cur); } catch { /* frame reloading */ } }
        scrub.value = cur;
        time.textContent = `${fmtTime(cur)} / ${fmtTime(tl ? tl.duration : 0)}`;
        if (tl) {
          const i = tl.scenes.findIndex(s => cur >= s.start && cur < s.end);
          $$(".marks i", transport).forEach((m, k) => m.classList.toggle("cur", k === i));
        }
      }
      function loop() {
        cur = now();
        if (tl && cur >= tl.duration) { cur = tl.duration; pause(); draw(); return; }
        draw();
        raf = requestAnimationFrame(loop);
      }
      function play() {
        if (!tl) return;
        if (cur >= tl.duration - 0.05) cur = 0;
        playing = true;
        t0 = cur; t0Perf = performance.now();
        if (audio.src && audio.duration && cur < audio.duration) { audio.currentTime = cur; audio.play().catch(() => {}); }
        else if (audio.src) { audio.currentTime = cur; audio.play().catch(() => {}); }
        win() && win().setPlaying && win().setPlaying(true);
        playBtn.textContent = "❚❚"; playBtn.setAttribute("aria-label", t("pause"));
        cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
      }
      function pause() {
        cur = now();
        playing = false;
        audio.pause();
        win() && win().setPlaying && win().setPlaying(false);
        playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", t("play"));
        cancelAnimationFrame(raf);
        draw();
      }
      function toggle() { playing ? pause() : play(); }
      function seek(x) {
        cur = Math.max(0, Math.min(x, tl ? tl.duration : x));
        if (playing) { t0 = cur; t0Perf = performance.now(); if (audio.src) audio.currentTime = cur; }
        draw();
      }
      return { load, seek, toggle, pause, setFormat, get t() { return cur; }, stop: () => { pause(); audio.src = ""; } };
    })();

    // ---------- voice / render / jobs ----------
    const clip = new Audio();
    async function listen(sceneId, voiced) {
      if (voiced) {
        preview.pause();
        clip.src = `/api/projects/${pid}/voice/${encodeURIComponent(sceneId)}.wav?ts=${Date.now()}`;
        clip.play().catch(e => toast(e.message, "error"));
        return;
      }
      await flush();
      startJob("voice", { scene: sceneId }, () => listen(sceneId, true));
    }
    async function flush() { if (S.version !== S.savedVersion) { clearTimeout(saveTimer); await save(); } }
    async function startJob(kind, body, after) {
      try {
        const j = await api("POST", `/api/projects/${pid}/${kind}`, body);
        trackJob(j, after);
      } catch (e) {
        if (e.status === 409 && e.data && e.data.id) trackJob(e.data, after);
        else toast(e.message, "error");
      }
    }
    function trackJob(j, after) {
      S.jobs[j.id] = j;
      renderJobs();
      const poll = async () => {
        if (!S.alive) return;
        try { S.jobs[j.id] = await api("GET", `/api/jobs/${j.id}`); } catch { S.jobs[j.id].status = "error"; S.jobs[j.id].error = "lost"; }
        const cur = S.jobs[j.id];
        renderJobs();
        if (cur.status === "queued" || cur.status === "running") { timers.push(setTimeout(poll, 700)); return; }
        if (cur.status === "done") {
          toast(cur.kind === "render" ? t("renderDone") : t("voiceDone"), "ok");
          if (cur.result && cur.result.warnings && cur.result.warnings.length) showWarnings(cur.result.warnings);
          if (cur.kind === "render") { await refreshAssets(); renderRenders(); }
          await refreshTimeline();
          if (cur.kind === "voice" && cur.result && cur.result.generated) renderPanel();
          after && after();
        } else if (cur.status === "error") {
          toast(t("jobFailed", { msg: cur.error }), "error", 12000);
          if (cur.kind === "render" && /not reachable|Ovoz/i.test(cur.error || "")) {
            modal(t("renderSilent"), el("p", {}, cur.error), [{ label: t("cancel"), onClick: () => {} },
              { label: t("renderSilent"), primary: true, onClick: () => { startJob("render", { allow_silent: true }); } }]);
          }
        }
        setTimeout(() => { delete S.jobs[j.id]; renderJobs(); }, cur.status === "done" ? 4000 : 15000);
      };
      timers.push(setTimeout(poll, 500));
    }
    function renderJobs() {
      jobBox.replaceChildren(...Object.values(S.jobs).map(j => {
        const pct = Math.round((j.progress || 0) * 100);
        const stage = j.status === "queued" ? t("queued", { n: j.position || 1 }) : t("stages." + (j.stage || j.status));
        return el("div", { class: `job ${j.status}` },
          el("div", { class: "row between" },
            el("b", {}, `${j.kind === "render" ? t("render") : t("generateVoice")} · ${stage}`),
            (j.status === "queued" || j.status === "running") ? el("button", { class: "btn small", onclick: () => api("POST", `/api/jobs/${j.id}/cancel`) }, t("cancelJob")) : null),
          el("div", { class: "bar", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, el("i", { style: { width: `${pct}%` } })),
          el("div", { class: "muted small" }, j.status === "error" ? j.error : `${pct}% ${j.message || ""}`));
      }));
      renderActions();
    }
    function renderActions() {
      const busy = kind => Object.values(S.jobs).some(j => j.kind === kind && (j.status === "queued" || j.status === "running"));
      const tl = S.tl;
      const allVoiced = tl && tl.voiced >= tl.voice_needed;
      actions.replaceChildren(
        el("button", { class: "btn", disabled: busy("voice") || !OV.ok, title: OV.ok ? "" : t("ovozDown", { url: OV.url || "" }),
          onclick: async () => { await flush(); startJob("voice", allVoiced ? { force: true } : {}); } },
          "🎙 " + (allVoiced ? t("regenerate") : t("generateVoice"))),
        el("button", { class: "btn primary", disabled: busy("render"), onclick: async () => {
          await flush();
          if (!OV.ok && !(tl && tl.voiced >= tl.voice_needed)) {
            if (!confirmBox(t("renderSilentConfirm"))) return;
            startJob("render", { allow_silent: true });
          } else startJob("render", {});
        } }, "⬇ " + t("render")),
        ...[presenterBadge()].filter(Boolean));
    }
    function presenterBadge() {
      const p = S.tl && S.tl.presenter;
      if (!p) return null;
      const j = (S.aiJobs || []).find(x => x.kind === "presenter" && x.project === pid && (x.status === "queued" || x.status === "running"));
      const [cls, txt, tip] = j ? ["work", t("ai.badgeWorking", { p: Math.round((j.progress || 0) * 100) }), j.message || ""]
        : p.ready ? ["ok", t("ai.badgeReady"), t("ai.presenterReady")]
        : p.stale ? ["stale", t("ai.badgeStale"), t("ai.presenterStale")] : ["missing", t("ai.badgeMissing"), t("ai.presenterMissing")];
      return el("button", { class: `pbadge ${cls}`, title: tip, onclick: () => setTab("ai") }, txt);
    }
    function renderRenders() {
      const list = S.renders || [];
      rendersBox.replaceChildren(el("h3", {}, t("renders")),
        list.length ? el("ul", { class: "render-list" }, list.map(r => el("li", {},
          el("div", {}, el("b", { class: "mono small" }, r.name),
            el("div", { class: "muted small" }, [r.format, r.duration ? `${r.duration.toFixed(1)} s` : "", fmtBytes(r.size),
              r.voice_needed && r.voiced < r.voice_needed ? "⚠ partly silent" : ""].filter(Boolean).join(" · "))),
          el("div", { class: "row gap" },
            el("button", { class: "btn small", onclick: () => watch(r) }, "▶ " + t("watch")),
            el("a", { class: "btn small", href: `/api/projects/${pid}/renders/${encodeURIComponent(r.name)}?download=1` }, t("download")),
            el("button", { class: "icon-btn danger", "aria-label": `${t("delete")} ${r.name}`, onclick: async () => {
              if (!confirmBox(t("confirmDeleteRender", { name: r.name }))) return;
              await api("DELETE", `/api/projects/${pid}/renders/${encodeURIComponent(r.name)}`);
              await refreshAssets(); renderRenders(); } }, "✕"))))) : el("p", { class: "muted small" }, t("noRenders")));
    }
    function watch(r) {
      preview.pause();
      const v = el("video", { src: `/api/projects/${pid}/renders/${encodeURIComponent(r.name)}`, controls: true, autoplay: true,
        class: `render-video ${r.format === "16:9" ? "land" : ""}` });
      const close = modal(r.name, v, [{ label: t("close"), onClick: () => {} }]);
      v.addEventListener("ended", () => {});
      return close;
    }

    // ---------- "Make video": voice -> free GPU -> AI -> render ----------
    let makeTimer = null, makeQuality = "final";
    const makeActive = () => S.make && (S.make.status === "queued" || S.make.status === "running");
    async function startMake() {
      await flush();
      const pres = S.brief.presenter;
      if (pres && pres.quality !== makeQuality) { pres.quality = makeQuality; changed({ now: true }); await flush(); }
      try { S.make = await api("POST", `/api/projects/${pid}/make`, { quality: makeQuality }); }
      catch (e) { if (e.status === 409 && e.data && e.data.id) S.make = e.data; else { toast(e.message, "error", 9000); return; } }
      renderMake(); pollMake(); pollAi();
    }
    async function makeAction(path) {
      try { S.make = await api("POST", `/api/make/${S.make.id}/${path}`); renderMake(); pollMake(); }
      catch (e) { toast(e.message, "error"); }
    }
    async function pollMake() {
      clearTimeout(makeTimer);
      if (!S.alive || !S.make) return;
      const was = S.make.status, wasStage = S.make.current;
      try { S.make = await api("GET", `/api/make/${S.make.id}`); } catch { /* restarting */ }
      renderMake();
      if (S.make.current !== wasStage || S.make.status !== was) {
        // the server changed the brief (b-roll / portrait / presenter quality) or made a render
        await refreshAssets(true); renderRenders(); await refreshTimeline();
        if (S.make.status === "done" && was !== "done") toast(t("make.done", { name: (S.make.result || {}).render || "" }), "ok", 8000);
        if (S.make.status === "error" && was !== "error") toast(t("jobFailed", { msg: S.make.error }), "error", 12000);
      }
      if (makeActive()) makeTimer = setTimeout(pollMake, 1500);
    }
    function renderMake() {
      const m = S.make;
      const fmtT = AI.fmtDur;
      const qSel = el("div", { class: "seg small-seg", role: "radiogroup", "aria-label": t("make.quality") },
        ["final", "draft"].map(q => el("button", { type: "button", class: `seg-btn ${q === makeQuality ? "on" : ""}`, role: "radio",
          "aria-checked": q === makeQuality ? "true" : "false", disabled: makeActive(), onclick: () => { makeQuality = q; renderMake(); } }, t("make.qualities." + q))));
      const head = el("div", { class: "row between wrap" },
        el("div", { class: "row gap" }, el("b", {}, t("make.title")), AI.badge()),
        el("div", { class: "row gap" }, el("span", { class: "muted small" }, t("make.quality")), qSel,
          el("button", { class: "btn primary", disabled: makeActive(), onclick: startMake }, "▶ " + (m && m.status === "done" ? t("make.again") : t("make.button")))));
      if (!m) { makeBox.replaceChildren(head, el("p", { class: "muted small" }, t("make.help"))); return; }
      const failedAt = Object.entries(m.stages).find(([, st]) => ["error", "cancelled", "pending", "running"].includes(st.state));
      const status = el("div", { class: "row between wrap" },
        el("span", { class: `small mk-status mk-${m.status}` }, (m.status === "queued" && m.position ? t("make.queuedPos", { n: m.position }) : t("make.status." + m.status)) +
          (m.elapsed != null ? ` · ${fmtT(m.elapsed)}` : "") + (m.eta ? ` · ${t("make.eta", { t: fmtT(m.eta) })}` : "")),
        el("div", { class: "row gap" },
          makeActive() ? el("button", { class: "btn small danger", onclick: () => makeAction("cancel") }, t("make.cancel")) : null,
          ["error", "cancelled", "interrupted"].includes(m.status) && failedAt
            ? el("button", { class: "btn small", onclick: () => makeAction("retry") }, t("make.retry", { stage: t("make.stages." + failedAt[0]) })) : null));
      const steps = el("ol", { class: "steps" }, ["voice", "unload", "ai", "render"].map((k, i) => {
        const st = m.stages[k];
        const icon = st.state === "done" ? "✓" : st.state === "warning" ? "!" : st.state === "error" ? "✕" : st.state === "cancelled" ? "–" : String(i + 1);
        const meta = [];
        if (st.state === "running") {
          if (st.elapsed != null) meta.push(fmtT(st.elapsed));
          if (st.eta) meta.push(t("make.eta", { t: fmtT(st.eta) }));
        } else if (st.started && st.finished) meta.push(fmtT(st.finished - st.started));
        const pct = Math.round((st.progress || 0) * 100);
        return el("li", { class: `step s-${st.state}` }, el("span", { class: "st-icon" }, icon),
          el("div", { class: "st-body" },
            el("div", { class: "row between" }, el("b", {}, t("make.stages." + k)),
              el("span", { class: "muted small" }, [t("make.states." + st.state), ...meta].join(" · "))),
            st.state === "running" ? el("div", { class: "bar" }, el("i", { style: { width: `${pct}%` } })) : null,
            st.message ? el("div", { class: "muted small" }, st.message) : null,
            st.error ? el("div", { class: "error small" }, st.error) : null));
      }));
      const notes = (m.notices || []).map(n => el("div", { class: "warn small notice" }, "⚠ " + n));
      const aiRows = m.current === "ai" ? (S.aiJobs || []).filter(j => (m.ai_jobs || []).includes(j.id) && (j.status === "queued" || j.status === "running"))
        .map(j => AI.jobRow(j, pollAi)) : [];
      makeBox.replaceChildren(head, status, steps, ...notes, ...aiRows);
    }

    // ---------- keyboard ----------
    const onKey = e => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || e.target.isContentEditable || $(".modal-wrap")) return;
      if (e.code === "Space" && !(tag === "button" && e.target.closest(".scene-list, .actions, .renders, .picker, .seg"))) { e.preventDefault(); preview.toggle(); }
      else if (e.key === "ArrowLeft" && !e.altKey) { preview.seek(preview.t - 1); }
      else if (e.key === "ArrowRight" && !e.altKey) { preview.seek(preview.t + 1); }
    };
    document.addEventListener("keydown", onKey);
    const onOvoz = () => { if (S.brief) { if (fixVoice()) changed(); renderSettings(); renderActions(); } };
    document.addEventListener("ovoz", onOvoz);
    const onUnload = e => { if (S.version !== S.savedVersion) { save(); e.preventDefault(); } };
    addEventListener("beforeunload", onUnload);

    // ---------- boot ----------
    (async () => {
      try { await loadProject(); }
      catch (e) { view.replaceChildren(el("section", { class: "page" }, el("h1", {}, "Not found"), el("p", {}, e.message), el("a", { href: "#/" }, "← " + t("back")))); return; }
      renderCrumb();
      await checkOvoz();
      if (fixVoice()) changed();
      renderSettings();
      renderSceneList();
      setTab("scene");
      renderRenders();
      renderActions();
      preview.setFormat(S.brief.format);
      transport.parentElement.querySelector(".pv-info").after(el("p", { class: "muted small shortcuts" }, t("shortcuts")));
      await refreshTimeline();
      AI.loadStatus().catch(() => {});
      pollAi();
      renderMake();
      if (makeActive()) pollMake();
      const st = S.tl && S.tl.scenes[0];
      if (st) preview.seek(Math.min(1.6, st.end - 0.3));
    })();

    return () => {
      S.alive = false;
      timers.forEach(clearTimeout);
      clearTimeout(aiTimer);
      clearTimeout(makeTimer);
      clearTimeout(saveTimer);
      if (S.version !== S.savedVersion) save();
      preview.stop();
      clip.pause();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("ovoz", onOvoz);
      removeEventListener("beforeunload", onUnload);
    };
  }

  route();
})();
