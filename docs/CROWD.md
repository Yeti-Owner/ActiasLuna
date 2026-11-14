# Arduino button + simulated decoy crowd

My part (Nick): the Uno Q button and the simulated decoy crowd it switches on.

**What it does for the demo.** Press the button and the dashboard fills with extra phones that wander around the real
person, to make the point that one person is harder to pick out of a busy room. Press again and they vanish.

**What it is not.** The decoys are a *display effect*. They follow the same rule as everything cosmetic in Moth
(ARCHITECTURE.md → "Raw vs display" and the `crowd.py` row under "Display only"):

- Nothing is transmitted over Wi-Fi. No real or fake frames are ever put on the air.
- The real Wi-Fi estimate is never touched, so our accuracy numbers stay honest.
- Every decoy is flagged `sim: true` with an obviously-fake MAC (`02:00:DE:C0:*`), so the UI labels it as simulated.

## Pieces

| File | Runs on | What it does |
|---|---|---|
| `firmware/crowd_button/crowd_button.ino` | Arduino / Uno Q MCU | On each button press, sends `{"t":"crowd","on":0\|1}` over USB serial. |
| `tools/unoq_crowd_relay.py` | laptop | Reads that serial line and forwards it to the server as UDP. |
| `server/crowd.py` | server | Owns the decoys: steps them each frame, puts them in the snapshot. |
| `tools/sim_crowd.py` | laptop | Previews the crowd in the terminal — no Arduino, no server, no radios. |
| `tests/test_crowd.py` | laptop | Unit tests (`py -m unittest discover tests`). |

## Try it now (no hardware at all)

```
py tools/sim_crowd.py --count 10          # watch the decoys move in a text map
py -m unittest discover tests -v          # 9 tests, all pass
```

**Full laptop-side chain, no board needed** — two terminals:

```
# terminal 1: a stand-in server that drives crowd.py the way the real tracker will
py tools/mock_crowd_server.py --port 5055

# terminal 2: pretend to be the button
py tools/send_crowd.py --on 1 --port 5055     # decoys appear and start walking
py tools/send_crowd.py --on 0 --port 5055     # decoys vanish
py tools/send_crowd.py --demo --port 5055     # on, 5 s, off
```

The mock server prints the decoy count and positions updating live. This is the exact integration the team's
server needs (`handle_packet` + `tick` + `snapshot`), so it doubles as a reference for the merge.

## With the Uno Q (serial), no button

The sketch also toggles on a serial command, so you can test the board without a button: open the Arduino **Serial
Monitor** (115200), type any letter and press Enter — a `{"t":"crowd","on":..}` line prints each time.

To forward those to the server, **close the Serial Monitor first** (only one program can hold the COM port), then:

```
py tools/unoq_crowd_relay.py --list                 # find the COM port
py tools/unoq_crowd_relay.py --port COM3 --host 127.0.0.1 --udp 5055
```

Run `mock_crowd_server.py` alongside it to see the toggles land. `py -m pip install pyserial` if the relay asks.

## With the button (once you have one)

Path A, approved by the group leader: a momentary push-button wired **D2 -> GND** (internal pull-up, no resistor).
Each press sends the same crowd line. See `firmware/crowd_button/crowd_button.ino` (`BUTTON_PIN`, `DEBUG`).

## Placing decoys in the real 3D room

`server/room.py` loads the scanned room (the file the 3D view uses) so the decoys and MAC phones stand where a
person actually could — inside the walls and off the furniture — in the same metres/frame as the ESP32 markers.

- `Room.load("data/scan/room.json")` → a room with `size`, `ok(x, y)` (standable?) and `random_point(rng)`.
- `crowd.tick(t, room, center=...)` and `macsim.tick(t, room)` now take a `Room` (or still a plain `(w, d)`
  tuple). Decoys spawn on standable spots and won't walk through walls or sit on furniture.
- The loader is tolerant (several key names for size/walls/furniture) and falls back to a plain rectangle, so it
  won't crash the demo if the file differs.

