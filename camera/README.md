# Laptop camera

Uses the laptop's webcam to sharpen the positions of **phones that joined the demo network**. The camera never adds
people of its own: a detection only counts when it clearly belongs to a phone that is already tracked, and every
other detection is dropped. It is off until you turn on **Laptop camera helps phone** in the dashboard's settings.

## How it works

1. **`luna_camera.py`** (on the laptop) finds people in each frame and works out where their feet touch the image
   and where the top of their head is. Every 250 ms it sends just those points to the server's UDP port as a `cam`
   packet. Frames are never saved or sent; only points (as fractions of the frame), box size and a confidence
   score leave the process.
2. **`homography.py`** maps image points to floor metres, using a calibration you make once with `calibrate.py`.
   There are two mappings:
   - **Feet:** a foot point is on the floor, so it maps straight to where the person stands. Used whenever the
     feet are in view.
   - **Head (fallback):** when a desk or the frame edge hides the feet, the top of the head maps to the floor spot
     below it. This assumes the person is as tall as whoever calibrated; someone taller or shorter is placed a bit
     too far or too near along the camera's line of sight.
3. **`fusion.py`** (imported by `server/tracker.py`) turns each detection into a floor position with an uncertainty,
   then matches it to at most one phone's raw estimate:
   - **Uncertainty:** a head detection's uncertainty is stretched along the camera's line of sight by the spread of
     people's heights (`HEIGHT_REL_SD`), worked out from where the head's sight line meets the floor. So it pulls
     the dot firmly sideways and only gently in depth.
   - **Within reach:** the detection must lie within `GATE_SIGMAS` of the phone. Unmatched detections are dropped.
   - **Unambiguous:** if two phones are about equally close, the detection is skipped rather than risk swapping
     who is who.
   - **Matched:** the detection becomes a per-cell log-likelihood passed to that phone's `Track.step` as `extra`,
     the same input device-free sensing uses. Its shape comes from pushing image noise through the homography, so
     it is stretched along the camera's line of sight and wider far away. An outlier floor (`OUTLIER`) means one
     wrong match can't wreck the filter.

`homography.py` and `fusion.py` need only numpy, so the server keeps its "standard library plus numpy" rule.
OpenCV is needed only by the camera process.

## Setup

```bash
pip install -r camera/requirements.txt     # numpy + OpenCV 4.x (OpenCV 5 removed the HOG detector)
python camera/test_camera.py               # unit tests, no camera needed
```

There are two ways to set the camera up, and you can do both. Each is done once per camera placement; redo it if
the laptop or its lid moves.

**Quick: head-size ranging** (`head_setup.py`, no floor marks). The camera judges distance from how big a head looks
and direction from where it is left to right. It works with feet hidden and for people sitting down.

1. Measure where the laptop camera sits on the floor plan, in metres from the layout's (0, 0) corner.
2. Run `python camera/head_setup.py --cam X Y`.
3. Stand on 2-3 spots at known positions, facing the camera: one left of view, one middle, one right, at different
   distances. Press **space** on each, hold still for a moment, then type the spot's `x y`.
4. It prints which way the camera faces, how wide its view is, and how far off each spot would be placed. Press
   **s** to save. It goes under `head_range` in `data/camera/calibration.json`, next to any floor calibration.

You don't measure the camera's angle or field of view: three spots are enough to work them out (`headrange.solve`).
Accuracy is set by how much people's heads differ in size, about ±12% of the distance along the line of sight
(roughly ±0.3 m at 2.5 m); direction is good to a few degrees. In simulation it cut the median error from 1.28 m
(Wi-Fi alone) to 0.30 m, and behind a desk from 1.60 m to 0.24 m.

**Precise: floor marks** (`calibrate.py`). Feet are on the floor, so this needs no size guess and is the most
accurate when feet are visible. With both set up, the server uses feet when it sees them, then head size, then
head height. It is also needed for calibrating the Wi-Fi map with the camera (below), which only trusts feet.

1. Mark 4 or more spots on the floor at known positions in the layout's coordinates, in metres. Spread them across
   the area the camera sees, with none three in a line. Board positions from the Layout tab, or furniture corners
   from a room scan, make good marks.
2. Run `python camera/calibrate.py`. For each mark, stand on it, upright, and press **space**, then type its
   `x y`. That records your detected feet and head top together, so both mappings are learned at once. Clicking a
   mark in the image instead trains only the feet mapping.
   - **The head mapping needs 4+ marks taken with space**, with your head in the frame. Marks far enough from the
     camera show both; very close ones may show only your feet.
   - **Your height becomes the reference.** Ideally the person calibrating is of average height for the people
     being tracked.
