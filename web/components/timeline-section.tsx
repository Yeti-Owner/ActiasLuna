import ScrollTimeline, { Milestone } from "@/components/ui/scroll-timeline"
import butterflyImg from "@/public/insects/butterfly.png"

const MOTH_MILESTONES: Milestone[] = [
  {
    id: "1",
    date: "STEP 01",
    title: "Listen",
    description:
      "Each board captures only the radio header of each frame (who sent it, to which network, how strong it was) and summarises it every 250 ms.",
  },
  {
    id: "2",
    date: "STEP 02",
    title: "Map",
    description:
      "A short calibration walk teaches Actias Luna how signal strength changes across your room: walls, furniture and all.",
  },
  {
    id: "3",
    date: "STEP 03",
    title: "Track",
    description:
      "A Bayesian filter combines those readings over time with motion to pin down a position far more precisely than any single reading could.",
  },
  {
    id: "4",
    date: "STEP 04",
    title: "Show",
    description:
      "A live dashboard draws everyone on a floor plan or a LiDAR-scanned 3D model of the room.",
  },
  {
    id: "5",
    date: "BY DESIGN",
    title: "Private by default",
    description:
      "Content is never read, strangers are filtered out in hardware, the network is air-gapped, and only phones that deliberately join are tracked.",
  },
]

export default function TimelineSection() {
  return (
    <section className="relative w-full max-w-5xl mx-auto pt-16 pb-20">
      {/* Decorative Monarch Butterfly floating on the upper-right */}
      <div
        data-moth-avoid
        className="absolute top-12 right-6 md:right-16 z-10 pointer-events-none select-none transition-transform duration-500 hover:scale-110"
      >
        <div className="relative animate-butterfly">
          {/* Subtle golden ambient highlight */}
          <div className="absolute -inset-2 rounded-full bg-amber-400/20 blur-lg" />
          <img
            src={butterflyImg}
            alt="Monarch Butterfly"
            className="relative w-16 h-16 sm:w-20 sm:h-20 md:w-24 md:h-24 object-contain drop-shadow-[0_8px_16px_rgba(0,0,0,0.35)]"
          />
        </div>
      </div>

      <div className="px-6 md:px-16 mb-8 text-left">
        <span data-moth-avoid className="font-serif text-xs uppercase tracking-widest text-white/60 mb-2 inline-block">
          Architecture
        </span>
        <h2 data-moth-avoid className="text-3xl md:text-4xl font-bold tracking-tight text-white">
          How Actias Luna works
        </h2>
      </div>
      <ScrollTimeline milestones={MOTH_MILESTONES} />
    </section>
  )
}
