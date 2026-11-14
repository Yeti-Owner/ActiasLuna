// Opens a Gemini Live voice session directly (no FaceTime/LiveKit) and checks it actually speaks.
// Run: node --env-file=.env.local check-gemini-live.mjs
import { GoogleGenAI, Modality } from '@google/genai';

const model = process.argv[2] || 'gemini-2.5-flash-native-audio-preview-12-2025';
const ai = new GoogleGenAI({ apiKey: process.env.GOOGLE_API_KEY });
let audioBytes = 0, firstAudioMs = null, done = false;
const t0 = Date.now();
const finish = msg => { if (done) return; done = true; console.log(msg); process.exit(0); };

const session = await ai.live.connect({
  model,
  config: { responseModalities: [Modality.AUDIO],
            speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: 'Puck' } } } },
  callbacks: {
    onopen: () => console.log(`[${Date.now() - t0} ms] connected to ${model}`),
    onmessage: m => {
      for (const p of m.serverContent?.modelTurn?.parts || []) {
        if (p.inlineData?.data) { audioBytes += Buffer.from(p.inlineData.data, 'base64').length; firstAudioMs ??= Date.now() - t0; }
      }
      if (m.serverContent?.turnComplete)
        finish(`OK: spoke ${(audioBytes / 48000).toFixed(1)} s of audio, first audio after ${firstAudioMs} ms`);
    },
    onerror: e => finish(`ERROR: ${e.message || e}`),
    onclose: e => finish(`CLOSED: code=${e.code} reason=${e.reason || '(none)'} after ${audioBytes} audio bytes`),
  },
});
session.sendClientContent({ turns: [{ role: 'user', parts: [{ text: "Say exactly: Hey, I'm Actias." }] }], turnComplete: true });
setTimeout(() => finish(`TIMEOUT: no complete reply in 20 s (${audioBytes} audio bytes so far)`), 20000);
