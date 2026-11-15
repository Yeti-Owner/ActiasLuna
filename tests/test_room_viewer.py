"""room_viewer draws cloud people only while Vultr is fresh, unless --last-known is on."""
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import room_viewer

NOW = 1_800_000_000.0


def fake_cloud(last_t, last_known):
    track = [{"t": last_t - 2, "pid": "person-1", "x": 1.0, "y": 1.0},
             {"t": last_t, "pid": "person-1", "x": 1.2, "y": 1.1},
             {"t": last_t - 60, "pid": "person-2", "x": 3.0, "y": 3.0}]
    track.sort(key=lambda p: p["t"])

    def get(path, **q):
        if path == "/api/now":
            return {"last_report": last_t}
        rows = [p for p in track if q["from"] <= p["t"] < q["to"]]
        return {"positions": rows, "points": {"A": [0, 0]}, "run": "run-x"}

    cloud = room_viewer.Cloud({"url": "http://x", "query_token": "t"}, last_known)
    cloud._get = get
    return cloud


class RoomViewerTest(unittest.TestCase):
    def poll(self, age, last_known):
        cloud = fake_cloud(NOW - age, last_known)
        with patch.object(room_viewer.time, "time", return_value=NOW):
            cloud.poll()
        return cloud.snapshot()

    def test_fresh_upload_shows_people_seen_recently(self):
        v = self.poll(5, last_known=False)
        self.assertEqual(v["status"], "live")
        self.assertEqual([(p["pid"], p["x"], p["kind"]) for p in v["people"]], [("person-1", 1.2, "estimated")])
        self.assertEqual(len(v["people"][0]["trail"]), 2)

    def test_stale_upload_shows_nobody(self):
        v = self.poll(room_viewer.STALE_S + 60, last_known=False)
        self.assertEqual(v["status"], "stale")
        self.assertEqual(v["people"], [])

    def test_last_known_shows_stale_people(self):
        v = self.poll(room_viewer.STALE_S + 60, last_known=True)
        self.assertEqual(v["status"], "stale")
        self.assertEqual([(p["pid"], p["kind"]) for p in v["people"]], [("person-1", "stale")])


if __name__ == "__main__":
    unittest.main()
