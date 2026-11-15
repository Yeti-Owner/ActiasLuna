"""Tests for the Moth cloud API (cloud/moth_cloud.py) and the laptop side (server/cloud_sync.py)."""
import datetime
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cloud"))
sys.path.insert(0, os.path.join(ROOT, "server"))

import moth_cloud as mc  # noqa: E402
import cloud_sync as cs  # noqa: E402

INGEST, QUERY = "ingest-token-0123456789", "query-token-0123456789ab"
UTC = datetime.timezone.utc


class IntervalTests(unittest.TestCase):
    def test_merges_and_splits(self):
        s = [(100, 1, 0, {"B": 1}), (110, 2, 0, {"B": 2}), (120, 0, 0, {}), (130, 1, 0, {"A": 1}),
             (500, 1, 0, {"A": 1})]                        # 130 -> 500 is a reporting gap: two separate periods
        ivs = mc.intervals(s)
        self.assertEqual([(i["start"], i["end"], i["peak"]) for i in ivs], [(100, 110, 2), (130, 130, 1),
                                                                           (500, 500, 1)])
        self.assertEqual(mc._top_zone(ivs[0]["zones"]), "B")

    def test_coverage_ignores_gaps(self):
        s = [(0, 0, 0, {}), (30, 0, 0, {}), (1000, 0, 0, {}), (1030, 0, 0, {})]
        self.assertEqual(mc.coverage(s, 0, 2000), 60)


