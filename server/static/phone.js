"use strict";
// Phone calibration page, served by the laptop on the demo network (PhoneHandler in luna_server.py). The server
// speaks metres; the screen shows feet. Route and check points come from server/planner.py (S.route, S.checks).
// What the card shows is decided only by view(), from (mode, server capture, walk.next).
const $ = (id) => document.getElementById(id);
const FT = 0.3048;
const ft = (m) => (m / FT).toFixed(1);
const median = (a) => (a && a.length ? a.slice().sort((x, y) => x - y)[a.length >> 1] : null);
const WALK_SPEED_MS = 0.6, CHECK_S = 8, SPOT_S = 12, NEAR_M = 0.25;
// ?mac= lets the laptop pose as a given phone for testing; the server accepts it only from 127.0.0.1.
const MAC = new URLSearchParams(location.search).get("mac") || "";

let pin = "", S = null, mode = "walk", busy = false, flash = "";
let startDot = 0, checkSel = null, spotTap = null;
let walk = { next: null, done: [], before: 0, skipped: new Set() };
try { pin = localStorage.lunaPin || ""; } catch (e) {}

async function call(path, body) {
  const payload = Object.assign({ pin, mac: MAC || undefined }, body || {});
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || res.status);
  return json;
}

// Polls only while a PIN is set. A 403 clears it so a stale or mistyped PIN can't run into the server's lockout.
let polling = false;
async function poll() {
  if (!pin || polling) return;
  polling = true;
  try {
    const query = "?pin=" + encodeURIComponent(pin) + (MAC ? "&mac=" + encodeURIComponent(MAC) : "");
    const res = await fetch("/api/state" + query, { cache: "no-store" });
    const json = await res.json();
    if (res.status === 403) { pin = ""; S = null; $("app").classList.add("hidden"); throw new Error(json.error); }
    if (!res.ok) throw new Error(json.error || res.status);
    const previous = S && S.capture;
    S = json;
    $("pinCard").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("err").textContent = "";
    if (previous && !S.capture) captureEnded(previous);
    keepAwake(!!S.capture);
    view();
    draw();
  } catch (e) {
    if (!S) { $("pinCard").classList.remove("hidden"); $("pinErr").textContent = String(e.message); }
    else $("err").textContent = "Lost connection to the laptop: " + e.message;
  } finally {
    polling = false;
  }
}

// Keep this phone sending frames: every request crosses the demo network, like the 10/s mode on the AP portal.
let inflight = 0;
setInterval(() => {
  if (document.hidden || inflight > 3) return;
  inflight++;
  fetch("/ping", { cache: "no-store" }).catch(() => {}).finally(() => inflight--);
}, 100);
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
const buzz = () => navigator.vibrate && navigator.vibrate(40);

// Motion: still/moving, steps and compass heading (degrees clockwise from north; the server learns the room offset
// from walks, walkcal.heading_fit). Posted to /api/motion (tracker.set_motion) every MOTION_MS. Needs https and, on
// iPhone, a tap to allow. This is the tracker's biggest accuracy source and walkcal uses it to place the walker
// between taps, so it must not miss slow walking (a fixed "|a| > 11.5" threshold counted 3 steps over 4.7 m).
// Steps: |acceleration| is low-passed (LP_S) and its slow part (BASE_S: gravity, tilt) removed, leaving the walking
// bounce. A step is that signal rising through +th after dipping below -th/2; th tracks recent bounce size but never
// drops under STEP_MIN (hand tremor is ~0.1 m/s^2). A lone bump isn't a step: it counts once a second follows
// within PAIR_MS.
const MOTION_MS = 250, LP_S = 0.06, BASE_S = 1.0, ENERGY_S = 0.8, STEP_MIN = 0.45, STEP_GAP_MS = 300, PAIR_MS = 1600,
  MOVING_MS = 1200, MOVING_RMS = 0.3;
