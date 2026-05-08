"""The single LangGraph ReAct agent.

One compiled graph for every user query. The router LLM picks tools by name
and passes typed arguments. Write tools call ``interrupt()`` themselves to
ask for confirmation; no separate approval node is needed.
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from openkite.llm import build_llm
from openkite.tools import ALL_TOOLS

SYSTEM_PROMPT = """You are an AWS operations assistant.

You have a toolbox of read-only inspectors and a smaller set of write actions
(stop_ec2, delete_volume, delete_lambda, put_s3_lifecycle, …). Use them like
this:

- For a narrow question ("is i-abc idle?", "what's in bucket X?"), call ONE
  targeted tool. Pass the resource ID or filter — never sweep everything when
  the user gave you a target.
- For a broad audit ("find cost waste", "security review"), call several
  composite analyzers in parallel: find_idle_ec2, find_orphan_ebs,
  find_dead_lambda, find_buckets_without_lifecycle, find_idle_nat_gateways,
  audit_public_buckets, audit_open_security_groups.
- Reach for primitives (list_*, get_*) only when an analyzer doesn't fit.
- Default region is us-east-1; honor any region the user names.
- After tool results, summarize in plain language. Include resource IDs.
  Estimated savings come from the tool output — don't invent numbers.
- Write tools modify the account. They will pause and ask the user to
  confirm before doing anything. Call them only when the user has explicitly
  asked for the change."""


def _make_checkpointer(db_path: str | None) -> BaseCheckpointSaver:
    if db_path is None:
        return InMemorySaver()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)


@lru_cache(maxsize=4)
def build_agent(
    provider: str | None = None,
    model: str | None = None,
    db_path: str | None = "checkpoints/openkite.db",
) -> Any:
    """Compile the ReAct agent. Cached because compilation is non-trivial.

    Args:
        provider: LLM provider. Falls back to ``$OPENKITE_PROVIDER`` then ``anthropic``.
        model: Model id (or combined ``provider:model``). Falls back to
            ``$OPENKITE_MODEL`` then the provider default.
        db_path: SQLite path for checkpointer; ``None`` uses in-memory.
    """
    return create_react_agent(
        model=build_llm(provider=provider, model=model),
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=_make_checkpointer(db_path),
    )
