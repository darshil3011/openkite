---
name: Bug report
about: Something isn't working the way it should
title: "[bug] "
labels: bug
---

## What happened

<!-- A clear, single-sentence description of the bug. -->

## Reproduction

```bash
openkite ask "..."
```

## Expected vs actual

- **Expected**: …
- **Actual**: …

## Logs / output

<!-- Paste the full output, including any tool calls (`→ tool_name(...)`) and
the final agent reply. Wrap long blocks in <details>. -->

```text

```

## Environment

- OpenKite version (`openkite --version` or commit SHA):
- Python version (`python --version`):
- `boto3` version (`pip show boto3 | grep Version`):
- OS:
- AWS region:
- Model used (`echo $OPENKITE_MODEL`, default is Haiku 4.5):

## Anything else?

<!-- Stack traces, AWS account quirks, custom env vars, etc. -->
