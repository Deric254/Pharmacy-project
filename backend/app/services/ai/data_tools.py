"""
The read-only data lookups the assistant may run to answer a question.

Each tool wraps an existing service -- the same source of truth the pages use,
so the assistant can never disagree with the screen -- behind two gates:

  * the same permission(s) the matching page or endpoint requires (a tool the
    person couldn't open themselves is never even offered to the model), and
  * strict argument validation (unknown tool, unknown argument, malformed date
    or out-of-range number are reported back, never guessed at).

Nothing here writes. Results are bounded in rows and size so a question about
"everything" can neither flood a prompt nor slow the app.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai.prompts import render_json
from app.services.customer_service import CustomerService
from app.services.inventory_service import InventoryService
from app.services.product_service import ProductService
from app.services.purchasing_service import PurchasingService
from app.services.report_service import ReportService
from app.services.sale_service import SaleService
from app.services.supplier_service import SupplierService

logger = logging.getLogger(__name__)

MAX_CALLS = 5
MAX_ROWS = 60
MAX_RESULT_CHARS = 10_000


class ToolInputError(ValueError):
    """The model asked for something a tool cannot do as written."""


@dataclass(frozen=True)
class ToolContext:
    db: AsyncSession
    include_profit: bool


@dataclass(frozen=True)
class Param:
    kind: Literal["date", "int", "text", "choice"]
    default: Any = None  # None: the argument is required
    low: int = 1
    high: int = 100
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataTool:
    name: str
    description: str
    permissions: tuple[str, ...]  # any one of these grants the tool
    params: dict[str, Param]
    run: Callable[[ToolContext, dict[str, Any]], Awaitable[object]]


@dataclass(frozen=True)
class ToolResult:
    tool: str
    args: dict[str, Any]
    data: object | None
    error: str | None


_START = Param("date")
_END = Param("date")
_PERIOD = {"start_date": _START, "end_date": _END}


def _limit(default: int, high: int = 30) -> Param:
    return Param("int", default=default, low=1, high=high)


async def _business_overview(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).kpi_dashboard(
        a["start_date"], a["end_date"], ctx.include_profit
    )


async def _sales_over_time(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).sales_summary(a["start_date"], a["end_date"], a["group_by"])


async def _top_products(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).top_products_by_revenue(
        a["start_date"], a["end_date"], a["limit"]
    )


async def _top_customers(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).top_customers(a["start_date"], a["end_date"], a["limit"])


async def _sales_by_category(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).revenue_by_category(a["start_date"], a["end_date"])


async def _sales_by_cashier(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).sales_by_cashier(a["start_date"], a["end_date"])


async def _fast_and_slow_movers(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).fast_slow_movers(a["days"], a["limit"])


async def _stock_runway(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).stock_runway(a["lookback_days"])


async def _expired_stock(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).expired_stock()


async def _customer_lifetime_value(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await CustomerService(ctx.db).lifetime_value()


async def _profit_summary(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).profit_report(a["start_date"], a["end_date"])


async def _profit_by_product(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await ReportService(ctx.db).profit_by_product(a["start_date"], a["end_date"])


async def _low_stock(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await InventoryService(ctx.db).get_low_stock_products()


async def _expiring_stock(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await InventoryService(ctx.db).get_expiring_batches(a["within_days"])


async def _stock_valuation(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await InventoryService(ctx.db).get_valuation()


# Cost and margin are profit information: only for someone who may see profit.
_PROFIT_FIELDS = {"current_cost", "margin_amount", "margin_percent", "markup_percent"}


async def _find_products(ctx: ToolContext, a: dict[str, Any]) -> object:
    products = await ProductService(ctx.db).list_all(a["search"])
    hidden = set() if ctx.include_profit else _PROFIT_FIELDS
    return [p.model_dump(mode="json", exclude=hidden) for p in products[:MAX_ROWS]]


async def _suppliers(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await SupplierService(ctx.db).list_all()


async def _supplier_summary(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await SupplierService(ctx.db).kpis(a["start_date"], a["end_date"])


async def _recent_purchases(ctx: ToolContext, a: dict[str, Any]) -> object:
    names = {s.id: s.name for s in await SupplierService(ctx.db).list_all()}
    orders = sorted(await PurchasingService(ctx.db).list_all(), key=lambda o: o.id, reverse=True)
    return [
        {
            "id": o.id,
            "supplier": names.get(o.supplier_id),
            "status": o.status,
            "received_at": o.received_at,
            "reversed_at": o.reversed_at,
            "reversal_reason": o.reversal_reason,
            "items": [
                {
                    "product": i.product_name,
                    "quantity": i.quantity_received,
                    "unit_cost": i.unit_cost_actual,
                }
                for i in o.items
            ],
        }
        for o in orders[: a["limit"]]
    ]


async def _recent_sales(ctx: ToolContext, a: dict[str, Any]) -> object:
    return await SaleService(ctx.db).list_sales(a["start_date"], a["end_date"], a["limit"])


_REPORTS = ("reports.view",)
_PROFIT = ("reports.view_profit",)
_INVENTORY = ("inventory.view",)
_PURCHASING = ("purchasing.create_po", "purchasing.approve_po")

TOOLS: tuple[DataTool, ...] = (
    DataTool(
        "business_overview",
        "Headline numbers for a period: revenue, transactions, average basket, change "
        "vs the previous period, top products, low-stock and expiring counts"
        " (and profit when allowed).",
        _REPORTS,
        _PERIOD,
        _business_overview,
    ),
    DataTool(
        "sales_over_time",
        "Revenue and transaction counts per day or per month across a period.",
        _REPORTS,
        {**_PERIOD, "group_by": Param("choice", default="day", choices=("day", "month"))},
        _sales_over_time,
    ),
    DataTool(
        "top_products",
        "Best-selling products by revenue in a period.",
        _REPORTS,
        {**_PERIOD, "limit": _limit(10)},
        _top_products,
    ),
    DataTool(
        "top_customers",
        "Customers who spent the most in a period.",
        _REPORTS,
        {**_PERIOD, "limit": _limit(10)},
        _top_customers,
    ),
    DataTool(
        "sales_by_category",
        "Revenue per product category in a period.",
        _REPORTS,
        _PERIOD,
        _sales_by_category,
    ),
    DataTool(
        "sales_by_cashier",
        "Sales totals per cashier in a period.",
        _REPORTS,
        _PERIOD,
        _sales_by_cashier,
    ),
    DataTool(
        "fast_and_slow_movers",
        "Fastest and slowest selling products over the last N days.",
        _REPORTS,
        {"days": Param("int", default=30, low=7, high=365), "limit": _limit(10)},
        _fast_and_slow_movers,
    ),
    DataTool(
        "stock_runway",
        "How many days each product's stock will last at its recent selling rate.",
        _REPORTS,
        {"lookback_days": Param("int", default=30, low=7, high=365)},
        _stock_runway,
    ),
    DataTool(
        "expired_stock",
        "Batches already past expiry that still hold stock, and their value.",
        _REPORTS,
        {},
        _expired_stock,
    ),
    DataTool(
        "customer_lifetime_value",
        "Each customer's total spend to date and the average lifetime value.",
        _REPORTS,
        {},
        _customer_lifetime_value,
    ),
    DataTool(
        "profit_summary",
        "Revenue, cost of goods, profit and margin for a period.",
        _PROFIT,
        _PERIOD,
        _profit_summary,
    ),
    DataTool(
        "profit_by_product",
        "Profit and margin per product in a period.",
        _PROFIT,
        _PERIOD,
        _profit_by_product,
    ),
    DataTool(
        "low_stock",
        "Products at or below their reorder point.",
        _INVENTORY,
        {},
        _low_stock,
    ),
    DataTool(
        "expiring_stock",
        "Batches with stock expiring within N days (default 90).",
        _INVENTORY,
        {"within_days": Param("int", default=90, low=1, high=730)},
        _expiring_stock,
    ),
    DataTool(
        "stock_valuation",
        "Total value of the stock on hand, at cost and at selling price.",
        _INVENTORY,
        {},
        _stock_valuation,
    ),
    DataTool(
        "find_products",
        "Look up products by name or barcode: stock on hand, reorder point, price.",
        _INVENTORY,
        {"search": Param("text")},
        _find_products,
    ),
    DataTool(
        "suppliers",
        "Every supplier with the amount currently owed to them (negative means "
        "they hold a credit).",
        _PURCHASING,
        {},
        _suppliers,
    ),
    DataTool(
        "supplier_summary",
        "Purchased, paid and still-due totals across all suppliers for a period.",
        _PURCHASING,
        _PERIOD,
        _supplier_summary,
    ),
    DataTool(
        "recent_purchases",
        "The most recent purchases (stock received), newest first, with what was "
        "bought, from whom, and whether a purchase was later reversed.",
        ("purchasing.create_po",),
        {"limit": _limit(10)},
        _recent_purchases,
    ),
    DataTool(
        "recent_sales",
        "Individual sales in a period, newest first: who sold, to whom, how much.",
        ("sales.create", "sales.refund"),
        {**_PERIOD, "limit": _limit(20, 50)},
        _recent_sales,
    ),
)


def available_tools(permission_codes: set[str]) -> list[DataTool]:
    return [t for t in TOOLS if permission_codes.intersection(t.permissions)]


def _describe(name: str, param: Param) -> str:
    if param.kind == "date":
        return f"{name}: date YYYY-MM-DD, required"
    if param.kind == "text":
        return f"{name}: text, required"
    if param.kind == "choice":
        return f"{name}: one of {'|'.join(param.choices)}, default {param.default}"
    return f"{name}: integer {param.low}-{param.high}, default {param.default}"


def catalog_text(tools: list[DataTool]) -> str:
    return "\n".join(
        f"- {t.name}({', '.join(_describe(n, p) for n, p in t.params.items())}): {t.description}"
        for t in tools
    )


def parse_plan(text: str) -> list[object] | None:
    """The model's chosen calls, or None if it did not reply with a plan at all."""
    start = text.find("{")
    if start == -1:
        return None
    try:
        plan, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    calls = plan.get("calls") if isinstance(plan, dict) else None
    return calls[:MAX_CALLS] if isinstance(calls, list) else None


