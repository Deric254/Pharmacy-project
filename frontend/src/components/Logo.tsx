import { useConfigStore } from '../config/store'

export function Logo({ className = 'h-8 w-8' }: { className?: string }) {
  const logoUrl = useConfigStore((s) => s.config?.logo_url)

  if (logoUrl) {
    return <img src={logoUrl} alt="" className={`${className} object-contain`} />
  }

  return (
    <div
      className={`${className} flex items-center justify-center border border-rule-strong text-brass`}
      aria-hidden="true"
    >
      <span className="font-display text-lg leading-none">℞</span>
    </div>
  )
}
