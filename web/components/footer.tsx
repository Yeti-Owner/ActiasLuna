import RidgelineMountains from "@/components/ui/ridgeline-mountains"

export default function Footer() {
  return (
    <footer data-moth-avoid className="mountain-footer relative w-full overflow-hidden" aria-label="Mountain landscape">
      <RidgelineMountains
        preset="evergreen"
        palette={{
          paper: "#12301D",
          tones: ["#12301D", "#235a37", "#347d4e", "#48a368", "#64c487", "#8fd3a5", "#c8f5d6"],
        }}
        mist={0.35}
        height="320px"
        interactive={true}
        altimeter={false}
      />
    </footer>
  )
}