class StoreTests(unittest.TestCase):
    def test_rejects_bad_input(self):
        st = mc.Store(":memory:")
        now = time.time()
        with self.assertRaises(ValueError):
            st.add("bad room!", [{"t": now, "in": 1}])
        with self.assertRaises(ValueError):
            st.add("r", [{"t": 5, "in": 1}])               # 1970: out of range
        with self.assertRaises(ValueError):
            st.add("r", [{"t": now, "in": -1}])
        self.assertEqual(st.add("r", [{"t": now, "in": 1, "zones": {"B": 1, "bad<zone>": 3}}]), 1)
        self.assertEqual(st.latest("r")[3], {"B": 1})        # junk zone name dropped
        st.close()

    def test_positions_kept_for_newest_runs_only(self):
        st, now = mc.Store(":memory:"), time.time()
        for i, run in enumerate(("run-a", "run-b", "run-c")):
            t = now - 300 + i * 100
            st.add("r", [{"t": t, "in": 1}], run)
            st.add_positions("r", [{"t": t, "pid": "person-1", "x": 1, "y": 1}], run)
            st.add_events("r", [{"t": t, "pid": "person-1", "kind": "enter"}], run)
        st.prune(30, 7, position_runs=2)
        self.assertEqual(len(st.positions("r", 0, now + 1, run="run-a")), 0)
        self.assertEqual(len(st.positions("r", 0, now + 1, run=None)), 2)
        self.assertEqual(len(st.events("r", 0, now + 1, None)), 3)   # history for the agent stays
        st.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.store = mc.Store(":memory:")
        self.srv = mc.make_server("127.0.0.1", 0, self.store, INGEST, QUERY, "UTC")
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.store.close()

    def call(self, path, token=QUERY, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method="GET" if body is None else "POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_auth(self):
        self.assertEqual(self.call("/health", token="nope")[0], 200)
        self.assertEqual(self.call("/api/now?room=r", token="nope")[0], 401)
        self.assertEqual(self.call("/ingest", token=QUERY, body={"room": "r", "samples": []})[0], 401)

    def test_occupied_then_empty(self):
        now = time.time()
        samples = [{"t": now - 60, "in": 0, "nearby": 0}, {"t": now - 40, "in": 2, "nearby": 1, "zones": {"B": 2}},
                   {"t": now - 20, "in": 2, "nearby": 1, "zones": {"B": 2}}, {"t": now - 1, "in": 1, "zones": {"B": 1}}]
        code, out = self.call("/ingest", token=INGEST, body={"room": "lab", "samples": samples})
        self.assertEqual((code, out["stored"]), (200, 4))
        _, now_out = self.call("/api/now?room=lab")
        self.assertEqual(now_out["status"], "occupied")
        self.assertIn("1 person in the room", now_out["answer"])
        self.assertIn("point B", now_out["answer"])
        self.call("/ingest", token=INGEST, body={"room": "lab", "samples": [{"t": time.time(), "in": 0}]})
        _, now_out = self.call("/api/now?room=lab")
        self.assertEqual(now_out["status"], "empty")
        self.assertIn("last person left", now_out["answer"])

    def test_unknown_timezone_is_an_error_not_utc(self):
        self.store.add("lab", [{"t": time.time(), "in": 0}])
        code, out = self.call("/api/now?room=lab&tz=Not/AZone")
        self.assertEqual(code, 400)                            # never silently answer in the wrong timezone
        self.assertIn("timezone", out["error"])

    def test_not_reporting_is_not_empty(self):
        self.store.add("lab", [{"t": time.time() - 600, "in": 0}])
        _, out = self.call("/api/now?room=lab")
        self.assertEqual(out["status"], "not_reporting")       # silence must never be reported as "empty"

    def test_day_summary(self):
        day = datetime.date(2026, 11, 13)
        base = datetime.datetime(2026, 11, 13, 15, 0, tzinfo=UTC).timestamp()
        self.store.add("lab", [{"t": base + i * 20, "in": 1, "zones": {"B": 1}} for i in range(4)]
                       + [{"t": base + 80, "in": 0}])
        _, out = self.call(f"/api/summary?room=lab&date={day.isoformat()}&tz=UTC")
        self.assertEqual(len(out["in_room"]), 1)
        self.assertIn("3:00 PM to 3:01 PM", out["answer"])
        self.assertIn("point B", out["answer"])
        _, empty = self.call("/api/summary?room=lab&date=2026-11-12&tz=UTC")
        self.assertIn("no sensor data", empty["answer"])

    def test_uploader_to_api(self):
        spool_dir = os.path.join(ROOT, "data", "_test_spool")
        os.makedirs(spool_dir, exist_ok=True)
        path = cs.Path(spool_dir) / "spool.jsonl"
        if path.exists():
            path.unlink()
        up = cs.Uploader({"url": self.base, "ingest_token": INGEST, "room": "lab"}, cs.Spool(path), log=lambda m: None)
        up.points = {"A": (0.0, 0.0)}
        now = time.time()
        self.store.add_events("lab", [{"t": now - 50, "pid": "person-9", "kind": "enter", "run": "run-earlier"}])
        up.spool.add("s", [{"t": now - 1, "in": 1, "nearby": 0, "zones": {"A": 1}}], "run-e2e")
        up.spool.add("p", [{"t": now - 1, "pid": "person-1", "x": 0.1, "y": 0.2}], "run-e2e")
        up.spool.add("e", [{"t": now - 40, "pid": "person-1", "kind": "enter"}], "run-e2e")
        self.assertTrue(up.flush_once())
        self.assertEqual(len(up.spool), 0)
        _, recent = self.call("/api/recent?room=lab&minutes=5")
        self.assertEqual(recent["answer"], "Someone entered 40 seconds ago and is still in the room.")   # not run-earlier
        self.assertEqual(recent["run"], "run-e2e")
        _, everything = self.call("/api/recent?room=lab&minutes=5&run=all")
        self.assertEqual(len(everything["visits"]), 2)
        _, where = self.call("/api/where?room=lab")
        self.assertEqual(where["people"][0]["nearest_point"], "A")
        self.assertEqual(self.call("/ingest", token=QUERY, body={"room": "lab", "events": []})[0], 401)
        path.unlink()
        os.rmdir(spool_dir)


class ObserverTests(unittest.TestCase):
    POINTS = {"A": (0.0, 0.0), "B": (3.0, 0.0)}

    def run_walk(self, path, t0=1_800_000_000.0):
        """path: list of (x, y) or None (unseen), one per second. Returns every event and the last output."""
        obs, events, out = cs.Observer(), [], None
        for i, xy in enumerate(path):
            out = obs.step(t0 + i, {} if xy is None else {"person-1": xy}, self.POINTS, 0, True)
            events += out["events"]
        return events, out

    def test_enter_stand_wander_leave(self):
        path = [(1.5, 2.0)] * 3 + [(0.2, 0.1)] * 10 + [(1.5, 1.5)] * 3 + [(2.9, 0.2)] * 6 + [None] * 12
        events, _ = self.run_walk(path)
        kinds = [(e["kind"], e.get("point")) for e in events]
        self.assertEqual(kinds, [("enter", None), ("arrive", "A"), ("depart", "A"), ("arrive", "B"),
                                 ("depart", "B"), ("leave", None)])
        dwell_a = events[2]["dur"]
        self.assertTrue(8 <= dwell_a <= 11, dwell_a)

    def test_blip_is_not_a_visit(self):
        events, _ = self.run_walk([(1.0, 1.0)] + [None] * 12)
        self.assertEqual(events, [])

    def test_not_watching_sends_no_samples(self):
        obs = cs.Observer()
        self.assertEqual(obs.step(1_800_000_000.0, {}, self.POINTS, 0, False)["samples"], [])
        self.assertEqual(len(obs.step(1_800_000_001.0, {}, self.POINTS, 0, True)["samples"]), 1)

    def test_no_identifiers_leave_the_laptop(self):
        class Ph:
            def __init__(self, x, y):
                self.raw, self.est, self.last = {"x": x, "y": y}, {"kind": "estimated", "x": 0, "y": 0}, 100.0

        class Live:
            phones = {"aabbccddeeff": Ph(1.0, 1.0), "112233445566": Ph(9.0, 9.0)}
            body = {"x": 1.2, "y": 1.1, "present": True}
            nodes = {}

        obs = cs.Observer()
        layout = {"polygon": [[0, 0], [4, 0], [4, 3], [0, 3]], "receivers": {}}
        seen, nearby, watching = cs.sightings(Live, layout, obs, 101.0)
        self.assertEqual(seen, {"person-1": (1.0, 1.0)})   # raw position, body merged into its phone
        self.assertEqual(nearby, 1)                         # heard but outside the room
        self.assertFalse(watching)
        self.assertNotIn("aabb", json.dumps(seen))

    def test_fresh_trace_is_sent_as_the_position(self):
        class Ph:
            raw, est, last = {"x": 1.0, "y": 1.0}, {"kind": "estimated"}, 100.0
            trace = [(99.0, 2.0, 2.5), (100.5, 3.0, 2.0)]

        class Live:
            phones = {"aabbccddeeff": Ph()}
            body, nodes = None, {}

        layout = {"polygon": [[0, 0], [4, 0], [4, 3], [0, 3]], "receivers": {}}
        obs = cs.Observer()
        self.assertEqual(cs.sightings(Live, layout, obs, 101.0)[0], {"person-1": (3.0, 2.0)})
        self.assertEqual(cs.sightings(Live, layout, obs, 103.0)[0], {"person-1": (1.0, 1.0)})   # finger lifted
        Ph.est = {"kind": "stale"}
        self.assertEqual(cs.sightings(Live, layout, obs, 101.0)[0], {"person-1": (3.0, 2.0)})   # trace alone counts
        Ph.trace = [(100.5, 4.6, 2.0)]                       # traced just past the wall still counts
        self.assertEqual(cs.sightings(Live, layout, obs, 101.0)[0], {"person-1": (4.6, 2.0)})
        Ph.trace = [(100.5, 9.0, 2.0)]
        self.assertEqual(cs.sightings(Live, layout, obs, 101.0)[0], {})


class NarrativeTests(unittest.TestCase):
    def test_recent_story(self):
        st, now = mc.Store(":memory:"), time.time()
        st.add("r", [{"t": now - 2, "in": 0}])
        st.add_events("r", [
            {"t": now - 30, "pid": "person-1", "kind": "enter"},
            {"t": now - 28, "pid": "person-1", "kind": "arrive", "point": "A"},
            {"t": now - 18, "pid": "person-1", "kind": "depart", "point": "A", "dur": 10},
            {"t": now - 14, "pid": "person-1", "kind": "arrive", "point": "B"},
            {"t": now - 9, "pid": "person-1", "kind": "depart", "point": "B", "dur": 5},
            {"t": now - 5, "pid": "person-1", "kind": "leave"}])
        out = mc.describe_activity(st, "r", now - 600, now + 1, UTC, now)
        self.assertEqual(out["answer"], "Someone entered 30 seconds ago, stood by point A for 10 seconds, then "
                                        "wandered to point B and stood there for 5 seconds, then left just now.")
        st.close()

    def test_ongoing_and_quiet(self):
        st, now = mc.Store(":memory:"), time.time()
        st.add("r", [{"t": now - 1, "in": 1, "zones": {"B": 1}}])
        self.assertIn("Nobody has been in the room", mc.describe_activity(st, "r", now - 600, now + 1, UTC,
                                                                          now)["answer"])
        st.add_events("r", [{"t": now - 70, "pid": "body", "kind": "enter"},
                            {"t": now - 20, "pid": "body", "kind": "arrive", "point": "B"}])
        st.add_positions("r", [{"t": now - 1, "pid": "body", "x": 3.0, "y": 0.5}])
        st.set_points("r", {"A": [0, 0], "B": [3, 0]})
        self.assertEqual(mc.describe_activity(st, "r", now - 600, now + 1, UTC, now)["answer"],
                         "Someone entered 1 minute ago and is standing by point B now.")
        where = mc.where_now(st, "r", UTC, now)
        self.assertIn("standing by point B", where["answer"])
        self.assertEqual(where["people"][0]["x"], 3.0)
        st.close()

    def test_old_runs_are_left_out(self):
        st, now = mc.Store(":memory:"), time.time()
        st.add("r", [{"t": now - 300, "in": 1, "run": "run-old"}])
        st.add_events("r", [{"t": now - 300, "pid": "person-1", "kind": "enter", "run": "run-old"},
                            {"t": now - 290, "pid": "person-1", "kind": "leave", "run": "run-old"}])
        st.add("r", [{"t": now - 200, "in": 1}])                                  # untagged (old uploader)
        st.add_events("r", [{"t": now - 200, "pid": "person-1", "kind": "enter"}])
        st.add("r", [{"t": now - 1, "in": 0}], run="run-new")
        self.assertEqual(st.latest_run("r"), "run-new")
        quiet = mc.describe_activity(st, "r", now - 600, now + 1, UTC, now, "run-new")["answer"]
        self.assertIn("Nobody has been in the room", quiet)
        self.assertEqual(len(mc.describe_activity(st, "r", now - 600, now + 1, UTC, now, None)["visits"]), 2)
        st.close()

    def test_upgrades_a_db_without_runs(self):
        import sqlite3
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE samples (room TEXT NOT NULL, t REAL NOT NULL, n_in INTEGER NOT NULL, "
                   "n_near INTEGER NOT NULL, zones TEXT NOT NULL, PRIMARY KEY (room, t))")
        db.execute("INSERT INTO samples VALUES ('r', ?, 1, 0, '{}')", (time.time() - 5,))
        db.commit()
        db.close()
        st = mc.Store(path)
        self.assertIsNone(st.latest_run("r"))
        self.assertEqual(st.latest("r", "")[1], 1)
        st.add("r", [{"t": time.time(), "in": 0}], run="run-1")
        self.assertEqual(st.latest_run("r"), "run-1")
        st.close()

    def test_silence_is_flagged(self):
        st, now = mc.Store(":memory:"), time.time()
        st.add("r", [{"t": now - 900, "in": 0}])
        self.assertIn("haven't reported", mc.describe_activity(st, "r", now - 600, now + 1, UTC, now)["answer"])
        st.close()


if __name__ == "__main__":
    unittest.main()
