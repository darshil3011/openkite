"""All tools the ReAct agent can call.

Each submodule exports its tools as a list. ``ALL_TOOLS`` aggregates them so
``openkite.agent`` can hand the full toolbox to ``create_react_agent``.

Three layers:

- **Read primitives** — small, typed boto3 wrappers (list / get).
- **Composite analyzers** — combine primitives to answer "is X bad?" questions.
- **Write actions** — gated; each calls ``interrupt()`` before mutating AWS.
"""

from __future__ import annotations

from openkite.tools.cloudtrail import CLOUDTRAIL_TOOLS
from openkite.tools.cost import COST_TOOLS
from openkite.tools.ec2 import EC2_TOOLS
from openkite.tools.lambda_ import LAMBDA_TOOLS
from openkite.tools.rds import RDS_TOOLS
from openkite.tools.s3 import S3_TOOLS

ALL_TOOLS = [
    *EC2_TOOLS, *RDS_TOOLS, *LAMBDA_TOOLS, *S3_TOOLS, *COST_TOOLS, *CLOUDTRAIL_TOOLS,
]


__all__ = ["ALL_TOOLS"]
