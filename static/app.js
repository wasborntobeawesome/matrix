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
// Uzbek names of the languages ("Rus aksenti"). Replaced by the server's list (/api/languages)
// at startup, so a language added there needs no change here.
let LANG_NAMES = { uz: "Oʻzbek", ru: "Rus", en: "Ingliz", ar: "Arab" };
const langName = (code) => LANG_NAMES[code] || code;

// Text a speaker reads aloud when recording a reference voice (~8–12 s, upbeat like an ad).
const READ_SCRIPTS = {
  uz: "Salom! Bugun sizga ajoyib yangilik bor: yangi ilovamiz endi hamma uchun ochiq. Sinab koʻring, sizga albatta yoqadi!",
  ru: "Привет! У нас отличная новость: наше новое приложение теперь доступно всем. Попробуйте — вам точно понравится!",
  en: "Hi there! We have great news: our new app is now available to everyone. Give it a try — you're going to love it!",
  ar: "مرحبًا! لدينا خبر رائع: تطبيقنا الجديد متاح الآن للجميع. جرّبه، سيعجبك بالتأكيد!",
};

// When index.html is opened straight from disk (file://), talk to the local server.
const API = location.protocol === "file:" ? "http://localhost:8000" : "";
const OFFLINE = "Serverga ulanib boʻlmadi. Server ishga tushirilganini tekshiring (start.bat yoki: uvicorn app:app).";

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
};

let models = [];
let lang = store.get("lang") in SAMPLES ? store.get("lang") : "uz";  // content language
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
  // A clip recorded for another language must not be saved under this one.
  if (l !== lang && (clipWav || recorder)) {
    discardClip();
    avMessage("Til oʻzgardi, shuning uchun yozilgan namuna oʻchirildi. Yangi tilda qaytadan yozib oling.");
  }
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
  $("langHint").textContent = "Bu tilda faqat koʻp tilli modellar ishlaydi: Microsoft, OmniVoice va Chatterbox Multilingual. Oʻzbekchaga oʻqitilgan modellar yashirildi.";
  refreshAddVoice();
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

  // Native voices first. Cross-lingual voices (cloned clips, Chatterbox's built-in speaker)
  // can also speak the model's other languages, with their speaker's accent.
  const usable = m.voices.filter((v) => !v.lang || v.lang === lang || v.cross_lingual);
  const voices = [
    ...usable.filter((v) => v.lang === lang),            // native speakers of this language
    ...usable.filter((v) => !v.lang),                    // built-in voices
    ...usable.filter((v) => v.lang && v.lang !== lang),  // other languages' voices (accented)
  ];
  // Warn only when this model would otherwise borrow a voice from another language.
  const hasNative = usable.some((v) => v.lang === lang);
  const hint = $("voiceHint");
  hint.hidden = hasNative || !usable.some((v) => v.lang && v.lang !== lang);
  hint.textContent = `${langName(lang)} tilida gapiradigan odamning namunasi hali yoʻq, shuning uchun ovoz aksent bilan chiqadi. Pastdagi «Ovoz qoʻshish» orqali yozib oling.`;
  const voiceKey = `voice:${id}:${lang}`;
  const saved = store.get(voiceKey);
  voice = voices.some((v) => v.id === saved) ? saved : voices[0]?.id;
  voicesEl.innerHTML = "";
  for (const v of voices) {
    const b = el("button", null, v.lang && v.lang !== lang ? `${v.name} · ${langName(v.lang)} aksenti` : v.name);
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
  if (!voices.length) voicesEl.append(el("span", "hint", "Ovoz yoʻq: pastdagi «Ovoz qoʻshish» orqali namuna yozib oling."));

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
  refreshAddVoice();
}

// ---- Add a native speaker's reference voice ---------------------------------------
let clipWav = null;      // ArrayBuffer: 24 kHz mono 16-bit WAV, ready to upload
let clipLang = null;     // language the clip was recorded/uploaded for
let clipToken = 0;       // bumped to drop clips still being decoded or recorded
let recorder = null, recTimer = null, micOpening = false;
const MAX_REC_SECONDS = 20;

