# ActiasLuna

**Indoor positioning from Wi-Fi signal strength alone. No app, no GPS, no cameras, and nothing past the radio header is ever read.**

ActiasLuna uses a few inexpensive ESP32-S3 boards and a laptop to show where people are in a room, live, on a floor plan and in 3D. It can follow a phone that joins its network, or sense a person carrying no phone at all.

## Goals

- Locate people indoors to about half a metre with commodity Wi-Fi hardware.
- Work with or without the person's cooperation, so the demo shows what Wi-Fi alone can reveal.
- Private and secure by design: measure signal strength, never content.

## Two ways to see someone

### 1. Connected: the phone joins the demo network
A participant joins the clearly named network **`DEMO-DONT JOIN`**. Three boards listen to the frames that phone sends and measure how strong each one is. The laptop turns those readings into a position with an uncertainty disc and a trail. The optional phone page adds step counting and compass heading, which roughly doubles accuracy.

### 2. Not connected: phone-free sensing
No phone, no app, no login. The boards ping each other many times a second, and a human body blocks and disturbs those links. ActiasLuna learns how each link reacts to where someone stands and tracks them from the pattern. *(Experimental: one person at a time.)*

## How it works

1. **Listen.** Each board keeps only the radio header of each frame (sender, network, signal strength) and summarises it every 250 ms.
2. **Map.** A short calibration walk teaches the system how signal strength varies across your room, walls and furniture included.
3. **Track.** A Bayesian grid filter fuses readings over time with motion (phone sensors or the radio itself) to get far more precise than any single reading.
4. **Show.** A live dashboard draws everyone on a floor plan or a scanned 3D model of the room.

## Security and privacy

- **Content is never read.** Payloads are never parsed or stored (and are WPA2-encrypted anyway). Only frame type, sender MAC, network ID and signal strength are used.
- **Strangers are filtered on the board.** Firmware drops every frame not addressed to the demo network before it reaches the laptop.
- **Consent gate.** In connected mode only phones that deliberately joined the demo network are tracked. Leave it and you disappear.
- **Offline.** The demo network has no internet; nothing is uploaded.
- **Locked-down dashboard.** The dashboard listens on `localhost` only. The phone page is HTTPS behind a 4-digit PIN with lockout.
- **No secrets in the repo.** Wi-Fi credentials and certificates are generated from one config file and never committed.
- **Phone-free mode is opt-in.** It sees anyone in the room, so it is an explicit switch. Tell people when it's on.

## Hardware

- 3 × ESP32-S3 boards (one access point, two receivers). More boards, better accuracy.
- A laptop with Python 3.10+ and numpy (`pip install -r requirements.txt`).
- Optional: a LiDAR room scan for furniture and the 3D view.

## Quick start

```bash
cp config/network.example.json config/network.json   # then set a WPA2 password
python tools/gen_secrets.py                            # firmware header + HTTPS cert
cd firmware/luna_node
idf.py -B build_ap -DLUNA_ROLE=AP build                # flash one board with this
idf.py -B build_rx -DLUNA_ROLE=RX build                # and the others with this
cd ../..
python server/luna_server.py                           # dashboard at http://127.0.0.1:8080
```

Join a phone to `DEMO-DONT JOIN`, walk the calibration route from the phone page, and watch yourself move on the map.

No boards yet? Run `python tools/sim_nodes.py` and start the server with
`--targets 127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103`.
