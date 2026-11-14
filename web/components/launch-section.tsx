import { Hero } from "@/components/ui/animated-hero"
import VineTransition from "@/components/ui/vine-transition"

export default function LaunchSection() {
  return (
    <section className="relative w-full" aria-label="Why Actias Luna should win">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-0" aria-hidden="true">
        <VineTransition />
      </div>
      <Hero />
    </section>
  )
}
