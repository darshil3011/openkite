"""Shared boto3 plumbing used by every tool module.

We deliberately keep this small:

- ``client(service, region)`` — single boto3 client factory so tests can
  monkeypatch one place.
- ``cw_metric_average`` / ``cw_metric_sum`` — narrow CloudWatch wrappers
  that handle the empty-datapoint case.
- ``confirm(message)`` — graph-level interrupt for write tools.

There is no ``safe_scan`` wrapper here. In the ReAct loop, an exception
inside a tool surfaces as a ``ToolMessage`` with the error string — the LLM
can read it and decide what to do (retry, ask the user, give up). That's a
better signal than a hardcoded "status: error" log entry.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from langgraph.types import interrupt


def client(service: str, region: str = "us-east-1") -> Any:
    return boto3.client(service, region_name=region)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def cw_metric_average(
    cw,
    namespace: str,
    metric_name: str,
    dimensions: list[dict],
    *,
    days: int,
    period: int = 3600,
) -> float | None:
    """Mean across all datapoints over the past ``days``. None if no data."""
    end = _now_utc()
    resp = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=end - timedelta(days=days),
        EndTime=end,
        Period=period,
        Statistics=["Average"],
    )
    points = resp.get("Datapoints") or []
    if not points:
        return None
    return sum(p["Average"] for p in points) / len(points)


def cw_metric_sum(
    cw,
    namespace: str,
    metric_name: str,
    dimensions: list[dict],
    *,
    days: int,
    period: int = 86400,
) -> float:
    """Total ``Sum`` over the past ``days``. 0.0 when there are no datapoints."""
    end = _now_utc()
    resp = cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        Dimensions=dimensions,
        StartTime=end - timedelta(days=days),
        EndTime=end,
        Period=period,
        Statistics=["Sum"],
    )
    return sum(p.get("Sum", 0.0) for p in (resp.get("Datapoints") or []))


def paginated(client_obj, op_name: str, **kwargs) -> Iterable[dict]:
    yield from client_obj.get_paginator(op_name).paginate(**kwargs)


def confirm(message: str) -> bool:
    """Pause the graph and ask the user to confirm a destructive action.

    Returns True if the user replies anything in ``{y, yes, ok, run, proceed}``.
    Anything else is treated as a refusal.
    """
    decision = interrupt({"type": "confirm_action", "message": message})
    return str(decision).strip().lower() in {"y", "yes", "ok", "run", "proceed", "confirm"}
