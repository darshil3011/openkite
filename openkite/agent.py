"""The single LangGraph ReAct agent.

One compiled graph for every user query. The router LLM picks tools by name
and passes typed arguments. Write tools call ``interrupt()`` themselves to
ask for confirmation; no separate approval node is needed.
"""

from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.prebuilt import create_react_agent

from openkite.tools import ALL_TOOLS

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

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


@lru_cache(maxsize=2)
def build_agent(
    model: str | None = None,
    db_path: str | None = "checkpoints/openkite.db",
) -> Any:
    """Compile the ReAct agent. Cached because compilation is non-trivial.

    Args:
        model: Anthropic model name. Falls back to ``$OPENKITE_MODEL`` then
            ``DEFAULT_MODEL``.
        db_path: SQLite path for checkpointer; ``None`` uses in-memory.
    """
    model_name = model or os.getenv("OPENKITE_MODEL", DEFAULT_MODEL)
    return create_react_agent(
        model=ChatAnthropic(model=model_name, temperature=0, max_tokens=4096),
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=_make_checkpointer(db_path),
    )
