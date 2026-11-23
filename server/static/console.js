/* SafeXTrail SAR console: talks ONLY to our own /api/v1 routes (the Orchestrate key never reaches the browser).
   Replaces the <script> block at the bottom of templates/index.html.
   Optional URL parameter: ?return=2026-11-21T23:30:00Z  (the hiker's expected return time, UTC). */
const API = "/api/v1";
const LIVE_ID = "adv-1";
const POLL_MS = 30000;
const URL_RETURN = new URLSearchParams(location.search).get("return") || "";
const expectedReturn = () => (($("return-utc") && $("return-utc").value.trim()) || URL_RETURN);

const $ = id => document.getElementById(id);
const colorFor = s => (s >= 70 ? "#ef4444" : s >= 40 ? "#f59e0b" : "#10b981");
const setText = (id, v) => { const el = $(id); if (el) el.textContent = v; };

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  const data = await res.json().catch(() => ({ ok: false, error: "bad response (HTTP " + res.status + ")" }));
  if (!data.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

/* ---------- map ---------- */
const map = L.map("map").setView([36.1698, -117.0892], 12);
L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", { maxZoom: 17, attribution: "© OpenTopoMap" }).addTo(map);
const trackLine = L.polyline([], { color: "#f97316", weight: 3, dashArray: "6, 6" }).addTo(map);
const markers = {};            // id -> circleMarker
let latestCoords = null, firstFix = true;

function drawMarker(a) {
  if (!a.position) return;
  const ll = [a.position.lat, a.position.lon], c = colorFor(a.score ?? a.risk?.score ?? 0);
  if (!markers[a.id]) markers[a.id] = L.circleMarker(ll, { radius: a.id === LIVE_ID ? 10 : 7, weight: 2, color: "#fff", fillOpacity: 1 }).addTo(map);
  markers[a.id].setLatLng(ll).setStyle({ fillColor: c });
  markers[a.id].bindTooltip(a.name + " · risk " + (a.score ?? a.risk?.score ?? "--"));
}
function centerOnHiker() { if (latestCoords) map.setView(latestCoords, 14); }

/* ---------- event feed ---------- */
const TAG_STYLE = { RISK: "text-red-400", OVERDUE: "text-amber-400", STATIONARY: "text-red-300", WEATHER: "text-cyan-400", ACTION: "text-emerald-400" };
function logEvent(tag, text, time) {
  const feed = $("event-feed"); if (!feed) return;
  const row = document.createElement("div");
  const t = document.createElement("span"), g = document.createElement("span"), x = document.createElement("span");
  t.className = "text-slate-500"; t.textContent = (time || new Date().toISOString().substr(11, 8)) + " ";
  g.className = "font-bold " + (TAG_STYLE[tag] || "text-slate-300"); g.textContent = "[" + tag + "] ";
  x.className = "text-slate-200"; x.textContent = text;                       // textContent: never inject HTML
  row.append(t, g, x); feed.prepend(row);
  while (feed.children.length > 40) feed.lastChild.remove();
}
async function loadEvents() {
  try {
    const { events } = await api("/events?limit=15");
    $("event-feed").replaceChildren();
    events.slice().reverse().forEach(e => logEvent(e.tag, e.text, e.time_utc));
  } catch (e) { /* feed is best-effort */ }
}

/* ---------- live telemetry ---------- */
async function fetchLocation() {
  try {
    const er = expectedReturn(), q = er ? "?expected_return_utc=" + encodeURIComponent(er) : "";
    const { adventurer: a } = await api("/adventurers/" + LIVE_ID + q);
    if (!a.position) { setText("hiker-event", a.status_text); return; }
    const r = a.risk, p = a.position, t = a.telemetry, s = a.state;
    latestCoords = [p.lat, p.lon];
    drawMarker(a);
    trackLine.setLatLngs(a.track);
    if (firstFix) { map.setView(latestCoords, 13); firstFix = false; }

    setText("hiker-name", a.name);
    setText("hiker-event", a.status_text);
    setText("risk-score-val", r.score);
    $("risk-score-val").style.color = colorFor(r.score);
    setText("fix-age", "Last fix: " + s.minutes_since_fix + " min ago");
    setText("fix-coords", p.lat.toFixed(4) + ", " + p.lon.toFixed(4));
    setText("tel-elev", p.elevation_ft == null ? "—" : p.elevation_ft.toLocaleString() + " ft");
    setText("tel-speed", t.speed_mph == null ? "—" : t.speed_mph + " mph");
    setText("tel-course", t.course_deg == null ? "—" : Math.round(t.course_deg) + "°");
    setText("tel-sos", t.sos_active ? "ACTIVE" : "INACTIVE");
    $("tel-sos").className = "text-sm font-bold mt-0.5 " + (t.sos_active ? "text-red-400" : "text-emerald-400");
    setText("tel-msg", t.message || "No message transmitted.");
    setText("status-badge", s.minutes_since_fix > 30 ? "STALE" : "ONLINE");

    await loadRoster();   // keeps the other markers fresh too
    loadEvents();
  } catch (e) { logEvent("WEATHER", "Telemetry error: " + e.message); }
}
async function loadRoster() {
  const { adventurers } = await api("/adventurers");
  adventurers.forEach(drawMarker);
  renderRoster(adventurers);
}
function renderRoster(list) {
  const box = $("roster-list"); if (!box) return;
  box.replaceChildren();
  list.forEach(a => {
    const c = colorFor(a.score ?? 0);
    const row = el("button", "w-full text-left flex items-center gap-2 p-2 rounded bg-slate-900/80 border border-slate-800 hover:border-slate-600");
    const pill = el("span", "w-9 h-9 shrink-0 rounded flex items-center justify-center text-sm font-extrabold", a.score ?? "--");
    pill.style.color = c; pill.style.border = "1px solid " + c;
    const txt = el("span", "min-w-0 flex-1");
    txt.append(el("div", "text-xs font-bold text-white", a.name + (a.live ? " · LIVE" : "")),
               el("div", "text-[11px] text-slate-400 truncate", a.status_text));
    row.append(pill, txt);
    row.onclick = () => { if (a.position) map.setView([a.position.lat, a.position.lon], 14); };
    box.append(row);
  });
}

/* ---------- assessment panel (#root) : structured agent answer + chat box ---------- */
let threadId = null;
function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
function renderAssessment(res) {
  const root = $("root"); if (!root) return;
  const a = res.assessment, box = el("div", "p-3 space-y-2 text-xs text-slate-200 border-b border-slate-800");
  if (a.sos) box.append(el("div", "p-2 rounded bg-red-600 text-white font-bold", "SOS ACTIVE: escalate to a human dispatcher now"));
  const head = el("div", "flex items-baseline gap-2");
  const sc = el("span", "text-2xl font-extrabold", a.score ?? "--"); if (a.score != null) sc.style.color = colorFor(a.score);
  head.append(sc, el("span", "text-slate-400", "/100 " + (a.level || "") + (a.confidence != null ? " · confidence " + a.confidence : "")),
              el("span", "ml-auto text-[10px] uppercase text-slate-500", res.source));
  box.append(head);
  const list = (title, items) => { if (!items || !items.length) return; box.append(el("div", "text-[10px] uppercase text-slate-400 mt-1", title));
    const ul = el("ul", "list-disc pl-4 space-y-0.5"); items.forEach(i => ul.append(el("li", "", i))); box.append(ul); };
  list("Why", a.why); list("Context", a.context);
  if (a.action) { box.append(el("div", "text-[10px] uppercase text-slate-400 mt-1", "Recommended action")); box.append(el("div", "", a.action)); }
  if (a.draft_message) { box.append(el("div", "text-[10px] uppercase text-amber-400 mt-1", "Draft message to hiker (NOT SENT)")); box.append(el("div", "p-2 rounded bg-slate-900 italic", a.draft_message)); }
  if (res.note) box.append(el("div", "text-[10px] text-slate-500", res.note));
  if (!a.why.length && res.text) box.append(el("pre", "whitespace-pre-wrap text-slate-300", res.text));   // agent changed format: still show it
  let holder = $("assess-holder");
  if (!holder) {
    root.replaceChildren(); holder = el("div"); holder.id = "assess-holder"; root.append(holder);
    const form = el("div", "p-2 flex gap-2"), inp = el("input", "flex-1 px-2 py-1.5 rounded bg-slate-900 border border-slate-700 text-xs text-slate-100");
    inp.placeholder = "Ask risk_agent…";
    const btn = el("button", "px-3 py-1.5 rounded bg-cyan-700 hover:bg-cyan-600 text-xs text-white", "Send");
    const send = async () => { const m = inp.value.trim(); if (!m) return; inp.value = ""; btn.disabled = true;
      try { const r = await post("/agent/chat", { message: m, thread_id: threadId }); threadId = r.thread_id || threadId; renderAssessment({ ...r, source: "orchestrate" }); }
      catch (e) { holder.prepend(el("div", "p-2 text-xs text-red-400", e.message)); } btn.disabled = false; };
    btn.onclick = send; inp.onkeydown = e => { if (e.key === "Enter") send(); };
    form.append(inp, btn); root.append(form);
  }
  holder.prepend(box);
}
async function runQuickAssessment() {
  const out = $("api-assess-output"); out.classList.remove("hidden"); out.textContent = "Asking risk_agent… (can take up to a minute)";
  try {
    const res = await post("/adventurers/" + LIVE_ID + "/assess", { expected_return_utc: expectedReturn() });
    out.textContent = res.source + ": " + (res.assessment.score ?? "--") + "/100 " + (res.assessment.level || "");
    threadId = res.thread_id || threadId; renderAssessment(res);
    logEvent("RISK", "Assessment (" + res.source + "): " + (res.assessment.score ?? "--") + "/100");
  } catch (e) { out.textContent = "Error: " + e.message; }
}

/* ---------- operator actions: wire buttons with onclick="sendAction('dispatch')" ---------- */
async function sendAction(action, adventurerId) {
  const who = prompt("Approving operator name (required):"); if (!who) return;
  const body = { approved_by: who };
  if (action === "message") { body.text = prompt("Message to hiker (max 160 characters):") || ""; }
  try { await post("/adventurers/" + (adventurerId || LIVE_ID) + "/actions/" + action, body); logEvent("ACTION", action + " queued, approved by " + who); }
  catch (e) { alert(e.message); }
}

/* ---------- boot ---------- */
setInterval(() => { const c = $("clock"); if (c) c.textContent = new Date().toISOString().substr(11, 8) + " UTC"; }, 1000);
fetchLocation(); setInterval(fetchLocation, POLL_MS);
