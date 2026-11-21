"""Response shapes for the business intelligence report.

The insight shape is the one shared with SME Pro (see INTELLIGENCE.md):
what happened, evidence, reporting period, historical comparison, why it
matters, potential impact, recommended action, confidence with stated
limitations, and follow-up. Money here is in the business currency's
major units (the same unit every other report in this app returns).
"""

from pydantic import BaseModel


class EvidenceOut(BaseModel):
    label: str
    value: str


class PeriodOut(BaseModel):
    label: str
    start: str  # first day included, YYYY-MM-DD
    end: str  # last day included, YYYY-MM-DD


class ComparisonOut(BaseModel):
    label: str
    period: PeriodOut
    current: float
    previous: float
    change_pct: float | None
    unit: str  # "money", "percent", "count" or "days"


class ImpactOut(BaseModel):
    amount: float
    basis: str


class ConfidenceOut(BaseModel):
    level: str  # "high", "medium" or "low"
    limitations: list[str]


class InsightOut(BaseModel):
    area: str
    kind: str
    title: str
    severity: str  # "problem", "watch", "info" or "good"
    what_happened: str
    evidence: list[EvidenceOut]
    period: PeriodOut
    comparison: ComparisonOut | None
    why_it_matters: str
    impact: ImpactOut | None
    recommended_action: str
    confidence: ConfidenceOut
    follow_up: str | None


class KpiOut(BaseModel):
    label: str
    value: float | None
    previous: float | None
    change_pct: float | None
    unit: str


class AreaOut(BaseModel):
    id: str
    title: str
    # "full", "partial" (some essential information is not recorded) or
    # "unavailable" (not permitted, or the data to start from is missing).
    status: str
    kpis: list[KpiOut]
    insights: list[InsightOut]
    missing: list[str]
    notes: list[str]


class IntelligenceReportOut(BaseModel):
    as_of: str
    currency: str
    period: PeriodOut
    previous_period: PeriodOut
    areas: list[AreaOut]
