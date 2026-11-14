import GardenInsect from "@/components/ui/garden-insect"
import ScrollTimeline, { Milestone } from "@/components/ui/scroll-timeline"

const MOTH_MILESTONES: Milestone[] = [
  {
    id: "1",
    title: "The problem we found",
    description:
      "Wi-Fi can reveal more than people expect. The signals a phone sends can help nearby receivers estimate where it is, even without GPS or reading its messages. In experimental setups, changes in radio signals can also reveal a person’s presence without a phone. Actias Luna makes this often invisible privacy risk visible through a local, consent-based demo.",
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
      <GardenInsect species="dragonfly" className="insect-timeline" />

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
