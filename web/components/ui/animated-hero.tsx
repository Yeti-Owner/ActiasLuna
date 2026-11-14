import { useEffect, useRef, useState } from "react"
import { AnimatePresence, motion, useInView, useReducedMotion } from "framer-motion"

const titles = ["visible.", "understandable.", "impossible to ignore."]

export function Hero() {
  const [titleNumber, setTitleNumber] = useState(0)
  const root = useRef<HTMLDivElement>(null)
  const inView = useInView(root, { amount: .25 })
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!inView || reduced) return
    const timer = window.setInterval(() => {
      if (!document.hidden) setTitleNumber(current => (current + 1) % titles.length)
    }, 2800)
    return () => clearInterval(timer)
  }, [inView, reduced])

  return (
    <div ref={root} className="relative mx-auto flex w-full max-w-4xl flex-col items-center px-6 pt-28 pb-12 text-center md:px-10 md:pt-40 md:pb-16">
      <h2 data-moth-avoid aria-label="We make privacy visible, understandable, and impossible to ignore." className="winning-headline w-full font-normal tracking-tight text-white">
        <span className="block">We make privacy</span>
        <span aria-hidden="true" className="relative block h-[1.35em] overflow-hidden text-[#d2e6ad]" style={{ perspective: 900 }}>
          <AnimatePresence initial={false}>
            <motion.span key={reduced ? 0 : titleNumber}
              className="absolute inset-x-0 top-0 block whitespace-nowrap font-semibold"
              initial={{ opacity: 0, y: "75%", rotateX: -55, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, rotateX: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: "-75%", rotateX: 45, filter: "blur(3px)" }}
              transition={{ type: "spring", stiffness: 85, damping: 19, mass: .8 }}
              style={{ transformOrigin: "50% 50%" }}
            >{titles[reduced ? 0 : titleNumber]}</motion.span>
          </AnimatePresence>
        </span>
      </h2>
    </div>
  )
}
