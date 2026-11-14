"""Session recordings: loading them, applying their lines to a Tracker, and paced playback."""
from __future__ import annotations

import json
import threading
import time
from bisect import bisect_left
from enum import Enum
from pathlib import Path

from tracker import Tracker

TICK_S = 0.25
MAX_GAP_S = 2.0


class ReplayState(str, Enum):
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"
    STOPPED = "stopped"


_ALLOWED = {
    ReplayState.PLAYING: {ReplayState.PAUSED, ReplayState.STOPPED, ReplayState.FINISHED},
    ReplayState.PAUSED: {ReplayState.PLAYING, ReplayState.STOPPED},
}


def load_session(path: Path):
    header, lines = {}, []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "header":
                header = obj
            elif isinstance(obj.get("rt"), (int, float)):
                lines.append(obj)
    lines.sort(key=lambda o: o["rt"])
    return header, lines


def apply(tr: Tracker, line: dict):
    """Turn one recorded line back into tracker calls. The only place this happens (Replay and lab.py share it).

    Line kinds: {"rt", "meta": {...}}, {"rt", "motion": {"m", "mv", "st", "hd"}} and {"rt", "src", "p": packet}.
    """
    if "meta" in line:
        tr.laptop_ip = (line["meta"] or {}).get("laptop_ip")
    elif "motion" in line:
        mo = line["motion"] or {}
        hd = mo.get("hd")
        tr.set_motion(str(mo.get("m", "")), int(mo.get("mv", 0)), int(mo.get("st", 0)), line["rt"],
                      float(hd) if isinstance(hd, (int, float)) else None)
    else:
        tr.ingest(line.get("p"), line["rt"], (line.get("src", ""), 0))


def feed(tr: Tracker, lines: list, t0: float, t1: float, on_tick=None):
    """Push lines with t0 <= rt <= t1 through tr as fast as possible, ticking every TICK_S; on_tick(t) after each."""
    for line in lines:
        if "meta" in line and line["rt"] <= t0:
            apply(tr, line)
    next_tick = None
    for line in lines[bisect_left(lines, t0, key=lambda o: o["rt"]):]:
        rt = line["rt"]
        if rt > t1:
            break
        if next_tick is None:
            next_tick = rt
        while next_tick <= rt:
            tr.tick(next_tick)
            if on_tick:
                on_tick(next_tick)
            next_tick += TICK_S
        apply(tr, line)


class Replay:
    """Paced playback on a worker thread.

    State machine, every transition through _move() under self.cv: PLAYING <-> PAUSED, PLAYING/PAUSED -> STOPPED,
    PLAYING -> FINISHED. FINISHED and STOPPED are terminal. The worker only ever waits on self.cv, so pause and stop
    act at once. Lines before `start` seconds are fed without delay (seek); long recording gaps are capped at MAX_GAP_S.
    """

    def __init__(self, fname: str, header: dict, lines: list, lock, get_ctx, boards, speed: float, start: float,
                 hidden=()):
        self.file = fname
        self.pkts = lines
        self.lock = lock
        self.tracker = Tracker(get_ctx, boards, hidden)
        self.speed = max(0.01, speed)
        self.t0, self.t1 = lines[0]["rt"], lines[-1]["rt"]
        self.pos = self.t0
        self.seek = self.t0 + max(0.0, start)
        self.header = {"started": header.get("started"), "calibration": header.get("calibration")}
        self.cv = threading.Condition()
        self.state = ReplayState.PLAYING
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _move(self, new: ReplayState) -> bool:
        with self.cv:
            if new not in _ALLOWED.get(self.state, set()):
                return False
            self.state = new
            self.cv.notify_all()
            return True

    def set_paused(self, paused: bool):
        self._move(ReplayState.PAUSED if paused else ReplayState.PLAYING)

    def set_speed(self, speed: float):
        with self.cv:
            self.speed = max(0.01, speed)
            self.cv.notify_all()

    def stop(self):
        self._move(ReplayState.STOPPED)

    def _wait(self, recorded_gap: float) -> bool:
        """Wait recorded_gap recording-seconds at the current speed, honouring pause. False once stopped."""
        left = min(recorded_gap, MAX_GAP_S)
        with self.cv:
            while True:
                if self.state == ReplayState.STOPPED:
                    return False
                if self.state == ReplayState.PAUSED:
                    self.cv.wait()
                    continue
                if left <= 0:
                    return True
                began = time.monotonic()
                self.cv.wait(left / self.speed)
                if self.state == ReplayState.PLAYING:
                    left -= (time.monotonic() - began) * self.speed

    def _run(self):
        tr = self.tracker
        next_tick = prev = self.t0
        for line in self.pkts:
            rt = line["rt"]
            if rt >= self.seek and not self._wait(rt - prev):
                return
            if self.state == ReplayState.STOPPED:
                return
            prev = rt
            with self.lock:
                while next_tick <= rt:
                    tr.tick(next_tick)
                    next_tick += TICK_S
                apply(tr, line)
                self.pos = rt
        with self.lock:
            tr.tick(next_tick)
        self._move(ReplayState.FINISHED)

    def info(self) -> dict:
        state = self.state
        return {"file": self.file, "t0": self.t0, "t1": self.t1, "pos": self.pos, "speed": self.speed,
                "state": state.value, "paused": state == ReplayState.PAUSED, "done": state == ReplayState.FINISHED,
                "header": self.header}
