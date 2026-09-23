import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
  LabelList,
} from 'recharts'
import type { RevenueTrendOut } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'
import { ChartScrollArea } from './ChartScrollArea'

const PX_PER_POINT = 56
export function RevenueTrendChart({ data }: { data: RevenueTrendOut }) {
  const formatCurrency = useCurrencyFormatter()
  const hasProfit = data.points.some((p) => p.profit !== null)
  const showLabels = data.points.length <= 31

  if (data.points.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in this range yet.</p>
  }

  return (
    <div>
      <ChartScrollArea itemCount={data.points.length} minPxPerItem={PX_PER_POINT}>
      <ResponsiveContainer width="100%" height={260}>
        {}
        <LineChart
          data={data.points}
          margin={{ top: 20, right: 16, left: 8, bottom: hasProfit ? 20 : 8 }}
        >
          <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" />
          <XAxis
            dataKey="period_label"
            tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
            stroke="var(--color-rule-strong)"
            interval={0}
            padding={{ left: 32, right: 32 }}
          />
          <YAxis
            tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
            stroke="var(--color-rule-strong)"
            tickFormatter={(v: number) => formatCurrency(v)}
            width={80}
          />
          <Tooltip
            formatter={(value) => formatCurrency(Number(value))}
            contentStyle={{
              background: 'var(--color-panel)',
              border: '1px solid var(--color-rule)',
              color: 'var(--color-ink)',
            }}
          />
          {hasProfit && <Legend wrapperStyle={{ fontSize: 12 }} />}
          <Line
            type="monotone"
            dataKey="revenue"
            name="Revenue"
            stroke="var(--color-brass)"
            strokeWidth={2}
            dot={data.points.length <= 31}
          >
            {showLabels && (
              <LabelList
                dataKey="revenue"
                position="top"
                formatter={(value) => (typeof value === 'number' ? formatCurrency(value) : '')}
                fill="var(--color-brass)"
                fontSize={10}
              />
            )}
          </Line>
          {hasProfit && (
            <Line
              type="monotone"
              dataKey="profit"
              name="Profit"
              stroke="var(--color-stamp-green)"
              strokeWidth={2}
              dot={data.points.length <= 31}
            >
              {showLabels && (
                <LabelList
                  dataKey="profit"
                  position="bottom"
                  formatter={(value) => (typeof value === 'number' ? formatCurrency(value) : '')}
                  fill="var(--color-stamp-green)"
                  fontSize={10}
                />
              )}
            </Line>
          )}
        </LineChart>
      </ResponsiveContainer>
      </ChartScrollArea>
      <p className="mt-1 text-right text-xs text-ink-soft">
        Grouped by {data.granularity} for this range
      </p>
    </div>
  )
}