3. With 5 or more points per mapping the tool prints a leave-one-out error, an honest accuracy figure. Aim for
   under 0.3 m on the feet mapping.
4. Press **s** to save to `data/camera/calibration.json` (git-ignored with the rest of `data/`). The server
   re-reads the file when it changes, so there's no need to restart it.

**Run** alongside the server:

```bash
python camera/luna_camera.py               # sends to 127.0.0.1:5007 (or server_udp_port in config/network.json)
python camera/luna_camera.py --preview     # local window showing what it sees; press q to quit
python camera/luna_camera.py --detector hog --port 5008
```

Then turn on **Laptop camera helps phone** in the dashboard. In the preview, a red dot marks the feet and a blue dot
the head top.

Detection runs at most `DETECT_FPS` (8) times a second on `CV_THREADS` (2) threads. The server only takes 4 packets
a second, and uncapped detection takes every core and slows the server on the same laptop.

**Detectors:**
- **`combined`** (default) uses the people detectors below, plus anything that moves. A moving thing that overlaps
  a person detection is the same person; the rest (odd poses, half hidden, side-on) are added.
- **`moving`** treats anything that moves as a candidate person, whatever its shape. Blobs are followed from frame
  to frame:
  - **Counting:** a blob counts once it has been seen for 3 detections in a row and has moved. Lighting flicker,
    screens and curtains rarely do both.
  - **Stopping:** someone who stops is remembered at their last spot for 20 s, and the background learns slowly
    enough that they take about a minute to fade.
  - **Leaving:** something that walks out of the side of the frame is dropped straight away.
- **`hybrid`** finds whole bodies, plus heads and shoulders or faces for anyone whose legs are hidden. It uses
  OpenCV's bundled detectors, so no downloads.
- **`hog`** finds whole bodies only. It needs most of the body in view, but gives feet and head together.
- **`upper`** finds heads and shoulders or faces only, so positions always come from the head mapping.
- **`motion`** is the older moving-blob detector: person-shaped blobs only, with no memory, so anyone who stands
  still fades away.

**Is it a person?** Moving-object detection reports pets and rolling chairs too, so the server checks height. When
both the bottom and the top of something are visible, and the head mapping exists, the two mappings give its height
relative to the person who calibrated:
- **Below `MIN_HEIGHT_REL` (0.45):** not a person, dropped. Sitting people are about 0.7, so they pass.
- **Above `MAX_HEIGHT_REL` (1.35):** its bottom isn't the floor. Usually a desk hides the legs, so the server uses
  the head instead.
- **In between:** a person standing or sitting, placed by their feet.

Without the head mapping, this check can't run, so calibrate with space. Otherwise, a pet near a phone could pull
its dot.

A person is used if either their feet or their head is in view; feet win when both are and the height is
plausible.

## Calibrating the Wi-Fi map with the camera

Once the camera is calibrated, it can do the tapping for the Wi-Fi calibration: it already knows where you are.

1. Run `camera/luna_camera.py` on the laptop. The camera must be calibrated (with space marks).
2. On the phone page, open **More** → **Calibrate with the laptop camera** → **Start**. The button only appears while
   the camera is calibrated and sending.
3. Walk slowly around everywhere the camera can see, phone in hand, stopping for a few seconds now and then. The
   card says whether the camera can see you right now. A couple of minutes is plenty.
4. Press **Stop and save**. The laptop's Log tab says how many calibration points it made.

How it works:
- **Recording:** each time the camera sees exactly one person with their feet in view, their floor position is
  recorded as where the phone is (`Tracker._record_camera_walk`).
- **When nothing is recorded:** if several people are in view, nothing is recorded (it can't tell who has the
  phone), and the phone page asks others to step out. Head-only detections aren't used either, since feet need no
  height guess.
- **Turning positions into points:** `walkcal.camera_truth` resamples the positions every 0.25 s and decides walking
  or standing from the phone's motion reports (or from the camera's own speed). It snaps each standing stretch to its
  median position.
- **Storage:** after that, it is an ordinary walk. `walkcal.walk_spots` makes the calibration points, and they are
  stored with the raw camera positions under `walks`, so they rebuild if `walkcal.VERSION` changes. They are held
  out together in accuracy checks, and the lab uses them as ground truth.
- **Settings:** it doesn't need the **Laptop camera helps phone** setting. Calibration never touches live
  positions.
- **Coverage:** it only covers what the camera sees. Use **I'm here** marks for the rest of the room.

In simulation, a 2-minute camera walk made 41 calibration points and improved Wi-Fi-only tracking from 0.78 m to
0.55 m median error. That is a simulated room, so the real gain will differ; the lab measures it on your
recordings.

## Trying it without a webcam

