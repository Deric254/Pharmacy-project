import { useConfigStore } from '../config/store'

export function Logo({ className = 'h-8 w-8' }: { className?: string }) {
  const logoUrl = useConfigStore((s) => s.config?.logo_url)

  if (logoUrl) {
    return <img src={logoUrl} alt="" className={`${className} object-contain`} />
  }

  // Neutral fallback mark (a generic mortar & pestle glyph via a plain
  // cross-in-box, not any specific business's branding) so an
  // unconfigured deployment still looks intentional, not broken.
  return (
    <div
      className={`${className} flex items-center justify-center border border-rule-strong text-brass`}
      aria-hidden="true"
    >
      <span className="font-display text-lg leading-none">℞</span>
    </div>
  )
}
