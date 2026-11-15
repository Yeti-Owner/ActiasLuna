import { useEffect, useId, useRef, useState, type CSSProperties } from "react"

type Point = [number, number]
const leafShape = "M0 0C-13-3-19-17-12-30C-7-40-2-44 0-51C3-42 12-34 14-24C19-9 9-1 0 0Z"

function curve(points: Point[], t: number): Point {
  const u = 1 - t
  return [0, 1].map(axis => u ** 3 * points[0][axis] + 3 * u ** 2 * t * points[1][axis] + 3 * u * t ** 2 * points[2][axis] + t ** 3 * points[3][axis]) as Point
}

function Branch({ length, seed, back, gradient }: { length: number; seed: number; back?: boolean; gradient: string }) {
  const bend = Math.sin(seed) * 13
  const start: Point = [0, back ? 117 : 100]
  const mid: Point = [length * .54, 87 + bend]
  const end: Point = [length, back ? 120 : 76 + bend]
  const first: Point[] = [start, [length * .2, 137], [length * .31, 49 + bend], mid]
  const second: Point[] = [mid, [length * .74, 119 + bend], [length * .87, 103], end]
  const path = `M${start}C${first[1]} ${first[2]} ${mid}C${second[1]} ${second[2]} ${end}`
  const curlStart = curve(second, .44)
  const count = Math.max(4, Math.floor(length / (back ? 61 : 43)))
  return (
    <g className={back ? "botanic-bough botanic-bough-back" : "botanic-bough"}>
      <path className="botanic-stem" d={path} pathLength="1" />
      <path className="botanic-stem-highlight" d={path} pathLength="1" />
      {Array.from({ length: count }, (_, i) => {
        const t = .04 + i / count * .85
        const segment = t < .54 ? first : second
        const localT = t < .54 ? t / .54 : (t - .54) / .46
        const [x, y] = curve(segment, localT)
        const side = (i + seed) % 2 ? 1 : -1
        const angle = side > 0 ? 31 + Math.sin(i + seed) * 16 : 139 + Math.cos(i * 2 + seed) * 15
        // Young leaves get smaller toward the growing tip.
        const size = (back ? .48 : .71) * (1 - t * .42) + Math.sin(i * 1.9 + seed) * .09
        return (
          <g key={i} transform={`translate(${x} ${y}) rotate(${angle}) scale(${size})`}>
            <g className="botanic-leaf" style={{ "--bud-delay": `${.22 + t * 1.7 + (back ? .3 : 0)}s` } as CSSProperties}>
              <path className="botanic-petiole" d="M0 4Q-1 0 0-5" />
              <path d={leafShape} fill={`url(#${gradient})`} className="botanic-blade" />
              <path d="M0 0Q-3-24 0-49" className="botanic-midrib" />
              <path d="M-1-12l-9-8m9 1 9-9M-2-26l-5-7m5 2 5-7" className="botanic-veins" />
            </g>
          </g>
        )
      })}
      <path className="botanic-curl" pathLength="1" d={`M${curlStart}q18 20 34 10c17-10 8-29-2-23s-3 17 4 11`} />
      <path className="botanic-tip" pathLength="1" d={`M${end}q13-5 17-16t-5-12`} />
    </g>
  )
}

export default function VineTransition() {
  const root = useRef<HTMLDivElement>(null)
  const [grown, setGrown] = useState(false)
  const [width, setWidth] = useState(1440)
  const id = useId().replace(/:/g, "")

  useEffect(() => {
    const element = root.current
    if (!element) return
    const observer = new IntersectionObserver(([entry]) => setGrown(entry.isIntersecting), { threshold: .35 })
    const resize = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(element)
    resize.observe(element)
    return () => { observer.disconnect(); resize.disconnect() }
  }, [])

  return (
    <div ref={root} className={`vine-transition${grown ? " is-grown" : ""}`} aria-hidden="true">
      <svg className="botanic-art" viewBox={`0 0 ${width} 180`} preserveAspectRatio="xMidYMid meet">
        <defs>
          <linearGradient id={`${id}-leaf`} x1="-15" y1="-20" x2="15" y2="-25" gradientUnits="userSpaceOnUse">
            <stop stopColor="#203f28" />
            <stop offset=".42" stopColor="#6d8d4a" />
            <stop offset=".52" stopColor="#a4b77a" />
            <stop offset=".58" stopColor="#526e39" />
            <stop offset="1" stopColor="#304e2b" />
          </linearGradient>
        </defs>
        <Branch length={width * .38} seed={3} back gradient={`${id}-leaf`} />
        <g transform={`translate(${width} -9) scale(-1 1)`}>
          <Branch length={width * .36} seed={8} back gradient={`${id}-leaf`} />
        </g>
        <g transform="translate(0 -20)">
          <Branch length={width * .44} seed={5} back gradient={`${id}-leaf`} />
        </g>
        <g transform={`translate(${width} -23) scale(-1 1)`}>
          <Branch length={width * .42} seed={11} back gradient={`${id}-leaf`} />
        </g>
        <Branch length={Math.min(width * .465, width / 2 - 27)} seed={2} gradient={`${id}-leaf`} />
        <g transform={`translate(${width} 4) scale(-1 1)`}>
          <Branch length={Math.min(width * .455, width / 2 - 31)} seed={7} gradient={`${id}-leaf`} />
        </g>
        {[.12, .28, .72, .9].map((x, i) => <circle key={x} className="botanic-firefly" cx={width * x} cy={62 + i % 2 * 60} r="1.3" style={{ animationDelay: `${i * .8}s` }} />)}
      </svg>
    </div>
  )
}
