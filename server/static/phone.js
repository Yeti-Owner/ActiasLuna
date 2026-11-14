"use strict";
// Phone page, served by the laptop on the demo network (PhoneHandler in luna_server.py). The server speaks metres;
// the screen shows feet. Primary calibration is "I'm here" (App.start_mark / _finish_mark); the guided walk over
// planner.py's route is the alternative under More; "trace" mode records where the holder says they are as test truth
// (POST /api/trace -> Tracker.trace). What the card shows is decided only by view(), from (mode, S.capture, walk.next).
const $ = (id) => document.getElementById(id);
// Trace: a finger sample every TRACE_STEP_MS while it moves (or stays down), posted every TRACE_POST_MS as
// [age_ms, x, y] (luna_server._phone_trace caps a post at 50 points). The phone keeps TRACE_SHOW_MS of its own line.
// TRACE_MARGIN_M: room drawn around the outline in trace mode, to trace a little outside it (luna_server.TRACE_MARGIN_M).
const TRACE_STEP_MS = 100, TRACE_POST_MS = 250, TRACE_SHOW_MS = 60000, TRACE_MARGIN_M = 1.0;
let finger = null, traceBuf = [], traceLine = [];
const FT = 0.3048;
const ft = (m) => (m / FT).toFixed(1);
const NEAR_M = 0.6, SNAP_M = 0.4;
// ?mac= lets the laptop pose as a given phone for testing; the server accepts it only from 127.0.0.1.
const MAC = new URLSearchParams(location.search).get("mac") || "";

let S = null, mode = "mark", busy = false, msg = null, tap = null, lastResult = null;
let startDot = 0, walk = { next: null, done: [], skipped: new Set() };

async function call(path, body) {
  const payload = Object.assign({ mac: MAC || undefined }, body || {});
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || res.status);
  return json;
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const res = await fetch("/api/state" + (MAC ? "?mac=" + encodeURIComponent(MAC) : ""),
      { cache: "no-store" });
    const json = await res.json();
    if (!res.ok) throw new Error(json.error || res.status);
    const previous = S && S.capture;
    S = json;
    $("connCard").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("err").textContent = "";
    if (previous && !S.capture) captureEnded(previous);
    noteResult();
    keepAwake(true);   // a sleeping screen stops the pings
    view();
    draw();
  } catch (e) {
    if (!S) $("connErr").textContent = "Can't reach the laptop yet (" + e.message + "). Retrying…";
    else $("err").textContent = "Lost the laptop: " + e.message;
  } finally {
    polling = false;
  }
}

// Keep this phone transmitting for the boards to hear while the page is open: the boards only see frames the phone
// sends, and an idle phone sends a few per second. Each ping is a small POST, so the phone itself puts PING_BYTES on
// the air (a GET is mostly downlink). The server caps each phone at luna_server.PING_MAX_HZ; keep PING_MS above that.
const PING_MS = 25, PING_INFLIGHT = 3, PING_BODY = "x".repeat(1000);
let pings = 0;
setInterval(() => {
  if (document.hidden || pings >= PING_INFLIGHT) return;
  pings++;
  fetch("/ping", { method: "POST", body: PING_BODY, cache: "no-store" }).catch(() => {}).finally(() => pings--);
}, PING_MS);
setInterval(poll, 500);

let wake = null;
async function keepAwake(on) {
  try {
    if (on && !wake && navigator.wakeLock) {
      wake = await navigator.wakeLock.request("screen");
      wake.addEventListener("release", () => (wake = null));
    } else if (!on && wake) {
      await wake.release();
      wake = null;
    }
  } catch (e) {}
}
const buzz = (ms = 40) => navigator.vibrate && navigator.vibrate(ms);

// Motion: still/moving, steps and compass heading (degrees clockwise from north; the server learns the room offset,
// walkcal.motion_fit <-> Tracker._facing). Posted to /api/motion (tracker.set_motion) every MOTION_MS, one request
// at a time: steps are a running count, so a skipped report loses nothing. Needs https and, on iPhone, a tap to
// allow. It must not miss slow walking (a fixed "|a| > 11.5" threshold counted 3 steps over 4.7 m).
// Steps: |acceleration| is low-passed (LP_S) and its slow part (BASE_S: gravity, tilt) removed, leaving the walking
// bounce. A step is that signal rising through +th after dipping below -th/2; th tracks recent bounce size but never
// drops under STEP_MIN (hand tremor is ~0.1 m/s^2). A lone bump isn't a step: it counts once a second follows
// within PAIR_MS.
const MOTION_MS = 250, LP_S = 0.06, BASE_S = 1.0, ENERGY_S = 0.8, STEP_MIN = 0.45, STEP_GAP_MS = 300, PAIR_MS = 1600,
  MOVING_MS = 1200, MOVING_RMS = 0.3;
