# ActiasLuna

**Indoor positioning from Wi-Fi signal strength alone. No app, no GPS, no cameras, and nothing past the radio header is ever read.**

ActiasLuna uses a few inexpensive ESP32-S3 boards and a laptop to show where people are in a room, live, on a floor plan and in 3D. It can follow a phone that joins its network, or sense a person carrying no phone at all.

## Goals

- Locate people indoors to about half a metre with commodity Wi-Fi hardware.
- Work with or without the person's cooperation, so the demo shows what Wi-Fi alone can reveal.
- Private and secure by design: measure signal strength, never content.

## Two ways to see someone

### 1. Connected: the phone joins the demo network
A participant joins the clearly named demo network (**`WATCHING-YOU`**, set in `config/network.json`). Three boards listen to the frames that phone sends and measure how strong each one is. The laptop turns those readings into a position with an uncertainty disc and a trail. The optional phone page adds step counting and compass heading, which roughly doubles accuracy.

### 2. Not connected: phone-free sensing
No phone, no app, no login. The boards ping each other many times a second, and a human body blocks and disturbs those links. ActiasLuna learns how each link reacts to where someone stands and tracks them from the pattern. *(Experimental: one person at a time.)*

## How it works

1. **Listen.** Each board keeps only the radio header of each frame (sender, network, signal strength) and summarises it every 250 ms.
2. **Map.** Stand somewhere, tap that spot on the phone and press *I'm here*. Each mark first measures how far off the map was there, then teaches it, and the walk between marks teaches the compass and stride. A few marks cover a room (a guided walk is the alternative).
3. **Track.** A Bayesian grid filter fuses readings over time with motion (phone sensors or the radio itself) to get far more precise than any single reading.
4. **Show.** A live dashboard draws everyone on a floor plan or a scanned 3D model of the room.

## Security and privacy

- **Content is never read.** Payloads are never parsed or stored (and are WPA2-encrypted anyway). Only frame type, sender MAC, network ID and signal strength are used.
- **Strangers are filtered on the board.** Firmware drops every frame not addressed to the demo network before it reaches the laptop.
- **Consent gate.** In connected mode only phones that deliberately joined the demo network are tracked. Leave it and you disappear.
- **Offline.** The demo network has no internet; nothing is uploaded.
- **Locked-down dashboard.** The dashboard listens on `localhost` only. The phone page is HTTPS and answers only devices on the demo network.
- **No secrets in the repo.** Wi-Fi credentials and certificates are generated from one config file and never committed.
- **Phone-free mode is opt-in.** It sees anyone in the room, so it is an explicit switch. Tell people when it's on.

## Hardware

- 3 × ESP32-S3 boards (one access point, two receivers). More boards, better accuracy.
- A laptop with Python 3.10+ and numpy (`pip install -r requirements.txt`).
- Optional: a LiDAR room scan for furniture and the 3D view.

## Setup (once)

```bash
pip install -r requirements.txt
cp config/network.example.json config/network.json    # set the SSID and a WPA2 password
python tools/gen_secrets.py                            # firmware header + HTTPS cert for the phone page
cd firmware/luna_node
idf.py -B build_ap -DLUNA_ROLE=AP build                # flash one board as the access point
idf.py -B build_rx -DLUNA_ROLE=RX build                # and the others as receivers
cd ../..
cd web && npm install && cd ..                         # only for the web frontend
```

Room scan: on the dashboard open Setup → Room → *Import room scan* (.glb, or a .zip holding one, exported in room mode
so walls, doors and furniture are labelled). It reads the floor outline, doors and furniture, sizes the layout and puts the scan in the 3D view (*Turn 90°* if the corners don't line up;
`python tools/import_scan.py scan.zip --set-layout` does the same). The scan lives in `data/scan/` and is committed;
the rest of `data/` (layout, calibration, recordings) stays on each laptop.

## Run it

1. Power the boards. The AP board creates the demo network (`WATCHING-YOU`, from `config/network.json`).
2. Join the laptop to that network.
3. Start the server: double-click `START_BACKEND.cmd` (opens the dashboard, uploads to Vultr when `config/cloud.json`
   is filled in), or run it yourself. It records every session to `data/sessions/`:

   ```bash
   python server/luna_server.py
   ```

4. Open http://127.0.0.1:8080. The boards turn green within a few seconds.
5. Join your phone to the same network. It shows up on the map as soon as the boards hear it. For step and compass
   data, open the phone page the dashboard shows under Setup → Calibrate (`https://<laptop IP>:8081/`; accept the
   certificate warning once). If the room isn't calibrated yet, mark a few spots there with *I'm here*.

Optional, each in its own terminal while the server runs:

```bash
python tools/unoq_button_adb.py     # Uno Q white button toggles the decoy crowd
cd web && npm run dev               # web frontend at http://localhost:5173/static/dashboard.html
```

No boards? `python tools/sim_nodes.py --real` simulates your boards at their saved spots; then start the server with
`--targets 127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103 --no-record`.

### Second laptop: website with the live room from Vultr

The laptop above (boards, Wi-Fi, Ethernet) uploads to Vultr. A second laptop needs only internet and a copy of
`config/cloud.json` (`url` and `query_token`). Double-click `FRONTEND.cmd`: it builds the website, serves it at
http://127.0.0.1:8090/ and opens it. The 3D room in the timeline shows whoever Vultr last saw; if Vultr has had no
upload for 5 minutes it shows nobody. `FRONTEND.cmd` passes `--last-known` for now, which keeps drawing the last known
positions (greyed, "not heard") so you can check it works without the backend running.

## Decoy crowd and the Uno Q button

The crowd is a display effect: simulated phones that gather around the tracked person. For the demo the dashboard
shows them exactly like real phones everywhere, including the Advanced tables (faked per-board signal, AP station
entries, join/leave events), and while it is on the real people's shown dots and trails jump around too, so nobody is
easy to follow. Decoys are flagged `sim: true` in the server data, nothing is transmitted, and the real estimates,
recordings and accuracy numbers are untouched (`crowd.py`, `App._jam`).

- **Uno Q white button:** plug the Uno Q in by USB and run `python tools/unoq_button_adb.py`. It reads the
  button over adb, so it needs Android platform-tools (`adb`); it finds the Arduino or Android SDK copy on its own,
  or pass `--adb <path>`. Each press toggles the crowd.
- **Without the button:** open http://127.0.0.1:8080/api/crowd (each visit toggles; `?on=1` / `?on=0` sets it), or
  `python tools/send_crowd.py --on 1`.

## MAC-randomization demo

- `python tools/sim_macspoof.py` shows in the terminal how decoys plus a rotating MAC break tracking
  (`--rotate 0` to compare without).
- `firmware/mac_randomizer/mac_randomizer.ino` does it over the air: a spare ESP32 joins the demo network and changes
  its own MAC every few seconds, so the dashboard keeps seeing a "new" phone. Set the SSID and password at the top.

## Checks

```bash
python tools/preview_room.py data/scan/room.json # decoys placed in the scanned room, in the terminal
python tools/sim_crowd.py                        # the crowd alone, in the terminal
```
