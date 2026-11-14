import GardenInsect from "@/components/ui/garden-insect"

const reasons = [
  {
    title: "Working from chip to screen.",
    description: "ESP32 receivers, signal filtering, and a live 3D room come together in one working system.",
  },
  {
    title: "A fresh use for familiar signals.",
    description: "We explore how changes in Wi-Fi can reveal presence, opening another path to indoor awareness.",
  },
  {
    title: "Privacy made tangible.",
    description: "We make invisible tracking risks visible and clearly label simulated decoys so the demonstration stays honest.",
  },
  {
    title: "Built for real people.",
    description: "An approachable interface turns complex radio data into a room people can explore and understand.",
  },
]

export default function WhyWinSection() {
  return (
    <section className="relative mx-auto w-full max-w-5xl px-6 py-16 md:px-16" aria-labelledby="why-win-heading">
      <GardenInsect species="lacewing" className="insect-modes" />
      <h2 id="why-win-heading" data-moth-avoid className="mb-8 text-sm font-medium uppercase tracking-[.16em] text-white/65">
        Why we should win
      </h2>
      <div className="grid grid-cols-1 gap-x-12 gap-y-8 md:grid-cols-2">
        {reasons.map(reason => (
          <div key={reason.title} data-moth-avoid className="border-t border-white/20 pt-5">
            <h3 className="mb-3 text-xl font-medium tracking-tight text-white md:text-2xl">{reason.title}</h3>
            <p className="max-w-sm text-base leading-relaxed text-white/75">{reason.description}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
