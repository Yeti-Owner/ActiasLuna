"use strict";
// Dashboard. Server state and the wire are metres; only what people read is in feet (ft(), snapFt()).
const $ = (id) => document.getElementById(id);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const FT = 0.3048;
const ft = (m, digits = 1) => (m / FT).toFixed(digits);
const snapFt = (m) => (Math.round((m / FT) * 2) / 2) * FT;
const clone = (o) => JSON.parse(JSON.stringify(o));
const PALETTE = ["#8fdcb2", "#7fa8ff", "#f4a259", "#e07bb5", "#e6c44c", "#b38cff", "#62d2e0", "#ff8a7a"];
const STATUS_COLOR = { online: "#8fdcb2", stale: "#e6b24c", offline: "#ee6a73" };
const GREY = "#7a819c";

// S: latest snapshot from the server (SSE, ~4 Hz). L: the layout on screen; follows S.layout unless editing.
let S = null, L = null, view = "live", stream = null;
let editing = false, selectedPhone = null, selectedCorner = -1, dragging = null, placing = null, plan = null;
let tab = "tLayout", simple = true, show3d = false, v3 = null, SCAN = null;
const colors = {};
// Drawn marker per phone (mac -> {x, y, unc}), eased toward the latest estimate each frame so the 2D map moves
// smoothly between 4 Hz snapshots.
const shown = {};

try { simple = localStorage.lunaMode !== "debug"; show3d = localStorage.luna3d === "1"; } catch (e) {}
const remember = (k, v) => { try { localStorage[k] = v; } catch (e) {} };

function colorFor(mac) {
  if (!colors[mac]) colors[mac] = PALETTE[Object.keys(colors).length % PALETTE.length];
  return colors[mac];
}
const nodeLabel = (id) => S?.nodes.find((n) => n.id === id)?.label ?? String(id).slice(-4);
const age = (a) => (a == null ? "—" : a < 1 ? "now" : a < 90 ? a.toFixed(0) + "s" : (a / 60).toFixed(0) + "m");

async function api(path, body) {
  const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, init);
  const json = await res.json().catch(() => ({}));
  if (!res.ok) { alert(json.error || res.status); throw new Error(json.error); }
  return json;
}

function connect() {
  if (stream) stream.close();
  stream = new EventSource("/api/events?view=" + view);
  stream.onmessage = (ev) => {
    S = JSON.parse(ev.data);
    mergeBody(S);
    mergeCrowd(S);
    if (!editing) L = clone(S.layout);
    render();
  };
  stream.onerror = () => { $("hdrInfo").innerHTML = '<span class="badbox">lost connection to the server…</span>'; };
}

function loadScan() {
  fetch("/api/scan").then((r) => r.json()).then((j) => {
    SCAN = j && j.polygon ? j : null;
    if (v3) v3.roomSig = "";
    drawMap();
  }).catch(() => {});
}

// The phone-free person (tracker.body) joins S.phones so map, list and 3D draw it like a phone. "body" is not a MAC
// and is never sent back to the server.
function mergeBody(s) {
  const b = s.body;
  if (!b || !b.present) return;
  s.phones.push({
    mac: "body", mac_fmt: "no phone", name: "Person (no phone)", associated: false, age: 0, per_node: {}, spark: [],
    trail: b.trail || [],
    est: { x: b.x, y: b.y, unc: b.unc, raw: [b.x, b.y, b.unc], kind: "estimated", receivers: b.links, mode: "device-free", age: 0, t: b.t },
  });
}

// Crowd mode (server/display.py): the simulated crowd replaces every real person in S.phones so everything draws it
// like phones. Its MACs are fake and never go back to the server.
function mergeCrowd(s) {
  const c = s.crowd;
  if (!c || !c.on) return;
  s.phones = c.people.map((p) => ({
    mac: p.mac, mac_fmt: p.mac.match(/../g).join(":"), name: "", associated: true, age: 0, per_node: {}, spark: [],
    trail: p.trail.map((q) => [q[0], q[1], q[2], 0.4, "estimated"]),
    est: { x: p.x, y: p.y, unc: 0.4, raw: [p.x, p.y, 0.4], kind: "estimated", receivers: 3, mode: "calibrated", age: 0, t: s.now },
  }));
}

// 3D view: needs WebGL and the bundled three.js; report failures instead of showing a black box.
function fail3d(msg) {
  const box = $("v3msg");
  box.innerHTML = msg + '<br><span class="small muted">The 2D map shows the same thing. Press "2D map".</span>';
  box.classList.remove("hidden");
}
function ensure3d() {
  if (!v3 && window.Luna3D) {
    try { v3 = window.Luna3D.create($("view3d")); $("v3msg").classList.add("hidden"); }
    catch (e) { fail3d("The 3D view couldn't start: WebGL is off or unavailable in this browser (" + esc(e.message || e) + ")."); }
  }
  if (!v3 && !window.Luna3D) {
    setTimeout(() => { if (!window.Luna3D && simple && show3d) fail3d("The 3D view didn't load (is three.js in server/static/lib/three?). Reload to retry."); }, 4000);
  }
  return v3;
}
function apply3d() {
  const on = simple && show3d;
  document.body.classList.toggle("d3", on);
  $("view3d").classList.toggle("hidden", !on);
  $("dimTog").textContent = on ? "2D map" : "3D view";
  if (on && ensure3d()) {
    v3.setVisible(true);
    if (S) { v3.setRoom(S.layout, SCAN); v3.update(S, colorFor, nodeLabel); }
  } else if (v3) v3.setVisible(false);
  if (!on) setTimeout(drawMap, 0);
}
window.addEventListener("luna3d-ready", apply3d);

