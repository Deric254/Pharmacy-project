from datetime import date, datetime

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


class StockTakeHistoryOut(BaseModel):
    entries: list[StockTakeHistoryEntry]
