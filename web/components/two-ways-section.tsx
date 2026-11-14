import GardenInsect from "@/components/ui/garden-insect"

export default function TwoWaysSection() {
  return (
    <section className="relative w-full max-w-5xl mx-auto px-6 md:px-16 py-16">
      <GardenInsect species="lacewing" className="insect-modes" />

      <div className="mb-10 text-left">
        <span data-moth-avoid className="font-serif text-xs uppercase tracking-widest text-white/60 mb-2 inline-block">
          Operation Modes
        </span>
        <h2 data-moth-avoid className="text-3xl md:text-4xl font-bold tracking-tight text-white">
          Two ways to see someone
        </h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        {/* Card 1: Connected */}
        <div data-moth-avoid className="rounded-2xl border border-white/20 bg-[#102b1b]/75 backdrop-blur-xl p-8 flex flex-col justify-between shadow-xl transition-all duration-300 hover:border-white/40">
          <div>
            <div className="flex items-center justify-between mb-4">
              <span className="inline-block rounded-full border border-white/25 bg-white/10 px-3 py-1 font-serif text-xs tracking-widest text-white/80">
                MODE 01
              </span>
              <span className="font-serif text-xs text-[#8FD3A5]">Standard</span>
            </div>
            <h3 className="text-2xl font-bold text-white mb-3">
              Connected: phone joins demo Wi-Fi
            </h3>
            <p className="text-base leading-relaxed text-white/80 mb-4">
              A participant joins the clearly named network <strong className="font-serif text-white bg-black/20 px-1.5 py-0.5 rounded">DEMO-DONT JOIN</strong>. Three boards listen to the frames that phone sends and measure how strong each one is.
            </p>
            <p className="text-base leading-relaxed text-white/80">
              The laptop turns those readings into a position, with an uncertainty circle and a trail. Opening the optional phone page adds steps and compass heading, which roughly doubles accuracy.
            </p>
          </div>
          <div className="mt-8 pt-6 border-t border-white/10 flex items-center justify-between font-serif text-xs text-white/70">
            <span>Accuracy: ~0.5 m</span>
            <span>Motion + RSSI</span>
          </div>
        </div>

        {/* Card 2: Not connected */}
        <div data-moth-avoid className="rounded-2xl border border-white/20 bg-[#102b1b]/75 backdrop-blur-xl p-8 flex flex-col justify-between shadow-xl transition-all duration-300 hover:border-white/40">
          <div>
            <div className="flex items-center justify-between mb-4">
              <span className="inline-block rounded-full border border-white/25 bg-white/10 px-3 py-1 font-serif text-xs tracking-widest text-white/80">
                MODE 02
              </span>
              <span className="font-serif text-xs text-white/60 italic">Experimental</span>
            </div>
            <h3 className="text-2xl font-bold text-white mb-3">
              Not connected: device-free sensing
            </h3>
            <p className="text-base leading-relaxed text-white/80 mb-4">
              No phone, no app, no Wi-Fi login. The boards ping each other many times a second, and a human body blocks and disturbs those radio links.
            </p>
            <p className="text-base leading-relaxed text-white/80">
              Actias Luna learns how each link reacts to where a person stands and tracks them from the disturbance pattern. <span className="italic opacity-90">(Experimental: one person at a time.)</span>
            </p>
          </div>
          <div className="mt-8 pt-6 border-t border-white/10 flex items-center justify-between font-serif text-xs text-white/70">
            <span>Requires 0 devices</span>
            <span>Mesh RF shadow</span>
          </div>
        </div>
      </div>
    </section>
  )
}
