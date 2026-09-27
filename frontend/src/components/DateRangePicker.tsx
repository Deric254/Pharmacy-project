import type { DateRangePreset } from '../lib/dateRangePresets'

interface DateRangePickerProps {
  preset: DateRangePreset
  range: { start: string; end: string }
  onPresetChange: (preset: DateRangePreset) => void
  onRangeChange: (range: { start: string; end: string }) => void
}

export function DateRangePicker({
  preset,
  range,
  onPresetChange,
  onRangeChange,
}: DateRangePickerProps) {
  return (
    <div className="flex items-center gap-2">
      {(['today', 'week', 'month'] as const).map((p) => (
        <button
          key={p}
          onClick={() => onPresetChange(p)}
          className={`border px-3 py-1.5 text-xs uppercase tracking-wide ${
            preset === p
              ? 'border-ink bg-ink text-paper'
              : 'border-rule text-ink-soft hover:border-brass'
          }`}
        >
          {p === 'today' ? 'Today' : p === 'week' ? 'Last 7 days' : 'This month'}
        </button>
      ))}
      <input
        type="date"
        value={range.start}
        onChange={(e) => {
          onPresetChange('custom')
          onRangeChange({ ...range, start: e.target.value })
        }}
        className="border border-rule bg-paper px-2 py-1.5 text-xs"
      />
      <span className="text-xs text-ink-soft">to</span>
      <input
        type="date"
        value={range.end}
        onChange={(e) => {
          onPresetChange('custom')
          onRangeChange({ ...range, end: e.target.value })
        }}
        className="border border-rule bg-paper px-2 py-1.5 text-xs"
      />
    </div>
  )
}
