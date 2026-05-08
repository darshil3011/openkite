# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- SSM `run_ssm_command` tool — execute shell commands on EC2 without SSH.
- IAM tools (`list_iam_users`, `audit_iam_mfa`, `audit_old_access_keys`).
- CloudWatch alarm triager (ReAct loop over alarms in `ALARM` state).
- CloudTrail audit tools.
- Multi-region scans.

## [0.2.0] — 2026-05-07

Major architectural rewrite. The agent is now a standard LangGraph ReAct loop
over a typed boto3 toolbox, replacing the previous bespoke graph-construction
pipeline.

### Added

- `openkite/agent.py` — single-file ReAct agent built with
  `create_react_agent` (~75 lines).
- `openkite/tools/` — 29 typed `@tool` functions across EC2, RDS, Lambda, S3,
  and Cost Explorer, organised into three layers (read primitives, composite
  analyzers, gated write actions).
- `openkite ask "..."` command — natural-language query with live tool-call
  streaming.
- `openkite tools` command — browse the toolbox with arg signatures.
- Per-tool human-in-the-loop: write tools call `interrupt()` themselves to
  pause for confirmation before mutating AWS.
- `tests/test_agent.py` — end-to-end ReAct flow tests with a fake LLM.
- Default model is now Claude Haiku 4.5 (override via `OPENKITE_MODEL`).
- `--thread <id>` for follow-up queries on the same conversation.

### Changed

- Replaced the runtime graph-construction layer (intent parser →
  `ExecutionPlan` → `build_dynamic_graph`) with a single compiled ReAct graph.
  The LLM picks tools and passes typed arguments instead of selecting from a
  catalog of agents.
- Replaced the two-stage approval flow (plan-approval interrupt →
  human-approval interrupt → executor → validator) with per-tool inline
  confirmation.

### Removed

- `openkite/agents/` (graph-resident agent nodes — superseded by tools).
- `openkite/planning/` (intent parser, plan generator).
- `openkite/graph_builder.py` (dynamic graph constructor).
- `openkite/registry.py`, `openkite/render.py`, `openkite/models.py`,
  `openkite/llm.py`.

### Fixed

- LangGraph `InvalidUpdateError` when multiple reporters wrote the same
  `report_markdown` key in parallel (no longer applicable — there are no
  reporter agents in the new design).

## [0.1.0] — 2026-05-06

Initial release: NL-driven dynamic agent orchestration with a planner LLM
that built a fresh `StateGraph` per request. 7 FinOps scanners with real
boto3 implementations; security/incident/reporting agents stubbed.

[Unreleased]: https://github.com/darshil3011/openkite/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/darshil3011/openkite/releases/tag/v0.2.0
[0.1.0]: https://github.com/darshil3011/openkite/releases/tag/v0.1.0
