"use client"

import { useEffect } from "react"
import { startMoth, type MothFlyOptions } from "@/lib/moth-fly"

/**
 * Drop <ScrollMoth /> once, anywhere in the page (for example next to the hero).
 * It renders nothing itself; the moth is a fixed overlay that cleans up on unmount.
 */
export default function ScrollMoth(props: MothFlyOptions) {
  useEffect(() => startMoth({ src: "/static/moth/", ...props }), []) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}
