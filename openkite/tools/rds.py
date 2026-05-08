"""RDS tools."""

from __future__ import annotations

from langchain_core.tools import tool

from openkite.tools._aws import client, confirm, cw_metric_average, paginated

HOURS_PER_MONTH = 730
RDS_HOURLY = {
    "db.t3.micro": 0.017, "db.t3.small": 0.034, "db.t3.medium": 0.068,
    "db.t4g.micro": 0.016, "db.t4g.small": 0.032,
    "db.m5.large": 0.171, "db.m5.xlarge": 0.342,
    "db.r5.large": 0.24, "db.r5.xlarge": 0.48,
}


def _rds_monthly(cls: str) -> float:
    return RDS_HOURLY.get(cls, 0.10) * HOURS_PER_MONTH


@tool
def list_rds_instances(state: str = "all", region: str = "us-east-1") -> list[dict]:
    """List RDS instances in ``region``. ``state`` filters by ``DBInstanceStatus``."""
    rds = client("rds", region)
    out = []
    for page in paginated(rds, "describe_db_instances"):
        for db in page.get("DBInstances", []):
            if state != "all" and db.get("DBInstanceStatus") != state:
                continue
            out.append({
                "id": db["DBInstanceIdentifier"],
                "engine": db.get("Engine"),
                "class": db.get("DBInstanceClass"),
                "status": db.get("DBInstanceStatus"),
                "multi_az": db.get("MultiAZ", False),
            })
    return out


@tool
def get_rds_metric(
    db_id: str,
    metric: str = "CPUUtilization",
    days: int = 7,
    region: str = "us-east-1",
) -> dict:
    """Average CloudWatch metric for one RDS instance."""
    cw = client("cloudwatch", region)
    avg = cw_metric_average(
        cw, "AWS/RDS", metric,
        [{"Name": "DBInstanceIdentifier", "Value": db_id}],
        days=days,
    )
    return {"db_id": db_id, "metric": metric, "days": days, "average": avg}


@tool
def find_idle_rds(
    cpu_threshold: float = 10.0,
    days: int = 7,
    region: str = "us-east-1",
) -> list[dict]:
    """Find RDS instances with average CPU below ``cpu_threshold`` over ``days``."""
    rds = client("rds", region)
    cw = client("cloudwatch", region)
    running = {"available", "backing-up", "modifying"}
    findings: list[dict] = []
    for page in paginated(rds, "describe_db_instances"):
        for db in page.get("DBInstances", []):
            if db.get("DBInstanceStatus") not in running:
                continue
            db_id = db["DBInstanceIdentifier"]
            avg = cw_metric_average(
                cw, "AWS/RDS", "CPUUtilization",
                [{"Name": "DBInstanceIdentifier", "Value": db_id}],
                days=days,
            )
            if avg is None or avg >= cpu_threshold:
                continue
            cls = db.get("DBInstanceClass", "unknown")
            findings.append({
                "db_id": db_id,
                "class": cls,
                "engine": db.get("Engine"),
                "avg_cpu_pct": round(avg, 2),
                "estimated_monthly_savings": round(_rds_monthly(cls), 2),
                "suggested_action": "stop_rds",
            })
    return findings


@tool
def stop_rds(db_id: str, region: str = "us-east-1") -> dict:
    """Stop one RDS instance. Pauses for user confirmation."""
    if not confirm(f"Stop RDS instance {db_id} in {region}?"):
        return {"db_id": db_id, "status": "cancelled_by_user"}
    rds = client("rds", region)
    rds.stop_db_instance(DBInstanceIdentifier=db_id)
    return {"db_id": db_id, "status": "stopping"}


RDS_TOOLS = [list_rds_instances, get_rds_metric, find_idle_rds, stop_rds]
