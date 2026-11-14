# Moth — Devpost submission

> Paste each section into the matching field on Devpost. Replace every **[ ]** placeholder.
> Keep the honesty: the tracking is real, the decoy crowd is a simulated, display-only demo.

---

## Project name
**Moth** (repo name: ActiasLuna)

## Tagline  (one line, ~120 chars)
We can watch you walk across a room using only your phone's Wi-Fi. Then we show you how to disappear.

## Elevator pitch  (max 200 characters — this is 190)
We track anyone in a room to half a meter using just their phone's Wi-Fi—no app, no GPS. Then one button floods the map with decoy phones until the system can't tell which dot is really you.

<!-- Longer version, in case you want it in the Story intro instead:
Your phone is a radio that never shuts up—every few seconds it broadcasts its address and signal strength in the clear, and it doesn't take a hacker to listen. With three $10 boards, Moth finds where you're standing to half a meter: no app, no GPS, no camera, and we never read a message. That's the attack. The defense: one button floods the map with decoy phones and your real position disappears into the crowd. (Simulated on screen, not broadcast—we prove the idea without jamming anyone's network.) -->


---

## Inspiration
It started with a fact that bugged us. Your phone is constantly shouting over Wi-Fi, and that shout includes a unique address and how strong the signal is. Turn off location all you want — the packets still go out. Stores already use this to follow shoppers around a mall. Anyone with a few cheap boards can do the same thing to you, and you'd never know.

So we built it to see how easy it really is. It's easy. A person lights up on a map with zero cooperation. That's the scary half.

The other half is the whole point: you shouldn't have to be a security expert to fight back. If a hundred phones in the room all look like you, the system can't pick you out. So we built the attack to prove the problem, and the defense to prove you're not stuck with it.

Then we asked one more question. The same sensors that are creepy when a stranger runs them are actually useful when the room is *yours*. Was anyone in my dorm while I was at class? Did someone walk into the lab overnight? You shouldn't need to dig through logs to find out. You should be able to just call the room and ask. So we gave Moth a memory and a face: it keeps a history of when people were in the room, and you can FaceTime it and ask.

## What it does
Moth is a Wi-Fi tracking system and the countermeasure to it, side by side.

**The attack — tracking out of thin air.**
Three ESP32 boards sit around the room and listen to Wi-Fi. They never read your messages — only the header: who sent the frame, on what network, and how loud it was. The closer you are to a board, the stronger your signal. Compare the strength across three boards, smooth it over time, and you get a live position to about half a meter. It works two ways: a phone that joins the network, or **phone-free** — your body blocks the signals the boards bounce between each other, and we track that shadow. Either way you show up as a moving dot on a 3D map of the actual room. No app, no permission, no idea it's happening.

**The defense — hide in the crowd.**
Press the button on our Arduino and the map floods with decoy phones that swarm around you. Walk with them and your real dot melts into the pack — the more addresses in the room, the less sure anyone can be about which one is actually you. We keep it honest: the crowd is simulated and drawn on the screen, clearly labelled, and nothing is transmitted. We show the defense without interfering with real networks or anyone else's devices.

**Ask the room — FaceTime it.**
Moth keeps a running history of your room in the cloud on **Vultr**: when people were in it, how many, and which part of the room they were near. Then you FaceTime the room's AI agent, built on **Chert**, and just ask: *"Was anyone in my room today?"* *"When did they leave?"* *"Is someone there right now?"* It answers out loud: *"Two people came in at 3:02 and left at 3:15, mostly near the desk."* It's also honest about what it doesn't know. If the sensors were off, it says *"I wasn't watching then,"* never *"nobody was there."*

**Private by design.** Only radio headers, never content. Tracking runs locally: the dashboard is localhost-only and the phone page is HTTPS behind a PIN. The cloud history stores **counts only** (how many people, which board they were near, and when), with no device addresses, no names, and no positions. The fake crowd is never saved, and history deletes itself after 30 days.

## How we built it
- **Firmware:** 3× ESP32-S3 (one access point, two receivers) running ESP-IDF / C. Each board keeps only frame headers, summarizes signal strength every 250 ms, and streams it to the laptop over UDP. Strangers' frames are dropped on the board before they ever reach the laptop.
- **Server:** Python (NumPy). A Bayesian grid filter fuses readings over time with motion — phone step/compass sensors, or the radio-link disturbance in phone-free mode — to beat any single noisy reading. A short calibration walk teaches it the room, walls and furniture included.
- **Dashboard & web:** a three.js 3D view of the scanned room, plus a React + Vite + TypeScript/Tailwind web front end.
- **The crowd / privacy defense:** an Arduino **Uno Q** white button toggles a Python decoy-crowd simulation that draws extra "phones" around the real person — display-only, flagged as simulated, and wired into the same live 3D view.
- **Room history on Vultr:** a small Python + SQLite API on a Vultr VM, behind HTTPS (Caddy). An uploader on the laptop reads the live dashboard every few seconds and sends tiny samples like "1 person in the room, near board B." The laptop's key can only write and the agent's key can only read.
- **FaceTime agent with Chert:** Chert gives our agent a real Apple line, so you FaceTime it like a person. When you ask about the room, the agent calls the Vultr API and turns the answer into speech. Every endpoint returns a ready-to-say sentence, so the agent doesn't have to do math on timestamps mid-call.

