import MothWordmark from "@/components/ui/moth-wordmark"

export default function Hero() {
  return (
    <section className="relative w-full overflow-hidden" data-moth-ignore>
      <div className="w-full">
        <MothWordmark
          word="Actias"
          transparent
          fontFamily="'Geist Serif', Georgia, serif"
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
    </section>
  )
}
