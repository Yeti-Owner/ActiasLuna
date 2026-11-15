import GardenInsect from "@/components/ui/garden-insect"

const reasons = [
  {
    title: "Security that costs less.",
    description: "A safer home shouldn’t mean a shopping list. We’re building around the Wi-Fi devices you already own.",
  },
  {
    title: "Less for hackers to steal.",
    description: "Your living room doesn’t need to be on camera. No video recordings means no private footage to leak.",
  },
  {
    title: "Just make a call.",
    description: "“Is anyone in my living room?” Our Chert call flow puts the answer in a conversation, not another dashboard.",
  },
  {
    title: "Beyond camera blind spots.",
    description: "A camera only sees where it points. Wi-Fi sensing can detect presence beyond that line of sight.",
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
