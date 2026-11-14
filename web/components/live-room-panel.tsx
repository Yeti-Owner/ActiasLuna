import { useEffect, useState } from "react"

const viewerUrl = `${window.location.protocol}//${window.location.hostname}:8080/?embed=1`

export default function LiveRoomPanel() {
  const [status, setStatus] = useState<"connecting" | "connected" | "offline">("connecting")

  useEffect(() => {
    let disposed = false
    let timer = 0
    let controller: AbortController | null = null
    const check = async () => {
      controller = new AbortController()
      const timeout = window.setTimeout(() => controller?.abort(), 3500)
      try {
        const response = await fetch("/api/state", { signal: controller.signal, cache: "no-store" })
        if (!response.ok) throw new Error("Server unavailable")
        const data = await response.json()
        if (!data.layout || !Array.isArray(data.nodes)) throw new Error("Invalid room data")
        if (!disposed) setStatus("connected")
      } catch {
        if (!disposed) setStatus("offline")
      } finally {
        clearTimeout(timeout)
        if (!disposed) timer = window.setTimeout(check, 5000)
      }
    }
    check()
    return () => { disposed = true; clearTimeout(timer); controller?.abort() }
  }, [])

  return (
    <div data-moth-avoid className="live-room-panel overflow-hidden rounded-2xl border border-white/15 bg-[#09170f] shadow-2xl">
      {status === "connected" ? (
        <iframe src={viewerUrl} title="Live 3D room" className="live-room-frame block w-full border-0" />
      ) : (
        <div className="live-room-frame flex items-center justify-center px-6 text-center text-sm text-white/60" role="status">
          {status === "connecting" ? "Loading the room…" : "Waiting for the local Luna server…"}
        </div>
      )}
    </div>
  )
}