let motionOn = false, motionNote = "", mv = -1, steps = 0, heading = null, motionBusy = false;
const acc = { t: 0, lp: 9.81, base: 9.81, energy: 0, amp: 1.5, armed: false, lo: 0, hi: 0, lastStep: 0, pending: 0, n: 0 };

function onOrientation(e) {
  if (typeof e.webkitCompassHeading === "number") heading = e.webkitCompassHeading;
  else if (e.absolute && typeof e.alpha === "number") heading = (360 - e.alpha) % 360;
}

function onDeviceMotion(e) {
  const g = e.accelerationIncludingGravity;
  if (!g || g.x == null) return;
  const t = performance.now();
  const dt = Math.min(0.1, Math.max(0.004, (t - acc.t) / 1000 || 0.016));
  acc.t = t;
  acc.n++;
  acc.lp += (Math.hypot(g.x, g.y, g.z) - acc.lp) * Math.min(1, dt / LP_S);
  acc.base += (acc.lp - acc.base) * Math.min(1, dt / BASE_S);
  const bounce = acc.lp - acc.base, th = Math.max(STEP_MIN, 0.4 * acc.amp);
  acc.energy += (bounce * bounce - acc.energy) * Math.min(1, dt / ENERGY_S);
  acc.lo = Math.min(acc.lo, bounce);
  acc.hi = Math.max(acc.hi, bounce);
  if (bounce < -th / 2) acc.armed = true;
  if (acc.armed && bounce > th && t - acc.lastStep > STEP_GAP_MS) {
    acc.armed = false;
    acc.amp += 0.3 * (Math.min(6, acc.hi - acc.lo) / 2 - acc.amp);
    acc.lo = acc.hi = 0;
    if (t - acc.lastStep < PAIR_MS) { steps += 1 + acc.pending; acc.pending = 0; }
    else acc.pending = 1;
    acc.lastStep = t;
  }
  if (t - acc.lastStep > PAIR_MS) {
    acc.pending = 0;
    acc.amp += (1.5 - acc.amp) * Math.min(1, dt / 3);
  }
}

function reportMotion() {
  if (!motionOn || !S || motionBusy) return;
  const rms = Math.sqrt(acc.energy);
  if (acc.n > 10) mv = (performance.now() - acc.lastStep < MOVING_MS && !acc.pending) || rms > MOVING_RMS ? 1 : 0;
  if (mv < 0) return;
  motionBusy = true;
  // ac (bounce rms, m/s^2) is only recorded, so the thresholds above can be retuned against real walks.
  call("/api/motion", { mv, st: steps, hd: heading, ac: Math.round(rms * 100) / 100 })
    .catch(() => {}).finally(() => (motionBusy = false));
}
setInterval(reportMotion, MOTION_MS);

// Must run inside a tap: iOS only shows its permission prompt for a user gesture, so nothing may be awaited first.
async function enableMotion() {
  if (motionOn) return;
  if (!window.isSecureContext) { motionNote = "needs https"; return; }
  try {
    if (window.DeviceMotionEvent?.requestPermission && (await DeviceMotionEvent.requestPermission()) !== "granted") {
      motionNote = "blocked: allow Motion & Orientation in Safari settings";
      return;
    }
    if (window.DeviceOrientationEvent?.requestPermission) await DeviceOrientationEvent.requestPermission();
  } catch (e) {
    motionNote = "unavailable";
    return;
  }
  window.addEventListener("devicemotion", onDeviceMotion);
  window.addEventListener("deviceorientation", onOrientation);
  window.addEventListener("deviceorientationabsolute", onOrientation);
  motionOn = true;
}
$("motion").onclick = async () => { await enableMotion(); view(); };

function captureEnded(cap) {
  if (cap.kind === "empty") msg = ["good", "Empty room recorded ✓ Come back in."];
  else if (cap.kind === "walk") {
    msg = ["good", "Walk saved ✓ " + (S.walks >= 2 ? "" : "Walking it again the other way round makes the map better.")];
    walk = { next: null, done: [], skipped: new Set() };
    mode = "mark";
  }
  if (cap.kind !== "mark") buzz();
}

