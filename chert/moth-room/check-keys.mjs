// Checks the LiveKit and Gemini keys in .env.local directly. Prints only pass/fail, never the keys.
// Run: node --env-file=.env.local check-keys.mjs
import crypto from 'node:crypto';

const b64u = b => Buffer.from(b).toString('base64url');

async function checkLiveKit() {
  const { LIVEKIT_URL: url, LIVEKIT_API_KEY: key, LIVEKIT_API_SECRET: secret } = process.env;
  const now = Math.floor(Date.now() / 1000) - 30;           // backdated 30 s so a fast clock can't break it
  const header = b64u(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const payload = b64u(JSON.stringify({ iss: key, sub: 'moth-check', nbf: now, iat: now, exp: now + 600,
                                        video: { roomList: true } }));
  const sig = crypto.createHmac('sha256', secret).update(`${header}.${payload}`).digest('base64url');
  const http = url.replace(/^wss:/, 'https:').replace(/^ws:/, 'http:').replace(/\/+$/, '');
  const res = await fetch(`${http}/twirp/livekit.RoomService/ListRooms`, {
    method: 'POST', body: '{}',
    headers: { Authorization: `Bearer ${header}.${payload}.${sig}`, 'Content-Type': 'application/json' },
  });
  const body = await res.text();
  return res.ok ? 'OK - key + secret accepted' : `REJECTED (${res.status}): ${body.slice(0, 160)}`;
}

async function checkGemini() {
  const res = await fetch('https://generativelanguage.googleapis.com/v1beta/models?pageSize=200', {
    headers: { 'x-goog-api-key': process.env.GOOGLE_API_KEY || '' },
  });
  if (!res.ok) return `REJECTED (${res.status}): ${(await res.text()).slice(0, 160)}`;
  const { models = [] } = await res.json();
  const live = models.filter(m => (m.supportedGenerationMethods || []).includes('bidiGenerateContent'))
    .map(m => m.name.replace('models/', ''));
  return `OK - key accepted; live voice models available: ${live.slice(0, 6).join(', ') || 'none listed'}`;
}

for (const [name, fn] of [['LiveKit', checkLiveKit], ['Gemini', checkGemini]]) {
  try { console.log(`${name}: ${await fn()}`); } catch (e) { console.log(`${name}: ERROR ${e.message}`); }
}
