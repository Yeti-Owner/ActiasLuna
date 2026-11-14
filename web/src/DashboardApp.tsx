import React, { useEffect, useRef, useState, useMemo } from 'react'
import { GeistSans } from 'geist/font/sans'
import { GeistMono } from 'geist/font/mono'
import { GeistPixel } from 'geist/font/pixel/variable'
import { GeistPixelSquare, GeistPixelGrid, GeistPixelCircle, GeistPixelTriangle, GeistPixelLine } from 'geist/font/pixel'
import { GeistSerif, GeistSerifRegular, GeistSerifSquare, GeistSerifTriangle, GeistSerifHeart } from 'geist/font/serif'

// Conversion helper: metres to feet
const M_TO_FT = 3.28084
function toFt(m: number): number {
  return m * M_TO_FT
}
function fmtFt(m: number): string {
  return `${(m * M_TO_FT).toFixed(1)} ft`
}

// Interfaces based on HANDOFF.md §3 and §4
export interface ReceiverNode {
  id: string
  label: string
  role: 'ap' | 'rx'
  status: 'online' | 'stale' | 'offline'
  age: number | null
  ip: string
  fw: string
  pkt_rate: number
  frame_rate: number
  loss_pct: number
  reboots: number
}

export interface TrackedPhone {
  mac: string
  mac_fmt: string
  name: string
  ip: string
  age: number
  est: {
    x: number
    y: number
    unc: number
    kind: 'estimated' | 'weak' | 'stale'
    receivers: number
    raw?: number[]
  } | null
  per_node?: Record<string, { r: number; rate: number }>
  trail?: [number, number, number, number, string][]
  spark?: [number, string, number][]
}

export interface ScanObject {
  label: string
  footprint: [number, number][]
  height?: number
}

export interface ScanOpening {
  kind: 'door' | 'window' | 'opening'
  footprint: [[number, number], [number, number]]
}

export interface ScanData {
  polygon: [number, number][]
  objects: ScanObject[]
  openings: ScanOpening[]
  transform?: { x0: number; z0: number; floor_y: number }
  keep_nodes?: string[]
}

export interface SnapshotState {
  now: number
  ssid: string
  laptop_ip: string | null
  view: 'live' | 'replay'
  nodes: ReceiverNode[]
  phones: TrackedPhone[]
  body: {
    x: number
    y: number
    unc: number
    present: boolean
    trail?: [number, number, number, number, string][]
  } | null
  crowd: {
    on: boolean
    people: { mac: string; x: number; y: number; trail?: [number, number, number][] }[]
  } | null
  layout: {
    name: string
    polygon: [number, number][]
    receivers: Record<string, { x: number; y: number }>
  }
  calib: {
    spots: { i: number; x: number; y: number; kind: string; group: string | null; orientation?: string; frames?: number }[]
    tests: { x: number; y: number; errors_m: number[] }[]
    test_summary?: { windows: number; median_m: number; p80_m: number }
    evaluation?: {
      loo?: { median_m: number; p80_m: number }
      model_only?: { median_m: number }
    }
    map?: { eta: number; rms_db: number; offsets_db: Record<string, number> }
    df?: { empty: boolean }
  }
  events: [number, string][]
  stations?: { mac: string; ip: string; nm: string; infra: boolean }[]
  links?: { tx: string; rx: string; r: number }[]
  packets: number
  bad_packets: number
  recording?: { on: boolean; file: string; packets: number }
  phone_cal?: { pin: string; port: number | null; https: boolean }
  settings: Record<string, boolean>
  settings_info: Record<string, [string, string]>
  lab?: {
    state: 'idle' | 'running' | 'empty' | 'error' | 'done'
    error?: string
    rows?: {
      name: string
      still_med: number | null
      still_p90: number | null
      walk_med: number | null
      walk_p90: number | null
      trail_med: number | null
      jumps: number | null
      checks: [number, number, number][]
      check_worst: number | null
    }[]
  }
  capture?: {
    kind: 'calibrate' | 'test' | 'walk' | 'empty'
    phase: 'countdown' | 'recording' | 'done'
    elapsed: number
    duration: number
  }
  replay?: {
    file: string
    t0: number
    t1: number
    pos: number
    speed: number
    state: 'playing' | 'paused' | 'finished' | 'stopped'
  }
}