// The server keeps each phone's latest mark outcome; a new t means a mark just finished.
// The first poll only takes note, so reloading the page doesn't replay an old outcome.
function noteResult() {
  const r = S.result, t = r ? r.t : 0;
  if (lastResult === null) { lastResult = t; return; }
  if (!r || t === lastResult || r.undone) { if (r) lastResult = t; return; }
  lastResult = t;
  buzz(r.ok ? [40, 60, 40] : 300);
  if (!r.ok) { msg = ["bad", `Not saved: ${r.why}.`]; return; }
  let text = "Saved ✓ ";
  if (r.err_m != null) text += r.calibrated ? `The map had you ${ft(r.err_m)} ft away; now it knows this spot.` : `First spot (the rough guess was ${ft(r.err_m)} ft off).`;
  if (r.learned) text += " Compass and stride learned from your walk.";
  msg = ["good", text, "mark"];
}


async function act(fn) {
  if (busy) return;
  busy = true;
  $("err").textContent = "";
  try { await fn(); await poll(); }
  catch (e) { $("err").textContent = e.message; }
  finally { busy = false; view(); }
}

const ori = () => $("ori").value;
const cancel = () => call("/api/cancel");

function markTarget() {
  if (tap) return tap;
  return S.next ? { x: S.next[0], y: S.next[1], ring: true } : null;
}
async function mark() {
  const where = markTarget();
  if (!where) return;
  await enableMotion();
  msg = null;
  await call("/api/mark", { x: where.x, y: where.y, orientation: ori() });
  tap = null;
  buzz();
}

// After a skip, the leg from the last mark to the next dot isn't a drawn straight line either.
const isDetour = (i) => !!(S.route[i][2] || walk.skipped.has(i));
async function startWalk() {
  await enableMotion();
  const [x, y] = S.route[startDot];
  walk = { next: startDot + 1, done: [[x, y, false]], skipped: new Set() };
  await call("/api/walk/start", { orientation: ori() });
  await call("/api/walk/mark", { x, y });
  buzz();
}
async function reached(onDot) {
  const i = walk.next, [x, y] = S.route[i], detour = isDetour(i);
  if (onDot) {
    await call("/api/walk/mark", { x, y, detour });
    walk.done.push([x, y, detour]);
    buzz();
  } else walk.skipped.add(i + 1);
  walk.next = i + 1;
  if (walk.next >= S.route.length) await call("/api/walk/stop");
}

function card(title, help, go, alt) {
  $("title").textContent = title;
  $("help").innerHTML = help || "";
  const g = $("go"), a = $("alt");
  g.classList.toggle("hidden", !go);
  if (go) { g.textContent = go[0]; g.disabled = !go[1]; g.onclick = () => act(go[1]); }
  a.classList.toggle("hidden", !alt);
  if (alt) { a.textContent = alt[0]; a.onclick = () => act(alt[1]); }
}
const countdown = (c) => (c.phase === "countdown" ? "Starting…" : `${Math.max(0, Math.ceil(c.duration - c.elapsed))} s`);

function viewMark() {
  const plan = S.plan || [], left = plan.filter((p) => !p[2]).length, where = markTarget();
  const title = S.next ? `Spot ${plan.length - left + 1} of ${plan.length}` : "Room covered ✓";
  let help;
  if (tap) help = `Marked at ${ft(tap.x)}, ${ft(tap.y)} ft. Stand still there and press <b>I'm here</b>.`;
  else if (S.next) help = "Walk straight to the <b>amber ring</b> and stand on it, facing the way you walked. The dashed line shows the spots after it. (Can't reach it? Tap where you are instead.)";
  else help = "Every spot is marked. Mark anywhere your blue dot still looks wrong: tap where you really are.";
  const undo = S.result && S.result.ok && msg && msg[2] === "mark" ? ["Undo that mark", async () => {
    await call("/api/mark/undo");
    msg = ["bad", "Mark undone."];
  }] : null;
  card(title, help, [tap ? "I'm here" : where ? "I'm on the ring" : "Tap where you are", where ? mark : null], undo);
}

