import { fileURLToPath } from 'node:url';
import { AutoSubscribe, defineAgent, voice } from '@livekit/agents';
import * as google from '@livekit/agents-plugin-google';
import * as openai from '@livekit/agents-plugin-openai';
import { runAgent } from './chert-rooms.mjs';
import { greetOnBridgeReady } from './greet.mjs';
import { instructionsFor, todayInTz, tools } from './moth-tools.mjs';   // room-history tools

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
    const session = new voice.AgentSession({
      llm: voiceBrain(),
    });
    ctx.addShutdownCallback(() => session.close());
    // Diagnostics only (no behaviour change): log when the agent speaks/listens, so a call's log shows it talked.
    const log = (...a) => console.log(JSON.stringify({ time: Date.now(), msg: `MOTH ${a.join(' ')}` }));
    session.on('agent_state_changed', ev => log('agent', ev.oldState, '->', ev.newState));
    session.on('user_state_changed', ev => log('caller', ev.oldState, '->', ev.newState));
    session.on('error', ev => log('error', ev.error?.message || String(ev.error)));
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
