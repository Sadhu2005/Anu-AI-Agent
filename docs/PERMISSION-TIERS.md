# Tool permission tiers

| Tier | Examples | Gate |
|------|----------|------|
| Autocomplete | research (allow-listed), sandboxed test, commit `agent/*`, push `agent/*` | auto when autonomous |
| Approval | major mutation (policy), gated PR create | human Approve |
| Deny | deploy, secrets, force-push, default-branch push, delete, new_repo | never auto; pause |

See `config/anu-autonomy.yaml` policy classes and `anu-command-gateway.yaml` allow-list.
