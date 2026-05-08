"""EC2-family tools: instances, EBS volumes, NAT gateways, security groups."""

from __future__ import annotations

from langchain_core.tools import tool

from openkite.tools._aws import (
    client,
    confirm,
    cw_metric_average,
    cw_metric_sum,
    paginated,
)

HOURS_PER_MONTH = 730
EC2_HOURLY = {
    "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464,
    "t3.micro": 0.0104, "t3.small": 0.0208, "t3.medium": 0.0416,
    "t3.nano": 0.0052, "t3.large": 0.0832, "t3.xlarge": 0.1664,
    "m5.large": 0.096, "m5.xlarge": 0.192, "m5.2xlarge": 0.384,
    "c5.large": 0.085, "c5.xlarge": 0.17,
    "r5.large": 0.126, "r5.xlarge": 0.252,
}
EBS_GP3_PER_GB_MONTH = 0.08
NAT_HOURLY = 0.045


def _ec2_monthly(itype: str) -> float:
    return EC2_HOURLY.get(itype, 0.10) * HOURS_PER_MONTH


def _name_tag(tags) -> str:
    for t in tags or []:
        if t.get("Key") == "Name":
            return t.get("Value", "")
    return ""


# ── Read primitives ────────────────────────────────────────────────────────────


@tool
def list_ec2_instances(state: str = "running", region: str = "us-east-1") -> list[dict]:
    """List EC2 instances in ``region``.

    Args:
        state: ``running``, ``stopped``, or ``all``.
        region: AWS region (default ``us-east-1``).
    """
    ec2 = client("ec2", region)
    filters = [] if state == "all" else [{"Name": "instance-state-name", "Values": [state]}]
    out = []
    for page in paginated(ec2, "describe_instances", Filters=filters):
        for r in page.get("Reservations", []):
            for inst in r.get("Instances", []):
                out.append({
                    "id": inst["InstanceId"],
                    "type": inst.get("InstanceType"),
                    "state": inst.get("State", {}).get("Name"),
                    "name": _name_tag(inst.get("Tags")),
                    "az": inst.get("Placement", {}).get("AvailabilityZone"),
                })
    return out


@tool
def get_ec2_metric(
    instance_id: str,
    metric: str = "CPUUtilization",
    days: int = 14,
    region: str = "us-east-1",
) -> dict:
    """Average CloudWatch metric for a single EC2 instance.

    Args:
        instance_id: Target instance ID (e.g. ``i-0abc``).
        metric: CloudWatch metric name from ``AWS/EC2`` (default ``CPUUtilization``).
        days: Lookback window.
        region: AWS region.
    """
    cw = client("cloudwatch", region)
    avg = cw_metric_average(
        cw, "AWS/EC2", metric,
        [{"Name": "InstanceId", "Value": instance_id}],
        days=days,
    )
    return {"instance_id": instance_id, "metric": metric, "days": days, "average": avg}


@tool
def list_ebs_volumes(state: str = "all", region: str = "us-east-1") -> list[dict]:
    """List EBS volumes in ``region``.

    Args:
        state: ``available`` (unattached), ``in-use``, or ``all``.
        region: AWS region.
    """
    ec2 = client("ec2", region)
    filters = [] if state == "all" else [{"Name": "status", "Values": [state]}]
    out = []
    for page in paginated(ec2, "describe_volumes", Filters=filters):
        for v in page.get("Volumes", []):
            out.append({
                "id": v["VolumeId"],
                "size_gb": int(v.get("Size", 0)),
                "state": v.get("State"),
                "az": v.get("AvailabilityZone"),
                "attachments": [a.get("InstanceId") for a in v.get("Attachments") or []],
            })
    return out


@tool
def list_nat_gateways(region: str = "us-east-1") -> list[dict]:
    """List NAT gateways in ``region``."""
    ec2 = client("ec2", region)
    out = []
    for page in paginated(ec2, "describe_nat_gateways"):
        for n in page.get("NatGateways", []):
            out.append({
                "id": n["NatGatewayId"],
                "state": n.get("State"),
                "vpc": n.get("VpcId"),
                "subnet": n.get("SubnetId"),
            })
    return out


