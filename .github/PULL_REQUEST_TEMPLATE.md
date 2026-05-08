<!-- Thanks for contributing! Keep PRs small and focused — one tool or one fix
per PR works best. -->

## Summary

<!-- What does this PR change, and why? Two-three sentences. -->

## Type of change

- [ ] New tool (added to `openkite/tools/`)
- [ ] Tool change (signature, behaviour, or moto test)
- [ ] Agent / CLI change
- [ ] Bug fix
- [ ] Docs only
- [ ] Other:

## If this adds or changes a tool

- [ ] Function is `@tool`-decorated with **typed args** and a clear one-line description.
- [ ] Appended to the module's `*_TOOLS` list (so `ALL_TOOLS` picks it up).
- [ ] Resource-targeting parameters are exposed where applicable (no
      forced "scan everything" behaviour).
- [ ] Write tools call `confirm()` before any boto3 mutation.
- [ ] Moto-backed test added in `tests/test_tools.py`.

## Verification

- [ ] `pytest -q` passes.
- [ ] `ruff check openkite tests` passes.
- [ ] If the change is user-visible, `README.md` and/or `CHANGELOG.md` updated.

## Anything reviewers should look at first?
