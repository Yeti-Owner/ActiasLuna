import type { ReactNode } from "react"
import { AudioLines, BatteryFull, Check, Mic, Phone, PhoneOff, Signal, Volume2, Wifi } from "lucide-react"
import { PhoneCarousel } from "@/components/ui/phone-mockups-1-utils/phone-carousel"

function CallScreen({ stage, children }: { stage: number; children?: ReactNode }) {
  return (
    <div className={`call-screen call-screen-${stage}`}>
      <div className="call-status" aria-hidden="true"><span>9:41</span><div className="call-island" /><span><Signal size={12} /><Wifi size={12} /><BatteryFull size={17} /></span></div>
      <div className="call-provider"><img src="/static/tracks/chert.png" alt="" /> Chert <span>·</span> {stage === 0 ? "outgoing call" : "voice call"}</div>
      <div className={`call-avatar ${stage === 0 ? "is-ringing" : ""}`} aria-hidden="true">
        <svg viewBox="0 0 80 80" fill="none"><path d="M39 36C27 14 8 15 13 35c3 12 15 12 23 7-16 5-16 20-5 18l6-12-1 25 5-23 4 23-1-25 7 12c11 2 11-13-5-18 8 5 20 5 23-7 5-20-14-21-26 1" fill="#e1efbc"/><path d="m37 31-5-9m11 9 5-9M20 27l15 10m25-10L45 37" stroke="#7c9a72" strokeWidth="1.5"/><ellipse cx="40" cy="41" rx="3" ry="12" fill="#c5d796"/></svg>
      </div>
      <h4>Actias Assistant</h4>
      <p className="call-connection">{stage === 0 ? "Calling through Chert…" : <><span /> Connected · {stage === 1 ? "00:03" : stage === 2 ? "00:08" : "00:12"}</>}</p>
      <div className="call-conversation">{children}</div>
      <div className="call-actions" aria-hidden="true"><div><Mic size={17} /><small>mute</small></div><div className="call-hangup"><PhoneOff size={21} /></div><div><Volume2 size={18} /><small>speaker</small></div></div>
      <div className="call-home-indicator" aria-hidden="true" />
    </div>
  )
}

function Transcript({ speaker, children, user = false }: { speaker: string; children: ReactNode; user?: boolean }) {
  return <div className={`call-transcript ${user ? "from-caller" : ""}`}><span>{speaker}</span><p>{children}</p></div>
}

const slides = [
  { id: "calling", label: "Call your home", content: <CallScreen stage={0}><div className="call-dialing"><Phone size={22} /><div className="call-wave" aria-hidden="true">{[0, 1, 2, 3, 4, 5, 6].map(n => <i key={n} style={{ animationDelay: `${n * 0.13}s` }} />)}</div><p>A familiar call.<br />A new way to check in.</p></div></CallScreen> },
  { id: "greeting", label: "Meet your assistant", content: <CallScreen stage={1}><div className="call-transcript-label"><AudioLines size={13} /> Live captions</div><Transcript speaker="Actias Assistant">Hi! I’m your Actias Assistant. How can I help you?</Transcript></CallScreen> },
  { id: "question", label: "Ask naturally", content: <CallScreen stage={2}><div className="call-transcript-label"><AudioLines size={13} /> Live captions</div><Transcript speaker="You" user>Hey, is there anyone in my living room?</Transcript><div className="call-listening"><span /><span /><span /> Checking your room</div></CallScreen> },
  { id: "answer", label: "Get a clear answer", content: <CallScreen stage={3}><div className="call-transcript-label"><AudioLines size={13} /> Live captions</div><Transcript speaker="Actias Assistant">Yes. One person is in your living room, near the TV.</Transcript><div className="call-room-summary"><span><Check size={12} /> Example room reading</span><div><i /> Living room <strong>1 person</strong></div><p>Trackers → Vultr → Actias</p></div></CallScreen> },
]

export default function PhoneMockupBasic() {
  return (
    <section className="call-walkthrough" aria-labelledby="call-walkthrough-title">
      <div className="call-section-heading"><span>Actias × Chert</span><h3 id="call-walkthrough-title">Your home, a phone call away.</h3><p>Illustrative call walkthrough</p></div>
      <PhoneCarousel slides={slides} />
    </section>
  )
}