function discardClip() {
  clipToken++;
  clipWav = clipLang = null;
  stopRecording();  // its onstop sees the stale token and drops the recording
  const prev = $("avPreview");
  if (prev.src) URL.revokeObjectURL(prev.src);
  prev.removeAttribute("src");
  prev.hidden = true;
  $("avTime").textContent = "";
  $("avRec").textContent = "● Yozib olish";
  updateSaveButton();
}

function refreshAddVoice() {
  $("avLang").textContent = langName(lang);
  const avText = $("avText");
  avText.dir = lang === "ar" ? "rtl" : "ltr";
  if (!avText.value.trim() || Object.values(READ_SCRIPTS).includes(avText.value)) avText.value = READ_SCRIPTS[lang];

  // Voices added from the UI (ids like "ru/anna"), with a delete button.
  const added = new Map();
  for (const m of models) for (const v of m.voices) if (v.clone && v.id.includes("/") && v.lang === lang) added.set(v.id, v);
  const list = $("avList");
  list.innerHTML = "";
  if (added.size) list.append(el("span", "label", `Qoʻshilgan ${langName(lang).toLowerCase()} ovozlar`));
  for (const v of added.values()) {
    const row = el("div", "av-item");
    row.append(el("span", null, v.name));
    const del = el("button", "link", "Oʻchirish");
    del.type = "button";
    del.onclick = async () => {
      if (!confirm(`«${v.name}» ovozini oʻchirasizmi?`)) return;
      const [l, slug] = v.id.split("/");
      try {
        const res = await api(`/api/voices/${encodeURIComponent(l)}/${encodeURIComponent(slug)}`, { method: "DELETE" });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(typeof data.detail === "string" ? data.detail : "Xatolik yuz berdi.");
        }
        await loadModels();
        avMessage(`«${v.name}» oʻchirildi.`);
      } catch (e) {
        avMessage(e.message || OFFLINE, true);
      }
    };
    row.append(del);
    list.append(row);
  }
}

function avMessage(msg, isError = false) {
  const p = $("avMsg");
  p.textContent = msg;
  p.className = isError ? "error" : "hint";
  p.hidden = !msg;
}

function updateSaveButton() {
  $("avSave").disabled = !(clipWav && $("avName").value.trim() && $("avText").value.trim() && $("avConsent").checked);
}

function encodeWav(samples, sr) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); str(8, "WAVE");
  str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, "data"); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, samples[i])) * 0x7fff, true);
  return buf;
}

// Any audio the browser can decode (recording, mp3, m4a, wav) -> trimmed 24 kHz mono WAV.
async function toWav(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  let decoded;
  try { decoded = await ctx.decodeAudioData(await blob.arrayBuffer()); } finally { ctx.close(); }
  const sr = 24000;
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * sr)), sr);
  const src = off.createBufferSource();
  src.buffer = decoded; src.connect(off.destination); src.start();
  const data = (await off.startRendering()).getChannelData(0);
  // Trim leading/trailing silence, keeping 0.15 s of padding.
  let peak = 0;
  for (const x of data) peak = Math.max(peak, Math.abs(x));
  const thr = peak * 0.05, pad = Math.round(0.15 * sr);
  let a = 0, b = data.length - 1;
  while (a < b && Math.abs(data[a]) < thr) a++;
  while (b > a && Math.abs(data[b]) < thr) b--;
  const clip = data.slice(Math.max(0, a - pad), Math.min(data.length, b + pad));
  return { wav: encodeWav(clip, sr), seconds: clip.length / sr };
}

