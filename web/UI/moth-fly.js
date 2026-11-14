// moth-fly.js: a watercolor moth that flies down the page as you scroll and weaves
// left and right through the empty space, so it never covers text, buttons or tables.
// Plain JavaScript, no dependencies, no network. Works on any static page.
//
//   import { startMoth } from "/static/moth-fly.js"
//   const stop = startMoth({ src: "/static/moth/" })   // call stop() to remove it
//
// Mark anything the moth must avoid with data-moth-avoid. Mark regions it may
// ignore (a full-screen art hero, say) with data-moth-ignore.

const DEFAULTS = {
  src: "/static/moth/",   // folder holding moth-left.png and moth-right.png
  width: 150,             // wingspan in px on a normal screen
  margin: 20,             // breathing room kept between the moth and content, in px
  top: 0.14,              // where the moth sits in the viewport at the top of the page (0..1)
  bottom: 0.78,           // ...and at the bottom of the page
  glow: true,             // soft light edge so dark wings stay visible on dark backgrounds
  zIndex: 40,
  avoid:
    "h1,h2,h3,h4,h5,h6,p,li,td,th,label,button,a,input,select,textarea,img,table,[data-moth-avoid]",
  ignore: "[data-moth-ignore]",
  images: null,           // optional { left, right } image URLs; overrides src
}

// Text-like elements are measured by their text lines, not their full block width,
// so a short heading in a wide container does not wall off the whole row.
const TEXTY = /^(H[1-6]|P|LI|TD|TH|LABEL)$/

const STYLE_ID = "moth-fly-style"
const CSS = `
.moth-fly{position:fixed;left:0;top:0;z-index:40;pointer-events:none;will-change:transform;contain:layout style;
  -webkit-user-select:none;user-select:none}
.moth-fly__bank{display:flex;align-items:center;will-change:transform}
.moth-fly__wing{display:block;will-change:transform}
.moth-fly__wing img{display:block;max-width:none;height:auto}
.moth-fly__wing--l{transform-origin:100% 50%}
.moth-fly__wing--r{transform-origin:0 50%}
.moth-fly--glow{filter:drop-shadow(0 0 7px rgba(255,255,255,.30)) drop-shadow(0 6px 9px rgba(0,0,0,.30))}
`

const clamp = (v, a, b) => (v < a ? a : v > b ? b : v)

