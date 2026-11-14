import MothLogo from "@/components/ui/moth-logo"
import RidgelineMountains from "@/components/ui/ridgeline-mountains"

export default function Footer() {
  return (
    <footer data-moth-avoid className="w-full border-t border-white/10 bg-[#12301D] pt-12 text-center relative overflow-hidden">
      <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4 px-6 md:px-16 mb-8 relative z-10">
        <div className="flex items-center gap-3">
          <MothLogo />
          <span className="font-bold tracking-tight text-white text-lg">Actias Luna</span>
        </div>
        <p className="font-serif text-xs text-white/60">
          Built for a hackathon.
        </p>
      </div>

      {/* Standalone Dithered Mountain Canvas */}
      <div className="w-full relative">
        <RidgelineMountains
          preset="evergreen"
          palette={{
            paper: "#12301D",
            tones: ["#12301D", "#235a37", "#347d4e", "#48a368", "#64c487", "#8fd3a5", "#c8f5d6"],
          }}
          mist={0.35}
          height="260px"
          interactive={true}
          altimeter={false}
        />
      </div>
    </footer>
  )
}

