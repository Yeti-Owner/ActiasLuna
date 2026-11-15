"use client"

// Moth Wordmark — a swarm of small moths over a night sky. They flit and spiral
// on a fast curl-noise current around a wandering centre, jittering the way
// moths do; after a couple of seconds they snap together into your word and
// hold it with a faint shimmer of wings. The pointer is a bat: the swarm parts
// around it and settles back, and only after a lot of sweeping through the
// word does it break, scatter, and later re-form.
//
// One file, React only. A 2D canvas for the sky, the moths and the reeds;
// every moth is two small filled wing shapes batched into a single path per
// frame. Nothing is fetched.

import * as React from "react"

export interface MothPalette {
  /** Sky at the top. */
  skyTop: string
  /** Sky at the horizon. */
  skyLow: string
  /** The moon / lamp glow low on the horizon. */
  glow: string
  /** The moths. */
  moth: string
  /** The reed line. */
  ink: string
}

export type MothPreset = "luna" | "night" | "dusk" | "paper"

export interface MothWordmarkProps {
  /** The word the swarm forms. Short and bold reads best. */
  word?: string
  /** How many moths. Scaled down on small screens. Defaults to 1400. */
  moths?: number
  /** Sky and moth colours. Defaults to "luna" (pale green moths, deep night). */
  preset?: MothPreset
  /** Colour overrides, merged over the preset. */
  palette?: Partial<MothPalette>
  /** Seconds the swarm flies free between formations. Defaults to 2. */
  flyFor?: number
  /** Seconds it takes the swarm to snap into the word. Defaults to 0.8. */
  formIn?: number
  /**
   * Once formed, the word holds until the pointer disturbs it this much: the
   * pointer's travel through the swarm, in screen-heights (it slowly heals
   * when left alone). Gentle hovering only parts the moths, which settle back.
   * Defaults to 3.
   */
  breakAt?: number
  /** Seconds the swarm takes to burst back out. Defaults to 0.35. */
  releaseIn?: number
  /** Show the reed line along the bottom. Defaults to true. */
  reeds?: boolean
  /** The pointer is a bat the swarm flees. Defaults to true. */
  interactive?: boolean
  /** Face the word is sampled from. Heavy weights form cleaner letters. */
  transparent?: boolean
  fontFamily?: string
  /** Content laid over the sky (a headline, a nav). */
  children?: React.ReactNode
  /** Root height. A definite length, never a percentage. */
  height?: string
  className?: string
}

const PRESETS: { [K in MothPreset]: MothPalette } = {
  luna: { skyTop: "#04070d", skyLow: "#122033", glow: "#9fd8c4", moth: "#c9f2cf", ink: "#020305" },
  night: { skyTop: "#05070d", skyLow: "#1a2236", glow: "#4a5d86", moth: "#e6e2d6", ink: "#020305" },
  dusk: { skyTop: "#1d2b44", skyLow: "#e89a6a", glow: "#ffd2a0", moth: "#1a1210", ink: "#14100f" },
  paper: { skyTop: "#efe9dd", skyLow: "#e6ddcc", glow: "#ffffff", moth: "#2a2219", ink: "#1a1714" },
}

const SANS_STACK = '"Inter Tight", "Inter", "Helvetica Neue", Arial, system-ui, sans-serif'

// #region swarm
// Pure helpers.

