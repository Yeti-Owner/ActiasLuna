"use client";

import React, { useEffect, useRef, useState } from "react";
import mothImg from "@/public/moth-luna/moth.png";

export interface Milestone {
  id: string;
  title?: string;
  description?: string;
  date?: string;
  content?: React.ReactNode;
}

export interface TimelineProps {
  milestones: Milestone[];
}

export default function Component({ milestones }: TimelineProps) {
  const trackRef = useRef<HTMLDivElement>(null);
  const markerRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLDivElement>(null);
  const trailRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const track = trackRef.current;
    const marker = markerRef.current;
    const heading = headingRef.current;
    const trail = trailRef.current;
    if (!track || !marker || !heading || !trail) return;
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    let target = 0, position = 0, velocity = 0;
    let angle = 180, targetAngle = 180;
    let height = 1, last = performance.now(), raf = 0;
    let lastScroll = window.scrollY;
    let directionTravel = 0;
    let active = -1;
    let waypoints: number[] = [];
    let disposed = false;

    const paint = () => {
      marker.style.transform = "translate3d(-50%," + (position * height).toFixed(3) + "px,0)";
      heading.style.transform = "rotate(" + angle.toFixed(3) + "deg)";
      trail.style.transform = "scaleY(" + position.toFixed(6) + ")";
      const next = waypoints.reduce((reached, point, index) =>
        position >= point - .025 ? index : reached, -1);
      if (next !== active) { active = next; setActiveIndex(next); }
    };

    const frame = (now: number) => {
      raf = 0;
      if (disposed || document.hidden) return;
      const dt = Math.min(.05, (now - last) / 1000);
      last = now;
      if (motion.matches) {
        position = target; velocity = 0; angle = targetAngle;
      } else {
        // Exact critically damped spring; no stacked CSS transitions or layout animation.
        const rate = 10;
        const delta = position - target;
        const spring = velocity + rate * delta;
        const decay = Math.exp(-rate * dt);
        position = target + (delta + spring * dt) * decay;
        velocity = (velocity - rate * spring * dt) * decay;
        const turn = ((targetAngle - angle + 540) % 360) - 180;
        angle += turn * (1 - Math.exp(-dt * 5));
      }
      paint();
      const turning = Math.abs(((targetAngle - angle + 540) % 360) - 180) > .1;
      if (Math.abs(target - position) > .00002 || Math.abs(velocity) > .0001 || turning) {
        raf = requestAnimationFrame(frame);
      } else {
        position = target; velocity = 0; angle = targetAngle; paint();
      }
    };

    const requestFrame = () => {
      if (!raf && !disposed && !document.hidden) {
        last = performance.now(); raf = requestAnimationFrame(frame);
      }
    };
    const update = () => {
      const rect = track.getBoundingClientRect();
      height = rect.height;
      waypoints = Array.from(track.querySelectorAll<HTMLElement>("[data-timeline-waypoint]")).map(el =>
        (el.getBoundingClientRect().top - rect.top) / Math.max(1, height));
      target = Math.max(0, Math.min(1, (window.innerHeight * .5 - rect.top) / Math.max(1, height)));
      const dy = window.scrollY - lastScroll;
      lastScroll = window.scrollY;
      // Small touchpad reversals should not make the moth spin around.
      if (dy !== 0) {
        directionTravel = Math.sign(dy) === Math.sign(directionTravel) ? directionTravel + dy : dy;
        if (Math.abs(directionTravel) > 18) targetAngle = directionTravel > 0 ? 180 : 0;
      }
      requestFrame();
    };
    const resume = () => {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      if (!document.hidden) update();
    };
    const resize = new ResizeObserver(update);
    resize.observe(track);
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    document.addEventListener("visibilitychange", resume);
    motion.addEventListener("change", update);
    update();
    position = target; paint();
    document.fonts.ready.then(() => { if (!disposed) update(); });
    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      resize.disconnect();
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      document.removeEventListener("visibilitychange", resume);
      motion.removeEventListener("change", update);
    };
  }, [milestones]);

  return (
    <div className="w-full px-6 py-12 font-sans md:px-16">
      <div className="relative" ref={trackRef}>
        {/* Faint flight track guide line on the left */}
        <div className="absolute left-0 top-0 bottom-0 w-[2px] -translate-x-1/2 bg-white/15" />

        {/* The moth and its trail share one spring, so they stay connected. */}
        <div
          ref={trailRef}
          className="absolute left-0 top-0 bottom-0 z-0 w-[2px] -ml-px origin-top bg-gradient-to-b from-white/40 via-white/80 to-[#8FD3A5]"
          style={{ transform: "scaleY(0)", willChange: "transform" }}
        />
        <div
          ref={markerRef}
          className="absolute left-0 top-0 z-30 pointer-events-none flex items-center justify-center w-12 h-12 -mt-6"
          style={{ transform: "translate3d(-50%,0,0)", willChange: "transform" }}
        >
          <div ref={headingRef} className="relative flex h-12 w-12 items-center justify-center" style={{ willChange: "transform" }}>
            <div className="absolute inset-0 rounded-full bg-[#8FD3A5]/20 blur-md" />
            <img
              src={mothImg}
              alt="Actias Luna scroll guide"
              className="relative w-10 h-10 md:w-12 md:h-12 object-contain"
              style={{ filter: "brightness(1.05) drop-shadow(0 0 5px rgba(143,211,165,.55))" }}
            />
          </div>
        </div>

        <div className="relative z-10 flex flex-col gap-16 md:gap-24">
          {milestones.map((milestone, index) => {
            // Has the moth reached this item?
            const isActive = index <= activeIndex;

            const dotClasses = isActive
              ? "border-white bg-[#8FD3A5] scale-125 shadow-[0_0_10px_rgba(143,211,165,0.8)]"
              : "border-white/30 bg-white/20 scale-100";

            return (
              <div key={milestone.id} data-timeline-waypoint className="relative w-full pl-10 md:pl-16">
                {/* The milestone waypoint dot, centered on the left line */}
                <div
                  className={`absolute left-0 top-0 z-20 mt-1.5 h-3.5 w-3.5 -translate-x-1/2 rounded-full border-2 transition-all duration-500 ${dotClasses}`}
                />

                {/* Content, always to the right of the line */}
                {(milestone.date || milestone.title || milestone.description) && <div
                  className={`max-w-2xl text-left transition-all duration-700 ease-out ${
                    isActive ? "translate-y-0 opacity-100" : "translate-y-8 opacity-30"
                  }`}
                >
                  {milestone.date && (
                    <div data-moth-avoid className="mb-3 inline-block rounded-full border border-white/25 bg-white/10 px-3 py-1 font-serif text-xs tracking-widest text-white/80">
                      {milestone.date}
                    </div>
                  )}
                  {milestone.title && <h3 className="text-2xl font-bold tracking-tight text-white">
                    {milestone.title}
                  </h3>}
                  {milestone.description && <p className="mt-3 text-base leading-relaxed text-white/80">
                    {milestone.description}
                  </p>}
                </div>}
                {milestone.content && <div className={milestone.date || milestone.title || milestone.description ? "mt-8" : undefined}>{milestone.content}</div>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
