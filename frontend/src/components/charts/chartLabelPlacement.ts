/**
 * The fraction of the plotted value range (from its minimum, always
 * including 0 since the y-axis baseline is always visible) below
 * which a point counts as "near the bottom" -- close enough to the
 * x-axis that a label placed below it would collide with the axis
 * text. See RevenueTrendChart's own comment for the real bug this
 * fixes: thin or negative pharmacy margins routinely sit near zero,
 * which is routine, not an edge case.
 */
const NEAR_BOTTOM_FRACTION = 0.15

export interface PlottedRange {
  min: number
  max: number
  range: number
}

/**
 * The value range a chart's points actually span, always including 0
 * -- matching how the chart's own y-axis baseline is computed. Falls
 * back to a range of 1 when every value is identical (min === max),
 * so a division against this range never divides by zero.
 */
export function computePlottedRange(values: number[]): PlottedRange {
  const allValues = [...values, 0]
  const min = Math.min(...allValues)
  const max = Math.max(...allValues)
  return { min, max, range: max - min || 1 }
}

/**
 * Whether a value sits close enough to the bottom of the plotted
 * range that a label below its point would collide with the x-axis.
 */
export function isNearBottomOfRange(value: number, plottedRange: PlottedRange): boolean {
  return (value - plottedRange.min) / plottedRange.range < NEAR_BOTTOM_FRACTION
}
