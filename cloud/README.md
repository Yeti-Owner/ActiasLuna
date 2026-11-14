# Moth cloud (Vultr)

Stores the room's **occupancy history**, meaning when people were in or near the monitored room, so you can ask about it later, for example over FaceTime through the Chert agent.

```
ESP32 boards → laptop dashboard ──tools/occupancy_uploader.py──► Vultr VM: moth_cloud.py + SQLite
                                                                        ▲
                                              FaceTime ⇄ Chert agent ───┘  "When was someone in the room today?"
```

**What gets stored:** per moment, how many people were *in* the room, how many other devices were heard *nearby*, and which board people were closest to. **No MAC addresses, names, or positions.** Simulated crowd decoys are never uploaded. Data older than 30 days is deleted automatically.

## 0. No Vultr yet? Run it on the laptop
```powershell
py tools/demo_run.py --sim --cloud      # or: --button --cloud for the real boards
```
`--cloud` starts the same history server on `http://127.0.0.1:8000` (`tools/local_cloud.py`, history in `data/moth_local.db`) plus the uploader. The first run writes `config/cloud.json` with fresh tokens. The Chert agent runs on the laptop too, so it reads that local server directly. Once the VM exists, point `config/cloud.json` and the agent's `MOTH_API_URL` / `MOTH_QUERY_TOKEN` at Vultr; nothing else changes.

## 1. Create the VM (Vultr dashboard, ~2 min)
1. **Deploy +** → **Cloud Compute – Shared CPU**
2. Location: **Atlanta**, or whichever is closest to you
3. Image: **Ubuntu 24.04 LTS**
4. Plan: the smallest one is plenty
5. **Deploy**. On the instance page, copy the **IP address** and **root password**

## 2. Install the service (from the laptop, PowerShell, in the repo folder)
```powershell
scp cloud\moth_cloud.py cloud\deploy.sh root@YOUR_IP:/root/
ssh root@YOUR_IP "bash /root/deploy.sh America/New_York"
```
It prints the **URL** (`https://YOUR-IP-WITH-DASHES.sslip.io`, which gets free HTTPS) plus two tokens:
- **INGEST token** goes to the laptop and can only *write* samples
- **QUERY token** goes to the Chert agent and can only *read* history

Check it: `curl https://YOUR-IP-WITH-DASHES.sslip.io/health` should print `{"ok": true}`.

## 3. Point the laptop at it
Copy `config\cloud.example.json` to `config\cloud.json` (git-ignored) and fill in `url` and `ingest_token`. Then, alongside the demo:
```powershell
py tools/demo_run.py --button          # window 1: the usual demo
py tools/occupancy_uploader.py         # window 2: sends occupancy to Vultr
```
Use `--dry-run` to see the samples without sending anything.

> **The demo Wi-Fi (WATCHING-YOU) has no internet.** The uploader saves samples to `data/occupancy_spool.jsonl` and uploads them as soon as the laptop can reach Vultr. For live updates during the demo, give the laptop a second connection, such as Ethernet or USB tethering from a phone, while its Wi-Fi stays on WATCHING-YOU.

## 4. Ask questions (what the Chert agent calls)
All read endpoints need `Authorization: Bearer <QUERY token>`. Each one returns JSON plus an `answer` sentence the agent can say out loud.

| Ask | Call |
|---|---|
| "Is anyone in the room right now?" | `GET /api/now?room=demo-room` |
| "When were people in the room today / on Friday?" | `GET /api/summary?room=demo-room&date=2026-11-13` |
| "Between 2 and 5 PM?" | `GET /api/occupancy?room=demo-room&from=2026-11-13T14:00&to=2026-11-13T17:00` |

Add `&tz=America/Chicago` and similar to change the timezone of spoken times.

The answers are deliberately careful. If the sensors weren't reporting, the agent says *"the sensors haven't reported, so I can't tell"* and never *"the room was empty"*.
