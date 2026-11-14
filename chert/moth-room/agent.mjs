import { fileURLToPath } from 'node:url';
import { AutoSubscribe, defineAgent, voice } from '@livekit/agents';
import * as google from '@livekit/agents-plugin-google';
import * as openai from '@livekit/agents-plugin-openai';

// The agent's voice brain: Gemini when GOOGLE_API_KEY is set (free tier at aistudio.google.com), else OpenAI.
function voiceBrain() {
  const real = k => k && !/YOUR_|paste-/.test(k);
  if (real(process.env.GOOGLE_API_KEY)) return new google.realtime.RealtimeModel({ voice: 'Puck' });
  return new openai.realtime.RealtimeModel({ model: 'gpt-realtime', voice: 'marin' });
}
import { runAgent } from './chert-rooms.mjs';
import { greetOnBridgeReady } from './greet.mjs';
import { instructionsFor, todayInTz, tools } from './moth-tools.mjs';   // room-history tools (Vultr API)
import { GREETING_TEXT, greetingStream, loadGreeting } from './greeting-audio.mjs';   // pre-made "Hey, I'm Actias"

const greeting = 'Greet the caller in one short sentence that begins exactly with "Hey, I\'m Actias": say you are the AI for their room and that they can ask who has been in it.';

const agent = defineAgent({
  entry: async ctx => {
    await ctx.connect(undefined, AutoSubscribe.AUDIO_ONLY);
    const greetAudio = loadGreeting().catch(() => null);   // from disk cache: ready long before FaceTime is
    // Chert prepares a room before a caller exists. Join promptly so Chert can find us, but wait before opening a
    // paid model session. Doctor jobs may end without a caller ever joining.
    const caller = await ctx.waitForParticipant();
    const session = new voice.AgentSession({
      llm: voiceBrain(),
    });
    ctx.addShutdownCallback(() => session.close());
    await session.start({
      room: ctx.room,
      agent: new voice.Agent({ instructions: instructionsFor(todayInTz()), tools }),
      record: false,
      inputOptions: {
        participantIdentity: caller.identity,
        audioEnabled: true,
        videoEnabled: false,
        textEnabled: false,
        closeOnDisconnect: true,
        deleteRoomOnClose: false,
      },
      outputOptions: { audioEnabled: true, transcriptionEnabled: false },
    });
    // Greet once when chert dev reports FaceTime is ready (bridge_ready): the call ends if the agent is silent for
    // 10 s after that. Participant arrival alone is not readiness.
    // Diagnostics: one line per state change, so a call's log shows when the greeting starts, ends or is cut off.
    const log = (...a) => console.log(JSON.stringify({ time: Date.now(), msg: `MOTH ${a.join(' ')}` }));
    session.on('agent_state_changed', ev => log('agent', ev.oldState, '->', ev.newState));
    session.on('user_state_changed', ev => log('caller', ev.oldState, '->', ev.newState));
    session.on('error', ev => log('error', ev.error?.message || String(ev.error)));

    // Measured on real calls: Chert signals "FaceTime ready" ~3 s BEFORE our audio actually reaches the caller, so a
    // greeting started on "ready" plays into dead air. Wait until Chert has subscribed to our audio AND at least 3.5 s
    // have passed since "ready" (cap 6 s: Chert ends calls with no agent audio 10 s after "ready").
    let subscribedAt = null;
    ctx.room.on('localTrackSubscribed', () => { if (!subscribedAt) { subscribedAt = Date.now(); log('Chert subscribed to our audio'); } });
    const MIN_AFTER_READY = 3500, MAX_AFTER_READY = 6000, AFTER_SUBSCRIBE = 700;
    const sleep = ms => new Promise(r => setTimeout(r, ms));

    // Play the pre-made greeting once the line carries audio; if it's missing or fails, have the model say it live.
    // Not interruptible: line noise while FaceTime connects can look like speech and cancel a short intro.
    const stopGreeting = greetOnBridgeReady(ctx.room, async () => {
      const readyAt = Date.now();
      log('FaceTime ready, waiting for the audio path');
      for (;;) {
        const now = Date.now();
        if (now - readyAt >= MAX_AFTER_READY) break;
        if (subscribedAt && now - readyAt >= MIN_AFTER_READY && now - subscribedAt >= AFTER_SUBSCRIBE) break;
        await sleep(100);
      }
      log(`greeting at +${Date.now() - readyAt} ms after ready; subscribed ${
        subscribedAt ? `${subscribedAt - readyAt} ms relative to ready` : 'not seen'}`);
      const audio = await greetAudio;
      try {
        if (audio) {
          const h = session.say(GREETING_TEXT, { audio: greetingStream(audio), allowInterruptions: false });
          log('greeting queued (pre-made audio)');
          h.addDoneCallback(sh => log(`greeting finished, interrupted=${sh.interrupted}`));
          return;
        }
        log('no pre-made audio, using live greeting');
      } catch (e) { log('pre-made greeting failed:', e.message); }
      try {
        const h = session.generateReply({ instructions: greeting, allowInterruptions: false });
        h.addDoneCallback(sh => log(`live greeting finished, interrupted=${sh.interrupted}`));
      } catch { /* the call ended first */ }
    });
    ctx.addShutdownCallback(async () => stopGreeting());
  },
});

export default agent;

// Your own LiveKit project when .env.local has its three LIVEKIT_ values; otherwise Chert rooms through chert dev.
runAgent(agent, { file: fileURLToPath(import.meta.url), agentName: process.env.AGENT_NAME || 'moth-room' });
