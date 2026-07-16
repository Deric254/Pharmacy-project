export function ComingSoonPage({ title }: { title: string }) {
  return (
    <div className="p-6">
      <h1 className="font-display text-2xl text-ink">{title}</h1>
      <p className="mt-2 text-sm text-ink-soft">
        This section isn't built yet. It's on the punch list, not forgotten.
      </p>
    </div>
  )
}
