"use client"

import { useEffect, useId, useRef } from "react"

interface ScrollMothProps {
  width?: number
  flapSpeed?: number
  flapDepth?: number
}

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n))
type Block = { left: number; right: number; top: number; bottom: number }

// An exact critically damped spring: smooth even when the scroll target changes.
function glide(position: number, velocity: number, target: number, rate: number, dt: number) {
  const delta = position - target
  const spring = velocity + rate * delta
  const decay = Math.exp(-rate * dt)
  return [target + (delta + spring * dt) * decay, (velocity - rate * spring * dt) * decay]
}

export default function ScrollMoth({ width = 78, flapSpeed = .75, flapDepth = .48 }: ScrollMothProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const flightRef = useRef<HTMLDivElement>(null)
  const leftRef = useRef<SVGGElement>(null)
  const rightRef = useRef<SVGGElement>(null)
  const id = useId().replace(/:/g, "")

  useEffect(() => {
    const root = rootRef.current
    const flight = flightRef.current
    const left = leftRef.current
    const right = rightRef.current
    if (!root || !flight || !left || !right) return
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)")
    let blocks: Block[] = []
    let heroEnd = window.innerHeight
    let viewportWidth = document.documentElement.clientWidth
    let viewportHeight = window.innerHeight
    let pageEnd = document.documentElement.scrollHeight - viewportHeight
    let size = width
    let disposed = false

    const measure = () => {
      const sy = window.scrollY
      viewportWidth = document.documentElement.clientWidth
      viewportHeight = window.innerHeight
      pageEnd = Math.max(1, document.documentElement.scrollHeight - viewportHeight)
      size = Math.min(width, viewportWidth < 640 ? 58 : width)
      root.style.width = `${size}px`
      root.style.height = `${size * 1.12}px`
      const hero = document.querySelector("[data-moth-ignore]")
      heroEnd = hero ? hero.getBoundingClientRect().bottom + sy : viewportHeight
      const range = document.createRange()
      const next: Block[] = []
      document.querySelectorAll("h1,h2,h3,p,li,button,a,input,select,textarea,img,[data-moth-avoid],.garden-insect").forEach(el => {
        if (el.closest("[data-moth-ignore]") || root.contains(el)) return
        let rects: DOMRect[]
        if (/^(H[1-6]|P|LI)$/.test(el.tagName)) {
          range.selectNodeContents(el)
          rects = Array.from(range.getClientRects())
        } else rects = [el.getBoundingClientRect()]
        rects.forEach(rect => {
          if (rect.width < 2 || rect.height < 2) return
          next.push({ left: rect.left, right: rect.right, top: rect.top + sy, bottom: rect.bottom + sy })
        })
      })
      blocks = next
    }
    measure()
    const interval = window.setInterval(measure, 750)
    window.addEventListener("resize", measure)

    let x = viewportWidth - size
    let y = viewportHeight * .3
    let vx = 0, vy = 0, scale = .8, opacity = 0, bank = 0
    let clock = 0, beat = 0, scrollEnergy = 0
    let previousScroll = window.scrollY
    let last = performance.now()
    let raf = 0

    const gapsAt = (cy: number, minX = 9) => {
      const halfHeight = size * .58 + 10
      const blocked = blocks.filter(b => b.bottom > cy - halfHeight && b.top < cy + halfHeight)
        .map(b => [Math.max(0, b.left - 10), Math.min(viewportWidth, b.right + 10)])
        .sort((a, b) => a[0] - b[0])
      const gaps: number[][] = []
      let edge = minX
      for (const [l, r] of blocked) {
        if (l > edge) gaps.push([edge, l])
        edge = Math.max(edge, r)
      }
      if (edge < viewportWidth - 9) gaps.push([edge, viewportWidth - 9])
      return gaps
    }

    const frame = (now: number) => {
      raf = 0
      if (disposed || document.hidden || motion.matches) return
      const dt = Math.min(.04, Math.max(0, (now - last) / 1000))
      last = now
      clock += dt
      const sy = window.scrollY
      const progress = clamp((sy - heroEnd * .55) / Math.max(1, pageEnd - heroEnd * .55), 0, 1)
      const scrollVelocity = Math.abs(sy - previousScroll) / Math.max(.001, dt)
      previousScroll = sy
      scrollEnergy += (clamp(scrollVelocity / 1100, 0, 1) - scrollEnergy) * (1 - Math.exp(-dt * 3))
      const desiredY = viewportHeight * (.28 + progress * .36) + Math.sin(clock * .85) * 11
      // Read the current zone bounds: loading the room can change the timeline's height.
      const zone = document.querySelector('[data-moth-flight-zone="right"]')?.getBoundingClientRect()
      const keepRight = !!zone && zone.top < viewportHeight && zone.bottom > 0
      const minFlightX = keepRight ? viewportWidth * .65 : 9
      const minCenter = minFlightX + size / 2
      if (keepRight && x < minCenter) {
        // Re-enter invisibly on the right instead of drifting across the timeline moth.
        x = minCenter
        vx = 0
        opacity = 0
      }
      const desiredX = keepRight
        ? viewportWidth * (.82 + .1 * Math.sin(clock * .18 + progress * 7))
        : viewportWidth * (.5 + .38 * Math.sin(clock * .18 + progress * 7))
      let targetX = viewportWidth - size * .38
      let targetY = desiredY
      let targetScale = .68
      let best = Infinity
      let found = false

      // Pick a nearby pocket in two dimensions, with a cost for changing lanes.
      for (const offset of [0, -65, 65, -125, 125]) {
        const cy = clamp(desiredY + offset, 70, viewportHeight - 65)
        for (const [l, r] of gapsAt(cy + sy, minFlightX)) {
          const available = r - l
          if (available < size * .59 + 10) continue
          const candidateScale = Math.min(1, (available - 10) / size)
          const half = size * candidateScale / 2 + 3
          const cx = clamp(desiredX, l + half, r - half)
          const score = Math.abs(cx - desiredX) * .22 + Math.abs(cx - x) * .58
            + Math.abs(cy - desiredY) * 1.5 + (1 - candidateScale) * 110
          if (score < best) {
            best = score
            targetX = cx
            targetY = cy
            targetScale = candidateScale
            found = true
          }
        }
      }
      // Limit acceleration when a new pocket appears across the page.
      ;[x, vx] = glide(x, vx, x + clamp(targetX - x, -200, 200), 2.5, dt)
      if (keepRight && x < minCenter) { x = minCenter; vx = Math.max(0, vx) }
      ;[y, vy] = glide(y, vy, targetY, 2.8, dt)
      scale += (targetScale - scale) * (1 - Math.exp(-dt * 2.8))
      bank += (clamp(vx * .09 - vy * .025, -19, 19) - bank) * (1 - Math.exp(-dt * 3.8))

      // A steady beat, with a brief glide and tiny left/right phase variation.
      beat += dt * Math.PI * 2 * (2.3 + scrollEnergy * .7) * flapSpeed
      const glideWindow = 1 - .28 * Math.pow(.5 + .5 * Math.sin(clock * .48), 8)
      const fold = clamp(flapDepth, 0, .75) * glideWindow
      const lOpen = 1 - fold * (.5 + .5 * Math.sin(beat))
      const rOpen = 1 - fold * (.5 + .5 * Math.sin(beat + .11))
      left.setAttribute("transform", `translate(50 39) rotate(${-2 + (1 - lOpen) * 5}) scale(${lOpen} 1) translate(-50 -39)`)
      right.setAttribute("transform", `translate(50 39) rotate(${2 - (1 - rOpen) * 5}) scale(${rOpen} 1) translate(-50 -39)`)
      const lift = Math.sin(beat - .6) * 1.15
      root.style.transform = `translate3d(${(x - size / 2).toFixed(2)}px,${(y - size * .56 + lift).toFixed(2)}px,0)`
      flight.style.transform = `rotate(${bank.toFixed(2)}deg) scale(${scale.toFixed(3)})`

      let alpha = found ? .94 : .1
      // Fade during a lane change rather than pass over the timeline's own moth.
      if (keepRight) alpha *= clamp((x - minFlightX) / (size * .45), 0, 1)
      const halfW = size * scale * .48
      const halfH = size * scale * .5
      if (blocks.some(b => x + halfW > b.left && x - halfW < b.right && y + sy + halfH > b.top && y + sy - halfH < b.bottom)) alpha = .08
      // Fade in through the end of the hero instead of abruptly appearing.
      alpha *= clamp((sy + viewportHeight * .42 - heroEnd) / 130, 0, 1)
      opacity += (alpha - opacity) * (1 - Math.exp(-dt * (alpha < opacity ? 7 : 2.4)))
      root.style.opacity = opacity.toFixed(3)
      raf = requestAnimationFrame(frame)
    }
    const resume = () => {
      if (raf) cancelAnimationFrame(raf)
      raf = 0
      last = performance.now()
      previousScroll = window.scrollY
      if (!document.hidden && !motion.matches) raf = requestAnimationFrame(frame)
      else root.style.opacity = "0"
    }
    document.addEventListener("visibilitychange", resume)
    motion.addEventListener("change", resume)
    resume()
    return () => {
      disposed = true
      cancelAnimationFrame(raf)
      clearInterval(interval)
      window.removeEventListener("resize", measure)
      document.removeEventListener("visibilitychange", resume)
      motion.removeEventListener("change", resume)
    }
  }, [width, flapSpeed, flapDepth])

  return (
    <div ref={rootRef} className="luna-flyer" aria-hidden="true">
      <div ref={flightRef} className="luna-flyer-flight">
        <svg viewBox="0 0 100 112" fill="none">
          <defs>
            <linearGradient id={`${id}-silk`} x1="12" y1="15" x2="80" y2="85" gradientUnits="userSpaceOnUse">
              <stop stopColor="#f5f5db" /><stop offset=".42" stopColor="#d7e5bf" /><stop offset="1" stopColor="#83aa7b" />
            </linearGradient>
            <linearGradient id={`${id}-tail`} x1="32" y1="46" x2="38" y2="104" gradientUnits="userSpaceOnUse">
              <stop stopColor="#d8e5ba" /><stop offset="1" stopColor="#7d9b64" stopOpacity=".65" />
            </linearGradient>
          </defs>
          <g ref={leftRef}>
            <path d="M49 42C39 32 19 15 4 17 3 36 13 52 36 52L49 48Z" fill={`url(#${id}-silk)`} stroke="#e8edd0" strokeWidth=".55" />
            <path d="M47 48C29 46 19 57 25 68 28 73 34 73 37 69 38 81 32 98 27 106 41 100 46 74 46 62Z" fill={`url(#${id}-tail)`} stroke="#c8d9a7" strokeWidth=".5" />
            <path d="M48 45 9 21m38 26L15 37m29 14L28 64m14 1-9 29M27 31l-3 11m11-4-2 8" stroke="#759765" strokeWidth=".55" opacity=".65" />
            <ellipse cx="29" cy="38" rx="4.5" ry="3.2" transform="rotate(26 29 38)" fill="#a7b87a" stroke="#e6dfb2" strokeWidth="1.3" />
            <ellipse cx="29" cy="38" rx="2.2" ry="1.3" fill="#56664a" />
            <path d="M6 18Q20 18 40 35" stroke="#c1a891" strokeWidth="1.3" opacity=".75" />
          </g>
          <g ref={rightRef}>
            <path d="M51 42C61 32 81 15 96 17 97 36 87 52 64 52L51 48Z" fill={`url(#${id}-silk)`} stroke="#e8edd0" strokeWidth=".55" />
            <path d="M53 48C71 46 81 57 75 68 72 73 66 73 63 69 62 81 68 98 73 106 59 100 54 74 54 62Z" fill={`url(#${id}-tail)`} stroke="#c8d9a7" strokeWidth=".5" />
            <path d="M52 45 91 21m-38 26 32-10M56 51l16 13m-14 1 9 29M73 31l3 11m-11-4 2 8" stroke="#759765" strokeWidth=".55" opacity=".65" />
            <ellipse cx="71" cy="38" rx="4.5" ry="3.2" transform="rotate(-26 71 38)" fill="#a7b87a" stroke="#e6dfb2" strokeWidth="1.3" />
            <ellipse cx="71" cy="38" rx="2.2" ry="1.3" fill="#56664a" />
            <path d="M94 18Q80 18 60 35" stroke="#c1a891" strokeWidth="1.3" opacity=".75" />
          </g>
          <path d="M48 36Q39 28 40 22m12 14q9-8 8-14" stroke="#e9e6c5" strokeWidth="1.2" strokeLinecap="round" />
          <path d="m43 27-4 1m5 1-4 2m17-4 4 1m-5 1 4 2" stroke="#dce3bb" strokeWidth=".65" />
          <ellipse cx="50" cy="48" rx="3" ry="13" fill="#acbc8b" />
          <ellipse cx="50" cy="40" rx="3.5" ry="6" fill="#e6e8c9" />
          <path d="M49 51h2m-2 4h2" stroke="#7b936c" strokeWidth=".65" />
          <circle cx="50" cy="34" r="2.5" fill="#e0e1bd" />
        </svg>
      </div>
    </div>
  )
}
