"""Packets in; receivers, board-to-board links and per-phone estimates out.

One instance runs live and a fresh one per recording fed back (recording.py). All timing uses the packet receive
time rt, so feeding a recording back is deterministic. Callers hold App.lock.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import deque
from enum import Enum
from typing import Callable, Dict, List, Optional

import radiomap as rmap
import sensing
import settings as cfg
import phone_filter
from display import PointSmoother

STALE_S = 3.0
LOST_S = 60.0
NODE_STALE_S = 2.0
NODE_OFFLINE_S = 6.0
RATE_S = 5.0
SAMPLE_KEEP_S = 120.0
TRAIL_KEEP_S = 600.0
TRAIL_SEND_S = 120.0
TRAIL_STEP_S = 0.5
SPARK_S = 30.0
LINK_ALPHA = 0.02
STATUS_HOLD_S = 1.5
DEMO_UNC_SCALE = 0.75
WALK_MAX_S = 600.0
# Each link keeps a fast level beside its slow baseline. While the fast level is LINK_DROP_DB below the baseline
# (someone standing in the link) the baseline barely moves, so sensing and the map keep the empty-room value.
LINK_FAST_ALPHA = 0.4
LINK_DROP_DB = 1.5
MOTION_REPORT_S = 3.0
# "radio_motion": a moving phone crosses a multipath fade every few cm, so per-packet RSSI at each board jitters; a
# still phone's does not. Moving when the largest per-board sd over the last RADIO_WIN_S exceeds RADIO_ON_DB, still
# again below RADIO_OFF_DB. The jitter expected from averaging few frames (packet sd / sqrt(frames)) is removed first,
# and packets under RADIO_MIN_FRAMES are ignored so a quiet phone gives no verdict (-1) instead of always "moving".
# It backs up phone sensors that miss slow walks.
RADIO_WIN_S = 1.5
RADIO_ON_DB = 2.1
RADIO_OFF_DB = 1.6
RADIO_MIN_PKTS = 4
RADIO_MIN_FRAMES = 6
# Only the radio says moving while the phone says still: a slow walk, or a turn on the spot re-reading one fade, so
# those readings count half. Hand sway alone exceeds RADIO_ON_DB on 44% of standing ticks (vs 88% of walking ones,
# measured on recorded marks and legs), so overruling the phone's "still" needs a clear RADIO_OVERRULE_DB.
RADIO_ONLY_STRENGTH = 0.5
RADIO_OVERRULE_DB = 6.0
STRIDE_M = 0.7
RECEIVERS_S = 2.0
# A window with this many packets from one board (a dwell) uses the median packet so a passer-by's brief shadow
# can't move it; shorter windows use the frame-weighted mean.
MEDIAN_MIN_PKTS = 6
# Experimental CSI motion: moving when recent CSI change is CSI_MOVE_RATIO times this phone's quiet level.
CSI_MOVE_RATIO = 1.8
# Weight of the device-free log-likelihood added to a phone's; only used with exactly one phone present.
FUSE_W = 0.5
# Per-phone motion reports kept so the walk between two "I'm here" marks can be cut out (App._finish_mark).
MOTION_LOG_S = 120.0
# Traced truth: the holder keeps a finger on the phone map where they are (phone.js trace mode, POST /api/trace).
# Kept TRACE_KEEP_S beside the raw fixes and scored against Phone.raw, never the display dot: the live error uses a
# trace point at most TRACE_NOW_S old; the session figures also try the estimate up to TRACE_LAG_MAX_S later, and the
# lag that fits best is how far the estimate trails the walker. Recomputed every TRACE_STATS_S.
TRACE_KEEP_S = 120.0
TRACE_SEND_S = 60.0
TRACE_NOW_S = 1.0
TRACE_LAG_MAX_S = 4.0
TRACE_LAG_STEP_S = 0.25
TRACE_MATCH_S = 0.3
TRACE_STATS_S = 1.0


def fmt_mac(mac: str) -> str:
    raw = str(mac).lower().replace(":", "")
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def _num(value, default=0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def capture_phase(capture: dict, t: float) -> str:
    """The only place the capture lifecycle (countdown -> recording -> done) is derived, from time and the stop flag."""
    if t < capture["t0"]:
        return "countdown"
    if capture.get("stop") or t >= capture["t0"] + capture["duration"]:
        return "done"
    return "recording"


class Node:
    def __init__(self, nid: str):
        self.id = nid
        self.role = "?"
        self.ifm = ""
        self.ip = ""
        self.fw = ""
        self.first = None
        self.last = None
        self.last_seq = None
        self.lost = 0
        self.received = 0
        self.reboots = 0
        self.pkt_times: deque = deque()
        self.frame_times: deque = deque()
        self.aprssi = 0
        self.disc = 0
        self.ovf = 0
        self.heap = 0
        self.csi = 0
        self.up_ms = 0

    def prune(self, t: float):
        while self.pkt_times and t - self.pkt_times[0] > RATE_S:
            self.pkt_times.popleft()
        while self.frame_times and t - self.frame_times[0][0] > RATE_S:
            self.frame_times.popleft()


class Status(str, Enum):
    ESTIMATED = "estimated"
    WEAK = "weak"
    STALE = "stale"


class Phone:
    """Per-phone state. status is None until the first estimate; only Tracker._set_status changes it.

    est is rebuilt from status, raw and display.PointSmoother each tick. trail holds final backward-smoothed points,
    recent the last phone_filter.SMOOTH_LAG_S that are still being refined.
    """

    def __init__(self, mac: str, t: float):
        self.mac = mac
        self.first = t
        self.last = None
        self.samples: deque = deque()
        self.track = phone_filter.Track()
        self.raw: Optional[dict] = None
        self.receivers = 0
        self.status: Optional[Status] = None
        self.pending: Optional[Status] = None
        self.pending_since = 0.0
        self.smoother = PointSmoother()
        self.est: Optional[dict] = None
        self.trail: deque = deque()
        self.recent: List[list] = []
        self.last_trail_t = 0.0
        self.steps_seen: Optional[int] = None
        self.radio_mv = -1
        self.radio_db = 0.0
        self.trace: deque = deque()
        self.raw_hist: deque = deque()
        self.trace_stats: Optional[dict] = None
        self.trace_stats_t = -math.inf


class Tracker:
    def __init__(self, get_ctx: Callable[[], dict], boards: List[dict]):
        self.get_ctx = get_ctx
        self.labels = {str(b.get("base_mac", "")).lower(): b.get("label", "") for b in boards}
        self.nodes: Dict[str, Node] = {}
        self.stations: Dict[str, dict] = {}
        self.sta_t = None
        self.phones: Dict[str, Phone] = {}
        self.links: Dict[tuple, List[float]] = {}
        self.events: deque = deque(maxlen=60)
        self.dropped_unassoc = 0
        self.laptop_ip: Optional[str] = None
        # The laptop's own Wi-Fi MACs. The AP lists a station before DHCP gives it an IP (ip 0.0.0.0), so matching the
        # laptop by laptop_ip alone lets it be tracked as a phone for those first seconds.
        self.laptop_macs: set = set()
        self.now = 0.0
        self.capture: Optional[dict] = None
        self.on_capture_done: Optional[Callable[[dict], None]] = None
        self.packets = 0
        self.bad_packets = 0
        # mac -> {"mv": 0|1, "st": steps, "hd": compass deg or None, "t": rt} from the phone page (/api/motion).
        self.motion_reports: Dict[str, dict] = {}
        # mac -> deque of [rt, steps, compass deg or None, moving]; the last MOTION_LOG_S of reports.
        self.motion_log: Dict[str, deque] = {}
        self.sense = sensing.LinkBuffer()
        self.person = sensing.Person()
        self.body: Optional[dict] = None
        self.body_ll: Optional[List[float]] = None
        self.body_trail: deque = deque()
        # Per-window RSSI statistic used for phones: "r" (mean) or "q3" (upper quartile). Set from settings each tick.
        self.rssi_key = "r"
    def _event(self, t: float, text: str):
        self.events.append([t, text])

    def label(self, nid: str) -> str:
        return self.labels.get(nid) or nid[-4:]

    def node_by_ifm(self) -> Dict[str, str]:
        return {n.ifm: n.id for n in self.nodes.values() if n.ifm}

    def excluded(self) -> set:
        """Never tracked as phones: boards (id and on-air MAC) and the laptop (its MACs, and whatever holds its IP)."""
        out = set(self.laptop_macs)
        for node in self.nodes.values():
            out.add(node.id)
            if node.ifm:
                out.add(node.ifm)
        if self.laptop_ip:
            out.update(m for m, s in self.stations.items() if s.get("ip") == self.laptop_ip)
        return out

    def _link(self, tx: str, rx: str, rssi: float, frames: int):
        """links[(tx, rx)] = [slow baseline dBm, frames seen, fast level dBm]."""
        if frames <= 0:
            return
        link = self.links.get((tx, rx))
        if link is None:
            self.links[(tx, rx)] = [rssi, frames, rssi]
            return
        link[2] += min(1.0, LINK_FAST_ALPHA * frames) * (rssi - link[2])
        blocked = link[2] < link[0] - LINK_DROP_DB
        rate = min(0.5, LINK_ALPHA * frames) * (0.1 if blocked else 1.0)
        link[0] += rate * (rssi - link[0])
        link[1] += frames

    def links_dict(self) -> Dict[tuple, float]:
        return {k: v[0] for k, v in self.links.items() if v[1] >= 10}

    def _track_seq(self, node: Node, pkt: dict, rt: float):
        # A lower seq means a reboot only if uptime also reset; otherwise it's just UDP reordering.
        seq, up = pkt.get("seq"), pkt.get("up")
        if isinstance(seq, int):
            if node.last_seq is None or seq > node.last_seq:
                if node.last_seq is not None and seq > node.last_seq + 1:
                    node.lost += seq - node.last_seq - 1
                node.last_seq = seq
            elif not isinstance(up, int) or up + 2000 < node.up_ms:
                node.reboots += 1
                self._event(rt, f"receiver {self.label(node.id)} restarted")
                node.last_seq = seq
            else:
                node.lost = max(0, node.lost - 1)
        if isinstance(up, int) and (up > node.up_ms or node.last_seq == seq):
            node.up_ms = up

    def ingest(self, pkt: dict, rt: float, src=("", 0)):
        self.now = max(self.now, rt)
        self.packets += 1
        nid = pkt.get("id") if isinstance(pkt, dict) else None
        if not isinstance(nid, str) or not nid or pkt.get("v") != 1:
            self.bad_packets += 1
            return
        kind = pkt.get("t")
        node = self.nodes.get(nid)
        if node is None:
            node = self.nodes[nid] = Node(nid)
            node.first = rt
            self._event(rt, f"receiver {self.label(nid)} online ({pkt.get('role')}, {src[0]})")
        node.role = pkt.get("role", node.role)
        node.ifm = pkt.get("ifm", node.ifm)
        node.ip = src[0]
        if node.last is not None and rt - node.last > NODE_OFFLINE_S:
            self._event(rt, f"receiver {self.label(nid)} back after {rt - node.last:.0f}s gap")
        node.last = rt
        node.received += 1
        self._track_seq(node, pkt, rt)
        node.pkt_times.append(rt)

        if kind == "hi":
            node.fw = pkt.get("fw", "")
            node.csi = pkt.get("csi", 0)
        elif kind == "tel":
            self._telemetry(node, pkt, rt)
        elif kind == "sta":
            self._stations(pkt, rt)
        elif kind == "ev":
            self._event(rt, f"{pkt.get('e')} {fmt_mac(pkt.get('m', '000000000000'))} reason={pkt.get('reason')}")
        node.prune(rt)

    def _telemetry(self, node: Node, pkt: dict, rt: float):
        node.aprssi = pkt.get("aprssi", 0)
        node.disc = pkt.get("disc", 0)
        node.ovf = pkt.get("ovf", 0)
        node.heap = pkt.get("heap", 0)
        node.csi = pkt.get("csi", 0)
        if node.role == "rx" and node.aprssi and pkt.get("part", 0) == 0:
            ap = next((n.id for n in self.nodes.values() if n.role == "ap"), None)
            if ap:
                self._link(ap, node.id, _num(node.aprssi), 1)
        obs = pkt.get("obs")
        self._obs(node.id, node, obs if isinstance(obs, list) else [], rt)

    def _stations(self, pkt: dict, rt: float):
        self.sta_t = rt
        current = {}
        for entry in pkt.get("sta") or []:
            mac = str(entry.get("m", "")).lower()
            if mac:
                current[mac] = {"ip": entry.get("ip"), "r": entry.get("r"), "nm": entry.get("nm", ""), "t": rt}
        for mac in current:
            if mac not in self.stations:
                self._event(rt, f"device joined {fmt_mac(mac)} {current[mac]['ip']}")
        for mac in self.stations:
            if mac not in current:
                self._event(rt, f"device left {fmt_mac(mac)}")
        self.stations = current

    def _obs(self, nid: str, node: Node, obs: list, rt: float):
        skip = self.excluded()
        boards = self.node_by_ifm()
        cap = self.capture
        recording = bool(cap) and capture_phase(cap, rt) == "recording"
        frames = ctrl = 0
        for o in obs:
            if not isinstance(o, dict):
                continue
            mac = str(o.get("m", "")).lower()
            count = int(_num(o.get("n", 0)))
            if count <= 0:
                continue
            rssi = _num(o.get("r", 0))
            if mac in boards:
                tx = boards[mac]
                if tx != nid:
                    cs, cm, sd = o.get("cs"), o.get("cm"), _num(o.get("sd", 0))
                    self._link(tx, nid, rssi, count)
                    self.sense.add(tx, nid, rt, rssi, count, cs, cm, sd)
                    if recording:
                        cap.setdefault("links", []).append([round(rt - cap["t0"], 3), sensing.key(tx, nid), rssi,
                                                            count, cs if isinstance(cs, list) else None, cm, sd])
                continue
            if self.rssi_key != "r" and isinstance(o.get(self.rssi_key), (int, float)):
                rssi = _num(o[self.rssi_key], rssi)
            if mac in skip:
                continue
            # Consent gate: only MACs on the AP's current station list are ever tracked. Never remove this.
            if mac not in self.stations:
                self.dropped_unassoc += count
                continue
            phone = self.phones.get(mac)
            if phone is None:
                phone = self.phones[mac] = Phone(mac, rt)
                self._event(rt, f"tracking {fmt_mac(mac)}")
            phone.last = rt
            frames += count
            ctrl += int(_num(o.get("ct", 0)))
            sd = _num(o.get("sd", 0))
            phone.samples.append((rt, nid, count, rssi, sd, o.get("cm")))
            if recording and cap["phone"] == mac:
                cap["samples"].append([round(rt - cap["t0"], 3), nid, count, rssi, sd])
        node.frame_times.append((rt, frames, ctrl))

    @staticmethod
    def radio_motion(ph: Phone, t: float) -> int:
        """-1 unknown, 0 still, 1 moving from RSSI jitter alone (see RADIO_WIN_S). Hysteresis state lives on ph."""
        per_board: Dict[str, list] = {}
        for s in reversed(ph.samples):
            if t - s[0] > RADIO_WIN_S:
                break
            if s[0] <= t and s[2] >= RADIO_MIN_FRAMES:
                per_board.setdefault(s[1], []).append((s[3], s[4] * s[4] / s[2]))
        jitter = []
        for rows in per_board.values():
            if len(rows) < RADIO_MIN_PKTS:
                continue
            mean = sum(r for r, _ in rows) / len(rows)
            excess = sum((r - mean) ** 2 for r, _ in rows) / len(rows) - sum(se2 for _, se2 in rows) / len(rows)
            jitter.append(math.sqrt(max(0.0, excess)))
        ph.radio_db = max(jitter, default=0.0)
        if len(jitter) < 2:
            ph.radio_mv = -1
        elif max(jitter) > RADIO_ON_DB:
            ph.radio_mv = 1
        elif max(jitter) < RADIO_OFF_DB or ph.radio_mv == -1:
            ph.radio_mv = 0
        return ph.radio_mv

    @staticmethod
    def window(ph: Phone, t0: float, t1: float) -> rmap.Live:
        """This phone's readings with t0 < rt <= t1, one value per board (see MEDIAN_MIN_PKTS)."""
        per_board: Dict[str, list] = {}
        for s in reversed(ph.samples):
            if s[0] <= t0:
                break
            if s[0] <= t1:
                per_board.setdefault(s[1], []).append((s[3], s[2]))
        out = {}
        for board, rows in per_board.items():
            frames = sum(k for _, k in rows)
            if len(rows) >= MEDIAN_MIN_PKTS:
                ordered = sorted(r for r, _ in rows)
                mid = len(ordered) // 2
                value = ordered[mid] if len(ordered) % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])
            else:
                value = sum(r * k for r, k in rows) / frames
            out[board] = (value, frames)
        return out

    def _set_status(self, ph: Phone, proposed: Status, t: float):
        # None -> anything, and anything <-> STALE, apply at once (silence and recovery must show immediately).
        # ESTIMATED <-> WEAK only once proposed for STATUS_HOLD_S, so one board dropping a window doesn't flicker.
        current = ph.status
        if proposed == current:
            ph.pending = None
            return
        if current is not None and Status.STALE not in (proposed, current):
            if ph.pending != proposed:
                ph.pending, ph.pending_since = proposed, t
                return
            if t - ph.pending_since < STATUS_HOLD_S:
                return
        ph.pending = None
        ph.status = proposed
        if proposed == Status.STALE:
            self._event(t, f"{fmt_mac(ph.mac)} not heard for {t - ph.last:.0f}s")
        elif current == Status.STALE:
            self._event(t, f"{fmt_mac(ph.mac)} heard again")

    def set_motion(self, mac: str, mv: int, steps: int, rt: float, hd: Optional[float] = None):
        self.motion_reports[mac] = {"mv": 1 if mv else 0, "st": steps, "hd": hd, "t": rt}
        log = self.motion_log.setdefault(mac, deque())
        log.append([rt, steps, hd, 1 if mv else 0])
        while log and rt - log[0][0] > MOTION_LOG_S:
            log.popleft()
        cap = self.capture
        if cap and cap["kind"] in ("walk", "mark") and cap["phone"] == mac and capture_phase(cap, rt) == "recording":
            # [t, steps, compass deg, moving]: walkcal reconstructs where the walker was; marks drop moving stretches.
            cap.setdefault("motion", []).append([round(rt - cap["t0"], 3), steps, hd, 1 if mv else 0])

    def motion_between(self, mac: str, t0: float, t1: float) -> List[list]:
        """Logged reports with t0 <= rt <= t1 as [rt - t0, steps, compass, moving] (walkcal.leg_fit's shape)."""
        return [[round(r[0] - t0, 3)] + r[1:] for r in self.motion_log.get(mac, ()) if t0 <= r[0] <= t1]

    def samples_between(self, mac: str, t0: float, t1: float) -> List[list]:
        """This phone's readings with t0 <= rt <= t1 as capture samples [rt - t0, board, frames, rssi, sd]."""
        ph = self.phones.get(mac)
        return [[round(s[0] - t0, 3), s[1], s[2], s[3], s[4]] for s in (ph.samples if ph else ()) if t0 <= s[0] <= t1]

    def trace(self, mac: str, x: float, y: float, t: float):
        """Where the holder says they are right now (traced truth, see TRACE_KEEP_S). Recorded, so the lab sees it."""
        ph = self.phones.get(mac)
        if ph is None:
            return
        if ph.trace and t < ph.trace[-1][0]:
            return
        ph.trace.append((t, x, y))
        while t - ph.trace[0][0] > TRACE_KEEP_S:
            ph.trace.popleft()

    def trace_info(self, ph: Phone, t: float) -> Optional[dict]:
        if not ph.trace or t - ph.trace[-1][0] > TRACE_KEEP_S:
            return None
        last = ph.trace[-1]
        now_err = None
        if ph.raw and t - last[0] <= TRACE_NOW_S:
            now_err = math.hypot(ph.raw["x"] - last[1], ph.raw["y"] - last[2])
        if t - ph.trace_stats_t >= TRACE_STATS_S:
            ph.trace_stats_t = t
            ph.trace_stats = self._trace_stats(ph)
        return {"pts": [[round(p[0] - t, 2), round(p[1], 3), round(p[2], 3)] for p in ph.trace
                        if t - p[0] <= TRACE_SEND_S],
                "err": None if now_err is None else round(now_err, 3), **(ph.trace_stats or {})}

    @staticmethod
    def _trace_stats(ph: Phone) -> Optional[dict]:
        """Median error of Phone.raw against the trace, as is and at the estimate lag that fits best."""
        hist = list(ph.raw_hist)
        if len(hist) < 2:
            return None
        times = [h[0] for h in hist]

        def raw_at(t):
            i = bisect_left(times, t)
            best = min((j for j in (i - 1, i) if 0 <= j < len(hist)), key=lambda j: abs(times[j] - t))
            return hist[best] if abs(times[best] - t) <= TRACE_MATCH_S else None

        def median_err(lag):
            errs = []
            for tt, x, y in ph.trace:
                h = raw_at(tt + lag)
                if h:
                    errs.append(math.hypot(h[1] - x, h[2] - y))
            return (sorted(errs)[len(errs) // 2], len(errs)) if errs else (None, 0)

        med, n = median_err(0.0)
        if med is None:
            return None
        lags = [k * TRACE_LAG_STEP_S for k in range(int(TRACE_LAG_MAX_S / TRACE_LAG_STEP_S) + 1)]
        scored = [(median_err(lag)[0], lag) for lag in lags]
        best_err, best_lag = min((e, lag) for e, lag in scored if e is not None)
        return {"med": round(med, 3), "n": n, "lag": best_lag, "med_at_lag": round(best_err, 3)}

    def anchor(self, mac: str, x: float, y: float, t: float):
        """The holder marked where they stand ("mark_anchor" setting). Recorded, so the lab sees it too."""
        ctx = self.get_ctx()
        ph, m = self.phones.get(mac), ctx.get("map")
        if ph and m and cfg.merged(ctx.get("settings"))["mark_anchor"]:
            ph.track.pin(m, x, y, t)

    def fresh_report(self, mac: str) -> Optional[dict]:
        report = self.motion_reports.get(mac)
        if report and self.now - report["t"] < MOTION_REPORT_S:
            return report
        return None

    def motion(self, mac: str, csi: bool = False) -> int:
        """-1 unknown, 0 still, 1 moving: from the phone page's sensors, else CSI when allowed."""
        report = self.fresh_report(mac)
        if report:
            return report["mv"]
        return self._csi_motion(mac) if csi else -1

    def _csi_motion(self, mac: str) -> int:
        ph = self.phones.get(mac)
        if not ph:
            return -1
        values = [(s[0], s[5]) for s in ph.samples if s[5] is not None]
        if len(values) < 20:
            return -1
        quiet = sorted(v for _, v in values)[len(values) // 5]
        recent = sorted(v for t, v in values if self.now - t < 2.0)
        if not recent or quiet <= 0:
            return -1
        return 1 if recent[len(recent) // 2] > CSI_MOVE_RATIO * quiet else 0

    def _facing(self, mac: str, ctx: dict) -> Optional[float]:
        """Holder's facing as a map angle (phone held in front: compass + learned room offset), or None."""
        report = self.fresh_report(mac)
        offset = (ctx.get("heading") or {}).get(mac)
        if not report or report.get("hd") is None or offset is None:
            return None
        return math.radians(report["hd"]) + offset

    def _new_steps(self, ph: Phone, mac: str) -> int:
        """Steps since the previous tick (0 with no fresh report or after the counter restarted)."""
        report = self.fresh_report(mac)
        if not report:
            ph.steps_seen = None
            return 0
        steps = int(report.get("st") or 0)
        prev, ph.steps_seen = ph.steps_seen, steps
        return steps - prev if prev is not None and steps > prev else 0

    def _display(self, ph: Phone, m, demo: bool, t: float):
        x, y, unc = ph.raw["x"], ph.raw["y"], ph.raw["unc"]
        if not demo:
            return x, y, unc
        sx, sy, su = ph.smoother(x, y, unc, t)
        if rmap.point_in_poly(sx, sy, m.poly):
            x, y = sx, sy
        return x, y, max(rmap.FT, DEMO_UNC_SCALE * su)

    def _body_tick(self, t: float, ctx: dict, st: dict):
        """Device-free person from link disturbance; also the body evidence fused into a lone phone's fix."""
        df = ctx.get("df")
        self.body_ll = None
        if not df or not (st["device_free"] or st["fuse_body"]):
            self.body = None
            return
        feats = sensing.features(self.sense.window(t - sensing.WIN_S, t + 1e-6), df.base)
        ll = df.loglik(feats)
        if ll is None:
            self.body = None
            return
        self.body_ll = ll
        if not st["device_free"]:
            return
        raw = self.person.update(df, feats, t)
        if not raw:
            return
        self.body = {"x": raw["x"], "y": raw["y"], "unc": raw["unc"], "present": self.person.present,
                     "moving": self.person.moving, "links": len(df.usable(feats)), "t": t}
        if self.person.present and (not self.body_trail or t - self.body_trail[-1][0] >= TRAIL_STEP_S):
            self.body_trail.append([round(t, 2), round(raw["x"], 3), round(raw["y"], 3), round(raw["unc"], 3), "body"])
        while self.body_trail and t - self.body_trail[0][0] > TRAIL_SEND_S:
            self.body_trail.popleft()

    def _phone_tick(self, mac: str, ph: Phone, t: float, ctx: dict, st: dict, m, fuse) -> bool:
        """Update one phone. Returns False when it has been silent long enough to forget."""
        while ph.samples and t - ph.samples[0][0] > SAMPLE_KEEP_S:
            ph.samples.popleft()
        while ph.trail and t - ph.trail[0][0] > TRAIL_KEEP_S:
            ph.trail.popleft()
        age = t - ph.last if ph.last else 1e9
        if age > LOST_S:
            return False
        if age > STALE_S:
            if ph.status is not None:
                self._set_status(ph, Status.STALE, t)
                ph.est = dict(ph.est, kind=ph.status.value, age=age)
            return True
        if not m or not m.cells:
            return True

        use_motion = st["motion"]
        mv = self.motion(mac, st["csi_motion"]) if use_motion else -1
        facing = self._facing(mac, ctx) if use_motion else None
        steps = self._new_steps(ph, mac) if use_motion else 0
        # The radio may overrule the phone's "still" (phones miss slow walks); it never overrules "moving".
        strength, creep = 1.0, True
        if st["radio_motion"] and mv != 1 and steps == 0:
            radio = self.radio_motion(ph, t)
            if mv == 0 and radio == 1 and ph.radio_db <= RADIO_OVERRULE_DB:
                radio = 0
            if mv == 0 and radio == 1:
                # Can still be sway or a turn on the spot: weaken the dwell, but never creep along the compass on the
                # radio's word alone.
                strength, creep = RADIO_ONLY_STRENGTH, False
            if mv == -1 or radio == 1:
                mv = radio
        extra = [FUSE_W * v for v in fuse] if fuse is not None else None
        args = (m, t, lambda t0, t1: self.window(ph, t0, t1), mv, steps, facing, float(ctx.get("stride") or STRIDE_M),
                st["pdr"], st["shadow_guard"])
        result = ph.track.step(*args, strength=strength, extra=extra, holder_facing=facing, creep=creep)
        ph.receivers = len(m.usable(self.window(ph, t - RECEIVERS_S, t)))
        if result:
            ph.raw = result
            ph.raw_hist.append((t, result["x"], result["y"]))
            while t - ph.raw_hist[0][0] > TRACE_KEEP_S + TRACE_LAG_MAX_S:
                ph.raw_hist.popleft()
        if ph.raw is None:
            return True
        full = ph.receivers >= min(3, len(m.nodes))
        self._set_status(ph, Status.ESTIMATED if full else Status.WEAK, t)
        x, y, unc = self._display(ph, m, st["smoothing"], t)
        raw = ph.raw
        ph.est = {"x": x, "y": y, "unc": unc, "raw": [round(raw["x"], 3), round(raw["y"], 3), round(raw["unc"], 3)],
                  "receivers": ph.receivers, "mode": "calibrated" if m.spots else "uncalibrated",
                  "kind": ph.status.value, "age": age, "t": t,
                  "rings": m.rings(self.window(ph, t - RECEIVERS_S, t), ph.track.off)}
        self._trail(ph, m, t)
        return True

    def tick(self, t: float):
        self.now = max(self.now, t)
        ctx = self.get_ctx()
        st = cfg.merged(ctx.get("settings"))
        self.rssi_key = "q3" if st["robust_rssi"] else "r"
        m = ctx.get("map")
        self._body_tick(t, ctx, st)
        for mac in self.excluded() & self.phones.keys():   # tracked before it was known to be a board or the laptop
            del self.phones[mac]
        fuse = self.body_ll if st["fuse_body"] and len(self.phones) == 1 else None
        for mac in list(self.phones):
            if not self._phone_tick(mac, self.phones[mac], t, ctx, st, m, fuse):
                del self.phones[mac]
        self._capture_tick(t)

    def _trail(self, ph: Phone, m, t: float):
        kind = ph.status.value
        ph.recent = []
        for ts, x, y, unc in ph.track.smoothed(m, ph.last_trail_t, TRAIL_STEP_S):
            point = [round(ts, 2), round(x, 3), round(y, 3), round(unc, 3), kind]
            if ts <= t - phone_filter.SMOOTH_LAG_S + 0.3:
                ph.trail.append(point)
                ph.last_trail_t = ts
            else:
                ph.recent.append(point)

    def start_capture(self, kind: str, phone: str, x: float, y: float, orientation: str, duration: float, t: float):
        """Kinds: "calibrate"/"test"/"mark" stand at (x, y) for duration ("mark" also keeps motion reports so moving
        stretches can be dropped); "walk" runs until stop_walk() and collects marks [t_rel, x, y] from the phone page
        (truth between marks is reconstructed by walkcal)."""
        self.capture = {"kind": kind, "phone": phone.lower(), "x": x, "y": y, "orientation": orientation,
                        "duration": duration, "t0": t, "samples": [], "marks": [], "stop": False}

    def start_walk(self, phone: str, orientation: str, t: float):
        self.start_capture("walk", phone, 0.0, 0.0, orientation, WALK_MAX_S, t)

    def mark(self, x: float, y: float, t: float, detour: bool = False) -> bool:
        """Record a tap. detour=True: the walker didn't go straight from the previous mark; walkcal skips that leg."""
        cap = self.capture
        if not cap or cap["kind"] != "walk" or capture_phase(cap, t) != "recording":
            return False
        cap["marks"].append([round(t - cap["t0"], 3), x, y] + ([1] if detour else []))
        cap["x"], cap["y"] = x, y
        return True

    def stop_walk(self):
        if self.capture and self.capture["kind"] == "walk":
            self.capture["stop"] = True

    def cancel_capture(self):
        self.capture = None

    def _capture_tick(self, t: float):
        cap = self.capture
        if not cap or capture_phase(cap, t) != "done":
            return
        self.capture = None
        per = rmap.per_node(cap["samples"])
        heard = [b for b, v in per.items() if v["n"] >= 20]
        warnings = []
        if len(heard) < len(self.nodes):
            warnings.append(f"only {len(heard)}/{len(self.nodes)} receivers heard >=20 frames - "
                            f"is the phone page on 10/s?")
        cap["per_node"], cap["warnings"], cap["t_end"] = per, warnings, t
        if self.on_capture_done:
            self.on_capture_done(cap)

    def _node_rows(self, t: float) -> List[dict]:
        rows = []
        for n in self.nodes.values():
            n.prune(t)
            age = t - n.last if n.last is not None else None
            if age is not None and age < NODE_STALE_S:
                status = "online"
            elif age is not None and age < NODE_OFFLINE_S:
                status = "stale"
            else:
                status = "offline"
            span = min(RATE_S, max(0.5, t - n.first)) if n.first is not None else RATE_S
            expected = n.received + n.lost
            rows.append({
                "id": n.id, "label": self.label(n.id), "role": n.role, "ip": n.ip, "fw": n.fw,
                "age": age, "status": status, "pkt_rate": round(len(n.pkt_times) / span, 2),
                "frame_rate": round(sum(f[1] for f in n.frame_times) / span, 2),
                "ctrl_rate": round(sum(f[2] for f in n.frame_times) / span, 2), "aprssi": n.aprssi,
                "lost": n.lost, "loss_pct": round(100 * n.lost / expected, 2) if expected else 0, "disc": n.disc,
                "ovf": n.ovf, "heap": n.heap, "csi": n.csi, "reboots": n.reboots,
                "uptime_s": int(n.up_ms or 0) // 1000,
            })
        rows.sort(key=lambda r: r["label"])
        return rows

    def _phone_row(self, mac: str, ph: Phone, t: float) -> dict:
        station = self.stations.get(mac, {})
        per: Dict[str, dict] = {}
        spark = []
        for s in ph.samples:
            ago = t - s[0]
            if ago <= SPARK_S:
                spark.append([round(-ago, 2), s[1], s[3]])
            if ago <= RATE_S:
                acc = per.setdefault(s[1], {"n": 0, "sum": 0.0, "last": 0})
                acc["n"] += s[2]
                acc["sum"] += s[2] * s[3]
                acc["last"] = max(acc["last"], s[0])
        per_out = {board: {"r": round(a["sum"] / a["n"], 1) if a["n"] else None,
                           "rate": round(a["n"] / RATE_S, 1), "age": round(t - a["last"], 1)}
                   for board, a in per.items()}
        return {"mac": mac, "mac_fmt": fmt_mac(mac), "name": station.get("nm", ""), "ip": station.get("ip"),
                "associated": mac in self.stations, "age": round(t - ph.last, 1) if ph.last else None,
                "est": ph.est, "per_node": per_out, "trace": self.trace_info(ph, t),
                "trail": [p for p in ph.trail if t - p[0] <= TRAIL_SEND_S] + ph.recent, "spark": spark}

    def snapshot(self, extra: Optional[dict] = None) -> dict:
        t = self.now
        skip = self.excluded()
        out = {
            "now": t,
            "nodes": self._node_rows(t),
            "phones": [self._phone_row(mac, ph, t) for mac, ph in self.phones.items()],
            "stations": [{"mac": fmt_mac(m), "ip": s["ip"], "r": s["r"], "nm": s["nm"], "infra": m in skip}
                         for m, s in self.stations.items()],
            "links": [{"tx": self.label(a), "rx": self.label(b), "r": round(v[0], 1)}
                      for (a, b), v in sorted(self.links.items()) if v[1] >= 20],
            "sta_age": (t - self.sta_t) if self.sta_t else None,
            "dropped_unassoc": self.dropped_unassoc, "laptop_ip": self.laptop_ip,
            "events": list(self.events)[-25:], "packets": self.packets, "bad_packets": self.bad_packets,
            "body": dict(self.body, trail=list(self.body_trail)) if self.body else None,
        }
        cap = self.capture
        if cap:
            info = {k: cap[k] for k in ("kind", "phone", "x", "y", "orientation", "duration")}
            info.update(elapsed=round(t - cap["t0"], 1), phase=capture_phase(cap, t), marks=len(cap["marks"]),
                        frames=sum(s[2] for s in cap["samples"]))
            out["capture"] = info
        if extra:
            out.update(extra)
        return out
