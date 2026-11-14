import { SiteBackground } from "@/components/ui/site-background"
import { GeistSerif } from "geist/font/serif"

import Hero from "@/components/hero"
import Nav from "@/components/nav"
import ScrollMoth from "@/components/ui/scroll-moth"
import TimelineSection from "@/components/timeline-section"
import TwoWaysSection from "@/components/two-ways-section"
import SecurityPrivacySection from "@/components/security-privacy-section"
import LaunchSection from "@/components/launch-section"
import Footer from "@/components/footer"

export default function App() {
  return (
    <div
      className={`site-shell relative isolate min-h-screen text-white flex flex-col selection:bg-[#8FD3A5] selection:text-[#12301D] ${GeistSerif.className}`}
      style={{
        fontFamily: "var(--font-geist-serif), 'Geist Serif', Georgia, serif",
      }}
    >
      <SiteBackground />
      {/* 1. Logo Hero (Top of site, full screen, 100svh) */}
      <Hero />

      {/* Flying white Luna moth starting under the logo */}
      <ScrollMoth
        width={78}
        flapSpeed={0.75}
        flapDepth={0.48}
      />

      {/* Sticky header after hero */}
      <Nav />

      {/* 2. Scroll Timeline */}
      <TimelineSection />

      {/* 3. Two ways to see someone */}
      <TwoWaysSection />

      {/* 4. Security and privacy */}
      <SecurityPrivacySection />

      {/* 5. Launch dashboard & phone setup */}
      <LaunchSection />

      {/* 6. Footer */}
      <Footer />
    </div>
  )
}
