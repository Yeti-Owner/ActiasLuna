import MothWordmark from "@/components/ui/moth-wordmark"

export default function Hero() {
  return (
    <section className="relative w-full">
      <div className="w-full">
        <MothWordmark
          word="Actias"
          preset="luna"
          palette={{
            skyTop: "#214F2E",   // --forest-deep
            skyLow: "#2E6F40",   // --forest: bottom of the hero meets the page background seamlessly
            glow:   "#8FD3A5",   // --forest-glow
            moth:   "#FFFFFF",   // white moths
            ink:    "#12301D",   // --forest-ink: reeds
          }}
        >
          {/* Optional overlay: keep it small and away from the middle of the screen so it never covers the word */}
          <div className="flex h-full flex-col items-center justify-end pb-24 text-center pointer-events-none">
            <p className="max-w-xl px-6 text-base text-white/80 md:text-lg select-none">
              Indoor positioning from Wi-Fi signals alone. No app, no GPS, no cameras.
            </p>
            <span className="mt-4 animate-pulse font-mono text-xs uppercase tracking-widest text-white/60 select-none">
              Scroll down ↓
            </span>
          </div>
        </MothWordmark>
      </div>
      {/* Seamless transition if needed to page background */}
      <div className="h-4 w-full bg-gradient-to-b from-transparent to-[#2E6F40] pointer-events-none -mt-4 relative z-10" />
    </section>
  )
}

