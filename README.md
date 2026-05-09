<div align="center">

```
   _____            _   _            _ 
  / ____|          | | (_)          | |
 | (___   ___ _ __ | |_ _ _ __   ___| |
  \___ \ / _ \ '_ \| __| | '_ \ / _ \ |
  ____) |  __/ | | | |_| | | | |  __/ |
 |_____/ \___|_| |_|\__|_|_| |_|\___|_|
                                       
       Natural-language AWS agent
```

**A LangGraph ReAct agent that talks to AWS in plain English — answers questions, audits cost & security, and runs approved actions.**

[![Star on GitHub](https://img.shields.io/github/stars/darshil3011/openkite?style=for-the-badge&logo=github&color=ffd700&logoColor=white)](https://github.com/darshil3011/openkite/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/darshil3011)

[Quickstart](#quickstart) · [Examples](#examples) · [Toolbox](#toolbox) · [Architecture](#architecture) · [Contributing](#contributing)

</div>

---

## Why OpenKite

Most AWS automation is either a static script (run all the checks every time) or a heavyweight workflow engine. OpenKite is the middle ground: a **single ReAct agent** with ~30 typed tools that the LLM picks from based on what you actually asked.

- Ask narrow questions → **one** tool call → cheap and fast.
- Ask for an audit → several analyzers in parallel.
- Ask to change something → the agent pauses and asks for confirmation before any write.

No bespoke graph compilation, no plan-generation step, no two-stage approval pipeline — just LangGraph's standard `create_react_agent` over a clean toolbox.

## Quickstart

### 1. Install

```bash
git clone https://github.com/darshil3011/openkite.git
cd openkite
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Add your API keys

OpenKite needs two keys: **AWS credentials** for the tools, and an **Anthropic API key** for the LLM.

Simplest way — export them in your shell:

```bash
# AWS (read-only IAM user is recommended for safety)
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_DEFAULT_REGION=us-east-1

# Anthropic (https://console.anthropic.com/)
export ANTHROPIC_API_KEY=sk-ant-...
```

To make it persistent, append those lines to `~/.bashrc` (or `~/.zshrc`) and `source` it. Boto3 and `ChatAnthropic` pick up the env vars automatically — no code changes needed.

Alternatively, if you already use AWS profiles:

```bash
export AWS_PROFILE=my-readonly-profile
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Use it

```bash
openkite ask "list my running EC2 instances"
openkite ask "is i-03e3c90cd9ea52c82 idle?"
openkite ask "audit my AWS for cost waste"
openkite ask "show me public S3 buckets"
openkite ask "delete volume vol-0abc"          # pauses to confirm
```

Browse the toolbox:

```bash
openkite tools
```

## Examples

### Targeted question — one LLM call, one boto3 call

```
$ openkite ask "is i-03e3c90cd9ea52c82 idle?"

→ get_ec2_metric(instance_id='i-03e3c90cd9ea52c82', days=14)
   {"average": 0.3, "metric": "CPUUtilization"}

Yes — the instance averaged 0.3% CPU over 14 days. It is idle.
```

### Broad audit — analyzers fan out in parallel

```
$ openkite ask "audit cost waste in us-east-1"

→ find_idle_ec2(region='us-east-1')
→ find_orphan_ebs(region='us-east-1')
→ find_dead_lambda(region='us-east-1')
→ find_buckets_without_lifecycle(region='us-east-1')
→ find_idle_nat_gateways(region='us-east-1')

Found 11 candidates totalling ~$143/mo:
- 4 idle EC2 instances (proxy, wc-admin-test, wc-admin-prod, ...)
- 1 idle RDS (wc-admin-prod)
- 6 unmanaged S3 buckets
```

### Write action — paused for human approval

```
$ openkite ask "delete vol-0abc1234"

→ delete_volume(volume_id='vol-0abc1234')

[confirm] Delete EBS volume vol-0abc1234 in us-east-1?  (yes/no): yes

   {"volume_id": "vol-0abc1234", "status": "deleted"}

Done — the volume has been deleted.
```

### Follow-up questions on the same thread

```bash
openkite ask "list orphan EBS volumes" --thread my-cleanup
openkite ask "delete the smallest one" --thread my-cleanup    # remembers the list
```

## Toolbox

31 typed tools across six service families:

| Category | Tools |
|---|---|
| **EC2** | `list_ec2_instances`, `get_ec2_metric`, `find_idle_ec2`, `stop_ec2` |
| **EBS** | `list_ebs_volumes`, `find_orphan_ebs`, `delete_volume` |
| **NAT / VPC / SG** | `list_nat_gateways`, `get_nat_traffic`, `find_idle_nat_gateways`, `delete_nat_gateway`, `list_security_groups`, `audit_open_security_groups` |
| **RDS** | `list_rds_instances`, `get_rds_metric`, `find_idle_rds`, `stop_rds` |
| **Lambda** | `list_lambda_functions`, `get_lambda_invocation_count`, `find_dead_lambda`, `delete_lambda` |
| **S3** | `list_s3_buckets`, `get_s3_lifecycle`, `get_s3_public_access`, `find_buckets_without_lifecycle`, `audit_public_buckets`, `put_s3_lifecycle` |
| **Cost** | `get_cost_breakdown`, `get_ri_coverage` |
| **CloudTrail** | `lookup_recent_changes`, `get_cloudtrail_event` |

Three layers per service:

- **Read primitives** (`list_*`, `get_*`) — small, fast, parameterised.
- **Composite analyzers** (`find_*`, `audit_*`) — wrap several primitives to answer "is X bad?".
- **Write actions** (`stop_*`, `delete_*`, `put_*`) — pause for confirmation via `interrupt()` before mutating anything.

Run `openkite tools` to see live arg signatures.

## Architecture

```
┌──────────────┐   user query
│     CLI      │ ─────────────────┐
└──────────────┘                  ▼
                          ┌───────────────┐
                          │  ReAct agent  │  ← create_react_agent(model, tools)
                          │  (LLM router) │     model: Claude Haiku 4.5 by default
                          └───────┬───────┘
                                  │ tool_calls
                          ┌───────▼───────┐
                          │  tools node   │  ← 31 @tool functions
                          └───────┬───────┘
                                  │ ToolMessage
                                  └────► back to agent until done
                                  
   write tools call interrupt() → graph pauses → user replies → resume
```

Two nodes. One conditional edge. SQLite checkpointer for thread persistence. That's it.

- **Model**: `claude-haiku-4-5-20251001` by default. Override with `OPENKITE_MODEL=claude-sonnet-4-6` for harder reasoning.
- **State**: standard LangGraph `MessagesState` — no custom reducers, no parallel-write conflicts.
- **HITL**: per-tool, in-band. Each write tool calls `confirm()` (a thin `interrupt()` wrapper) before its boto3 call.

See [`openkite/agent.py`](openkite/agent.py) — the entire agent is ~75 lines.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | _required_ | Claude API key |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | _required_ | AWS credentials |
| `AWS_DEFAULT_REGION` | `us-east-1` | Default region for tools |
| `AWS_PROFILE` | — | Use a named profile from `~/.aws/credentials` instead |
| `OPENKITE_MODEL` | `claude-haiku-4-5-20251001` | Override the LLM |

## Development

```bash
# run the test suite (29 tests; moto-backed where possible, stubs elsewhere; no real AWS calls)
pytest -q

# lint
ruff check openkite tests

# install dev deps
pip install -e ".[dev]"
```

The full test suite never hits real AWS or real Anthropic — moto fakes the AWS API and a `FakeMessagesListChatModel` scripts the LLM responses for ReAct flow tests.

## Project layout

```
openkite/
├── agent.py              # build_agent() = create_react_agent(...)
├── cli.py                # `openkite ask` and `openkite tools`
└── tools/
    ├── _aws.py           # boto3 client factory + CW metric helpers + confirm()
    ├── ec2.py            # EC2 / EBS / NAT / SG tools
    ├── rds.py            # RDS tools
    ├── lambda_.py        # Lambda tools
    ├── s3.py             # S3 tools
    ├── cost.py           # Cost Explorer tools
    └── cloudtrail.py     # CloudTrail recent-changes lookup
tests/
├── test_tools.py         # moto-backed tool tests
└── test_agent.py         # ReAct flow tests with fake LLM
```

## Roadmap

- [ ] SSM `run_ssm_command` tool — execute shell commands on EC2 without SSH
- [ ] IAM tools — `list_iam_users`, `audit_iam_mfa`, `audit_old_access_keys`
- [ ] CloudWatch Alarm triager — ReAct loop over alarms in `ALARM` state
- [x] CloudTrail tools — `lookup_recent_changes`, `get_cloudtrail_event`
- [ ] Multi-region scans
- [ ] Slack / GitHub Issues output
- [ ] Web UI

Pull requests welcome — see [Contributing](#contributing).

## Contributing

The toolbox is the contract. To add a new capability:

1. Pick a service file in `openkite/tools/` (or create a new one and register it in `__init__.py`).
2. Add a `@tool`-decorated function with **typed args** and a **clear one-line docstring** — the LLM picks tools by description.
3. Append it to the module's `*_TOOLS` list.
4. Write a moto test in `tests/test_tools.py`.
5. `ruff check` + `pytest`.

Composite analyzers should accept thresholds as parameters (e.g. `cpu_threshold=5.0`) so the LLM can pass arguments instead of getting hardcoded behaviour.

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by [Darshil](https://linkedin.com/in/darshil3011). If OpenKite saves you a Sunday of cleanup, a star on [GitHub](https://github.com/darshil3011/openkite) is the best thank-you.