let motionOn = false, mv = -1, steps = 0, heading = null;
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
  if (!motionOn || !pin) return;
  const rms = Math.sqrt(acc.energy);
  if (acc.n > 10) mv = (performance.now() - acc.lastStep < MOVING_MS && !acc.pending) || rms > MOVING_RMS ? 1 : 0;
  // ac (bounce rms, m/s^2) is only recorded, so the thresholds above can be retuned against real walks.
  if (mv >= 0) call("/api/motion", { mv, st: steps, hd: heading, ac: Math.round(rms * 100) / 100 }).catch(() => {});
  $("motionTxt").textContent = mv < 0 ? "waiting for the sensor…" : `${mv ? "moving" : "still"} · ${steps} steps`;
}
setInterval(reportMotion, MOTION_MS);

$("motionBtn").onclick = async () => {
  const say = (t) => ($("motionTxt").textContent = t);
  if (!window.isSecureContext) return say("Needs the https address shown on the laptop.");
  try {
    if (window.DeviceMotionEvent?.requestPermission && (await DeviceMotionEvent.requestPermission()) !== "granted") {
      return say("Permission denied. Allow Motion & Orientation for this site in Safari settings.");
    }
    if (window.DeviceOrientationEvent?.requestPermission) await DeviceOrientationEvent.requestPermission();
  } catch (e) {
    return say("Motion isn't available: " + e.message);
  }
  window.addEventListener("devicemotion", onDeviceMotion);
  window.addEventListener("deviceorientation", onOrientation);
  window.addEventListener("deviceorientationabsolute", onOrientation);
  motionOn = true;
  $("motionBtn").classList.add("hidden");
  say("waiting for the sensor…");
};

$("emptyBtn").onclick = () => { if (S && !S.capture) act(() => call("/api/empty/start")); };
$("clearBtn").onclick = () => {
  if (!S || S.capture || !confirm("Delete every walk, point and check for this room?")) return;
  act(async () => { await call("/api/calib/clear"); flash = "Calibration cleared. Start again with 1 · Walk."; });
};

function captureEnded(cap) {
  const added = S.spots.length - walk.before;
  if (cap.kind === "empty") flash = "Empty room recorded ✓ Come back in, and do 1 · Walk next if you haven't.";
  else if (cap.kind === "walk") {
    if (added <= 0) flash = "Nothing was saved (walk too short?).";
    else flash = `Saved ✓ ${added} new map points. ` + (S.walks >= 2 ? "Next: tap 2 · Check." : "Now walk it the other way round (the dots are renumbered) for a much better map.");
  } else if (cap.kind === "test") {
    const last = S.tests[S.tests.length - 1], err = last && median(last.errors_m);
    flash = err != null ? `Off by ${ft(err)} ft here.` : "No position here, try again.";
    checkSel = null;
  } else flash = added > 0 ? "Point saved ✓" : "Point not saved (too few frames).";
  walk = { next: null, done: [], before: S.spots.length, skipped: new Set() };
  buzz();
}

$("pinGo").onclick = () => {
  pin = $("pin").value.trim();
  try { localStorage.lunaPin = pin; } catch (e) {}
  poll();
};
document.querySelectorAll("[data-mode]").forEach((btn) => (btn.onclick = () => {
  if (S && S.capture) return;
  mode = btn.dataset.mode;
  flash = "";
  spotTap = null;
  document.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b === btn));
  view();
  draw();
}));

async function act(fn) {
  if (busy) return;
  busy = true;
  $("err").textContent = "";
  try { await fn(); await poll(); }
  catch (e) { $("err").textContent = e.message; }
  finally { busy = false; view(); }
}

function showButtons(list) {
  const box = $("actions");
  box.innerHTML = "";
  for (const [text, cls, fn, disabled] of list) {
    const b = document.createElement("button");
    b.textContent = text;
    if (cls) b.className = cls;
    b.disabled = !!disabled;
    b.onclick = () => act(fn);
    box.appendChild(b);
  }
}
function card(title, help, status) {
  $("title").textContent = title;
  $("help").innerHTML = help;
  $("status").textContent = status || "";
}
const countdown = (c) => (c.phase === "countdown" ? `Starting in ${Math.ceil(-c.elapsed)}…` : `${Math.max(0, Math.ceil(c.duration - c.elapsed))} s left`);

