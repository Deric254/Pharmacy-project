import type { ReactElement } from 'react'
import type { Props as RechartsLabelProps } from 'recharts/types/component/Label'
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
import { computePlottedRange, isNearBottomOfRange } from './chartLabelPlacement'
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

  // A profit label fixed at "bottom" collides with the x-axis date
  // text whenever profit sits close to the bottom of the chart's own
  // value range -- which, for a pharmacy, is routine: thin or
  // negative margins are common, not an edge case. Rather than a
  // fixed pixel guess (fragile against font/margin changes), this
  // measures "close to bottom" the same way the chart itself does:
  // relative to the actual plotted value range across both series
  // (including 0, since the y-axis baseline is always visible).
  // Below that threshold, the label renders above its point instead.
  const plottedRange = computePlottedRange(data.points.flatMap((p) => [p.revenue, p.profit ?? 0]))

  function renderProfitLabel(props: RechartsLabelProps): ReactElement {
    const x = Number(props.x ?? 0)
    const y = Number(props.y ?? 0)
    const numericValue = Number(props.value)
    const isNearBottom = isNearBottomOfRange(numericValue, plottedRange)
    return (
      <text
        x={x}
        y={isNearBottom ? y - 8 : y + 16}
        textAnchor="middle"
        fontSize={10}
        fill="var(--color-stamp-green)"
      >
        {formatCurrency(numericValue)}
      </text>
    )
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
          margin={{ top: 20, right: 16, left: 8, bottom: hasProfit && showLabels ? 20 : 8 }}
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
              {showLabels && <LabelList dataKey="profit" content={renderProfitLabel} />}
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
