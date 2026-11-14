import React, { useEffect, useState } from "react"

interface StateData {
  laptop_ip: string | null
  phone_cal?: {
    port?: number | null
    https?: boolean
  }
}

export default function LaunchSection() {
  const [state, setState] = useState<StateData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let active = true
    fetch("/api/state")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load state")
        return res.json()
      })
      .then((data) => {
        if (active) {
          setState(data)
          setLoading(false)
        }
      })
      .catch(() => {
        if (active) {
          setError(true)
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [])

  const laptopIp = state?.laptop_ip
  const port = state?.phone_cal?.port ?? 8081
  const phoneUrl = laptopIp ? `https://${laptopIp}:${port}/` : null

  return (
    <section className="relative w-full max-w-5xl mx-auto px-6 md:px-16 py-16">
      <div data-moth-avoid className="rounded-3xl border border-white/20 bg-[#214F2E] p-8 md:p-12 shadow-2xl">
        <div className="max-w-2xl mb-8">
          <span className="font-mono text-xs uppercase tracking-widest text-[#8FD3A5] mb-2 block">
            Live Demo
          </span>
          <h2 className="text-3xl md:text-4xl font-bold tracking-tight text-white mb-4">
            Launch Moth System
          </h2>
          <p className="text-base text-white/80 leading-relaxed">
            Open the real-time audience floor plan and engineering debug console, or connect a participant phone for calibration.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-4 items-stretch sm:items-center">
          {/* Button 1: Open dashboard */}
          <a
            href="/static/dashboard.html"
            className="inline-flex items-center justify-center rounded-xl bg-white px-8 py-4 text-base font-bold text-[#12301D] shadow-lg transition-all duration-200 hover:bg-white/90 hover:scale-[1.02] active:scale-[0.98]"
          >
            Open dashboard
            <span className="ml-2 font-mono text-lg">→</span>
          </a>

          {/* Button 2: Phone setup */}
          {phoneUrl ? (
            <a
              href={phoneUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center justify-center rounded-xl border-2 border-white/30 bg-white/10 px-8 py-4 text-base font-semibold text-white backdrop-blur-sm transition-all duration-200 hover:bg-white/20 hover:border-white/50"
            >
              Phone setup
              <span className="ml-2 font-mono text-xs opacity-70">({phoneUrl})</span>
            </a>
          ) : (
            <button
              disabled={loading || (!error && laptopIp === null)}
              className="inline-flex items-center justify-center rounded-xl border border-white/20 bg-white/5 px-8 py-4 text-base font-semibold text-white/60 cursor-not-allowed"
            >
              Phone setup
            </button>
          )}
        </div>

        {/* Status / Warning messages */}
        <div className="mt-8 pt-6 border-t border-white/10">
          {loading && (
            <p className="font-mono text-xs text-white/60">
              Checking demo Wi-Fi status…
            </p>
          )}

          {!loading && !error && laptopIp === null && (
            <div className="rounded-lg border border-amber-400/30 bg-amber-500/10 p-4 text-sm text-amber-200 flex items-start gap-3">
              <span className="text-lg">⚠️</span>
              <div>
                <strong>Laptop not on demo Wi-Fi:</strong> The host laptop must join the <code className="font-mono bg-black/30 px-1 py-0.5 rounded text-white">DEMO-DONT JOIN</code> network before phones can connect for tracking.
              </div>
            </div>
          )}

          {!loading && !error && phoneUrl && (
            <p className="font-mono text-xs text-white/70">
              Phone calibration available at: <a href={phoneUrl} target="_blank" rel="noreferrer" className="underline text-[#8FD3A5] hover:text-white">{phoneUrl}</a> (enter PIN shown on dashboard)
            </p>
          )}

          {!loading && error && (
            <p className="font-mono text-xs text-white/60">
              Moth server offline or standalone preview mode. Phone page will be served over HTTPS at port 8081 when active.
            </p>
          )}
        </div>
      </div>
    </section>
  )
}

