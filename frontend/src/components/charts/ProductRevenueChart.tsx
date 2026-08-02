import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TopProductEntry } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'

export function ProductRevenueChart({ data }: { data: TopProductEntry[] }) {
  const formatCurrency = useCurrencyFormatter()

  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in this range yet.</p>
  }

  // Longest name first isn't useful here -- highest revenue first is
  // what makes a bar chart actually readable at a glance.
  const sorted = [...data].sort((a, b) => b.revenue - a.revenue)

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, sorted.length * 36)}>
      <BarChart
        data={sorted}
        layout="vertical"
        margin={{ top: 8, right: 24, left: 8, bottom: 8 }}
      >
        <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          tickFormatter={(v: number) => formatCurrency(v)}
        />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fill: 'var(--color-ink)', fontSize: 12 }}
          stroke="var(--color-rule-strong)"
          width={140}
        />
        <Tooltip
          formatter={(value) => formatCurrency(Number(value))}
          contentStyle={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-rule)',
            color: 'var(--color-ink)',
          }}
        />
        <Bar dataKey="revenue" name="Revenue" fill="var(--color-brass)" radius={[0, 3, 3, 0]}>
          <LabelList
            dataKey="revenue"
            position="right"
            formatter={(value) => formatCurrency(Number(value))}
            style={{ fill: 'var(--color-ink)', fontSize: 11 }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
