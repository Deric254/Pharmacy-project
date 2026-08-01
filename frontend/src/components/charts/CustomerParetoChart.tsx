import {
  Bar,
  ComposedChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TopCustomerEntry } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'

export function CustomerParetoChart({ data }: { data: TopCustomerEntry[] }) {
  const formatCurrency = useCurrencyFormatter()

  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">No customer-attached sales in this range yet.</p>
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart data={data} margin={{ top: 8, right: 12, left: 8, bottom: 40 }}>
        <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" />
        <XAxis
          dataKey="name"
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          angle={-35}
          textAnchor="end"
          interval={0}
        />
        <YAxis
          yAxisId="revenue"
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          tickFormatter={(v: number) => formatCurrency(v)}
          width={80}
        />
        <YAxis
          yAxisId="cumulative"
          orientation="right"
          domain={[0, 100]}
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          tickFormatter={(v: number) => `${v}%`}
          width={44}
        />
        <Tooltip
          formatter={(value, name) =>
            name === 'Cumulative %' ? `${Number(value).toFixed(1)}%` : formatCurrency(Number(value))
          }
          contentStyle={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-rule)',
            color: 'var(--color-ink)',
          }}
        />
        <Bar
          yAxisId="revenue"
          dataKey="revenue"
          name="Revenue"
          fill="var(--color-brass)"
          radius={[3, 3, 0, 0]}
        />
        <Line
          yAxisId="cumulative"
          type="monotone"
          dataKey="cumulative_percent"
          name="Cumulative %"
          stroke="var(--color-stamp-red)"
          strokeWidth={2}
          dot={{ r: 3 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
