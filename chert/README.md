# Actias FaceTime agent (Chert)

FaceTime the room and ask about it: *"Did anyone come in?"*, *"Where are they?"*, *"Was anyone here today?"* The agent answers out loud from the room history on Vultr (`../cloud/`), using a **read-only** QUERY token. For example: *"Someone entered 30 seconds ago, stood by the Amazon Alexa for 10 seconds, then wandered to the Smart Thermostat, then left."*

## Setup (once)
Needs **Node 24** (nodejs.org) and a filled-in `config\cloud.json` (see `../cloud/README.md`). From the repo root:
```powershell
powershell -ExecutionPolicy Bypass -File chert\setup.ps1
```
This creates the agent in `%USERPROFILE%\chert-moth\moth-room`, outside the repo, because Chert on Windows breaks under OneDrive. Chert's starter supplies `greet.mjs`, `chert-rooms.mjs` and `node_modules`, and our files go on top. The script also moves the agent's health port to 8091, since 8081 is the Moth phone page. It writes `.env.local` with the Vultr URL, room and QUERY token already filled in. Options: `-Agent <unique-name>` and `-Dir C:\somewhere`.

**Then paste your keys into `.env.local`:**
- `GOOGLE_API_KEY` (free at aistudio.google.com/apikey) **or** `OPENAI_API_KEY`
- `LIVEKIT_URL / _API_KEY / _API_SECRET`: leave them empty to use Chert's rooms, or fill all three from one cloud.livekit.io project

Never paste these into chat or commit them.

**Windows clock:** Chert declines calls when the PC clock is more than ¼ s fast. If `chert doctor` flags the clock, run these in an **Administrator** PowerShell: `w32tm /config /manualpeerlist:"time.windows.com,0x9" /syncfromflags:manual /update`, then `Restart-Service w32time`, then `w32tm /resync /force`.

## Run
Keys live in `chert\moth-room\.env.local` in the repo (git-ignored). After changing it, re-run `chert\setup.ps1`. Node 24 is picked up from `%USERPROFILE%\node24` when it's there.
```powershell
py server/luna_server.py                                          # terminal 1, in the repo: tracking + upload
powershell -ExecutionPolicy Bypass -File chert\run.ps1 check      # anytime: Vultr tools + keys, no Chert calls
powershell -ExecutionPolicy Bypass -File chert\run.ps1 agent      # terminal 2: the agent
powershell -ExecutionPolicy Bypass -File chert\run.ps1 line       # terminal 3: on your Chert line (uses Chert)
```
Then FaceTime your Chert line from the verified iPhone. Start `line` only when you're about to call, and stop it with Ctrl+C afterwards.

The agent only hears about the **latest run**. Each `luna_server` start is a new run, so earlier tests and sessions never show up in its answers.

## Tools the agent has
| Tool | Server call | For |
|---|---|---|
| `recent_activity(minutes)` | `/api/recent` | "Did anyone come in?", "What just happened?" |
| `where_now` | `/api/where` | "Where are they?", "Is someone by B?" |
| `room_status_now` | `/api/now` | "Is anyone there?" |
| `room_history_for_day(date)` | `/api/summary` | "today", "yesterday", "on Friday" |
| `room_history_between(from, to)` | `/api/events` | "between 2 and 5 PM" |

## Rules the agent follows
- It always checks the history before answering and never guesses.
- It knows how many people there were and where they stood, never **who** they were.
- If the sensors weren't reporting, it says so. It never turns a gap into "nobody was there."