@tool
def get_nat_traffic(nat_id: str, days: int = 7, region: str = "us-east-1") -> dict:
    """Total bytes-out for a NAT gateway over the past ``days``."""
    cw = client("cloudwatch", region)
    total = cw_metric_sum(
        cw, "AWS/NATGateway", "BytesOutToDestination",
        [{"Name": "NatGatewayId", "Value": nat_id}],
        days=days,
    )
    daily_gb = (total / (1024 ** 3)) / days if total else 0.0
    return {"nat_id": nat_id, "total_bytes": total, "daily_gb_avg": daily_gb}


@tool
def list_security_groups(region: str = "us-east-1") -> list[dict]:
    """List security groups with their inbound rules."""
    ec2 = client("ec2", region)
    out = []
    for page in paginated(ec2, "describe_security_groups"):
        for sg in page.get("SecurityGroups", []):
            out.append({
                "id": sg["GroupId"],
                "name": sg.get("GroupName"),
                "vpc": sg.get("VpcId"),
                "ingress": [
                    {
                        "protocol": r.get("IpProtocol"),
                        "from_port": r.get("FromPort"),
                        "to_port": r.get("ToPort"),
                        "cidrs": [ip.get("CidrIp") for ip in r.get("IpRanges") or []],
                    }
                    for r in sg.get("IpPermissions") or []
                ],
            })
    return out


# ── Composite analyzers ───────────────────────────────────────────────────────


@tool
def find_idle_ec2(
    cpu_threshold: float = 5.0,
    days: int = 14,
    region: str = "us-east-1",
) -> list[dict]:
    """Find running EC2 instances with average CPU below ``cpu_threshold`` over ``days``.

    Returns one dict per suspect instance with savings estimate and a suggested
    ``stop_ec2`` action payload.
    """
    ec2 = client("ec2", region)
    cw = client("cloudwatch", region)
    findings: list[dict] = []
    for page in paginated(
        ec2, "describe_instances",
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}],
    ):
        for r in page.get("Reservations", []):
            for inst in r.get("Instances", []):
                iid = inst["InstanceId"]
                avg = cw_metric_average(
                    cw, "AWS/EC2", "CPUUtilization",
                    [{"Name": "InstanceId", "Value": iid}],
                    days=days,
                )
                if avg is None or avg >= cpu_threshold:
                    continue
                itype = inst.get("InstanceType", "unknown")
                findings.append({
                    "instance_id": iid,
                    "name": _name_tag(inst.get("Tags")),
                    "type": itype,
                    "avg_cpu_pct": round(avg, 2),
                    "estimated_monthly_savings": round(_ec2_monthly(itype), 2),
                    "suggested_action": "stop_ec2",
                })
    return findings


@tool
def find_orphan_ebs(region: str = "us-east-1") -> list[dict]:
    """Find unattached EBS volumes (state ``available``) in ``region``."""
    ec2 = client("ec2", region)
    findings: list[dict] = []
    for page in paginated(
        ec2, "describe_volumes",
        Filters=[{"Name": "status", "Values": ["available"]}],
    ):
        for v in page.get("Volumes", []):
            size = int(v.get("Size", 0))
            findings.append({
                "volume_id": v["VolumeId"],
                "size_gb": size,
                "az": v.get("AvailabilityZone"),
                "estimated_monthly_savings": round(size * EBS_GP3_PER_GB_MONTH, 2),
                "suggested_action": "delete_volume",
            })
    return findings