The simulator can play the camera too. It sees the simulated phones and crowd walkers through a pinhole camera in
one corner. A desk in the far corner hides feet there, and people have different heights, so the head mapping gets
a realistic test:

```bash
python tools/sim_nodes.py --camera --camera-cal data/camera/calibration.json
python server/luna_server.py --targets 127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103
```

`--camera-cal` overwrites the calibration file with the simulated camera's, so leave it off once you've calibrated
a real camera.

## Where it plugs into the server

| File | What it does |
|---|---|
| `server/settings.py` | The `camera` setting, off by default. The dashboard and the lab pick it up automatically. |
| `server/tracker.py` | `cam` packets go to `CameraFusion.ingest` before receiver handling, so the camera never appears as a board. `_camera_tick` matches detections to phones in the ESTIMATED or WEAK state; `_phone_tick` adds the match to `extra`. |
| `server/luna_server.py` | `camera_calib()` loads `data/camera/calibration.json` (reloaded when it changes) into `ctx["camera"]`. |
| `server/lab.py` | Passes the calibration to replays, so the lab scores tracking with and without the camera. |
| Recordings | `cam` packets are ordinary UDP packets, so they are recorded and replayed with everything else. Detections are projected when used, so a new calibration also applies to old recordings. |

The snapshot gains `"camera": {"calibrated", "head_calibrated", "age", "seen", "seen_by_head", "used"}` (or `null`
with no camera): counts only, never positions of people the camera sees. `seen_by_head` counts the people placed by
their head because their feet were hidden.

## Tuning

All tunables are module-level constants in `fusion.py`, read when used. Override them as `camera.fusion.NAME`,
which is the module the tracker imports.

| Constant | Default | Meaning |
|---|---|---|
| `PIXEL_SIGMA` | 0.015 | Foot-point noise, as a fraction of image height |
| `MIN_SIGMA_M` | 0.15 | Smallest floor noise ever assumed |
| `MAX_SIGMA_M` | 1.5 | Ignore detections vaguer than this (far away, near the horizon) |
| `MIN_SCORE` | 0.3 | Ignore detections below this confidence |
| `MAX_AGE_S` | 0.6 | Detections older than this are stale |
| `GATE_SIGMAS` | 3.0 | How close a detection must be to a phone to match it |
| `AMBIGUITY` | 1.5 | Skip a detection if a second phone is within this factor of the best distance |
| `OUTLIER` | 0.05 | Chance a match is wrong |
| `WEIGHT` | 2.0 | Scale on the camera's influence |
| `HEAD_PIXEL_SIGMA` | 0.02 | Head-top noise, as a fraction of image height |
| `HEIGHT_REL_SD` | 0.1 | Spread of standing head heights, relative to the calibrator's (about 17 cm at 1.7 m) |
| `MIN_HEIGHT_REL` | 0.45 | Anything shorter, relative to the calibrator, is not a person |
| `MAX_HEIGHT_REL` | 1.35 | Anything taller has its bottom hidden: the head is used instead of the feet |

The moving-object constants are in `luna_camera.py`: `MOVE_MIN_FRAMES` (3), `MOVE_MIN_SHIFT` (0.02 of the frame
width), `MOVE_HOLD_S` (20 s), `MOVE_BG_LEARN` (0.003, slower means a still person fades later but a moved chair
lingers longer as a false mover).

In simulation, `WEIGHT` 4 and `OUTLIER` 0.02 do even better, but the simulated camera is ideal. Use the lab on
real recordings before raising them.

## Limits

- **One viewpoint:** a person hidden behind furniture or another person isn't seen; the radio carries on alone.
- **Head positions assume standing:** someone sitting has their head about 30% lower, so the head mapping places
  them well out along the line of sight. That usually falls outside the phone's match range and is dropped; when it
  doesn't, the outlier floor limits the pull. Feet, when visible, have no such problem.
- **Moving objects of a person's height pass the check:** an opening door or a coat on a moving chair can look
  like a person. They only matter when near a phone, since unmatched detections are dropped.
- **"Ghosts" after someone leaves:** when a person who stood still for a while walks off, the empty spot looks
  different from the learned background for up to a minute. It never moves, though, so it is only kept if it was
  already being followed.
- **The head mapping needs the head in frame:** a camera close to eye level, or a person very near the camera, can
  put the head above the frame.
- **Needs a radio fix first:** the camera only sharpens a phone that already has an estimate; it can't start one.
- **Matching isn't identity:** someone without a phone standing right next to a phone holder can be matched to them
  for a moment. The gate and ambiguity check keep this short and small, and the camera never shows that person.
- **Fixed camera:** the calibration assumes the laptop doesn't move. Recalibrate if it does.
