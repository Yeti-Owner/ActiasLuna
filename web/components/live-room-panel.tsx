import { useEffect, useState } from "react"

// Two hosts serve this site. server/room_viewer.py (frontend laptop) answers /api/cloud/room and serves a
// cloud-fed 3D room at /room/ on the same origin. Otherwise (npm run dev beside luna_server) the room comes from
// the local Luna server's embedded dashboard on port 8080.
const cloudViewerUrl = "/room/"
const localViewerUrl = `${window.location.protocol}//${window.location.hostname}:8080/?embed=1`

type Source = "connecting" | "cloud" | "local" | "offline"

async function probe(path: string, signal: AbortSignal, valid: (data: any) => boolean) {
  const response = await fetch(path, { signal, cache: "no-store" })
  if (!response.ok) return false
  return valid(await response.json())
}

export default function LiveRoomPanel() {
  const [source, setSource] = useState<Source>("connecting")

  useEffect(() => {
    let disposed = false
    let timer = 0
    let controller: AbortController | null = null
    const check = async () => {
      controller = new AbortController()
      const timeout = window.setTimeout(() => controller?.abort(), 3500)
      let next: Source = "offline"
      try {
        if (await probe("/api/cloud/room", controller.signal, (d) => d && Array.isArray(d.people))) next = "cloud"
      } catch { /* not the cloud viewer */ }
      if (next === "offline") {
        try {
          if (await probe("/api/state", controller.signal, (d) => d && d.layout && Array.isArray(d.nodes))) next = "local"
        } catch { /* no local server either */ }
      }
      clearTimeout(timeout)
      if (disposed) return
      setSource(next)
      // Once the cloud viewer is found the iframe polls by itself; keep probing only for the local server.
      if (next !== "cloud") timer = window.setTimeout(check, 5000)
    }
    check()
    return () => { disposed = true; clearTimeout(timer); controller?.abort() }
  }, [])

  const src = source === "cloud" ? cloudViewerUrl : source === "local" ? localViewerUrl : null
  return (
    <div id="live-room" data-moth-avoid className="live-room-panel scroll-mt-8 overflow-hidden rounded-2xl border border-white/15 bg-[#09170f] shadow-2xl">
      {src ? (
        <iframe src={src} title="Live 3D room" className="live-room-frame block w-full border-0" />
      ) : (
        <div className="live-room-frame flex items-center justify-center px-6 text-center text-sm text-white/60" role="status">
          {source === "connecting" ? "Loading the room…" : "Waiting for the room server…"}
        </div>
      )}
    </div>
  )
}
