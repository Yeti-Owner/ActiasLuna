"use client";

import React, { useEffect, useRef, useState } from "react";

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

  useEffect(() => {
    const handleScroll = () => {
      if (!containerRef.current) return;

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

    window.addEventListener("scroll", handleScroll);
    handleScroll(); // initial calculation

    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="w-full px-6 py-12 font-sans md:px-16" ref={containerRef}>
      <div className="relative">
        {/* Background line (faded), on the LEFT */}
        <div className="absolute left-0 top-0 bottom-0 w-[2px] -translate-x-1/2 bg-white/20" />

        {/* Animated foreground line (draws on scroll), WHITE */}
        <div
          className="absolute left-0 top-0 z-0 w-[2px] -translate-x-1/2 bg-white transition-all duration-100 ease-out"
          style={{ height: `${scrollProgress * 100}%` }}
        />

        <div className="relative z-10 flex flex-col gap-16 md:gap-24">
          {milestones.map((milestone, index) => {
            // Has the line reached this item?
            const itemThreshold = index / (milestones.length - 1 || 1);
            const isActive = scrollProgress >= itemThreshold - 0.05;

            const dotClasses = isActive
              ? "border-white bg-[#2E6F40] scale-125"
              : "border-white/30 bg-white/30 scale-100";

            return (
              <div key={milestone.id} className="relative w-full pl-10 md:pl-16">
                {/* The dot, centred on the left line */}
                <div
                  className={`absolute left-0 top-0 z-20 mt-1.5 h-4 w-4 -translate-x-1/2 rounded-full border-2 transition-all duration-500 ${dotClasses}`}
                />

                {/* Content, always to the right of the line */}
                <div
                  className={`max-w-2xl text-left transition-all duration-700 ease-out ${
                    isActive ? "translate-y-0 opacity-100" : "translate-y-8 opacity-30"
                  }`}
                >
                  <div className="mb-3 inline-block rounded-full border border-white/25 bg-white/10 px-3 py-1 font-mono text-xs tracking-widest text-white/70">
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

