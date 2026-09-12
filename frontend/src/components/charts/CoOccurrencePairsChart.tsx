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
import type { ProductPairEntry } from '../../types/api'

export function CoOccurrencePairsChart({ data }: { data: ProductPairEntry[] }) {
  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">Not enough shared sales yet to find a pattern.</p>
  }

  // Already sorted by co_occurrence_count from the backend, but the
  // chart only has room to show so many pairs meaningfully -- the top
  // ones are also the most statistically real ones.
  const top = data.slice(0, 15).map((p) => ({
    ...p,
    pairLabel: `${p.product_a_name} + ${p.product_b_name}`,
  }))

  const longestLabel = top.reduce((max, p) => Math.max(max, p.pairLabel.length), 0)
  const leftWidth = Math.min(260, Math.max(140, longestLabel * 6))

  return (
    <ResponsiveContainer width="100%" height={Math.max(200, top.length * 36)}>
      <BarChart
        data={top}
        layout="vertical"
        margin={{ top: 8, right: 48, left: 8, bottom: 8 }}
      >
        <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          domain={[0, (max: number) => Math.ceil(max * 1.15)]}
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          allowDecimals={false}
        />
        <YAxis
          type="category"
          dataKey="pairLabel"
          tick={{ fill: 'var(--color-ink)', fontSize: 12 }}
          stroke="var(--color-rule-strong)"
          width={leftWidth}
        />
        <Tooltip
          formatter={(value, name, props) => {
            if (name === 'co_occurrence_count') {
              const percent = props.payload?.percent_of_a_sales
              return [`${value} sales (${percent}% of ${props.payload?.product_a_name} sales)`, 'Bought together']
            }
            return [value, name]
          }}
          contentStyle={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-rule)',
            color: 'var(--color-ink)',
          }}
        />
        <Bar
          dataKey="co_occurrence_count"
          name="co_occurrence_count"
          fill="var(--color-brass)"
          radius={[0, 3, 3, 0]}
        >
          <LabelList dataKey="co_occurrence_count" position="right" fill="var(--color-ink)" fontSize={11} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
