export function Marquee({ text }: { text: string }) {
  if (!text.trim()) return null

  return (
    <div className="overflow-hidden whitespace-nowrap" aria-hidden="true">
      <div className="marquee-track inline-flex">
        <span className="pr-8 text-xs tracking-wide">{text}</span>
        <span className="pr-8 text-xs tracking-wide">{text}</span>
      </div>
    </div>
  )
}
