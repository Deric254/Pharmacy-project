import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SeasonalTrendEntry } from '../../types/api'

const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
]

const LINE_COLORS = ['var(--color-brass)', 'var(--color-stamp-red)', 'var(--color-stamp-green)']

export function SeasonalTrendsChart({ data }: { data: SeasonalTrendEntry[] }) {
  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in range yet.</p>
  }

  const totalsByProduct = new Map<number, { name: string; total: number }>()
  for (const e of data) {
    const existing = totalsByProduct.get(e.product_id)
    totalsByProduct.set(e.product_id, {
      name: e.name,
      total: (existing?.total ?? 0) + e.total_quantity_sold,
    })
  }
  const topProductIds = [...totalsByProduct.entries()]
    .sort((a, b) => b[1].total - a[1].total)
    .slice(0, 3)
    .map(([id]) => id)

  const chartData = MONTH_NAMES.map((monthName, i) => {
    const month = i + 1
    const point: Record<string, string | number> = { month: monthName }
    for (const pid of topProductIds) {
      const entry = data.find((e) => e.product_id === pid && e.month === month)
      point[String(pid)] = entry?.total_quantity_sold ?? 0
    }
    return point
  })

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={chartData} margin={{ top: 20, right: 16, left: 8, bottom: 8 }}>
        <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" />
        <XAxis
          dataKey="month"
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
        />
        <YAxis
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          allowDecimals={false}
        />
        <Tooltip
          contentStyle={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-rule)',
            color: 'var(--color-ink)',
          }}
        />
        <Legend
          formatter={(value) => totalsByProduct.get(Number(value))?.name ?? value}
          wrapperStyle={{ fontSize: 12, color: 'var(--color-ink-soft)' }}
        />
        {topProductIds.map((pid, i) => (
          <Line
            key={pid}
            type="monotone"
            dataKey={String(pid)}
            name={String(pid)}
            stroke={LINE_COLORS[i]}
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