// Fallback demo state if server is not yet running
const DEFAULT_DEMO_STATE: SnapshotState = {
  now: Date.now() / 1000,
  ssid: "DEMO-DONT JOIN",
  laptop_ip: "192.168.4.3",
  view: "live",
  nodes: [
    { id: "node_a", label: "A", role: "ap", status: "online", age: 0.1, ip: "192.168.4.1", fw: "0.5.0", pkt_rate: 6.4, frame_rate: 52.1, loss_pct: 0.0, reboots: 0 },
    { id: "node_b", label: "B", role: "rx", status: "online", age: 0.2, ip: "192.168.4.11", fw: "0.5.0", pkt_rate: 5.8, frame_rate: 49.3, loss_pct: 0.1, reboots: 0 },
    { id: "node_c", label: "C", role: "rx", status: "online", age: 0.1, ip: "192.168.4.12", fw: "0.5.0", pkt_rate: 6.1, frame_rate: 50.8, loss_pct: 0.0, reboots: 0 }
  ],
  phones: [
    {
      mac: "5a1111111111",
      mac_fmt: "5a:11:11:11:11:11",
      name: "Alex",
      ip: "192.168.4.2",
      age: 0.1,
      est: { x: 4.8, y: 2.6, unc: 0.45, kind: "estimated", receivers: 3 },
      per_node: {
        node_a: { r: -42.0, rate: 52.1 },
        node_b: { r: -49.5, rate: 49.3 },
        node_c: { r: -53.2, rate: 50.8 }
      },
      trail: [
        [Date.now()/1000 - 12, 2.5, 1.8, 0.6, "estimated"],
        [Date.now()/1000 - 9, 3.2, 2.1, 0.5, "estimated"],
        [Date.now()/1000 - 6, 4.0, 2.4, 0.48, "estimated"],
        [Date.now()/1000 - 3, 4.5, 2.5, 0.46, "estimated"],
        [Date.now()/1000, 4.8, 2.6, 0.45, "estimated"]
      ],
      spark: [
        [-25, "node_a", -45], [-20, "node_a", -44], [-15, "node_a", -43], [-10, "node_a", -42], [-5, "node_a", -42], [0, "node_a", -42]
      ]
    }
  ],
  body: null,
  crowd: null,
  layout: {
    name: "Main Room",
    polygon: [[0, 0], [7.5, 0], [7.5, 5.0], [0, 5.0]],
    receivers: {
      node_a: { x: 0.5, y: 0.5 },
      node_b: { x: 7.0, y: 0.5 },
      node_c: { x: 3.8, y: 4.5 }
    }
  },
  calib: {
    spots: [
      { i: 0, x: 1.5, y: 1.5, kind: "walk", group: "walk_1", frames: 320 },
      { i: 1, x: 3.5, y: 1.5, kind: "walk", group: "walk_1", frames: 340 },
      { i: 2, x: 5.5, y: 2.5, kind: "walk", group: "walk_1", frames: 310 }
    ],
    tests: [{ x: 4.5, y: 2.5, errors_m: [0.38, 0.44] }],
    test_summary: { windows: 4, median_m: 0.42, p80_m: 0.61 },
    evaluation: {
      loo: { median_m: 0.48, p80_m: 0.72 },
      model_only: { median_m: 1.45 }
    },
    map: { eta: 2.2, rms_db: 0.9, offsets_db: { node_a: -3.5, node_b: -4.1, node_c: -2.8 } },
    df: { empty: true }
  },
  events: [
    [Date.now()/1000 - 120, "Receiver A (AP) online at 192.168.4.1"],
    [Date.now()/1000 - 110, "Receiver B online"],
    [Date.now()/1000 - 105, "Receiver C online"],
    [Date.now()/1000 - 45, "Station Alex (5a:11:11:11:11:11) joined DEMO-DONT JOIN"],
    [Date.now()/1000 - 10, "Bayesian filter locked position (unc: 0.45 m)"]
  ],
  stations: [
    { mac: "5a:11:11:11:11:11", ip: "192.168.4.2", nm: "Alex", infra: false }
  ],
  links: [
    { tx: "A", rx: "B", r: -46.2 },
    { tx: "A", rx: "C", r: -51.0 },
    { tx: "B", rx: "C", r: -48.5 }
  ],
  packets: 1420,
  bad_packets: 0,
  recording: { on: false, file: "session_20261113.jsonl", packets: 1420 },
  phone_cal: { pin: "7821", port: 8081, https: true },
  settings: { motion: true, smoothing: true },
  settings_info: {
    motion: ["Use phone motion", "Standing still holds the dot steady and steps advance it"],
    smoothing: ["Temporal Bayesian filter", "Smooth jitter without lagging sudden direction changes"]
  },
  lab: { state: "idle" }
}

const DEFAULT_SCAN_DATA: ScanData = {
  polygon: [[0, 0], [7.5, 0], [7.5, 5.0], [0, 5.0]],
  objects: [
    { label: "Desk", footprint: [[1.0, 0.5], [2.6, 0.5], [2.6, 1.3], [1.0, 1.3]], height: 0.75 },
    { label: "Meeting Table", footprint: [[4.0, 1.8], [6.2, 1.8], [6.2, 3.4], [4.0, 3.4]], height: 0.8 },
    { label: "Sofa", footprint: [[1.0, 3.8], [2.8, 3.8], [2.8, 4.6], [1.0, 4.6]], height: 0.85 }
  ],
  openings: [
    { kind: "door", footprint: [[0, 2.0], [0, 3.0]] },
    { kind: "window", footprint: [[7.5, 1.5], [7.5, 3.5]] }
  ]
}

