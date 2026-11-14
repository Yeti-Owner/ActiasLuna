const PRIVACY_ITEMS = [
  {
    title: "Content is never read",
    desc: "Payloads are never parsed or stored, and they are WPA2-encrypted anyway. Actias Luna only sees frame type, sender MAC, network ID and signal strength.",
    tag: "Radio headers only",
  },
  {
    title: "Strangers filtered in hardware",
    desc: "Firmware drops every frame not meant for the demo network before it reaches the laptop. Neighbours and passers-by are never recorded.",
    tag: "Firmware filter",
  },
  {
    title: "Explicit consent gate",
    desc: "In connected mode, only phones that deliberately joined the demo network are tracked. Leave the network and you disappear completely.",
    tag: "Opt-in",
  },
  {
    title: "Air-gapped by default",
    desc: "The demo network has no internet. Nothing is uploaded and there is no cloud infrastructure involved.",
    tag: "Zero cloud",
  },
  {
    title: "Locked-down dashboard",
    desc: "The dashboard only answers on localhost. The phone page runs over HTTPS and needs a 4-digit PIN, with lockout after repeated wrong guesses.",
    tag: "PIN protected",
  },
  {
    title: "No secrets in code",
    desc: "Wi-Fi credentials and certificates are generated from one config file and never committed to the repository.",
    tag: "Local certs",
  },
  {
    title: "Honest about device-free mode",
    desc: "Sensing without a phone sees anyone in the room, so Actias Luna makes it an explicit opt-in switch. Tell people when it's on.",
    tag: "Explicit switch",
  },
]

export default function SecurityPrivacySection() {
  return (
    <section className="relative w-full max-w-5xl mx-auto px-6 md:px-16 py-16">
      <div className="mb-10 text-left">
        <span data-moth-avoid className="font-serif text-xs uppercase tracking-widest text-white/60 mb-2 inline-block">
          Trust & Safety
        </span>
        <h2 data-moth-avoid className="text-3xl md:text-4xl font-bold tracking-tight text-white">
          Security and privacy by design
        </h2>
        <p className="mt-2 text-base text-white/80 max-w-2xl">
          Actias Luna is built to show what Wi-Fi sensing can do, without becoming a surveillance tool itself.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {PRIVACY_ITEMS.map((item, i) => (
          <div
            data-moth-avoid
            key={i}
            className={`rounded-xl border border-white/20 bg-[#102b1b]/75 backdrop-blur-xl p-6 flex flex-col justify-between transition-all duration-300 hover:border-white/40 ${
              i === 6 ? "md:col-span-2 lg:col-span-1" : ""
            }`}
          >
            <div>
              <div className="mb-3">
                <span className="inline-block rounded-full border border-white/20 bg-white/5 px-2.5 py-0.5 font-serif text-[11px] tracking-wider text-[#8FD3A5]">
                  {item.tag}
                </span>
              </div>
              <h3 className="text-lg font-bold text-white mb-2">
                {item.title}
              </h3>
              <p className="text-sm leading-relaxed text-white/80">
                {item.desc}
              </p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
