import { useEffect, useRef, useState, type ReactNode } from "react"
import { motion, useInView, useReducedMotion } from "framer-motion"
import { ArrowLeft, ArrowRight, Pause, Play } from "lucide-react"

export type PhoneSlide = { id: string; label: string; content: ReactNode }

export function PhoneCarousel({ slides }: { slides: PhoneSlide[] }) {
  const [active, setActive] = useState(0)
  const [playing, setPlaying] = useState(true)
  const ref = useRef<HTMLDivElement>(null)
  const visible = useInView(ref, { amount: 0.35 })
  const reduced = useReducedMotion()
  const move = (delta: number) => setActive(current => (current + delta + slides.length) % slides.length)

  useEffect(() => {
    if (!playing || !visible || reduced || slides.length < 2) return
    const timer = window.setInterval(() => {
      if (!document.hidden) setActive(current => (current + 1) % slides.length)
    }, 6500)
    return () => window.clearInterval(timer)
  }, [playing, visible, reduced, active, slides.length])

  if (!slides.length) return null

  return (
    <div ref={ref} className="phone-carousel" role="region" aria-roledescription="carousel" aria-label="Actias Assistant call walkthrough" data-moth-avoid>
      <div className="phone-carousel-stage">
        {slides.map((slide, index) => {
          let offset = (index - active + slides.length) % slides.length
          if (offset > slides.length / 2) offset -= slides.length
          const selected = offset === 0
          return (
            <motion.div
              key={slide.id}
              className="call-phone-position"
              aria-hidden={!selected}
              role="group"
              aria-roledescription="slide"
              aria-label={`${index + 1} of ${slides.length}: ${slide.label}`}
              initial={false}
              animate={{ x: offset * 195, y: selected ? 0 : 36, scale: selected ? 1 : 0.85, opacity: Math.abs(offset) > 1 ? 0 : selected ? 1 : 0.36, rotate: offset === 0 ? 0 : offset > 0 ? 5 : -5 }}
              transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 105, damping: 23, mass: 1.1 }}
              style={{ zIndex: selected ? 3 : 1, pointerEvents: selected ? "auto" : "none" }}
            >
              <div className="call-phone-frame">{slide.content}</div>
            </motion.div>
          )
        })}
      </div>
      <div className="call-carousel-caption" aria-live={playing ? "off" : "polite"}>
        <span className="call-stage-number">0{active + 1} / 0{slides.length}</span>
        <span>{slides[active].label}</span>
      </div>
      <div className="call-carousel-controls">
        <button type="button" aria-label="Previous call screen" onClick={() => { setPlaying(false); move(-1) }}><ArrowLeft size={18} /></button>
        <div className="call-carousel-dots" role="group" aria-label="Choose call screen">
          {slides.map((slide, index) => <button key={slide.id} type="button" aria-label={`Show ${slide.label}`} aria-pressed={active === index} onClick={() => { setPlaying(false); setActive(index) }}><span className={active === index ? "is-active" : ""} /></button>)}
        </div>
        <button type="button" aria-label={playing && !reduced ? "Pause call animation" : "Play call animation"} disabled={!!reduced} onClick={() => setPlaying(value => !value)}>{playing && !reduced ? <Pause size={16} /> : <Play size={16} />}</button>
        <button type="button" aria-label="Next call screen" onClick={() => { setPlaying(false); move(1) }}><ArrowRight size={18} /></button>
      </div>
    </div>
  )
}
