# Laptop camera

Uses the laptop's webcam to sharpen the positions of **phones that joined the demo network**. The camera never adds
people of its own: a detection only counts when it clearly belongs to a phone that is already tracked, and every
other detection is dropped. It helps whenever `data/camera/calibration.json` exists and the camera is sending; the
**Laptop camera helps** setting (dashboard, or the switch on /camera) turns that off while the camera keeps running.

**Set it up:** open http://127.0.0.1:8080/camera (not linked from the dashboard), click where the laptop is, click
where its camera looks, pick its height, press **Save**. The server then starts `luna_camera.py` itself (log in
`data/camera/luna_camera.log`; `--no-camera` stops that) and fine-tunes the lid's tilt from people it sees head to
toe. Redo it if the laptop moves; **Remove camera** turns it off.

## How it works

1. **`luna_camera.py`** (on the laptop) finds people in each frame and works out where their feet touch the image
   and where the top of their head is. Every 250 ms it sends just those points to the server's UDP port as a `cam`
   packet. Frames are never saved or sent; only points (as fractions of the frame), box size and a confidence
   score leave the process. The one exception is the /camera page's live picture: while that page is open the
   process keeps the latest annotated frame in memory and serves it on 127.0.0.1 only (`PREVIEW_PORT`, relayed by
   the server's `/api/camera/preview.jpg`), so it never reaches the demo Wi-Fi. One person often fires several
   detectors (body, head and shoulders, face, moving blob); boxes overlapping by `MERGE_OVERLAP` are sent once.
2. **`homography.py`** maps image points to floor metres. **`pose.py`** builds the mappings from the /camera page's
   two clicks: position and facing from the clicks, height from the preset, a typical webcam field of view
   (`HFOV_DEG`), and a pitch that `refine` fits from whole-body sightings (only the right tilt makes everyone the
   same height at every distance; it lands within about 1.5 degrees in simulation). `calibrate.py` and
   `head_setup.py` below are slower manual alternatives. There are two mappings:
   - **Feet:** a foot point is on the floor, so it maps straight to where the person stands. Used whenever the
     feet are in view.
   - **Bearing (feet hidden):** when a desk or the frame edge hides the feet, only the *direction* from the laptop is
     used (from the box centre, so any visible part of the person works, face or not, sitting or standing). The
     radio decides how far along that line the person is. The line stops at walls (when the laptop is inside the
     room) and at scanned objects at least `OCCLUDER_MIN_H_M` tall; desks and chairs don't hide anyone. Needs the
     camera's floor spot: the /camera page's pose, `camera_xy`, or head-size ranging's `cam`.
   - **Head (fallback, only without the camera's floor spot or with `BEARING` off):** the top of the head maps to
     the floor spot below it. This assumes the person is as tall as whoever calibrated; someone taller or shorter is placed a bit
     too far or too near along the camera's line of sight.
3. **`fusion.py`** (imported by `server/tracker.py`) turns each detection into a floor position with an uncertainty,
   then matches it to at most one phone:
   - **Uncertainty:** a head detection's uncertainty is stretched along the camera's line of sight by the spread of
     people's heights (`HEIGHT_REL_SD`), worked out from where the head's sight line meets the floor. So it pulls
     the dot firmly sideways and only gently in depth.
   - **Radio-only gate:** matching uses a second copy of each phone's filter that the camera never feeds
     (`Phone.radio_track`, run only while a camera is live). Gating on the camera-fed estimate locked wrong matches
     in: a match pulls the phone onto that person and shrinks its uncertainty, so the gate holds it there.
   - **Within reach:** the detection must lie within `GATE_SIGMAS` of the phone, with the phone's uncertainty floored
     at `MIN_TRACK_SD_M` (radio-only uncertainty is overconfident). Unmatched detections are dropped.
   - **Joint:** all phones are matched to all people at once, so a phone whose radio drifted onto another phone's
     holder leaves that holder to their own phone.
   - **Unambiguous:** a phone gets its match only if every assignment pairing it differently scores at least
     `MARGIN` worse. With the holder and someone beside them, or two phones near one person, it gets nothing rather
     than risk pulling it onto a stranger.
   - **Matched:** the detection becomes a per-cell log-likelihood passed to that phone's `Track.step` as `extra`,
     the same input device-free sensing uses. Its shape comes from pushing image noise through the homography, so
     it is stretched along the camera's line of sight and wider far away. An outlier floor (`OUTLIER`) means one
     wrong match can't wreck the filter. Each camera packet is fused once per phone, though it lives several ticks.

`homography.py` and `fusion.py` need only numpy, so the server keeps its "standard library plus numpy" rule.
OpenCV is needed only by the camera process.

## Setup

```bash
pip install -r camera/requirements.txt     # numpy + OpenCV 4.x (OpenCV 5 removed the HOG detector)
python camera/test_camera.py               # unit tests, no camera needed
```

The /camera page (top of this file) is all most setups need. The two manual ways below are for when it isn't
accurate enough; you can do both, and saving either replaces the page's placement. Each is done once per camera placement; redo it if
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

The server starts using it straight away. In the preview, a red dot marks the feet and a blue dot the head top.

Detection runs at most `DETECT_FPS` (8) times a second on `CV_THREADS` (2) threads. The server only takes 4 packets
a second, and uncapped detection takes every core and slows the server on the same laptop.

**Detectors:**
- **`combined`** (default) runs whole bodies, heads and shoulders, front and side-on faces, and anything that moves,
  then keeps only people (`PersonTracks`). A candidate counts once it is found in 2 of the last 3 frames and someone
  vouched for it in the last 10 s: a face, a strong whole body, or head and shoulders that are moving. Moving blobs
  and lone head-and-shoulders boxes only carry a person already vouched for (turned away, side-on), so walls, doors
  and things walked past are dropped. Head and shoulders are what's sent: from a table the feet are rarely in view,
  and a body box's bottom is usually the table edge. The /camera preview shows dropped candidates in grey.
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
| `server/tracker.py` | `cam` packets go to `CameraFusion.ingest` before receiver handling, so the camera never appears as a board. `_camera_tick` matches detections to the radio-only estimates of phones in the ESTIMATED or WEAK state; `_phone_tick` steps `radio_track` and adds the match to `extra`. The `camera` setting is the off switch. |
| `server/luna_server.py` | `camera_calib()` loads `data/camera/calibration.json` (reloaded when it changes) into `ctx["camera"]`. |
| `server/lab.py` | Passes the calibration to replays, so the lab scores tracking with and without the camera. |
| Recordings | `cam` packets are ordinary UDP packets, so they are recorded and replayed with everything else. Detections are projected when used, so a new calibration also applies to old recordings. |

The snapshot gains `"camera": {"calibrated", "head_calibrated", "age", "seen", "seen_by_head", "seen_by_bearing",
"used"}` (or `null` with no camera) and each phone's `est.camera` (`"feet"` / `"range"` / `"bearing"` / `"head"` or
`null`). The dashboard doesn't
render either. Counts only, never positions of people the camera sees. `seen_by_head` counts the people placed by
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
| `MIN_TRACK_SD_M` | 0.8 | Smallest phone uncertainty assumed when matching |
| `MARGIN` | 1.5 | How much better the chosen pairing must score than any rival (sigmas squared) |
| `OUTLIER` | 0.02 | Chance a match is wrong |
| `WEIGHT` | 3.0 | Scale on the camera's influence |
| `HEAD_PIXEL_SIGMA` | 0.02 | Head-top noise, as a fraction of image height |
| `HEIGHT_REL_SD` | 0.1 | Spread of standing head heights, relative to the calibrator's (about 17 cm at 1.7 m) |
| `MIN_HEIGHT_REL` | 0.45 | Anything shorter, relative to the calibrator, is not a person |
| `MAX_HEIGHT_REL` | 1.35 | Anything taller has its bottom hidden: the head is used instead of the feet |
| `BEARING` | True | Feet hidden: use direction only (off: the old head-height guess) |
| `BEARING_SD_DEG` | 3.0 | Direction noise of a bearing |
| `OCCLUDER_MIN_H_M` | 1.8 | Scanned objects this tall cut bearing lines |

The moving-object constants are in `luna_camera.py`: `MOVE_MIN_FRAMES` (3), `MOVE_MIN_SHIFT` (0.02 of the frame
width), `MOVE_HOLD_S` (20 s), `MOVE_BG_LEARN` (0.003, slower means a still person fades later but a moved chair
lingers longer as a false mover).

Simulation (`--real` layout, two phones standing still, walkers crossing), median error camera off -> on:
0 walkers 1.34 -> 0.09 m and 0.34 -> 0.08 m; 2 walkers 1.1 -> 0.40 m and 0.9 -> 0.06 m; 4 walkers 1.19 -> 0.83 m
and 0.92 -> 0.25 m. The simulated camera is ideal, so use the lab on real recordings before raising `WEIGHT` or
lowering `MARGIN`.

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
