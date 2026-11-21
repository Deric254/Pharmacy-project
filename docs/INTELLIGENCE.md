# Business Intelligence — definitions (Pharmacy Pro)

The eight intelligence areas are one reporting standard shared with SME Pro. This
file is the reference for what each term means here. The implementation is
`backend/app/services/intelligence_service.py`, exposed at
`GET /api/v1/reports/intelligence?days=` (permission `reports.view`) and shown in
**Reports → Intelligence**. It is read-only: it writes nothing and adds no table or
migration. Wherever an existing report already answers a question it is called, not
re-implemented: KPI dashboard, profit and profit by product, top customers, stock
runway, expired stock, and stock valuation with its dead-capital rule.

## An area counts as implemented only when

it analyses the stored records, produces findings the data supports, presents them
plainly, and shows the evidence. Stored data or an existing report is not enough.
Where essential information is not recorded, the area says what is missing and
produces only what the data supports. Nothing is estimated to fill a gap.

## The insight

Every finding has: `what_happened`, `evidence`, `period`, `comparison` (against the
equal period before it), `why_it_matters`, `impact` (amount and the basis it is
measured on), `recommended_action`, `confidence` (level plus stated limitations) and
`follow_up`. Severity: `problem`, `watch`, `info`, `good`. Confidence starts `high`;
a stated limitation makes it `medium`; thin coverage or too little history makes it
`low`; under 20 records adds a limitation. Whether a past finding later cleared is
not tracked (that needs stored snapshots); each finding states what to re-check.

## Periods

`days` (7–180, default 30) ending on the business's today (its own timezone) is the
current period; the equal number of days before it is the previous period; the same
again before that is used for growth. A comparison is made only when recorded sales
history fully covers the previous period; otherwise the area says how much history
exists and gives no comparison.

## Measures

| Measure | Definition here |
|---|---|
| Revenue | Net of refunds, exactly as the app's other reports define it |
| Gross profit / margin | Revenue minus cost at the time of sale. Every sale line records its cost, so there is no cost-coverage gap |
| Sales (transactions) | Number of sales, as in the KPI dashboard |
| Identified customer | A sale with a customer attached; walk-in sales are not counted |
| Stock value | Non-expired batches at cost, as Stock Valuation counts it |
| Dead stock | The app's own dead-capital rule (unexpired batches older than the dead-stock days with no sale of the product in that time) |
| Cash movement | Cash received (payments on sales) minus refunds paid minus supplier payments |
| Supplier payment | A negative supplier-ledger entry with no reversal link, or a positive entry that reverses one |
| Supplier balance owed | Sum of a supplier's ledger when positive |

Profit and anything built on cost is withheld from a role without `reports.view_profit`,
as every other profit figure is.

## Pharmacy-specific findings

- **Stock that will expire before it sells.** For each batch expiring within 90
  days: units ahead of it in expiry order must also sell first (the app dispenses the
  earliest expiry first); expected sales until its expiry = the 30-day average daily
  pace × days left; units above that are at risk, valued at batch cost. A product with
  no recent sales has all its units counted as at risk, and says so.
- **Expired stock still on the shelf**, **dead stock** and **sold-out sellers**.
  Sellable stock excludes expired batches and batches locked by a stock take.
- **Supplier price increases.** The same supplier and product, received purchase
  orders in the two periods, cost per unit at least 5% higher; impact is the extra
  paid on the quantity received. A changed pack size looks like a price change, and
  this is stated.
- **Supplier concentration**, **supplier balances against recent takings**, and
  **payment-method mix shift** (≥10 points).

## Rules that fire

Shared with SME Pro, so the same thresholds apply: sales change ≥15% (problem at
≤ −30%, at least 10 sales across the two periods); product decline (a product with ≥3% of
last period's revenue lost ≥40%); concentration (≥4 products and top ≥40%, or top three
≥70%); margin shift ≥3 points (problem at ≤ −6); items below cost; profit
concentration (top ≥50%); discounts up ≥2 points; customer concentration (top ≥30% or
top three ≥60%); lapsed customers (≥3 and ≥10% of last period's identified revenue);
retention (<50% or ≥75% with ≥5 customers); purchasing change ±30%; scorecard (±5%, ±1
point for rates); refund share up ≥2 points; growth over two periods (±5%) or a
direction change ≥10%; growing products (+30%); new products (≥2% of revenue); customer
growth (±30% with ≥3 in each period); outlook (three covered periods, average with
low–high range).

## What the app does not record (stated in the report, never guessed)

- **Operating expenses** (rent, wages, utilities). Expense Intelligence therefore
  covers purchasing, supplier prices, refunds and discounts only, and is marked partial.
- **Opening cash, till or bank balances.** Cash Flow analyses movement, not position
  or runway.
- **Customer credit.** There are no receivables, so none are reported.
- **Supplier due dates.** What is overdue cannot be told; balances are compared with
  recent takings instead.
- **Targets and budgets.** Performance compares with the previous period only.
- **Growth drivers** (marketing, capacity). Growth is described from sales, customers
  and products only.