Preview it against any room file, no hardware:

```
py tools/preview_room.py data/scan/sample/room.json --count 10
```

**To match your exact file:** paste a snippet of your `room.json` (how size, walls and furniture are stored) and
how the ESP32 marker positions are stored. If my loader misreads it, `preview_room.py` prints what it understood.

## MAC decoys + randomization (the "fake addresses" demo)

`server/macsim.py` + `tools/sim_macspoof.py` make the privacy point behind Moth: tracking needs a *stable
address*, and you can take that away two ways. **It is a software simulation only — nothing is transmitted over
the air, no real frames are forged, and the real estimate is never touched.** Every address is an obviously-fake
locally-administered MAC (`02:00:DE:C0:*` decoys, `02:00:5A:*` target), flagged `sim: true`.

- **Decoys:** many fake-MAC phones appear at once, so the target is one address among many.
- **Randomization:** the target changes its own MAC every few seconds, so its trail keeps resetting and can't be
  linked over time — this is exactly what iPhone/Android already do ("Private Wi-Fi Address").

```
py tools/sim_macspoof.py                 # decoys + target rotating its MAC every 4 s
py tools/sim_macspoof.py --rotate 0      # randomization OFF, to compare (steady address = followable)
py tools/sim_macspoof.py --decoys 15 --rotate 3 --seconds 20
```

## Live over-the-air version (real radio, honest)

`firmware/mac_randomizer/mac_randomizer.ino` is the live counterpart: **one real ESP32 joins the demo hotspot and
changes its own Wi-Fi address every few seconds**, exactly like a phone's "Private Wi-Fi Address". The Moth boards
hear what looks like a new device each time, so the person's trail keeps resetting on real hardware — a live
"watch tracking fail" moment to pair with the on-screen simulation.

- Runs on a **spare ESP32 / ESP32-S3** as the "demo phone" (needs the *esp32 by Espressif* board package). Not the
  Uno Q — its Wi-Fi is on the Linux side; there you'd instead rotate the address from Linux, e.g. a small loop
  doing `ip link set wlan0 address <random locally-administered MAC>` then reconnecting.
- Fill in `SSID` / `PASS` to match `config/network.json`. Don't commit real secrets.
- `ROTATE_S` sets how often the address changes. It prints each new identity over serial as
  `{"t":"macrot","n":..,"mac":".."}`.
- Firmware not yet compiled/flashed here (no ESP32 on hand) — expect to tune it on the board, like any first flash.

**Where the line is.** This device randomizes **its own** address — self-randomization, the same thing every modern
phone does for privacy. What is deliberately **not** built is a radio that forges *other* devices' addresses or
fabricates a crowd of fake devices to flood/confuse the receivers: that's an over-the-air spoofing/injection tool,
reusable against other networks and the shared airspace a hotspot can't contain. The simulation already gives that
"crowd" look on screen, so the demo loses nothing.

## For the group leader — questions / decisions

1. **Where to hook in** (needs the server code, which isn't in this branch yet): in the tracker, call
   `crowd.handle_packet(p)` on each UDP packet, `crowd.tick(t, room_size, center=<real person xy>)` each frame, and
   add `crowd.snapshot()` to the snapshot under a `crowd` field (then document it in HANDOFF.md). The UI draws those
   as labelled fakes.
2. **How many decoys / how fast** — `DEFAULT_COUNT`, `SPEED`, `TURN` at the top of `crowd.py`.
3. **Button pin** — `BUTTON_PIN` in the .ino (default pin 2 to GND, internal pull-up). Tell me the real Uno Q button
   pin and I'll set it.
4. **Does the Uno Q present as a plain USB serial port?** If it needs the doc's `unoq_rx.py`/relay path instead of a
   simple serial line, I'll match whatever that uses.

Open question for the team: the original parts list called this a "phone spoofer / make it hard to track someone." I
built the honest, software-only version above rather than anything that actually transmits fake addresses over the
air. If the team wants more than a labelled display effect, let's talk about it together first.
