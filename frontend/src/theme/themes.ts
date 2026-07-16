export interface ThemeDefinition {
  name: string
  label: string
  description: string
  vars: Record<string, string>
  fontLinks: string[]
}

export const THEMES: Record<string, ThemeDefinition> = {
  ledger: {
    name: 'ledger',
    label: 'Ledger',
    description: 'Aged paper and ink -- an accounting-book feel for a numbers-first shop.',
    vars: {
      '--color-paper': '#ede8db',
      '--color-paper-dim': '#e3ddcb',
      '--color-ink': '#211c14',
      '--color-ink-soft': '#5a5546',
      '--color-rule': '#c9c0a4',
      '--color-rule-strong': '#a89c78',
      '--color-brass': '#8a6d3b',
      '--color-brass-soft': '#b79a5f',
      '--color-stamp-red': '#a13d2e',
      '--color-stamp-red-soft': '#e7c9c2',
      '--color-stamp-green': '#3a6b4c',
      '--color-stamp-green-soft': '#c9dcc9',
      '--color-panel': '#f6f3e9',
      '--font-display': '"Fraunces", "Iowan Old Style", ui-serif, Georgia, serif',
      '--font-body': '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      '--font-mono': '"IBM Plex Mono", ui-monospace, "SF Mono", monospace',
    },
    fontLinks: ['Fraunces:opsz,wght@9..144,400;9..144,600', 'IBM+Plex+Sans:wght@400;500;600'],
  },

  clinical: {
    name: 'clinical',
    label: 'Clinical',
    description: 'Bright, high-contrast, modern -- the look of a well-run health-tech app.',
    vars: {
      '--color-paper': '#ffffff',
      '--color-paper-dim': '#f2f6f7',
      '--color-ink': '#13213a',
      '--color-ink-soft': '#54677d',
      '--color-rule': '#d8e2e8',
      '--color-rule-strong': '#a9bdc9',
      '--color-brass': '#0f8b8d',
      '--color-brass-soft': '#7fc4c5',
      '--color-stamp-red': '#d1453d',
      '--color-stamp-red-soft': '#f6d3d0',
      '--color-stamp-green': '#1b9c6f',
      '--color-stamp-green-soft': '#c8ecdd',
      '--color-panel': '#f7fafb',
      '--font-display': '"Manrope", ui-sans-serif, system-ui, sans-serif',
      '--font-body': '"Inter", ui-sans-serif, system-ui, sans-serif',
      '--font-mono': '"IBM Plex Mono", ui-monospace, "SF Mono", monospace',
    },
    fontLinks: ['Manrope:wght@500;700', 'Inter:wght@400;500;600'],
  },

  midnight: {
    name: 'midnight',
    label: 'Midnight',
    description: 'Dark, low-glare -- easy on the eyes for a back-office screen running all day.',
    vars: {
      '--color-paper': '#15171c',
      '--color-paper-dim': '#0f1013',
      '--color-ink': '#e8e6df',
      '--color-ink-soft': '#9a9da6',
      '--color-rule': '#2e323b',
      '--color-rule-strong': '#454a56',
      '--color-brass': '#d9a441',
      '--color-brass-soft': '#8a6d2f',
      '--color-stamp-red': '#e5675a',
      '--color-stamp-red-soft': '#4a2a28',
      '--color-stamp-green': '#6fcf97',
      '--color-stamp-green-soft': '#25402f',
      '--color-panel': '#1c1f26',
      '--font-display': '"Space Grotesk", ui-sans-serif, system-ui, sans-serif',
      '--font-body': '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      '--font-mono': '"IBM Plex Mono", ui-monospace, "SF Mono", monospace',
    },
    fontLinks: ['Space+Grotesk:wght@500;700', 'IBM+Plex+Sans:wght@400;500;600'],
  },

  sunrise: {
    name: 'sunrise',
    label: 'Sunrise',
    description: 'Warm and approachable -- a friendly neighborhood-pharmacy feel.',
    vars: {
      '--color-paper': '#fbf3e7',
      '--color-paper-dim': '#f5e9d6',
      '--color-ink': '#3b2a1e',
      '--color-ink-soft': '#7a6650',
      '--color-rule': '#e6d2b5',
      '--color-rule-strong': '#cbab7d',
      '--color-brass': '#e08e1d',
      '--color-brass-soft': '#f0c37e',
      '--color-stamp-red': '#c1443a',
      '--color-stamp-red-soft': '#f1d2ce',
      '--color-stamp-green': '#4c8c5b',
      '--color-stamp-green-soft': '#d9ead9',
      '--color-panel': '#fdf8f0',
      '--font-display': '"Lora", "Iowan Old Style", ui-serif, Georgia, serif',
      '--font-body': '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      '--font-mono': '"IBM Plex Mono", ui-monospace, "SF Mono", monospace',
    },
    fontLinks: ['Lora:wght@500;600', 'IBM+Plex+Sans:wght@400;500;600'],
  },
}

export const DEFAULT_THEME = 'ledger'

export function applyTheme(themeName: string): void {
  const theme = THEMES[themeName] ?? THEMES[DEFAULT_THEME]
  const root = document.documentElement
  for (const [key, value] of Object.entries(theme.vars)) {
    root.style.setProperty(key, value)
  }
  root.dataset.theme = theme.name
  loadThemeFonts(theme)
}

const loadedFontFamilies = new Set<string>()

function loadThemeFonts(theme: ThemeDefinition): void {
  for (const family of theme.fontLinks) {
    if (loadedFontFamilies.has(family)) continue
    loadedFontFamilies.add(family)
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = `https://fonts.googleapis.com/css2?family=${family}&display=swap`
    document.head.appendChild(link)
  }
}
