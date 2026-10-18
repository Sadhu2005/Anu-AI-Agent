# Closed action kinds (template planner)

The V0.6.1 template planner is **closed**: only a fixed set of action kinds
and execution operations is allowed. This keeps Goal → Plan → Task Graph
expansion on the existing Safe Mutation surface until V0.6.2+ capabilities
widen the contract deliberately.

Source of truth: `bin/anu-template-planner-v0.1.py`.

## AgentAction.kind

| Kind | Status | Notes |
|---|---|---|
| `TASK` | **Allowed** | Every template step today is a schedulable task node. |
| *(any other)* | **Rejected** | Planner must not emit other kinds until a version doc opens them. |

## Operations (execution spec)

Template steps map to Safe Mutation / gateway operations only:

| Operation | Maps to | In `ALLOWED_OPERATIONS` |
|---|---|---|
| `python_test` | Sandbox `python_test` (unittest discover) | yes |
| `python_compile` | Sandbox `python_compile` | yes |
| *(any other)* | — | **no** |

```python
ALLOWED_OPERATIONS = frozenset({"python_test", "python_compile"})
```

Roles such as `inspect`, `propose`, or `verify` may appear in
`AgentAction.parameters` for future planners; **execution** still uses
`python_test` or `python_compile` only.

## Implications

- No filesystem, git, browser, or research steps in templates until capability
  invoke and policy (V0.6.2+) register them explicitly.
- Adding a new kind or operation requires updating this document, the planner
  allow-list, and a version step with tests.