export function startMoth(options = {}) {
  if (typeof window === "undefined" || typeof document === "undefined") return () => {}
  const o = { ...DEFAULTS, ...options }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
  const base = o.src.endsWith("/") ? o.src : o.src + "/"

  if (!document.getElementById(STYLE_ID)) {
    const st = document.createElement("style")
    st.id = STYLE_ID
    st.textContent = CSS
    document.head.appendChild(st)
  }

  // Sprite: two wing halves meeting at the body. 213 + 211 source px wide, 197 tall.
  const root = document.createElement("div")
  root.className = "moth-fly" + (o.glow ? " moth-fly--glow" : "")
  root.setAttribute("aria-hidden", "true")
  root.style.zIndex = String(o.zIndex)
  root.style.opacity = "0"
  root.innerHTML =
    '<div class="moth-fly__bank">' +
    '<div class="moth-fly__wing moth-fly__wing--l"><img alt="" src="' + (o.images ? o.images.left : base + 'moth-left.png') + '"></div>' +
    '<div class="moth-fly__wing moth-fly__wing--r"><img alt="" src="' + (o.images ? o.images.right : base + 'moth-right.png') + '"></div>' +
    "</div>"
  document.body.appendChild(root)
  const bank = root.firstChild
  const wingL = bank.children[0]
  const wingR = bank.children[1]
  const imgL = wingL.firstChild
  const imgR = wingR.firstChild

  const K0 = 1 / 420            // source px per sprite-width unit
  let baseW = o.width           // current wingspan in px
  const sizeSprite = (w) => {
    baseW = w
    imgL.style.width = 213 * K0 * w + "px"
    imgR.style.width = 211 * K0 * w + "px"
  }
  sizeSprite(o.width)
  const spriteH = () => 197 * K0 * baseW

  // ---- What to avoid: cached in document coordinates, refreshed twice a second.
  let blocks = []
  const range = document.createRange()
  const measure = () => {
    const sx = window.scrollX
    const sy = window.scrollY
    const list = []
    document.querySelectorAll(o.avoid).forEach((el) => {
      if (root.contains(el) || (o.ignore && el.closest(o.ignore))) return
      let r
      if (TEXTY.test(el.tagName)) {
        range.selectNodeContents(el)
        r = range.getBoundingClientRect()
        if (r.width < 1 || r.height < 1) r = el.getBoundingClientRect()
      } else {
        r = el.getBoundingClientRect()
      }
      if (r.width < 2 || r.height < 2) return
      list.push({ l: r.left + sx, r: r.right + sx, t: r.top + sy, b: r.bottom + sy })
    })
    blocks = list
  }
  measure()
  const remeasure = setInterval(measure, 500)
  window.addEventListener("resize", measure)
  window.addEventListener("load", measure)

  // ---- Flight state
  let x = window.innerWidth * 0.8
  let y = window.innerHeight * o.top
  let bankDeg = 0
  let flap = Math.random() * 6.28
  let speed = 0                 // smoothed scroll speed, 0..1
  let lastY = window.scrollY
  let last = performance.now()
  let clock = 0
  let shown = false
  let raf = 0

  // Free horizontal gaps in the rows the moth is about to fly through.
  const gapsFor = (docTop, docBottom, vw) => {
    const iv = []
    for (const b of blocks) if (b.b > docTop && b.t < docBottom) iv.push([b.l, b.r])
    iv.sort((a, c) => a[0] - c[0])
    const gaps = []
    let cur = 0
    for (const [l, r] of iv) {
      if (l > cur) gaps.push([cur, l])
      if (r > cur) cur = r
    }
    if (cur < vw) gaps.push([cur, vw])
    return gaps
  }

  const frame = (now) => {
    raf = requestAnimationFrame(frame)
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    clock += dt

    const vw = document.documentElement.clientWidth || window.innerWidth
    const vh = window.innerHeight
    const sy = window.scrollY
    const maxScroll = Math.max(1, document.documentElement.scrollHeight - vh)
    const progress = clamp(sy / maxScroll, 0, 1)

    // Scale down a little on phones.
    const w = clamp(o.width * (vw < 600 ? 0.72 : 1), 60, o.width)
    if (Math.abs(w - baseW) > 0.5) sizeSprite(w)

    // Scroll speed drives the wing beat.
    const dScroll = Math.abs(sy - lastY)
    lastY = sy
    speed += (clamp(dScroll / (vh * 0.04), 0, 1) - speed) * (1 - Math.exp(-dt * 6))

    // Vertical: glide from near the top of the screen to near the bottom as you scroll,
    // with a gentle hover.
    const bob = reduced ? 0 : Math.sin(clock * 1.7) * 9 + Math.sin(clock * 0.8) * 5
    const ty = vh * (o.top + (o.bottom - o.top) * progress) + bob

    // Horizontal: wander left and right, then settle in the nearest gap that fits.
    const h = spriteH()
    const docTop = sy + y - h * 0.9 - o.margin
    const docBottom = sy + y + h * 1.6 + o.margin     // look ahead (the moth is heading down)
    const gaps = gapsFor(docTop, docBottom, vw)
    const half = baseW / 2 + o.margin
    const desired = vw * 0.5 + Math.sin(clock * 0.32 + progress * 9) * vw * 0.46
    let tx = desired
    let fits = false
    let bestD = Infinity
    for (const [a, c] of gaps) {
      if (c - a >= half * 2) {
        const p = clamp(desired, a + half, c - half)
        // Crossing into another gap costs extra, so the moth stays put until it is worth it.
        const d = Math.abs(p - desired) + (x >= a && x <= c ? 0 : vw * 0.25)
        if (d < bestD) { bestD = d; tx = p; fits = true }
      }
    }
    let alpha = 1
    if (!fits) {
      // No gap is wide enough: shrink into the widest one, or fade at the right edge.
      let widest = null
      for (const g of gaps) if (!widest || g[1] - g[0] > widest[1] - widest[0]) widest = g
      if (widest && widest[1] - widest[0] > 56) {
        tx = (widest[0] + widest[1]) / 2
        const s = clamp((widest[1] - widest[0] - o.margin) / baseW, 0.4, 1)
        alpha = 0.9
        root.style.setProperty("--moth-s", String(s))
      } else {
        tx = vw - baseW * 0.15      // tuck against the right edge, half off-screen
        alpha = 0.35
      }
    }
    const sTarget = fits ? 1 : parseFloat(root.style.getPropertyValue("--moth-s") || "1")

    // Smooth motion.
    const kx = reduced ? 1 : 1 - Math.exp(-dt * 2.4)
    const ky = reduced ? 1 : 1 - Math.exp(-dt * 3.2)
    const prevX = x
    x += (tx - x) * kx
    y += (ty - y) * ky
    const vx = dt > 0 ? (x - prevX) / dt : 0
    bankDeg += (clamp(vx * 0.045, -26, 26) - bankDeg) * (1 - Math.exp(-dt * 6))

    // Wings: fold toward the body and open again.
    flap += dt * (reduced ? 0 : 22 + 24 * speed)
    const open = 0.5 + 0.5 * Math.sin(flap)
    const sx = reduced ? 1 : 1 - 0.62 * open
    wingL.style.transform = "scaleX(" + sx.toFixed(3) + ")"
    wingR.style.transform = "scaleX(" + sx.toFixed(3) + ")"

    const sc = fits ? 1 : sTarget
    root.style.transform =
      "translate3d(" + (x - baseW / 2).toFixed(1) + "px," + (y - h / 2).toFixed(1) + "px,0)"
    bank.style.transform = "rotate(" + bankDeg.toFixed(2) + "deg) scale(" + sc.toFixed(3) + ")"
    // Passing over something on the way between gaps: fade out until it is clear.
    const mw = baseW * sc * 0.5
    const mh = h * sc * 0.5
    const cx = x + window.scrollX
    const cy = y + sy
    for (const b of blocks) {
      if (cx + mw > b.l && cx - mw < b.r && cy + mh > b.t && cy - mh < b.b) { alpha = Math.min(alpha, 0.22); break }
    }
    if (!shown) { shown = true; root.style.transition = "opacity .35s ease" }
    root.style.opacity = String(alpha)
  }
  raf = requestAnimationFrame(frame)

  return function stop() {
    cancelAnimationFrame(raf)
    clearInterval(remeasure)
    window.removeEventListener("resize", measure)
    window.removeEventListener("load", measure)
    root.remove()
  }
}

export default startMoth