function applyMode() {
  document.body.classList.toggle("simple", simple);
  $("modeTog").textContent = simple ? "Setup & debug" : "Simple view";
  if (simple && editing) { editing = false; $("editTog").checked = false; }
  setTimeout(drawMap, 0);
  apply3d();
}

function render() {
  if (!S) return;
  const live = view === "live", rp = S.replay;
  $("viewBadge").textContent = live ? "LIVE" : "REPLAY";
  $("viewBadge").className = "badge " + (live ? "live" : "replay");
  const net = S.ssid ? `network “${S.ssid}”` : "no config/network.json";
  $("hdrInfo").textContent = `${net} · laptop ${S.laptop_ip ? "on the demo network" : "NOT on the demo network"}`;
  $("recBadge").textContent = S.recording.on ? "● REC" : "not recording";
  $("recBadge").className = "badge" + (S.recording.on ? " rec" : "");
  $("recTog").textContent = S.recording.on ? "Stop recording" : "Start recording";
  $("recInfo").textContent = S.recording.on ? `${S.recording.file} (${S.recording.packets} packets)` : "";
  $("viewTog").textContent = live ? "Show replay" : "Back to live";
  $("replayBar").classList.toggle("hidden", !(!live && rp));
  if (rp && !live) {
    const span = rp.t1 - rp.t0, at = rp.pos - rp.t0;
    $("rpProg").value = at / Math.max(1e-6, span);
    $("rpTime").textContent = `${at.toFixed(0)} / ${span.toFixed(0)} s ${rp.state === "finished" ? "(end)" : ""} · ${rp.file}`;
    $("rpPause").textContent = rp.state === "paused" ? "Play" : "Pause";
    $("rpPause").disabled = rp.state === "finished";
  }
  renderSimple();
  if (!simple) { renderTables(); renderLayoutPanel(); renderCalibration(); renderSettings(); }
  if (simple && show3d && v3) { v3.setRoom(S.layout, SCAN); v3.update(S, colorFor, nodeLabel); }
}

function boundsOf(poly) {
  const xs = poly.map((p) => p[0]), ys = poly.map((p) => p[1]);
  return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) };
}
function inside(x, y, poly) {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) hit = !hit;
  }
  return hit;
}
function segDist(x, y, a, b) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy + 1e-12)));
  return Math.hypot(x - a[0] - t * dx, y - a[1] - t * dy);
}
const edgeDist = (x, y, poly) => Math.min(...poly.map((p, i) => segDist(x, y, p, poly[(i + 1) % poly.length])));
const polyDist = (x, y, poly) => (inside(x, y, poly) ? 0 : edgeDist(x, y, poly));

// A plain-language place: the nearest scanned furniture, door or window within NEAR_M, else by the wall or the middle
// of the room. Shown when the estimate is too rough for the dot alone to mean much.
const NEAR_M = 0.9, VAGUE_UNC_M = 2 * FT;
function placeName(x, y) {
  let best = null, bestD = NEAR_M;
  const things = SCAN ? SCAN.objects.concat(SCAN.openings.map((o) => ({ label: o.kind, footprint: o.footprint }))) : [];
  for (const o of things) {
    const d = polyDist(x, y, o.footprint);
    if (d < bestD) { bestD = d; best = o.label.toLowerCase(); }
  }
  if (best) return "by the " + best;
  return edgeDist(x, y, (L || S.layout).polygon) < NEAR_M ? "by the wall" : "middle of the room";
}

function whereText(p) {
  const e = p.est;
  if (!e) return Object.keys(S.layout?.receivers || {}).length ? "waiting for all boards…" : "place the boards in Setup → Layout";
  if (e.kind === "stale") return `not heard for ${age(p.age)}`;
  return `(${ft(e.x)}, ${ft(e.y)}) ft ± ${ft(e.unc)} ft` + (e.kind === "weak" ? " · few boards hear it" : "");
}
const displayName = (p) => p.name || "Phone " + p.mac_fmt.slice(-5);

function renderSimple() {
  const people = S.phones.slice().sort((a, b) => (a.name || a.mac).localeCompare(b.name || b.mac));
  $("sPeople").innerHTML = people.map((p) => {
    const e = p.est, stale = e && e.kind === "stale";
    const certainty = e && !stale ? (e.unc < 1.5 * FT ? "pinpointed" : e.unc < 3 * FT ? "located" : "rough area") : "";
    const line = stale ? `not heard for ${age(p.age)}` : e ? `${placeName(e.x, e.y)} · ${certainty}, within ~${ft(e.unc, 0)} ft` : esc(whereText(p));
    return `<div class="person ${stale ? "stale" : ""}"><span class="sw" style="background:${colorFor(p.mac)}"></span>
      <div><div class="nm">${esc(displayName(p))}</div><div class="wh">${line}</div></div></div>`;
  }).join("") || '<div class="muted">Nobody yet. Join the demo network and open http://192.168.4.1</div>';
  $("sRx").innerHTML = S.nodes.map((n) => `<span class="rxchip"><span class="dot ${n.status}"></span>${esc(n.label)}</span>`).join("")
    || '<span class="muted small">no boards heard. Is the laptop on the demo network?</span>';

  const unplaced = S.nodes.filter((n) => !L.receivers[n.id]).length;
  const c = S.calib, ev = c.evaluation;
  let hint;
  if (unplaced) hint = `${unplaced} board(s) not on the map yet (Setup & debug → Layout).`;
  else if (S.settings?.device_free && !c.df?.empty) hint = "Phone-free tracking is on but the empty room hasn't been recorded (phone page → More, or Setup → Calibrate).";
  else if (!c.spots.length) hint = "Not calibrated, so positions are rough. Open the phone page (Setup & debug → Calibrate) and mark a few spots with I'm here.";
  else if (ev && ev.loo) hint = `Calibrated with ${c.spots.length} points · a single reading is ~${ft(ev.loo.median_m)} ft off, better when tracked with Motion on`;
  else hint = `Calibrated with ${c.spots.length} point(s).`;
  $("sHint").innerHTML = hint;
}

