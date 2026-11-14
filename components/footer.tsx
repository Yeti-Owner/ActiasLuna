export default function Footer() {
  return (
    <footer className="w-full border-t border-white/10 bg-[#12301D] py-12 px-6 md:px-16 text-center">
      <div className="max-w-5xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="font-bold tracking-tight text-white text-lg">Moth</span>
          <span className="text-white/40">·</span>
          <span className="font-mono text-xs text-white/60">Actias Luna</span>
        </div>
        <p className="font-mono text-xs text-white/60">
          Built for a hackathon.
        </p>
      </div>
    </footer>
  )
}

