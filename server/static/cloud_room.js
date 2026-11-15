// 3D room on the frontend laptop, fed by server/room_viewer.py's /api/cloud/room (people from Vultr, no boards).
// view3d.update() takes the dashboard snapshot shape, so the cloud people are dressed as S.phones and the cloud's
// points (A/B/C, which default to the boards' spots) as S.layout.receivers.
const POLL_MS = 1000;
const $ = (id) => document.getElementById(id);
const DEFAULT_POLYGON = [[0, 0], [4, 0], [4, 4], [0, 4]];

// view3d.js calls the dashboard's global personLabel(); cloud pids are already pseudonyms (person-N, body).
window.personLabel = (p) => (p.mac === "body" ? "Unknown person" : p.mac.replace(/^person-/, "Person "));

const PALETTE = ["#8fdcb2", "#7fb4ff", "#f2a65a", "#d78cf0", "#f07a8c", "#e6d36a"];
const colors = {};
const colorFor = (pid) => (colors[pid] ??= PALETTE[Object.keys(colors).length % PALETTE.length]);

let view = null, scan = null, layout = { polygon: DEFAULT_POLYGON, receivers: {} };

function ago(s) {
  if (s == null) return "";
  if (s < 90) return `${Math.round(s)} s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  return `${Math.round(s / 3600)} h ago`;
}

function badge(r) {
  const n = r.people.length, who = n === 1 ? "1 person" : `${n} people`;
  const text = {
    live: `Live · ${who} in the room`,
    stale: r.last_known && n ? `No live data · showing last known (${ago(r.age_s)})`
      : `No live data · last update ${ago(r.age_s)}`,
    no_data: "No room data in the cloud yet",
    offline: "Can't reach the cloud",
    connecting: "Connecting to the cloud…",
  }[r.status] || r.status;
  $("badge").className = r.status;
  $("badgeText").textContent = text;
}

function snapshot(r) {
  const receivers = {}, nodes = [];
  for (const [id, [x, y]] of Object.entries(r.points || {})) {
    receivers[id] = { x, y };
    nodes.push({ id, status: r.status === "live" ? "online" : "stale" });
  }
  const phones = r.people.map((p) => ({
    mac: p.pid,
    est: { x: p.x, y: p.y, unc: 0.35, kind: p.kind },
    trail: p.trail.map(([t, x, y]) => [t, x, y, 0.35, "estimated"]),
  }));
  return { layout: { polygon: layout.polygon, receivers }, nodes, phones };
}

async function tick() {
  try {
    const r = await fetch("/api/cloud/room", { cache: "no-store" }).then((res) => res.json());
    badge(r);
    if (view) {
      const S = snapshot(r);
      await view.setRoom(S.layout, scan);
      view.update(S, colorFor, (id) => "Point " + id);
    }
  } catch (e) {
    badge({ status: "offline", people: [] });
  } finally {
    setTimeout(tick, POLL_MS);
  }
}

function start() {
  try {
    view = window.Luna3D.create($("view3d"));
    view.setVisible(true);
  } catch (e) {
    $("msg").textContent = "The 3D view couldn't start: WebGL is off or unavailable in this browser.";
    $("msg").classList.remove("hidden");
  }
  tick();
}

fetch("/api/scan").then((r) => r.json()).then((j) => {
  if (j && j.polygon) { scan = j; layout = { polygon: j.polygon, receivers: {} }; }
}).catch(() => {}).finally(() => {
  if (window.Luna3D) start(); else window.addEventListener("luna3d-ready", start, { once: true });
});
