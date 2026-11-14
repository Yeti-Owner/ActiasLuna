# Moth FaceTime agent (Chert)

FaceTime the room and ask about it: *"Is anyone in my room?"*, *"Was anyone here today?"*, *"Who was here between 2 and 5?"* The agent answers out loud using the room history stored on Vultr (see `../cloud/`).

These are **our** files only. The rest of the agent comes from Chert's starter: `greet.mjs`, `chert-rooms.mjs`, and `node_modules`.

## Setup (Windows)
Chert's Windows support needs **Node 24** and an agent folder **outside OneDrive**. This repo lives inside OneDrive, so don't run the agent from here.

```powershell
$env:Path = "C:\Users\nicho\node24;" + $env:Path          # portable Node 24
cd C:\Users\nicho\chert-moth
npx --yes @trychert/facetime@0.1.0-beta.21 new moth-room --agent moth-room-nick
# copy agent.mjs, moth-tools.mjs, test-tools.mjs from this folder into C:\Users\nicho\chert-moth\moth-room
cd moth-room; npm install
```

Fill in `.env.local`, which is never committed. Never paste these values into chat.
- `LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET`: run `chert livekit` once Chert provisions it, or use a free project from cloud.livekit.io
- `OPENAI_API_KEY`
- `MOTH_API_URL` and `MOTH_QUERY_TOKEN`: from `cloud/deploy.sh`'s output. Use the **QUERY** token, which can only read.

## Brain: Gemini or OpenAI
If `GOOGLE_API_KEY` is set (free at aistudio.google.com/apikey), the agent uses Gemini's live voice model; otherwise it uses OpenAI. `chert doctor` always warns "No OPENAI_API_KEY" because its check only knows the OpenAI starter. With Gemini, that warning is expected.

**Also edit `chert-rooms.mjs`** (Chert's starter file): pass `port: 8091` to `new ServerOptions({...})`. The agent's health server defaults to **8081**, which collides with Moth's phone calibration page.

**Windows clock:** Chert declines calls when the PC clock is more than ¼ s fast. If `chert doctor` flags the clock, run in an **Administrator** PowerShell: `w32tm /config /manualpeerlist:"time.windows.com,0x9" /syncfromflags:manual /update`, `Restart-Service w32time`, then wait a few seconds and run `w32tm /resync /force`.

## Run (three terminals)
```powershell
py tools/demo_run.py --sim --cloud      # in the repo: dashboard + room history (use --button instead of --sim for real boards)
```
```powershell
npm start                                                            # terminal 1: the agent
npx @trychert/facetime@0.1.0-beta.21 dev --agent moth-room-nick      # terminal 2: puts it on your Chert line
```
Then FaceTime your Chert line from the verified iPhone.

`node test-tools.mjs` (with the `MOTH_*` variables set) calls each tool exactly as the voice model would, with no call needed.

## Rules the agent follows
- It always checks the history before answering and never guesses.
- It knows **how many** people were there and which board they were near, never **who** they were.
- If the sensors weren't reporting, it says so. It never turns a gap into "nobody was there."
