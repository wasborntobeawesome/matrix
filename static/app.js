const $ = (id) => document.getElementById(id);
const text = $("text"), count = $("count"), notice = $("notice"), latin = $("latin");
const rate = $("rate"), pitch = $("pitch"), speak = $("speak"), player = $("player");
const download = $("download"), error = $("error"), result = $("result");
const voicesEl = $("voices"), modelsEl = $("models"), modelDesc = $("modelDesc");
const commercialOnly = $("commercialOnly");

// [chip label, text] per content language; the first one is the default text.
const SAMPLES = {
  uz: [
    ["Lotin", "Assalomu alaykum! Oʻzbekiston — goʻzal va qadimiy mamlakat. Bugun 2026-yil 14-noyabr."],
    ["Kirill", "Ассалому алайкум! Тошкент — Ўзбекистоннинг пойтахти. Шаҳар аҳолиси уч миллиондан ортиқ."],
    ["Yangilik", "Samarqand shahrida xalqaro ilmiy anjuman boʻlib oʻtdi. Unda 30 dan ortiq davlatdan kelgan olimlar ishtirok etdi va sunʼiy intellekt, taʼlim hamda madaniy meros masalalari muhokama qilindi."],
    ["Reklama", "Yangi ilovamiz bilan har kuni vaqtingizni tejang! Hoziroq yuklab oling — birinchi oy mutlaqo bepul."],
  ],
  ru: [
    ["Reklama", "Экономьте время каждый день с нашим новым приложением! Скачайте прямо сейчас — первый месяц бесплатно."],
    ["Ilova tavsifi", "Более 50 000 пользователей уже выбрали нас. Простой интерфейс, быстрая доставка и поддержка круглосуточно."],
  ],
  en: [
    ["Reklama", "Save time every day with our new app! Download it now — your first month is completely free."],
    ["Ilova tavsifi", "Join more than 50,000 happy users. Simple design, fast delivery, and support around the clock."],
  ],
  ar: [
    ["Reklama", "وفّر وقتك كل يوم مع تطبيقنا الجديد! حمّله الآن، والشهر الأول مجاني تمامًا."],
    ["Ilova tavsifi", "انضم إلى أكثر من 50,000 مستخدم سعيد. تصميم بسيط، وتوصيل سريع، ودعم على مدار الساعة."],
  ],
};
const ALL_SAMPLES = new Set(Object.values(SAMPLES).flat().map(([, t]) => t));

// When index.html is opened straight from disk (file://), talk to the local server.
const API = location.protocol === "file:" ? "http://localhost:8000" : "";
const OFFLINE = "Serverga ulanib boʻlmadi. Server ishga tushirilganini tekshiring (start.bat yoki: uvicorn app:app).";

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

let models = [];
let lang = ["uz", "ru", "en", "ar"].includes(store.get("lang")) ? store.get("lang") : "uz";  // content language
let model = null;   // selected model object
let voice = null;   // selected voice id
let style = null;   // selected emotion/style id (models that support it)
let audioUrl = null;

async function api(path, options) {
  try {
    return await fetch(API + path, options);
  } catch {
    throw new Error(OFFLINE);
  }
}

const hasCyrillic = (s) => /[Ѐ-ӿ]/.test(s);

function updateMeta() {
  count.textContent = `${text.value.length} / 5000`;
  notice.hidden = lang !== "uz" || !hasCyrillic(text.value);
}

const speaks = (m) => (m.languages || ["uz"]).includes(lang);

