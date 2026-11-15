# ActiasLuna

Indoor positioning from Wi-Fi signal strength, using three ESP32-S3 boards and a laptop. It shows where people are in
a room on a floor plan and in 3D, with no app, no GPS and no cameras. It can follow a phone that joins its network, or
roughly locate one person who carries no phone at all.

This is a hackathon project. It works in a single room, at room scale: expect a dot that is usually within a metre or
two, steadier when the person stands still or the phone page is open. It is not centimetre-level tracking.

## How it works

```
ESP32-S3 boards ──UDP──► laptop: luna_server (tracking, dashboard) ──HTTPS──► Vultr VM (history)
   sniff headers,            grid Bayes filter per phone                         ▲ read-only token
   RSSI + CSI                + device-free body sensing                          │
                                                                FaceTime ⇄ Chert voice agent
```

1. **Listen.** One board runs a WPA2 access point (the demo network); the other two are receivers. All three sniff
   802.11 frames in promiscuous mode and keep only the header fields and the radio's measurements. Every 250 ms each
   board sends the laptop a small JSON summary per sender.
2. **Estimate.** The laptop turns those summaries into a probability map over the room and filters it over time.
3. **Show.** A local dashboard draws each person on the floor plan or on a LiDAR scan of the room, with an uncertainty
   disc and a trail.
4. **Remember.** Pseudonymous positions and events go to a small service on Vultr, which a Chert voice agent queries
   when you FaceTime it.

## The ESP32 side

Firmware is ESP-IDF C (`firmware/luna_node/`), built twice from one source: `LUNA_ROLE=AP` or `RX`.

- **Header-only sniffing.** The promiscuous callback reads frame type, sender MAC, BSSID and RSSI. Payload bytes are
  never parsed or forwarded. Frames that aren't for the demo network are dropped on the board.
- **More frames per phone.** Besides data and management frames it keeps control frames that carry a transmitter
  address (RTS, PS-Poll, BlockAck), since an idle phone sends few data frames.
- **Window summaries.** Per sender and 250 ms window: frame counts by type, RSSI mean, sum of squares, min/max, and a
  1 dB histogram so the laptop gets an upper quartile (`q3`). The upper quartile is less hurt by fades and body
  blocking than the mean.
- **CSI.** When the laptop asks, boards also enable channel state information (64 LLTF subcarriers, HT20). They report
  mean amplitude, frame-to-frame change, and a 13-bin spectral shape.
- **Board-to-board pings.** In device-free mode each board pings its peers 40 times a second, so every directed link
  (A→B, B→A, ...) is measured continuously.
- **Consent gate.** The AP reports its station list once a second. The laptop tracks only phones that have joined the
  demo network.
- **Subscription protocol.** The laptop re-broadcasts a one-line `LUNA_SUB v1 csi= sense= peers= lk=` message. It
  expires after 30 s, so boards fall back to plain sniffing when the laptop goes away.
- Power save is off on every board (`WIFI_PS_NONE`) so the radios never doze through frames.

## The math

All estimators share one grid (`server/radiomap.py`): the room polygon cut into roughly 15 cm cells. A belief is a
flat numpy array over those cells.

**Radio model.** For receiver *n* the expected RSSI at position *x* follows a log-distance path-loss model,

    M_n(x) = T_n − 10 · η_n · log10(d_n(x))

with priors on the reference power *T* and the path-loss exponent *η* (around 2.2).

**Per-board gain from reciprocity.** Boards transmit at equal power, so for any pair the difference
m(i→j) − m(j→i) ≈ g_j − g_i. A small least-squares solve (with Σg = 0) recovers each board's receive gain from the
board-to-board links alone. That gives a reasonable map before anyone has walked the room.

**Unknown phone offset.** Phones differ by 10 dB or more in transmit power, case and grip. For each cell the filter
solves the phone's offset in closed form (MAP under a Gaussian prior) before scoring that cell. Only the *pattern*
across boards counts, not the absolute level. The offset is then learned slowly over time and re-opened if it jumps
(the phone went into a pocket).

**Robust likelihood.** Each reading is scored with a Huber-style cost: quadratic near the prediction, linear in the
tails, plus a small uniform mixture so one bad board can't veto a cell. Readings *weaker* than predicted switch to the
linear cost sooner, because bodies only attenuate. That "shadow guard" stops one blocked board from dragging the fix.

**Holder's body.** The person holding the phone shadows boards behind them, by about 7 dB · max(0, −cos φ), where φ
is the angle between where they face and the board. With the phone page open the compass supplies φ. Without it the
filter assumes the direction-averaged loss.

**Time filter** (`server/phone_filter.py`). A hidden-Markov forward filter on the grid, with motion from the phone's
own sensors:

- *Still:* a standing phone sits in one multipath fade, so re-reading it adds no information. A dwell counts as one
  measurement (its running mean), not one per tick. A dwell breaks if the readings drift by 12 dB, or if they become
  much less likely than at the room's best cell. Then the belief is partly respread.
