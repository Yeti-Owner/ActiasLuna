import MothLogo from "@/components/ui/moth-logo"

export default function Nav() {
  return (
    <header data-moth-avoid className="sticky top-0 z-40 w-full backdrop-blur-md bg-[#2E6F40]/80 border-b border-white/10 transition-colors">
      <div className="max-w-5xl mx-auto flex h-16 items-center justify-between px-6 md:px-16">
        <a href="/" className="flex items-center gap-2 group">
          <MothLogo className="w-7 h-7 text-white transition-transform group-hover:scale-110" />
          <span className="font-bold tracking-tight text-white text-lg">
            Actias <span className="font-light text-white/70">Luna</span>
          </span>
        </a>

        <div className="flex items-center gap-4">
          <a
            href="/static/dashboard.html"
            className="rounded-lg border border-white/20 bg-white/10 px-4 py-1.5 font-serif text-xs font-medium text-white transition-all hover:bg-white/20 hover:border-white/40"
          >
            Dashboard
          </a>
        </div>
      </div>
    </header>
  )
}