// After a skip, the leg from the last mark to the next dot isn't a drawn straight line either.
const isDetour = (i) => !!(S.route[i][2] || walk.skipped.has(i));
const checkDone = (i) => { const [x, y] = S.checks[i]; return S.tests.find((t) => Math.hypot(t.x - x, t.y - y) < NEAR_M); };
function routeMinutes(from) {
  let dist = 0;
  for (let i = from + 1; i < S.route.length; i++) dist += Math.hypot(S.route[i][0] - S.route[i - 1][0], S.route[i][1] - S.route[i - 1][1]);
  return Math.max(1, Math.round((dist / WALK_SPEED_MS + 2 * (S.route.length - from)) / 60));
}

async function startWalk() {
  const [x, y] = S.route[startDot];
  walk = { next: startDot + 1, done: [[x, y, false]], before: S.spots.length, skipped: new Set() };
  await call("/api/walk/start", { orientation: $("ori").value });
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
const cancel = () => call("/api/cancel");
const stopWalk = () => call("/api/walk/stop");

function viewWalking() {
  if (walk.next == null) {
    card("Walk in progress", "This page was reloaded during a walk. Save what was recorded or cancel it.");
    showButtons([["Stop and save", "primary huge", stopWalk], ["Cancel walk", "", cancel]]);
    return;
  }
  const n = walk.next + 1;
  const how = isDetour(walk.next) ? "Walk <b>around the furniture</b> to the green dot. "
    : "Walk <b>straight</b> to the green dot at a normal pace, phone held in front of you. ";
  card(`Walk to dot ${n}`, how + "<b>Stop on it</b>, then press the button." + (motionOn ? "" : " <b>Turn Motion on first</b>: the map is much better with it."),
    `${walk.next} of ${S.route.length} done`);
  showButtons([[`I'm on dot ${n}`, "primary huge", () => reached(true)], ["Can't get there, skip", "", () => reached(false)],
    ["Stop and save", "", stopWalk], ["Cancel", "", cancel]]);
}

function viewWalkIntro() {
  const reversed = S.route_reversed ? `<b>Walk ${S.walks + 1}: same dots, opposite direction</b>, so the map learns the room from both sides of your body.` : "";
  card("Step 1 · Walk the room", reversed + `<ol>
    <li>Go to the <b>green dot ${startDot + 1}</b> on the map and stand on it.<span class="small muted"> (Tap another dot to start there.)</span></li>
    <li>Hold the phone in front of you as if reading it, pointing the way you walk.</li>
    <li>Press Start. Walk to each next dot at a normal pace, <b>stop on it</b>, then press <b>I'm on dot …</b>. No rush between dots: standing is recorded too.</li></ol>
    <span class="small muted">About ${routeMinutes(startDot)} min. You can stop and save any time.</span>`);
  showButtons([[`Start at dot ${startDot + 1}`, "primary huge", startWalk]]);
}

function viewCheck() {
  const left = S.checks.map((_, i) => i).filter((i) => !checkDone(i));
  if (checkSel == null) checkSel = left.length ? left[0] : null;
  const errs = S.checks.map((_, i) => checkDone(i)).filter(Boolean).map((t) => median(t.errors_m)).filter((x) => x != null);
  const typical = median(errs);
  const summary = typical == null ? "" : `<br>Typical error so far: <b>${ft(typical)} ft</b>. ` + (typical < 3 * FT ? "Looks good." : "Walk the room again (Step 1) to improve it.");
  if (checkSel == null) {
    card("Step 2 · Done", "Every check square is measured. Tap a square to measure it again." + summary);
    showButtons([]);
    return;
  }
  card("Step 2 · Check accuracy", `Stand on the <b>blue square ${checkSel + 1}</b> and press Check, then stay still for ${CHECK_S} seconds. This doesn't change the map.${summary}`);
  showButtons([[`Check at square ${checkSel + 1}`, "primary huge", () => {
    walk.before = S.spots.length;
    const [x, y] = S.checks[checkSel];
    return call("/api/capture", { kind: "test", x, y, orientation: $("ori").value, duration: CHECK_S });
  }]]);
}

function viewExtraSpot() {
  card("Extra standing point", "For a corner or area the walk missed: tap exactly where you're standing, then Record (stand still 12 s).",
    spotTap ? `At ${ft(spotTap.x)}, ${ft(spotTap.y)} ft` : "Tap your position on the map");
  showButtons([["Record here", "primary huge", () => {
    walk.before = S.spots.length;
    return call("/api/capture", { kind: "calibrate", x: spotTap.x, y: spotTap.y, orientation: $("ori").value, duration: SPOT_S });
  }, !spotTap]]);
}

function view() {
  if (!S) return;
  const c = S.capture;
  $("moreCard").classList.toggle("hidden", mode !== "more" || !!c);
  if (!motionOn && !S.https) $("motionTxt").textContent = "Motion needs the laptop's https page (it isn't set up).";
  $("flash").textContent = c ? "" : flash;
  $("prog").classList.toggle("hidden", !(c && c.kind !== "walk" && c.phase === "recording"));
  if (c && c.kind !== "walk") $("prog").firstElementChild.style.width = Math.min(100, (100 * c.elapsed) / c.duration) + "%";
  const est = S.me && S.me.est;
  $("me").textContent = S.me ? (S.me.name || S.me.mac) + (est && mode !== "walk" ? ` · current estimate (hollow circle) is ±${ft(est.unc)} ft` : "") : "";

  if (!S.me) { card("Not connected", "This phone isn't on the demo network yet. Join it in your Wi-Fi settings, then reload."); showButtons([]); return; }
  if (!S.route.length) { card("Room not set up", "The room outline isn't set on the laptop yet (Setup &amp; debug → Layout)."); showButtons([]); return; }
  if (c && c.kind === "walk") return viewWalking();
  if (c && c.kind === "empty") {
    card(c.phase === "countdown" ? "Leave the room now" : "Recording the empty room…",
      "Everyone out, and close the door if there is one. The phone buzzes when it's done.", countdown(c));
    showButtons([["Cancel", "", cancel]]);
    return;
  }
  if (c) {
    card(c.kind === "test" ? "Checking…" : "Recording point…", "Stand still on the spot, phone held normally.", countdown(c));
    showButtons([["Cancel", "", cancel]]);
    return;
  }
  if (mode === "walk") viewWalkIntro();
  else if (mode === "check") viewCheck();
  else viewExtraSpot();
}

const canvas = $("map"), g = canvas.getContext("2d");
let tf = { k: 1, ox: 0, oy: 0, w: 0, h: 0 };
function fitView() {
  const w = canvas.clientWidth, xs = S.polygon.map((p) => p[0]), ys = S.polygon.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys), pad = 16;
  const k = (w - 2 * pad) / Math.max(0.1, x1 - x0), h = Math.round((y1 - y0) * k + 2 * pad), dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.height = h + "px";
  }
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  tf = { k, ox: pad - x0 * k, oy: pad - y0 * k, w, h };
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
function numberedDot(x, y, r, fill, num, ink = "#090b16") {
  g.beginPath(); g.arc(toX(x), toY(y), r, 0, 2 * Math.PI); g.fillStyle = fill; g.fill();
  if (num != null) centredText(num, toX(x), toY(y) + 4, `bold ${r > 10 ? 13 : 11}px sans-serif`, ink);
}

