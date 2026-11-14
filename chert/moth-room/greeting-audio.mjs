// Instant greeting: the "Hey, I'm Actias" audio is made ONCE with Gemini TTS (same Puck voice as the live agent),
// cached to greeting-cache/, and played the moment FaceTime is ready -- instead of asking the model to compose a
// greeting live on every call (which adds a second or two of silence).
import crypto from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { AudioFrame } from '@livekit/rtc-node';

export const GREETING_TEXT = "Hey, I'm Actias, the AI for your room. Ask me who's been in it.";
const VOICE = 'Puck';
const TTS_MODEL = 'gemini-2.5-flash-preview-tts';
const CACHE = join(dirname(fileURLToPath(import.meta.url)), 'greeting-cache');

/** -> { pcm: Int16Array, rate: number } or throws. Cached by text+voice, so the API is hit only once. */
export async function loadGreeting(text = GREETING_TEXT, apiKey = process.env.GOOGLE_API_KEY) {
  const id = crypto.createHash('sha256').update(`${TTS_MODEL}|${VOICE}|${text}`).digest('hex').slice(0, 16);
  const pcmPath = join(CACHE, `${id}.pcm`), metaPath = join(CACHE, `${id}.json`);
  try {
    const { rate } = JSON.parse(await readFile(metaPath, 'utf8'));
    return { pcm: toInt16(await readFile(pcmPath)), rate };
  } catch { /* not cached yet */ }
  if (!apiKey || /YOUR_/.test(apiKey)) throw new Error('no GOOGLE_API_KEY for greeting audio');
  const res = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${TTS_MODEL}:generateContent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': apiKey },
    body: JSON.stringify({
      contents: [{ parts: [{ text: `Say in a warm, friendly, upbeat voice: ${text}` }] }],
      generationConfig: { responseModalities: ['AUDIO'],
        speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName: VOICE } } } },
    }),
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`greeting TTS failed (${res.status}): ${(await res.text()).slice(0, 200)}`);
  const part = (await res.json()).candidates?.[0]?.content?.parts?.find(p => p.inlineData);
  if (!part) throw new Error('greeting TTS returned no audio');
  const rate = Number(/rate=(\d+)/.exec(part.inlineData.mimeType || '')?.[1] || 24000);
  const bytes = Buffer.from(part.inlineData.data, 'base64');
  await mkdir(CACHE, { recursive: true });
  await writeFile(pcmPath, bytes);
  await writeFile(metaPath, JSON.stringify({ rate, text, voice: VOICE, model: TTS_MODEL }));
  return { pcm: toInt16(bytes), rate };
}

function toInt16(buf) {
  const copy = new Uint8Array(buf.length - (buf.length % 2));
  copy.set(buf.subarray(0, copy.length));
  return new Int16Array(copy.buffer);                    // 16-bit little-endian mono PCM
}

/** The cached greeting as a stream of 20 ms LiveKit audio frames, for session.say(text, { audio }). */
export function greetingStream({ pcm, rate }) {
  const step = Math.round(rate / 50);                    // samples per 20 ms frame
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= pcm.length) return controller.close();
      const chunk = pcm.slice(i, i + step);
      i += step;
      controller.enqueue(new AudioFrame(chunk, rate, 1, chunk.length));
    },
  });
}
