# Moth

**Indoor positioning from Wi-Fi signals alone. No app, no GPS, no cameras, and nothing you say or send ever leaves the radio header.**

Moth uses a few cheap ESP32-S3 boards and a laptop to show where people are in a room, live, on a map and in 3D. It can follow a phone that joins its Wi-Fi, or sense a person who carries no phone at all.

## Goals

- Locate people indoors to within about half a metre using only commodity Wi-Fi hardware.
- Work with or without the person's cooperation, so the demo shows what Wi-Fi alone can reveal.
- Be private and secure by design: measure signal strength, never content.

## Two ways to see someone

### 1. Connected: phone joins the demo Wi-Fi
A participant joins the clearly named network **`DEMO-DONT JOIN`**. Three boards listen to the frames that phone sends and measure how strong each one is. The laptop turns those readings into a position, with an uncertainty circle and a trail. Opening the optional phone page adds steps and compass heading, which roughly doubles accuracy.

### 2. Not connected: device-free sensing
No phone, no app, no Wi-Fi login. The boards ping each other many times a second, and a human body blocks and disturbs those links. Moth learns how each link reacts to where a person stands and tracks them from the pattern. *(Experimental: one person at a time.)*

## How it works

1. **Listen.** Each board captures only the radio header of each frame (who sent it, to which network, how strong it was) and summarises it every 250 ms.
2. **Map.** A short calibration walk teaches Moth how signal strength changes across your room: walls, furniture and all.
3. **Track.** A Bayesian filter combines those readings over time with motion (from the phone's sensors or the radio itself) to pin down a position far more precisely than any single reading could.
4. **Show.** A live dashboard draws everyone on a floor plan or a LiDAR-scanned 3D model of the room.

## Security and privacy

Moth is built to show what Wi-Fi sensing can do, without becoming a surveillance tool itself.

- **Content is never read.** Payloads are never parsed or stored, and they are WPA2-encrypted anyway. Moth only sees frame type, sender MAC, network ID and signal strength.
- **Strangers are filtered out in hardware.** Firmware drops every frame not meant for the demo network before it reaches the laptop. Neighbours and passers-by are never recorded.
- **Consent gate.** In connected mode, only phones that deliberately joined the demo network are tracked. Leave the network and you disappear.
- **Air-gapped by default.** The demo network has no internet. Nothing is uploaded and there is no cloud.
- **Locked-down dashboard.** The dashboard only answers on `localhost`. The phone page runs over HTTPS and needs a 4-digit PIN, with lockout after repeated wrong guesses.
- **No secrets in code.** Wi-Fi credentials and certificates are generated from one config file and never committed.
- **Honest about device-free mode.** Sensing without a phone sees anyone in the room, so Moth makes it an explicit opt-in switch. Tell people when it's on.

## Hardware

- 3 × ESP32-S3 boards (one access point, two receivers). More boards means better accuracy.
- A laptop running Python 3.10+ with numpy.
- Optional: an Arduino Uno Q as a fourth receiver, and a LiDAR room scan for the 3D view.

## Quick start

```bash
python tools/gen_secrets.py          # Wi-Fi secrets + HTTPS cert from config/network.json
powershell tools/fw.ps1 flash AP     # one board as the access point
powershell tools/fw.ps1 flash RX     # the others as receivers
python server/moth_server.py         # dashboard at http://127.0.0.1:8080
```

Join a phone to `DEMO-DONT JOIN`, walk the calibration route from the phone page, and watch yourself move on the map.
# ActiasLuna