def _coerce(name: str, param: Param, raw: Any) -> Any:
    if param.kind == "date":
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise ToolInputError(f"{name} must be a date written YYYY-MM-DD") from None
    if param.kind == "int":
        try:
            return min(max(int(raw), param.low), param.high)
        except (TypeError, ValueError):
            raise ToolInputError(f"{name} must be a whole number") from None
    value = str(raw).strip()
    if param.kind == "choice":
        if value.lower() not in param.choices:
            raise ToolInputError(f"{name} must be one of {', '.join(param.choices)}")
        return value.lower()
    if not value or len(value) > 80:
        raise ToolInputError(f"{name} must be 1 to 80 characters")
    return value


def _validate(tool: DataTool, args: object) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ToolInputError("args must be an object")
    unknown = set(args) - set(tool.params)
    if unknown:
        raise ToolInputError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    clean: dict[str, Any] = {}
    for name, param in tool.params.items():
        raw = args.get(name, param.default)
        if raw is None:
            raise ToolInputError(f"missing required argument: {name}")
        clean[name] = _coerce(name, param, raw)
    if "start_date" in clean and clean["start_date"] > clean["end_date"]:
        raise ToolInputError("start_date must not be after end_date")
    return clean


def _to_jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    return value


def _bounded(value: object) -> object:
    """Cap every list at MAX_ROWS and say how many rows were left out."""
    if isinstance(value, list):
        if len(value) <= MAX_ROWS:
            return value
        return {"rows": value[:MAX_ROWS], "rows_omitted": len(value) - MAX_ROWS}
    if isinstance(value, dict):
        bounded: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(item, list) and len(item) > MAX_ROWS:
                bounded[key] = item[:MAX_ROWS]
                bounded[f"{key}_omitted"] = len(item) - MAX_ROWS
            else:
                bounded[key] = item
        return bounded
    return value