export default function DashboardApp() {
  const [state, setState] = useState<SnapshotState>(DEFAULT_DEMO_STATE)
  const [scan, setScan] = useState<ScanData>(DEFAULT_SCAN_DATA)
  const [activeTab, setActiveTab] = useState<'audience' | 'debug'>('audience')
  const [debugSubTab, setDebugSubTab] = useState<'layout' | 'calibrate' | 'settings' | 'telemetry' | 'events'>('layout')
  const [selectedPhoneMac, setSelectedPhoneMac] = useState<string | null>(null)
  const [isLiveConnected, setIsLiveConnected] = useState(false)
  const [viewMode, setViewMode] = useState<'live' | 'replay'>('live')
  const [actionNotice, setActionNotice] = useState<string | null>(null)

  // Layout editing state
  const [layoutWidthFt, setLayoutWidthFt] = useState(25)
  const [layoutHeightFt, setLayoutHeightFt] = useState(16)
  const [editReceivers, setEditReceivers] = useState<Record<string, { x: number; y: number }>>({})

  // Notice helper
  const notify = (msg: string) => {
    setActionNotice(msg)
    setTimeout(() => setActionNotice(null), 4000)
  }

  // Connect to SSE or fallback polling
  useEffect(() => {
    let evtSource: EventSource | null = null
    let pollTimer: any = null
    let cancelled = false

    const fetchScan = () => {
      fetch('/api/scan')
        .then(r => r.ok ? r.json() : null)
        .then(data => {
          if (data && !cancelled) setScan(data)
        })
        .catch(() => {})
    }
    fetchScan()

    const setupSSE = () => {
      try {
        evtSource = new EventSource(`/api/events?view=${viewMode}`)
        evtSource.onmessage = (e) => {
          try {
            const data = JSON.parse(e.data)
            if (!cancelled) {
              setState(data)
              setIsLiveConnected(true)
            }
          } catch (err) {}
        }
        evtSource.onerror = () => {
          if (evtSource) evtSource.close()
          setIsLiveConnected(false)
          // Fallback to polling
          pollTimer = setInterval(() => {
            fetch(`/api/state?view=${viewMode}`)
              .then(r => r.ok ? r.json() : null)
              .then(data => {
                if (data && !cancelled) {
                  setState(data)
                  setIsLiveConnected(true)
                }
              })
              .catch(() => {
                if (!cancelled) setIsLiveConnected(false)
              })
          }, 500)
        }
      } catch (e) {
        setIsLiveConnected(false)
      }
    }

    setupSSE()

    return () => {
      cancelled = true
      if (evtSource) evtSource.close()
      if (pollTimer) clearInterval(pollTimer)
    }
  }, [viewMode])

  // Sync layout receivers when state updates
  useEffect(() => {
    if (state.layout?.receivers) {
      setEditReceivers(state.layout.receivers)
    }
    if (state.layout?.polygon && state.layout.polygon.length >= 4) {
      const xs = state.layout.polygon.map(p => p[0])
      const ys = state.layout.polygon.map(p => p[1])
      const wM = Math.max(...xs) - Math.min(...xs)
      const hM = Math.max(...ys) - Math.min(...ys)
      setLayoutWidthFt(Math.round(toFt(wM)))
      setLayoutHeightFt(Math.round(toFt(hM)))
    }
  }, [state.layout])

  // API Call helper
  const callApi = async (path: string, body?: any) => {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : JSON.stringify({}),
      })
      const data = await res.json()
      if (!res.ok) {
        notify(`Error: ${data.error || 'Request failed'}`)
      } else {
        notify('Action saved successfully')
      }
      return data
    } catch (e: any) {
      notify(`Server offline or error: ${e.message}`)
    }
  }

  // Calculate plain English location for a person relative to scan objects
  const getPlainLocation = (x: number, y: number, unc: number): string => {
    let nearestObj: string | null = null
    let minDist = Infinity

    for (const obj of scan.objects || []) {
      // Find center of footprint
      const cx = obj.footprint.reduce((acc, p) => acc + p[0], 0) / obj.footprint.length
      const cy = obj.footprint.reduce((acc, p) => acc + p[1], 0) / obj.footprint.length
      const dist = Math.hypot(x - cx, y - cy)
      if (dist < minDist) {
        minDist = dist
        nearestObj = obj.label
      }
    }

    const ftStr = `within ~${toFt(unc).toFixed(1)} ft`
    if (nearestObj && minDist < 2.0) {
      return `by the ${nearestObj} · ${ftStr}`
    } else if (x < 1.0 || y < 1.0 || (state.layout?.polygon && (x > 6.5 || y > 4.2))) {
      return `by the wall · ${ftStr}`
    } else {
      return `middle of the room · ${ftStr}`
    }
  }

  // Active people list (crowd takes precedence if on)
  const activePeople = useMemo(() => {
    if (state.crowd?.on && state.crowd.people) {
      return state.crowd.people.map(p => ({
        id: p.mac,
        name: `Simulated #${p.mac.slice(-4)}`,
        x: p.x,
        y: p.y,
        unc: 0.5,
        kind: 'estimated' as const,
        age: 0,
        trail: p.trail || []
      }))
    }

    const list: any[] = []
    if (state.phones) {
      for (const phone of state.phones) {
        if (phone.est) {
          list.push({
            id: phone.mac,
            name: phone.name || phone.mac_fmt,
            x: phone.est.x,
            y: phone.est.y,
            unc: phone.est.unc,
            kind: phone.est.kind,
            age: phone.age,
            trail: phone.trail || []
          })
        }
      }
    }
    if (state.body?.present) {
      list.push({
        id: 'device_free_body',
        name: 'Person (no phone)',
        x: state.body.x,
        y: state.body.y,
        unc: state.body.unc,
        kind: 'estimated' as const,
        age: 0,
        trail: state.body.trail || []
      })
    }
    return list
  }, [state.phones, state.body, state.crowd])

  // Hints
  const unplacedReceivers = state.nodes.filter(n => !state.layout?.receivers?.[n.id])
  const calibErrorFt = state.calib?.evaluation?.loo?.median_m ? toFt(state.calib.evaluation.loo.median_m).toFixed(1) : null

  return (
    <div
      className={`min-h-screen bg-[#2E6F40] text-white flex flex-col selection:bg-[#8FD3A5] selection:text-[#12301D] ${GeistSans.className}`}
      style={{
        fontFamily: "var(--font-geist-sans), 'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* Top Banner & Header */}
      <header className="border-b border-white/10 bg-[#214F2E] px-6 py-4 shadow-md sticky top-0 z-30">
        <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <a href="/" className="flex items-center gap-2 group">
              <svg className="w-6 h-6 text-white transition-transform group-hover:scale-110" viewBox="0 0 24 24" fill="currentColor">
                <path d="M12 4C11.5 2 8 2 5 4C2 6 2 10 4 13C6 16 10 17 12 15C14 17 18 16 20 13C22 10 22 6 19 4C16 2 12.5 2 12 4ZM12 8C11 8 10 10 10 12C10 14 11 15 12 15C13 15 14 14 14 12C14 10 13 8 12 8Z" />
              </svg>
              <span className="font-bold tracking-tight text-white text-xl">Moth</span>
            </a>
            <span className="text-white/40">/</span>
            <span className="font-mono text-sm text-white/80">Dashboard</span>

            {/* Live / Replay Badge */}
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-xs font-semibold ${
              viewMode === 'live' ? 'bg-emerald-500/20 text-[#8FD3A5] border border-emerald-400/30' : 'bg-purple-500/20 text-purple-200 border border-purple-400/30'
            }`}>
              <span className={`h-2 w-2 rounded-full ${viewMode === 'live' ? 'bg-[#8FD3A5] animate-pulse' : 'bg-purple-400'}`} />
              {viewMode === 'live' ? 'LIVE STREAM' : 'REPLAY'}
            </span>

            {/* Connection status indicator */}
            <span className="font-mono text-xs text-white/60">
              {isLiveConnected ? 'Connected (4 Hz)' : 'Standalone Preview'}
            </span>
          </div>

          <div className="flex items-center flex-wrap gap-3">
            {/* Wi-Fi warning if laptop_ip is null */}
            {state.laptop_ip ? (
              <span className="font-mono text-xs rounded-lg border border-white/20 bg-white/5 px-2.5 py-1 text-white/80">
                Laptop: <strong className="text-white">{state.laptop_ip}</strong>
              </span>
            ) : (
              <span className="font-mono text-xs rounded-lg border border-amber-400/40 bg-amber-500/20 px-2.5 py-1 text-amber-200">
                ⚠️ Laptop NOT on demo Wi-Fi
              </span>
            )}

            {/* Recording badge */}
            {state.recording?.on && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/20 border border-red-400/30 px-3 py-1 font-mono text-xs text-red-200 animate-pulse">
                <span className="h-2 w-2 rounded-full bg-red-400" />
                REC ({state.recording.packets} pkts)
              </span>
            )}

            {/* Live / Replay Toggle */}
            <button
              onClick={() => setViewMode(v => v === 'live' ? 'replay' : 'live')}
              className="rounded-lg border border-white/25 bg-white/10 px-3 py-1 font-mono text-xs font-medium text-white hover:bg-white/20 transition-colors"
            >
              {viewMode === 'live' ? 'Switch to Replay' : 'Switch to Live'}
            </button>

            {/* View Mode Toggle: Audience vs Setup/Debug */}
            <div className="inline-flex rounded-xl border border-white/20 bg-[#12301D] p-1">
              <button
                onClick={() => setActiveTab('audience')}
                className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-all ${
                  activeTab === 'audience' ? 'bg-white text-[#12301D] shadow' : 'text-white/80 hover:text-white'
                }`}
              >
                Audience View
              </button>
              <button
                onClick={() => setActiveTab('debug')}
                className={`rounded-lg px-4 py-1.5 text-xs font-semibold transition-all ${
                  activeTab === 'debug' ? 'bg-white text-[#12301D] shadow' : 'text-white/80 hover:text-white'
                }`}
              >
                Setup & Debug
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Notification Toast */}
      {actionNotice && (
        <div className="bg-[#12301D] border-b border-[#8FD3A5]/40 text-[#8FD3A5] px-6 py-2 text-center font-mono text-xs animate-in fade-in">
          {actionNotice}
        </div>
      )}

      {/* Main Workspace */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 flex flex-col gap-6">
        {/* Hint Line */}
        <div className="rounded-xl border border-white/15 bg-[#214F2E]/60 px-5 py-3 flex flex-wrap items-center justify-between text-xs font-mono text-white/80">
          <div className="flex items-center gap-4">
            {unplacedReceivers.length > 0 ? (
              <span className="text-amber-300">⚠️ {unplacedReceivers.length} receivers not placed</span>
            ) : (
              <span className="text-[#8FD3A5]">✓ All receivers placed</span>
            )}
            <span>·</span>
            {calibErrorFt ? (
              <span>Typical error ~{calibErrorFt} ft (Bayesian LOO)</span>
            ) : (
              <span className="text-white/60">Not calibrated yet</span>
            )}
          </div>
          <div className="text-white/60">
            SSID: <strong className="text-white">{state.ssid}</strong> · {state.packets} pkts captured
          </div>
        </div>

        {activeTab === 'audience' ? (
          /* =========================================================================
             AUDIENCE VIEW
             ========================================================================= */
          <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
            {/* Interactive Floor Plan Map (3 cols) */}
            <div className="lg:col-span-3 rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl flex flex-col">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <h2 className="text-lg font-bold text-white">Live Floor Plan</h2>
                  <p className="text-xs text-white/70">
                    Room: {state.layout?.name || "Room 1"} ({layoutWidthFt} ft × {layoutHeightFt} ft) · 1 ft grid
                  </p>
                </div>
                <div className="flex items-center gap-3 font-mono text-xs">
                  <span className="flex items-center gap-1.5 text-white/80">
                    <span className="h-3 w-3 rounded-full bg-[#8FD3A5] border border-white" /> Estimated
                  </span>
                  <span className="flex items-center gap-1.5 text-white/80">
                    <span className="h-3 w-3 rounded-full border-2 border-[#8FD3A5] bg-transparent" /> Weak
                  </span>
                  <span className="flex items-center gap-1.5 text-white/60">
                    <span className="h-3 w-3 rounded-full border border-dashed border-white/50 bg-white/20" /> Stale
                  </span>
                </div>
              </div>

              {/* Map Canvas / SVG Container */}
              <div className="relative w-full aspect-[16/10] bg-[#12301D] rounded-xl border border-white/10 overflow-hidden shadow-inner flex items-center justify-center">
                <FloorPlanSvg
                  layout={state.layout}
                  scan={scan}
                  nodes={state.nodes}
                  people={activePeople}
                  selectedPersonId={selectedPhoneMac}
                  onSelectPerson={setSelectedPhoneMac}
                />
              </div>

              {/* Status footer for map */}
              <div className="mt-4 flex flex-wrap items-center justify-between text-xs text-white/70 font-mono">
                <span>Receivers: {state.nodes.map(n => `${n.label} (${n.status})`).join(', ')}</span>
                <span>Active Trackers: {activePeople.length}</span>
              </div>
            </div>

            {/* Audience Sidebar: People List & Receivers (1 col) */}
            <div className="space-y-6">
              {/* People in Room */}
              <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-5 shadow-xl">
                <h3 className="font-bold text-base text-white mb-3 flex items-center justify-between">
                  <span>People in Room</span>
                  <span className="font-mono text-xs bg-white/10 px-2 py-0.5 rounded-full text-[#8FD3A5]">
                    {activePeople.length}
                  </span>
                </h3>

                {activePeople.length === 0 ? (
                  <p className="text-xs text-white/60 py-6 text-center italic">
                    No participants detected yet.<br />Join phone to DEMO-DONT JOIN.
                  </p>
                ) : (
                  <div className="space-y-3">
                    {activePeople.map(p => (
                      <div
                        key={p.id}
                        onClick={() => setSelectedPhoneMac(p.id)}
                        className={`rounded-xl border p-3 cursor-pointer transition-all ${
                          selectedPhoneMac === p.id ? 'border-white bg-white/15' : 'border-white/15 bg-white/5 hover:border-white/30'
                        }`}
                      >
                        <div className="flex items-center justify-between mb-1">
                          <strong className="text-sm text-white font-medium">{p.name}</strong>
                          <span className={`text-[10px] font-mono uppercase px-2 py-0.5 rounded ${
                            p.kind === 'estimated' ? 'bg-[#8FD3A5]/20 text-[#8FD3A5]' : p.kind === 'weak' ? 'border border-[#8FD3A5] text-[#8FD3A5]' : 'bg-white/10 text-white/50'
                          }`}>
                            {p.kind}
                          </span>
                        </div>
                        <p className="text-xs text-white/80">
                          {p.age > 3 ? `not heard for ${p.age.toFixed(0)} s` : getPlainLocation(p.x, p.y, p.unc)}
                        </p>
                        <div className="mt-2 text-[11px] font-mono text-white/60 flex justify-between">
                          <span>Pos: ({toFt(p.x).toFixed(1)}, {toFt(p.y).toFixed(1)}) ft</span>
                          <span>Unc: ±{toFt(p.unc).toFixed(1)} ft</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Receivers Status */}
              <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-5 shadow-xl">
                <h3 className="font-bold text-base text-white mb-3">Receivers Status</h3>
                <div className="space-y-2.5">
                  {state.nodes.map(node => (
                    <div key={node.id} className="flex items-center justify-between text-xs border-b border-white/10 pb-2">
                      <div className="flex items-center gap-2">
                        <span className="font-bold text-sm bg-white/10 w-6 h-6 rounded flex items-center justify-center font-mono">
                          {node.label}
                        </span>
                        <div>
                          <span className="font-medium text-white">{node.role.toUpperCase()} Node</span>
                          <span className="text-[10px] text-white/60 block font-mono">{node.ip}</span>
                        </div>
                      </div>
                      <div className="text-right">
                        <span className={`font-mono font-semibold text-[11px] ${
                          node.status === 'online' ? 'text-[#8FD3A5]' : node.status === 'stale' ? 'text-amber-300' : 'text-red-300'
                        }`}>
                          ● {node.status}
                        </span>
                        <span className="text-[10px] text-white/60 block font-mono">{node.frame_rate.toFixed(0)} fps</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* =========================================================================
             SETUP & DEBUG VIEW
             ========================================================================= */
          <div className="space-y-6">
            {/* Sub-tabs bar */}
            <div className="flex border-b border-white/15 gap-2 overflow-x-auto pb-2">
              {[
                { id: 'layout', label: '1. Room Layout & Receivers' },
                { id: 'calibrate', label: '2. Calibration & Accuracy' },
                { id: 'settings', label: '3. Bayesian Filters & Lab' },
                { id: 'telemetry', label: '4. Node Telemetry & Spark' },
                { id: 'events', label: '5. Events Log' }
              ].map(tab => (
                <button
                  key={tab.id}
                  onClick={() => setDebugSubTab(tab.id as any)}
                  className={`rounded-lg px-4 py-2 font-mono text-xs font-semibold whitespace-nowrap transition-all ${
                    debugSubTab === tab.id ? 'bg-white text-[#12301D]' : 'bg-white/10 text-white/70 hover:bg-white/20 hover:text-white'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* TAB 1: Layout */}
            {debugSubTab === 'layout' && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <div className="lg:col-span-2 rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                  <h3 className="text-lg font-bold text-white mb-2">Room Dimensions & Receiver Placement</h3>
                  <p className="text-xs text-white/70 mb-4">
                    Metres internally, shown in feet. Drag or type receiver coordinates.
                  </p>

                  <div className="relative w-full aspect-[16/10] bg-[#12301D] rounded-xl border border-white/10 overflow-hidden shadow-inner mb-6">
                    <FloorPlanSvg
                      layout={{
                        ...state.layout,
                        receivers: editReceivers
                      }}
                      scan={scan}
                      nodes={state.nodes}
                      people={[]}
                      editableReceivers={true}
                      onUpdateReceiverPos={(id, x, y) => {
                        setEditReceivers(prev => ({ ...prev, [id]: { x, y } }))
                      }}
                    />
                  </div>

                  <div className="flex flex-wrap gap-4 items-center justify-between">
                    <button
                      onClick={async () => {
                        const plan = await fetch('/api/plan').then(r => r.ok ? r.json() : null)
                        if (plan?.spots && plan.spots.length > 0) {
                          const updated: Record<string, { x: number; y: number }> = {}
                          for (const s of plan.spots) {
                            if (s.id) updated[s.id] = { x: s.x, y: s.y }
                          }
                          setEditReceivers(updated)
                          notify(`Suggested spots applied! Score: ${plan.mean_m?.toFixed(2)} m`)
                        } else {
                          notify('Suggestion plan received from API')
                        }
                      }}
                      className="rounded-xl border border-white/30 bg-white/10 px-5 py-2.5 font-mono text-xs font-semibold text-white hover:bg-white/20 transition-all"
                    >
                      Suggest receiver spots (/api/plan)
                    </button>

                    <button
                      onClick={() => {
                        const poly: [number, number][] = [
                          [0, 0],
                          [layoutWidthFt / M_TO_FT, 0],
                          [layoutWidthFt / M_TO_FT, layoutHeightFt / M_TO_FT],
                          [0, layoutHeightFt / M_TO_FT]
                        ]
                        callApi('/api/layout', {
                          layout: {
                            name: state.layout?.name || "Room 1",
                            polygon: poly,
                            receivers: editReceivers
                          }
                        })
                      }}
                      className="rounded-xl bg-white px-6 py-2.5 font-mono text-xs font-bold text-[#12301D] hover:bg-white/90 shadow transition-all"
                    >
                      Save Layout (/api/layout)
                    </button>
                  </div>
                </div>

                {/* Receiver List & Coord Inputs */}
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl space-y-4">
                  <h4 className="font-bold text-sm text-white">Room Size (Feet)</h4>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[11px] font-mono text-white/60 mb-1">Width (ft)</label>
                      <input
                        type="number"
                        value={layoutWidthFt}
                        onChange={e => setLayoutWidthFt(Number(e.target.value))}
                        className="w-full rounded-lg bg-black/30 border border-white/20 px-3 py-1.5 font-mono text-sm text-white"
                      />
                    </div>
                    <div>
                      <label className="block text-[11px] font-mono text-white/60 mb-1">Height (ft)</label>
                      <input
                        type="number"
                        value={layoutHeightFt}
                        onChange={e => setLayoutHeightFt(Number(e.target.value))}
                        className="w-full rounded-lg bg-black/30 border border-white/20 px-3 py-1.5 font-mono text-sm text-white"
                      />
                    </div>
                  </div>

                  <h4 className="font-bold text-sm text-white pt-2 border-t border-white/10">Receiver Positions</h4>
                  <div className="space-y-3">
                    {state.nodes.map(n => {
                      const pos = editReceivers[n.id] || { x: 0, y: 0 }
                      return (
                        <div key={n.id} className="rounded-lg border border-white/10 bg-white/5 p-3">
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-bold text-white font-mono text-xs">Node {n.label} ({n.role})</span>
                            <span className="text-[10px] text-white/50 font-mono">{n.id}</span>
                          </div>
                          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                            <div>
                              <span className="text-[10px] text-white/50 block">X (ft)</span>
                              <input
                                type="number"
                                step="0.5"
                                value={toFt(pos.x).toFixed(1)}
                                onChange={e => {
                                  const ft = parseFloat(e.target.value) || 0
                                  setEditReceivers(prev => ({ ...prev, [n.id]: { x: ft / M_TO_FT, y: pos.y } }))
                                }}
                                className="w-full rounded bg-black/20 border border-white/15 px-2 py-1 text-white"
                              />
                            </div>
                            <div>
                              <span className="text-[10px] text-white/50 block">Y (ft)</span>
                              <input
                                type="number"
                                step="0.5"
                                value={toFt(pos.y).toFixed(1)}
                                onChange={e => {
                                  const ft = parseFloat(e.target.value) || 0
                                  setEditReceivers(prev => ({ ...prev, [n.id]: { x: pos.x, y: ft / M_TO_FT } }))
                                }}
                                className="w-full rounded bg-black/20 border border-white/15 px-2 py-1 text-white"
                              />
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            )}

            {/* TAB 2: Calibrate */}
            {debugSubTab === 'calibrate' && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Phone Setup & PIN */}
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl space-y-4">
                  <h3 className="text-lg font-bold text-white">Phone Calibration Connect</h3>
                  <div className="rounded-xl border border-white/15 bg-black/20 p-5 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-white/70">Phone Setup URL</span>
                      <span className="font-mono text-xs text-[#8FD3A5]">HTTPS Only</span>
                    </div>
                    {state.laptop_ip ? (
                      <p className="font-mono text-base font-bold text-white break-all">
                        https://{state.laptop_ip}:{state.phone_cal?.port ?? 8081}/
                      </p>
                    ) : (
                      <p className="text-sm text-amber-300">
                        Laptop must join demo Wi-Fi (DEMO-DONT JOIN) before phone page is available.
                      </p>
                    )}

                    <div className="mt-4 pt-4 border-t border-white/10 flex items-center justify-between">
                      <span className="text-sm text-white/80">4-Digit Security PIN:</span>
                      <span className="font-mono text-2xl font-black tracking-widest text-[#8FD3A5] bg-[#12301D] px-4 py-1 rounded-lg border border-white/20">
                        {state.phone_cal?.pin || "----"}
                      </span>
                    </div>
                  </div>

                  <div className="pt-2">
                    <h4 className="font-bold text-sm text-white mb-2">Accuracy Evaluation</h4>
                    <div className="grid grid-cols-3 gap-3 font-mono text-center">
                      <div className="rounded-lg bg-white/5 border border-white/10 p-3">
                        <span className="text-[10px] text-white/60 block">TYPICAL ERROR</span>
                        <strong className="text-base text-white">
                          {state.calib?.evaluation?.loo?.median_m ? fmtFt(state.calib.evaluation.loo.median_m) : '---'}
                        </strong>
                      </div>
                      <div className="rounded-lg bg-white/5 border border-white/10 p-3">
                        <span className="text-[10px] text-white/60 block">P80 ERROR</span>
                        <strong className="text-base text-white">
                          {state.calib?.evaluation?.loo?.p80_m ? fmtFt(state.calib.evaluation.loo.p80_m) : '---'}
                        </strong>
                      </div>
                      <div className="rounded-lg bg-white/5 border border-white/10 p-3">
                        <span className="text-[10px] text-white/60 block">UNCALIBRATED</span>
                        <strong className="text-base text-white/60">
                          {state.calib?.evaluation?.model_only?.median_m ? fmtFt(state.calib.evaluation.model_only.median_m) : '---'}
                        </strong>
                      </div>
                    </div>
                  </div>

                  <div className="flex gap-3 pt-3">
                    <button
                      onClick={() => callApi('/api/empty/start')}
                      disabled={!!state.capture}
                      className="flex-1 rounded-xl border border-white/20 bg-white/10 py-3 font-mono text-xs font-semibold text-white hover:bg-white/20 disabled:opacity-50"
                    >
                      Record empty room (/api/empty/start)
                    </button>
                    <button
                      onClick={() => {
                        if (confirm('Delete all calibration data?')) {
                          callApi('/api/calib/clear')
                        }
                      }}
                      className="rounded-xl border border-red-400/40 bg-red-500/20 px-4 py-3 font-mono text-xs text-red-200 hover:bg-red-500/30"
                    >
                      Clear All
                    </button>
                  </div>
                </div>

                {/* Spot List */}
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                  <h3 className="text-lg font-bold text-white mb-2">Calibration Walks & Spots</h3>
                  <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
                    {state.calib?.spots && state.calib.spots.length > 0 ? (
                      state.calib.spots.map((spot) => (
                        <div key={spot.i} className="flex items-center justify-between text-xs rounded-lg border border-white/10 bg-white/5 p-3">
                          <div className="font-mono">
                            <span className="font-bold text-white">Spot #{spot.i}</span>
                            <span className="text-white/60 ml-2">({toFt(spot.x).toFixed(1)} ft, {toFt(spot.y).toFixed(1)} ft)</span>
                            <span className="text-white/40 block text-[10px]">{spot.kind} · {spot.group || "ungrouped"} · {spot.frames || 0} frames</span>
                          </div>
                          <button
                            onClick={() => callApi('/api/spot/delete', { i: spot.i })}
                            className="rounded px-2.5 py-1 font-mono text-[11px] text-red-300 hover:bg-red-500/20"
                          >
                            Delete
                          </button>
                        </div>
                      ))
                    ) : (
                      <p className="text-xs text-white/60 py-8 text-center italic">
                        No calibration spots recorded yet.<br />Use the phone page to walk the room.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* TAB 3: Settings & Lab */}
            {debugSubTab === 'settings' && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl space-y-5">
                  <h3 className="text-lg font-bold text-white">Filter & Tracking Switches</h3>
                  <div className="space-y-4">
                    {Object.entries(state.settings_info || {}).map(([key, [title, desc]]) => {
                      const isOn = !!state.settings[key]
                      return (
                        <div key={key} className="flex items-start justify-between gap-4 p-4 rounded-xl border border-white/10 bg-white/5">
                          <div>
                            <strong className="text-sm text-white block">{title}</strong>
                            <p className="text-xs text-white/70 mt-1 leading-relaxed">{desc}</p>
                          </div>
                          <button
                            onClick={() => callApi('/api/settings', { key, on: !isOn })}
                            className={`rounded-full w-12 h-6 p-0.5 transition-colors ${isOn ? 'bg-[#8FD3A5]' : 'bg-white/20'}`}
                          >
                            <div className={`h-5 w-5 rounded-full bg-white transition-transform ${isOn ? 'translate-x-6' : 'translate-x-0'}`} />
                          </button>
                        </div>
                      )
                    })}
                  </div>

                  <div className="pt-4 border-t border-white/10 flex justify-between items-center">
                    <div>
                      <strong className="text-sm text-white block">Automated Lab Benchmarking</strong>
                      <p className="text-xs text-white/70">Compare accuracy across configurations</p>
                    </div>
                    <button
                      onClick={() => callApi('/api/lab/run')}
                      className="rounded-xl bg-white px-5 py-2 font-mono text-xs font-bold text-[#12301D] hover:bg-white/90"
                    >
                      Run Lab (/api/lab/run)
                    </button>
                  </div>
                </div>

                {/* Lab Benchmarking Table */}
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                  <h3 className="text-lg font-bold text-white mb-2">Lab Comparison Results</h3>
                  {state.lab?.rows && state.lab.rows.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left font-mono text-xs">
                        <thead>
                          <tr className="border-b border-white/15 text-white/60">
                            <th className="py-2">CONFIG</th>
                            <th className="py-2">STILL (MED)</th>
                            <th className="py-2">WALK (MED)</th>
                            <th className="py-2">TRAIL (MED)</th>
                          </tr>
                        </thead>
                        <tbody>
                          {state.lab.rows.map((row, idx) => (
                            <tr key={idx} className="border-b border-white/5 hover:bg-white/5">
                              <td className="py-2.5 font-bold text-white">{row.name}</td>
                              <td className="py-2.5">{row.still_med ? fmtFt(row.still_med) : '-'}</td>
                              <td className="py-2.5">{row.walk_med ? fmtFt(row.walk_med) : '-'}</td>
                              <td className="py-2.5 text-[#8FD3A5]">{row.trail_med ? fmtFt(row.trail_med) : '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <p className="text-xs text-white/60 py-12 text-center italic">
                      No lab benchmark rows yet.<br />Click "Run Lab" to simulate across recordings.
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* TAB 4: Telemetry & Spark */}
            {debugSubTab === 'telemetry' && (
              <div className="space-y-6">
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                  <h3 className="text-lg font-bold text-white mb-4">Receiver Nodes Telemetry</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left font-mono text-xs">
                      <thead>
                        <tr className="border-b border-white/15 text-white/60">
                          <th className="py-2">NODE</th>
                          <th className="py-2">ROLE</th>
                          <th className="py-2">STATUS</th>
                          <th className="py-2">IP</th>
                          <th className="py-2">FW</th>
                          <th className="py-2">PKT RATE</th>
                          <th className="py-2">FRAME RATE</th>
                          <th className="py-2">LOSS %</th>
                          <th className="py-2">REBOOTS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {state.nodes.map(n => (
                          <tr key={n.id} className="border-b border-white/5 hover:bg-white/5">
                            <td className="py-2.5 font-bold text-white">{n.label} ({n.id.slice(-4)})</td>
                            <td className="py-2.5 uppercase">{n.role}</td>
                            <td className="py-2.5">
                              <span className={`px-2 py-0.5 rounded text-[11px] font-semibold ${
                                n.status === 'online' ? 'bg-[#8FD3A5]/20 text-[#8FD3A5]' : 'bg-red-500/20 text-red-300'
                              }`}>
                                {n.status}
                              </span>
                            </td>
                            <td className="py-2.5">{n.ip}</td>
                            <td className="py-2.5">{n.fw}</td>
                            <td className="py-2.5">{n.pkt_rate.toFixed(1)}/s</td>
                            <td className="py-2.5">{n.frame_rate.toFixed(1)}/s</td>
                            <td className="py-2.5">{n.loss_pct.toFixed(1)}%</td>
                            <td className="py-2.5">{n.reboots}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Tracked Phones RSSI per Node */}
                <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                  <h3 className="text-lg font-bold text-white mb-4">Tracked Phones RSSI & Sparkline</h3>
                  <div className="space-y-4">
                    {state.phones.map(phone => (
                      <div key={phone.mac} className="rounded-xl border border-white/10 bg-white/5 p-4">
                        <div className="flex items-center justify-between mb-3">
                          <div>
                            <strong className="text-sm text-white">{phone.name}</strong>
                            <span className="font-mono text-xs text-white/60 ml-2">({phone.mac_fmt})</span>
                          </div>
                          <span className="font-mono text-xs text-white/70">IP: {phone.ip}</span>
                        </div>
                        <div className="grid grid-cols-3 gap-3 font-mono text-xs">
                          {Object.entries(phone.per_node || {}).map(([nId, data]) => (
                            <div key={nId} className="bg-black/20 rounded p-2 border border-white/10">
                              <span className="text-[10px] text-white/50 block">Receiver {nId}</span>
                              <span className="font-bold text-[#8FD3A5]">{data.r.toFixed(1)} dBm</span>
                              <span className="text-white/60 text-[10px] ml-2">({data.rate.toFixed(0)} fps)</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* TAB 5: Events Log */}
            {debugSubTab === 'events' && (
              <div className="rounded-2xl border border-white/20 bg-[#214F2E] p-6 shadow-xl">
                <h3 className="text-lg font-bold text-white mb-3">Events Log (Last 25)</h3>
                <div className="space-y-2 font-mono text-xs max-h-96 overflow-y-auto">
                  {state.events && state.events.length > 0 ? (
                    state.events.map(([time, msg], i) => (
                      <div key={i} className="flex gap-4 border-b border-white/5 py-1.5">
                        <span className="text-white/40 shrink-0">
                          {new Date(time * 1000).toLocaleTimeString()}
                        </span>
                        <span className="text-white/90">{msg}</span>
                      </div>
                    ))
                  ) : (
                    <p className="text-white/50 py-4 italic">No events logged yet.</p>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// FloorPlanSvg Component: Draws room, grid, furniture, receivers & people
// ---------------------------------------------------------------------------
interface FloorPlanSvgProps {
  layout: SnapshotState['layout']
  scan: ScanData
  nodes: ReceiverNode[]
  people: any[]
  selectedPersonId?: string | null
  onSelectPerson?: (id: string) => void
  editableReceivers?: boolean
  onUpdateReceiverPos?: (id: string, x: number, y: number) => void
}

function FloorPlanSvg({
  layout,
  scan,
  nodes,
  people,
  selectedPersonId,
  onSelectPerson,
  editableReceivers,
  onUpdateReceiverPos
}: FloorPlanSvgProps) {
  // Determine bounding box
  const poly = layout?.polygon && layout.polygon.length >= 3 ? layout.polygon : [[0, 0], [7.5, 0], [7.5, 5.0], [0, 5.0]]
  const xs = poly.map(p => p[0])
  const ys = poly.map(p => p[1])
  const minX = Math.min(...xs, 0)
  const minY = Math.min(...ys, 0)
  const maxX = Math.max(...xs, 7.5)
  const maxY = Math.max(...ys, 5.0)

  // Add margin in metres
  const margin = 0.6
  const viewBox = `${minX - margin} ${minY - margin} ${(maxX - minX) + margin * 2} ${(maxY - minY) + margin * 2}`

  // 1-foot grid lines (0.3048 m step)
  const gridLines = useMemo(() => {
    const lines = []
    const step = 0.3048
    for (let x = minX; x <= maxX; x += step) {
      lines.push(<line key={`gx_${x}`} x1={x} y1={minY} x2={x} y2={maxY} stroke="rgba(255,255,255,0.06)" strokeWidth="0.015" />)
    }
    for (let y = minY; y <= maxY; y += step) {
      lines.push(<line key={`gy_${y}`} x1={minX} y1={y} x2={maxX} y2={y} stroke="rgba(255,255,255,0.06)" strokeWidth="0.015" />)
    }
    return lines
  }, [minX, maxX, minY, maxY])

  return (
    <svg className="w-full h-full" viewBox={viewBox} preserveAspectRatio="xMidYMid meet">
      {/* 1 ft Grid */}
      {gridLines}

      {/* Room Polygon */}
      <polygon
        points={poly.map(p => `${p[0]},${p[1]}`).join(' ')}
        fill="#12301D"
        stroke="rgba(255,255,255,0.4)"
        strokeWidth="0.06"
      />

      {/* Furniture / Scan Objects */}
      {scan.objects?.map((obj, i) => (
        <g key={`obj_${i}`}>
          <polygon
            points={obj.footprint.map(p => `${p[0]},${p[1]}`).join(' ')}
            fill="rgba(46, 111, 64, 0.45)"
            stroke="rgba(255,255,255,0.25)"
            strokeWidth="0.02"
          />
          <text
            x={obj.footprint.reduce((a, b) => a + b[0], 0) / obj.footprint.length}
            y={obj.footprint.reduce((a, b) => a + b[1], 0) / obj.footprint.length}
            fontSize="0.18"
            fill="rgba(255,255,255,0.7)"
            textAnchor="middle"
            dominantBaseline="central"
            fontFamily="monospace"
          >
            {obj.label}
          </text>
        </g>
      ))}

      {/* Openings (Doors / Windows) */}
      {scan.openings?.map((op, i) => (
        <line
          key={`op_${i}`}
          x1={op.footprint[0][0]}
          y1={op.footprint[0][1]}
          x2={op.footprint[1][0]}
          y2={op.footprint[1][1]}
          stroke={op.kind === 'door' ? '#8FD3A5' : '#93C5FD'}
          strokeWidth="0.08"
          strokeLinecap="round"
        />
      ))}

      {/* Receivers (Nodes) */}
      {nodes.map(node => {
        const pos = layout.receivers?.[node.id]
        if (!pos) return null
        const isOnline = node.status === 'online'
        const isStale = node.status === 'stale'
        const color = isOnline ? '#8FD3A5' : isStale ? '#FBBF24' : '#F87171'

        return (
          <g key={node.id} className={editableReceivers ? "cursor-move" : ""}>
            <circle cx={pos.x} cy={pos.y} r="0.18" fill="#12301D" stroke={color} strokeWidth="0.04" />
            <circle cx={pos.x} cy={pos.y} r="0.10" fill={color} />
            <text
              x={pos.x}
              y={pos.y + 0.35}
              fontSize="0.18"
              fill="#FFFFFF"
              fontWeight="bold"
              textAnchor="middle"
              fontFamily="monospace"
            >
              {node.label}
            </text>
          </g>
        )
      })}

      {/* Tracked People */}
      {people.map(person => {
        const isSelected = selectedPersonId === person.id
        const isWeak = person.kind === 'weak'
        const isStale = person.kind === 'stale'

        // Trail points
        const trailPoints = person.trail?.map((t: any) => `${t[1]},${t[2]}`).join(' ')

        return (
          <g key={person.id} onClick={() => onSelectPerson && onSelectPerson(person.id)} className="cursor-pointer">
            {/* Trail line */}
            {trailPoints && (
              <polyline
                points={trailPoints}
                fill="none"
                stroke="rgba(143, 211, 165, 0.35)"
                strokeWidth="0.03"
                strokeDasharray="0.05 0.05"
              />
            )}

            {/* Uncertainty circle (HANDOFF.md §3) */}
            <circle
              cx={person.x}
              cy={person.y}
              r={person.unc}
              fill="rgba(143, 211, 165, 0.15)"
              stroke={isStale ? "rgba(255,255,255,0.4)" : "#8FD3A5"}
              strokeWidth="0.02"
              strokeDasharray={isStale ? "0.05 0.05" : undefined}
            />

            {/* Position dot (solid for estimated, hollow for weak, greyed for stale) */}
            <circle
              cx={person.x}
              cy={person.y}
              r="0.12"
              fill={isWeak ? "#12301D" : isStale ? "#9CA3AF" : "#FFFFFF"}
              stroke={isWeak ? "#8FD3A5" : "#12301D"}
              strokeWidth="0.03"
            />

            {/* Name label */}
            <text
              x={person.x}
              y={person.y - person.unc - 0.1}
              fontSize="0.18"
              fill="#FFFFFF"
              fontWeight="bold"
              textAnchor="middle"
              fontFamily="sans-serif"
            >
              {person.name}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