## Challenges we ran into
- **A button that wasn't where we thought.** The Uno Q's on-board white button turned out to be wired to the board's *Linux* side as a power key — invisible to any Arduino sketch. We ended up reading it over the USB cable with `adb` and bridging each press to the server. Days of "why won't this button work?" in one sentence.
- **Windows fighting the flash.** Smart App Control silently blocked the firmware flasher until we tracked it down.
- **Getting everything on the same network.** Boards, laptop and phone all have to sit on the demo Wi-Fi subnet; a wrong SSID or a Windows firewall rule meant "no signal" even though everything was on.
- **Staying honest.** The tempting version of "confuse the tracker" is a real Wi-Fi spoofer that broadcasts fake addresses — but that interferes with shared airspace and other people's networks. We deliberately built the crowd as a **simulated, display-only** demo instead, so we show the privacy idea without shipping something that messes with anyone's network.
- **Accuracy from a noisy signal.** Turning jittery RSSI into a smooth, believable position took calibration and filtering, not just math.
- **A cloud feature on a network with no internet.** Our demo Wi-Fi is offline on purpose, which makes uploading to Vultr awkward. The uploader saves every sample to disk and syncs as soon as the laptop can reach the internet, so no history is lost.
- **Our own defense almost broke our own logs.** While the crowd is on, it pushes real people's dots around on screen. If we had logged what the screen showed, the history would have recorded the jammed positions. The uploader reads the true estimate underneath, and it skips the fake crowd entirely.
- **"Empty" vs "not watching."** A voice agent that says "nobody was in your room" when the sensors were actually unplugged is worse than useless. We made the API track when it was really watching, and the agent says so out loud.

## Accomplishments that we're proud of
- A room that **tracks a person live from Wi-Fi alone**, shown in 3D, with no app on the phone.
- A privacy defense that's **genuinely honest** — clearly labelled, transmits nothing — and still makes the point: more devices in a room means less certainty about any one person.
- Integrating awkward hardware (a dual-brain Arduino button over USB) into a clean one-button demo.
- Merging four people's work — firmware, server, 3D/web, and the crowd — into one working build.
- You can **FaceTime a room** and ask it who was there — and it answers honestly, including when it wasn't watching.

## What we learned
- How much a **passive Wi-Fi observer** can infer about you from signal strength alone — and why "I didn't share my location" isn't the whole story.
- The quirks of a dual-brain board (an MCU and a full Linux computer sharing one USB cable).
- How to design a security/privacy demo that is **safe and honest** — showing a risk and a defense without building an offensive tool.
- A lot about multi-person Git, calibration, and turning a noisy sensor into something you can trust on screen.

## What's next for Moth
- Tighter accuracy with more boards and a longer calibration.
- Maturing the phone-free sensing mode (today it's one person at a time).
- Real-room field testing, and writing up what we found about Wi-Fi privacy.

---

## Built with  (Devpost "Built with" tags)
esp32-s3 · esp-idf · c · arduino · uno-q · python · numpy · three.js · react · vite · typescript · tailwindcss · adb · udp · wifi · rssi · bayesian-filter · vultr · sqlite · caddy · chert · facetime · livekit · node.js

## "Try it out" links
- GitHub: **[ repo URL — e.g. https://github.com/<org>/ActiasLuna ]**
- Demo video: **[ YouTube/Vimeo link — ≤ 3 min, shows live tracking + the crowd button ]**

## Team
**[ names + what each person built — e.g. Nick: Arduino button + decoy crowd; Callum: Wi-Fi detection + server + 3D; Tomas: web front end; Victor: … ]**

## Event / hackathon
**[ event name ]** — tracks: **Best Use of FaceTime (Chert)**, **[ Vultr track name, if there is one ]**

---

### Notes for your submission (delete before posting)
- Upload the 3-min demo video and 3–5 screenshots (dashboard with the moving dot, the crowd-on moment with the SIMULATED banner, the boards/room in 3D, the hardware).
- In the video, say out loud that the crowd is simulated — it reads as more impressive *because* you're being straight about it.
- Double-check the repo is public (or add judges) before the deadline.
- **Before you submit:** the FaceTime part is written in present tense. If the Chert agent isn't answering calls by the deadline, move the "Ask the room" paragraph to "What's next" so the page stays accurate.
- For the FaceTime track, the video should show a real FaceTime call to the agent: ask "was anyone in the room today?" and let it answer. Chert's track is literally about agents that are useful on a call, so that clip is your strongest moment.
