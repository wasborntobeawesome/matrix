/* Video Studio UI — plain JS, no build step.
 * Routes: #/ (projects)   #/p/<id> (editor)   #/ai (AI page, ai.js)   #/monitor (system monitor, monitor.js)
 */
(() => {
  "use strict";
  // UI language (EN / OʻZ): strings.js builds window.STRINGS for the language remembered in this browser
  const t = (key, vars) => {
    let s = key.split(".").reduce((o, k) => (o == null ? o : o[k]), window.STRINGS);
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
  const FORMATS = ["9:16", "1:1", "16:9"];
  const DEFAULT_VOICE = { uz: "uz-UZ-MadinaNeural", ru: "ru-RU-SvetlanaNeural", en: "en-US-AvaNeural", ar: "ar-SA-ZariyahNeural" };
  const TEXT_FIELDS = ["say", "kicker", "headline", "sub", "callout", "button", "url", "stat"];
  // a translation map {"uz": "...", "ru": "..."} (not a screens item {asset, label})
  const isMap = v => !!v && typeof v === "object" && !Array.isArray(v) && !("asset" in v);
  const fmtSlug = f => String(f || "").replace(":", "x");
  const LAYOUTS = ["hero", "screen", "screens", "chips", "stat", "list", "cta", "presenter"];
  const DEVICES = ["phone", "browser", "none"];
  const fmtBytes = n => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);
  const fmtTime = s => { s = Math.max(0, s || 0); return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
  const ago = ts => {
    const d = Date.now() / 1000 - ts;
    if (d < 60) return t("justNow");
    if (d < 3600) return t("minAgo", { n: Math.round(d / 60) });
    if (d < 86400) return t("hAgo", { n: Math.round(d / 3600) });
    return fmtDate(ts, false);
  };
  // dates: the browser's Uzbek locale data is incomplete ("M11"), so Uzbek uses its own month names
  const UZ_MONTHS = ["yan", "fev", "mar", "apr", "may", "iyn", "iyl", "avg", "sen", "okt", "noy", "dek"];
  function fmtDate(ts, withTime = true) {
    const d = new Date(ts * 1000);
    if (uiLang() !== "uz") return withTime ? d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : d.toLocaleDateString();
    const p2 = n => String(n).padStart(2, "0");
    return `${d.getDate()}-${UZ_MONTHS[d.getMonth()]}` + (withTime ? `, ${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}` : ` ${d.getFullYear()}`);
  }
  const uiLang = () => window.UI_LANG || "en";
  // a manifest label may be a string or {"en": "...", "uz": "..."}
  const lbl = v => (v && typeof v === "object" ? (v[uiLang()] || v.en || Object.values(v)[0] || "") : (v == null ? "" : String(v)));
  function applyStatic() {
    document.documentElement.lang = uiLang();
    document.querySelectorAll("[data-t]").forEach(n => { n.textContent = t(n.dataset.t); });
    const seg = document.getElementById("ui-lang");
    if (seg) seg.replaceChildren(...["en", "uz"].map(l => el("button", { type: "button", class: `seg-btn ${uiLang() === l ? "on" : ""}`,
      "aria-pressed": uiLang() === l ? "true" : "false", title: l === "en" ? "English interface" : "Oʻzbekcha interfeys",
      onclick: () => { if (l !== uiLang()) { window.setUiLang(l); applyStatic(); route(); } } }, l === "en" ? "EN" : "OʻZ")));
  }

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

  window.SUI = { el, t, api, toast, modal, confirmBox, fmtBytes, ago, $, $$, lbl, uiLang };

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
    } catch { OV.ok = false; OV.error = t("serverDown"); }
    const p = $("#ovoz");
    p.className = `pill ${OV.ok ? "ok" : "bad"}`;
    p.textContent = OV.ok ? t("ovozOk") : t("ovozOffline");
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
    const sm = $("#sysmon");
    if (sm) sm.classList.toggle("on", location.hash.startsWith("#/monitor"));
    if (window.MonUI) window.MonUI.initWidget();
    if (m) cleanup = Editor(view, m[1]);
    else if (location.hash.startsWith("#/ai")) cleanup = window.AIUI.page(view);
    else if (location.hash.startsWith("#/monitor")) cleanup = window.MonUI.page(view);
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
              el("div", { class: "muted small" }, `${p.product} · ${(p.languages || [p.language]).map(l => l.toUpperCase()).join(" ")} · ${(p.formats || [p.format]).join(" ")}`),
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
    const more = el("div", { class: "row gap wrap", role: "group", "aria-label": t("moreLanguages") }, LANGS.map(l =>
      el("label", { class: "row gap small chk" }, el("input", { type: "checkbox", value: l, "data-lang": l }), t("langs." + l))));
    const fmts = el("div", { class: "row gap wrap", role: "group", "aria-label": t("formats_") }, FORMATS.map(f =>
      el("label", { class: "row gap small chk" }, el("input", { type: "checkbox", value: f, checked: f === "9:16" }), t("formats." + f))));
    modal(t("newProject"), el("div", { class: "form" },
      el("label", { for: "np-name" }, t("productName")), name,
      el("label", { for: "np-lang" }, t("mainLanguage")), lang,
      el("span", { class: "lbl small muted" }, t("moreLanguages")), more,
      el("span", { class: "lbl small muted" }, t("formats_")), fmts), [
      { label: t("cancel"), onClick: () => {} },
      { label: t("create"), primary: true, onClick: () => {
        const languages = [lang.value, ...$$("input:checked", more).map(i => i.value).filter(l => l !== lang.value)];
        const formats = $$("input:checked", fmts).map(i => i.value);
        api("POST", "/api/projects?source=ui", { blank: true, product: name.value.trim() || "My product", language: lang.value,
          languages, formats: formats.length ? formats : ["9:16"] })
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
      lang: null, fmt: null, formLang: null, alignPolls: 0,
      templates: [], tplPreview: null,
    };
    // undo / redo: snapshots of the brief (JSON) at the start of each burst of edits
    const UN = { undo: [], redo: [], stable: null, burst: false, timer: null };
    // ---------- languages / translations ----------
    const main = () => S.brief.language;
    const langs = () => (S.brief.languages && S.brief.languages.length ? S.brief.languages : [S.brief.language]);
    const fmts = () => (S.brief.formats && S.brief.formats.length ? S.brief.formats : [S.brief.format]);
    const multi = () => langs().length > 1;
    const getT = (v, lg) => (isMap(v) ? (v[lg] || "") : v == null ? "" : String(v));
    // a plain string in a non-main language tab: the same text is used for every language
    const shared = (v, lg) => multi() && !isMap(v) && lg !== main() && v != null && v !== "";
    function setT(v, lg, val) {
      if (!multi()) return val;
      if (isMap(v)) { const m = { ...v }; if (val) m[lg] = val; else delete m[lg]; return Object.keys(m).length ? m : ""; }
      if (lg === main()) return val;                  // a plain string stays plain in the main language
      const m = {};
      if (v) m[main()] = v;
      if (val) m[lg] = val;
      return Object.keys(m).length ? m : "";
    }
    function missingFor(sc) {           // {lang: [fields]} (same rules as brief.missing_translations)
      const out = {};
      if (!multi()) return out;
      for (const lg of langs()) {
        const f = [];
        for (const k of TEXT_FIELDS) {
          const v = sc[k];
          if (isMap(v) && !String(v[lg] || "").trim()) f.push(k);
          else if (k === "say" && typeof v === "string" && v.trim() && lg !== main()) f.push(k);
        }
        (sc.items || []).forEach((it, i) => {
          if (isMap(it) && !it[lg]) f.push(`items[${i + 1}]`);
          else if (it && typeof it === "object" && it.asset && isMap(it.label) && !it.label[lg]) f.push(`items[${i + 1}].label`);
        });
        if (f.length) out[lg] = f;
      }
      return out;
    }
    function syncLangState() {
      if (!S.brief.voices) S.brief.voices = { [main()]: S.brief.voice };
      if (!langs().includes(S.lang)) S.lang = main();
      if (!fmts().includes(S.fmt)) S.fmt = fmts()[0];
      if (S.formLang !== "all" || !multi()) S.formLang = S.lang;
    }
    const timers = [];
    let saveTimer = null;

    // ---------- layout skeleton ----------
    const sceneList = el("ol", { class: "scene-list", "aria-label": t("scenes") });
    const addSel = el("select", { "aria-label": t("layout") }, LAYOUTS.map(l => el("option", { value: l }, t("layouts." + l))));
    const left = el("aside", { class: "col col-scenes" },
      el("div", { class: "col-head" }, el("h2", {}, t("scenes")), el("span", { class: "muted small", id: "total-len" })),
      sceneList,
      el("div", { class: "add-scene" }, addSel, el("button", { class: "btn", onclick: () => addScene(addSel.value) }, "+ " + t("addScene"))),
      el("button", { class: "btn followup-btn", title: t("fu.help"), onclick: () => followupDialog() }, "✦ " + t("fu.button")));
    const tabs = el("div", { class: "tabs", role: "tablist" });
    const panel = el("div", { class: "tab-panel", role: "tabpanel" });
    const mid = el("section", { class: "col col-form" }, tabs, panel);
    const settings = el("div", { class: "settings" });
    const previewBox = el("div", { class: "preview-box" });
    const pvBar = el("div", { class: "pv-bar" });
    const coverBox = el("div", { class: "cover-box muted small" });
    const transport = el("div", { class: "transport" });
    const actions = el("div", { class: "actions" });
    const jobBox = el("div", { class: "jobs", "aria-live": "polite" });
    const makeBox = el("div", { class: "make", "aria-live": "polite" });
    const rendersBox = el("div", { class: "renders" });
    const right = el("section", { class: "col col-preview", "aria-label": t("preview") }, pvBar, previewBox, transport, coverBox, actions, makeBox, jobBox, rendersBox);
    // language + format of the live preview (and of the scene form's language tab)
    function renderPreviewBar() {
      const kids = [];
      if (multi()) kids.push(el("div", { class: "seg small-seg", role: "radiogroup", "aria-label": t("previewLanguage") },
        langs().map(l => el("button", { type: "button", class: `seg-btn ${l === S.lang ? "on" : ""}`, role: "radio",
          "aria-checked": l === S.lang ? "true" : "false", title: t("langs." + l), onclick: () => setLang(l) }, l.toUpperCase()))));
      if (fmts().length > 1) kids.push(el("div", { class: "seg small-seg", role: "radiogroup", "aria-label": t("previewFormat") },
        fmts().map(f2 => el("button", { type: "button", class: `seg-btn ${f2 === S.fmt ? "on" : ""}`, role: "radio",
          "aria-checked": f2 === S.fmt ? "true" : "false", onclick: () => { S.fmt = f2; renderPreviewBar(); refreshTimeline(); } }, f2))));
      const reload = el("button", { type: "button", class: "icon-btn pv-reload", title: t("tpl.reloadHelp"), "aria-label": t("tpl.reload"),
        onclick: async () => { await loadTemplates(); renderSettings(); refreshTimeline(); } }, "↻");
      pvBar.hidden = false;
      pvBar.replaceChildren(...(kids.length ? [el("span", { class: "muted small" }, t("previewIn")), ...kids] : []), el("span", { class: "grow" }), reload);
    }
    function renderCover() {
      const c = S.brief.cover;
      const at = c && c.scene ? t("coverAt", { scene: c.scene, t: (c.t ?? 0).toFixed(1) }) : t("coverDefault");
      coverBox.replaceChildren(el("span", {}, "🖼 " + at),
        el("button", { class: "btn small", title: t("coverSetHelp"), onclick: () => {
          const tt = preview.t, sc2 = S.tl && S.tl.scenes.find(x => tt >= x.start && tt < x.end);
          if (!sc2) return;
          S.brief.cover = { scene: sc2.id, t: Math.round((tt - sc2.start) * 100) / 100 };
          changed({ now: true }); renderCover(); toast(t("coverSet"), "ok");
        } }, t("coverSet")),
        c ? el("button", { class: "btn small", onclick: () => { delete S.brief.cover; changed({ now: true }); renderCover(); } }, t("coverReset")) : "");
    }
    view.append(el("div", { class: "editor" }, settings, el("div", { class: "editor-grid" }, left, mid, right)));

    // ---------- save ----------
    function setSave(state) { $("#savestate").textContent = state ? t(state) : ""; $("#savestate").className = `savestate ${state || ""}`; }
    function changed(opts = {}) {
      noteChange();
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

    // ---------- undo / redo ----------
    function noteChange() {
      if (!UN.burst) {
        if (UN.stable != null) { UN.undo.push(UN.stable); if (UN.undo.length > 200) UN.undo.shift(); }
        UN.redo = [];
        UN.burst = true;
      }
      clearTimeout(UN.timer);
      UN.timer = setTimeout(settle, 700);
      renderUndo();
    }
    function settle() { clearTimeout(UN.timer); UN.burst = false; UN.stable = JSON.stringify(S.brief); renderUndo(); }
    function applyState(json, { saved = false } = {}) {
      UN.stable = json;
      adoptBrief(JSON.parse(json));
      renderPreviewBar(); renderCover(); renderActions();
      S.version++;
      if (saved) { S.savedVersion = S.version; refreshTimeline(); }
      else { setSave("saving"); clearTimeout(saveTimer); saveTimer = setTimeout(save, 0); }
      renderUndo();
    }
    function undo() {
      if (UN.burst) settle();
      if (!UN.undo.length) return;
      UN.redo.push(UN.stable);
      applyState(UN.undo.pop());
      toast(t("undone"), "info", 1600);
    }
    function redo() {
      if (UN.burst) settle();
      if (!UN.redo.length) return;
      UN.undo.push(UN.stable);
      applyState(UN.redo.pop());
      toast(t("redone"), "info", 1600);
    }
    const undoBtns = { u: null, r: null };
    function renderUndo() {
      if (!undoBtns.u) return;
      undoBtns.u.disabled = !(UN.undo.length || (UN.burst && UN.stable != null));
      undoBtns.r.disabled = !UN.redo.length || UN.burst;
    }

    // ---------- data ----------
    async function loadProject() {
      const d = await api("GET", `/api/projects/${pid}`);
      S.project = d; S.brief = d.brief; S.assets = d.assets; S.renders = d.renders; S.warnings = d.warnings;
      S.lang = S.brief.language; S.fmt = S.brief.format;
      syncLangState();
      document.title = `${S.brief.title} — ${t("appName")}`;
      for (const j of d.jobs || []) if (j.status === "queued" || j.status === "running") trackJob(j);
      S.aiJobs = d.ai_jobs || [];
      S.make = d.make && d.make.id ? d.make : null;
      for (const j of S.aiJobs) S.aiPrev[j.id] = j.status;
    }
    let alignTimer = null;
    async function refreshTimeline() {
      try {
        syncLangState();
        const tq = S.tplPreview ? `&template=${encodeURIComponent(S.tplPreview)}` : "";
        S.tl = await api("GET", `/api/projects/${pid}/timeline?lang=${S.lang}&format=${encodeURIComponent(S.fmt)}${tq}`);
        preview.load(S.tl);
        if (S.tab === "scene" && S.formSig != null && !panel.contains(document.activeElement)) {
          const sc0 = S.brief.scenes[S.sel], st0 = sc0 && sceneTl(sc0.id);
          if (`${st0 && st0.voiced}|${st0 && st0.timing}` !== S.formSig) renderSceneForm();
        }
        // word timing runs in the background (faster-whisper); pick it up when it is ready
        clearTimeout(alignTimer);
        const tm = S.tl.timing;
        if (tm && tm.aligned < tm.voiced && S.alignPolls < 12) { S.alignPolls++; alignTimer = setTimeout(() => { if (S.alive) refreshTimeline(); }, 2500); }
        else if (tm && tm.aligned >= tm.voiced) S.alignPolls = 0;
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
      syncLangState();
      const i = S.brief.scenes.findIndex(x => x.id === selId);
      S.sel = i >= 0 ? i : Math.min(S.sel, S.brief.scenes.length - 1);
      renderCrumb(); renderSettings(); renderSceneList(); renderPanel();
      preview.setFormat(S.fmt);
    }

    // ---------- crumb + settings bar ----------
    function renderCrumb() {
      const title = el("input", { class: "title-input", value: S.brief.title, "aria-label": t("title"),
        oninput: e => { S.brief.title = e.target.value; document.title = `${e.target.value} — ${t("appName")}`; changed(); } });
      undoBtns.u = el("button", { class: "icon-btn undo-btn", title: t("undoTip"), "aria-label": t("undo"), onclick: undo }, "↶");
      undoBtns.r = el("button", { class: "icon-btn undo-btn", title: t("redoTip"), "aria-label": t("redo"), onclick: redo }, "↷");
      $("#crumb").replaceChildren(el("a", { href: "#/", class: "back" }, "← " + t("back")), title,
        el("div", { class: "row undo-row", role: "group", "aria-label": t("undo") }, undoBtns.u, undoBtns.r));
      renderUndo();
    }
    function field(label, input, cls = "") {
      const id = `f-${Math.random().toString(36).slice(2, 8)}`;
      input.id = id;
      return el("div", { class: `field ${cls}` }, el("label", { for: id }, label), input);
    }
    function modelsFor(lang) { return OV.models.filter(m => (m.languages || []).includes(lang)); }
    function voicesFor(m, lang) { return (m && m.voices || []).filter(v => !v.lang || v.lang === lang || v.cross_lingual); }
    function fixVoiceOf(v, lang) {
      let m = OV.models.find(x => x.id === v.model);
      let changedAny = false;
      if (!m || !(m.languages || []).includes(lang) || !m.available) {
        const cands = modelsFor(lang).filter(x => x.available);
        m = cands.find(x => x.id === "edge") || cands[0];
        if (m) { v.model = m.id; changedAny = true; }
      }
      const vs = voicesFor(m, lang);
      if (m && !vs.find(x => x.id === v.voice)) {
        const pick = vs.find(x => x.id === DEFAULT_VOICE[lang]) || vs.find(x => x.lang === lang) || vs[0];
        if (pick) { v.voice = pick.id; changedAny = true; }
      }
      if (m && v.style && !(m.styles || []).find(s => s.id === v.style)) { delete v.style; changedAny = true; }
      return changedAny;
    }
    function fixVoice() {
      const b = S.brief;
      if (!OV.models.length) return false;
      if (!b.voices) b.voices = {};
      let any = false;
      for (const lg of langs()) {
        if (!b.voices[lg]) { b.voices[lg] = lg === main() && b.voice ? b.voice : { model: "edge", voice: DEFAULT_VOICE[lg], rate: 0 }; any = true; }
        if (fixVoiceOf(b.voices[lg], lg)) any = true;
      }
      b.voice = b.voices[main()];
      return any;
    }
    function toggleLang(l) {
      const b = S.brief;
      let ls = [...langs()];
      if (ls.includes(l)) { if (ls.length === 1) return; ls = ls.filter(x => x !== l); }
      else ls.push(l);
      b.languages = ls;
      if (!ls.includes(b.language)) b.language = ls[0];
      if (!b.voices) b.voices = {};
      if (!b.voices[l]) b.voices[l] = { model: "edge", voice: DEFAULT_VOICE[l], rate: 0 };
      b.voice = b.voices[b.language];
      fixVoice(); syncLangState();
      renderSettings(); renderSceneList(); renderPanel(); renderActions();
      changed({ now: true });
    }
    function setMainLang(l) {
      const b = S.brief;
      b.language = l;
      b.languages = [l, ...langs().filter(x => x !== l)];
      if (!b.voices) b.voices = {};
      if (!b.voices[l]) b.voices[l] = { model: "edge", voice: DEFAULT_VOICE[l], rate: 0 };
      b.voice = b.voices[l];
      fixVoice(); syncLangState();
      renderSettings(); renderSceneList(); renderPanel();
      changed({ now: true });
    }
    function toggleFmt(f) {
      const b = S.brief;
      let fs = [...fmts()];
      if (fs.includes(f)) { if (fs.length === 1) return; fs = fs.filter(x => x !== f); }
      else fs.push(f);
      b.formats = FORMATS.filter(x => fs.includes(x));
      if (!b.formats.includes(b.format)) b.format = b.formats[0];
      b.formats = [b.format, ...b.formats.filter(x => x !== b.format)];
      syncLangState();
      renderSettings(); renderPreviewBar(); renderActions();
      changed({ now: true });
    }
    function setLang(l) {          // the language being edited + previewed
      if (l === S.lang && S.formLang !== "all") return;
      S.lang = l;
      if (S.formLang !== "all") S.formLang = l;
      renderSettings(); renderPreviewBar(); renderSceneList(); renderPanel();
      refreshTimeline();
    }
    function renderSettings() {
      const b = S.brief;
      fixVoice();
      const product = el("input", { type: "text", value: b.product, oninput: e => { b.product = e.target.value; changed(); } });
      const langSeg = el("div", { class: "seg toggles", role: "group", "aria-label": t("languages") }, LANGS.map(l => el("button", {
        type: "button", class: `seg-btn ${langs().includes(l) ? "on" : ""}`, "aria-pressed": langs().includes(l) ? "true" : "false",
        title: t("langs." + l), onclick: () => toggleLang(l) }, l.toUpperCase() + (l === main() && multi() ? " ★" : ""))));
      const mainSel = multi() ? el("select", { onchange: e => setMainLang(e.target.value) },
        langs().map(l => el("option", { value: l, selected: l === main() }, t("langs." + l)))) : null;
      const fmtSeg = el("div", { class: "seg toggles", role: "group", "aria-label": t("formats_") }, FORMATS.map(f => el("button", {
        type: "button", class: `seg-btn ${fmts().includes(f) ? "on" : ""}`, "aria-pressed": fmts().includes(f) ? "true" : "false",
        title: t("formats." + f), onclick: () => toggleFmt(f) }, f)));
      const lg = S.lang;
      const v = b.voices[lg] || (b.voices[lg] = { model: "edge", voice: DEFAULT_VOICE[lg], rate: 0 });
      const syncMain = () => { b.voice = b.voices[main()]; };
      const ms = modelsFor(lg);
      const model = el("select", { onchange: e => { v.model = e.target.value; fixVoiceOf(v, lg); syncMain(); renderSettings(); changed(); } },
        OV.models.length ? ms.map(m => el("option", { value: m.id, selected: m.id === v.model, disabled: !m.available },
          m.name + (m.available ? "" : ` (${t("unavailable")})`))) : el("option", { value: v.model }, v.model));
      const cur = OV.models.find(m => m.id === v.model);
      const voice = el("select", { onchange: e => { v.voice = e.target.value; syncMain(); changed(); } },
        cur ? voicesFor(cur, lg).map(x => el("option", { value: x.id, selected: x.id === v.voice }, x.name))
          : el("option", { value: v.voice }, v.voice));
      const rate = el("input", { type: "number", min: -50, max: 100, step: 5, value: v.rate || 0, class: "narrow",
        onchange: e => { v.rate = Math.max(-50, Math.min(100, parseInt(e.target.value, 10) || 0)); syncMain(); changed(); } });
      const styles = cur && cur.styles && cur.styles.length ? el("select", { onchange: e => { if (e.target.value) v.style = e.target.value; else delete v.style; syncMain(); changed(); } },
        el("option", { value: "" }, t("none")), cur.styles.map(s2 => el("option", { value: s2.id, selected: s2.id === v.style }, s2.name))) : null;
      const color = k => el("input", { type: "color", value: b.brand[k], "aria-label": t(k), title: t(k),
        oninput: e => { b.brand[k] = e.target.value; changed(); } });
      const imgs = Object.entries(S.assets).filter(([, m]) => m.type === "image").map(([n]) => n);
      const logo = el("select", { onchange: e => { if (e.target.value) b.brand.logo = e.target.value; else delete b.brand.logo; changed(); } },
        el("option", { value: "" }, t("none")), imgs.map(n => el("option", { value: n, selected: n === b.brand.logo }, n)));
      const caps = el("input", { type: "checkbox", checked: b.captions !== false, onchange: e => { b.captions = e.target.checked; changed(); } });
      const vl = multi() ? ` (${lg.toUpperCase()})` : "";
      settings.replaceChildren(...[
        el("div", { class: "field auto" }, el("span", { class: "lbl" }, t("tpl.template")), templateButton()),
        el("div", { class: "field auto" }, el("span", { class: "lbl" }, t("tx.transition")), transitionButton()),
        field(t("product"), product),
        el("div", { class: "field auto" }, el("span", { class: "lbl" }, t("languages")), langSeg),
        mainSel ? field(t("mainLanguage"), mainSel, "narrow2") : null,
        el("div", { class: "field auto" }, el("span", { class: "lbl" }, t("formats_")), fmtSeg),
        field(t("voiceModel") + vl, model), field(t("voice") + vl, voice), field(t("rate"), rate, "narrow"),
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
    let listTimer = null;
    function renderSceneListSoon() { clearTimeout(listTimer); listTimer = setTimeout(() => { if (S.alive) renderSceneList(); }, 250); }
    function renderSceneList() {
      sceneList.replaceChildren();
      const scenes = S.brief.scenes;
      scenes.forEach((sc, i) => {
        const st = sceneTl(sc.id);
        const voiced = st && st.voiced;
        const hasSay = !!(getT(sc.say, S.lang).trim() || getT(sc.say, main()).trim());
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
              el("span", { class: "scene-badges" },
                st && hasSay ? el("span", { class: `tbadge ${st.timing === "aligned" ? "al" : "es"}`,
                  title: st.timing === "aligned" ? t("timingAlignedHelp") : t("timingEstimatedHelp") },
                  st.timing === "aligned" ? t("timingAligned") : t("timingEstimated")) : null,
                ...Object.keys(missingFor(sc)).map(lg => el("span", { class: "mbadge", title: t("missingIn", { lang: t("langs." + lg), list: missingFor(sc)[lg].join(", ") }) }, `⚠ ${lg.toUpperCase()}`))),
              el("span", { class: "scene-say", dir: S.lang === "ar" ? "rtl" : "auto" }, getT(sc.say, S.lang) || getT(sc.say, main()) || getT(sc.headline, S.lang) || "—"))),
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
      if (layout === "hero" || layout === "cta" || layout === "presenter") sc.headline = S.brief.product;
      if (layout === "screen") { sc.device = "phone"; sc.asset = firstImg ? firstImg[0] : ""; sc.headline = "New feature"; }
      if (layout === "screens") { sc.device = "phone"; sc.items = Object.entries(S.assets).filter(([, m]) => m.type === "image").slice(0, 2).map(([n]) => ({ asset: n })); sc.headline = "Feature tour"; }
      if (layout === "chips" || layout === "list") { sc.headline = "Highlights"; sc.items = ["One", "Two", "Three"]; }
      if (layout === "stat") { sc.stat = "3x"; sc.headline = "faster"; }
      const at = S.sel + 1;
      S.brief.scenes.splice(at, 0, sc);
      S.sel = at;
      changed({ list: true, now: true });
      setTab("scene");
    }

    // ---------- tabs ----------
    function setTab(name) {
      S.tab = name;
      tabs.replaceChildren(...["scene", "assets", "music", "ai", "history", "json"].map(k => el("button", {
        class: `tab ${k === name ? "on" : ""}`, role: "tab", "aria-selected": k === name ? "true" : "false",
        onclick: () => setTab(k) }, t({ scene: "tabScene", assets: "tabAssets", music: "tabMusic", ai: "tabAi", history: "tabHistory", json: "tabJson" }[k]) +
        (k === "assets" ? ` (${Object.keys(S.assets).length})` : "") +
        (k === "music" && S.brief.music && S.brief.music.track ? " ♪" : ""))));
      renderPanel();
      if (name === "ai") pollAi();
    }
    function renderPanel() {
      if (S.tab === "scene") renderSceneForm();
      else if (S.tab === "assets") renderAssets();
      else if (S.tab === "music") renderMusic();
      else if (S.tab === "ai") renderAiTab();
      else if (S.tab === "history") renderHistory();
      else renderJson();
    }

    // ---------- scene form ----------
    function renderSceneForm() {
      const sc = S.brief.scenes[S.sel];
      if (!sc) { panel.replaceChildren(el("p", { class: "empty" }, t("noScene"))); return; }
      const f = el("div", { class: "form scene-form" });
      const upd = (k, v, opts) => { if (v === "" || v == null) delete sc[k]; else sc[k] = v; changed(opts); refreshMissing(); };
      const FL = S.formLang;
      const ALL = FL === "all" && multi();
      const dirOf = lg => (lg === "ar" ? "rtl" : "auto");
      // one input bound to the translation of field k in language lg
      const tInput = (k, lg, opts = {}) => {
        const sh = shared(sc[k], lg);
        const inp = el(opts.area ? "textarea" : "input", { type: "text", rows: opts.rows || 2, dir: dirOf(lg),
          placeholder: sh ? `${sc[k]} — ${t("sameAllLangs")}` : (opts.ph || ""), "aria-label": ALL ? `${opts.label || k} (${lg})` : null,
          oninput: e => upd(k, setT(sc[k], lg, e.target.value), { list: opts.list }) });
        inp.value = sh ? "" : getT(sc[k], lg);
        return inp;
      };
      const text = (k, label, opts = {}) => {
        if (!ALL) return field(label, tInput(k, FL, opts), opts.cls);
        return el("div", { class: "field" }, el("span", { class: "lbl" }, label),
          el("div", { class: "tr-grid", style: { gridTemplateColumns: `repeat(${langs().length}, minmax(0, 1fr))` } },
            langs().map(lg => el("div", { class: `tr-cell ${(missingFor(sc)[lg] || []).includes(k) ? "miss" : ""}`, "data-k": k, "data-lg": lg },
              el("span", { class: "tr-lang" }, lg.toUpperCase()), tInput(k, lg, { ...opts, label })))));
      };
      // translation tabs
      const missBox = el("div", { class: "warn small miss-box", role: "status" });
      const refreshMissing = () => {
        const m = missingFor(sc);
        const ks = Object.keys(m);
        missBox.hidden = !ks.length;
        missBox.replaceChildren(...(ks.length ? ["⚠ ", t("missingTitle"), " ", ...ks.map(lg => el("span", { class: "miss-l" }, `${lg.toUpperCase()}: ${m[lg].join(", ")}`))] : []));
        $$(".ltab", f).forEach(b2 => { const lg = b2.dataset.lg; b2.classList.toggle("miss", !!m[lg]); });
        $$(".tr-cell", f).forEach(c => c.classList.toggle("miss", (m[c.dataset.lg] || []).includes(c.dataset.k)));
        renderSceneListSoon();
      };
      if (multi()) {
        f.append(el("div", { class: "ltabs", role: "tablist", "aria-label": t("translations") },
          ...langs().map(lg => el("button", { type: "button", role: "tab", class: `ltab ${FL === lg ? "on" : ""}`, "data-lg": lg,
            "aria-selected": FL === lg ? "true" : "false", onclick: () => { S.formLang = lg; setLang(lg); } },
            lg.toUpperCase() + (lg === main() ? " ★" : ""))),
          el("button", { type: "button", role: "tab", class: `ltab all ${ALL ? "on" : ""}`, "aria-selected": ALL ? "true" : "false",
            onclick: () => { S.formLang = "all"; renderSceneForm(); } }, "⇆ " + t("sideBySide"))), missBox);
      }
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
      const st = sceneTl(sc.id);
      S.formSig = `${st && st.voiced}|${st && st.timing}`;
      if (ALL) f.append(text("say", t("say"), { area: true, rows: 3, list: true }));
      else {
        const sh = shared(sc.say, FL);
        const sayIn = el("textarea", { rows: 3, class: "say", dir: dirOf(FL), placeholder: sh ? `${sc.say} — ${t("sayNotTranslated")}` : "" });
        sayIn.value = sh ? "" : getT(sc.say, FL);
        const counter = el("div", { class: "muted small counter" });
        const count = () => {
          const n = (sayIn.value.trim().match(/\S+/g) || []).length;
          const est = n ? Math.max(1, sayIn.value.trim().length / 14 + 0.25) : 0;
          counter.textContent = (n > 18 ? t("tooManyWords", { n }) : t("words", { n })) + (n ? ` · ${t("estSeconds", { s: est.toFixed(1) })}` : "");
          counter.classList.toggle("warn", n > 18);
        };
        sayIn.addEventListener("input", () => { upd("say", setT(sc.say, FL, sayIn.value), { list: true }); if (sc.say == null) sc.say = ""; count(); });
        count();
        const vbtn = getT(sc.say, FL).trim() ? el("button", { class: "btn small", onclick: () => listen(sc.id, st && st.voiced) },
          st && st.voiced ? "▶ " + t("listen") : "◌ " + t("generateThis")) : null;
        const timing = st && st.voiced ? el("span", { class: `tbadge ${st.timing === "aligned" ? "al" : "es"}`,
          title: st.timing === "aligned" ? t("timingAlignedHelp") : t("timingEstimatedHelp") },
          `${t("timing")}: ${st.timing === "aligned" ? t("timingAligned") : t("timingEstimated")}`) : null;
        f.append(el("div", { class: "field" }, el("label", { for: "say-in" }, t("say") + (multi() ? ` · ${FL.toUpperCase()}` : "")),
          Object.assign(sayIn, { id: "say-in" }), el("div", { class: "row between" }, counter, el("div", { class: "row gap" }, timing, vbtn))));
      }
      // layout fields
      const L = sc.layout;
      if (L === "hero") f.append(text("kicker", t("kicker")), text("headline", t("headline")), text("sub", t("sub")));
      if (L === "chips" || L === "list") {
        f.append(text("headline", t("headline")));
        // items: one per line; other languages translate the main language's items line by line
        const itemsFor = lg => {
          const ta = el("textarea", { rows: 5, dir: dirOf(lg), "aria-label": `${t("items")} (${lg})`, oninput: e => {
            const raw = e.target.value.split("\n");
            const old = sc.items || [];
            if (!multi()) sc.items = raw.map(x => x.trim()).filter(Boolean);
            else if (lg === main()) {
              sc.items = raw.map(x => x.trim()).filter(Boolean).map((ln, i) => (isMap(old[i]) ? { ...old[i], [lg]: ln } : ln));
            } else {
              sc.items = old.map((o, i) => {
                const ln = (raw[i] || "").trim();
                if (isMap(o)) { const m = { ...o }; if (ln) m[lg] = ln; else delete m[lg]; return m; }
                if (typeof o === "string") return !ln || ln === o ? o : { [main()]: o, [lg]: ln };
                return o;
              });
            }
            changed(); refreshMissing();
          } });
          ta.value = (sc.items || []).map(x => (isMap(x) ? (x[lg] || "") : typeof x === "string" ? x : x.label || x.asset || "")).join("\n");
          return ta;
        };
        if (ALL) f.append(el("div", { class: "field" }, el("span", { class: "lbl" }, t("items")),
          el("div", { class: "tr-grid", style: { gridTemplateColumns: `repeat(${langs().length}, minmax(0, 1fr))` } },
            langs().map(lg => el("div", { class: "tr-cell" }, el("span", { class: "tr-lang" }, lg.toUpperCase()), itemsFor(lg))))));
        else f.append(field(t("items"), itemsFor(FL)));
        if (multi()) f.append(el("p", { class: "muted small help" }, t("itemsTranslateHelp", { main: main().toUpperCase() })));
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
        const adv = el("div", { class: "row gap wrap" }, num("zoom", t("zoom"), 1, 4, 0.1, t("auto")));
        if (S.assets[sc.asset] && S.assets[sc.asset].type === "video") adv.append(num("trim_start", t("trimStart"), 0, 600, 0.1, "0"));
        f.append(adv);
      }
      if (L === "screens") {
        f.append(text("headline", t("headline")), text("sub", t("sub")));
        const box = el("div", { class: "screens-items" });
        (sc.items || []).forEach((it, k) => {
          const labIn = lg => {
            const sh = shared(it.label, lg);
            const inp = el("input", { type: "text", dir: dirOf(lg), placeholder: sh ? `${it.label} — ${t("sameAllLangs")}` : t("label"),
              "aria-label": `${t("label")} ${k + 1}${multi() ? ` (${lg})` : ""}`,
              oninput: e => { const v2 = setT(it.label, lg, e.target.value); if (v2) it.label = v2; else delete it.label; changed(); refreshMissing(); } });
            inp.value = sh ? "" : getT(it.label, lg);
            return inp;
          };
          const lab = ALL ? el("div", { class: "row gap" }, langs().map(lg => labIn(lg))) : labIn(FL);
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
      if (S.sel > 0) f.append(sceneTransition(sc));
      f.append(backgroundSection(sc));
      f.append(el("div", { class: "row gap wrap" }, num("pad", t("pad"), 0, 5, 0.1, S.sel === S.brief.scenes.length - 1 ? "1.0" : "0.4"),
        num("duration", t("minDuration"), 1, 30, 0.5, t("auto"))));
      panel.replaceChildren(warnBox, f);
      renderWarnings();
      refreshMissing();
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
            el("span", { class: "muted small" }, `${t("assetTypes." + m.type)} · ${m.w}×${m.h}${m.duration ? ` · ${m.duration.toFixed(1)} s` : ""} · ${fmtBytes(m.size)}`),
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

    // ---------- music tab ----------
    S.music = null;
    const musPrev = new Audio();
    let musPrevId = null;
    async function loadMusic() { S.music = await api("GET", "/api/library/music"); return S.music; }
    function renderMusic() {
      if (!S.music) { loadMusic().then(() => { if (S.tab === "music") renderMusic(); }).catch(e => toast(e.message, "error")); }
      const b = S.brief;
      const cur = b.music || null;
      const tracks = S.music || [];
      const pick = id => {
        if (!id) { delete b.music; }
        else b.music = { volume_db: -20, duck: true, duck_db: -10, fade_in: 1, fade_out: 2.5, start: 0, ...(b.music || {}), track: id };
        changed({ now: true }); renderMusic(); setTab("music");
      };
      const playBtn = m => el("button", { class: "icon-btn", "aria-label": `${t("musicPreview")} ${m.title}`, title: t("musicPreview"),
        onclick: () => {
          if (musPrevId === m.id && !musPrev.paused) { musPrev.pause(); musPrevId = null; renderMusic(); return; }
          preview.pause(); musPrev.src = m.url; musPrev.currentTime = 0; musPrev.volume = 0.6; musPrev.play().catch(() => {}); musPrevId = m.id; renderMusic();
        } }, musPrevId === m.id && !musPrev.paused ? "❚❚" : "▶");
      const row = m => el("li", { class: `mtrack ${cur && cur.track === m.id ? "on" : ""}` },
        el("label", { class: "row gap mt-pick" }, el("input", { type: "radio", name: "music-track", checked: !!(cur && cur.track === m.id), onchange: () => pick(m.id) }),
          el("span", { class: "mt-txt" }, el("b", {}, m.title),
            el("span", { class: "muted small" }, [m.artist, `${Math.round(m.duration)} s`, m.bpm ? `${m.bpm} bpm` : "", m.mood].filter(Boolean).join(" · ")),
            el("span", { class: "muted small" }, `${t("licence")}: ${m.licence}`))),
        el("span", { class: `src-badge ${m.source}` }, t("musicSources." + m.source)),
        playBtn(m),
        m.source !== "builtin" ? el("button", { class: "icon-btn danger", "aria-label": `${t("delete")} ${m.title}`, title: t("delete"), onclick: async () => {
          const n = (m.used_in || []).length;
          if (!confirmBox(t("confirmDeleteTrack", { name: m.title, n }))) return;
          try { await api("DELETE", `/api/library/music/${m.id}`); await loadMusic(); if (cur && cur.track === m.id) pick(null); else renderMusic(); }
          catch (e) { toast(e.message, "error"); } } }, "✕") : null);
      const list = el("ul", { class: "mtracks" },
        el("li", { class: `mtrack ${!cur || !cur.track ? "on" : ""}` }, el("label", { class: "row gap mt-pick" },
          el("input", { type: "radio", name: "music-track", checked: !cur || !cur.track, onchange: () => pick(null) }), el("b", {}, t("noMusic")))),
        tracks.map(row));
      const kids = [el("p", { class: "muted small" }, t("musicHelp")), list];
      if (cur && cur.track) {
        const missing = S.music && !tracks.find(x => x.id === cur.track);
        if (missing) kids.push(el("p", { class: "warn small" }, t("musicMissing", { id: cur.track })));
        const meta = tracks.find(x => x.id === cur.track);
        const slider = (k, min, max, step, fmt2, label) => {
          const out = el("span", { class: "mono small" }, fmt2(cur[k] ?? 0));
          const r = el("input", { type: "range", min, max, step, value: cur[k] ?? 0, "aria-label": label,
            oninput: e => { cur[k] = parseFloat(e.target.value); out.textContent = fmt2(cur[k]); changed(); } });
          return el("div", { class: "field slider" }, el("span", { class: "lbl" }, label), el("div", { class: "row gap" }, r, out));
        };
        const db = v => `${v > 0 ? "+" : ""}${Number(v).toFixed(0)} dB`, sec = v => `${Number(v).toFixed(1)} s`;
        const duck = el("input", { type: "checkbox", id: "mu-duck", checked: cur.duck !== false, onchange: e => { cur.duck = e.target.checked; changed({ now: true }); renderMusic(); } });
        kids.push(el("div", { class: "form mt" },
          el("h3", {}, t("musicSettings")),
          el("div", { class: "row gap wrap" }, slider("volume_db", -40, 0, 1, db, t("musicVolume")),
            el("label", { class: "row gap", for: "mu-duck" }, duck, el("span", {}, t("musicDuck")))),
          cur.duck !== false ? slider("duck_db", -30, 0, 1, db, t("musicDuckDb")) : null,
          el("div", { class: "row gap wrap" }, slider("fade_in", 0, 10, 0.5, sec, t("musicFadeIn")), slider("fade_out", 0, 15, 0.5, sec, t("musicFadeOut"))),
          slider("start", 0, Math.max(1, Math.floor(meta ? meta.duration : 60)), 0.5, sec, t("musicStart")),
          el("p", { class: "muted small" }, t("musicMixHelp"))));
      }
      // generate (ACE-Step)
      const gen = b.music && b.music.generate;
      const pendMusic = (S.aiJobs || []).filter(j => j.kind === "music" && isActive(j) && j.params && j.params.assign_project === pid);
      kids.push(el("details", { class: "mt gen-music", open: !!gen || pendMusic.length > 0 },
        el("summary", {}, "✦ " + t("ai.musicTitle")),
        gen ? el("p", { class: "ok-t small" }, t("ai.musicPlanned", { p: [gen.genre, gen.mood, gen.prompt].filter(Boolean).join(", ") }),
          " ", el("button", { class: "btn small", onclick: () => { delete b.music.generate; changed({ now: true }); renderMusic(); } }, t("ai.musicUnplan"))) : null,
        pendMusic.length ? el("div", { class: "jobs" }, pendMusic.map(j => AI.jobRow(j, pollAi))) : null,
        AI.musicForm({ project: pid, duration: S.tl ? Math.ceil(S.tl.duration + 3) : 30,
          onQueued: j => { onAiJob(j); renderMusic(); },
          onSpec: sp => { b.music = { volume_db: -20, duck: true, duck_db: -10, fade_in: 1, fade_out: 2.5, start: 0, ...(b.music || {}), generate: sp };
            changed({ now: true }); toast(t("ai.musicPlannedToast"), "ok"); renderMusic(); } })));
      if (!AI.status) AI.loadStatus().then(() => { if (S.tab === "music") renderMusic(); }).catch(() => {});
      // upload
      const file = el("input", { type: "file", accept: ".mp3,.wav,.ogg,.m4a,audio/*", id: "mu-file" });
      const title = el("input", { type: "text", id: "mu-title", placeholder: t("musicTitlePh") });
      const artist = el("input", { type: "text", id: "mu-artist" });
      const lic = el("input", { type: "text", id: "mu-lic", placeholder: t("licencePh"), required: true });
      const mood = el("input", { type: "text", id: "mu-mood", placeholder: t("moodPh") });
      const err = el("div", { class: "error small", role: "alert" });
      kids.push(el("details", { class: "mt upload-music" }, el("summary", {}, "⇪ " + t("musicUpload")),
        el("div", { class: "form" }, field(t("musicFile"), file), field(t("musicTitle"), title), field(t("musicArtist"), artist),
          field(t("licence") + " *", lic), field(t("musicMood"), mood), err,
          el("button", { class: "btn primary", onclick: async () => {
            err.textContent = "";
            if (!file.files.length) { err.textContent = t("musicNoFile"); return; }
            if (!lic.value.trim()) { err.textContent = t("licenceRequired"); lic.focus(); return; }
            const fd = new FormData();
            fd.append("file", file.files[0], file.files[0].name);
            fd.append("licence", lic.value.trim()); fd.append("title", title.value.trim()); fd.append("artist", artist.value.trim());
            fd.append("mood", mood.value.trim()); fd.append("project", pid);
            toast(t("uploading", { n: 1 }));
            try {
              const m = await api("POST", "/api/library/music", fd, true);
              await flush(); await loadMusic(); await refreshAssets(true);
              b.music = { volume_db: -20, duck: true, duck_db: -10, fade_in: 1, fade_out: 2.5, start: 0, ...(b.music || {}), track: m.id };
              changed({ now: true }); toast(t("musicAdded", { name: m.title }), "ok"); setTab("music");
            } catch (e) { err.textContent = e.message; }
          } }, t("musicAddBtn")))));
      panel.replaceChildren(warnBox, el("div", { class: "form" }, el("h3", {}, "♪ " + t("music")), ...kids));
      renderWarnings();
    }

    // ---------- JSON tab ----------
    function renderJson() {
      const ta = el("textarea", { class: "mono json", spellcheck: "false", "aria-label": t("tabJson"), "data-native-undo": "1" });
      ta.value = JSON.stringify(S.brief, null, 2);
      const err = el("div", { class: "error small", role: "alert" });
      panel.replaceChildren(warnBox, el("div", { class: "row gap wrap" },
        el("button", { class: "btn primary", onclick: async () => {
          let raw;
          try { raw = JSON.parse(ta.value); } catch (e) { err.textContent = t("jsonInvalid", { msg: e.message }); return; }
          try {
            const r = await api("PUT", `/api/projects/${pid}`, { brief: raw });
            if (UN.burst) settle();
            if (UN.stable != null) UN.undo.push(UN.stable);
            UN.redo = []; UN.stable = JSON.stringify(r.brief);
            S.warnings = r.warnings; S.version++; S.savedVersion = S.version;
            err.textContent = ""; toast(t("jsonApplied"), "ok"); showWarnings(r.warnings);
            adoptBrief(r.brief); refreshTimeline(); renderUndo();
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
      } else if (j.kind === "music" && r.track) {
        toast(t("ai.musicReady", { name: r.title || r.track }), "ok");
        await loadMusic().catch(() => {});
        if (j.params && j.params.assign_project === pid) {
          S.brief.music = { volume_db: -20, duck: true, duck_db: -10, fade_in: 1, fade_out: 2.5, start: 0, ...(S.brief.music || {}), track: r.track };
          changed({ now: true }); refreshTimeline();
        }
        if (S.tab === "music") renderMusic();
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
      const provs = (AI.status && AI.status.providers) || [];
      const provName = id => (provs.find(x => x.id === id) || {}).name || id;
      const projProv = S.brief.broll_provider || "wan22-5b";
      box.append(el("p", { class: "muted small" }, t("ai.bgProvider", { p: provName(bg.provider || projProv) }) +
        (bg.provider ? "" : ` (${t("ai.projectDefault")})`) + (bg.image ? ` · ${t("ai.bgAnimates", { a: bg.image })}` : "")));
      if (cur && cur.provider) box.append(el("p", { class: "muted tiny" }, t("ai.clipFrom", { p: provName(cur.provider), l: cur.licence || "" })));
      if (bg.clip && S.lib && !cur) box.append(el("p", { class: "warn small" }, t("ai.bgMissing", { id: bg.clip })));
      if (!bg.clip) box.append(el("p", { class: "muted small" }, t("ai.bgNone")));
      if (cur && ((cur.w > cur.h) !== (S.brief.format === "16:9"))) {
        box.append(el("p", { class: "warn small" }, t("ai.bgOrientWarn", { o: cur.w > cur.h ? t("horizontal") : t("vertical"), f: S.brief.format })));
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
          provider: bg.provider || projProv, image: bg.image || "",
          images: Object.entries(S.assets).filter(([, m]) => m.type === "image").map(([n]) => ({ value: n, label: n, ref: { project: pid, asset: n } }))
            .concat((S.portraits || []).map(m => ({ value: m.id, label: `${t("ai.kinds.portrait")}: ${m.name || m.id}`, ref: { portrait: m.id } }))),
          assign: { project: pid, scene: sc.id, dim: bg.dim, blur: bg.blur },
          onQueued: (j, o) => {
            if (o) {    // remember the choice in the brief, so "Make video" regenerates it the same way
              if (o.provider && o.provider !== projProv) bg.provider = o.provider; else delete bg.provider;
              if (o.image) bg.image = o.image; else delete bg.image;
              changed({ now: true });
            }
            onAiJob(j);
          } })));
      if (!S.portraits) loadPortraits().catch(() => {});
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
      const provs = (AI.status && AI.status.providers) || [];
      const provSel = el("select", { id: "proj-prov", onchange: e => {
        if (e.target.value === "wan22-5b") delete b.broll_provider; else b.broll_provider = e.target.value; changed({ now: true }); } },
        provs.map(p2 => el("option", { value: p2.id, selected: (b.broll_provider || "wan22-5b") === p2.id }, `${p2.name} — ${p2.licence}`)));
      const provField = el("div", { class: "form mt" }, el("h3", {}, t("ai.projectProvider")),
        el("p", { class: "muted small" }, t("ai.projectProviderHelp")), provs.length ? provSel : el("p", { class: "muted small" }, "…"));
      panel.replaceChildren(warnBox, f, provField,
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

    // ---------- templates: picker drawer (thumbnails + live preview), options, import ----------
    const curTplId = () => S.brief.template || "aurora";
    const tplOf = id => S.templates.find(m => m.id === id) || null;
    async function loadTemplates() {
      try { S.templates = await api("GET", "/api/templates"); } catch { S.templates = []; }
      return S.templates;
    }
    const previewFor = (m, fmt) => {
      const pv = (m && m.previews) || [];
      const want = `preview-${fmtSlug(fmt || S.fmt)}`;
      return pv.find(u => u.includes(want)) || pv[0] || null;
    };
    function templateButton() {
      const m = tplOf(curTplId());
      const src = previewFor(m);
      return el("button", { type: "button", class: "btn tpl-btn", "aria-haspopup": "dialog", title: t("tpl.pick"), onclick: () => templateDrawer() },
        src ? el("img", { src, alt: "", class: `tpl-thumb fmt-${fmtSlug(S.fmt)}` }) : el("span", { class: "tpl-thumb ph" }),
        el("span", { class: "tpl-name" }, m ? lbl(m.name) : curTplId()), el("span", { class: "muted" }, "▾"));
    }
    let drawerEl = null;
    function closeDrawer() {
      if (!drawerEl) return;
      drawerEl.remove(); drawerEl = null;
      if (S.tplPreview) { S.tplPreview = null; refreshTimeline(); }
    }
    let hoverTimer = null;
    function hoverPreview(id) {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(() => {
        const want = id && id !== curTplId() ? id : null;
        if (want === S.tplPreview) return;
        S.tplPreview = want;
        refreshTimeline();
      }, 140);
    }
    function templateDrawer(focusId) {
      if (drawerEl) { closeDrawer(); }
      const list = el("div", { class: "tpl-grid", role: "listbox", "aria-label": t("tpl.template") });
      const optsBox = el("div", { class: "tpl-opts" });
      const importBox = el("div", { class: "tpl-import" });
      const draw = () => {
        list.replaceChildren(...S.templates.map(m => {
          const on = m.id === curTplId();
          const src = previewFor(m);
          const card = el("div", { class: `tpl-card ${on ? "on" : ""} ${m.error ? "broken" : ""}`, role: "option", "aria-selected": on ? "true" : "false",
            tabindex: "0", "data-id": m.id,
            onmouseenter: () => { if (!m.error) hoverPreview(m.id); }, onfocus: () => { if (!m.error) hoverPreview(m.id); },
            onclick: e => { if (!e.target.closest(".tpl-del") && !m.error) pickTemplate(m.id); },
            onkeydown: e => { if ((e.key === "Enter" || e.key === " ") && !m.error) { e.preventDefault(); pickTemplate(m.id); } } },
            el("div", { class: `tpl-shot fmt-${fmtSlug(S.fmt)}` }, src ? el("img", { src, alt: "", loading: "lazy" }) : el("span", { class: "muted small" }, m.error ? "⚠" : "—")),
            el("div", { class: "tpl-body" },
              el("div", { class: "row between" }, el("b", {}, lbl(m.name) || m.id),
                el("span", { class: `kbadge ${m.kind || ""}` }, m.kind ? t("tpl.kinds." + m.kind) : "")),
              m.error ? el("div", { class: "error small" }, m.error) : el("div", { class: "muted small tpl-desc" }, lbl(m.description) || ""),
              el("div", { class: "muted tiny-l" }, [m.author, m.version ? `v${m.version}` : "", m.licence].filter(Boolean).join(" · ")),
              on ? el("div", { class: "ok-t small" }, "✓ " + t("tpl.inUse")) : null),
            !m.builtin ? el("button", { class: "icon-btn danger tpl-del", title: t("delete"), "aria-label": `${t("delete")} ${lbl(m.name)}`, onclick: async () => {
              if (!confirmBox(t("tpl.confirmDelete", { name: lbl(m.name) }))) return;
              try { await api("DELETE", `/api/templates/${m.id}`); await loadTemplates();
                if (curTplId() === m.id) { delete S.brief.template; delete S.brief.template_options; changed({ now: true }); renderSettings(); }
                draw(); } catch (e) { toast(e.message, "error"); } } }, "✕") : null);
          return card;
        }));
        drawOptions();
      };
      const drawOptions = () => {
        const m = tplOf(curTplId());
        const opts = (m && m.options) || [];
        if (!opts.length) { optsBox.replaceChildren(el("p", { class: "muted small" }, t("tpl.noOptions", { name: m ? lbl(m.name) : curTplId() }))); return; }
        const cur = S.brief.template_options || {};
        const setOpt = (k, v) => { S.brief.template_options = { ...(S.brief.template_options || {}), [k]: v }; changed({ now: true }); };
        optsBox.replaceChildren(el("h3", {}, t("tpl.options", { name: lbl(m.name) })), ...opts.map(o => {
          const v = cur[o.key] ?? o.default;
          const id = `to-${o.key}`;
          let inp;
          if (o.type === "boolean") {
            inp = el("input", { type: "checkbox", id, checked: !!v, onchange: e => setOpt(o.key, e.target.checked) });
            return el("label", { class: "row gap", for: id }, inp, el("span", {}, lbl(o.label) || o.key));
          }
          if (o.type === "select") inp = el("select", { id, onchange: e => setOpt(o.key, e.target.value) },
            (o.choices || []).map(c => el("option", { value: c.value, selected: c.value === v }, lbl(c.label) || c.value)));
          else if (o.type === "color") inp = el("input", { type: "color", id, value: v || "#000000", oninput: e => setOpt(o.key, e.target.value) });
          else if (o.type === "number") inp = el("input", { type: "number", id, value: v ?? "", min: o.min, max: o.max, step: o.step || "any", class: "narrow",
            onchange: e => setOpt(o.key, parseFloat(e.target.value)) });
          else inp = el("input", { type: "text", id, value: v ?? "", oninput: e => setOpt(o.key, e.target.value) });
          return el("div", { class: "field" }, el("label", { for: id }, lbl(o.label) || o.key), inp, o.help ? el("span", { class: "muted small" }, lbl(o.help)) : null);
        }));
      };
      const pickTemplate = id => {
        if (id === curTplId()) return;
        if (id === "aurora") delete S.brief.template; else S.brief.template = id;
        // options belong to one template; transitions it added are dropped when the new one lacks them
        delete S.brief.template_options;
        S.tplPreview = null;
        changed({ now: true });
        renderSettings(); renderPanel(); draw();
        toast(t("tpl.applied", { name: lbl((tplOf(id) || {}).name) || id }), "ok", 2200);
      };
      // import (.zip)
      const file = el("input", { type: "file", accept: ".zip,application/zip", id: "tpl-zip" });
      const repl = el("input", { type: "checkbox", id: "tpl-replace" });
      const out = el("div", { class: "tpl-result", "aria-live": "polite" });
      importBox.replaceChildren(el("h3", {}, "⇪ " + t("tpl.import")), el("p", { class: "muted small" }, t("tpl.importHelp")),
        el("div", { class: "row gap wrap" }, file,
          el("label", { class: "row gap small", for: "tpl-replace" }, repl, el("span", {}, t("tpl.replace"))),
          el("button", { class: "btn primary", onclick: async () => {
            out.replaceChildren();
            if (!file.files.length) { out.append(el("p", { class: "error small" }, t("tpl.chooseZip"))); return; }
            const fd = new FormData();
            fd.append("file", file.files[0], file.files[0].name);
            try {
              const m = await api("POST", `/api/templates${repl.checked ? "?replace=1" : ""}`, fd, true);
              await loadTemplates(); draw();
              out.append(el("p", { class: "ok-t small" }, "✓ " + t("tpl.imported", { name: lbl(m.name) })),
                ...(m.warnings && m.warnings.length ? [el("ul", { class: "warn small" }, m.warnings.map(w => el("li", {}, w)))] : []));
              file.value = "";
              const c = list.querySelector(`[data-id="${m.id}"]`); if (c) c.scrollIntoView({ block: "nearest" });
            } catch (e) {
              const errs = (e.data && e.data.errors) || [e.message];
              out.append(el("div", { class: "errbox" }, el("b", {}, t("tpl.importFailed")), el("ul", {}, errs.map(x => el("li", {}, x)))));
            }
          } }, t("tpl.importBtn"))), out,
        el("p", { class: "muted small" }, t("tpl.docs"), " ", el("code", {}, "video/templates/TEMPLATES.md"), " · ", el("code", {}, "validate.py")));
      drawerEl = el("aside", { class: "drawer", role: "dialog", "aria-label": t("tpl.template"),
        onmouseleave: () => hoverPreview(null), onkeydown: e => { if (e.key === "Escape") closeDrawer(); } },
        el("div", { class: "drawer-head" }, el("h2", {}, t("tpl.template")),
          el("div", { class: "row gap" },
            el("button", { class: "btn small", title: t("tpl.reloadHelp"), onclick: async () => { await loadTemplates(); draw(); refreshTimeline(); } }, "↻ " + t("tpl.reload")),
            el("button", { class: "icon-btn", "aria-label": t("close"), onclick: closeDrawer }, "✕"))),
        el("div", { class: "drawer-body" }, el("p", { class: "muted small" }, t("tpl.help")), list, optsBox, importBox));
      document.body.append(drawerEl);
      draw();
      (list.querySelector(".tpl-card.on") || list.querySelector(".tpl-card") || drawerEl).focus();
    }

    // ---------- transitions: tiny animated previews ----------
    const TXB = window.VS_TRANSITIONS || {};
    S.txMods = {};
    async function tplTransitions() {
      const m = tplOf(curTplId());
      if (!m || !m.has_js || !(m.transitions || []).length) return {};
      const key = `${m.id}@${m.hash}`;
      if (!S.txMods[key]) S.txMods[key] = import(`/templates/${m.id}/template.js?v=${m.hash}`).then(x => x.transitions || {}).catch(() => ({}));
      return S.txMods[key];
    }
    const txName = id => {
      const m = tplOf(curTplId());
      const own = m && (m.transitions || []).find(x => x.id === id);
      return own ? lbl(own.name) : t("tx.names." + id) !== "tx.names." + id ? t("tx.names." + id) : id;
    };
    const txIds = () => [...(TXB.order || []), ...(((tplOf(curTplId()) || {}).transitions || []).map(x => x.id))];
    const txDefault = () => ((tplOf(curTplId()) || {}).default_transition) || "lift";
    const txIdOf = v => (v && typeof v === "object" ? v.type : v) || null;
    // a 64x40 mini stage: scene A (brand primary) hands over to scene B (accent) with transition `id`
    function txTile(id, { on = false, label = null, onPick = null, sub = null } = {}) {
      const box = el("span", { class: "tx-mini", "aria-hidden": "true" });
      const a = box.appendChild(el("i", { class: "tx-a" }, "A")), b = box.appendChild(el("i", { class: "tx-b" }, "B"));
      const flash = box.appendChild(el("i", { class: "tx-flash" }));
      let raf = 0, t0 = 0, fn = null;
      const W = 64, H = 40, rtl = S.lang === "ar";
      const apply = (node, st) => {
        st = st || {};
        node.style.opacity = String(st.o ?? 1);
        node.style.transform = `translate(${((st.x || 0) * W / 1080).toFixed(2)}px, ${((st.y || 0) * H / 1920).toFixed(2)}px) scale(${st.s ?? 1}) rotate(${st.r || 0}deg)`;
        node.style.filter = st.blur ? `blur(${(st.blur * W / 1080).toFixed(2)}px)` : "none";
        node.style.clipPath = st.clip ? `inset(${st.clip.map(v => `${(v * 100).toFixed(1)}%`).join(" ")})` : "";
        return st.flash || 0;
      };
      const frame = p => {
        if (!fn) return;
        const c = { W: 1080, H: 1920, rtl };
        apply(a, p >= 1 ? { o: 0 } : fn.out(p, c));
        const f = apply(b, p <= 0 ? { o: 0 } : fn.in(p, c));
        flash.style.opacity = String(Math.min(1, f));
        if (p <= 0) b.style.opacity = "0";
      };
      const resolve = async () => { const own = await tplTransitions(); fn = own[id] || TXB[id] || null; frame(0.5); };
      resolve();
      const loop = now => {
        const cyc = 1.6, k = ((now - t0) / 1000) % cyc;
        // hold A, play the transition over its own window (scaled to 0.9 s), hold B
        frame(Math.max(0, Math.min(1, (k - 0.3) / 0.9)));
        raf = requestAnimationFrame(loop);
      };
      const start = () => { cancelAnimationFrame(raf); t0 = performance.now(); raf = requestAnimationFrame(loop); };
      const stop = () => { cancelAnimationFrame(raf); frame(0.5); };
      const btn = el(onPick ? "button" : "span", { type: onPick ? "button" : null, class: `tx-tile ${on ? "on" : ""}`, title: label || txName(id),
        role: onPick ? "radio" : null, "aria-checked": onPick ? (on ? "true" : "false") : null,
        onmouseenter: start, onmouseleave: stop, onfocus: start, onblur: stop, onclick: onPick ? () => onPick(id) : null },
        box, el("span", { class: "tx-label" }, label || txName(id)), sub ? el("span", { class: "tx-sub muted" }, sub) : null);
      return btn;
    }
    function transitionButton() {
      const cur = txIdOf(S.brief.transition);
      const id = cur || txDefault();
      const tile = txTile(id, { label: cur ? txName(cur) : `${t("tx.templateDefault")} · ${txName(txDefault())}` });
      return el("button", { type: "button", class: "btn tx-btn", "aria-haspopup": "dialog", title: t("tx.pickProject"),
        onclick: e => txPopover(e.currentTarget, cur, v => {
          if (v) S.brief.transition = v; else delete S.brief.transition;
          changed({ now: true }); renderSettings();
        }, t("tx.projectHelp")) }, tile, el("span", { class: "muted" }, "▾"));
    }
    let popEl = null;
    function txPopover(anchor, cur, onPick, help) {
      if (popEl) { popEl.remove(); popEl = null; }
      const r = anchor.getBoundingClientRect();
      const close = () => { if (popEl) { popEl.remove(); popEl = null; document.removeEventListener("mousedown", outside, true); } };
      const outside = e => { if (popEl && !popEl.contains(e.target) && !anchor.contains(e.target)) close(); };
      popEl = el("div", { class: "popover", role: "dialog", "aria-label": t("tx.transition"), onkeydown: e => { if (e.key === "Escape") { close(); anchor.focus(); } } },
        el("p", { class: "muted small" }, help),
        txGrid(cur, v => { onPick(v); close(); }));
      popEl.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 470))}px`;
      popEl.style.top = `${r.bottom + 6 + scrollY}px`;
      document.body.append(popEl);
      document.addEventListener("mousedown", outside, true);
      const f = popEl.querySelector(".tx-tile.on") || popEl.querySelector(".tx-tile"); f && f.focus();
    }
    function txGrid(cur, onPick, { defaultLabel = null } = {}) {
      const grid = el("div", { class: "tx-grid", role: "radiogroup", "aria-label": t("tx.transition") });
      grid.append(txTile(txDefault(), { on: !cur, label: defaultLabel || t("tx.templateDefault"), sub: txName(txDefault()), onPick: () => onPick(null) }));
      for (const id of txIds()) grid.append(txTile(id, { on: cur === id, onPick: () => onPick(id) }));
      if (cur && !txIds().includes(cur)) grid.append(el("p", { class: "warn small" }, t("tx.notInTemplate", { name: cur })));
      return grid;
    }
    function sceneTransition(sc) {
      const cur = txIdOf(sc.transition);
      const proj = txIdOf(S.brief.transition) || txDefault();
      const box = el("div", { class: "field tx-section" });
      box.append(el("div", { class: "row between" }, el("span", { class: "lbl" }, t("tx.intoScene")),
        el("span", { class: "muted small" }, cur ? txName(cur) : `${t("tx.projectDefault")} (${txName(proj)})`)),
        txGrid(cur, v => { if (v) sc.transition = v; else delete sc.transition; changed({ now: true }); renderSceneForm(); },
          { defaultLabel: t("tx.projectDefault") }));
      return box;
    }

    // ---------- version history ----------
    async function renderHistory() {
      const box = el("div", { class: "form history" },
        el("div", { class: "row between" }, el("h3", {}, "🕘 " + t("hist.title")),
          el("span", { class: "muted small" }, t("hist.shortcut"))),
        el("p", { class: "muted small" }, t("hist.help")));
      const listEl = el("ol", { class: "hist-list" }, el("li", { class: "muted small" }, "…"));
      box.append(listEl);
      panel.replaceChildren(warnBox, box);
      renderWarnings();
      await flush();
      let items = [];
      try { items = await api("GET", `/api/projects/${pid}/history`); } catch (e) { listEl.replaceChildren(el("li", { class: "error small" }, e.message)); return; }
      if (S.tab !== "history") return;
      if (!items.length) { listEl.replaceChildren(el("li", { class: "muted small" }, t("hist.empty"))); return; }
      const fmtD = ts => fmtDate(ts, true);
      listEl.replaceChildren(...items.map((h, i) => el("li", { class: `hist-item ${i === 0 ? "cur" : ""}` },
        el("div", { class: "hist-main" },
          el("div", { class: "row gap wrap" }, el("b", {}, fmtD(h.updated || h.created)),
            el("span", { class: "muted small" }, ago(h.updated || h.created)),
            el("span", { class: `kbadge k-${h.kind}` }, t("hist.kinds." + h.kind) !== "hist.kinds." + h.kind ? t("hist.kinds." + h.kind) : h.kind),
            h.scenes != null ? el("span", { class: "muted small" }, t("scenesCount", { n: h.scenes })) : null),
          el("div", { class: "small hist-sum" }, h.summary || "")),
        i === 0 ? el("span", { class: "ok-t small nowrap" }, t("hist.current"))
          : el("button", { class: "btn small", onclick: async () => {
            await flush();
            try {
              const r = await api("POST", `/api/projects/${pid}/history/${h.id}/restore`);
              if (UN.burst) settle();
              if (UN.stable != null) UN.undo.push(UN.stable);
              UN.redo = [];
              applyState(JSON.stringify(r.brief), { saved: true });
              S.warnings = r.warnings || [];
              toast(t("hist.restored", { when: fmtD(h.updated || h.created) }), "ok");
              if (S.tab === "history") renderHistory();
            } catch (e) { toast(e.message, "error"); }
          } }, "↺ " + t("hist.restore")))));
    }

    // ---------- "Ask the AI for missing material" ----------
    async function followupDialog() {
      await flush();
      let r;
      try { r = await api("GET", `/api/projects/${pid}/followup`); } catch (e) { toast(e.message, "error"); return; }
      const ta = el("textarea", { class: "mono tall", spellcheck: "false", "aria-label": t("fu.title"), "data-native-undo": "1" });
      ta.value = r.message;
      const kinds = Object.entries(r.counts || {});
      modal(t("fu.title"), el("div", { class: "form" },
        el("p", { class: "muted small" }, t("fu.intro")),
        el("div", { class: "row gap wrap" }, r.ok ? el("span", { class: "chipx ok" }, "✓ " + t("fu.allGood")) : null,
          ...kinds.map(([k, n]) => el("span", { class: `chipx ${k === "suggestion" ? "" : "bad"}` }, `${t("fu.kinds." + k) !== "fu.kinds." + k ? t("fu.kinds." + k) : k}: ${n}`))),
        ta, el("p", { class: "muted small" }, t("fu.api"), " ", el("code", {}, `GET /api/projects/${pid}/followup`))),
        [{ label: t("close"), onClick: () => {} }, { label: "⧉ " + t("copy"), primary: true, onClick: () => { copyText(ta.value); } }]);
    }

    // ---------- preview ----------
    const preview = (() => {
      const frame = el("iframe", { class: "pv-frame", src: "/renderer/index.html?embed", title: t("preview"), tabindex: "-1" });
      const frameBox = el("div", { class: "pv-stage" }, frame);
      const audio = new Audio();
      audio.preload = "auto";
      let tl = null, loaded = false, playing = false, cur = 0, t0Perf = 0, t0 = 0, audioKey = null, raf = 0;
      const playBtn = el("button", { class: "btn round", "aria-label": t("play"), onclick: () => toggle() }, "▶");
      const scrub = el("input", { type: "range", min: 0, max: 10, step: 0.01, value: 0, "aria-label": t("time"),
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
      function setFormat(fmt) { frameBox.classList.toggle("land", fmt === "16:9"); frameBox.classList.toggle("square", fmt === "1:1"); }
      // background music: plays along with the voice-over, approximate ducking from the server's envelope
      const mus = new Audio();
      mus.preload = "auto"; mus.loop = true;
      let M = null;
      function musicAt(t1) {
        if (!M) return 0;
        let db = M.gain_db;
        if (M.duck && M.duck.length) {
          const x = t1 / (M.duck_step || 0.1), i = Math.floor(x), f = x - i;
          const a = M.duck[Math.min(i, M.duck.length - 1)] || 0, b = M.duck[Math.min(i + 1, M.duck.length - 1)] || 0;
          db += a + (b - a) * f;
        }
        let g = Math.pow(10, db / 20);
        if (M.fade_in > 0) g *= Math.sin(Math.min(1, Math.max(0, t1 / M.fade_in)) * Math.PI / 2);
        const D = tl ? tl.duration : 0;
        if (M.fade_out > 0 && t1 > D - M.fade_out) g *= Math.cos(Math.min(1, (t1 - (D - M.fade_out)) / M.fade_out) * Math.PI / 2);
        return Math.max(0, Math.min(1, g));
      }
      function syncMusic(force) {
        if (!M || !mus.src) return;
        if (!playing) { if (!mus.paused) mus.pause(); return; }
        const want = ((M.start || 0) + cur) % Math.max(0.1, M.duration || mus.duration || 1);
        if (mus.paused) { mus.currentTime = want; mus.play().catch(() => {}); }
        else if (force || Math.abs(mus.currentTime - want) > 0.35) mus.currentTime = want;
        mus.volume = musicAt(cur);
      }
      function load(newTl) {
        tl = newTl;
        setFormat(tl.format);
        scrub.max = tl.duration.toFixed(2);
        if (cur > tl.duration) cur = 0;
        marks.replaceChildren(...tl.scenes.map((s, i) => el("i", { style: { left: `${(s.start / tl.duration) * 100}%`, width: `${((s.end - s.start) / tl.duration) * 100}%` },
          class: s.voiced ? "v" : "", title: s.id }, String(i + 1))));
        info.textContent = `${tl.language.toUpperCase()} · ${tl.duration.toFixed(1)} s · ${tl.width}×${tl.height} · ` + t("voicedCount", { v: tl.voiced, n: tl.voice_needed }) +
          (tl.timing && tl.timing.voiced ? ` · ${t("timingCount", { a: tl.timing.aligned, n: tl.timing.voiced })}` : "") +
          (tl.voiced < tl.voice_needed ? ` · ${t("estimatedTiming")}` : "") +
          (tl.music ? ` · ♪ ${tl.music.title}` : "");
        const nm = tl.music || null;
        if (!nm) { M = null; mus.pause(); mus.removeAttribute("src"); }
        else { const was = M && M.url; M = nm; if (was !== nm.url) { mus.src = nm.url; mus.load(); } if (playing) syncMusic(true); }
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
        syncMusic(false);
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
        syncMusic(true);
        playBtn.textContent = "❚❚"; playBtn.setAttribute("aria-label", t("pause"));
        cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
      }
      function pause() {
        cur = now();
        playing = false;
        audio.pause();
        mus.pause();
        win() && win().setPlaying && win().setPlaying(false);
        playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", t("play"));
        cancelAnimationFrame(raf);
        draw();
      }
      function toggle() { playing ? pause() : play(); }
      function seek(x) {
        cur = Math.max(0, Math.min(x, tl ? tl.duration : x));
        if (playing) { t0 = cur; t0Perf = performance.now(); if (audio.src) audio.currentTime = cur; syncMusic(true); }
        draw();
      }
      return { load, seek, toggle, pause, setFormat, get t() { return cur; }, get musicVolume() { return M ? musicAt(cur) : null; },
        stop: () => { pause(); audio.src = ""; mus.removeAttribute("src"); } };
    })();

    window.SUI.preview = preview;   // read-only handle for UI tests (time, music volume)

    // ---------- voice / render / jobs ----------
    const clip = new Audio();
    async function listen(sceneId, voiced) {
      if (voiced) {
        preview.pause();
        clip.src = `/api/projects/${pid}/voice/${encodeURIComponent(sceneId)}.wav?lang=${S.lang}&ts=${Date.now()}`;
        clip.play().catch(e => toast(e.message, "error"));
        return;
      }
      await flush();
      startJob("voice", { scene: sceneId, lang: S.lang }, () => listen(sceneId, true));
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
          "🎙 " + (allVoiced ? t("regenerate") : t("generateVoice")) + (multi() ? ` (${langs().length})` : "")),
        el("button", { class: "btn primary", disabled: busy("render"), title: `${langs().join(", ")} × ${fmts().join(", ")}`, onclick: async () => {
          await flush();
          if (!OV.ok && !(tl && tl.voiced >= tl.voice_needed)) {
            if (!confirmBox(t("renderSilentConfirm"))) return;
            startJob("render", { allow_silent: true });
          } else startJob("render", {});
        } }, "⬇ " + t("render") + (langs().length * fmts().length > 1 ? ` (${langs().length * fmts().length})` : "")),
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
      const fileUrl = (n, dl) => `/api/projects/${pid}/renders/${encodeURIComponent(n)}${dl ? "?download=1" : ""}`;
      // one batch = one render job (every language x format); older batches fold away
      const batchOf = r => r.batch || (r.name.match(/(\d{8}-\d{6})/) || [r.name])[0];
      const batches = [];
      for (const r of list) { const k = batchOf(r); let g = batches.find(x => x.k === k); if (!g) batches.push(g = { k, items: [] }); g.items.push(r); }
      const item = r => el("li", {},
          r.cover ? el("a", { class: "r-cover", href: fileUrl(r.cover), target: "_blank", title: t("cover") },
            el("img", { src: fileUrl(r.cover), alt: t("cover"), loading: "lazy", class: `fmt-${fmtSlug(r.format)}` })) : null,
          el("div", { class: "r-main" }, el("b", { class: "mono small" }, r.name),
            el("div", { class: "muted small" }, [r.language ? r.language.toUpperCase() : "", r.format, r.duration ? `${r.duration.toFixed(1)} s` : "", fmtBytes(r.size),
              r.timing && r.timing.voiced ? t("timingCount", { a: r.timing.aligned, n: r.timing.voiced }) : "",
              r.music && r.music.lufs != null ? `♪ ${r.music.lufs.toFixed(1)} LUFS` : "",
              r.voice_needed && r.voiced < r.voice_needed ? "⚠ " + t("partlySilent") : ""].filter(Boolean).join(" · ")),
            el("div", { class: "row gap wrap r-links" },
              r.srt ? el("a", { class: "small", href: fileUrl(r.srt, 1) }, ".srt") : null,
              r.vtt ? el("a", { class: "small", href: fileUrl(r.vtt, 1) }, ".vtt") : null,
              r.cover ? el("a", { class: "small", href: fileUrl(r.cover, 1) }, t("coverPng")) : null)),
          el("div", { class: "row gap" },
            el("button", { class: "btn small", onclick: () => watch(r) }, "▶ " + t("watch")),
            el("a", { class: "btn small", href: fileUrl(r.name, 1) }, t("download")),
            el("button", { class: "icon-btn danger", "aria-label": `${t("delete")} ${r.name}`, onclick: async () => {
              if (!confirmBox(t("confirmDeleteRender", { name: r.name }))) return;
              await api("DELETE", `/api/projects/${pid}/renders/${encodeURIComponent(r.name)}`);
              await refreshAssets(); renderRenders(); } }, "✕")));
      const older = batches.slice(1);
      rendersBox.replaceChildren(el("h3", {}, t("renders")),
        ...(list.length ? [el("ul", { class: "render-list" }, batches[0].items.map(item)),
          older.length ? el("details", { class: "older-renders" }, el("summary", { class: "muted small" }, t("olderRenders", { n: older.reduce((a, g) => a + g.items.length, 0) })),
            el("ul", { class: "render-list" }, older.flatMap(g => g.items).map(item))) : ""] : [el("p", { class: "muted small" }, t("noRenders"))]));
    }
    function watch(r) {
      preview.pause();
      const v = el("video", { src: `/api/projects/${pid}/renders/${encodeURIComponent(r.name)}`, controls: true, autoplay: true,
        class: `render-video ${r.format === "16:9" ? "land" : ""}`, crossorigin: "anonymous" });
      if (r.vtt) v.append(el("track", { kind: "subtitles", src: `/api/projects/${pid}/renders/${encodeURIComponent(r.vtt)}`,
        srclang: r.language || "en", label: (r.language || "").toUpperCase() || t("subtitles") }));
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
        if (S.make.status === "done" && was !== "done") {
          const rs = (S.make.result || {}).renders || [(S.make.result || {}).render];
          toast(rs.length > 1 ? t("make.doneMany", { n: rs.length }) : t("make.done", { name: rs[0] || "" }), "ok", 8000);
        }
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
      // Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y: project undo/redo everywhere except the raw JSON editor
      const k = (e.key || "").toLowerCase();
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (k === "z" || k === "y") && !$(".modal-wrap") &&
          !(e.target.dataset && e.target.dataset.nativeUndo)) {
        e.preventDefault();
        if (k === "y" || e.shiftKey) redo(); else undo();
        return;
      }
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
      catch (e) { view.replaceChildren(el("section", { class: "page" }, el("h1", {}, t("notFound")), el("p", {}, e.message), el("a", { href: "#/" }, "← " + t("back")))); return; }
      await loadTemplates();
      renderCrumb();
      await checkOvoz();
      if (fixVoice()) changed();
      renderSettings();
      renderSceneList();
      setTab("scene");
      renderRenders();
      renderActions();
      renderPreviewBar();
      renderCover();
      preview.setFormat(S.fmt);
      transport.parentElement.querySelector(".pv-info").after(el("p", { class: "muted small shortcuts" }, t("shortcuts")));
      await refreshTimeline();
      AI.loadStatus().catch(() => {});
      pollAi();
      renderMake();
      if (makeActive()) pollMake();
      const st = S.tl && S.tl.scenes[0];
      if (st) preview.seek(Math.min(1.6, st.end - 0.3));
      settle();
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
      musPrev.pause();
      clearTimeout(alignTimer);
      clearTimeout(UN.timer);
      closeDrawer();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("ovoz", onOvoz);
      removeEventListener("beforeunload", onUnload);
    };
  }

  applyStatic();
  route();
})();
