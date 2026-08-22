import {
  Bar,
  ComposedChart,
  CartesianGrid,
  LabelList,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TopCustomerEntry } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'
import { ChartScrollArea } from './ChartScrollArea'

// Names are angled at -35deg rather than horizontal, so each customer
// needs less width than an upright label would, but still enough that
// adjacent angled labels don't run into each other as the customer
// count grows.
const PX_PER_CUSTOMER = 68
export function CustomerParetoChart({ data }: { data: TopCustomerEntry[] }) {
  const formatCurrency = useCurrencyFormatter()

  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">No customer-attached sales in this range yet.</p>
  }

  return (
    <ChartScrollArea itemCount={data.length} minPxPerItem={PX_PER_CUSTOMER}>
    <ResponsiveContainer width="100%" height={280}>
      {/* top:28 gives the tallest bar's revenue label room above it
          instead of butting against the container edge; bottom:44
          gives the angled customer-name ticks room below the plot
          without the cumulative-% line's own labels landing on top
          of them. */}
      <ComposedChart data={data} margin={{ top: 28, right: 12, left: 8, bottom: 44 }}>
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
        >
          <LabelList
            dataKey="revenue"
            position="top"
            formatter={(value) => (typeof value === 'number' ? formatCurrency(value) : '')}
            fill="var(--color-ink)"
            fontSize={10}
          />
        </Bar>
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
    </ChartScrollArea>
  )
}
