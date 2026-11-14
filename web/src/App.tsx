import { GeistSans } from 'geist/font/sans'
import { GeistMono } from 'geist/font/mono'
import { GeistPixel } from 'geist/font/pixel/variable'
import { GeistPixelSquare, GeistPixelGrid, GeistPixelCircle, GeistPixelTriangle, GeistPixelLine } from 'geist/font/pixel'
import { GeistSerif, GeistSerifRegular, GeistSerifSquare, GeistSerifTriangle, GeistSerifHeart } from 'geist/font/serif'

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
      className={`min-h-screen bg-[#2E6F40] text-white flex flex-col selection:bg-[#8FD3A5] selection:text-[#12301D] ${GeistSans.className}`}
      style={{
        fontFamily: "var(--font-geist-sans), 'Geist Sans', system-ui, sans-serif",
      }}
    >
      {/* 1. Logo Hero (Top of site, full screen, 100svh) */}
      <Hero />

      {/* Flying white Luna moth starting under the logo */}
      <ScrollMoth
        src="/static/moth-luna/"
        sprite="luna"
        width={120}
        flapSpeed={0.4}
        flapDepth={0.5}
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
