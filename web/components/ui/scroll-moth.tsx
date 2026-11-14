"use client"

import { useEffect } from "react"
import { startMoth, type MothFlyOptions } from "@/lib/moth-fly"

/**
 * Drop <ScrollMoth /> once on the landing page under the logo.
 * It renders nothing itself; the moth is a fixed overlay that cleans up on unmount.
 * The animation starts once the user scrolls under the logo.
 */
export default function ScrollMoth(props: MothFlyOptions) {
  useEffect(() => {
    const stop = startMoth({
      src: "/static/moth-luna/",
      sprite: "luna",
      width: 120,
      flapSpeed: 0.4,
      flapDepth: 0.5,
      ...props
    })

    // Hide moth while looking at the logo hero; reveal as soon as scrolling under the logo
    const handleScroll = () => {
      const heroEl = document.querySelector('[data-moth-ignore]')
      const heroBottom = heroEl ? heroEl.getBoundingClientRect().bottom : window.innerHeight
      const mothEl = document.querySelector('.moth-fly') as HTMLElement | null
      if (mothEl) {
        if (heroBottom > 100) {
          // Still in the logo hero
          mothEl.style.display = 'none'
        } else {
          // Scrolled under the logo
          mothEl.style.display = ''
        }
      }
    }

    window.addEventListener("scroll", handleScroll, { passive: true })
    handleScroll()

    return () => {
      window.removeEventListener("scroll", handleScroll)
      stop()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return null
}
