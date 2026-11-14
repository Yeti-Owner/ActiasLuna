"""Tests for the Moth cloud API (cloud/moth_cloud.py) and the laptop uploader (tools/occupancy_uploader.py)."""
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
sys.path.insert(0, os.path.join(ROOT, "tools"))

import moth_cloud as mc  # noqa: E402
import occupancy_uploader as up  # noqa: E402

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
        self.assertIn("board B", now_out["answer"])
        self.call("/ingest", token=INGEST, body={"room": "lab", "samples": [{"t": time.time(), "in": 0}]})
        _, now_out = self.call("/api/now?room=lab")
        self.assertEqual(now_out["status"], "empty")
        self.assertIn("last person left", now_out["answer"])

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
        self.assertIn("board B", out["answer"])
        _, empty = self.call("/api/summary?room=lab&date=2026-11-12&tz=UTC")
        self.assertIn("no sensor data", empty["answer"])


class UploaderTests(unittest.TestCase):
    STATE = {
        "layout": {"polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
                   "receivers": {"aa": {"x": 0.2, "y": 0.2}, "bb": {"x": 3.8, "y": 2.8}}},
        "nodes": [{"id": "aa", "label": "A"}, {"id": "bb", "label": "B"}],
        "phones": [
            # jammed display position is near A, but the true estimate (raw) is near B
            {"mac": "p1", "age": 1, "est": {"x": 0.5, "y": 0.5, "raw": [3.5, 2.5, 0.4], "kind": "estimated"}},
            {"mac": "p2", "age": 2, "est": {"x": 9, "y": 9, "raw": [9, 9, 0.5], "kind": "weak"}},   # outside room
            {"mac": "p3", "age": 400, "est": {"x": 1, "y": 1, "kind": "stale"}},                    # long gone
        ],
        "decoys": [{"mac": "02:00:DE:C0:00:01", "x": 2, "y": 2, "sim": True}] * 8,
        "body": {"x": 3.6, "y": 2.4},                                                              # same person as p1
    }

    def test_counts_only_real_people_at_true_positions(self):
        s = up.make_sample(self.STATE, t=1_800_000_000)
        self.assertEqual(s["in"], 1)            # p1 only: decoys ignored, body de-duplicated, p3 stale
        self.assertEqual(s["nearby"], 1)        # p2 heard but outside the room
        self.assertEqual(s["zones"], {"B": 1})  # true position near B, not the jammed one near A
        self.assertNotIn("p1", json.dumps(s))   # no device identifiers leave the laptop

    def test_point_in_poly(self):
        poly = [[0, 0], [4, 0], [4, 3], [0, 3]]
        self.assertTrue(up.point_in_poly(1, 1, poly))
        self.assertFalse(up.point_in_poly(5, 1, poly))


if __name__ == "__main__":
    unittest.main()