function renderTables() {
  $("nodesT").innerHTML = "<tr><th>Board</th><th>role</th><th>seen</th><th>pkt/s</th><th title='phone frames per second; in brackets those from RTS/BlockAck/PS-Poll control frames'>frames/s</th><th>loss</th><th>notes</th></tr>" +
    S.nodes.map((n) => {
      const notes = (n.reboots ? `reboots ${n.reboots} ` : "") + (n.disc ? `disc ${n.disc} ` : "") + (n.ovf ? `ovf ${n.ovf}` : "");
      return `<tr title="${esc(n.id)} ${esc(n.ip)} fw ${esc(n.fw)}"><td><span class="dot ${n.status}"></span>${esc(n.label)}</td><td>${esc(n.role)}</td>
        <td>${age(n.age)}</td><td>${n.pkt_rate}</td><td>${n.frame_rate}${n.ctrl_rate ? ` <span class="muted">(${n.ctrl_rate} ctl)</span>` : ""}</td>
        <td>${n.loss_pct}%</td><td class="small muted">${notes}</td></tr>`;
    }).join("") + (S.nodes.length ? "" : '<tr><td colspan=7 class="muted">no boards heard yet</td></tr>');

  const ids = S.nodes.map((n) => n.id);
  const cell = (q) => (q && q.r != null ? `${q.r}<span class=muted>/${q.rate}</span>` : "<span class=muted>—</span>");
  $("phonesT").innerHTML = "<tr><th>phone</th><th>where</th>" + ids.map((i) => `<th>${esc(nodeLabel(i))} dBm</th>`).join("") + "</tr>" +
    S.phones.map((p) => `<tr class="click ${selectedPhone === p.mac ? "sel" : ""}" data-mac="${esc(p.mac)}">
      <td><span class="dot" style="background:${colorFor(p.mac)}"></span>${esc(p.name || p.mac_fmt.slice(-8))}</td><td>${esc(whereText(p))}</td>` +
      ids.map((i) => { const q = p.per_node[i]; return `<td title="${q ? q.rate + " frames/s" : "none"}">${cell(q)}</td>`; }).join("") + "</tr>").join("") +
    (S.phones.length ? "" : `<tr><td colspan=${2 + ids.length} class="muted">no phones sending frames</td></tr>`);
  document.querySelectorAll("#phonesT tr.click").forEach((tr) => (tr.onclick = () => { selectedPhone = tr.dataset.mac; render(); }));
  renderPhoneDetail();

  $("staT").innerHTML = "<tr><th>MAC</th><th>IP</th><th>name</th></tr>" + S.stations.map((s) =>
    `<tr><td class="mono">${esc(s.mac)}</td><td>${esc(s.ip)}</td><td>${s.infra ? '<span class="muted">board/laptop</span>' : esc(s.nm)}</td></tr>`).join("");
  const links = S.links.map((l) => `${esc(l.tx)}→${esc(l.rx)} ${l.r}`).join(", ") || "none yet";
  $("dropInfo").innerHTML = `Board-to-board links: ${links}<br>${S.dropped_unassoc} frames ignored from devices not on the demo network · ` +
    `${S.packets} packets${S.bad_packets ? ", " + S.bad_packets + " bad" : ""}`;
  $("evList").innerHTML = S.events.slice().reverse().map((e) => `<div>${new Date(e[0] * 1000).toLocaleTimeString()} ${esc(e[1])}</div>`).join("");
}

function renderPhoneDetail() {
  const p = S.phones.find((q) => q.mac === selectedPhone);
  if (!p) { $("phoneDetail").innerHTML = ""; return; }
  $("phoneDetail").innerHTML = `<div class="small"><b>${esc(p.name || "(no nickname)")}</b> <span class="mono">${p.mac_fmt}</span> ${esc(p.ip || "")}<br>${esc(whereText(p))}</div>
    <canvas id="spark" width="360" height="110" style="height:110px;min-height:0"></canvas><div class="small muted">signal strength per board, last 30 s</div>`;
  const c = $("spark"), g = c.getContext("2d");
  const lo = -90, hi = -15;
  const py = (r) => c.height - ((r - lo) / (hi - lo)) * c.height;
  const px = (dt) => c.width + (dt / 30) * c.width;
  g.fillStyle = "#090b16"; g.fillRect(0, 0, c.width, c.height);
  g.strokeStyle = "#262e48"; g.fillStyle = "#8d95b3"; g.font = "9px sans-serif";
  for (let r = -80; r <= -20; r += 20) { g.beginPath(); g.moveTo(0, py(r)); g.lineTo(c.width, py(r)); g.stroke(); g.fillText(r, 2, py(r) - 2); }
  const byBoard = {};
  p.spark.forEach(([dt, board, r]) => (byBoard[board] = byBoard[board] || []).push([dt, r]));
  Object.entries(byBoard).sort().forEach(([board, pts], k) => {
    g.strokeStyle = PALETTE[(k + 3) % PALETTE.length];
    g.beginPath();
    pts.forEach(([dt, r], i) => (i ? g.lineTo(px(dt), py(r)) : g.moveTo(px(dt), py(r))));
    g.stroke();
    g.fillStyle = g.strokeStyle; g.fillText(nodeLabel(board), c.width - 30, 10 + 10 * k);
  });
}

