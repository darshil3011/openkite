"""ReAct agent flow tests using a fake LLM.

We replace ChatAnthropic with a ``FakeMessagesListChatModel`` that emits a
predetermined sequence of messages. Each test scripts the conversation:

    [tool-call AIMessage] → tool runs → [final AIMessage]

This exercises the agent → tools → agent loop end-to-end without burning
real API credits.
"""

from __future__ import annotations

import boto3
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
from moto import mock_aws

from openkite.tools import ALL_TOOLS

REGION = "us-east-1"


class _FakeChat(FakeMessagesListChatModel):
    """``FakeMessagesListChatModel`` with a no-op ``bind_tools`` so the ReAct
    agent's tool-binding step doesn't blow up. We don't actually need the
    bound schema — the scripted ``AIMessage.tool_calls`` already encode what
    the ReAct loop should dispatch."""

    def bind_tools(self, tools, **_kwargs):  # noqa: D401
        return self


@pytest.fixture(autouse=True)
def aws_creds(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_SESSION_TOKEN": "test", "AWS_DEFAULT_REGION": REGION,
    }.items():
        monkeypatch.setenv(k, v)


def _fake(messages: list[AIMessage]):
    """ChatAnthropic stand-in. Returns each scripted message in order."""
    return _FakeChat(responses=messages)


def _build(model):
    return create_react_agent(model=model, tools=ALL_TOOLS, checkpointer=InMemorySaver())


def _config():
    return {"configurable": {"thread_id": "t-1"}}


# ── Targeted query: one tool call, then final answer ────────────────────────


@mock_aws
def test_targeted_query_calls_one_tool():
    ec2 = boto3.client("ec2", region_name=REGION)
    ami = ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]
    inst = ec2.run_instances(ImageId=ami, InstanceType="t3.micro",
                             MinCount=1, MaxCount=1)["Instances"][0]
    iid = inst["InstanceId"]

    scripted = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "list_ec2_instances",
                "args": {"state": "running", "region": REGION},
                "id": "call-1",
            }],
        ),
        AIMessage(content=f"Found 1 running instance: {iid}."),
    ]
    agent = _build(_fake(scripted))
    final = agent.invoke({"messages": [{"role": "user", "content": "list my running ec2"}]},
                         config=_config())

    # The tool ran, its result message is in the conversation, and the LLM finished.
    names = [getattr(m, "name", None) for m in final["messages"]]
    assert "list_ec2_instances" in names
    assert iid in final["messages"][-1].content


# ── Audit query: multiple parallel tool calls in one turn ──────────────────


@mock_aws
def test_audit_query_runs_multiple_tools_in_one_turn():
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)  # one orphan

    scripted = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "find_orphan_ebs", "args": {"region": REGION}, "id": "c1"},
                {"name": "find_idle_ec2",   "args": {"region": REGION}, "id": "c2"},
            ],
        ),
        AIMessage(content="Audit complete."),
    ]
    agent = _build(_fake(scripted))
    final = agent.invoke({"messages": [{"role": "user", "content": "audit cost waste"}]},
                         config=_config())
    tool_names = {getattr(m, "name", None) for m in final["messages"]}
    assert {"find_orphan_ebs", "find_idle_ec2"}.issubset(tool_names)


# ── Write tool: interrupts and resumes ─────────────────────────────────────


@mock_aws
def test_write_tool_pauses_for_confirmation_then_resumes():
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]

    scripted = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "delete_volume",
                "args": {"volume_id": vol, "region": REGION},
                "id": "call-del",
            }],
        ),
        AIMessage(content=f"Deleted {vol}."),
    ]
    agent = _build(_fake(scripted))
    config = _config()

    agent.invoke({"messages": [{"role": "user", "content": f"delete {vol}"}]}, config=config)

    # The graph should be paused on the confirm_action interrupt.
    snap = agent.get_state(config)
    assert snap.tasks
    interrupts = snap.tasks[0].interrupts
    assert interrupts and interrupts[0].value["type"] == "confirm_action"
    assert vol in interrupts[0].value["message"]

    # User confirms — the volume should actually be deleted.
    final = agent.invoke(Command(resume="yes"), config=config)
    assert any(getattr(m, "name", None) == "delete_volume"
               and "deleted" in str(m.content).lower()
               for m in final["messages"])

    # And boto3 actually performed the delete.
    remaining = boto3.client("ec2", region_name=REGION).describe_volumes()["Volumes"]
    assert all(v["VolumeId"] != vol for v in remaining)


@mock_aws
def test_write_tool_aborts_when_user_declines():
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=10)["VolumeId"]

    scripted = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "delete_volume",
                "args": {"volume_id": vol, "region": REGION},
                "id": "call-del",
            }],
        ),
        AIMessage(content="OK, leaving the volume alone."),
    ]
    agent = _build(_fake(scripted))
    config = _config()

    agent.invoke({"messages": [{"role": "user", "content": f"delete {vol}"}]}, config=config)
    final = agent.invoke(Command(resume="no"), config=config)

    # Volume must still exist.
    remaining = boto3.client("ec2", region_name=REGION).describe_volumes()["Volumes"]
    assert any(v["VolumeId"] == vol for v in remaining)
    # And the tool message reports cancellation.
    cancelled = [m for m in final["messages"]
                 if getattr(m, "name", None) == "delete_volume"
                 and "cancelled" in str(m.content).lower()]
    assert cancelled


# ── Toolbox sanity ─────────────────────────────────────────────────────────


def test_toolbox_size_and_shapes():
    assert len(ALL_TOOLS) >= 25
    # Every tool must have a non-empty description (LLM picks tools by description).
    for t in ALL_TOOLS:
        assert t.description, f"{t.name} has empty description"
        assert hasattr(t, "args"), f"{t.name} has no typed args"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
