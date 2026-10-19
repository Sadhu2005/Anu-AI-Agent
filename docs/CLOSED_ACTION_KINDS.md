# Closed action kinds (template planner)

The template planner is **closed**: only a fixed set of action kinds
and execution operations is allowed. This keeps Goal → Plan → Task Graph
expansion on deliberate surfaces.

Source of truth: `bin/anu-template-planner-v0.1.py`.

## AgentAction.kind

| Kind | Status | Notes |
|---|---|---|
| `TASK` | **Allowed** | Every template step today is a schedulable task node. |
| *(any other)* | **Rejected** | Planner must not emit other kinds until a version doc opens them. |

## Operations (execution spec)

| Operation | Maps to | Notes |
|---|---|---|
| `python_test` | Sandbox via capability adapter | Safe Mutation |
| `python_compile` | Sandbox via capability adapter | Safe Mutation |
| `research` | `anu-research` (V0.8.5/7) | Approval-gated HTTPS search/fetch |
| `github` | `anu-github` (V0.8.6/7) | Approval-gated GitHub GET |
| *(any other)* | — | **no** |

```python
ALLOWED_OPERATIONS = frozenset({
    "python_test", "python_compile", "research", "github",
})
```

`multi_stage_goal` research stage uses operation `research` (V0.8.7).
Other stages still use `python_compile` / `python_test`.

## Execution path

```text
python_test / python_compile
  → Safe Mutation → capability adapter → gateway / sandbox

research / github
  → Task Engine capability path → approval gate → registry.invoke
  → anu-research / anu-github → research table
```

## Implications

- Browser / filesystem.write remain hard-disabled.
- Adding a new operation requires updating this document, the planner
  allow-list, Task Engine routing, and a version step with tests.
