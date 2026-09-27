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
import type { CategoryRevenueEntry } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'

interface CategoryRevenueChartProps {
  data: CategoryRevenueEntry[]
  onSelectCategory: (entry: CategoryRevenueEntry) => void
}

export function CategoryRevenueChart({ data, onSelectCategory }: CategoryRevenueChartProps) {
  const formatCurrency = useCurrencyFormatter()

  if (data.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in this range yet.</p>
  }

  const sorted = [...data].sort((a, b) => b.revenue - a.revenue)

  function barLabel(entry: CategoryRevenueEntry): string {
    return `${formatCurrency(entry.revenue)} · ${entry.percent_of_total}%`
  }

  const longestLabel = sorted.reduce((max, entry) => Math.max(max, barLabel(entry).length), 0)
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
          domain={[0, (max: number) => Math.ceil(max * 1.12)]}
          tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
          stroke="var(--color-rule-strong)"
          tickFormatter={(v: number) => formatCurrency(v)}
        />
        <YAxis
          type="category"
          dataKey="category_name"
          tick={{ fill: 'var(--color-ink)', fontSize: 12 }}
          stroke="var(--color-rule-strong)"
          width={140}
        />
        <Tooltip
          formatter={(value, _name, item) => [
            `${formatCurrency(Number(value))} (${item.payload.percent_of_total}% of total revenue)`,
            'Revenue',
          ]}
          contentStyle={{
            background: 'var(--color-panel)',
            border: '1px solid var(--color-rule)',
            color: 'var(--color-ink)',
          }}
        />
        <Bar
          dataKey="revenue"
          name="Revenue"
          fill="var(--color-brass)"
          radius={[0, 3, 3, 0]}
          cursor="pointer"
          onClick={(data: { payload?: CategoryRevenueEntry }) => {
            if (data.payload) onSelectCategory(data.payload)
          }}
        >
          <LabelList
            dataKey="revenue"
            position="right"
            content={(props) => {
              const { x, y, width, height, index } = props
              const entry = sorted[index as number]
              if (!entry || x == null || y == null || width == null || height == null) return null
              return (
                <text
                  x={Number(x) + Number(width) + 6}
                  y={Number(y) + Number(height) / 2}
                  dy={4}
                  fill="var(--color-ink)"
                  fontSize={11}
                >
                  {barLabel(entry)}
                </text>
              )
            }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