function setLang(l) {
  lang = l;
  store.set("lang", l);
  document.querySelectorAll("#langs button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.lang === l)));
  const dir = l === "ar" ? "rtl" : "ltr";
  text.dir = dir; text.lang = l; latin.dir = dir;

  const samplesEl = $("samples");
  samplesEl.innerHTML = "";
  for (const [label, sample] of SAMPLES[l]) {
    const b = el("button", "chip", label);
    b.type = "button";
    b.onclick = () => { text.value = sample; latin.hidden = true; updateMeta(); text.focus(); };
    samplesEl.append(b);
  }
  // Swap in the new language's sample unless the user has typed their own text.
  if (!text.value.trim() || ALL_SAMPLES.has(text.value)) text.value = SAMPLES[l][0][1];
  latin.hidden = true;
  updateMeta();

  $("langHint").hidden = l === "uz";
  $("langHint").textContent = "Bu tilda faqat koʻp tilli modellar ishlaydi: Microsoft va OmniVoice. Oʻzbekchaga oʻqitilgan modellar yashirildi.";
  if (!models.length) return;
  const keep = model && speaks(model) && model.available ? model.id : null;
  const next = keep || store.get(`model:${l}`) || store.get("model");
  const first = models.find((m) => m.id === next && m.available && speaks(m)) || models.find((m) => m.available && speaks(m));
  if (first) selectModel(first.id); else { model = null; renderModels(); }
}

function showError(msg) {
  error.textContent = msg;
  error.hidden = !msg;
}

function el(tag, cls, content) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (content != null) e.textContent = content;
  return e;
}

function badges(m) {
  const wrap = el("span", "badges");
  wrap.append(el("span", `badge ${m.kind === "online" ? "online" : "local"}`, m.kind === "online" ? "Onlayn" : "Lokal GPU"));
  if (m.terms_unclear) {
    const u = el("span", "badge nc", "? Shartlar noaniq");
    u.title = "Litsenziya shartlari tasdiqlanmagan: tijoriy foydalanishdan oldin muallif shartlarini tekshiring.";
    wrap.append(u);
  } else if (!m.commercial) {
    const nc = el("span", "badge nc", "NC · notijorat");
    nc.title = "Notijorat litsenziya: faqat shaxsiy, oʻquv yoki tadqiqot maqsadida foydalanish mumkin.";
    wrap.append(nc);
  }
  wrap.append(el("span", "badge lic", m.license));
  return wrap;
}

function renderModels() {
  modelsEl.innerHTML = "";
  for (const m of models) {
    if (commercialOnly.checked && !m.commercial) continue;
    if (!speaks(m)) continue;
    const b = el("button", "model");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(model && m.id === model.id));
    b.disabled = !m.available;
    const top = el("span", "model-top");
    top.append(el("span", "model-name", m.name), badges(m));
    b.append(top);
    let status = m.kind === "online" ? "Internet orqali" : m.loaded ? "● Xotirada — tez ishlaydi" : "Birinchi soʻrovda yuklanadi";
    if (!m.available) status = `Mavjud emas: ${m.unavailable_reason}`;
    b.append(el("span", `model-status${m.loaded ? " ready" : ""}`, status));
    b.onclick = () => selectModel(m.id);
    modelsEl.append(b);
  }
}

function selectModel(id) {
  const m = models.find((x) => x.id === id && x.available && speaks(x));
  if (!m) return;
  model = m;
  store.set("model", id);
  store.set(`model:${lang}`, id);
  if (commercialOnly.checked && !m.commercial) commercialOnly.checked = false;
  renderModels();

  modelDesc.innerHTML = "";
  modelDesc.append(document.createTextNode(m.description + " "));
  const a = el("a", null, "Model sahifasi ↗");
  a.href = m.url; a.target = "_blank"; a.rel = "noopener";
  modelDesc.append(a);

  const voices = m.voices.filter((v) => !v.lang || v.lang === lang);
  const voiceKey = `voice:${id}:${lang}`;
  const saved = store.get(voiceKey);
  voice = voices.some((v) => v.id === saved) ? saved : voices[0]?.id;
  voicesEl.innerHTML = "";
  for (const v of voices) {
    const b = el("button", null, v.name);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(v.id === voice));
    b.onclick = () => {
      voice = v.id;
      store.set(voiceKey, v.id);
      voicesEl.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    };
    voicesEl.append(b);
  }
  if (!voices.length) voicesEl.append(el("span", "hint", "Ovoz yoʻq: voices/ papkasiga namuna qoʻshing."));

  const styles = m.styles || [];
  const stylesEl = $("styles");
  $("styleField").hidden = !styles.length;
  stylesEl.innerHTML = "";
  const savedStyle = store.get(`style:${id}`);
  style = styles.some((s) => s.id === savedStyle) ? savedStyle : styles[0]?.id || null;
  for (const s of styles) {
    const b = el("button", "chip", s.name);
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(s.id === style));
    b.onclick = () => {
      style = s.id;
      store.set(`style:${id}`, s.id);
      stylesEl.querySelectorAll("button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    };
    stylesEl.append(b);
  }

  setSupported($("rateField"), rate, m.supports_rate);
  setSupported($("pitchField"), pitch, m.supports_pitch);
  latin.hidden = true;
}

