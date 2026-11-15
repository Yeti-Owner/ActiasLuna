// Can audio actually flow through LiveKit from this laptop's network? Two test participants join a throwaway room in
// your LiveKit project; one plays a tone, the other counts what arrives. No Chert, no model.
// Run: node --env-file=.env.local media-test.mjs
import crypto from 'node:crypto';
import { AudioFrame, AudioSource, AudioStream, LocalAudioTrack, Room, RoomEvent, TrackKind, TrackPublishOptions,
  TrackSource } from '@livekit/rtc-node';

const { LIVEKIT_URL: url, LIVEKIT_API_KEY: key, LIVEKIT_API_SECRET: secret } = process.env;
const ROOM = `media-test-${Date.now()}`;
const b64u = b => Buffer.from(b).toString('base64url');
function token(identity) {
  const now = Math.floor(Date.now() / 1000) - 30;
  const h = b64u(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const p = b64u(JSON.stringify({ iss: key, sub: identity, nbf: now, exp: now + 600,
    video: { room: ROOM, roomJoin: true, canPublish: true, canSubscribe: true } }));
  return `${h}.${p}.${crypto.createHmac('sha256', secret).update(`${h}.${p}`).digest('base64url')}`;
}

const t0 = Date.now();
const log = (...a) => console.log(`[${((Date.now() - t0) / 1000).toFixed(1)}s]`, ...a);
const listener = new Room();
const speaker = new Room();
let received = 0, loud = 0;
listener.on(RoomEvent.TrackSubscribed, async track => {
  if (track.kind !== TrackKind.KIND_AUDIO) return;
  log('listener subscribed to the tone');
  for await (const f of new AudioStream(track, 48000, 1)) {
    received += f.samplesPerChannel;
    if (f.data.some(s => Math.abs(s) > 1000)) loud += f.samplesPerChannel;
  }
});
listener.on(RoomEvent.ConnectionStateChanged, s => log('listener connection', s));

await listener.connect(url, token('listener'), { autoSubscribe: true, dynacast: false });
log('listener joined');
await speaker.connect(url, token('speaker'), { autoSubscribe: false, dynacast: false });
log('speaker joined');
const source = new AudioSource(48000, 1);
const track = LocalAudioTrack.createAudioTrack('tone', source);
const opts = new TrackPublishOptions();
opts.source = TrackSource.SOURCE_MICROPHONE;
await speaker.localParticipant.publishTrack(track, opts);
log('speaker publishing a 440 Hz tone for 6 s');
let phase = 0;
for (let i = 0; i < 600; i++) {            // 600 x 10 ms
  const d = new Int16Array(480);
  for (let j = 0; j < 480; j++) d[j] = Math.round(8000 * Math.sin((phase++ * 2 * Math.PI * 440) / 48000));
  await source.captureFrame(new AudioFrame(d, 48000, 1, 480));
}
await new Promise(r => setTimeout(r, 1500));
const secs = received / 48000, loudSecs = loud / 48000;
log(`received ${secs.toFixed(1)} s of audio, ${loudSecs.toFixed(1)} s of it the tone`);
console.log(loudSecs > 3 ? 'MEDIA OK: audio flows through LiveKit on this network'
  : 'MEDIA BLOCKED: signalling works but audio does not flow (UDP/WebRTC blocked on this network?)');
await speaker.disconnect();
await listener.disconnect();
process.exit(0);
