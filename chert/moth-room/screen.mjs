// What the caller SEES on the FaceTime video call: the Actias moth (moth.png), centred on black, in portrait.
// While the agent talks the moth glows a little brighter, so the caller can tell it is speaking.
// If anything here fails, the agent logs it and the call carries on as voice only.
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

export const WIDTH = 540;
export const HEIGHT = 960;
export const FPS = 10;
const IMAGE = fileURLToPath(new URL('./moth.png', import.meta.url));
const LEVELS = 8;            // precomputed brightness steps for the "speaking" glow

/** Load moth.png once -> a function (tick, speaking) => RGBA frame of WIDTH x HEIGHT. */
export async function loadScreen() {
  const size = Math.min(WIDTH, HEIGHT);           // square image fills the width, black above and below
  const trimmed = await sharp(IMAGE).flatten({ background: { r: 0, g: 0, b: 0 } }).trim({ threshold: 20 }).toBuffer();
  const pad = Math.round(size * 0.05);           // keep a thin black margin around the moth
  const inner = size - 2 * pad;
  // sharp applies one resize + one extend per pipeline, so do the padding and centring in a single extend.
  const { data } = await sharp(trimmed)
    .resize(inner, inner, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 1 } })
    .extend({ top: Math.floor((HEIGHT - inner) / 2), bottom: Math.ceil((HEIGHT - inner) / 2),
              left: Math.floor((WIDTH - inner) / 2), right: Math.ceil((WIDTH - inner) / 2),
              background: { r: 0, g: 0, b: 0, alpha: 1 } })
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  if (data.length !== WIDTH * HEIGHT * 4) throw new Error(`moth frame is ${data.length} bytes, expected ${WIDTH * HEIGHT * 4}`);
  const base = new Uint8Array(data);
  // Level 0 = resting (slightly dimmed), top level = full brightness while speaking.
  const frames = [];
  for (let l = 0; l < LEVELS; l++) {
    const k = 0.72 + 0.28 * (l / (LEVELS - 1));
    const f = new Uint8Array(base.length);
    for (let i = 0; i < base.length; i += 4) {
      f[i] = base[i] * k; f[i + 1] = base[i + 1] * k; f[i + 2] = base[i + 2] * k; f[i + 3] = 255;
    }
    frames.push(f);
  }
  let level = 0;
  return (tick, speaking) => {
    // Speaking: pulse between mid and full brightness. Silent: ease back to resting.
    const target = speaking ? Math.round((LEVELS - 1) * (0.65 + 0.35 * Math.abs(Math.sin(tick * 0.5)))) : 0;
    level += Math.sign(target - level);
    return frames[level];
  };
}