async function useClip(blob) {
  const token = ++clipToken, forLang = lang;
  try {
    const { wav, seconds } = await toWav(blob);
    if (token !== clipToken || forLang !== lang) return;  // language changed while decoding
    clipWav = wav;
    clipLang = forLang;
    const prev = $("avPreview");
    if (prev.src) URL.revokeObjectURL(prev.src);
    prev.src = URL.createObjectURL(new Blob([wav], { type: "audio/wav" }));
    prev.hidden = false;
    $("avTime").textContent = `${seconds.toFixed(1)} s`;
    avMessage(seconds < 3 ? "Namuna juda qisqa: kamida 3 soniya kerak (5–15 s tavsiya etiladi)." : seconds > 30 ? "Namuna juda uzun: koʻpi bilan 30 soniya." : "", seconds < 3 || seconds > 30);
    if (seconds < 3 || seconds > 30) clipWav = null;
  } catch {
    if (token !== clipToken) return;
    clipWav = clipLang = null;
    avMessage("Audio faylni oʻqib boʻlmadi. WAV, MP3 yoki M4A fayl tanlang.", true);
  }
  updateSaveButton();
}

function stopRecording() {
  clearInterval(recTimer);  // stop the auto-stop timer at once, so it can't call stop() twice
  if (recorder?.state === "recording") recorder.stop();
}

async function toggleRecording() {
  const btn = $("avRec");
  if (recorder) { stopRecording(); return; }  // still recording, or finishing: never stop() twice
  if (micOpening) return;                      // a double-click must not start two recordings
  let stream;
  micOpening = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  } catch {
    return avMessage("Mikrofonga ruxsat berilmadi. Brauzer sozlamalarida mikrofonni yoqing.", true);
  } finally {
    micOpening = false;
  }
  const chunks = [], token = ++clipToken;
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (e) => chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(recTimer);
    recorder = null;
    btn.classList.remove("recording");
    if (token !== clipToken) return;  // discarded (e.g. the language changed mid-recording)
    btn.textContent = "● Qayta yozish";
    useClip(new Blob(chunks, { type: chunks[0]?.type || "audio/webm" }));
  };
  const started = Date.now();
  recTimer = setInterval(() => {
    const s = (Date.now() - started) / 1000;
    $("avTime").textContent = `${s.toFixed(0)} / ${MAX_REC_SECONDS} s`;
    if (s >= MAX_REC_SECONDS) stopRecording();
  }, 250);
  avMessage("");
  recorder.start();
  btn.textContent = "■ Toʻxtatish";
  btn.classList.add("recording");
}

function toBase64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

async function saveVoice() {
  if (!clipWav || clipLang !== lang) return discardClip();  // never save a clip under another language
  const btn = $("avSave");
  btn.disabled = true;
  try {
    const res = await api("/api/voices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: $("avName").value.trim(), language: clipLang, transcript: $("avText").value.trim(),
        consent: $("avConsent").checked, audio: toBase64(clipWav),
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Xatolik yuz berdi.");
    // Pick the new voice in every model that can use it.
    for (const m of models) if (m.languages.includes(data.language)) store.set(`voice:${m.id}:${data.language}`, data.id);
    discardClip();
    $("avName").value = "";
    await loadModels();
    avMessage("Saqlandi! Endi OmniVoice yoki Chatterbox Multilingual modelini tanlab, shu ovoz bilan sinab koʻring.");
  } catch (e) {
    avMessage(e.message || OFFLINE, true);
  } finally {
    updateSaveButton();
  }
}

$("avRec").onclick = toggleRecording;
$("avFile").onchange = (e) => { if (e.target.files[0]) useClip(e.target.files[0]); e.target.value = ""; };
$("avSave").onclick = saveVoice;
["avName", "avText"].forEach((id) => ($(id).oninput = updateSaveButton));
$("avConsent").onchange = updateSaveButton;

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
api("/api/languages")
  .then((res) => res.json())
  .then((list) => {
    LANG_NAMES = Object.fromEntries(list.map((l) => [l.id, l.label]));
    refreshAddVoice();
    if (model) selectModel(model.id);
  })
  .catch(() => { /* keep the built-in names */ });