function viewWalkIntro() {
  card("Guided walk", (S.route_reversed ? "Same dots as last time, opposite direction. " : "") +
    `Stand on the <b>green dot ${startDot + 1}</b> (tap another dot to start there), phone held in front. ` +
    "Walk to each next dot at a normal pace, stop on it, press the button.",
  [`Start at dot ${startDot + 1}`, startWalk], ["Back to marking", async () => { mode = "mark"; }]);
}

function viewWalking() {
  if (walk.next == null) {
    card("Walk in progress", "This page was reloaded during a walk.", ["Stop and save", () => call("/api/walk/stop")], ["Cancel walk", cancel]);
    return;
  }
  const n = walk.next + 1;
  const how = isDetour(walk.next) ? "Walk <b>around the furniture</b> to the green dot." : "Walk <b>straight</b> to the green dot.";
  card(`Walk to dot ${n}`, `${how} Stop on it, then press. <span class="stats">${walk.next} of ${S.route.length} done</span>`,
    [`I'm on dot ${n}`, () => reached(true)], ["Can't get there, skip", () => reached(false)]);
  $("msg").innerHTML = '<button id="stopWalk">Stop and save</button> <button id="cancelWalk">Cancel</button>';
  $("stopWalk").onclick = () => act(() => call("/api/walk/stop"));
  $("cancelWalk").onclick = () => act(cancel);
}

function viewCapture(c) {
  const rec = c.phase === "recording";
  $("prog").classList.toggle("hidden", !rec);
  $("prog").firstElementChild.style.width = rec ? Math.min(100, (100 * c.elapsed) / c.duration) + "%" : "0";
  if (c.kind === "empty") {
    card(rec ? "Recording the empty room…" : "Leave the room now", "Everyone out, door closed if there is one. The phone buzzes when it's done.<br><b>" + countdown(c) + "</b>", null, ["Cancel", cancel]);
  } else {
    card("Hold still… " + countdown(c), "Phone in front of you as you'd normally hold it. Anyone else in the room: please keep still too. It buzzes when done.", null, ["Cancel", cancel]);
  }
}

function viewChrome() {
  const chip = $("motion");
  chip.classList.remove("hidden");
  chip.className = "chip " + (motionOn && mv >= 0 ? "on" : "off");
  chip.textContent = motionOn ? (mv < 0 ? "Motion: starting…" : `Motion ${mv ? "· moving" : "· still"} · ${steps} steps`)
    : !S.https ? "Motion needs https" : motionNote ? "Motion " + motionNote : "Turn on motion";
  chip.disabled = motionOn || !S.https;
  const plan = S.plan || [];
  const bits = [plan.length ? `${plan.filter((p) => p[2]).length}/${plan.length} spots` : `${S.marks.length} marked`];
  if (S.walks) bits.push(`${S.walks} walk${S.walks === 1 ? "" : "s"}`);
  if (S.typical_m != null) bits.push(`typical error ${ft(S.typical_m)} ft`);
  bits.push(S.compass ? "compass ✓" : "compass: walk between marks");
  if (S.stride) bits.push(`stride ${ft(S.stride)} ft`);
  $("stats").textContent = bits.join(" · ");
  const est = S.me && S.me.est;
  $("me").textContent = S.me ? (S.me.name || S.me.mac) + (est ? ` · blue dot ±${ft(est.unc)} ft` : "") : "";
  const legend = [["#7fa8ff", "you (estimate)"]];
  if (mode === "mark") legend.push(["#e6b24c", "next spot"], ["#3e9e74", "marked: <2 ft off"], ["#e06c6c", ">4 ft off"]);
  else if (mode === "trace") legend.push(["#ffffff", "your finger (truth)"]);
  else legend.push(["#3e9e74", "route dot"]);
  $("legend").innerHTML = legend.map(([c, t]) => `<span><i style="background:${c}"></i>${t}</span>`).join("");
}

function view() {
  if (!S) return;
  const c = S.capture;
  viewChrome();
  $("more").classList.toggle("hidden", !!c);
  $("prog").classList.add("hidden");
  $("msg").className = "msg " + (msg ? msg[0] : "");
  $("msg").textContent = !c && msg ? msg[1] : "";
  if (!S.me) return card("Not connected", "Join this phone to the demo Wi-Fi, then reload.");
  if (!S.polygon || S.polygon.length < 3) return card("Room not set up", "Set the room outline on the laptop first (Setup &amp; debug → Layout).");
  if (c && c.kind === "walk") return viewWalking();
  if (c) return viewCapture(c);
  if (mode === "walk" && S.route.length) return viewWalkIntro();
  if (mode === "trace") return viewTrace();
  viewMark();
}

