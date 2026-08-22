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
import type { RenderableText } from 'recharts'
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

  // The value label sits just past the end of each bar, so the bar
  // with the largest revenue -- the one nearest the right edge of the
  // plot -- is the one whose label is at risk of running past the
  // container and getting clipped or overlapping the axis border.
  // Sizing the right margin off the actual longest formatted label
  // (rather than a fixed guess) keeps that label fully visible no
  // matter how many digits the currency and amount add up to.
  const longestLabel = sorted.reduce(
    (max, entry) => Math.max(max, formatCurrency(entry.revenue).length),
    0,
  )
  const rightMargin = Math.max(24, longestLabel * 7 + 12)

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, sorted.length * 36)}>
      <BarChart
        data={sorted}
        layout="vertical"
        margin={{ top: 8, right: rightMargin, left: 8, bottom: 8 }}
      >
        <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          // A little headroom past the largest bar so its label has
          // somewhere to sit that isn't directly on top of the axis
          // line -- without this, the longest bar and its label both
          // end right at the domain edge and visually collide.
          domain={[0, (max: number) => Math.ceil(max * 1.12)]}
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
          {/* The whole point of `rightMargin` above is reserving room
              for this label past the end of each bar -- without it,
              the chart has no data label at all, just bare bars. */}
          <LabelList
            dataKey="revenue"
            position="right"
            // Recharts' LabelFormatter type is
            // `(label: RenderableText) => RenderableText`, where
            // RenderableText = string | number | boolean | null |
            // undefined -- broader than a plain number because a
            // LabelList can label all sorts of things, not just this
            // dataKey. Typing the param as plain `number` is what tsc
            // rejected. Matching the real type and coercing explicitly
            // keeps this honest about what it can actually receive
            // while still handing formatCurrency a real number.
            formatter={(value: RenderableText) => formatCurrency(Number(value ?? 0))}
            fill="var(--color-ink)"
            fontSize={11}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