async def run_tool_calls(
    ctx: ToolContext, tools: list[DataTool], calls: list[object]
) -> list[ToolResult]:
    by_name = {t.name: t for t in tools}
    results: list[ToolResult] = []
    for call in calls[:MAX_CALLS]:
        name = call.get("tool") if isinstance(call, dict) else None
        raw_args = call.get("args", {}) if isinstance(call, dict) else {}
        tool = by_name.get(name) if isinstance(name, str) else None
        if tool is None:
            results.append(ToolResult(str(name), {}, None, "unknown or unavailable lookup"))
            continue
        try:
            args = _validate(tool, raw_args)
            data = _bounded(_to_jsonable(await tool.run(ctx, args)))
        except ToolInputError as exc:
            results.append(ToolResult(tool.name, {}, None, str(exc)))
        except Exception:  # a failing lookup is reported to the model, never raised to the person
            logger.exception("AI data lookup %s failed", tool.name)
            results.append(ToolResult(tool.name, {}, None, "the lookup failed"))
        else:
            results.append(ToolResult(tool.name, args, data, None))
    return results


def render_results(results: list[ToolResult]) -> str:
    blocks = []
    for result in results:
        title = f"{result.tool} {render_json(result.args)}" if result.args else result.tool
        body = f"ERROR: {result.error}" if result.error else render_json(result.data)
        if len(body) > MAX_RESULT_CHARS:
            body = f"{body[:MAX_RESULT_CHARS]} ...[cut: too long]"
        blocks.append(f"{title}\n{body}")
    return "\n\n".join(blocks)
