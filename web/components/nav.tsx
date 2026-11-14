import GardenInsect from "@/components/ui/garden-insect"
import VineTransition from "@/components/ui/vine-transition"

export default function Nav() {
  return (
    <div data-moth-avoid className="botanical-divider relative z-10 h-20 w-full" aria-hidden="true">
      <VineTransition />
      <GardenInsect species="beetle" className="insect-vine" />
    </div>
  )
}
