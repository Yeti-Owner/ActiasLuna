import GardenInsect from "@/components/ui/garden-insect"
import ScrollTimeline, { Milestone } from "@/components/ui/scroll-timeline"
import LiveRoomPanel from "@/components/live-room-panel"
import PhoneMockupBasic from "@/components/ui/phone-mockups-1"

const MOTH_MILESTONES: Milestone[] = [
  {
    id: "1",
    title: "What if your Wi-Fi could tell you someone’s there without a camera?",
  },
  {
    id: "2",
    content: <LiveRoomPanel />,
  },
  {
    id: "3",
    content: <PhoneMockupBasic />,
  },
]

export default function TimelineSection() {
  return (
    <section data-moth-flight-zone="right" className="relative w-full max-w-7xl mx-auto pt-16 pb-20">
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