function drawRoute(walking) {
  const current = walking ? walk.next : startDot;
  // Detour legs (furniture in the way) are drawn faint: there's no straight line to follow.
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
  S.route.forEach((p, i) => { if (i !== current) numberedDot(p[0], p[1], 9, walking && i < current ? "#454d6c" : "#e6b24c", i + 1); });
  if (current < S.route.length) {
    const p = S.route[current], pulse = 13 + 3 * Math.sin(performance.now() / 200);
    g.beginPath(); g.arc(toX(p[0]), toY(p[1]), pulse + 5, 0, 2 * Math.PI); g.strokeStyle = "#3e9e74"; g.lineWidth = 2; g.stroke();
    numberedDot(p[0], p[1], 13, "#3e9e74", current + 1, "#fff");
  }
}

function drawChecks() {
  S.checks.forEach(([x, y], i) => {
    const done = checkDone(i), sel = i === checkSel, s = sel ? 13 : 9;
    g.fillStyle = done ? "#454d6c" : sel ? "#7fa8ff" : "#7fa8ff99";
    g.fillRect(toX(x) - s, toY(y) - s, 2 * s, 2 * s);
    centredText(i + 1, toX(x), toY(y) + 4, "bold 12px sans-serif", "#fff");
    const err = done && median(done.errors_m);
    if (err != null) { g.fillStyle = "#e7eaf6"; g.font = "12px sans-serif"; g.fillText(`${ft(err)} ft off`, toX(x) + s + 3, toY(y) + 4); }
  });
}

