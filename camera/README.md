# Laptop camera

Uses the laptop's webcam to sharpen the positions of **phones that joined the demo network**. The camera never adds
people of its own: a detection only counts when it clearly belongs to a phone that is already tracked, and every
other detection is dropped.

## How it works

1. **`moth_camera.py`** (on the laptop) finds people in each frame and works out where their feet touch the image.
   Every 250 ms it sends just those points to the server's UDP port as a `cam` packet, the same way a board sends
   `tel`. Frames are never saved or sent; only `u, v` (foot point as a fraction of the frame), box size and a
   confidence score leave the process.
2. **`homography.py`** maps a foot point to floor metres, using a calibration you make once with `calibrate.py`.
3. **`fusion.py`** (in the server) matches each detection to at most one phone track:
   - **Within reach:** the detection must lie within `GATE_SIGMAS` of the phone's estimate. Unmatched detections
     are dropped.
   - **Unambiguous:** if two phones are about equally close, the detection is skipped rather than risk swapping
     who is who.
   - **Matched:** the detection adds a Gaussian likelihood to that phone's grid. Its shape comes from pushing
     image noise through the homography, so it is stretched along the camera's line of sight and wider far away.
     An outlier floor (`OUTLIER`) means one wrong match can't wreck the filter.

`homography.py` and `fusion.py` need only numpy, so the server keeps its "standard library plus numpy" rule.
OpenCV is needed only by the camera process.

## Setup

```bash
pip install -r camera/requirements.txt     # numpy + OpenCV 4.x (OpenCV 5 removed the HOG detector)
python camera/test_camera.py               # unit tests, no camera needed
```

**Calibrate** (once per camera placement; redo it if the laptop moves):

1. Mark 4 or more spots on the floor at known positions in the layout's coordinates, in metres. Spread them across
   the area the camera sees, with none three in a line. Board positions from the Layout tab make good marks.
2. Run `python camera/calibrate.py`. For each mark, either click it in the image and type its `x y`, or stand on
   it and press **space** (this uses your detected foot point, which matches how tracking measures).
3. With 5 or more points the tool prints a leave-one-out error, an honest accuracy figure. Aim for under 0.3 m.
4. Press **s** to save to `data/camera/calibration.json` (git-ignored with the rest of `data/`).

**Run** alongside the server:

```bash
python camera/moth_camera.py               # sends to 127.0.0.1:<server_udp_port from config/network.json>
python camera/moth_camera.py --preview     # local window showing what it sees; press q to quit
python camera/moth_camera.py --detector motion --port 9000
```

**Detectors:**
- **`hog`** (default) finds people who are standing still, but needs most of the body in view.
- **`motion`** copes with partly visible bodies, but loses anyone who stays still long enough to fade into the
  background.

People whose feet are cut off by the bottom of the frame are skipped, because their floor position would be
wrong.

## Server integration

The server isn't in this repo yet, so these are the hooks to add there. Each step follows the rules in
`ARCHITECTURE.md`.

**1. Create the fusion object** (in `Tracker.__init__`, or wherever calibration is loaded):

```python
import sys; sys.path.insert(0, str(ROOT))          # repo root, so `camera` is importable
from camera.fusion import CameraFusion
self.camera = CameraFusion(calibration.get("cam"))  # or json.load(open("data/camera/calibration.json"))
```

Storing the record under a `cam` key in `data/calibration/<room>.json` keeps it per-room, like the rest of the
calibration.

**2. Ingest packets** (in `Tracker.ingest`):

```python
if p["t"] == "cam":
    self.camera.ingest(p, rt)
    return                       # not a radio receiver: don't add it to receivers or links
```

The camera packets are ordinary UDP packets, so they are recorded as board-packet lines, and replays and the lab
see them with no extra work.

**3. Add a setting** (in `settings.py`): a `camera` switch, off by default, in `DEFAULTS` and `INFO`. The UI and
the lab pick it up automatically, so the lab can score tracking with and without the camera.

**4. Fuse** (in `Tracker.tick`, then `Track.step`):

```python
matches = {}
if ctx["settings"].get("camera"):
    phones = {mac: (tr.x, tr.y, tr.unc) for mac, tr in self.tracks.items()}   # consented phones only
    matches = self.camera.assign(phones, t)
# ... per phone:
track.step(..., camera=matches.get(mac))

# inside Track.step, alongside the radio-map score:
if camera is not None:
    log_post += cam_fusion.loglik(camera, self.grid_x, self.grid_y)
```

This keeps `Track.step`'s `{x, y, unc}` output unchanged. `unc` is expected to be a 1-sigma radius in metres.

**5. Optional, for the UI:** put `self.camera.status(t)` in the snapshot. It holds counts only (`calibrated`,
`seen`, `used`) and never the positions of unmatched people. Add it to `HANDOFF.md`.

## Tuning

All tunables are module-level constants in `fusion.py`, read when used, so the lab can override them:

| Constant | Default | Meaning |
|---|---|---|
| `PIXEL_SIGMA` | 0.015 | Foot-point noise, as a fraction of image height |
| `MIN_SIGMA_M` | 0.15 | Smallest floor noise ever assumed |
| `MAX_SIGMA_M` | 1.5 | Ignore detections vaguer than this (far away, near the horizon) |
| `MIN_SCORE` | 0.3 | Ignore detections below this confidence |
| `MAX_AGE_S` | 0.6 | Detections older than this are stale |
| `GATE_SIGMAS` | 3.0 | How close a detection must be to a phone to match it |
| `AMBIGUITY` | 1.5 | Skip a detection if a second phone is within this factor of the best distance |
| `OUTLIER` | 0.1 | Chance a match is wrong |
| `WEIGHT` | 1.0 | Scale on the camera's influence |

## Limits

- **One viewpoint:** a person hidden behind furniture or another person isn't seen; the radio carries on alone.
- **Matching isn't identity:** someone without a phone standing right next to a phone holder can be matched to them
  for a moment. The gate and ambiguity check keep this short and small, and the camera never shows that person.
- **Fixed camera:** the calibration assumes the laptop doesn't move. Moving the lid changes the view, so
  recalibrate.