const canvas = $("map"), ctx = canvas.getContext("2d");
// World (m) -> canvas px. Recomputed every draw so resizes and layout edits just work.
let view2d = { k: 50, ox: 20, oy: 20, w: 0, h: 0 };
function fitView() {
  const r = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const pts = L.polygon.concat(Object.values(L.receivers).map((p) => [p.x, p.y]));
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const minX = Math.min(0, ...xs), maxX = Math.max(...xs, 1), minY = Math.min(0, ...ys), maxY = Math.max(...ys, 1);
  const pad = 36, k = Math.max(5, Math.min((r.width - 2 * pad) / (maxX - minX), (r.height - 2 * pad) / (maxY - minY)));
  view2d = { k, w: r.width, h: r.height,
    ox: pad - minX * k + (r.width - 2 * pad - (maxX - minX) * k) / 2,
    oy: pad - minY * k + (r.height - 2 * pad - (maxY - minY) * k) / 2 };
}
const toX = (x) => view2d.ox + x * view2d.k, toY = (y) => view2d.oy + y * view2d.k;
const fromX = (px) => (px - view2d.ox) / view2d.k, fromY = (py) => (py - view2d.oy) / view2d.k;
function tracePoly(poly) {
  ctx.beginPath();
  poly.forEach((p, i) => (i ? ctx.lineTo(toX(p[0]), toY(p[1])) : ctx.moveTo(toX(p[0]), toY(p[1]))));
  ctx.closePath();
}
function label(text, x, y, font = "11px sans-serif", color = "#8d95b3", align = "left") {
  ctx.fillStyle = color; ctx.font = font; ctx.textAlign = align; ctx.fillText(text, x, y); ctx.textAlign = "left";
}

function drawRoom() {
  const b = boundsOf(L.polygon);
  tracePoly(L.polygon);
  ctx.fillStyle = "#11162a"; ctx.fill();
  ctx.save(); ctx.clip();
  const gridLine = (foot, x0, y0, x1, y1) => {
    ctx.strokeStyle = foot % 5 ? "#171d33" : "#232b46";
    ctx.beginPath(); ctx.moveTo(toX(x0), toY(y0)); ctx.lineTo(toX(x1), toY(y1)); ctx.stroke();
  };
  for (let f = 0; f * FT <= b.x1 + 1e-6; f++) gridLine(f, f * FT, b.y0, f * FT, b.y1);
  for (let f = 0; f * FT <= b.y1 + 1e-6; f++) gridLine(f, b.x0, f * FT, b.x1, f * FT);
  ctx.restore();
  tracePoly(L.polygon);
  ctx.strokeStyle = "#6d7699"; ctx.lineWidth = 2; ctx.stroke();
  if (SCAN) {
    for (const o of SCAN.objects) {
      tracePoly(o.footprint); ctx.fillStyle = "#1d2440"; ctx.fill(); ctx.strokeStyle = "#39436a"; ctx.lineWidth = 1; ctx.stroke();
      const fb = boundsOf(o.footprint);
      label(o.label, toX((fb.x0 + fb.x1) / 2), toY((fb.y0 + fb.y1) / 2) + 4, "11px sans-serif", "#8089aa", "center");
    }
    for (const o of SCAN.openings) {
      tracePoly(o.footprint); ctx.strokeStyle = o.kind === "window" ? "#62d2e0" : "#e6b24c"; ctx.lineWidth = 4; ctx.stroke();
    }
  }
  label(`${ft(b.x1 - b.x0)} ft`, toX((b.x0 + b.x1) / 2), toY(b.y0) - 10, "11px sans-serif", "#8d95b3", "center");
  ctx.save(); ctx.translate(toX(b.x0) - 12, toY((b.y0 + b.y1) / 2)); ctx.rotate(-Math.PI / 2);
  label(`${ft(b.y1 - b.y0)} ft`, 0, 0, "11px sans-serif", "#8d95b3", "center");
  ctx.restore();
  if (editing) L.polygon.forEach((p, i) => { ctx.fillStyle = i === selectedCorner ? "#e6b24c" : "#6d7699"; ctx.fillRect(toX(p[0]) - 5, toY(p[1]) - 5, 10, 10); });
}

function drawCalibration() {
  for (const s of S?.calib?.spots || []) {
    const r = s.kind === "walk" ? 2 : 4;
    ctx.fillStyle = s.kind === "walk" ? "#8f78d0" : "#c9b6ff";
    ctx.fillRect(toX(s.x) - r, toY(s.y) - r, 2 * r, 2 * r);
  }
  ctx.strokeStyle = "#f4a259"; ctx.lineWidth = 1.5;
  for (const t of S?.calib?.tests || []) {
    const X = toX(t.x), Y = toY(t.y);
    ctx.beginPath(); ctx.moveTo(X - 5, Y - 5); ctx.lineTo(X + 5, Y + 5); ctx.moveTo(X + 5, Y - 5); ctx.lineTo(X - 5, Y + 5); ctx.stroke();
  }
}

