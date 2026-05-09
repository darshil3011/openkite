"""CloudTrail tools — query recent control-plane changes.

CloudTrail's ``lookup_events`` is a 90-day rolling window over management
events. Two API quirks shape the design:

- Each event carries a multi-KB ``CloudTrailEvent`` JSON blob. We strip it
  by default and expose a separate drill-down tool that returns it parsed.
- ``LookupAttributes`` accepts only ONE attribute server-side. So when the
  caller supplies an explicit filter (event_name / username / resource_name)
  we send that to the API and apply ``writes_only`` client-side. Otherwise
  we send ``ReadOnly=false`` server-side, which alone strips ~95% of noise.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool

from openkite.tools._aws import client


def _slim(event: dict) -> dict:
    resources = event.get("Resources") or []
    return {
        "event_id": event.get("EventId"),
        "time": event["EventTime"].isoformat() if event.get("EventTime") else None,
        "event": event.get("EventName"),
        "user": event.get("Username"),
        "source": event.get("EventSource"),
        "read_only": str(event.get("ReadOnly", "")).lower() == "true",
        "resources": [r.get("ResourceName") for r in resources if r.get("ResourceName")],
    }


@tool
def lookup_recent_changes(
    hours: int = 1,
    event_name: str | None = None,
    username: str | None = None,
    resource_name: str | None = None,
    writes_only: bool = True,
    limit: int = 25,
    region: str = "us-east-1",
) -> list[dict]:
    """Recent control-plane changes from CloudTrail (``hours`` back, max 24).

    Returns slim rows ``{event_id, time, event, user, source, read_only,
    resources}``. Use ``get_cloudtrail_event(event_id)`` for the full record.

    Filters: ``event_name`` (e.g. "TerminateInstances"), ``username``,
    ``resource_name`` (e.g. "i-0abc..."). ``writes_only=True`` (default)
    drops Describe/Get/List noise.
    """
    hours = max(1, min(int(hours), 24))
    limit = max(1, min(int(limit), 50))

    end = datetime.now(UTC)
    start = end - timedelta(hours=hours)

    # CloudTrail allows ONE LookupAttribute. Prefer the explicit filter; fall
    # back to ReadOnly=false only when writes_only is the only filter in play.
    attrs: list[dict] = []
    filter_writes_client_side = False
    if event_name:
        attrs = [{"AttributeKey": "EventName", "AttributeValue": event_name}]
        filter_writes_client_side = writes_only
    elif resource_name:
        attrs = [{"AttributeKey": "ResourceName", "AttributeValue": resource_name}]
        filter_writes_client_side = writes_only
    elif username:
        attrs = [{"AttributeKey": "Username", "AttributeValue": username}]
        filter_writes_client_side = writes_only
    elif writes_only:
        attrs = [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]

    kwargs: dict = {"StartTime": start, "EndTime": end, "MaxResults": limit}
    if attrs:
        kwargs["LookupAttributes"] = attrs

    ct = client("cloudtrail", region)
    resp = ct.lookup_events(**kwargs)
    events = resp.get("Events", [])
    if filter_writes_client_side:
        events = [e for e in events if str(e.get("ReadOnly", "")).lower() != "true"]
    return [_slim(e) for e in events[:limit]]


@tool
def get_cloudtrail_event(event_id: str, region: str = "us-east-1") -> dict:
    """Full CloudTrail record for one ``event_id`` (CloudTrailEvent parsed)."""
    ct = client("cloudtrail", region)
    resp = ct.lookup_events(
        LookupAttributes=[{"AttributeKey": "EventId", "AttributeValue": event_id}],
        MaxResults=1,
    )
    events = resp.get("Events", [])
    if not events:
        return {"event_id": event_id, "found": False}
    e = events[0]
    raw = e.get("CloudTrailEvent")
    detail = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return {
        "event_id": e.get("EventId"),
        "time": e["EventTime"].isoformat() if e.get("EventTime") else None,
        "event": e.get("EventName"),
        "user": e.get("Username"),
        "source": e.get("EventSource"),
        "read_only": str(e.get("ReadOnly", "")).lower() == "true",
        "resources": e.get("Resources") or [],
        "detail": detail,
        "found": True,
    }


CLOUDTRAIL_TOOLS = [lookup_recent_changes, get_cloudtrail_event]
