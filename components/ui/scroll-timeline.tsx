"use client";

import React, { useEffect, useRef, useState } from "react";
import mothImg from "@/public/moth-guide.png";

export interface Milestone {
  id: string;
  title: string;
  description: string;
  date: string;
}

export interface TimelineProps {
  milestones: Milestone[];
}

export default function Component({ milestones }: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollProgress, setScrollProgress] = useState(0);
  const [scrollDirection, setScrollDirection] = useState<"down" | "up">("down");
  const lastScrollY = useRef(0);
  const isReduced = useRef(false);

  useEffect(() => {
    isReduced.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const handleScroll = () => {
      if (!containerRef.current) return;

      const currentY = window.scrollY;
      if (currentY > lastScrollY.current + 2) {
        setScrollDirection("down");
      } else if (currentY < lastScrollY.current - 2) {
        setScrollDirection("up");
      }
      lastScrollY.current = currentY;

      const rect = containerRef.current.getBoundingClientRect();
      const windowHeight = window.innerHeight;

      // Progress is measured from the middle of the screen
      const triggerPoint = windowHeight / 2;
      const totalHeight = rect.height;
      const scrolledPast = triggerPoint - rect.top;

      let progress = scrolledPast / totalHeight;
      progress = Math.max(0, Math.min(1, progress));
      setScrollProgress(progress);
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    handleScroll(); // initial calculation

    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="w-full px-6 py-12 font-sans md:px-16" ref={containerRef}>
      <div className="relative">
        {/* Faint flight track guide line on the left */}
        <div className="absolute left-0 top-0 bottom-0 w-[2px] -translate-x-1/2 bg-white/15" />

        {/* Illuminated flight path trailing the moth */}
        <div
          className="absolute left-0 top-0 z-0 w-[2px] -translate-x-1/2 bg-gradient-to-b from-white/40 via-white/80 to-[#8FD3A5] transition-all duration-150 ease-out"
          style={{ height: `${scrollProgress * 100}%` }}
        />

        {/* Flying Luna Moth Indicator that glides along the timeline */}
        <div
          className="absolute left-0 z-30 -translate-x-1/2 transition-all duration-300 ease-out pointer-events-none"
          style={{
            top: `${scrollProgress * 100}%`,
            transform: `translate(-50%, -50%) rotate(${scrollDirection === "down" ? "180deg" : "0deg"})`,
          }}
        >
          <div className="relative flex items-center justify-center">
            {/* Soft bioluminescent glow */}
            <div className="absolute -inset-2 rounded-full bg-[#8FD3A5]/30 blur-md animate-pulse" />
            
            {/* Actias Luna moth image from user illustration */}
            <img
              src={mothImg}
              alt="Actias Luna scroll guide"
              className="relative w-9 h-9 md:w-12 md:h-12 object-contain drop-shadow-[0_0_10px_rgba(143,211,165,0.7)] transition-transform duration-300"
              style={{
                filter: "brightness(1.1) drop-shadow(0 0 6px rgba(143,211,165,0.8))",
              }}
            />
          </div>
        </div>

        <div className="relative z-10 flex flex-col gap-16 md:gap-24">
          {milestones.map((milestone, index) => {
            // Has the moth reached this item?
            const itemThreshold = index / (milestones.length - 1 || 1);
            const isActive = scrollProgress >= itemThreshold - 0.05;

            const dotClasses = isActive
              ? "border-white bg-[#8FD3A5] scale-125 shadow-[0_0_10px_rgba(143,211,165,0.8)]"
              : "border-white/30 bg-white/20 scale-100";

            return (
              <div key={milestone.id} className="relative w-full pl-10 md:pl-16">
                {/* The milestone waypoint dot, centered on the left line */}
                <div
                  className={`absolute left-0 top-0 z-20 mt-1.5 h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 transition-all duration-500 ${dotClasses}`}
                />

                {/* Content, always to the right of the line */}
                <div
                  className={`max-w-2xl text-left transition-all duration-700 ease-out ${
                    isActive ? "translate-y-0 opacity-100" : "translate-y-8 opacity-30"
                  }`}
                >
                  <div data-moth-avoid className="mb-3 inline-block rounded-full border border-white/25 bg-white/10 px-3 py-1 font-mono text-xs tracking-widest text-white/80">
                    {milestone.date}
                  </div>
                  <h3 className="text-2xl font-bold tracking-tight text-white">
                    {milestone.title}
                  </h3>
                  <p className="mt-3 text-base leading-relaxed text-white/80">
                    {milestone.description}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