function addTracePoint(x, y) {
  const t = performance.now();
  finger = { x, y, t };
  traceBuf.push([t, x, y]);
  traceLine.push([t, x, y]);
  while (traceLine.length && t - traceLine[0][0] > TRACE_SHOW_MS) traceLine.shift();
}
setInterval(() => {
  if (mode !== "trace") return;
  const now = performance.now();
  if (finger && now - finger.t >= TRACE_STEP_MS) addTracePoint(finger.x, finger.y);   // standing with the finger down
  if (!traceBuf.length) return;
  const pts = traceBuf.splice(0).slice(-50).map(([t, x, y]) => [Math.round(now - t), Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]);
  fetch("/api/trace", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mac: MAC || undefined, pts }) }).catch(() => {});
}, TRACE_POST_MS);

function viewTrace() {
  const tr = S.me && S.me.trace;
  let help = "Put a finger on the map <b>where you are</b> and keep it under you as you walk. Lift it to pause.";
  if (tr && tr.med != null) {
    help += `<br><b>Now ${tr.err != null ? ft(tr.err) + " ft off" : "—"}</b> · typical ${ft(tr.med)} ft`;
    if (tr.lag > 0) help += ` · trails you by ~${tr.lag.toFixed(1)} s (${ft(tr.med_at_lag)} ft once caught up)`;
  }
  help += "<br><span class=\"stats\">Save as calibration teaches the map from the lines drawn since the last save " +
    "(within 2 minutes). Lines you don't save stay a test.</span>";
  const save = async () => {
    await new Promise((r) => setTimeout(r, TRACE_POST_MS + 100));   // let the last finger points reach the laptop
    const res = await call("/api/trace/save");
    msg = res.added ? ["good", `Saved ✓ ${res.added} calibration spots from your line.`] : ["bad", "Nothing new to save: draw a line of at least 3 s first."];
    traceLine = [];
  };
  card(finger ? "Tracing…" : "Trace your walk", help, [traceLine.length ? "Save as calibration" : "Draw a line first", traceLine.length ? save : null],
    ["Done", async () => { mode = "mark"; finger = null; }]);
}

const canvas = $("map"), g = canvas.getContext("2d");
let tf = { k: 1, ox: 0, oy: 0, w: 0, h: 0 };
function fitView() {
  const w = canvas.clientWidth, xs = S.polygon.map((p) => p[0]), ys = S.polygon.map((p) => p[1]);
  const mg = mode === "trace" ? TRACE_MARGIN_M : 0, pad = 16;
  const x0 = Math.min(...xs) - mg, x1 = Math.max(...xs) + mg, y0 = Math.min(...ys) - mg, y1 = Math.max(...ys) + mg;
  const k = Math.min((w - 2 * pad) / Math.max(0.1, x1 - x0), (0.62 * innerHeight - 2 * pad) / Math.max(0.1, y1 - y0));
  const h = Math.round((y1 - y0) * k + 2 * pad), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.height = h + "px";
  }
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  tf = { k, ox: (w - (x1 - x0) * k) / 2 - x0 * k, oy: pad - y0 * k, w, h };
}
const toX = (x) => tf.ox + x * tf.k, toY = (y) => tf.oy + y * tf.k;
function tracePoly(poly) {
  g.beginPath();
  poly.forEach((p, i) => (i ? g.lineTo(toX(p[0]), toY(p[1])) : g.moveTo(toX(p[0]), toY(p[1]))));
  g.closePath();
}
function centredText(text, x, y, font, color) {
  g.fillStyle = color; g.font = font; g.textAlign = "center"; g.fillText(text, x, y); g.textAlign = "left";
}
function dot(x, y, r, fill, num, ink = "#090b16") {
  g.beginPath(); g.arc(toX(x), toY(y), r, 0, 2 * Math.PI); g.fillStyle = fill; g.fill();
  if (num != null) centredText(num, toX(x), toY(y) + 4, `bold ${r > 10 ? 13 : 11}px sans-serif`, ink);
}
function ring(x, y, r, color, width = 2) {
  g.beginPath(); g.arc(toX(x), toY(y), r, 0, 2 * Math.PI); g.strokeStyle = color; g.lineWidth = width; g.stroke();
}
function crosshair(x, y, color) {
  const X = toX(x), Y = toY(y);
  ring(x, y, 11, color, 2.5);
  g.beginPath(); g.moveTo(X - 17, Y); g.lineTo(X + 17, Y); g.moveTo(X, Y - 17); g.lineTo(X, Y + 17); g.stroke();
}
const pulse = () => 3 * Math.sin(performance.now() / 220);
const errColor = (e) => (e == null ? "#c9b6ff" : e < 2 * FT ? "#3e9e74" : e < 4 * FT ? "#e6b24c" : "#e06c6c");

