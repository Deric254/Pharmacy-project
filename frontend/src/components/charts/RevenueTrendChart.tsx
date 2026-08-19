import {
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
} from 'recharts'
import type { RevenueTrendOut } from '../../types/api'
import { useCurrencyFormatter } from '../../lib/currency'
import { ChartScrollArea } from './ChartScrollArea'

// Pixels each point gets along the x-axis. Wide enough that period
// labels (dates/week numbers) never touch their neighbours -- once
// there are more points than fit at this width, the chart widens
// past its panel and ChartScrollArea takes over instead of Recharts
// silently thinning out which ticks it draws.
const PX_PER_POINT = 56
export function RevenueTrendChart({ data }: { data: RevenueTrendOut }) {
  const formatCurrency = useCurrencyFormatter()
  const hasProfit = data.points.some((p) => p.profit !== null)

  if (data.points.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in this range yet.</p>
  }

  return (
    <div>
      <ChartScrollArea itemCount={data.points.length} minPxPerItem={PX_PER_POINT}>
      <ResponsiveContainer width="100%" height={260}>
        {/* top/bottom margins give room for labels above AND below the
            line (revenue on top, profit on bottom); left/right stay
            modest -- the actual fix for edge-point labels is XAxis's
            own `padding` below, not outer margin. Margin only moves
            the whole chart (axis included) away from the container
            edge; it does nothing to the gap between the y-axis and
            the first plotted point, which is what was actually
            colliding. */}
        <LineChart
          data={data.points}
          margin={{ top: 20, right: 16, left: 8, bottom: hasProfit ? 20 : 8 }}
        >
          <CartesianGrid stroke="var(--color-rule)" strokeDasharray="3 3" />
          <XAxis
            dataKey="period_label"
            tick={{ fill: 'var(--color-ink-soft)', fontSize: 11 }}
            stroke="var(--color-rule-strong)"
            // Every point already has PX_PER_POINT of guaranteed width
            // via ChartScrollArea, so there's no need for Recharts'
            // default behavior of dropping ticks it guesses won't fit
            // -- that guess is what caused labels to disappear or
            // crowd together in the first place.
            interval={0}
            // This is the actual fix for the first/last point's value
            // label colliding with the y-axis and the container edge:
            // padding pulls the first and last plotted points inward
            // from the axis boundaries, so a label centered on either
            // one has real room on both sides instead of butting
            // straight up against the y-axis tick text (left) or
            // getting clipped by the container (right).
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
