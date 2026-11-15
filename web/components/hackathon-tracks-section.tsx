import { LogoCloud } from "@/components/ui/logo-cloud-2"

export default function HackathonTracksSection() {
  return (
    <section className="relative mx-auto w-full max-w-5xl px-6 py-20 md:px-16 md:py-24" aria-labelledby="hackathon-tracks-heading">
      <h2 id="hackathon-tracks-heading" className="mb-10 text-center text-2xl font-normal tracking-tight text-white/65 md:text-3xl">
        The tracks we <span className="font-semibold text-white">built for.</span>
      </h2>
      <LogoCloud data-moth-avoid className="mx-auto max-w-3xl" />
    </section>
  )
}
