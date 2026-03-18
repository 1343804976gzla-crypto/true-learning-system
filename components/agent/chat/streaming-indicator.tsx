'use client'

export function StreamingIndicator() {
  return (
    <span className="inline-flex items-center gap-0.5 align-middle ml-0.5">
      <span className="size-[5px] rounded-full bg-white/30 animate-[blink_1.4s_ease-in-out_infinite]" />
      <span className="size-[5px] rounded-full bg-white/30 animate-[blink_1.4s_ease-in-out_0.15s_infinite]" />
      <span className="size-[5px] rounded-full bg-white/30 animate-[blink_1.4s_ease-in-out_0.3s_infinite]" />
      <style>{`
        @keyframes blink {
          0%, 100% { opacity: 0.2; }
          50% { opacity: 0.8; }
        }
      `}</style>
    </span>
  )
}
