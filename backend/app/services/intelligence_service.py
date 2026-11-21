# ruff: noqa: E501  (findings are written as plain sentences; wrapping them would only hurt readability)
"""Business intelligence: the eight formal areas (Sales, Profitability,
Inventory, Customer, Expense, Cash Flow, Business Performance, Business
Growth), each producing structured insights.

Read-only: nothing here writes, and no table is added. Every figure comes
from records the app already holds, and wherever an existing report already
answers a question it is called rather than re-implemented (profit and
profit by product, KPI dashboard, top customers, stock runway, expired
stock, stock valuation and dead capital).

The insight shape, the period rules and the thresholds are the ones shared
with SME Pro (see INTELLIGENCE.md). A finding is produced only when the
data supports it; otherwise the area says what is missing. Nothing is
estimated to fill a gap.

Pharmacy-specific logic: expiry write-off risk per batch (first-expiring
batches are assumed to sell first, as the app's own FEFO dispensing does),
supplier price changes from received purchase orders, supplier balances,
and payment-method mix.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.business_time import business_today, local_day_bounds_utc
from app.models.business_config import BusinessConfig
from app.models.medicine_batch import MedicineBatch
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus
from app.models.refund import Refund
from app.models.sale import Payment, Sale, SaleItem
from app.models.supplier import Supplier, SupplierTransaction
from app.schemas.intelligence import IntelligenceReportOut
from app.services.inventory_service import InventoryService
from app.services.report_service import ReportService

# ---------------------------------------------------------------- helpers


def pct_change(cur: float, prev: float) -> float | None:
    return None if prev == 0 else (cur - prev) / abs(prev) * 100.0


def pct(part: float, whole: float) -> float | None:
    return None if whole <= 0 else part / whole * 100.0


def _r2(x: float) -> float:
    return round(x + 0.0, 2)


def _top(pairs: list[tuple[str, float]], key: Any, n: int) -> list[tuple[str, float]]:
    return sorted(pairs, key=lambda p: (-key(p), p[0]))[:n]


@dataclass
class Win:
    revenue: float = 0.0  # net of refunds, as every report here defines it
    orders: int = 0
    profit: float | None = None
    refunds: float = 0.0
    subtotal: float = 0.0
    discounts: float = 0.0
    payments: dict[str, float] = field(default_factory=dict)

    @property
    def aov(self) -> float | None:
        return self.revenue / self.orders if self.orders else None

    @property
    def margin(self) -> float | None:
        return None if self.profit is None else pct(self.profit, self.revenue)


@dataclass
class Itm:
    name: str
    qty: int
    revenue: float
    profit: float | None


@dataclass
class Cust:
    name: str
    revenue: float
    sales: int


@dataclass
class Data:
    cur: Win
    prev: Win
    prev2: Win
    items_cur: dict[int, Itm]
    items_prev: dict[int, Itm]
    cust_cur: dict[int, Cust]
    cust_prev: dict[int, Cust]
    cust_first: dict[int, dt.datetime]
    prod_first: dict[int, dt.datetime]
    low_stock: int
    history_days: int
    prev_ok: bool
    prev2_ok: bool


class Ctx:
    def __init__(
        self, db: AsyncSession, today: dt.date, days: int, currency: str, include_profit: bool
    ):
        self.db = db
        self.today = today
        self.days = days
        self.currency = currency
        self.include_profit = include_profit
        d = dt.timedelta(days=days)
        self.cur = (today - d + dt.timedelta(days=1), today)
        self.prev = (today - 2 * d + dt.timedelta(days=1), today - d)
        self.prev2 = (today - 3 * d + dt.timedelta(days=1), today - 2 * d)
        self.cur_utc: tuple[dt.datetime, dt.datetime] | None = None

    def money(self, x: float) -> str:
        return f"{self.currency} {x:,.2f}"

    def period(self, which: str = "cur") -> dict[str, str]:
        s, e = self.cur if which == "cur" else self.prev
        label = f"Last {self.days} days" if which == "cur" else f"Previous {self.days} days"
        return {"label": label, "start": s.isoformat(), "end": e.isoformat()}


def new_insight(ctx: Ctx, area: str, kind: str, title: str, severity: str) -> dict[str, Any]:
    return {
        "area": area,
        "kind": kind,
        "title": title,
        "severity": severity,
        "what_happened": "",
        "evidence": [],
        "period": ctx.period(),
        "comparison": None,
        "why_it_matters": "",
        "impact": None,
        "recommended_action": "",
        "confidence": {"level": "high", "limitations": []},
        "follow_up": None,
    }


def ev(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def impact(amount: float, basis: str) -> dict[str, Any]:
    return {"amount": _r2(amount), "basis": basis}


def confidence(limitations: list[str], weak: bool, sample: int) -> dict[str, Any]:
    """Derived, never asserted: a stated limitation lowers it to medium, thin
    coverage or too little history to low, and under 20 records adds its own
    limitation."""
    lim = list(limitations)
    if sample < 20:
        lim.append(f"Based on only {sample} record{'' if sample == 1 else 's'}.")
    level = "low" if weak else ("medium" if lim else "high")
    return {"level": level, "limitations": lim}


def comparison(ctx: Ctx, label: str, unit: str, cur: float, prev: float) -> dict[str, Any]:
    return {
        "label": label,
        "period": ctx.period("prev"),
        "current": _r2(cur),
        "previous": _r2(prev),
        "change_pct": None if unit == "percent" else pct_change(cur, prev),
        "unit": unit,
    }


def kpi(
    label: str, unit: str, value: float | None, previous: float | None = None
) -> dict[str, Any]:
    change = None
    if value is not None and previous is not None and unit != "percent":
        change = pct_change(value, previous)
    return {
        "label": label,
        "value": None if value is None else _r2(value),
        "previous": None if previous is None else _r2(previous),
        "change_pct": change,
        "unit": unit,
    }


def area(area_id: str, title: str) -> dict[str, Any]:
    return {
        "id": area_id,
        "title": title,
        "status": "full",
        "kpis": [],
        "insights": [],
        "missing": [],
        "notes": [],
    }


def unavailable(area_id: str, title: str, reason: str) -> dict[str, Any]:
    a = area(area_id, title)
    a["status"] = "unavailable"
    a["missing"].append(reason)
    return a


def weekday_limitation(ctx: Ctx) -> str | None:
    if ctx.days % 7 != 0:
        return "The two periods are not a whole number of weeks, so their weekday mix can differ."
    return None


def history_limitation(d: Data, ctx: Ctx) -> str | None:
    if d.prev_ok:
        return None
    return (
        f"Only {d.history_days} day(s) of sales history exist, less than the "
        f"{ctx.days * 2} needed for a full previous period."
    )


# ------------------------------------------------------------- data loading


async def _window(ctx: Ctx, start: dt.date, end: dt.date) -> tuple[Win, int]:
    svc = ReportService(ctx.db)
    kpis = await svc.kpi_dashboard(start, end, ctx.include_profit)
    utc_start, utc_end = await local_day_bounds_utc(ctx.db, start, end)
    refunds = float(
        await ctx.db.scalar(
            select(func.coalesce(func.sum(Refund.total_amount), 0.0)).where(
                Refund.created_at >= utc_start, Refund.created_at < utc_end
            )
        )
        or 0.0
    )
    sub, disc = (
        await ctx.db.execute(
            select(
                func.coalesce(func.sum(Sale.subtotal), 0.0),
                func.coalesce(func.sum(Sale.discount_amount), 0.0),
            ).where(Sale.created_at >= utc_start, Sale.created_at < utc_end)
        )
    ).one()
    pays = (
        await ctx.db.execute(
            select(Payment.method, func.coalesce(func.sum(Payment.amount), 0.0))
            .join(Sale, Sale.id == Payment.sale_id)
            .where(Sale.created_at >= utc_start, Sale.created_at < utc_end)
            .group_by(Payment.method)
        )
    ).all()
    w = Win(
        revenue=float(kpis.revenue),
        orders=int(kpis.transaction_count),
        profit=None if kpis.profit is None else float(kpis.profit),
        refunds=refunds,
        subtotal=float(sub),
        discounts=float(disc),
        payments={str(getattr(m, "value", m)): float(a) for m, a in pays},
    )
    return w, int(kpis.low_stock_count)


async def _items(ctx: Ctx, start: dt.date, end: dt.date) -> dict[int, Itm]:
    rows = await ReportService(ctx.db).profit_by_product(start, end)
    return {
        r.product_id: Itm(
            r.name,
            int(r.net_quantity_sold),
            float(r.revenue),
            float(r.profit) if ctx.include_profit else None,
        )
        for r in rows
    }


async def _customers(ctx: Ctx, start: dt.date, end: dt.date) -> dict[int, Cust]:
    out = await ReportService(ctx.db).top_customers(start, end, limit=100000)
    return {e.customer_id: Cust(e.name, float(e.revenue), int(e.sale_count)) for e in out.entries}


async def load_data(ctx: Ctx) -> Data:
    db = ctx.db
    cur, low = await _window(ctx, *ctx.cur)
    prev, _ = await _window(ctx, *ctx.prev)
    prev2, _ = await _window(ctx, *ctx.prev2)
    first_sale = await db.scalar(select(func.min(Sale.created_at)))

    async def covered(start: dt.date) -> bool:
        if first_sale is None:
            return False
        _, day_end = await local_day_bounds_utc(db, start, start)
        return bool(first_sale < day_end)

    history_days = 0 if first_sale is None else (ctx.today - first_sale.date()).days + 1
    cust_first = {
        int(c): f
        for c, f in (
            await db.execute(
                select(Sale.customer_id, func.min(Sale.created_at))
                .where(Sale.customer_id.is_not(None))
                .group_by(Sale.customer_id)
            )
        ).all()
    }
    prod_first = {
        int(p): f
        for p, f in (
            await db.execute(
                select(SaleItem.product_id, func.min(Sale.created_at))
                .join(Sale, Sale.id == SaleItem.sale_id)
                .group_by(SaleItem.product_id)
            )
        ).all()
    }
    ctx.cur_utc = await local_day_bounds_utc(db, *ctx.cur)
    return Data(
        cur=cur,
        prev=prev,
        prev2=prev2,
        items_cur=await _items(ctx, *ctx.cur),
        items_prev=await _items(ctx, *ctx.prev),
        cust_cur=await _customers(ctx, *ctx.cur),
        cust_prev=await _customers(ctx, *ctx.prev),
        cust_first=cust_first,
        prod_first=prod_first,
        low_stock=low,
        history_days=history_days,
        prev_ok=await covered(ctx.prev[0]),
        prev2_ok=await covered(ctx.prev2[0]),
    )


# ------------------------------------------------------------------ sales


def sales_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("sales", "Sales Intelligence")
    cur, prev = d.cur, d.prev
    pv = (lambda x: x) if d.prev_ok else (lambda x: None)
    a["kpis"] = [
        kpi("Revenue", "money", cur.revenue, pv(prev.revenue)),
        kpi("Sales (transactions)", "count", cur.orders, pv(prev.orders)),
        kpi("Average basket", "money", cur.aov, pv(prev.aov)),
        kpi(
            "Units sold",
            "count",
            sum(i.qty for i in d.items_cur.values()),
            pv(sum(i.qty for i in d.items_prev.values())),
        ),
    ]
    if cur.orders == 0:
        a["notes"].append("No sales are recorded in this period.")
    lim_h = history_limitation(d, ctx)
    if lim_h:
        a["notes"].append(lim_h)

    if d.prev_ok and prev.revenue > 0 and cur.orders + prev.orders >= 10:
        change = pct_change(cur.revenue, prev.revenue)
        if change is not None and abs(change) >= 15.0:
            names = set(d.items_cur) | set(d.items_prev)
            deltas = [
                (
                    d.items_cur[p].name if p in d.items_cur else d.items_prev[p].name,
                    (d.items_cur[p].revenue if p in d.items_cur else 0.0)
                    - (d.items_prev[p].revenue if p in d.items_prev else 0.0),
                )
                for p in names
            ]
            deltas = [x for x in deltas if (x[1] < 0) == (change < 0) and x[1] != 0]
            deltas = _top(deltas, lambda x: abs(x[1]), 3)
            sev = "problem" if change <= -30.0 else ("watch" if change < 0 else "good")
            i = new_insight(
                ctx,
                "sales",
                "sales_change",
                "Sales fell against the previous period"
                if change < 0
                else "Sales rose against the previous period",
                sev,
            )
            i["what_happened"] = (
                f"Revenue was {ctx.money(cur.revenue)} in this period against {ctx.money(prev.revenue)} "
                f"in the equal period before it ({change:+.1f}%)."
            )
            i["evidence"] = [
                ev("Revenue this period", ctx.money(cur.revenue)),
                ev("Revenue previous period", ctx.money(prev.revenue)),
                ev("Sales (transactions)", f"{cur.orders} vs {prev.orders}"),
            ]
            i["evidence"] += [
                ev(f"Biggest driver: {n}", f"{'-' if x < 0 else '+'}{ctx.money(abs(x))}")
                for n, x in deltas
            ]
            i["comparison"] = comparison(ctx, "Revenue", "money", cur.revenue, prev.revenue)
            i["why_it_matters"] = (
                "A sustained drop in sales reduces the cash and profit available to cover costs."
                if change < 0
                else "A real rise shows what is working and what stock and cash it will need to keep up."
            )
            i["impact"] = impact(
                cur.revenue - prev.revenue, "Revenue difference between the two periods"
            )
            i["recommended_action"] = (
                "Look at the products driving the drop first: check stock availability, price changes and whether regular customers stopped buying."
                if change < 0
                else "Check the products driving the rise have enough stock and a healthy margin before relying on it continuing."
            )
            lim = [x for x in [weekday_limitation(ctx)] if x]
            i["confidence"] = confidence(lim, False, min(cur.orders, prev.orders))
            i["follow_up"] = (
                f"Re-check after the next {ctx.days} days: does revenue stay near {ctx.money(cur.revenue)}?"
            )
            a["insights"].append(i)

    if d.prev_ok and prev.revenue > 0:
        falling = []
        for p, pi in d.items_prev.items():
            if pi.revenue >= prev.revenue * 0.03:
                c = d.items_cur[p].revenue if p in d.items_cur else 0.0
                if c <= pi.revenue * 0.6:
                    falling.append((pi.name, pi.revenue - c))
        if falling:
            falling = _top(falling, lambda x: x[1], 5)
            i = new_insight(
                ctx, "sales", "product_decline", "Products with a sharp sales drop", "watch"
            )
            i["what_happened"] = (
                f"{len(falling)} product(s) that mattered last period lost at least 40% of their revenue this period."
            )
            i["evidence"] = [ev(n, f"-{ctx.money(x)}") for n, x in falling]
            i["why_it_matters"] = (
                "These were meaningful sellers; losing them explains part of any overall change."
            )
            i["impact"] = impact(
                -sum(x for _, x in falling),
                "Revenue lost on these products versus the previous period",
            )
            i["recommended_action"] = (
                "Check whether each is out of stock, repriced, or replaced by something customers now prefer."
            )
            lim = [x for x in [weekday_limitation(ctx)] if x]
            i["confidence"] = confidence(lim, False, min(cur.orders, prev.orders))
            a["insights"].append(i)

    if len(d.items_cur) >= 4 and cur.revenue > 0:
        v = _top([(i.name, i.revenue) for i in d.items_cur.values()], lambda x: x[1], 3)
        t1, t3 = pct(v[0][1], cur.revenue) or 0.0, pct(sum(x for _, x in v), cur.revenue) or 0.0
        if t1 >= 40.0 or t3 >= 70.0:
            i = new_insight(
                ctx, "sales", "sales_concentration", "Sales depend on very few products", "watch"
            )
            i["what_happened"] = (
                f"{v[0][0]} accounts for {t1:.0f}% of revenue, and the top three products for {t3:.0f}%."
            )
            i["evidence"] = [
                ev(n, f"{ctx.money(x)} ({pct(x, cur.revenue) or 0:.0f}%)") for n, x in v
            ]
            i["why_it_matters"] = (
                "If one of these stops selling or runs out, a large part of revenue goes with it."
            )
            i["impact"] = impact(v[0][1], "Revenue from the single largest product")
            i["recommended_action"] = (
                "Keep these well stocked and look for a second line of products that can carry part of the load."
            )
            i["confidence"] = confidence([], False, cur.orders)
            a["insights"].append(i)
    return a


# ---------------------------------------------------------- profitability


def profit_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    if not ctx.include_profit:
        return unavailable(
            "profitability",
            "Profitability Intelligence",
            "Profit figures need the View profit permission.",
        )
    a = area("profitability", "Profitability Intelligence")
    cur, prev = d.cur, d.prev
    pv = (lambda x: x) if d.prev_ok else (lambda x: None)

    def disc_rate(w: Win) -> float | None:
        return pct(w.discounts, w.subtotal)

    a["kpis"] = [
        kpi("Gross profit", "money", cur.profit, pv(prev.profit)),
        kpi("Gross margin", "percent", cur.margin, pv(prev.margin)),
        kpi(
            "Discounts as share of sales before discount",
            "percent",
            disc_rate(cur),
            pv(disc_rate(prev)),
        ),
    ]
    a["notes"].append(
        "Every sale line records its cost at the moment of sale, so profit does not depend on cost entered later."
    )
    if cur.orders == 0:
        a["notes"].append("No sales are recorded in this period.")
        return a

    if (
        d.prev_ok
        and cur.revenue > 0
        and prev.revenue > 0
        and cur.margin is not None
        and prev.margin is not None
    ):
        delta = cur.margin - prev.margin
        if abs(delta) >= 3.0:
            names = set(d.items_cur) | set(d.items_prev)
            drivers = []
            for p in names:
                c = d.items_cur.get(p)
                q = d.items_prev.get(p)
                cp = (c.profit or 0.0) if c else 0.0
                pp = (q.profit or 0.0) if q else 0.0
                nm = c.name if c else q.name  # type: ignore[union-attr]
                if (cp - pp < 0) == (delta < 0) and cp != pp:
                    drivers.append((nm, cp - pp))
            drivers = _top(drivers, lambda x: abs(x[1]), 3)
            sev = "problem" if delta <= -6.0 else ("watch" if delta < 0 else "good")
            i = new_insight(
                ctx,
                "profitability",
                "margin_shift",
                "Gross margin has narrowed" if delta < 0 else "Gross margin has widened",
                sev,
            )
            i["what_happened"] = (
                f"Gross margin was {cur.margin:.1f}% against {prev.margin:.1f}% in the previous period ({delta:+.1f} points)."
            )
            i["evidence"] = [
                ev("Gross profit this period", ctx.money(cur.profit or 0.0)),
                ev("Gross profit previous period", ctx.money(prev.profit or 0.0)),
            ]
            i["evidence"] += [
                ev(f"Profit change: {n}", f"{'-' if x < 0 else '+'}{ctx.money(abs(x))}")
                for n, x in drivers
            ]
            i["comparison"] = comparison(ctx, "Gross margin", "percent", cur.margin, prev.margin)
            i["why_it_matters"] = (
                "Each sale now keeps less of its price, so the same revenue produces less profit."
                if delta < 0
                else "Each sale now keeps more of its price."
            )
            i["impact"] = impact(
                delta / 100.0 * cur.revenue, "Margin change applied to this period's revenue"
            )
            i["recommended_action"] = (
                "Check whether supplier costs rose or discounts deepened on the products listed, and reprice or renegotiate."
                if delta < 0
                else "Confirm the gain comes from pricing or cost savings you can keep, not a one-off."
            )
            lim = [x for x in [weekday_limitation(ctx)] if x]
            i["confidence"] = confidence(lim, False, min(cur.orders, prev.orders))
            i["follow_up"] = (
                f"Next check: does margin hold near {cur.margin:.1f}% over the following {ctx.days} days?"
            )
            a["insights"].append(i)

    losers = [
        (i.name, i.profit) for i in d.items_cur.values() if i.profit is not None and i.profit < 0
    ]
    if losers:
        losers = sorted(losers, key=lambda x: (x[1], x[0]))[:5]
        i = new_insight(
            ctx,
            "profitability",
            "loss_making_items",
            "Products selling below their cost",
            "problem",
        )
        i["what_happened"] = f"{len(losers)} product(s) cost more than they earned this period."
        i["evidence"] = [ev(n, f"-{ctx.money(abs(p))}") for n, p in losers]
        i["why_it_matters"] = "Every sale of these products reduces total profit."
        i["impact"] = impact(
            sum(p for _, p in losers), "Gross loss on the listed products this period"
        )
        i["recommended_action"] = (
            "Raise the price, renegotiate the cost, or stop selling the product below cost."
        )
        i["confidence"] = confidence([], False, cur.orders)
        a["insights"].append(i)

    gainers = [
        (i.name, i.profit) for i in d.items_cur.values() if i.profit is not None and i.profit > 0
    ]
    total_pos = sum(p for _, p in gainers)
    if len(gainers) >= 4 and total_pos > 0:
        g = _top(gainers, lambda x: x[1], 3)
        share = pct(g[0][1], total_pos) or 0.0
        if share >= 50.0:
            i = new_insight(
                ctx,
                "profitability",
                "profit_concentration",
                "Most profit comes from one product",
                "watch",
            )
            i["what_happened"] = (
                f"{g[0][0]} produced {share:.0f}% of the profit made on profitable products."
            )
            i["evidence"] = [ev(n, ctx.money(p)) for n, p in g]
            i["why_it_matters"] = (
                "Profit is fragile if this one product's price, cost or demand changes."
            )
            i["impact"] = impact(g[0][1], "Gross profit from the single most profitable product")
            i["recommended_action"] = (
                "Protect its supply and price, and build up other profitable products."
            )
            i["confidence"] = confidence([], False, cur.orders)
            a["insights"].append(i)

    rc, rp = disc_rate(cur), disc_rate(prev)
    if d.prev_ok and rc is not None and rp is not None and rc - rp >= 2.0 and cur.discounts > 0:
        extra = (rc - rp) / 100.0 * cur.subtotal
        i = new_insight(
            ctx,
            "profitability",
            "discount_leakage",
            "Discounts are taking a bigger share of sales",
            "watch",
        )
        i["what_happened"] = (
            f"Discounts were {rc:.1f}% of sales before discount, up from {rp:.1f}% in the previous period."
        )
        i["evidence"] = [
            ev("Discounts given this period", ctx.money(cur.discounts)),
            ev("Discounts previous period", ctx.money(prev.discounts)),
        ]
        i["comparison"] = comparison(ctx, "Discount share", "percent", rc, rp)
        i["why_it_matters"] = (
            "Discounts come straight out of profit; a rising share without more sales is margin given away."
        )
        i["impact"] = impact(-extra, "Extra discount versus the previous period's share")
        i["recommended_action"] = (
            "Check who is approving the larger discounts and on which products."
        )
        i["confidence"] = confidence([], False, cur.orders)
        a["insights"].append(i)
    return a


# --------------------------------------------------------------- inventory


async def inventory_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("inventory", "Inventory Intelligence")
    db = ctx.db
    today = ctx.today
    val = await InventoryService(db).get_valuation()
    a["kpis"] = [
        kpi("Stock value at cost", "money", val.total_value),
        kpi("Stock not sold for a long time", "money", val.dead_capital),
        kpi("Products at or below reorder point", "count", d.low_stock),
    ]
    runway = (await ReportService(db).stock_runway(30)).entries
    by_id = {e.product_id: e for e in runway}
    weak_hist = d.history_days < 14

    # 1. Sold this period but now out of stock. stock_runway leaves out products with no stock, so
    # this reads sellable stock directly, counted the way the app counts it: batches that have not
    # expired and are not locked by a stock take.
    sellable: dict[int, int] = {}
    if d.items_cur:
        sellable = {
            int(pid): int(qty)
            for pid, qty in (
                await db.execute(
                    select(MedicineBatch.product_id, func.sum(MedicineBatch.qty_remaining))
                    .where(
                        MedicineBatch.product_id.in_(list(d.items_cur)),
                        MedicineBatch.expiry_date > today,
                        MedicineBatch.locked_by_stock_take_id.is_(None),
                    )
                    .group_by(MedicineBatch.product_id)
                )
            ).all()
        }
    gone = [
        (pid, it) for pid, it in d.items_cur.items() if it.qty > 0 and sellable.get(pid, 0) <= 0
    ]
    a["kpis"].append(kpi("Selling products out of stock", "count", len(gone)))
    if gone:
        rows = _top([(it.name, it.revenue) for _, it in gone], lambda x: x[1], 5)
        i = new_insight(
            ctx,
            "inventory",
            "sold_out_sellers",
            "Products that were selling are now out of stock",
            "problem",
        )
        i["what_happened"] = (
            f"{len(gone)} product(s) that sold this period have no sellable stock left."
        )
        i["evidence"] = [ev(n, f"{ctx.money(r)} sold this period") for n, r in rows]
        i["why_it_matters"] = (
            "Every day these stay empty, the sales they were making are lost, and customers may go elsewhere."
        )
        i["impact"] = impact(
            sum(it.revenue for _, it in gone),
            "Revenue these products earned this period, at risk while out of stock",
        )
        i["recommended_action"] = "Reorder these first."
        i["confidence"] = confidence(
            ["Expired batches and batches locked by a stock take do not count as sellable."],
            False,
            d.cur.orders,
        )
        a["insights"].append(i)

    # 2. Running out within a week.
    risky = sorted(
        [
            e
            for e in runway
            if e.qty_on_hand > 0 and e.days_remaining is not None and e.days_remaining < 7.0
        ],
        key=lambda e: (e.days_remaining, e.name),
    )
    if risky:
        urgent = any(e.days_remaining is not None and e.days_remaining < 3.0 for e in risky)
        i = new_insight(
            ctx,
            "inventory",
            "stockout_risk",
            "Products that will run out within a week",
            "problem" if urgent else "watch",
        )
        i["what_happened"] = (
            f"{len(risky)} product(s) have under 7 days of stock at their recent selling pace."
        )
        i["evidence"] = [
            ev(
                e.name,
                f"{e.days_remaining:.1f} days left ({e.qty_on_hand} on hand, about {e.avg_daily_sales:.1f} sold per day)",
            )
            for e in risky[:5]
        ]
        i["why_it_matters"] = (
            "Running out of a steady seller means lost sales and customers going elsewhere."
        )
        i["recommended_action"] = "Reorder the products listed, starting with the fewest days left."
        lim = ["Based on the last 30 days of sales; stock already on order is not considered."]
        if weak_hist:
            lim.append("Under 14 days of sales history makes the selling pace unreliable.")
        i["confidence"] = confidence(lim, weak_hist, d.cur.orders)
        a["insights"].append(i)

    # 3. Capital in stock that has stopped selling (the app's own dead-capital definition).
    from app.services.inventory_service import DEAD_STOCK_DAYS

    if val.dead_capital > 0:
        cutoff = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=DEAD_STOCK_DAYS)
        dead_rows = (
            await db.execute(
                select(
                    Product.name, func.sum(MedicineBatch.qty_remaining * MedicineBatch.cost_price)
                )
                .join(Product, Product.id == MedicineBatch.product_id)
                .where(
                    Product.deleted_at.is_(None),
                    MedicineBatch.expiry_date > today,
                    MedicineBatch.qty_remaining > 0,
                    MedicineBatch.created_at < cutoff,
                    ~select(SaleItem.id)
                    .join(Sale, Sale.id == SaleItem.sale_id)
                    .where(
                        SaleItem.product_id == MedicineBatch.product_id, Sale.created_at >= cutoff
                    )
                    .exists(),
                )
                .group_by(Product.id, Product.name)
            )
        ).all()
        dead_top = _top([(n, float(v)) for n, v in dead_rows], lambda x: x[1], 5)
        share = pct(val.dead_capital, val.total_value) or 0.0
        i = new_insight(
            ctx,
            "inventory",
            "dead_stock",
            "Money tied up in stock that is not selling",
            "problem" if share >= 25.0 else "watch",
        )
        i["what_happened"] = (
            f"{ctx.money(val.dead_capital)} of stock at cost has not sold in {DEAD_STOCK_DAYS} days, {share:.0f}% of total stock value."
        )
        i["evidence"] = [ev(n, ctx.money(v)) for n, v in dead_top]
        i["why_it_matters"] = (
            "Cash sitting in unsold stock cannot pay suppliers or costs, and medicines in it will eventually expire."
        )
        i["impact"] = impact(
            val.dead_capital, f"Stock at cost with no sale in {DEAD_STOCK_DAYS} days"
        )
        i["recommended_action"] = (
            "Discount, return to the supplier or stop reordering the largest items until they move."
        )
        lim = []
        if d.history_days < DEAD_STOCK_DAYS:
            lim.append(
                f'Sales history covers only {d.history_days} day(s), so "no sale in {DEAD_STOCK_DAYS} days" cannot yet be judged properly.'
            )
        i["confidence"] = confidence(lim, d.history_days < DEAD_STOCK_DAYS, len(dead_rows))
        i["follow_up"] = "Re-run after 30 days: has the tied-up value fallen?"
        a["insights"].append(i)

    # 4. Already expired but still on the shelf (the app's own expired-stock report).
    exp = await ReportService(db).expired_stock()
    if exp.entries:
        exp_top = sorted(exp.entries, key=lambda e: (-e.value_at_cost, e.product_name))[:5]
        i = new_insight(
            ctx, "inventory", "expired_stock", "Expired stock still on the shelf", "problem"
        )
        i["what_happened"] = (
            f"{len(exp.entries)} batch(es) are past their expiry date and still count as stock, worth {ctx.money(exp.total_value)} at cost."
        )
        i["evidence"] = [
            ev(
                f"{e.product_name} (batch {e.batch_number})",
                f"{ctx.money(e.value_at_cost)}, expired {e.days_expired} day(s) ago",
            )
            for e in exp_top
        ]
        i["why_it_matters"] = (
            "Expired medicine must not be dispensed, and it is a direct write-off."
        )
        i["impact"] = impact(exp.total_value, "Cost of expired units still in stock")
        i["recommended_action"] = (
            "Quarantine and remove these batches, and ask suppliers about returns where they allow it."
        )
        i["confidence"] = confidence([], False, len(exp.entries))
        a["insights"].append(i)

    # 5. Stock that will expire before it can be sold (pharmacy-specific).
    horizon = today + dt.timedelta(days=90)
    batches = (
        await db.execute(
            select(
                MedicineBatch.product_id,
                Product.name,
                MedicineBatch.batch_number,
                MedicineBatch.expiry_date,
                MedicineBatch.qty_remaining,
                MedicineBatch.cost_price,
            )
            .join(Product, Product.id == MedicineBatch.product_id)
            .where(
                Product.deleted_at.is_(None),
                MedicineBatch.qty_remaining > 0,
                MedicineBatch.expiry_date > today,
                MedicineBatch.expiry_date <= horizon,
            )
            .order_by(MedicineBatch.product_id, MedicineBatch.expiry_date, MedicineBatch.id)
        )
    ).all()
    risk_rows: list[
        tuple[str, str, dt.date, float, float]
    ] = []  # name, batch, expiry, at-risk units, value
    cum_by: dict[int, int] = {}
    for pid, name, bno, expiry, qty, cost in batches:
        # first-expiring-first-out: units ahead of this batch must also sell before it expires
        prior = 0
        for b in batches:
            if b[0] == pid and (b[3], b[2]) < (expiry, bno):
                prior += b[4]
        daily = by_id[pid].avg_daily_sales if pid in by_id else 0.0
        expected = daily * (expiry - today).days
        at_risk = max(0.0, min(float(qty), prior + qty - expected))
        if at_risk >= 1.0:
            risk_rows.append((name, bno, expiry, at_risk, at_risk * float(cost)))
        cum_by[pid] = prior + qty
    if risk_rows:
        total = sum(r[4] for r in risk_rows)
        soon = any((r[2] - today).days <= 30 for r in risk_rows)
        risk_top = sorted(risk_rows, key=lambda r: (-r[4], r[0]))[:5]
        i = new_insight(
            ctx,
            "inventory",
            "expiry_write_off_risk",
            "Stock likely to expire before it sells",
            "problem" if soon else "watch",
        )
        i["what_happened"] = (
            f"{len(risk_rows)} batch(es) expiring within 90 days are unlikely to sell out in time at the recent selling pace, "
            f"about {ctx.money(total)} at cost."
        )
        i["evidence"] = [
            ev(
                f"{n} (batch {b}, expires {e.isoformat()})",
                f"about {u:.0f} unit(s), {ctx.money(v)}",
            )
            for n, b, e, u, v in risk_top
        ]
        i["why_it_matters"] = (
            "Units that expire unsold are written off at full cost; acting early can recover part of it."
        )
        i["impact"] = impact(total, "Cost of units expected to expire unsold")
        i["recommended_action"] = (
            "Promote or discount these first, move stock between shelves, or ask the supplier about returns before expiry."
        )
        lim = [
            "Assumes the last 30 days' selling pace continues and the earliest-expiring batch is dispensed first.",
            "A product with no recent sales has all its units counted as at risk.",
        ]
        i["confidence"] = confidence(lim, weak_hist, len(risk_rows))
        i["follow_up"] = "Re-check weekly: has the at-risk value fallen?"
        a["insights"].append(i)
    return a


# --------------------------------------------------------------- customers


def customer_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("customers", "Customer Intelligence")
    ident = sum(c.revenue for c in d.cust_cur.values())
    share = pct(ident, d.cur.revenue)
    n_cur, n_prev = len(d.cust_cur), len(d.cust_prev)
    start_utc = ctx.cur_utc[0] if ctx.cur_utc else None
    new_cur = sum(
        1 for k in d.cust_cur if start_utc and d.cust_first.get(k) and d.cust_first[k] >= start_utc
    )
    repeat = sum(1 for c in d.cust_cur.values() if c.sales >= 2)
    a["kpis"] = [
        kpi("Identified customers", "count", n_cur, n_prev if d.prev_ok else None),
        kpi("New customers", "count", new_cur),
        kpi("Returning customers", "count", n_cur - new_cur),
        kpi("Repeat buyers", "percent", pct(repeat, n_cur)),
        kpi("Revenue from identified customers", "percent", share),
    ]
    note = "Customers are only those attached to a sale; walk-in sales with no customer are not counted here."
    if share is not None and share < 70.0:
        a["notes"].append(
            f"Only {share:.0f}% of revenue is attached to a customer, so these findings cover that part only."
        )
        a["status"] = "partial"
        a["missing"].append(
            "A customer on more sales, so more of the revenue can be analysed by customer."
        )
    if n_cur == 0:
        a["notes"].append("No identified customers in this period.")
        return a
    weak = share is None or share < 50.0

    def lim(extra: str | None = None) -> list[str]:
        return [note] + ([extra] if extra else [])

    if n_cur >= 4 and ident > 0:
        v = _top([(c.name, c.revenue) for c in d.cust_cur.values()], lambda x: x[1], 3)
        t1, t3 = pct(v[0][1], ident) or 0.0, pct(sum(x for _, x in v), ident) or 0.0
        if t1 >= 30.0 or t3 >= 60.0:
            i = new_insight(
                ctx,
                "customers",
                "customer_concentration",
                "A few customers account for most sales",
                "watch",
            )
            i["what_happened"] = (
                f"{v[0][0]} brings {t1:.0f}% of identified-customer revenue; the top three bring {t3:.0f}%."
            )
            i["evidence"] = [ev(n, ctx.money(r)) for n, r in v]
            i["why_it_matters"] = "Losing one of them would remove a large share of sales."
            i["impact"] = impact(v[0][1], "Revenue from the largest customer this period")
            i["recommended_action"] = (
                "Look after these relationships and work on widening the customer base."
            )
            i["confidence"] = confidence(lim(), weak, n_cur)
            a["insights"].append(i)

    if d.prev_ok and n_prev > 0:
        prev_rev = sum(c.revenue for c in d.cust_prev.values())
        lapsed = [(c.name, c.revenue) for k, c in d.cust_prev.items() if k not in d.cust_cur]
        lapsed_rev = sum(r for _, r in lapsed)
        if len(lapsed) >= 3 and prev_rev > 0 and lapsed_rev >= prev_rev * 0.10:
            share_lost = pct(lapsed_rev, prev_rev) or 0.0
            i = new_insight(
                ctx,
                "customers",
                "lapsed_customers",
                "Customers who stopped buying",
                "problem" if share_lost >= 30.0 else "watch",
            )
            i["what_happened"] = (
                f"{len(lapsed)} customer(s) who bought in the previous period have not bought in this one; "
                f"they accounted for {share_lost:.0f}% of that period's identified revenue."
            )
            i["evidence"] = [
                ev(n, f"{ctx.money(r)} last period") for n, r in _top(lapsed, lambda x: x[1], 5)
            ]
            i["comparison"] = comparison(ctx, "Identified customers", "count", n_cur, n_prev)
            i["why_it_matters"] = (
                "Existing customers, especially those on repeat prescriptions, are the cheapest sales to keep."
            )
            i["impact"] = impact(-lapsed_rev, "Revenue these customers gave in the previous period")
            i["recommended_action"] = (
                "Contact the biggest ones to find out why, and offer a reason to come back."
            )
            i["confidence"] = confidence(
                lim(
                    "A customer who normally buys less often than every period can look lapsed."
                    if ctx.days < 30
                    else None
                ),
                weak,
                len(lapsed),
            )
            i["follow_up"] = (
                f"Check next period: how many of these {len(lapsed)} customers bought again?"
            )
            a["insights"].append(i)

    if d.prev_ok and n_prev >= 5:
        kept = sum(1 for k in d.cust_prev if k in d.cust_cur)
        rate = kept / n_prev * 100.0
        if rate < 50.0 or rate >= 75.0:
            i = new_insight(
                ctx,
                "customers",
                "retention",
                "Fewer customers are coming back"
                if rate < 50.0
                else "Most customers are coming back",
                "watch" if rate < 50.0 else "good",
            )
            i["what_happened"] = (
                f"{kept} of the {n_prev} customers from the previous period bought again this period ({rate:.0f}%)."
            )
            i["evidence"] = [
                ev("Bought in both periods", str(kept)),
                ev("Bought only last period", str(n_prev - kept)),
                ev("New this period", str(new_cur)),
            ]
            i["comparison"] = comparison(ctx, "Identified customers", "count", n_cur, n_prev)
            i["why_it_matters"] = "Repeat buying is the steadiest source of revenue."
            i["recommended_action"] = (
                "Find out why customers do not return: stock gaps, price, service or competitors."
                if rate < 50.0
                else "Keep doing what brings them back and note what it is."
            )
            i["confidence"] = confidence(
                lim(
                    "Over a short period, regular customers who buy less often than that look as if they left."
                    if ctx.days < 30
                    else None
                ),
                weak,
                n_prev,
            )
            a["insights"].append(i)
    return a


# ----------------------------------------------------------------- expense


async def _purchases(
    ctx: Ctx, start: dt.date, end: dt.date
) -> dict[tuple[int, int], tuple[float, int]]:
    s, e = await local_day_bounds_utc(ctx.db, start, end)
    rows = (
        await ctx.db.execute(
            select(
                PurchaseOrder.supplier_id,
                PurchaseOrderItem.product_id,
                func.sum(PurchaseOrderItem.quantity_received * PurchaseOrderItem.unit_cost_actual),
                func.sum(PurchaseOrderItem.quantity_received),
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
            .where(
                PurchaseOrder.status == PurchaseOrderStatus.RECEIVED,
                PurchaseOrder.received_at >= s,
                PurchaseOrder.received_at < e,
                PurchaseOrderItem.quantity_received > 0,
                PurchaseOrderItem.unit_cost_actual.is_not(None),
            )
            .group_by(PurchaseOrder.supplier_id, PurchaseOrderItem.product_id)
        )
    ).all()
    return {(int(sid), int(pid)): (float(sp), int(q)) for sid, pid, sp, q in rows}


async def expense_area(ctx: Ctx, d: Data | None) -> dict[str, Any]:
    a = area("expenses", "Expense Intelligence")
    a["status"] = "partial"
    a["missing"].append(
        "Operating expenses (rent, wages, utilities and similar) are not recorded in this app, so expense findings cover "
        "what it does record: purchasing, supplier prices, refunds and discounts."
    )
    db = ctx.db
    cur = await _purchases(ctx, *ctx.cur)
    prev = await _purchases(ctx, *ctx.prev)
    first_po = await db.scalar(
        select(func.min(PurchaseOrder.received_at)).where(
            PurchaseOrder.status == PurchaseOrderStatus.RECEIVED
        )
    )
    prev_ok = False
    if first_po is not None:
        _, day_end = await local_day_bounds_utc(db, ctx.prev[0], ctx.prev[0])
        prev_ok = bool(first_po < day_end)
    spend_cur = sum(v[0] for v in cur.values())
    spend_prev = sum(v[0] for v in prev.values())
    pv = (lambda x: x) if prev_ok else (lambda x: None)
    a["kpis"] = [
        kpi("Stock purchased (received)", "money", spend_cur, pv(spend_prev)),
        kpi(
            "Refunds paid out",
            "money",
            d.cur.refunds if d else None,
            pv(d.prev.refunds) if d else None,
        ),
        kpi(
            "Discounts given",
            "money",
            d.cur.discounts if d else None,
            pv(d.prev.discounts) if d else None,
        ),
    ]
    sup_names = {int(i): n for i, n in (await db.execute(select(Supplier.id, Supplier.name))).all()}
    prod_names = {int(i): n for i, n in (await db.execute(select(Product.id, Product.name))).all()}
    cap = "Purchases are counted when received, at the cost actually paid per unit, excluding reversed purchases."
    n_lines = len(cur)

    if prev_ok and spend_prev > 0 and n_lines >= 1:
        change = pct_change(spend_cur, spend_prev)
        if change is not None and (change >= 30.0 or change <= -30.0):
            rev_change = pct_change(d.cur.revenue, d.prev.revenue) if d and d.prev_ok else None
            up = change > 0
            sev = "watch" if up and (rev_change is None or rev_change < 10.0) else "info"
            i = new_insight(
                ctx,
                "expenses",
                "purchase_spend_change",
                "Stock purchasing rose" if up else "Stock purchasing fell",
                sev,
            )
            i["what_happened"] = (
                f"Stock received cost {ctx.money(spend_cur)} against {ctx.money(spend_prev)} in the previous period ({change:+.1f}%)."
            )
            i["evidence"] = [
                ev("This period", ctx.money(spend_cur)),
                ev("Previous period", ctx.money(spend_prev)),
            ]
            if rev_change is not None:
                i["evidence"].append(
                    ev("Revenue change over the same periods", f"{rev_change:+.1f}%")
                )
            i["comparison"] = comparison(ctx, "Stock purchased", "money", spend_cur, spend_prev)
            i["why_it_matters"] = (
                "Buying faster than selling ties cash up in stock, and in a pharmacy some of it can expire."
                if up
                else "Buying less frees cash but can lead to stock-outs if sales hold up."
            )
            i["impact"] = impact(
                spend_cur - spend_prev, "Purchasing difference between the two periods"
            )
            i["recommended_action"] = (
                "Check the orders against actual selling pace before the next purchase."
                if up
                else "Check the fast sellers still have enough stock."
            )
            i["confidence"] = confidence([cap], False, n_lines)
            a["insights"].append(i)

    if prev_ok:
        rises = []
        for key, (sp, q) in cur.items():
            if key in prev and q > 0 and prev[key][1] > 0:
                u_now, u_before = sp / q, prev[key][0] / prev[key][1]
                if u_before > 0 and (u_now - u_before) / u_before * 100.0 >= 5.0:
                    rises.append(
                        (
                            f"{sup_names.get(key[0], 'Supplier')} - {prod_names.get(key[1], 'Product')}",
                            (u_now - u_before) * q,
                            u_before,
                            u_now,
                        )
                    )
        if rises:
            top = sorted(rises, key=lambda r: (-r[1], r[0]))[:5]
            total = sum(r[1] for r in rises)
            i = new_insight(
                ctx,
                "expenses",
                "supplier_price_increase",
                "Suppliers charging more per unit",
                "watch",
            )
            i["what_happened"] = (
                f"{len(rises)} product(s) cost at least 5% more per unit from the same supplier than in the previous period."
            )
            i["evidence"] = [
                ev(n, f"{ctx.money(b)} to {ctx.money(c)} per unit ({(c - b) / b * 100:+.1f}%)")
                for n, _, b, c in top
            ]
            i["why_it_matters"] = (
                "A higher cost per unit cuts margin on every sale unless the selling price follows."
            )
            i["impact"] = impact(total, "Extra paid on the quantities received this period")
            i["recommended_action"] = (
                "Ask the supplier to explain the rise, get a competing quote, and reprice if it is permanent."
            )
            i["confidence"] = confidence(
                [
                    cap,
                    "Compares the same supplier and product in the two periods; a changed pack size looks like a price change.",
                ],
                False,
                len(rises),
            )
            i["follow_up"] = (
                "Check the next order from each supplier: did the unit cost come back down?"
            )
            a["insights"].append(i)

    by_sup: dict[int, float] = {}
    for (sid, _), (sp, _) in cur.items():
        by_sup[sid] = by_sup.get(sid, 0.0) + sp
    if len(by_sup) >= 3 and spend_cur > 0:
        v = _top([(sup_names.get(s, "Supplier"), x) for s, x in by_sup.items()], lambda x: x[1], 3)
        share = pct(v[0][1], spend_cur) or 0.0
        if share >= 50.0:
            i = new_insight(
                ctx,
                "expenses",
                "supplier_concentration",
                "Purchasing depends on one supplier",
                "info",
            )
            i["what_happened"] = f"{v[0][0]} supplied {share:.0f}% of stock purchased this period."
            i["evidence"] = [ev(n, ctx.money(x)) for n, x in v]
            i["why_it_matters"] = (
                "A delay or price rise from one supplier then affects most of the shelf."
            )
            i["impact"] = impact(v[0][1], "Stock bought from the largest supplier")
            i["recommended_action"] = "Keep a second supplier for the fast-moving products."
            i["confidence"] = confidence([cap], False, n_lines)
            a["insights"].append(i)

    if d and d.prev_ok and d.cur.refunds > 0:
        rc, rp = (
            pct(d.cur.refunds, d.cur.revenue + d.cur.refunds),
            pct(d.prev.refunds, d.prev.revenue + d.prev.refunds),
        )
        if rc is not None and rp is not None and rc - rp >= 2.0:
            i = new_insight(ctx, "expenses", "refund_cost", "Refunds are costing more", "watch")
            i["what_happened"] = (
                f"Refunds were {rc:.1f}% of sales value, up from {rp:.1f}% in the previous period."
            )
            i["evidence"] = [
                ev("Refunds this period", ctx.money(d.cur.refunds)),
                ev("Refunds previous period", ctx.money(d.prev.refunds)),
            ]
            i["comparison"] = comparison(ctx, "Refund share", "percent", rc, rp)
            i["why_it_matters"] = (
                "Refunds return cash, and a non-restocked return also loses the stock."
            )
            i["impact"] = impact(
                -(d.cur.refunds - d.prev.refunds), "Extra refunds versus the previous period"
            )
            i["recommended_action"] = "Review the refund reasons and the products involved."
            i["confidence"] = confidence(
                [
                    "Refunds are counted when issued, sales when sold, so a refund can belong to an earlier period's sale."
                ],
                False,
                d.cur.orders,
            )
            a["insights"].append(i)
    return a


# --------------------------------------------------------------- cash flow


async def _supplier_paid(ctx: Ctx, start: dt.date, end: dt.date) -> float:
    """Cash paid to suppliers: a payment is a negative ledger entry with no reversal link, and a
    reversed payment is a positive entry that has one (purchases are positive, purchase reversals
    negative with a link, so neither is counted)."""
    s, e = await local_day_bounds_utc(ctx.db, start, end)
    t = SupplierTransaction
    total = await ctx.db.scalar(
        select(func.coalesce(func.sum(t.amount), 0.0)).where(
            t.created_at >= s,
            t.created_at < e,
            ((t.amount < 0) & t.reverses_transaction_id.is_(None))
            | ((t.amount > 0) & t.reverses_transaction_id.is_not(None)),
        )
    )
    return -float(total or 0.0)


async def cash_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("cash_flow", "Cash Flow Intelligence")
    a["status"] = "partial"
    a["missing"].append(
        "Opening cash, till or bank balances and operating costs are not recorded, so the cash position and how long cash will last "
        "cannot be stated. Only cash movement from sales, refunds and supplier payments is analysed."
    )
    db = ctx.db

    def cash_in(w: Win) -> float:
        return sum(w.payments.values())

    out_sup_cur = await _supplier_paid(ctx, *ctx.cur)
    out_sup_prev = await _supplier_paid(ctx, *ctx.prev)
    net = cash_in(d.cur) - d.cur.refunds - out_sup_cur
    net_prev = cash_in(d.prev) - d.prev.refunds - out_sup_prev
    pv = (lambda x: x) if d.prev_ok else (lambda x: None)
    balances = (
        await db.execute(
            select(SupplierTransaction.supplier_id, func.sum(SupplierTransaction.amount))
            .group_by(SupplierTransaction.supplier_id)
            .having(func.sum(SupplierTransaction.amount) > 0)
        )
    ).all()
    owed = sum(float(b) for _, b in balances)
    a["kpis"] = [
        kpi("Cash received from sales", "money", cash_in(d.cur), pv(cash_in(d.prev))),
        kpi("Refunds paid out", "money", d.cur.refunds, pv(d.prev.refunds)),
        kpi("Paid to suppliers", "money", out_sup_cur, pv(out_sup_prev)),
        kpi("Net cash movement", "money", net, pv(net_prev)),
        kpi("Owed to suppliers", "money", owed),
    ]
    cap = "Cash movement comes from recorded sales payments, refunds and supplier payments; operating costs are not recorded."
    n_records = d.cur.orders
    if n_records >= 3:
        sev = (
            "problem"
            if net < 0 and d.prev_ok and net_prev < 0
            else (
                "watch"
                if net < 0
                else ("good" if d.prev_ok and net_prev > 0 and net >= net_prev * 1.2 else "")
            )
        )
        if sev:
            i = new_insight(
                ctx,
                "cash_flow",
                "cash_flow_trend",
                "More cash went out than came in" if net < 0 else "Cash generation improved",
                sev,
            )
            i["what_happened"] = (
                f"Cash received was {ctx.money(cash_in(d.cur))}; refunds and supplier payments took {ctx.money(d.cur.refunds + out_sup_cur)}, "
                f"a net {'outflow' if net < 0 else 'inflow'} of {ctx.money(abs(net))} before operating costs."
            )
            i["evidence"] = [
                ev("Cash received from sales", ctx.money(cash_in(d.cur))),
                ev("Refunds paid out", ctx.money(d.cur.refunds)),
                ev("Paid to suppliers", ctx.money(out_sup_cur)),
            ]
            if d.prev_ok:
                i["evidence"].append(ev("Net, previous period", ctx.money(net_prev)))
                i["comparison"] = comparison(ctx, "Net cash movement", "money", net, net_prev)
            i["why_it_matters"] = (
                "Paying out more than comes in drains the business unless it is a planned stock investment."
                if net < 0
                else "Stronger cash generation gives room to pay suppliers and invest."
            )
            i["impact"] = impact(
                net, "Cash received minus refunds and supplier payments this period"
            )
            i["recommended_action"] = (
                "Check supplier payment timing and the size of recent orders against sales."
                if net < 0
                else "Keep the discipline that produced it and set aside a buffer."
            )
            i["confidence"] = confidence([cap], False, n_records)
            a["insights"].append(i)

    if owed > 0 and cash_in(d.cur) > 0:
        ratio = owed / cash_in(d.cur) * 100.0
        if ratio >= 50.0:
            top = _top([(str(s), float(b)) for s, b in balances], lambda x: x[1], 3)
            names = {
                int(i): n for i, n in (await db.execute(select(Supplier.id, Supplier.name))).all()
            }
            i = new_insight(
                ctx,
                "cash_flow",
                "supplier_balances",
                "Supplier balances are large against recent takings",
                "problem" if ratio >= 100.0 else "watch",
            )
            i["what_happened"] = (
                f"The pharmacy owes suppliers {ctx.money(owed)}, equal to {ratio:.0f}% of this period's cash received from sales."
            )
            i["evidence"] = [ev(names.get(int(s), "Supplier"), ctx.money(b)) for s, b in top]
            i["why_it_matters"] = "Large unpaid balances can end credit terms or supply."
            i["impact"] = impact(owed, "Total currently owed to suppliers")
            i["recommended_action"] = (
                "Plan the payments against expected takings, starting with the suppliers you cannot do without."
            )
            i["confidence"] = confidence(
                [
                    "Suppliers' due dates are not recorded, so what is actually overdue cannot be told."
                ],
                False,
                len(balances),
            )
            a["insights"].append(i)

    if d.prev_ok:
        tot_c, tot_p = cash_in(d.cur), cash_in(d.prev)
        if tot_c > 0 and tot_p > 0:
            shifts = []
            for m in set(d.cur.payments) | set(d.prev.payments):
                sc, sp = (
                    d.cur.payments.get(m, 0.0) / tot_c * 100.0,
                    d.prev.payments.get(m, 0.0) / tot_p * 100.0,
                )
                if abs(sc - sp) >= 10.0:
                    shifts.append((m, sc, sp))
            if shifts:
                i = new_insight(
                    ctx,
                    "cash_flow",
                    "payment_mix_shift",
                    "The way customers pay has shifted",
                    "info",
                )
                i["what_happened"] = (
                    "; ".join(
                        f"{m.title()} went from {sp:.0f}% to {sc:.0f}% of takings"
                        for m, sc, sp in shifts
                    )
                    + "."
                )
                i["evidence"] = [
                    ev(m.title(), f"{sc:.0f}% now, {sp:.0f}% before") for m, sc, sp in shifts
                ]
                i["why_it_matters"] = (
                    "More mobile-money or card takings change how and when cash reaches the bank and what needs reconciling."
                )
                i["recommended_action"] = (
                    "Reconcile M-Pesa and card statements against the sales recorded."
                )
                i["confidence"] = confidence([cap], False, d.cur.orders)
                a["insights"].append(i)
    return a


# ------------------------------------------------- performance and growth


def performance_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("performance", "Business Performance Intelligence")
    a["status"] = "partial"
    a["missing"].append(
        "No business targets are recorded, so performance against a target cannot be assessed; results are compared with the previous equal period instead."
    )
    if not d.prev_ok:
        a["notes"].append(history_limitation(d, ctx) or "")
        return a
    cur, prev = d.cur, d.prev
    m: list[tuple[str, str, float, float, bool]] = [
        ("Revenue", "money", cur.revenue, prev.revenue, True),
        ("Sales (transactions)", "count", cur.orders, prev.orders, True),
    ]
    if cur.aov is not None and prev.aov is not None:
        m.append(("Average basket", "money", cur.aov, prev.aov, True))
    if ctx.include_profit and cur.profit is not None and prev.profit is not None:
        m.append(("Gross profit", "money", cur.profit, prev.profit, True))
        if cur.margin is not None and prev.margin is not None:
            m.append(("Gross margin", "percent", cur.margin, prev.margin, True))
    rs_c, rs_p = (
        pct(cur.refunds, cur.revenue + cur.refunds),
        pct(prev.refunds, prev.revenue + prev.refunds),
    )
    if rs_c is not None and rs_p is not None:
        m.append(("Refunds as share of sales", "percent", rs_c, rs_p, False))
    better = worse = flat = 0
    lines = []
    for label, unit, c, p, higher_good in m:
        moved = (c - p) if unit == "percent" else (pct_change(c, p) or 0.0)
        good = moved if higher_good else -moved
        thr = 1.0 if unit == "percent" else 5.0
        better, worse, flat = (
            (better + 1, worse, flat)
            if good >= thr
            else ((better, worse + 1, flat) if good <= -thr else (better, worse, flat + 1))
        )

        def fmt(v: float, u: str = unit) -> str:
            if u == "money":
                return ctx.money(v)
            return f"{v:.1f}%" if u == "percent" else f"{v:.0f}"

        lines.append(
            ev(
                label,
                f"{fmt(c)} vs {fmt(p)} ({moved:+.1f} pts)"
                if unit == "percent"
                else f"{fmt(c)} vs {fmt(p)} ({moved:+.1f}%)",
            )
        )
    a["kpis"] = [
        kpi("Measures improved", "count", better),
        kpi("Measures worsened", "count", worse),
        kpi("Measures about level", "count", flat),
    ]
    if len(m) >= 3:
        sev = (
            ("problem" if worse >= 3 and better == 0 else "watch")
            if worse > better
            else ("good" if better > worse else "info")
        )
        title = {
            "good": "Business performance improved",
            "info": "Business performance is mixed",
        }.get(sev, "Business performance weakened")
        i = new_insight(ctx, "performance", "performance_scorecard", title, sev)
        i["what_happened"] = (
            f"Of {len(m)} measures compared with the previous period, {better} improved, {worse} worsened and {flat} stayed about level."
        )
        i["evidence"] = lines
        i["comparison"] = comparison(ctx, "Revenue", "money", cur.revenue, prev.revenue)
        i["why_it_matters"] = (
            "One view across sales, profit and returns shows whether the business is moving the right way overall."
        )
        i["recommended_action"] = (
            "Start with the measures that worsened most; the other areas in this report show the causes."
            if worse > better
            else "Keep what is working and check the measures that slipped."
        )
        lim = [
            "Compared with the previous equal period, not against targets (none are recorded)."
        ] + [x for x in [weekday_limitation(ctx)] if x]
        i["confidence"] = confidence(lim, False, min(cur.orders, prev.orders))
        i["follow_up"] = (
            f"Re-run after the next {ctx.days} days to see whether the weaker measures recovered."
        )
        a["insights"].append(i)
    return a


def growth_area(ctx: Ctx, d: Data) -> dict[str, Any]:
    a = area("growth", "Business Growth Intelligence")
    a["status"] = "partial"
    a["missing"].append(
        "Growth drivers beyond sales history (marketing spend, capacity, targets) are not recorded, so growth is described from sales, customers and products only."
    )
    c, p, p2 = d.cur.revenue, d.prev.revenue, d.prev2.revenue
    start_utc = ctx.cur_utc[0] if ctx.cur_utc else None
    new_prod = (
        [
            (d.items_cur[k].name, d.items_cur[k].revenue)
            for k in d.items_cur
            if d.prod_first.get(k) and start_utc and d.prod_first[k] >= start_utc
        ]
        if d.prev_ok
        else []
    )
    new_cust = sum(
        1 for k in d.cust_cur if start_utc and d.cust_first.get(k) and d.cust_first[k] >= start_utc
    )
    a["kpis"] = [
        kpi(
            "Revenue growth vs previous period", "percent", pct_change(c, p) if d.prev_ok else None
        ),
        kpi("New customers", "count", new_cust),
        kpi("Products first sold this period", "count", len(new_prod) if d.prev_ok else None),
    ]
    if not d.prev_ok:
        a["notes"].append(history_limitation(d, ctx) or "")
        return a

    if d.prev2_ok and p > 0 and p2 > 0:
        now, before = pct_change(c, p), pct_change(p, p2)
        if now is not None and before is not None:
            if now >= 5.0 and before >= 5.0:
                sev, title = "good", "Sales are growing period after period"
            elif now <= -5.0 and before <= -5.0:
                sev, title = "problem", "Sales have fallen two periods in a row"
            elif abs(now) >= 10.0 and (now > 0) != (before > 0):
                sev, title = "info", "The direction of sales growth changed"
            else:
                sev, title = "", ""
            if sev:
                i = new_insight(ctx, "growth", "revenue_growth", title, sev)
                i["what_happened"] = (
                    f"Revenue moved {now:+.1f}% this period after {before:+.1f}% in the one before."
                )
                i["evidence"] = [
                    ev("This period", ctx.money(c)),
                    ev("Previous period", ctx.money(p)),
                    ev("Period before that", ctx.money(p2)),
                ]
                i["comparison"] = comparison(ctx, "Revenue", "money", c, p)
                i["why_it_matters"] = (
                    "Two periods in a row separates a real trend from one good or bad stretch."
                )
                i["impact"] = impact(c - p, "Revenue change versus the previous period")
                i["recommended_action"] = {
                    "good": "Make sure stock, staff and cash can support continued growth.",
                    "problem": "Treat this as a trend, not a blip: review pricing, stock gaps and customer losses.",
                }.get(
                    sev, "Find what changed between the periods before assuming it will continue."
                )
                i["confidence"] = confidence(
                    [x for x in [weekday_limitation(ctx)] if x],
                    False,
                    min(d.cur.orders, d.prev.orders, d.prev2.orders),
                )
                i["follow_up"] = (
                    f"Re-run after the next {ctx.days} days to see whether the trend holds."
                )
                a["insights"].append(i)

    if c > 0:
        grow = []
        for k, it in d.items_cur.items():
            before = d.items_prev[k].revenue if k in d.items_prev else 0.0
            delta = it.revenue - before
            if (
                before > 0
                and it.revenue >= before * 1.3
                and it.revenue >= c * 0.03
                and delta >= c * 0.01
            ):
                grow.append((it.name, delta))
        if grow:
            grow = _top(grow, lambda x: x[1], 5)
            i = new_insight(ctx, "growth", "growing_products", "Products growing fastest", "good")
            i["what_happened"] = (
                f"{len(grow)} product(s) grew at least 30% and now matter to total sales."
            )
            i["evidence"] = [ev(n, f"+{ctx.money(x)}") for n, x in grow]
            i["why_it_matters"] = "These are where growth is coming from."
            i["impact"] = impact(
                sum(x for _, x in grow),
                "Extra revenue on these products versus the previous period",
            )
            i["recommended_action"] = (
                "Keep them in stock and check their margin so growth adds profit, not just volume."
            )
            i["confidence"] = confidence(
                ["Products are matched by the app's product record."],
                False,
                min(d.cur.orders, d.prev.orders),
            )
            a["insights"].append(i)

    new_rev = sum(r for _, r in new_prod)
    if new_prod and c > 0 and new_rev >= c * 0.02:
        v = _top(new_prod, lambda x: x[1], 5)
        i = new_insight(ctx, "growth", "new_products", "New products are contributing", "good")
        i["what_happened"] = (
            f"{len(new_prod)} product(s) sold for the first time this period and brought {pct(new_rev, c) or 0:.0f}% of revenue."
        )
        i["evidence"] = [ev(n, ctx.money(r)) for n, r in v]
        i["why_it_matters"] = (
            "A widening range is a source of growth that does not depend on existing lines."
        )
        i["impact"] = impact(new_rev, "Revenue from products first sold this period")
        i["recommended_action"] = "Back the ones that sell and review the ones that do not."
        i["confidence"] = confidence([], False, d.cur.orders)
        a["insights"].append(i)

    if d.prev2_ok:
        prev_start_utc_ok = ctx.cur_utc is not None
        if prev_start_utc_ok:
            new_prev = sum(
                1
                for k in d.cust_prev
                if d.cust_first.get(k)
                and ctx.cur_utc
                and d.cust_first[k] >= ctx.cur_utc[0] - (ctx.cur_utc[1] - ctx.cur_utc[0])
                and d.cust_first[k] < ctx.cur_utc[0]
            )
            if new_cust >= 3 and new_prev >= 3:
                ch = pct_change(new_cust, new_prev)
                if ch is not None and abs(ch) >= 30.0:
                    up = ch > 0
                    i = new_insight(
                        ctx,
                        "growth",
                        "customer_growth",
                        "More new customers than last period"
                        if up
                        else "Fewer new customers than last period",
                        "good" if up else "watch",
                    )
                    i["what_happened"] = (
                        f"{new_cust} new customers this period against {new_prev} in the previous one ({ch:+.0f}%)."
                    )
                    i["evidence"] = [
                        ev("New customers this period", str(new_cust)),
                        ev("New customers previous period", str(new_prev)),
                    ]
                    i["comparison"] = comparison(ctx, "New customers", "count", new_cust, new_prev)
                    i["why_it_matters"] = (
                        "New customers are what grows the base beyond existing buyers."
                    )
                    i["recommended_action"] = (
                        "Find out where they came from and do more of it."
                        if up
                        else "Review what brought customers in before, and whether it stopped."
                    )
                    i["confidence"] = confidence(
                        ["Only customers attached to a sale are counted."],
                        False,
                        new_cust + new_prev,
                    )
                    a["insights"].append(i)

    if d.prev2_ok and c > 0 and p > 0 and p2 > 0:
        avg, lo, hi = (c + p + p2) / 3.0, min(c, p, p2), max(c, p, p2)
        i = new_insight(
            ctx, "growth", "revenue_outlook", "Revenue outlook if the recent pace holds", "info"
        )
        i["what_happened"] = (
            f"If the next {ctx.days} days look like the average of the last three periods, revenue would be about "
            f"{ctx.money(avg)}, within {ctx.money(lo)} to {ctx.money(hi)}."
        )
        i["evidence"] = [
            ev("Last three periods, average", ctx.money(avg)),
            ev("Lowest of the three", ctx.money(lo)),
            ev("Highest of the three", ctx.money(hi)),
        ]
        i["why_it_matters"] = (
            "A plain baseline to plan stock and cash against, and to judge whether the next period beat it."
        )
        i["recommended_action"] = (
            "Plan stock and costs against the low end; treat anything above the average as upside."
        )
        i["confidence"] = confidence(
            [
                "A simple average of three periods: it ignores seasonality, price changes and stock shortages. It is an outlook, not a promise."
            ],
            False,
            min(d.cur.orders, d.prev.orders, d.prev2.orders),
        )
        i["follow_up"] = f"Compare the next {ctx.days}-day result with {ctx.money(avg)}."
        a["insights"].append(i)
    return a


# ------------------------------------------------------------------ report

_RANK = {"problem": 0, "watch": 1, "info": 2, "good": 3}


async def build_report(db: AsyncSession, days: int, include_profit: bool) -> IntelligenceReportOut:
    """The full intelligence report for the `days` ending today (clamped to 7..180), compared with the
    equal period before it. Read-only."""
    days = max(7, min(180, int(days)))
    today = await business_today(db)
    currency = await db.scalar(select(BusinessConfig.currency).limit(1)) or "KES"
    ctx = Ctx(db, today, days, currency, include_profit)
    d = await load_data(ctx)
    areas = [
        sales_area(ctx, d),
        profit_area(ctx, d),
        await inventory_area(ctx, d),
        customer_area(ctx, d),
        await expense_area(ctx, d),
        await cash_area(ctx, d),
        performance_area(ctx, d),
        growth_area(ctx, d),
    ]
    for a in areas:
        a["insights"].sort(key=lambda i: _RANK.get(i["severity"], 3))
        a["notes"] = [n for n in a["notes"] if n]
    prev = ctx.period("prev")
    return IntelligenceReportOut.model_validate(
        {
            "as_of": today.isoformat(),
            "currency": currency,
            "period": ctx.period(),
            "previous_period": prev,
            "areas": areas,
        }
    )
