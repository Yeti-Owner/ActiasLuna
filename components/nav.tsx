export default function Nav() {
  return (
    <header className="sticky top-0 z-40 w-full backdrop-blur-md bg-[#2E6F40]/80 border-b border-white/10 transition-colors">
      <div className="max-w-5xl mx-auto flex h-16 items-center justify-between px-6 md:px-16">
        <a href="/" className="flex items-center gap-2 group">
          {/* Small moth icon in pure white */}
          <svg className="w-6 h-6 text-white transition-transform group-hover:scale-110" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 4C11.5 2 8 2 5 4C2 6 2 10 4 13C6 16 10 17 12 15C14 17 18 16 20 13C22 10 22 6 19 4C16 2 12.5 2 12 4ZM12 8C11 8 10 10 10 12C10 14 11 15 12 15C13 15 14 14 14 12C14 10 13 8 12 8Z" />
          </svg>
          <span className="font-bold tracking-tight text-white text-lg">
            Actias <span className="font-light text-white/70">Moth</span>
          </span>
        </a>

        <div className="flex items-center gap-4">
          <a
            href="/static/dashboard.html"
            className="rounded-lg border border-white/20 bg-white/10 px-4 py-1.5 font-mono text-xs font-medium text-white transition-all hover:bg-white/20 hover:border-white/40"
          >
            Dashboard
          </a>
        </div>
      </div>
    </header>
  )
}

