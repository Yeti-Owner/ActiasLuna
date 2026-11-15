"""The scanned room supplies startup bounds when no local layout is saved."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "server"))
import luna_server


class InitialLayoutTest(unittest.TestCase):
    def test_scan_bounds_are_used_without_a_saved_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "scan").mkdir()
            polygon = [[0, 0], [3.736, 0], [3.736, 4.77], [0, 4.77]]
            (root / "scan" / "room.json").write_text(json.dumps({"polygon": polygon}))
            with patch.object(luna_server, "DATA", root):
                layout = luna_server.App._initial_layout()
            self.assertEqual(layout["polygon"], polygon)
            self.assertEqual(layout["receivers"], {})

    def test_saved_layout_takes_precedence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "layouts").mkdir()
            saved = {"name": "calibrated-room", "polygon": [[0, 0], [5, 0], [5, 6], [0, 6]],
                     "receivers": {"board-a": {"x": 1, "y": 2}}}
            (root / "layouts" / "current.json").write_text(json.dumps(saved))
            with patch.object(luna_server, "DATA", root):
                layout = luna_server.App._initial_layout()
            self.assertEqual(layout["name"], "calibrated-room")
            self.assertEqual(layout["receivers"], saved["receivers"])

    def test_missing_scan_and_layout_use_default(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(luna_server, "DATA", pathlib.Path(folder)):
                layout = luna_server.App._initial_layout()
            self.assertEqual(layout["polygon"], luna_server.DEFAULT_LAYOUT["polygon"])
