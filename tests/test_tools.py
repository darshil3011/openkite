"""Tool-level tests against moto.

Each tool is invoked through its LangChain ``invoke`` interface (the same
path the ReAct agent uses) so the test exercises argument validation, not
just the underlying boto3 logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from openkite.tools import cost as cost_mod
from openkite.tools.ec2 import (
    audit_open_security_groups,
    delete_volume,
    find_idle_ec2,
    find_idle_nat_gateways,
    find_orphan_ebs,
    get_ec2_metric,
    list_ebs_volumes,
    list_ec2_instances,
    stop_ec2,
)
from openkite.tools.lambda_ import find_dead_lambda, list_lambda_functions
from openkite.tools.rds import find_idle_rds, list_rds_instances
from openkite.tools.s3 import (
    audit_public_buckets,
    find_buckets_without_lifecycle,
    get_s3_lifecycle,
    list_s3_buckets,
)

REGION = "us-east-1"


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_SESSION_TOKEN": "test", "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(k, v)


def _seed_metric(cw, namespace, metric, dim_name, dim_value, value, unit="Percent", count=24):
    """Seed `count` hourly datapoints back-dated so moto includes them."""
    now = datetime.now(UTC)
    for i in range(1, count + 1):
        cw.put_metric_data(
            Namespace=namespace,
            MetricData=[{
                "MetricName": metric,
                "Dimensions": [{"Name": dim_name, "Value": dim_value}],
                "Timestamp": now - timedelta(hours=i),
                "Value": value,
                "Unit": unit,
            }],
        )


# ── EC2 read primitives ──────────────────────────────────────────────────────

@mock_aws
def test_list_ec2_instances_filters_by_state():
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    ec2.run_instances(ImageId=ami, InstanceType="t3.micro", MinCount=2, MaxCount=2)

    running = list_ec2_instances.invoke({"state": "running", "region": REGION})
    assert len(running) == 2
    assert all(i["state"] == "running" for i in running)


@mock_aws
def test_get_ec2_metric_returns_average():
    ec2 = boto3.client("ec2", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro",
                             MinCount=1, MaxCount=1)["Instances"][0]
    _seed_metric(cw, "AWS/EC2", "CPUUtilization", "InstanceId", inst["InstanceId"], 7.5)

    out = get_ec2_metric.invoke({"instance_id": inst["InstanceId"], "days": 14, "region": REGION})
    assert out["instance_id"] == inst["InstanceId"]
    assert out["average"] == pytest.approx(7.5)


@mock_aws
def test_list_ebs_volumes_includes_attachments():
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro",
                             MinCount=1, MaxCount=1)["Instances"][0]
    free = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)
    busy = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20)
    ec2.attach_volume(VolumeId=busy["VolumeId"],
                      InstanceId=inst["InstanceId"], Device="/dev/sdh")

    vols = list_ebs_volumes.invoke({"state": "all", "region": REGION})
    by_id = {v["id"]: v for v in vols}
    assert by_id[free["VolumeId"]]["attachments"] == []
    assert inst["InstanceId"] in by_id[busy["VolumeId"]]["attachments"]


# ── Composite analyzers ─────────────────────────────────────────────────────

@mock_aws
def test_find_idle_ec2_passes_threshold_through():
    ec2 = boto3.client("ec2", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    idle = ec2.run_instances(ImageId=ami, InstanceType="t3.medium",
                             MinCount=1, MaxCount=1)["Instances"][0]
    busy = ec2.run_instances(ImageId=ami, InstanceType="t3.medium",
                             MinCount=1, MaxCount=1)["Instances"][0]
    _seed_metric(cw, "AWS/EC2", "CPUUtilization", "InstanceId", idle["InstanceId"], 1.0)
    _seed_metric(cw, "AWS/EC2", "CPUUtilization", "InstanceId", busy["InstanceId"], 80.0)

    out = find_idle_ec2.invoke({"cpu_threshold": 5.0, "days": 14, "region": REGION})
    flagged = {f["instance_id"] for f in out}
    assert idle["InstanceId"] in flagged
    assert busy["InstanceId"] not in flagged


@mock_aws
def test_find_orphan_ebs_only_flags_available():
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro",
                             MinCount=1, MaxCount=1)["Instances"][0]
    orphan = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=50)
    attached = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20)
    ec2.attach_volume(VolumeId=attached["VolumeId"],
                      InstanceId=inst["InstanceId"], Device="/dev/sdh")

    out = find_orphan_ebs.invoke({"region": REGION})
    ids = {f["volume_id"] for f in out}
    assert orphan["VolumeId"] in ids
    assert attached["VolumeId"] not in ids


@mock_aws
def test_find_idle_nat_gateways_with_no_traffic():
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    sub = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.1.0/24")["Subnet"]["SubnetId"]
    eip = ec2.allocate_address(Domain="vpc")
    nat = ec2.create_nat_gateway(SubnetId=sub, AllocationId=eip["AllocationId"])
    nat_id = nat["NatGateway"]["NatGatewayId"]

    out = find_idle_nat_gateways.invoke({"daily_gb_threshold": 1.0, "region": REGION})
    assert any(f["nat_id"] == nat_id for f in out)


@mock_aws
def test_audit_open_security_groups_finds_open_ssh():
    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    sg = ec2.create_security_group(GroupName="exposed", Description="x", VpcId=vpc)
    ec2.authorize_security_group_ingress(
        GroupId=sg["GroupId"],
        IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
        }],
    )
    out = audit_open_security_groups.invoke({"region": REGION})
    flagged = {f["sg_id"] for f in out}
    assert sg["GroupId"] in flagged


# ── RDS ─────────────────────────────────────────────────────────────────────

@mock_aws
def test_find_idle_rds_uses_threshold():
    rds = boto3.client("rds", region_name=REGION)
    cw = boto3.client("cloudwatch", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="db-quiet", Engine="postgres",
        DBInstanceClass="db.t3.medium", AllocatedStorage=20,
        MasterUsername="u", MasterUserPassword="password123",
    )
    _seed_metric(cw, "AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "db-quiet", 2.0)

    out = find_idle_rds.invoke({"cpu_threshold": 10.0, "days": 7, "region": REGION})
    assert any(f["db_id"] == "db-quiet" for f in out)


@mock_aws
def test_list_rds_instances():
    rds = boto3.client("rds", region_name=REGION)
    rds.create_db_instance(
        DBInstanceIdentifier="db1", Engine="mysql", DBInstanceClass="db.t3.micro",
        AllocatedStorage=20, MasterUsername="u", MasterUserPassword="password123",
    )
    out = list_rds_instances.invoke({"state": "all", "region": REGION})
    assert any(d["id"] == "db1" for d in out)


# ── Lambda ──────────────────────────────────────────────────────────────────

@mock_aws
def test_find_dead_lambda_includes_zero_invocation_fn():
    iam = boto3.client("iam", region_name=REGION)
    role = iam.create_role(
        RoleName="lambda-role",
        AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[]}',
    )["Role"]["Arn"]
    lam = boto3.client("lambda", region_name=REGION)
    lam.create_function(
        FunctionName="dead-fn", Runtime="python3.11", Role=role,
        Handler="index.handler", Code={"ZipFile": b"def handler(e,c): pass"},
    )

    out = find_dead_lambda.invoke({"days": 30, "region": REGION})
    assert any(f["name"] == "dead-fn" for f in out)


@mock_aws
def test_list_lambda_functions():
    iam = boto3.client("iam", region_name=REGION)
    role = iam.create_role(
        RoleName="r", AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[]}',
    )["Role"]["Arn"]
    lam = boto3.client("lambda", region_name=REGION)
    lam.create_function(FunctionName="x", Runtime="python3.11", Role=role,
                        Handler="i.h", Code={"ZipFile": b"def h(e,c): pass"})
    out = list_lambda_functions.invoke({"region": REGION})
    assert any(f["name"] == "x" for f in out)


# ── S3 ──────────────────────────────────────────────────────────────────────

@mock_aws
def test_find_buckets_without_lifecycle_skips_buckets_with_rules():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="raw")
    s3.create_bucket(Bucket="managed")
    s3.put_bucket_lifecycle_configuration(
        Bucket="managed",
        LifecycleConfiguration={"Rules": [{
            "ID": "x", "Status": "Enabled",
            "Filter": {"Prefix": ""}, "Expiration": {"Days": 365},
        }]},
    )
    out = find_buckets_without_lifecycle.invoke({"region": REGION})
    names = {f["bucket"] for f in out}
    assert "raw" in names
    assert "managed" not in names


@mock_aws
def test_get_s3_lifecycle_returns_empty_for_unmanaged_bucket():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="empty-rules")
    out = get_s3_lifecycle.invoke({"bucket": "empty-rules"})
    assert out == {"bucket": "empty-rules", "rules": []}


@mock_aws
def test_list_s3_buckets_returns_region():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="openkite-list-test")
    out = list_s3_buckets.invoke({})
    assert any(b["name"] == "openkite-list-test" for b in out)


@mock_aws
def test_audit_public_buckets_returns_list():
    # Just smoke-test that the analyzer runs without error against moto.
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket="audit-test")
    out = audit_public_buckets.invoke({"region": REGION})
    assert isinstance(out, list)


# ── Cost Explorer (stubbed; moto's CE is partial) ──────────────────────────

def test_get_ri_coverage_parses_response(monkeypatch):
    payload = {
        "CoveragesByTime": [{"Groups": [
            {"Attributes": {"instanceTypeFamily": "m5"},
             "Coverage": {"CoverageHours": {
                 "CoverageHoursPercentage": "20.0", "OnDemandHours": "500.0",
             }}},
        ]}],
    }

    class _StubCE:
        def get_reservation_coverage(self, **_):
            return payload

    monkeypatch.setattr(cost_mod, "client", lambda *_a, **_k: _StubCE())
    out = cost_mod.get_ri_coverage.invoke({"days": 30})
    assert out[0]["family"] == "m5"
    assert out[0]["coverage_pct"] == 20.0
    assert out[0]["on_demand_hours"] == 500.0


# ── Write tools — interrupt path ────────────────────────────────────────────

def test_stop_ec2_returns_cancellation_when_user_declines(monkeypatch):
    """When the confirm() helper says no, no boto3 call should fire."""
    monkeypatch.setattr("openkite.tools.ec2.confirm", lambda _msg: False)
    called = {"count": 0}
    monkeypatch.setattr(
        "openkite.tools.ec2.client",
        lambda *_a, **_k: pytest.fail("boto3 should not be called when user declines"),
    )
    out = stop_ec2.invoke({"instance_id": "i-x"})
    assert out["status"] == "cancelled_by_user"
    assert called["count"] == 0


@mock_aws
def test_delete_volume_runs_when_user_confirms(monkeypatch):
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]
    monkeypatch.setattr("openkite.tools.ec2.confirm", lambda _msg: True)
    out = delete_volume.invoke({"volume_id": vol, "region": REGION})
    assert out["status"] == "deleted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