function draw() {
  if (!S) return;
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
  const c = S.capture, walkCapture = c && c.kind === "walk", walking = walkCapture && walk.next != null;
  if (mode === "walk" || walking) drawRoute(walking);
  else {
    for (const [x, y, kind] of S.spots) {
      const r = kind === "walk" ? 2 : 4;
      g.fillStyle = kind === "walk" ? "#8f78d088" : "#c9b6ff";
      g.fillRect(toX(x) - r, toY(y) - r, 2 * r, 2 * r);
    }
  }
  if (mode === "check" && !walkCapture) drawChecks();
  const est = S.me && S.me.est;
  if (est && mode !== "walk" && !walkCapture) {
    g.strokeStyle = "#7fa8ff"; g.lineWidth = 2; g.beginPath(); g.arc(toX(est.x), toY(est.y), 7, 0, 2 * Math.PI); g.stroke();
  }
  if (mode === "more" && spotTap) {
    const X = toX(spotTap.x), Y = toY(spotTap.y);
    g.strokeStyle = "#e6b24c"; g.lineWidth = 2;
    g.beginPath(); g.arc(X, Y, 12, 0, 2 * Math.PI); g.moveTo(X - 18, Y); g.lineTo(X + 18, Y); g.moveTo(X, Y - 18); g.lineTo(X, Y + 18); g.stroke();
  }
}
setInterval(() => { if (S && !document.hidden && (mode === "walk" || (S.capture && S.capture.kind === "walk"))) draw(); }, 50);

function inside(x, y, poly) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) hit = !hit;
  }
  return hit;
}
function nearestIndex(list, x, y) {
  let best = -1, bestD = 0.6;
  list.forEach((p, i) => { const d = Math.hypot(p[0] - x, p[1] - y); if (d < bestD) { bestD = d; best = i; } });
  return best;
}
canvas.addEventListener("pointerdown", (ev) => {
  if (!S || S.capture) return;
  const r = canvas.getBoundingClientRect();
  const x = (ev.clientX - r.left - tf.ox) / tf.k, y = (ev.clientY - r.top - tf.oy) / tf.k;
  if (mode === "walk") { const i = nearestIndex(S.route, x, y); if (i >= 0) startDot = i; }
  else if (mode === "check") { const i = nearestIndex(S.checks, x, y); if (i >= 0) { checkSel = i; flash = ""; } }
  else if (inside(x, y, S.polygon)) spotTap = { x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 };
  view();
  draw();
});
window.addEventListener("resize", draw);
$("pin").value = pin;
poll();
