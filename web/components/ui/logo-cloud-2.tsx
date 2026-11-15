import type { ComponentProps } from "react"
import { PlusIcon, ShieldCheck } from "lucide-react"
import { cn } from "@/lib/utils"

type LogoCloudProps = ComponentProps<"div">

export function LogoCloud({ className, ...props }: LogoCloudProps) {
  return (
    <div className={cn("track-logo-cloud relative grid grid-cols-2 border border-white/20 md:grid-cols-4", className)} {...props}>
      <div className="pointer-events-none absolute -top-px left-1/2 w-screen -translate-x-1/2 border-t border-white/15" />
      <LogoCard className="border-b border-r border-white/20 bg-white/[.045] md:border-b-0">
        <ShieldCheck className="h-7 w-7 shrink-0 text-[#c4d9ab]" strokeWidth={1.35} />
        <span className="track-brand track-cyber">CyberSecurity</span>
      </LogoCard>
      <LogoCard className="border-b border-white/20 bg-[#102b1b]/30 md:border-b-0 md:border-r">
        <img src="/static/tracks/spacex.svg" alt="SpaceX" className="w-40 max-w-full select-none brightness-0 invert" />
      </LogoCard>
      <LogoCard className="border-r border-white/20 bg-[#102b1b]/30 md:bg-white/[.045]">
        <img src="/static/tracks/vultr.svg" alt="" className="h-8 w-8 select-none" />
        <span className="track-brand text-[27px] font-semibold tracking-tight">Vultr</span>
      </LogoCard>
      <LogoCard className="bg-white/[.045] md:bg-[#102b1b]/30">
        <img src="/static/tracks/chert.png" alt="" className="h-10 w-10 select-none object-contain" />
        <span className="track-brand text-[27px] font-semibold tracking-tight">Chert</span>
      </LogoCard>
      {[25, 50, 75].map(position => (
        <PlusIcon key={position} aria-hidden="true" strokeWidth={1}
          className="pointer-events-none absolute bottom-0 z-10 hidden size-5 -translate-x-1/2 translate-y-1/2 text-[#d5e3c2] md:block"
          style={{ left: `${position}%` }} />
      ))}
      <PlusIcon aria-hidden="true" strokeWidth={1} className="pointer-events-none absolute left-1/2 top-1/2 z-10 size-5 -translate-x-1/2 -translate-y-1/2 text-[#d5e3c2] md:hidden" />
      <div className="pointer-events-none absolute -bottom-px left-1/2 w-screen -translate-x-1/2 border-b border-white/15" />
    </div>
  )
}

function LogoCard({ className, children, ...props }: ComponentProps<"div">) {
  return (
    <div className={cn("relative flex aspect-square items-center justify-center gap-2.5 px-4 backdrop-blur-sm transition-colors duration-300 hover:bg-white/[.08]", className)} {...props}>
      {children}
    </div>
  )
}
