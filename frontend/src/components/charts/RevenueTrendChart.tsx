import {
  Line,
  LineChart,
  LabelList,
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
// Labels on every point are only readable up to a point; past this,
// even with a scrollable axis, the labels crowd each other value against
// value. Same cap as before, kept explicit rather than tied to px math
// so it stays predictable as the layout changes.
const MAX_LABELED_POINTS = 15

export function RevenueTrendChart({ data }: { data: RevenueTrendOut }) {
  const formatCurrency = useCurrencyFormatter()
  const hasProfit = data.points.some((p) => p.profit !== null)
  const showLabels = data.points.length <= MAX_LABELED_POINTS

  if (data.points.length === 0) {
    return <p className="text-sm text-ink-soft">No sales in this range yet.</p>
  }

  return (
    <div>
      <ChartScrollArea itemCount={data.points.length} minPxPerItem={PX_PER_POINT}>
      <ResponsiveContainer width="100%" height={260}>
        {/* top/bottom margins are deliberately larger than the other
            charts' -- this is the one place labels can sit above AND
            below the plotted line (revenue on top, profit on bottom),
            so both need clearance from the container edge or the
            highest/lowest label gets clipped instead of just crowded. */}
        <LineChart
          data={data.points}
          margin={{ top: 20, right: 12, left: 8, bottom: hasProfit && showLabels ? 20 : 8 }}
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
                formatter={(value) => formatCurrency(Number(value))}
                style={{ fill: 'var(--color-brass)', fontSize: 10 }}
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
                  formatter={(value) => formatCurrency(Number(value))}
                  style={{ fill: 'var(--color-stamp-green)', fontSize: 10 }}
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
