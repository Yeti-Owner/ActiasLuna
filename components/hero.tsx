import MothWordmark from "@/components/ui/moth-wordmark"
import sunImg from "@/public/sun.png"

export default function Hero() {
  return (
    <section className="relative w-full overflow-hidden" data-moth-ignore>
      {/* Golden Corner Sun peeking through the top-left corner of the website */}
      <div
        data-moth-avoid
        className="absolute -top-14 -left-14 sm:-top-20 sm:-left-20 md:-top-28 md:-left-28 lg:-top-32 lg:-left-32 z-20 pointer-events-none select-none"
      >
        <div className="relative flex items-center justify-center">
          {/* Ambient golden sunbeam atmospheric bloom */}
          <div className="absolute inset-0 rounded-full bg-gradient-to-br from-yellow-300/35 via-amber-400/20 to-[#8FD3A5]/15 blur-3xl animate-pulse" />
          
          {/* Soft inner glow */}
          <div className="absolute inset-6 sm:inset-8 rounded-full bg-yellow-200/25 blur-2xl" />

          {/* Textured watercolor sun with radiating rays, slowly rotating */}
          <img
            src={sunImg}
            alt="Golden watercolor sun peeking from the top-left corner"
            className="relative w-44 h-44 sm:w-56 sm:h-56 md:w-72 md:h-72 lg:w-80 lg:h-80 object-contain animate-sun-peek drop-shadow-[0_0_20px_rgba(253,224,71,0.55)] drop-shadow-[0_0_50px_rgba(245,158,11,0.35)]"
          />
        </div>
      </div>

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
