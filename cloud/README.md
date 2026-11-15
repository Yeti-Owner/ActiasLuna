# Moth cloud (Vultr)

Keeps the room's **activity history** so you can ask about it later, for example over FaceTime through the Chert agent (`../chert/`).

```
ESP32 boards → luna_server (server/cloud_sync.py) ──HTTPS──► Vultr VM: moth_cloud.py + SQLite
                                                                   ▲  read-only QUERY token
                                         FaceTime ⇄ Chert agent ───┘  "Did anyone come in?"
```

**What gets uploaded:** four times a second while someone is in the room, their X/Y position in metres (the finger point while they trace on the phone map); events when someone **enters**, **stands by point A/B/C** (3+ s within 1 m), **moves on**, and **leaves**; and a room-count heartbeat every 10 s. People are pseudonyms such as `person-1` or `body`. **No MAC addresses, names or IPs.** Simulated crowd decoys are never uploaded. Positions are kept only for the current and previous run (`MOTH_POSITION_RUNS`, default 2, checked every 10 minutes) and never longer than 7 days; events and counts are kept 30 days.

The points default to the boards' positions and labels (A, B, C). To use other spots, add `"points": {"A": [x, y], ...}` in metres to `config/cloud.json`.

## Day to day
With `config/cloud.json` filled in, uploading is automatic:
```powershell
py server/luna_server.py          # prints "cloud: uploading to https://..." once it reaches Vultr
```
`--no-cloud` turns it off. With the board simulator (`--targets`) it stays off unless you pass `--cloud-config`, so fake people never reach Vultr. If the laptop can't reach Vultr (the demo Wi-Fi has no internet), everything is saved to `data/cloud_spool_<room>.jsonl` and sent once it can. For live updates during a demo, give the laptop a second connection (Ethernet or USB tethering) while its Wi-Fi stays on the demo network. If the boards go quiet, nothing is sent, so the agent says "the sensors weren't reporting" and never "the room was empty".

## No Vultr? Run it on the laptop
```powershell
py tools/local_cloud.py           # http://127.0.0.1:8000, history in data/moth_local.db
```
The first run writes `config/cloud.json` pointing at itself with fresh tokens. To go back to Vultr, put the Vultr URL and tokens back in `config/cloud.json`.

## Set up or update the VM
1. Vultr dashboard: **Deploy +** → **Cloud Compute (Shared CPU)**, Ubuntu 24.04, smallest plan. Copy the **IP**.
2. From the repo folder, in PowerShell:
   ```powershell
   scp cloud\moth_cloud.py cloud\deploy.sh root@YOUR_IP:/root/
   ssh root@YOUR_IP "bash /root/deploy.sh America/New_York"
   ```
   **Re-run these two lines whenever `moth_cloud.py` changes.** Re-running keeps the tokens and data. If the laptop prints "the Vultr server runs the old counts-only moth_cloud.py", that's what it needs.
3. It prints the URL (`https://YOUR-IP-WITH-DASHES.sslip.io`) and two tokens:
   - **INGEST** (write only) goes in `config/cloud.json` as `ingest_token`
   - **QUERY** (read only) goes in `config/cloud.json` as `query_token`. `chert\setup.ps1` copies it into the agent's `.env.local`.

Copy `config\cloud.example.json` to `config\cloud.json` (git-ignored) and fill it in. Check it with `curl https://YOUR-IP-WITH-DASHES.sslip.io/health`, which should print `{"ok": true, "version": 2}`.

## Read endpoints (what the agent calls)
All need `Authorization: Bearer <QUERY token>`. Each returns JSON plus an `answer` sentence to say out loud. Add `&tz=America/Chicago` to change the timezone of spoken times.

| Ask | Call | Example answer |
|---|---|---|
| "Did anyone come in?" | `GET /api/recent?minutes=10` | Someone entered 30 seconds ago, stood by point A for 10 seconds, then wandered to point B and stood there for 5 seconds, then left just now. |
| "Where are they?" | `GET /api/where` | There's 1 person in the room: they're standing by point B (for 20 seconds so far). |
| "Is anyone there?" | `GET /api/now` | Yes. There is 1 person in the room right now, mostly near point B. It's been occupied for 2 minutes. |
| "When today?" | `GET /api/summary?date=2026-11-14` | On Saturday, November 14, people were in the room 2 times: ... |
| "Between 2 and 5 PM?" | `GET /api/events?from=2026-11-14T14:00&to=2026-11-14T17:00` | per-visit story, plus raw `events` |
| raw track | `GET /api/positions?from=&to=&pid=` | `positions: [{t, pid, x, y}]`, `points` |

**Runs:** each `luna_server` start gets a run ID (`run-20261114-153000`, printed at startup). By default every read answers only about the **latest run**, so old tests and sessions stay out. Add `&run=all` for everything, or `&run=<id>` for one; `GET /api/runs` lists them.

`POST /ingest` (INGEST token) is the only write. The QUERY token can't write, so the agent has read-only access.
