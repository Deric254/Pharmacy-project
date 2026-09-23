from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class SalesSummaryEntry(BaseModel):
    period: str  # "2026-07-04" for daily, "2026-07" for monthly
    sale_count: int
    total_revenue: float
    total_discount: float


class SalesSummaryOut(BaseModel):
    entries: list[SalesSummaryEntry]
    total_revenue: float
    total_sale_count: int


class ProfitReportOut(BaseModel):
    start_date: date
    end_date: date
    total_revenue: float
    total_cost: float
    total_profit: float
    profit_margin_percent: float


class ProfitByProductEntry(BaseModel):
    product_id: int
    name: str
    net_quantity_sold: int
    revenue: float
    cost: float
    profit: float
    profit_margin_percent: float | None


class ExpiredStockEntry(BaseModel):
    batch_id: int
    product_id: int
    product_name: str
    batch_number: str
    expiry_date: date
    days_expired: int
    qty_remaining: int
    value_at_cost: float


class ExpiredStockReportOut(BaseModel):
    entries: list[ExpiredStockEntry]
    total_value: float
    recommendation: str


class ProductMovementEntry(BaseModel):
    product_id: int
    name: str
    quantity_sold: int


class NeverSoldEntry(BaseModel):
    product_id: int
    name: str


class FastSlowMoversOut(BaseModel):
    period_days: int
    fast_movers: list[ProductMovementEntry]
    slow_movers: list[ProductMovementEntry]
    never_sold: list[NeverSoldEntry]


class ReceivingDiscrepancyEntry(BaseModel):
    purchase_order_id: int
    item_id: int
    product_id: int
    product_name: str
    quantity_ordered: int
    quantity_received: int
    variance: int


class ReceivingDiscrepancyReportOut(BaseModel):
    entries: list[ReceivingDiscrepancyEntry]
    recommendation: str


class StockTakeHistoryEntry(BaseModel):
    stock_take_id: int
    started_at: datetime
    closed_at: datetime | None
    shrinkage_value: float
    shrinkage_percent: float
    excess_value: float
    excess_percent: float
    net_variance_value: float  # excess_value - shrinkage_value; negative = net loss


class StockTakeHistoryOut(BaseModel):
    entries: list[StockTakeHistoryEntry]


class TopProductEntry(BaseModel):
    product_id: int
    name: str
    quantity_sold: int
    revenue: float


class TopCustomerEntry(BaseModel):
    customer_id: int
    name: str
    sale_count: int
    revenue: float
    # Running total as % of all revenue in the period -- the real Pareto number.
    cumulative_percent: float


class TopCustomersOut(BaseModel):
    entries: list[TopCustomerEntry]
    total_revenue: float


class CashierSalesEntry(BaseModel):
    cashier_user_id: int
    cashier_name: str
    sale_count: int
    # Net of refunds against that cashier's sales in the period, same
    # netting rule as TopCustomerEntry.revenue above -- a refunded sale
    # doesn't count as full revenue just because it happened before
    # the refund did.
    revenue: float


class CashierSalesOut(BaseModel):
    start_date: date
    end_date: date
    entries: list[CashierSalesEntry]


class KpiDashboardOut(BaseModel):
    start_date: date
    end_date: date

    revenue: float
    transaction_count: int
    average_basket: float
    revenue_change_percent: float | None  # vs the immediately preceding period of equal length

    # None entirely for anyone without reports.view_profit -- not
    # zeroed out, which would look like a real (bad) number rather
    # than "you can't see this".
    profit: float | None
    profit_margin_percent: float | None

    top_products: list[TopProductEntry]
    low_stock_count: int
    expiring_soon_count: int


class RevenuePotentialEntry(BaseModel):
    product_id: int
    name: str
    qty_on_hand: int
    potential_revenue: float
    potential_cost: float
    potential_gross_profit: float


class RevenuePotentialOut(BaseModel):
    """
    A real hypothetical, not a forecast: exactly what would happen if
    every unit currently in stock sold at today's price, computed
    entirely from real current stock and real recorded batch costs.
    Says nothing about *when* this would happen, or whether it will --
    that depends on real customer demand this system has no way to
    predict. See its own field for the honest caveat.
    """

    total_potential_revenue: float
    total_potential_cost: float
    total_potential_gross_profit: float
    overall_margin_percent: float | None
    by_product: list[RevenuePotentialEntry]
    caveat: str


class StockRunwayEntry(BaseModel):
    product_id: int
    name: str
    qty_on_hand: int
    units_sold_in_window: int
    avg_daily_sales: float
    # None when there's no recent sales history to extrapolate from --
    # never a fabricated number, and never "infinite" either.
    days_remaining: float | None


class StockRunwayOut(BaseModel):
    """
    A transparent extrapolation from real recent sales, not a
    forecast: at the pace of the last `lookback_days`, this is how
    long current stock would last if nothing about demand changes.
    Real demand changes constantly (season, promotions, new
    competitors, a supplier issue elsewhere) -- this is a starting
    point for reorder timing, not a guarantee of what will happen.
    """

    lookback_days: int
    entries: list[StockRunwayEntry]
    caveat: str


class ProductPairEntry(BaseModel):
    product_a_id: int
    product_a_name: str
    product_b_id: int
    product_b_name: str
    # Distinct sales containing both -- see the service method's own
    # docstring for why this is deduplicated by (sale_id, product_id)
    # first, not a raw SaleItem count.
    co_occurrence_count: int
    # Of sales containing product_a, the % that also contained
    # product_b -- the actionable "if X, how often Y too" number, not
    # just a raw count that means nothing without a denominator.
    percent_of_a_sales: float


class ProductCoOccurrenceOut(BaseModel):
    lookback_days: int
    pairs: list[ProductPairEntry]


class SeasonalTrendEntry(BaseModel):
    product_id: int
    name: str
    month: int  # 1-12, calendar month, summed across every year in range
    total_quantity_sold: int


class SeasonalTrendsOut(BaseModel):
    lookback_days: int
    entries: list[SeasonalTrendEntry]
    # False until there's enough real history to call a single-month
    # spike a "pattern" rather than one occurrence -- see the service
    # method's own docstring for the exact threshold and reasoning.
    has_sufficient_history: bool


class RevenueTrendPoint(BaseModel):
    # e.g. "2026-07-15" (day), "2026-07-14" (that week's Monday), "2026-07" (month)
    period_label: str
    revenue: float
    profit: float | None  # None entirely for anyone without reports.view_profit
    transaction_count: int


class RevenueTrendOut(BaseModel):
    granularity: Literal["day", "week", "month"]
    points: list[RevenueTrendPoint]