/** Small seeded PRNG (mulberry32). */
export function mthRandom(seed: number) {
  let a = (seed >>> 0) || 1
  return function () {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Divergence-free current at (x, y) in unit space at time t: the curl of a
 * sum of travelling waves. Returns [vx, vy]. Being a curl, it never piles moths
 * into a point or tears holes in the swarm; it only folds it.
 */
export function mthCurl(x: number, y: number, t: number) {
  return mthCurlInto([0, 0], x, y, t)
}

/** mthCurl written into out, so the per-moth loop allocates nothing. */
export function mthCurlInto(out: number[], x: number, y: number, t: number) {
  const a = 1.7 * x + t
  const b = 1.3 * y - 0.6 * t
  const c = 2.9 * y + 1.1 * x - 0.8 * t
  const d = 4.3 * x - 3.7 * y + 1.3 * t
  const dpdx = 1.7 * Math.cos(a) * Math.cos(b) + 0.6 * 1.1 * Math.cos(c) - 0.35 * 4.3 * Math.sin(d)
  const dpdy = -1.3 * Math.sin(a) * Math.sin(b) + 0.6 * 2.9 * Math.cos(c) + 0.35 * 3.7 * Math.sin(d)
  out[0] = dpdy
  out[1] = -dpdx
  return out
}

/** The swarm's wandering centre in unit space ([-1, 1] across the width). Fast. */
export function mthCentre(t: number) {
  return [0.42 * Math.sin(t * 0.55) + 0.12 * Math.sin(t * 1.4 + 1), 0.16 * Math.sin(t * 0.85 + 2) - 0.08]
}

/** Smoothstep. */
export function mthEase(k: number) {
  const x = k < 0 ? 0 : k > 1 ? 1 : k
  return x * x * (3 - 2 * x)
}

/**
 * Pair every moth with a target point. Targets are reused round-robin when
 * there are fewer points than moths, and the pairing is shuffled so the word
 * fills in from everywhere at once rather than letter by letter.
 */
export function mthAssign(moths: number, points: number, seed: number) {
  const out = new Int32Array(moths)
  if (points <= 0) return out.fill(-1)
  const order = new Int32Array(points)
  for (let i = 0; i < points; i++) order[i] = i
  const rand = mthRandom(seed)
  for (let i = points - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    const tmp = order[i]
    order[i] = order[j]
    order[j] = tmp
  }
  for (let i = 0; i < moths; i++) out[i] = order[i % points]
  return out
}

/** A colour at an alpha, for #rgb / #rrggbb; anything else is returned as-is. */
export function mthAlpha(hex: string, alpha: number) {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec((hex || "").trim())
  if (!m) return hex
  const h = m[1].length === 3 ? m[1].split("").map((c) => c + c).join("") : m[1]
  return "rgba(" + parseInt(h.slice(0, 2), 16) + "," + parseInt(h.slice(2, 4), 16) + "," + parseInt(h.slice(4, 6), 16) + "," + alpha + ")"
}
// #endregion

const MTH_CSS = `
.mth-root { position: relative; width: 100%; overflow: hidden; isolation: isolate; background: var(--mth-sky); }
.mth-root canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; max-width: none; }
.mth-over { position: relative; z-index: 1; height: 100%; pointer-events: none; }
.mth-over [data-interactive], .mth-over a, .mth-over button { pointer-events: auto; }
.mth-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
`

export default function MothWordmark({
  word = "Actias",
  transparent = false,
  moths = 1400,
  preset = "luna",
  palette,
  flyFor = 2,
  formIn = 0.8,
  breakAt = 3,
  releaseIn = 0.35,
  reeds = true,
  interactive = true,
  fontFamily = SANS_STACK,
  children,
  height = "100svh",
  className = "",
}: MothWordmarkProps) {
  const colors = { ...(PRESETS[preset] || PRESETS.luna), ...(palette || {}) }
  const rootRef = React.useRef(null as HTMLDivElement | null)
  const canvasRef = React.useRef(null as HTMLCanvasElement | null)
  const opts = React.useRef({ flyFor, formIn, breakAt, releaseIn, interactive })
  opts.current = { flyFor, formIn, breakAt, releaseIn, interactive }
  const key = [transparent, word, moths, colors.skyTop, colors.skyLow, colors.glow, colors.moth, colors.ink, reeds, fontFamily].join("|")

  React.useEffect(() => {
    const root = rootRef.current
    const canvas = canvasRef.current
    if (!root || !canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches

    let w = 1
    let h = 1
    let dpr = 1
    let n = 0
    let raf = 0
    let visible = true
    let sky = null as HTMLCanvasElement | null
    // Per moth: position, velocity (px), depth, wing phase, flutter seed, target index.
    let px = new Float32Array(0)
    let py = new Float32Array(0)
    let vx = new Float32Array(0)
    let vy = new Float32Array(0)
    let depth = new Float32Array(0)
    let phase = new Float32Array(0)
    let jit = new Float32Array(0)
    let target = new Int32Array(0)
    let tx = new Float32Array(0)
    let ty = new Float32Array(0)
    let hx = -1e5
    let hy = -1e5
    let moved = 0 // pointer travel (px) since the last frame
    let last = performance.now()
    let clock = 0
    // Swarm state: fly free → form → hold (until disturbed) → release → fly…
    let mode = 0 // 0 fly, 1 form, 2 hold, 3 release
    let modeT = 0
    let disturb = 0
    const rand = mthRandom(11)
    const curl = [0, 0]

    const layout = () => {
      const rect = root.getBoundingClientRect()
      w = Math.max(1, Math.round(rect.width))
      h = Math.max(1, Math.round(rect.height))
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      canvas.width = Math.round(w * dpr)
      canvas.height = Math.round(h * dpr)

      // Sky: a vertical gradient, a glow low on the horizon, grain.
      // Skipped when transparent: it is never drawn, and the grain alone is ~w*h/10 fillRects per resize.
      sky = transparent ? null : document.createElement("canvas")
      if (sky) {
        sky.width = canvas.width
        sky.height = canvas.height
      }
      const s = sky && sky.getContext("2d")
      if (s) {
        s.setTransform(dpr, 0, 0, dpr, 0, 0)
        const g = s.createLinearGradient(0, 0, 0, h)
        g.addColorStop(0, colors.skyTop)
        g.addColorStop(0.78, colors.skyLow)
        g.addColorStop(1, colors.skyLow)
        s.fillStyle = g
        s.fillRect(0, 0, w, h)
        const glow = s.createRadialGradient(w * 0.62, h * 0.86, 0, w * 0.62, h * 0.86, Math.max(w, h) * 0.55)
        glow.addColorStop(0, colors.glow)
        glow.addColorStop(0.25, mthAlpha(colors.glow, 0.5))
        glow.addColorStop(1, mthAlpha(colors.glow, 0))
        s.globalAlpha = 0.45
        s.fillStyle = glow
        s.fillRect(0, 0, w, h)
        s.globalAlpha = 1
        const gr = mthRandom(5)
        for (let i = 0; i < (w * h) / 10; i++) {
          s.fillStyle = gr() < 0.5 ? "rgba(255,255,255,0.03)" : "rgba(0,0,0,0.035)"
          s.fillRect(gr() * w, gr() * h, 1, 1)
        }
        if (reeds) {
          // A reed bed along the bottom, stalks leaning with a wind.
          s.strokeStyle = colors.ink
          s.fillStyle = colors.ink
          const base = h - Math.max(6, h * 0.012)
          s.fillRect(0, base, w, h - base)
          for (let x = -10; x < w + 10; x += 2 + gr() * 9) {
            if (gr() < 0.18) x += gr() * 30
            const tall = Math.pow(gr(), 1.6) * Math.max(34, h * 0.13) + 6
            const lean = (gr() - 0.3) * 10
            s.lineWidth = 0.6 + gr() * 1.2
            s.beginPath()
            s.moveTo(x, base + 2)
            s.quadraticCurveTo(x + lean * 0.3, base - tall * 0.6, x + lean, base - tall)
            s.stroke()
            if (gr() < 0.07) {
              s.beginPath()
              s.ellipse(x + lean, base - tall - 4, 1.8, 6, lean * 0.04, 0, Math.PI * 2)
              s.fill()
            }
          }
        }
      }

      // Scale the swarm to the screen, then seed it as a loose cloud.
      const want = Math.max(500, Math.round(Math.max(600, Math.min(9000, moths)) * Math.min(1, (w * h) / (1440 * 860))))
      if (want !== n) {
        n = want
        px = new Float32Array(n)
        py = new Float32Array(n)
        vx = new Float32Array(n)
        vy = new Float32Array(n)
        depth = new Float32Array(n)
        phase = new Float32Array(n)
        jit = new Float32Array(n)
        for (let i = 0; i < n; i++) {
          const a = rand() * Math.PI * 2
          const r = Math.sqrt(rand())
          px[i] = w * 0.5 + Math.cos(a) * r * w * 0.22
          py[i] = h * 0.4 + Math.sin(a) * r * h * 0.16
          depth[i] = 0.55 + rand() * 0.45
          phase[i] = rand() * Math.PI * 2
          jit[i] = rand() * 1000
        }
      }

      // The word, sampled into points on a 1x mask. The sampling step is picked
      // so there are about as many points as moths: the letters fill evenly
      // instead of leaving gaps where tiny moths can't cover.
      const m = document.createElement("canvas")
      m.width = w
      m.height = h
      const mc = m.getContext("2d", { willReadFrequently: true })
      const pts = [] as number[]
      if (mc) {
        mc.font = "900 100px " + fontFamily
        const wordW = mc.measureText(word || " ").width || 1
        const size = Math.min(((w * 0.74) / wordW) * 100, (h * 0.4) / 0.72)
        mc.font = "900 " + size + "px " + fontFamily
        mc.textAlign = "center"
        mc.textBaseline = "middle"
        mc.fillText(word || " ", w / 2, h * 0.44)
        const img = mc.getImageData(0, 0, w, h).data
        let filled = 0
        for (let i = 3; i < img.length; i += 4) if (img[i] > 128) filled++
        const step = Math.max(1.5, Math.sqrt(filled / n))
        for (let y = 0; y < h; y += step) {
          for (let x = 0; x < w; x += step) {
            const xi = Math.floor(x)
            const yi = Math.floor(y)
            if (img[(yi * w + xi) * 4 + 3] > 128) pts.push(x + (rand() - 0.5) * step, y + (rand() - 0.5) * step)
          }
        }
      }
      tx = new Float32Array(pts.filter((_, i) => i % 2 === 0))
      ty = new Float32Array(pts.filter((_, i) => i % 2 === 1))
      target = mthAssign(n, tx.length, 23)

      // Reduced motion: no flight at all, the moths simply sit in the word.
      if (reduced) {
        for (let i = 0; i < n; i++) {
          const ti = target[i]
          if (ti >= 0) {
            px[i] = tx[ti]
            py[i] = ty[ti]
          }
        }
      }
    }

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame)
      if (!visible) {
        last = now
        return
      }
      const dt = reduced ? 0 : Math.min(0.033, (now - last) / 1000)
      last = now
      clock += dt
      const o = opts.current
      const fly = Math.max(0.3, o.flyFor)
      const form = Math.max(0.2, o.formIn)
      const rel = Math.max(0.1, o.releaseIn)
      modeT += dt
      if (mode === 0 && modeT >= fly) (mode = 1), (modeT = 0)
      else if (mode === 1 && modeT >= form) (mode = 2), (modeT = 0), (disturb = 0)
      else if (mode === 3 && modeT >= rel) (mode = 0), (modeT = 0)
      // 0 → 1 while forming, 1 while holding, back to 0 on release.
      let pull = 0
      if (reduced || mode === 2) pull = 1
      else if (mode === 1) pull = mthEase(modeT / form)
      else if (mode === 3) pull = 1 - mthEase(modeT / rel)

      // Spring stiffness scales with formIn so the word lands on time;
      // damping keeps it just under critical so moths overshoot a hair and settle.
      const omega = 5.5 / form
      const kSpring = omega * omega
      const kDamp = 1.6 * omega

      const u = Math.min(w, h)
      const cen = mthCentre(clock)
      const cxp = w / 2 + cen[0] * w * 0.5
      const cyp = h * 0.42 + cen[1] * h
      const t = clock * 0.9
      const bat = o.interactive && hx > -1e4
      const batR = u * 0.16
      const free = 1 - pull
      let hits = 0
      const cv = curl

      for (let i = 0; i < n; i++) {
        const x = px[i]
        const y = py[i]
        let ax = 0
        let ay = 0
        // Free flight: a fast current folds the swarm, the centre holds it together.
        const fx = (x - w / 2) / u
        const fy = (y - h / 2) / u
        mthCurlInto(cv, fx * 2.6 + depth[i] * 0.3, fy * 2.6, t)
        ax += cv[0] * 950 * free
        ay += cv[1] * 950 * free
        const dxC = cxp - x
        const dyC = (cyp - y) * 1.6
        const dC = Math.hypot(dxC, dyC) / u
        const pullIn = dC < 0.14 ? 0.15 : 0.15 + (dC - 0.14) * 6
        ax += dxC * pullIn * 5 * free
        ay += dyC * pullIn * 3.6 * free
        // Moth flutter: an erratic, per-moth jink. Faint while the word holds,
        // so the letters shimmer without losing their edges.
        const j = jit[i] + clock * 7
        const jAmp = 1400 * free + 160 * pull
        ax += Math.sin(j * 1.7) * Math.cos(j * 0.6) * jAmp
        ay += Math.cos(j * 1.3) * Math.sin(j * 0.9) * jAmp
        // Formation: spring to the moth's point in the word.
        const ti = target[i]
        if (pull > 0 && ti >= 0) {
          ax += ((tx[ti] - x) * kSpring - vx[i] * kDamp) * pull
          ay += ((ty[ti] - y) * kSpring - vy[i] * kDamp) * pull
        }
        // The bat: a hard shove away inside its radius.
        if (bat) {
          const dx = x - hx
          const dy = y - hy
          const d2 = dx * dx + dy * dy
          if (d2 < batR * batR) {
            hits++
            const d = Math.sqrt(d2) + 0.01
            const f = (1 - d / batR) * 9000
            ax += (dx / d) * f
            ay += (dy / d) * f
          }
        }
        let nvx = (vx[i] + ax * dt) * (1 - 1.4 * dt)
        let nvy = (vy[i] + ay * dt) * (1 - 1.4 * dt)
        const sp = Math.hypot(nvx, nvy)
        const cap = 900 + 1400 * pull
        if (sp > cap) {
          nvx = (nvx / sp) * cap
          nvy = (nvy / sp) * cap
        }
        vx[i] = nvx
        vy[i] = nvy
        px[i] = x + nvx * dt
        py[i] = y + nvy * dt
        // Wing beat: slow enough to read as fluttering, not strobing.
        phase[i] += dt * (9 + sp * 0.006)
      }

      // Disturbance: pointer travel that actually ploughs through moths counts;
      // it heals slowly, so it takes a lot of sweeping to break the word.
      if (mode === 2 && !reduced) {
        if (hits > 12) disturb += moved / u
        disturb *= Math.exp(-0.25 * dt)
        if (disturb >= Math.max(0.2, o.breakAt)) (mode = 3), (modeT = 0)
      }
      moved = 0

      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      if (sky && !transparent) ctx.drawImage(sky, 0, 0)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      // Two depth passes so near moths read brighter and heavier.
      for (let pass = 0; pass < 2; pass++) {
        ctx.beginPath()
        for (let i = 0; i < n; i++) {
          const near = depth[i] > 0.78
          if ((pass === 1) !== near) continue
          // Small but readable: about 8–15 px wingspan, a touch smaller in the word.
          const s = (3 + depth[i] * 3.5) * (1 - 0.15 * pull)
          const sp = Math.hypot(vx[i], vy[i])
          // Heading: velocity when moving, otherwise a slow drift so a moth
          // hovering in a letter still has a sensible orientation.
          const dx = sp > 4 ? vx[i] / sp : 0
          const dy = sp > 4 ? vy[i] / sp : -1
          // Wings seen from above: span eases open and closed a little (gentler still in the word).
          const flap = 0.8 + 0.2 * Math.sin(phase[i]) * (1 - 0.6 * pull)
          const bx = px[i]
          const by = py[i]
          const nx = -dy * s * flap
          const ny = dx * s * flap
          const fdx = dx * s
          const fdy = dy * s
          // One side: head → forewing tip → wing trailing edge → hindwing → tail.
          // Mirrored for the other side. Both are filled in the batched path.
          for (let side = -1; side <= 1; side += 2) {
            ctx.moveTo(bx + fdx * 0.5, by + fdy * 0.5)
            ctx.lineTo(bx + nx * side * 1.15 + fdx * 0.35, by + ny * side * 1.15 + fdy * 0.35)
            ctx.lineTo(bx + nx * side * 0.8 - fdx * 0.2, by + ny * side * 0.8 - fdy * 0.2)
            ctx.lineTo(bx + nx * side * 0.55 - fdx * 0.85, by + ny * side * 0.55 - fdy * 0.85)
            ctx.lineTo(bx - fdx * 0.45, by - fdy * 0.45)
            ctx.closePath()
          }
        }
        ctx.fillStyle = colors.moth
        ctx.globalAlpha = pass === 0 ? 0.55 : 0.95
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    const onMove = (e: PointerEvent) => {
      const b = canvas.getBoundingClientRect()
      const nx = e.clientX - b.left
      const ny = e.clientY - b.top
      if (hx > -1e4) moved += Math.hypot(nx - hx, ny - hy)
      hx = nx
      hy = ny
    }
    const onLeave = () => {
      hx = -1e5
      hy = -1e5
    }

    layout()
    raf = requestAnimationFrame(frame)
    root.addEventListener("pointermove", onMove)
    root.addEventListener("pointerleave", onLeave)
    const ro = new ResizeObserver(() => layout())
    ro.observe(root)
    const io = new IntersectionObserver((entries) => {
      visible = entries[0] ? entries[0].isIntersecting : true
    })
    io.observe(root)
    let alive = true
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => {
        if (alive) layout()
      })
    }
    return () => {
      alive = false
      cancelAnimationFrame(raf)
      ro.disconnect()
      io.disconnect()
      root.removeEventListener("pointermove", onMove)
      root.removeEventListener("pointerleave", onLeave)
    }
  }, [key])

  return (
    <div
      ref={rootRef}
      className={"mth-root " + className}
      style={{ height, "--mth-sky": transparent ? "transparent" : colors.skyTop } as React.CSSProperties}
    >
      <style>{MTH_CSS}</style>
      <canvas ref={canvasRef} aria-hidden="true" />
      <span className="mth-sr">{word}</span>
      {children ? <div className="mth-over">{children}</div> : null}
    </div>
  )
}
