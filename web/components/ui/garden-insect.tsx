import { useId } from "react"

type Species = "dragonfly" | "lacewing" | "beetle" | "moth"

/** Small botanical ornaments, kept out of the reading and interaction areas. */
export default function GardenInsect({ species, className = "" }: { species: Species; className?: string }) {
  const id = useId().replace(/:/g, "")
  return (
    <span className={`garden-insect garden-insect-${species} ${className}`} aria-hidden="true">
      <svg viewBox="0 0 100 100" fill="none">
        <defs>
          <linearGradient id={`${id}-wing`} x1="20" y1="20" x2="75" y2="75" gradientUnits="userSpaceOnUse">
            <stop stopColor="#f4efd0" stopOpacity=".7" />
            <stop offset=".55" stopColor="#cadac1" stopOpacity=".25" />
            <stop offset="1" stopColor="#93b58b" stopOpacity=".08" />
          </linearGradient>
          <linearGradient id={`${id}-shell`} x1="30" y1="35" x2="65" y2="70" gradientUnits="userSpaceOnUse">
            <stop stopColor="#b4c886" /><stop offset=".4" stopColor="#456b42" /><stop offset="1" stopColor="#142f25" />
          </linearGradient>
        </defs>
        {species === "dragonfly" && <>
          <g className="insect-wings" fill={`url(#${id}-wing)`} stroke="#e4e9c9" strokeWidth=".65">
            <path d="M49 43C33 24 8 14 7 25S31 45 49 45ZM51 43C67 24 92 14 93 25S69 45 51 45Z" />
            <path d="M48 48C30 39 10 44 15 52S37 56 49 49ZM52 48C70 39 90 44 85 52S63 56 51 49Z" />
            <path d="M48 43 13 25m38 18 36-18M48 48l-29 2m33-2 29 2" opacity=".65" />
            <path d="m24 30 1 6m9-1 1 7m42-12-1 6m-9-1-1 7" opacity=".4" />
          </g>
          <path d="M47 48 40 59m13-11 7 11m-14-16-9 5m17-5 9 5" stroke="#a2b69a" strokeWidth="1" />
          <path d="M50 40v43" stroke="#c8c491" strokeWidth="2.7" strokeLinecap="round" />
          <path d="M49 55h2m-2 6h2m-2 6h2m-2 6h2" stroke="#425b3c" strokeWidth="1.5" />
          <ellipse cx="50" cy="44" rx="3" ry="7" fill="#718563" />
          <circle cx="47.5" cy="35" r="2.5" fill="#b8c88c" /><circle cx="52.5" cy="35" r="2.5" fill="#b8c88c" />
        </>}
        {species === "lacewing" && <>
          <path d="m45 43-12-5m22 5 12-5M46 53 33 63m21-10 13 10" stroke="#c0c69a" strokeWidth=".8" />
          <g className="insect-wings" fill={`url(#${id}-wing)`} stroke="#e0e6bf" strokeWidth=".7">
            <path d="M48 36C29 31 15 54 24 80 42 77 51 55 48 36ZM52 36C71 31 85 54 76 80 58 77 49 55 52 36Z" />
            <path d="M47 40 26 76m27-36 21 36M38 48l-11 3m14 2-14 7m9 2-10 6m36-20 11 3m-14 2 14 7m-9 2 10 6" opacity=".55" />
            <path d="m33 44 3 9-5 6 3 9m33-24-3 9 5 6-3 9" opacity=".35" />
          </g>
          <path d="M50 33v37" stroke="#bac88a" strokeWidth="2" strokeLinecap="round" />
          <path d="M48 31Q35 15 31 17m21 14q13-16 17-14" stroke="#dce0b2" strokeWidth=".7" />
          <circle cx="50" cy="32" r="3" fill="#cbd6a4" />
        </>}
        {species === "beetle" && <>
          <g stroke="#b9bd8d" strokeWidth="1.7" strokeLinecap="round">
            <path d="m37 39-12-8-5 5m43 3 12-8 5 5M36 50 19 51l-4 7m49-8 17 1 4 7M37 65 25 77l-1 8m39-20 12 12 1 8M44 23l-7-9m19 9 7-9" />
          </g>
          <ellipse cx="50" cy="52" rx="20" ry="28" fill={`url(#${id}-shell)`} stroke="#9eae74" strokeWidth=".8" />
          <path d="M50 28v50M34 44q16-7 32 0" stroke="#172f25" strokeWidth="1.6" />
          <path d="M36 48q-4 17 6 25" stroke="#d3dca3" strokeWidth="1" opacity=".6" />
          <path d="M38 24q12-12 24 0l-2 9H40Z" fill="#2c4e36" stroke="#a3b57b" strokeWidth=".7" />
          <ellipse cx="50" cy="21" rx="7" ry="5" fill="#314b32" />
          <circle cx="40" cy="52" r="1.3" fill="#b6bf83" /><circle cx="60" cy="52" r="1.3" fill="#b6bf83" />
        </>}
        {species === "moth" && <>
          <g className="insect-wings" fill={`url(#${id}-wing)`} stroke="#e0e7cc" strokeWidth=".75">
            <path d="M48 42Q25 18 8 22C8 45 21 53 40 52 25 56 24 72 37 70L34 91Q47 78 48 53ZM52 42Q75 18 92 22C92 45 79 53 60 52 75 56 76 72 63 70L66 91Q53 78 52 53Z" />
            <path d="M15 28 43 48m42-20L57 48M31 60l11-5m27 5-11-5" opacity=".6" />
            <circle cx="31" cy="40" r="3" stroke="#b7c58c" /><circle cx="69" cy="40" r="3" stroke="#b7c58c" />
          </g>
          <path d="M50 36v29m-2-29-7-10m11 10 7-10" stroke="#d1d9b0" strokeWidth="1.6" strokeLinecap="round" />
        </>}
      </svg>
    </span>
  )
}