- *Walking:* pedestrian dead reckoning. Each step shifts the belief one stride along the compass heading (bilinear
  shift on the grid), and spread grows with distance.
- *Unknown:* Gaussian diffusion at walking pace.
- A fixed-lag backward pass (forward-backward smoothing over the last few seconds) cleans the recent trail. It needs
  the adjoints of `diffuse` and `shift`, which are kept as exact transposes.
- Furniture from the room scan becomes a soft prior, so bookshelves are unlikely and sofas only mildly so.

**Device-free sensing** (`server/sensing.py`). With no phone, each board-to-board link is compared against an
empty-room baseline:

- *Attenuation*, modelled with a Fresnel-ellipse kernel exp(−x/w), where x = |p−tx| + |p−rx| − |tx−rx|. Blocking
  needs the body near the line.
- *RSSI variance:* even a standing person sways and breathes. Modelled with a bistatic reflection kernel
  (|tx−rx| / (|p−tx|·|p−rx|))^w, with an unknown per-cell activity level so a very still person isn't read as "far
  from everything". This was the strongest feature.
- *CSI change* and *CSI shape* help a little at 20 MHz.

These give a log-likelihood on the same grid. It tracks one person, or fuses with phone tracking.

**Display smoothing** (`server/display.py`). A 2-D One-Euro filter plus a 5 cm leash keeps the drawn dot calm at rest
and responsive when moving. It is display-only: accuracy numbers always use the raw estimate.

**Board placement** (`server/planner.py`). Scores candidate board spots by expected confusion distance between cells
under path loss with the phone offset removed. This penalises the mirror ambiguity you get with three nearly
collinear boards.

## Privacy

- Nothing past the 802.11 header is read, and payloads are WPA2-encrypted anyway.
- Only phones that joined the demo network are tracked. Leave the network and you disappear.
- Everything leaving the laptop uses pseudonyms (`person-1`, `body`), never MACs, names or IPs.
- **MAC randomisation** (`firmware/mac_randomizer/`): a spare ESP32 rotates its own MAC every few seconds, showing how
  private Wi-Fi addresses break tracking.

## Vultr and Chert

- **Vultr** (`cloud/`): a single-file, standard-library Python service with SQLite on the smallest Ubuntu VM.
  `server/cloud_sync.py` POSTs positions four times a second while someone is present. It also sends events: entered,
  stood by A/B/C (3+ s within 1 m, with hysteresis), moved on, left. A room-count heartbeat goes out every 10 s. When
  the laptop is offline, uploads spool to disk. Separate write-only (INGEST) and read-only (QUERY) tokens. Positions
  are kept for a short time, and events for 30 days. Read endpoints (`/api/now`, `/api/recent`, `/api/where`,
  `/api/summary`, `/api/events`) return JSON plus a plain-English `answer`.
- **Chert** (`chert/`): a LiveKit voice agent (Gemini Live or OpenAI) behind a Chert FaceTime line. It holds only the
  QUERY token and calls the endpoints above as tools. Ask "Did anyone come in?" and it answers, for example:
  "Someone entered two minutes ago, stood by the Amazon Alexa for 10 seconds, then wandered to the fridge and left."
  It knows *where*, never *who*. The caller sees an animated moth as the agent's video.
- **Website** (`web/`): a Vite + React site. Its 3D room panel (`server/room_viewer.py`) reads Vultr, so a second
  laptop with internet only can show the live room.

## Layout

| Path | What |
|---|---|
| `firmware/luna_node/` | ESP32-S3 firmware (AP and receiver roles) |
| `firmware/mac_randomizer/` | Arduino sketch for the MAC-rotation demo |
| `server/` | Laptop server: tracker, estimators, dashboard (`static/`), cloud upload |
| `cloud/` | Vultr service and deploy script |
| `chert/` | FaceTime voice agent |
| `web/` | Project website |
| `tools/` | Board simulator, scan import, secrets generator, preflight check |
| `config/` | Board positions and example configs (real ones are git-ignored) |
| `data/scan/` | LiDAR room scan used by the 3D view |

## Running it

```bash
pip install -r requirements.txt
cp config/network.example.json config/network.json   # demo SSID + WPA2 password
python tools/gen_secrets.py                           # firmware header + HTTPS cert
# flash one board with -DLUNA_ROLE=AP and two with -DLUNA_ROLE=RX (ESP-IDF, firmware/luna_node)
python server/luna_server.py                          # dashboard at http://127.0.0.1:8080
```

No boards? Simulate them:

```bash
python tools/sim_nodes.py --real
python server/luna_server.py --targets 127.0.0.1:6101,127.0.0.1:6102,127.0.0.1:6103 --no-record
```

On Windows, `START_BACKEND.cmd`, `FRONTEND.cmd`, `CHECK.cmd` and `CALL_ACTIAS.cmd` wrap the common steps. The Vultr and
Chert setup is in `cloud/README.md` and `chert/README.md`.
