export default function MothLogo({ className = "w-7 h-7 text-white" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      {/* Broad forewings and long, tapered Luna moth tails. */}
      <path fill="currentColor" d="M15 13C12 8 6 5 2 5c0 6 2 11 8 12-4 1-5 4-3 6 2 2 4 1 5 0l-1 7c3-2 4-7 4-12V13Zm2 0c3-5 9-8 13-8 0 6-2 11-8 12 4 1 5 4 3 6-2 2-4 1-5 0l1 7c-3-2-4-7-4-12V13Z" />
      <path d="M16 10v11m-1-11c0-3-2-5-3-6m5 6c0-3 2-5 3-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="m6 9 7 6m13-6-7 6" stroke="#2E6F40" strokeWidth=".8" strokeLinecap="round" />
    </svg>
  )
}
