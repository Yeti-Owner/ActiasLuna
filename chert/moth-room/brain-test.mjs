// Tests the agent's voice brain against Gemini with no LiveKit, FaceTime or Chert: same model, instructions and tools
// as agent.mjs. Says the greeting, then asks a room question as text, and reports audio produced and tools called.
// Run: node --env-file=.env.local brain-test.mjs            (uses a little Gemini quota, no Chert allowance)
import { initializeLogger, llm } from '@livekit/agents';
import * as google from '@livekit/agents-plugin-google';
import { instructionsFor, todayInTz, tools } from './moth-tools.mjs';

initializeLogger({ pretty: false, level: 'warn' });

const QUESTION = process.argv[2] || 'What happened in the room in the last five minutes?';
const model = new google.realtime.RealtimeModel({ voice: 'Puck' });
const session = model.session();
const byName = Object.fromEntries(tools.map(t => [t.name, t]));
const toolCtx = new llm.ToolContext(tools);
const t0 = Date.now();
const log = (...a) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s]`, ...a);

session.on('error', e => log('ERROR', e.error?.message || String(e.error || e)));
let turnDone;
session.on('generation_created', async gen => {
  log('generation started', gen.userInitiated ? '(asked)' : '(model)');
  let frames = 0, text = '';
  const calls = [];
  const readMsgs = (async () => {
    for await (const msg of gen.messageStream) {
      const a = (async () => { for await (const f of msg.audioStream) frames += f.samplesPerChannel; })();
      const b = (async () => { for await (const s of msg.textStream) text += s; })();
      await Promise.all([a, b]);
    }
  })();
  const readCalls = (async () => { for await (const fc of gen.functionStream) calls.push(fc); })();
  await Promise.all([readMsgs, readCalls]);
  log(`generation done: ${(frames / 24000).toFixed(1)} s of audio`, text ? `text="${text.slice(0, 160)}"` : '');
  for (const fc of calls) {
    log(`tool call ${fc.name}(${fc.args})`);
    const out = await byName[fc.name].execute(fc.args ? JSON.parse(fc.args) : {}, {});
    log(`tool result: ${JSON.stringify(out.answer || out).slice(0, 160)}`);
    const chat = session.chatCtx.copy();
    chat.insert([fc, llm.FunctionCallOutput.create({ callId: fc.callId, name: fc.name, output: JSON.stringify(out),
      isError: false })]);
    await session.updateChatCtx(chat);
    log('sent tool result back; waiting for the spoken answer');
    return;
  }
  turnDone?.();
});

const wait = ms => new Promise(r => setTimeout(r, ms));
const nextTurn = ms => new Promise(r => { turnDone = r; setTimeout(r, ms); });

await session.updateInstructions(instructionsFor(todayInTz()));
await session.updateTools(toolCtx);
log('greeting...');
const g = nextTurn(15000);
await session.generateReply('Greet the caller in one short sentence that begins exactly with "Hey, I\'m Actias".');
await g;
log(`asking: "${QUESTION}"`);
const q = nextTurn(25000);
const chat = session.chatCtx.copy();
chat.addMessage({ role: 'user', content: QUESTION });
await session.updateChatCtx(chat);
await session.generateReply();
await q;
await wait(12000);
await session.close();
process.exit(0);