function setSupported(field, input, ok) {
  input.disabled = !ok;
  field.classList.toggle("off", !ok);
  field.title = ok ? "" : "Bu model qoʻllab-quvvatlamaydi";
}

async function loadModels() {
  const res = await api("/api/models");
  models = await res.json();
  const keep = model?.id || store.get(`model:${lang}`) || store.get("model");
  const first = models.find((m) => m.id === keep && m.available && speaks(m)) || models.find((m) => m.available && speaks(m));
  if (first) selectModel(first.id); else renderModels();
}

async function synthesize() {
  const value = text.value.trim();
  if (!value) return showError("Iltimos, matn kiriting.");
  if (!model || !voice) return showError("Model va ovozni tanlang.");
  showError("");
  speak.disabled = true;
  const slow = model.kind === "local" && !model.loaded;
  speak.innerHTML = `<span class="spinner"></span>${slow ? "Model yuklanmoqda… (birinchi marta bir necha daqiqa)" : "Tayyorlanmoqda…"}`;
  const m = model, l = lang;
  try {
    const res = await api("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: value, model: m.id, voice, rate: +rate.value, pitch: +pitch.value, style: m.styles?.length ? style : null, language: l }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "Xatolik yuz berdi.");
    }
    const blob = await res.blob();
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    audioUrl = URL.createObjectURL(blob);
    player.src = audioUrl;
    player.hidden = false;
    download.href = audioUrl;
    download.download = `ovoz-${m.id}-${l}.${blob.type === "audio/mpeg" ? "mp3" : "wav"}`;
    download.textContent = `⬇ ${blob.type === "audio/mpeg" ? "MP3" : "WAV"} yuklab olish`;
    download.hidden = false;
    const secs = res.headers.get("X-Synthesis-Seconds");
    result.innerHTML = "";
    result.append(document.createTextNode(`${m.name}${secs ? ` · ${secs} s` : ""}`));
    if (!m.commercial) {
      result.append(el("span", "nc-warn", m.terms_unclear
        ? `⚠ ${m.license}: shartlar tasdiqlanmagan — tijoriy foydalanishdan oldin tekshiring.`
        : `⚠ NC model (${m.license}): bu audiodan tijoriy maqsadda foydalanib boʻlmaydi.`));
    }
    result.hidden = false;
    player.play().catch(() => {});
    if (slow) loadModels();  // refresh "loaded" status
  } catch (e) {
    showError(e.message || OFFLINE);
  } finally {
    speak.disabled = false;
    speak.textContent = "▶ Ovozlashtirish";
  }
}

$("showLatin").onclick = async () => {
  if (!latin.hidden) { latin.hidden = true; return; }
  try {
    const res = await api("/api/normalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.value, model: model?.id || "edge", language: lang }),
    });
    latin.textContent = (await res.json()).text;
    latin.hidden = false;
  } catch (e) {
    showError(e.message);
  }
};

document.querySelectorAll("#langs button").forEach((b) => { b.onclick = () => setLang(b.dataset.lang); });

commercialOnly.onchange = () => {
  store.set("commercialOnly", commercialOnly.checked ? "1" : "");
  if (commercialOnly.checked && model && !model.commercial) {
    const alt = models.find((m) => m.commercial && m.available && speaks(m));
    if (alt) return selectModel(alt.id);
  }
  renderModels();
};
rate.oninput = () => ($("rateOut").textContent = `${rate.value > 0 ? "+" : ""}${rate.value}%`);
pitch.oninput = () => ($("pitchOut").textContent = `${pitch.value > 0 ? "+" : ""}${pitch.value} Hz`);
text.oninput = () => { latin.hidden = true; updateMeta(); };
text.onkeydown = (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) synthesize(); };
speak.onclick = synthesize;

commercialOnly.checked = store.get("commercialOnly") === "1";
setLang(lang);
loadModels().catch((e) => showError(e.message));