function drawBoards() {
  if (plan) for (const p of plan.spots) {
    const X = toX(p.x), Y = toY(p.y);
    ctx.strokeStyle = "#e6b24c"; ctx.lineWidth = 2; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.arc(X, Y, 13, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]);
    label(`${p.label} here`, X + 15, Y - 10, "bold 12px sans-serif", "#e6b24c");
  }
  for (const [id, p] of Object.entries(L.receivers)) {
    const node = S?.nodes.find((n) => n.id === id), X = toX(p.x), Y = toY(p.y);
    ctx.beginPath(); ctx.moveTo(X, Y - 9); ctx.lineTo(X + 8, Y + 6); ctx.lineTo(X - 8, Y + 6); ctx.closePath();
    ctx.fillStyle = node ? STATUS_COLOR[node.status] : GREY; ctx.fill();
    label(nodeLabel(id), X + 10, Y + 4, "bold 12px sans-serif", "#e7eaf6");
  }
}

function drawTrail(points, color) {
  ctx.lineWidth = 2.5; ctx.lineCap = "round";
  for (let i = 1; i < points.length; i++) {
    const a0 = points[Math.max(0, i - 2)], a = points[i - 1], q = points[i], solid = q[2] === "estimated";
    ctx.globalAlpha = 0.08 + 0.72 * (i / points.length);
    ctx.strokeStyle = solid ? color : GREY; ctx.setLineDash(solid ? [] : [3, 4]);
    ctx.beginPath();
    ctx.moveTo((a0[0] + a[0]) / 2, (a0[1] + a[1]) / 2);
    ctx.quadraticCurveTo(a[0], a[1], (a[0] + q[0]) / 2, (a[1] + q[1]) / 2);
    ctx.stroke();
  }
  ctx.globalAlpha = 1; ctx.setLineDash([]); ctx.lineCap = "butt";
}

function drawPerson(p, now) {
  const color = colorFor(p.mac), e = p.est, m = shown[p.mac];
  const pts = p.trail.map((q) => [toX(q[1]), toY(q[2]), q[4]]);
  if (m) pts.push([toX(m.x), toY(m.y), e ? e.kind : "stale"]);
  drawTrail(pts, color);
  if (!e || !m) return;
  const X = toX(m.x), Y = toY(m.y), stale = e.kind === "stale", c = stale ? GREY : color, R = Math.max(8, m.unc * view2d.k);
  const fill = ctx.createRadialGradient(X, Y, 0, X, Y, R);
  fill.addColorStop(0, c + "55"); fill.addColorStop(1, c + "08");
  ctx.beginPath(); ctx.arc(X, Y, R, 0, 2 * Math.PI); ctx.fillStyle = fill; ctx.fill();
  ctx.strokeStyle = c + "aa"; ctx.lineWidth = 1; ctx.setLineDash(stale ? [4, 4] : []); ctx.stroke(); ctx.setLineDash([]);
  if (!stale) {
    const phase = (now * 0.7 + (parseInt(p.mac.slice(-2), 16) || 0) / 255) % 1;
    ctx.globalAlpha = 0.6 * (1 - phase); ctx.strokeStyle = c; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(X, Y, 7 + 18 * phase, 0, 2 * Math.PI); ctx.stroke(); ctx.globalAlpha = 1;
  }
  ctx.beginPath(); ctx.arc(X, Y, 7, 0, 2 * Math.PI);
  if (e.kind === "estimated") { ctx.fillStyle = c; ctx.fill(); } else { ctx.strokeStyle = c; ctx.lineWidth = 3; ctx.stroke(); }
  const note = stale ? ` (not heard ${age(p.age)})` : e.kind === "weak" ? " (few boards)" : "";
  label((p.name || p.mac_fmt.slice(-5)) + note, X + 10, Y - 9, "12px sans-serif", "#e7eaf6");
  if (!stale && m.unc > VAGUE_UNC_M) label(placeName(m.x, m.y), X + 10, Y + 5, "11px sans-serif", "#a3abc8");
}

function drawMap() {
  if (!L) return;
  fitView();
  ctx.clearRect(0, 0, view2d.w, view2d.h);
  drawRoom();
  if (!simple) drawCalibration();
  drawBoards();
  const now = performance.now() / 1000;
  for (const p of S?.phones || []) drawPerson(p, now);
  if (placing) label(`click the map where board ${nodeLabel(placing.id)} is`, 10, 18, "13px sans-serif", "#e6b24c");
}
window.addEventListener("resize", drawMap);