function drawRoute(walking) {
  const current = walking ? walk.next : startDot;
  for (let i = 1; i < S.route.length; i++) {
    const a = S.route[i - 1], b = S.route[i];
    g.strokeStyle = b[2] ? "#7a819c44" : "#e6b24c88"; g.lineWidth = 2; g.setLineDash(b[2] ? [2, 6] : [5, 5]);
    g.beginPath(); g.moveTo(toX(a[0]), toY(a[1])); g.lineTo(toX(b[0]), toY(b[1])); g.stroke();
  }
  g.setLineDash([]);
  g.strokeStyle = "#8fdcb2"; g.lineWidth = 3;
  for (let i = 1; i < walk.done.length; i++) {
    if (walk.done[i][2]) continue;
    g.beginPath(); g.moveTo(toX(walk.done[i - 1][0]), toY(walk.done[i - 1][1])); g.lineTo(toX(walk.done[i][0]), toY(walk.done[i][1])); g.stroke();
  }
  S.route.forEach((p, i) => { if (i !== current) dot(p[0], p[1], 9, walking && i < current ? "#454d6c" : "#e6b24c", i + 1); });
  if (current < S.route.length) {
    const p = S.route[current];
    ring(p[0], p[1], 18 + pulse(), "#3e9e74");
    dot(p[0], p[1], 13, "#3e9e74", current + 1, "#fff");
  }
}

// The plan's open spots in walking order from the next one, so the dashed line shows where the walk goes.
function upcoming() {
  const plan = S.plan || [];
  const i = S.next ? plan.findIndex((p) => p[0] === S.next[0] && p[1] === S.next[1]) : -1;
  if (i < 0) return [];
  const fwd = plan.slice(i).filter((p) => !p[2]), back = plan.slice(0, i + 1).reverse().filter((p) => !p[2]);
  return fwd.length >= back.length ? fwd : back;
}

function drawMarks(c) {
  for (const [x, y, kind] of S.spots) if (kind !== "mark") { g.fillStyle = "#8f78d055"; g.fillRect(toX(x) - 2, toY(y) - 2, 4, 4); }
  const path = upcoming();
  g.strokeStyle = "#e6b24c55"; g.lineWidth = 2; g.setLineDash([4, 6]);
  g.beginPath();
  path.forEach((p, i) => (i ? g.lineTo(toX(p[0]), toY(p[1])) : g.moveTo(toX(p[0]), toY(p[1]))));
  g.stroke(); g.setLineDash([]);
  for (const p of path.slice(1)) ring(p[0], p[1], 5, "#e6b24c88", 1.5);
  for (const [x, y, err] of S.marks) { dot(x, y, 6, errColor(err)); ring(x, y, 6, "#090b16", 1.5); }
  if (c && c.kind === "mark") return crosshair(c.x, c.y, "#ffffff");
  if (tap) return crosshair(tap.x, tap.y, "#ffffff");
  if (S.next) { ring(S.next[0], S.next[1], 15 + pulse(), "#e6b24c", 3); dot(S.next[0], S.next[1], 4, "#e6b24c"); }
}

