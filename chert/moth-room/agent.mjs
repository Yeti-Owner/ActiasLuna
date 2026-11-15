import { fileURLToPath } from 'node:url';
import { AutoSubscribe, defineAgent, voice } from '@livekit/agents';
import * as google from '@livekit/agents-plugin-google';
import * as openai from '@livekit/agents-plugin-openai';
import { runAgent } from './chert-rooms.mjs';
import { greetOnBridgeReady } from './greet.mjs';
import { instructionsFor, todayInTz, tools } from './moth-tools.mjs';   // room-history tools
import { AudioStream, LocalVideoTrack, RoomEvent, TrackKind, TrackSource, VideoBufferType, VideoFrame,
  VideoSource } from '@livekit/rtc-node';
import { FPS, HEIGHT, WIDTH, loadScreen } from './screen.mjs';     // the moth the caller sees

// Publish the moth as the agent's camera (Chert shows video only from the participant that speaks = this agent).
// Any failure is logged and the call goes on as voice only.
async function startScreen(ctx, log) {
  try {
    const draw = await loadScreen();
    const source = new VideoSource(WIDTH, HEIGHT);
    const track = LocalVideoTrack.createVideoTrack('actias-moth', source);
    let tick = 0;
    const state = { speaking: false };
    const frame = () => source.captureFrame(new VideoFrame(draw(tick++, state.speaking), WIDTH, HEIGHT, VideoBufferType.RGBA));
    frame();
    await ctx.room.localParticipant.publishTrack(track, { source: TrackSource.SOURCE_CAMERA });
    const timer = setInterval(() => { try { frame(); } catch { /* skip a frame */ } }, 1000 / FPS);
    ctx.addShutdownCallback(async () => { clearInterval(timer); await track.close(); await source.close(); });
    log('screen on');
    return state;
  } catch (e) {
    log('screen failed (voice still works):', e?.message || String(e));
    return { speaking: false };
  }
}

// Call diagnostics: who is in the room, what they publish, whether the caller's audio reaches us and how loud it is.
// A silent call shows here as either "no caller audio track", "caller audio level 0" or no "agent audio out" line.
function watchMedia(room, callerId, log) {
  const desc = p => `${p.identity} [${[...p.trackPublications.values()].map(t => `${t.kind === TrackKind.KIND_AUDIO ? 'audio' : 'video'}:${t.source}${t.subscribed ? '+sub' : ''}`).join(', ') || 'no tracks'}]`;
  log('room has', [...room.remoteParticipants.values()].map(desc).join('; ') || 'nobody else', '| caller =', callerId);
  room.on(RoomEvent.ParticipantConnected, p => log('joined', desc(p)));
  room.on(RoomEvent.ParticipantDisconnected, p => log('left', p.identity));
  room.on(RoomEvent.TrackPublished, (pub, p) => log('published by', p.identity, pub.kind === TrackKind.KIND_AUDIO ? 'audio' : 'video'));
  room.on(RoomEvent.TrackUnsubscribed, (_t, _pub, p) => log('unsubscribed from', p.identity));
  room.on(RoomEvent.LocalTrackPublished, pub => log('agent published', pub.kind === TrackKind.KIND_AUDIO ? 'audio' : 'video'));
  room.on(RoomEvent.TrackSubscribed, async (track, _pub, p) => {
    log('subscribed to', p.identity, track.kind === TrackKind.KIND_AUDIO ? 'audio' : 'video');
    if (track.kind !== TrackKind.KIND_AUDIO) return;
    let peak = 0, frames = 0, last = Date.now();
    for await (const f of new AudioStream(track)) {
      frames++;
      for (const s of f.data) peak = Math.max(peak, Math.abs(s));
      if (Date.now() - last >= 3000) {
        log(`audio from ${p.identity}: ${frames} frames, peak ${peak}${peak < 300 ? ' (silence)' : ''}`);
        peak = 0; frames = 0; last = Date.now();
      }
    }
  });
}

// The agent's voice brain: Gemini when GOOGLE_API_KEY is set (free tier at aistudio.google.com), else OpenAI.
function voiceBrain() {
  const real = k => k && !/YOUR_|paste-/.test(k);
  if (real(process.env.GOOGLE_API_KEY)) return new google.realtime.RealtimeModel({ voice: 'Puck' });
  return new openai.realtime.RealtimeModel({ model: 'gpt-realtime', voice: 'marin' });
}

// Said once, as soon as FaceTime is ready. Keep it short: it is the first thing the caller hears.
const greeting = 'Greet the caller in one short sentence that begins exactly with "Hey, I\'m Actias": say you are the AI for their room and that they can ask who has been in it.';

const agent = defineAgent({
  entry: async ctx => {
    await ctx.connect(undefined, AutoSubscribe.AUDIO_ONLY);
    // Chert prepares a room before a caller exists. Join promptly so Chert can find us, but wait before opening a
    // paid model session. Doctor jobs may end without a caller ever joining.
    const caller = await ctx.waitForParticipant();
    const log = (...a) => console.log(JSON.stringify({ time: Date.now(), msg: `MOTH ${a.join(' ')}` }));
    watchMedia(ctx.room, caller.identity, log);
    // MOTH_VIDEO=0 skips the moth camera track (voice only), to rule video out when a call has no sound.
    const screen = process.env.MOTH_VIDEO === '0' ? (log('video off (MOTH_VIDEO=0)'), { speaking: false })
      : await startScreen(ctx, log);
    const session = new voice.AgentSession({
      llm: voiceBrain(),
    });
    ctx.addShutdownCallback(() => session.close());
    // Diagnostics: log when the agent speaks/listens, so a call's log shows it talked. Also drives the moth's glow.
    session.on('agent_state_changed', ev => { screen.speaking = ev.newState === 'speaking'; log('agent', ev.oldState, '->', ev.newState); });
    session.on('user_state_changed', ev => log('caller', ev.oldState, '->', ev.newState));
    session.on('error', ev => log('error', ev.error?.message || String(ev.error)));
    session.on('function_tools_executed', ev => log('tools ran:', ev.functionCalls.map(c => c.name).join(', ')));
    session.on('close', ev => log('session closed:', ev.reason, ev.error ? String(ev.error.message || ev.error) : ''));
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
    const stopGreeting = greetOnBridgeReady(ctx.room, () => session.generateReply({ instructions: greeting }));
    ctx.addShutdownCallback(async () => stopGreeting());
  },
});

export default agent;

// Your own LiveKit project when .env.local has its three LIVEKIT_ values; otherwise Chert rooms through chert dev.
runAgent(agent, { file: fileURLToPath(import.meta.url), agentName: process.env.AGENT_NAME || 'moth-room' });