// One animation loop for the 2D map: eases markers toward the newest estimate (time constant EASE_S) and redraws.
// The server already smooths positions; this only hides the 4 Hz steps.
const EASE_S = 0.18;
let lastFrame = performance.now();
function frame(t) {
  const dt = Math.min(0.1, (t - lastFrame) / 1000);
  lastFrame = t;
  const k = 1 - Math.exp(-dt / EASE_S), alive = new Set();
  for (const p of S?.phones || []) {
    const e = p.est;
    if (!e) continue;
    alive.add(p.mac);
    const m = shown[p.mac];
    if (!m || Math.hypot(m.x - e.x, m.y - e.y) > 3) shown[p.mac] = { x: e.x, y: e.y, unc: e.unc };
    else { m.x += (e.x - m.x) * k; m.y += (e.y - m.y) * k; m.unc += (e.unc - m.unc) * k; }
  }
  for (const mac of Object.keys(shown)) if (!alive.has(mac)) delete shown[mac];
  if (!(simple && show3d) && !document.hidden) drawMap();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function pick(px, py) {
  const near = (x, y) => Math.hypot(toX(x) - px, toY(y) - py) < 12;
  for (const [id, p] of Object.entries(L.receivers)) if (near(p.x, p.y)) return { kind: "board", id };
  for (let i = 0; i < L.polygon.length; i++) if (near(L.polygon[i][0], L.polygon[i][1])) return { kind: "corner", i };
  return null;
}
// What a click on the map does, by priority. This is the only place that decides it.
function canvasMode() {
  if (placing) return "place";
  if (editing) return "edit";
  return "view";
}
const pointer = (ev) => { const r = canvas.getBoundingClientRect(); return [ev.clientX - r.left, ev.clientY - r.top]; };
canvas.addEventListener("mousedown", (ev) => {
  const [px, py] = pointer(ev), mode = canvasMode();
  if (mode === "place") {
    L.receivers[placing.id] = { x: snapFt(fromX(px)), y: snapFt(fromY(py)) };
    placing = null;
    renderLayoutPanel(true); drawMap();
    return;
  }
  if (mode !== "edit") return;
  dragging = pick(px, py);
  if (dragging && dragging.kind === "corner") selectedCorner = dragging.i;
});
canvas.addEventListener("mousemove", (ev) => {
  if (!dragging) return;
  const [px, py] = pointer(ev), x = snapFt(fromX(px)), y = snapFt(fromY(py));
  if (dragging.kind === "board") L.receivers[dragging.id] = { x, y };
  else L.polygon[dragging.i] = [x, y];
  drawMap(); renderLayoutPanel(true);
});
window.addEventListener("mouseup", () => { dragging = null; });

let layoutSig = "";
function renderLayoutPanel(force) {
  const sig = JSON.stringify([L, S?.nodes.map((n) => n.id), editing]);
  if (!force && sig === layoutSig) return;
  layoutSig = sig;
  const off = editing ? "" : "disabled";
  const ids = new Set([...(S?.nodes || []).map((n) => n.id), ...Object.keys(L.receivers)]);
  $("rxPlace").innerHTML = [...ids].sort((a, b) => nodeLabel(a).localeCompare(nodeLabel(b))).map((id) => {
    const p = L.receivers[id];
    const fields = p
      ? `x <input type="number" step="0.5" data-rx="${esc(id)}" data-k="x" value="${ft(p.x)}" ${off}> y <input type="number" step="0.5" data-rx="${esc(id)}" data-k="y" value="${ft(p.y)}" ${off}> ft`
      : '<span class="warnbox">not placed</span>';
    return `<div class="zrow"><b>${esc(nodeLabel(id))}</b> ${fields} <button data-place="${esc(id)}" ${off}>${p ? "click-place" : "place"}</button></div>`;
  }).join("") || '<span class="muted small">no boards heard yet</span>';
  document.querySelectorAll("[data-place]").forEach((b) => (b.onclick = () => { placing = { id: b.dataset.place }; drawMap(); }));
  document.querySelectorAll("[data-rx]").forEach((inp) => (inp.onchange = () => { L.receivers[inp.dataset.rx][inp.dataset.k] = +inp.value * FT; drawMap(); }));
  const b = boundsOf(L.polygon);
  if (document.activeElement !== $("roomW")) $("roomW").value = ft(b.x1 - b.x0);
  if (document.activeElement !== $("roomH")) $("roomH").value = ft(b.y1 - b.y0);
  for (const id of ["roomW", "roomH", "roomRect", "addVert", "delVert", "saveLayout"]) $(id).disabled = !editing;
}

function startEditing() { editing = true; $("editTog").checked = true; L = clone(S.layout); }
function stopEditing() { editing = false; $("editTog").checked = false; }
const showPlanButtons = (on) => ["planUse", "planHide"].forEach((id) => $(id).classList.toggle("hidden", !on));

// Suggested board spots from /api/plan (server/planner.py), drawn dashed until hidden or used.
$("planBtn").onclick = async () => {
  $("planMsg").textContent = "working it out…";
  plan = await (await fetch("/api/plan")).json();
  if (!plan.spots.length) { $("planMsg").textContent = "Set the room outline first."; plan = null; return; }
  const lines = plan.spots.map((p) => `<b>${esc(p.label)}</b> (${p.role === "AP" ? "hotspot" : "receiver"}): ${ft(p.x)}, ${ft(p.y)} ft, ${esc(p.near)}`);
  $("planMsg").innerHTML = lines.join("<br>") + `<br>Geometry score ${ft(plan.mean_m)} ft` +
    (plan.current ? ` vs ${ft(plan.current.mean_m)} ft where the boards are now` : "") +
    " (lower is better; compares placements, not real accuracy)." + (plan.furniture ? "" : " No matching room scan, so furniture is ignored.");
  showPlanButtons(true);
  drawMap();
};
$("planHide").onclick = () => { plan = null; $("planMsg").textContent = ""; showPlanButtons(false); drawMap(); };
$("planUse").onclick = () => {
  if (!plan) return;
  if (!editing) startEditing();
  for (const p of plan.spots) if (p.id) L.receivers[p.id] = { x: p.x, y: p.y };
  $("planMsg").innerHTML += "<br><b>Moved on the map. Put the boards there, measure where they actually are, correct the numbers, then Save layout.</b>";
  renderLayoutPanel(true); drawMap();
};
$("editTog").onchange = () => { if ($("editTog").checked) startEditing(); else editing = false; renderLayoutPanel(true); drawMap(); };
$("roomRect").onclick = () => {
  const w = +$("roomW").value * FT, h = +$("roomH").value * FT;
  L.polygon = [[0, 0], [w, 0], [w, h], [0, h]];
  renderLayoutPanel(true); drawMap();
};
$("addVert").onclick = () => {
  const n = L.polygon.length;
  let longest = 0, len = -1;
  L.polygon.forEach((p, i) => { const q = L.polygon[(i + 1) % n], d = Math.hypot(p[0] - q[0], p[1] - q[1]); if (d > len) { len = d; longest = i; } });
  const p = L.polygon[longest], q = L.polygon[(longest + 1) % n];
  L.polygon.splice(longest + 1, 0, [snapFt((p[0] + q[0]) / 2), snapFt((p[1] + q[1]) / 2)]);
  drawMap();
};
$("delVert").onclick = () => {
  if (selectedCorner < 0 || L.polygon.length <= 3) return;
  L.polygon.splice(selectedCorner, 1); selectedCorner = -1; drawMap();
};
$("saveLayout").onclick = async () => {
  await api("/api/layout", { layout: L });
  loadScan();
  $("layoutMsg").textContent = "saved " + new Date().toLocaleTimeString();
  stopEditing();
  renderLayoutPanel(true);
};

function accuracyHtml(c) {
  const ev = c.evaluation, tests = c.test_summary, m = c.map;
  let html = "";
  if (ev && ev.loo) html += `<b>Calibrated: a single reading is off by ~${ft(ev.loo.median_m)} ft</b> (80% within ${ft(ev.loo.p80_m)} ft; leave-one-out over ${ev.loo.windows} map points). Live tracking with Motion on does better: Settings → Measure on my recordings gives the real figure.<br>`;
  else html += c.spots.length ? "Need more calibration before accuracy can be estimated.<br>" : "Not calibrated yet: mark spots with I'm here on the phone.<br>";
  if (ev && ev.model_only) html += `Uncalibrated it would be ~${ft(ev.model_only.median_m)} ft.<br>`;
  if (tests.windows) html += `<b>Checks and marks (before each was learned): typical error ${ft(tests.median_m)} ft</b>, 80% within ${ft(tests.p80_m)} ft (${tests.windows} windows).<br>`;
  if (m) {
    const offsets = Object.entries(m.offsets_db).map(([n, v]) => `${esc(nodeLabel(n))} ${v > 0 ? "+" : ""}${v} dB`).join(", ");
    html += `<span class="muted">Radio map: path-loss exponent ${m.eta}, fit error ${m.rms_db ?? "—"} dB, board offsets ${offsets}.</span>`;
  } else html += '<span class="warnbox">Place the boards in Layout first.</span>';
  return html;
}

function spotListHtml(spots) {
  const groups = {}, rows = [];
  for (const s of spots) {
    if (s.kind !== "walk" && s.kind !== "mark") { rows.push(["spot", s]); continue; }
    if (!groups[s.group]) { groups[s.group] = { n: 0, ori: s.orientation, kind: s.kind }; rows.push(["group", s.group]); }
    groups[s.group].n++;
  }
  return rows.map(([kind, v]) => {
    if (kind === "group") {
      const w = groups[v], ori = w.ori === "hand" ? "" : " · " + esc(w.ori.replace("_", " "));
      const what = w.kind === "mark" ? `marked spots · ${w.n}` : `walk · ${w.n} points`;
      return `<div class="sess"><span>${what}${ori}</span><button data-delg="${esc(v)}">✕</button></div>`;
    }
    return `<div class="sess"><span>(${ft(v.x)}, ${ft(v.y)}) ft · ${esc(v.orientation.replace("_", " "))} · ${v.frames} frames${v.warn.length ? ' <span class="warnbox">⚠</span>' : ""}</span><button data-del="${v.i}">✕</button></div>`;
  }).join("") || '<span class="muted">none</span>';
}

let calSig = "";
function renderCalibration() {
  const pc = S.phone_cal || {};
  if (!pc.port) $("phoneCal").innerHTML = '<span class="warnbox">The phone calibration page is off (port in use?).</span>';
  else if (S.laptop_ip) $("phoneCal").innerHTML = `On a phone joined to the demo network open <b>${pc.https ? "https" : "http"}://${esc(S.laptop_ip)}:${pc.port}/</b>` +
    (pc.https ? '<br><span class="muted">The first time, the phone warns the certificate isn\'t trusted: Show Details → visit this website.</span>' : "");
  else $("phoneCal").innerHTML = '<span class="warnbox">Join the laptop to the demo network to use phone calibration.</span>';

  const c = S.calib;
  const sig = JSON.stringify([c.name, c.spots.length, c.tests.length, c.evaluation, c.map]);
  if (sig !== calSig) {
    calSig = sig;
    $("calQual").innerHTML = accuracyHtml(c);
    $("spotList").innerHTML = spotListHtml(c.spots);
    document.querySelectorAll("#spotList [data-del]").forEach((b) => (b.onclick = () => api("/api/spot/delete", { i: +b.dataset.del })));
    document.querySelectorAll("#spotList [data-delg]").forEach((b) => (b.onclick = () => api("/api/spot/delete_group", { group: b.dataset.delg })));
  }
  $("calClear").disabled = !c.spots.length && !(c.tests || []).length;
  const df = c.df || {}, compass = Object.values(df.heading || {}).length;
  $("dfInfo").innerHTML = `Empty room: ${df.empty ? `recorded (${df.links} board-to-board links)` : '<span class="warnbox">not recorded</span>'} · ` +
    `walk data for phone-free tracking: ${df.walk_windows || 0} windows · compass: ${compass ? "learned" : "not learned yet (walk straight between marks with Motion on)"}` +
    (df.stride ? ` · stride ${ft(df.stride)} ft` : "");
  $("emptyGo").disabled = !!S.capture;
}
$("emptyGo").onclick = () => api("/api/empty/start", {});
$("calClear").onclick = () => { if (confirm("Delete every walk, point and check for this room?")) api("/api/calib/clear", {}); };

// Settings: live switches (server/settings.py) plus the lab, which replays recordings with each one flipped.
let setSig = "";
function labTable(lab) {
  const base = lab.rows[0], f = (m) => (m == null ? "—" : ft(m) + " ft");
  const grade = (r, k) => {
    if (r === base || r[k] == null || base[k] == null) return "";
    return r[k] < base[k] - 0.05 ? ' class="good"' : r[k] > base[k] + 0.05 ? ' class="bad"' : "";
  };
  const rows = lab.rows.map((r) => `<tr><td>${esc(r.name)}</td><td${grade(r, "still_med")}>${f(r.still_med)}</td><td${grade(r, "still_p90")}>${f(r.still_p90)}</td>
    <td${grade(r, "check_worst")}>${f(r.check_worst)}</td><td${grade(r, "walk_med")}>${f(r.walk_med)}</td><td${grade(r, "trail_med")}>${f(r.trail_med)}</td>
    <td>${r.jumps == null ? "—" : (100 * r.jumps).toFixed(1) + "%"}</td></tr>`).join("");
  return `<table><tr><th></th><th>standing</th><th>worst 10%</th><th>worst check</th><th>walking</th><th>trail</th><th>dot jumps</th></tr>${rows}</table>
    <span class="muted">Error = distance from where you really were (trail = the drawn path, refined a few seconds later). Green beats your settings, red is worse.
    From ${lab.sessions} recording(s), ${base.n_still} standing and ${base.n_walk} walking samples.</span>`;
}
function renderSettings() {
  const st = S.settings || {}, info = S.settings_info || {}, lab = S.lab || {};
  const sig = JSON.stringify([st, lab.state, lab.finished]);
  if (sig === setSig) return;
  setSig = sig;
  $("setList").innerHTML = Object.keys(info).map((k) => `<label class="setrow"><input type="checkbox" data-set="${esc(k)}" ${st[k] ? "checked" : ""}>
    <span><b>${esc(info[k][0])}</b><br><span class="small muted">${esc(info[k][1])}</span></span></label>`).join("");
  document.querySelectorAll("[data-set]").forEach((b) => (b.onchange = () => api("/api/settings", { key: b.dataset.set, on: b.checked })));
  $("labRun").disabled = lab.state === "running";
  const out = $("labOut");
  if (lab.state === "running") out.textContent = `Replaying ${lab.sessions} recording(s) against ${lab.truths} walks/checks…`;
  else if (lab.state === "empty") out.innerHTML = '<span class="warnbox">No recording covers a mark, walk or check yet. Mark spots on the phone while recording, then try again.</span>';
  else if (lab.state === "error") out.innerHTML = `<span class="warnbox">Lab failed: ${esc(lab.error)}</span>`;
  else if (lab.state === "done") out.innerHTML = labTable(lab);
  else out.textContent = "";
}
$("labRun").onclick = () => api("/api/lab/run", {});

async function loadSessions() {
  const list = await (await fetch("/api/sessions")).json();
  $("sessList").innerHTML = list.map((s) => `<div class="sess"><span class="mono">${esc(s.file)} <span class="muted">${(s.bytes / 1024).toFixed(0)} kB</span></span>
    <button data-rp="${esc(s.file)}">Replay</button><button data-del="${esc(s.file)}">Delete</button></div>`).join("") || "none";
  document.querySelectorAll("#sessList [data-rp]").forEach((b) => (b.onclick = async () => {
    await api("/api/replay/start", { file: b.dataset.rp, speed: +$("rpSpeed").value });
    view = "replay"; connect();
  }));
  document.querySelectorAll("#sessList [data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete " + b.dataset.del + "?")) return;
    await api("/api/session/delete", { file: b.dataset.del });
    loadSessions();
  }));
}
$("recTog").onclick = async () => { await api("/api/record", { on: !S.recording.on }); setTimeout(loadSessions, 300); };
$("rpPause").onclick = () => S && S.replay && api("/api/replay/control", { paused: !S.replay.paused });
$("rpSpeed").onchange = () => api("/api/replay/control", { speed: +$("rpSpeed").value });
$("rpStop").onclick = async () => { await api("/api/replay/stop", {}); view = "live"; connect(); };
$("viewTog").onclick = () => { view = view === "live" ? "replay" : "live"; connect(); };
$("dimTog").onclick = () => { show3d = !show3d; remember("luna3d", show3d ? "1" : "0"); apply3d(); };
$("v3home").onclick = () => v3 && v3.resetView();
$("v3top").onclick = () => v3 && v3.topView();
$("modeTog").onclick = () => { simple = !simple; remember("lunaMode", simple ? "simple" : "debug"); applyMode(); };
document.querySelectorAll(".tabs button").forEach((btn) => (btn.onclick = () => {
  tab = btn.dataset.tab;
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("on", b === btn));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("hidden", t.id !== tab));
  if (tab === "tSess") loadSessions();
  drawMap();
}));

loadScan();
applyMode();
connect();
