"""Lambda tools.

Module is named ``lambda_`` because ``lambda`` is a Python keyword.
"""

from __future__ import annotations

from langchain_core.tools import tool

from openkite.tools._aws import client, confirm, cw_metric_sum, paginated


@tool
def list_lambda_functions(region: str = "us-east-1") -> list[dict]:
    """List Lambda functions in ``region``."""
    lam = client("lambda", region)
    out = []
    for page in paginated(lam, "list_functions"):
        for fn in page.get("Functions", []):
            out.append({
                "name": fn["FunctionName"],
                "runtime": fn.get("Runtime"),
                "memory_mb": fn.get("MemorySize"),
                "last_modified": fn.get("LastModified"),
            })
    return out


@tool
def get_lambda_invocation_count(
    function_name: str,
    days: int = 30,
    region: str = "us-east-1",
) -> dict:
    """Total invocations for a Lambda function over the past ``days``."""
    cw = client("cloudwatch", region)
    total = cw_metric_sum(
        cw, "AWS/Lambda", "Invocations",
        [{"Name": "FunctionName", "Value": function_name}],
        days=days,
    )
    return {"function_name": function_name, "days": days, "invocations": int(total)}


@tool
def find_dead_lambda(days: int = 30, region: str = "us-east-1") -> list[dict]:
    """Find Lambda functions with zero invocations in the past ``days``."""
    lam = client("lambda", region)
    cw = client("cloudwatch", region)
    findings: list[dict] = []
    for page in paginated(lam, "list_functions"):
        for fn in page.get("Functions", []):
            name = fn["FunctionName"]
            total = cw_metric_sum(
                cw, "AWS/Lambda", "Invocations",
                [{"Name": "FunctionName", "Value": name}],
                days=days,
            )
            if total > 0:
                continue
            findings.append({
                "name": name,
                "runtime": fn.get("Runtime"),
                "last_modified": fn.get("LastModified"),
                "suggested_action": "delete_lambda",
            })
    return findings


@tool
def delete_lambda(function_name: str, region: str = "us-east-1") -> dict:
    """Delete one Lambda function. Pauses for user confirmation."""
    if not confirm(f"Delete Lambda function {function_name} in {region}?"):
        return {"function_name": function_name, "status": "cancelled_by_user"}
    lam = client("lambda", region)
    lam.delete_function(FunctionName=function_name)
    return {"function_name": function_name, "status": "deleted"}


LAMBDA_TOOLS = [
    list_lambda_functions, get_lambda_invocation_count,
    find_dead_lambda, delete_lambda,
]