function draw() {
  if (!S || !S.polygon || S.polygon.length < 3) return;
  fitView();
  g.clearRect(0, 0, tf.w, tf.h);
  tracePoly(S.polygon);
  g.fillStyle = "#11162a"; g.fill(); g.strokeStyle = "#6d7699"; g.lineWidth = 2; g.stroke();
  for (const o of S.objects) {
    tracePoly(o.footprint); g.fillStyle = "#1d2440"; g.fill(); g.strokeStyle = "#39436a"; g.lineWidth = 1; g.stroke();
    const fx = o.footprint.map((p) => p[0]), fy = o.footprint.map((p) => p[1]);
    centredText(o.label, toX((Math.min(...fx) + Math.max(...fx)) / 2), toY((Math.min(...fy) + Math.max(...fy)) / 2) + 4, "11px sans-serif", "#8089aa");
  }
  for (const b of S.boards) {
    g.beginPath(); g.moveTo(toX(b.x), toY(b.y) - 7); g.lineTo(toX(b.x) + 6, toY(b.y) + 4); g.lineTo(toX(b.x) - 6, toY(b.y) + 4); g.closePath();
    g.fillStyle = "#8fdcb288"; g.fill();
  }
  const c = S.capture, walking = c && c.kind === "walk";
  if (walking || (mode === "walk" && !c)) drawRoute(walking && walk.next != null);
  else if (mode === "trace") drawTraceLine();
  else drawMarks(c);
  const est = S.me && S.me.est;
  if (est && !walking && est.kind !== "stale") {
    g.beginPath(); g.arc(toX(est.x), toY(est.y), Math.max(8, est.unc * tf.k), 0, 2 * Math.PI);
    g.fillStyle = "#7fa8ff22"; g.fill();
    dot(est.x, est.y, 6, "#7fa8ff");
    ring(est.x, est.y, 6, "#ffffff", 1.5);
  }
}
setInterval(() => { if (S && !document.hidden) draw(); }, 60);

function drawTraceLine() {
  g.strokeStyle = "#ffffffaa"; g.lineWidth = 3; g.lineJoin = "round";
  g.beginPath();
  traceLine.forEach(([, x, y], i) => (i ? g.lineTo(toX(x), toY(y)) : g.moveTo(toX(x), toY(y))));
  g.stroke();
  if (finger) crosshair(finger.x, finger.y, "#ffffff");
}

function inside(x, y, poly) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) hit = !hit;
  }
  return hit;
}
function nearestIndex(list, x, y) {
  let best = -1, bestD = NEAR_M;
  list.forEach((p, i) => { const d = Math.hypot(p[0] - x, p[1] - y); if (d < bestD) { bestD = d; best = i; } });
  return best;
}
const mapXY = (ev) => {
  const r = canvas.getBoundingClientRect();
  return [(ev.clientX - r.left - tf.ox) / tf.k, (ev.clientY - r.top - tf.oy) / tf.k];
};
function traceAt(ev) {
  const [x, y] = mapXY(ev);
  if (!finger || Math.hypot(x - finger.x, y - finger.y) > 0.03 || performance.now() - finger.t >= TRACE_STEP_MS) addTracePoint(x, y);
}
canvas.addEventListener("pointermove", (ev) => { if (mode === "trace" && finger) traceAt(ev); });
const lift = () => { if (mode === "trace") { finger = null; view(); } };
canvas.addEventListener("pointerup", lift);
canvas.addEventListener("pointercancel", lift);
canvas.addEventListener("pointerdown", (ev) => {
  if (!S || S.capture) return;
  if (mode === "trace") {
    canvas.setPointerCapture(ev.pointerId);
    const [x, y] = mapXY(ev);
    addTracePoint(x, y);
    view();
    return;
  }
  let [x, y] = mapXY(ev);
  if (mode === "walk") { const i = nearestIndex(S.route, x, y); if (i >= 0) startDot = i; }
  else if (inside(x, y, S.polygon)) {
    // A tap on a plan spot means that spot exactly, so the server counts it done (planner.MARK_DONE_M).
    const i = nearestIndex(S.plan || [], x, y);
    if (i >= 0 && Math.hypot(S.plan[i][0] - x, S.plan[i][1] - y) < SNAP_M) [x, y] = S.plan[i];
    tap = { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
    msg = null;
  }
  view();
  draw();
});

$("traceBtn").onclick = () => { if (S && !S.capture) { mode = "trace"; msg = null; tap = null; $("more").open = false; enableMotion(); view(); draw(); } };
$("walkBtn").onclick = () => { if (S && !S.capture) { mode = "walk"; msg = null; $("more").open = false; view(); draw(); } };
$("emptyBtn").onclick = () => { if (S && !S.capture) act(() => call("/api/empty/start")); };
$("clearBtn").onclick = () => {
  if (!S || S.capture || !confirm("Delete every mark, walk and check for this room?")) return;
  act(async () => { await call("/api/calib/clear"); msg = ["good", "Calibration cleared."]; tap = null; });
};
window.addEventListener("resize", draw);
poll();
