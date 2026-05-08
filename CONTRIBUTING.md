# Contributing to OpenKite

Thanks for your interest. The agent is small on purpose — its power comes from
the toolbox. The fastest way to make OpenKite more useful is to **add a new
tool**.

## Dev setup

```bash
git clone https://github.com/darshil3011/openkite.git
cd openkite
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest -q          # 23 tests, no real AWS or Anthropic calls
ruff check openkite tests
```

## Adding a new tool

The whole architecture is "the LLM has typed tools." If a capability isn't
exposed as a `@tool`, the LLM can't use it. Adding one is a five-minute
exercise.

1. **Pick a service module** in `openkite/tools/` (`ec2.py`, `rds.py`,
   `lambda_.py`, `s3.py`, `cost.py`) or create a new one and register it in
   `openkite/tools/__init__.py`.

2. **Write the function with a `@tool` decorator, typed args, and a
   one-line description.** The LLM picks tools by description, so it should
   read like a quick spec:

   ```python
   from langchain_core.tools import tool
   from openkite.tools._aws import client

   @tool
   def list_iam_users(region: str = "us-east-1") -> list[dict]:
       """List IAM users in the account, with last-used timestamp."""
       iam = client("iam", region)
       return [
           {"username": u["UserName"], "created": u["CreateDate"].isoformat()}
           for u in iam.list_users()["Users"]
       ]
   ```

3. **Append it to the module's `*_TOOLS` list** so `ALL_TOOLS` picks it up.

4. **Write a moto test** in `tests/test_tools.py`. Use `@mock_aws`,
   create the AWS state, call `tool.invoke({...})` (the same code path the
   ReAct agent uses), and assert on the output.

5. **Run `ruff check` and `pytest`.** Both must pass.

### Tool design rules

- **Typed parameters, sensible defaults.** Composite analyzers should accept
  thresholds (`cpu_threshold=5.0`) so the LLM can pass arguments instead of
  inheriting hardcoded behaviour.
- **Resource-targeting parameters first.** If a tool can be narrowed by
  resource ID or tag filter, accept that argument. Avoid the "scan
  everything every time" pattern.
- **Use the helpers in `openkite/tools/_aws.py`** — `client()`,
  `cw_metric_average()`, `cw_metric_sum()`, `paginated()`, `confirm()`.
- **Return JSON-friendly dicts/lists.** No custom classes — the LLM serializes
  the output to a `ToolMessage` and reads it as a string.
- **Write tools must call `confirm()`** before mutating AWS:

  ```python
  @tool
  def stop_ec2(instance_id: str, region: str = "us-east-1") -> dict:
      """Stop one EC2 instance. Pauses for user confirmation."""
      if not confirm(f"Stop EC2 instance {instance_id} in {region}?"):
          return {"instance_id": instance_id, "status": "cancelled_by_user"}
      ec2 = client("ec2", region)
      ec2.stop_instances(InstanceIds=[instance_id])
      return {"instance_id": instance_id, "status": "stopped"}
  ```

## Reporting bugs

Open an issue with:

- The `openkite ask` query you ran.
- The full error or unexpected output.
- The output of `openkite tools` if relevant.
- Your Python and `boto3` versions.

## Pull requests

Small, focused PRs are easier to review and ship faster. One tool per PR is
fine — don't bundle.

- Keep formatting consistent (`ruff check --fix`).
- Don't add real AWS calls or hit the Anthropic API in tests.
- Update `README.md`'s toolbox table if you've added new tools to a category.

## Code of Conduct

Be kind, be specific, assume good faith. That's it.
