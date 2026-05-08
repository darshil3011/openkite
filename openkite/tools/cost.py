"""Cost Explorer tools.

Cost Explorer is a global API that lives in ``us-east-1``. The ``region``
argument is ignored on these tools — present only so the LLM doesn't get
confused by an inconsistent signature.
"""

from __future__ import annotations

from datetime import date, timedelta

from langchain_core.tools import tool

from openkite.tools._aws import client


@tool
def get_cost_breakdown(days: int = 30, group_by: str = "SERVICE") -> dict:
    """Total unblended cost grouped by ``SERVICE`` (or ``LINKED_ACCOUNT``, ``REGION``).

    Returns ``{"period": "...", "total": float, "by_group": {name: cost}}``.
    """
    ce = client("ce", "us-east-1")
    end = date.today()
    start = end - timedelta(days=days)
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": group_by}],
    )
    by_group: dict[str, float] = {}
    for period in resp.get("ResultsByTime", []):
        for g in period.get("Groups", []):
            key = g["Keys"][0]
            amt = float(g["Metrics"]["UnblendedCost"]["Amount"])
            by_group[key] = by_group.get(key, 0.0) + amt
    return {
        "period": f"{start} → {end}",
        "total": round(sum(by_group.values()), 2),
        "by_group": {k: round(v, 2) for k, v in sorted(by_group.items(), key=lambda kv: -kv[1])},
    }


@tool
def get_ri_coverage(days: int = 30) -> list[dict]:
    """Per-instance-family Reserved Instance coverage over the past ``days``."""
    ce = client("ce", "us-east-1")
    end = date.today()
    start = end - timedelta(days=days)
    # The Cost Explorer API only accepts Hour | Unit | Cost as metric names.
    # Requesting "Hour" causes the response to populate CoverageHours, which
    # contains both CoverageHoursPercentage and OnDemandHours.
    resp = ce.get_reservation_coverage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        GroupBy=[{"Type": "DIMENSION", "Key": "INSTANCE_TYPE_FAMILY"}],
        Metrics=["Hour"],
    )
    out: list[dict] = []
    for g in resp.get("CoveragesByTime", [{}])[0].get("Groups", []):
        cov = g.get("Coverage", {}).get("CoverageHours", {})
        out.append({
            "family": g.get("Attributes", {}).get("instanceTypeFamily", "unknown"),
            "coverage_pct": round(float(cov.get("CoverageHoursPercentage", 0.0)), 2),
            "on_demand_hours": round(float(cov.get("OnDemandHours", 0.0)), 1),
        })
    return out


COST_TOOLS = [get_cost_breakdown, get_ri_coverage]
