import MothWordmark from "@/components/ui/moth-wordmark"

export default function Hero() {
  return (
    <section className="relative w-full" data-moth-ignore>
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
        />
      </div>
      {/* Seamless transition to page background */}
      <div className="h-4 w-full bg-gradient-to-b from-transparent to-[#2E6F40] pointer-events-none -mt-4 relative z-10" />
    </section>
  )
}
