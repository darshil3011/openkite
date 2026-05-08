"""S3 tools."""

from __future__ import annotations

from botocore.exceptions import ClientError
from langchain_core.tools import tool

from openkite.tools._aws import client, confirm

_DEFAULT_LIFECYCLE = {
    "Rules": [{
        "ID": "openkite-default-transition",
        "Status": "Enabled",
        "Filter": {"Prefix": ""},
        "Transitions": [{"Days": 90, "StorageClass": "STANDARD_IA"}],
        "NoncurrentVersionExpiration": {"NoncurrentDays": 365},
    }]
}


def _bucket_in_region(loc: str | None, region: str) -> bool:
    if region == "us-east-1":
        return loc in (None, "", "us-east-1")
    return loc == region


@tool
def list_s3_buckets() -> list[dict]:
    """List all S3 buckets in the account, with their region."""
    s3 = client("s3", "us-east-1")  # list_buckets is global
    out = []
    for b in s3.list_buckets().get("Buckets", []):
        try:
            loc = s3.get_bucket_location(Bucket=b["Name"]).get("LocationConstraint")
        except ClientError:
            loc = None
        out.append({
            "name": b["Name"],
            "region": loc or "us-east-1",
            "created": b.get("CreationDate").isoformat() if b.get("CreationDate") else None,
        })
    return out


@tool
def get_s3_lifecycle(bucket: str) -> dict:
    """Return the lifecycle configuration for one bucket, or ``rules: []`` if none."""
    s3 = client("s3", "us-east-1")
    try:
        resp = s3.get_bucket_lifecycle_configuration(Bucket=bucket)
        return {"bucket": bucket, "rules": resp.get("Rules") or []}
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchLifecycleConfiguration":
            return {"bucket": bucket, "rules": []}
        raise


@tool
def get_s3_public_access(bucket: str) -> dict:
    """Check whether a bucket is publicly accessible via ACL or bucket policy."""
    s3 = client("s3", "us-east-1")
    via_acl = False
    try:
        acl = s3.get_bucket_acl(Bucket=bucket)
        for grant in acl.get("Grants", []):
            uri = grant.get("Grantee", {}).get("URI", "")
            if uri.endswith("AllUsers") or uri.endswith("AuthenticatedUsers"):
                via_acl = True
                break
    except ClientError:
        pass

    pab_blocked = False
    try:
        pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
        pab_blocked = all(pab.get(k, False) for k in (
            "BlockPublicAcls", "IgnorePublicAcls",
            "BlockPublicPolicy", "RestrictPublicBuckets",
        ))
    except ClientError:
        pab_blocked = False

    return {"bucket": bucket, "public_via_acl": via_acl, "public_access_block_active": pab_blocked}


@tool
def find_buckets_without_lifecycle(region: str = "us-east-1") -> list[dict]:
    """Find S3 buckets in ``region`` that have no lifecycle rules configured."""
    s3 = client("s3", region)
    findings: list[dict] = []
    for b in s3.list_buckets().get("Buckets", []):
        name = b["Name"]
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
        except ClientError:
            continue
        if not _bucket_in_region(loc, region):
            continue
        try:
            resp = s3.get_bucket_lifecycle_configuration(Bucket=name)
            if resp.get("Rules"):
                continue
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "NoSuchLifecycleConfiguration":
                continue
        findings.append({"bucket": name, "suggested_action": "put_s3_lifecycle"})
    return findings


@tool
def audit_public_buckets(region: str = "us-east-1") -> list[dict]:
    """Find buckets in ``region`` that are publicly accessible."""
    s3 = client("s3", region)
    findings: list[dict] = []
    for b in s3.list_buckets().get("Buckets", []):
        name = b["Name"]
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint")
        except ClientError:
            continue
        if not _bucket_in_region(loc, region):
            continue
        info = get_s3_public_access.invoke({"bucket": name})
        if info["public_via_acl"] and not info["public_access_block_active"]:
            findings.append({"bucket": name, "reason": "public_acl"})
    return findings


@tool
def put_s3_lifecycle(bucket: str, rules: dict | None = None) -> dict:
    """Apply a lifecycle configuration to one bucket. Pauses for confirmation.

    Args:
        bucket: Bucket name.
        rules: Full ``LifecycleConfiguration`` dict; defaults to a 90-day
            STANDARD_IA transition + 365-day noncurrent expiration.
    """
    config = rules or _DEFAULT_LIFECYCLE
    if not confirm(f"Apply lifecycle rules to bucket {bucket}?"):
        return {"bucket": bucket, "status": "cancelled_by_user"}
    s3 = client("s3", "us-east-1")
    s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration=config)
    return {"bucket": bucket, "status": "updated"}


S3_TOOLS = [
    list_s3_buckets, get_s3_lifecycle, get_s3_public_access,
    find_buckets_without_lifecycle, audit_public_buckets,
    put_s3_lifecycle,
]