@tool
def find_idle_nat_gateways(
    daily_gb_threshold: float = 1.0,
    days: int = 7,
    region: str = "us-east-1",
) -> list[dict]:
    """Find NAT gateways averaging less than ``daily_gb_threshold`` GB/day out-traffic."""
    ec2 = client("ec2", region)
    cw = client("cloudwatch", region)
    findings: list[dict] = []
    for page in paginated(ec2, "describe_nat_gateways"):
        for n in page.get("NatGateways", []):
            if n.get("State") != "available":
                continue
            nid = n["NatGatewayId"]
            total = cw_metric_sum(
                cw, "AWS/NATGateway", "BytesOutToDestination",
                [{"Name": "NatGatewayId", "Value": nid}],
                days=days,
            )
            daily_gb = (total / (1024 ** 3)) / days if total else 0.0
            if daily_gb >= daily_gb_threshold:
                continue
            findings.append({
                "nat_id": nid,
                "vpc": n.get("VpcId"),
                "daily_gb_avg": round(daily_gb, 3),
                "estimated_monthly_savings": round(NAT_HOURLY * HOURS_PER_MONTH, 2),
                "suggested_action": "delete_nat_gateway",
            })
    return findings


@tool
def audit_open_security_groups(
    sensitive_ports: list[int] | None = None,
    region: str = "us-east-1",
) -> list[dict]:
    """Find security groups with 0.0.0.0/0 open on sensitive ports.

    Args:
        sensitive_ports: Defaults to ``[22, 3389, 3306, 5432, 6379, 27017, 9200]``.
        region: AWS region.
    """
    ports = sensitive_ports or [22, 3389, 3306, 5432, 6379, 27017, 9200]
    ec2 = client("ec2", region)
    findings: list[dict] = []
    for page in paginated(ec2, "describe_security_groups"):
        for sg in page.get("SecurityGroups", []):
            exposed: list[int] = []
            for rule in sg.get("IpPermissions") or []:
                cidrs = [ip.get("CidrIp") for ip in rule.get("IpRanges") or []]
                if "0.0.0.0/0" not in cidrs:
                    continue
                lo = rule.get("FromPort") or 0
                hi = rule.get("ToPort") or 65535
                exposed.extend(p for p in ports if lo <= p <= hi)
            if exposed:
                findings.append({
                    "sg_id": sg["GroupId"],
                    "sg_name": sg.get("GroupName"),
                    "vpc": sg.get("VpcId"),
                    "exposed_ports": sorted(set(exposed)),
                })
    return findings


# ── Write actions (gated) ─────────────────────────────────────────────────────


@tool
def stop_ec2(instance_id: str, region: str = "us-east-1") -> dict:
    """Stop one EC2 instance. Pauses for user confirmation before calling AWS."""
    if not confirm(f"Stop EC2 instance {instance_id} in {region}?"):
        return {"instance_id": instance_id, "status": "cancelled_by_user"}
    ec2 = client("ec2", region)
    resp = ec2.stop_instances(InstanceIds=[instance_id])
    return {
        "instance_id": instance_id,
        "status": "stopped",
        "previous_state": resp["StoppingInstances"][0]["PreviousState"]["Name"],
    }


@tool
def delete_volume(volume_id: str, region: str = "us-east-1") -> dict:
    """Delete one EBS volume. Pauses for user confirmation."""
    if not confirm(f"Delete EBS volume {volume_id} in {region}?"):
        return {"volume_id": volume_id, "status": "cancelled_by_user"}
    ec2 = client("ec2", region)
    ec2.delete_volume(VolumeId=volume_id)
    return {"volume_id": volume_id, "status": "deleted"}


@tool
def delete_nat_gateway(nat_id: str, region: str = "us-east-1") -> dict:
    """Delete one NAT gateway. Pauses for user confirmation."""
    if not confirm(f"Delete NAT gateway {nat_id} in {region}?"):
        return {"nat_id": nat_id, "status": "cancelled_by_user"}
    ec2 = client("ec2", region)
    ec2.delete_nat_gateway(NatGatewayId=nat_id)
    return {"nat_id": nat_id, "status": "deleted"}


EC2_TOOLS = [
    list_ec2_instances, get_ec2_metric, list_ebs_volumes,
    list_nat_gateways, get_nat_traffic, list_security_groups,
    find_idle_ec2, find_orphan_ebs, find_idle_nat_gateways,
    audit_open_security_groups,
    stop_ec2, delete_volume, delete_nat_gateway,
]
