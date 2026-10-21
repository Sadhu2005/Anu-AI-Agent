# Security notes — V0.9 Autonomy

## Auto-approval policy

- Kill switch: `config/anu-autonomy.yaml` → `auto_approve: false`
- Always-deny classes cannot be overridden by LLM output
- Policy evaluates capability class independently of planner text (prompt-injection resistant control plane)
- Secret scan on mutation diffs; protected paths (`.github/workflows/`, `deploy/`, `infra/`)
- Rate limit on policy approvals per hour
- GitHub owner allow-list still enforced (`Sadhu2005`)

## Dashboard

- Binds `127.0.0.1` only — not reachable on the LAN without an SSH tunnel
- Token via `ANU_DASHBOARD_TOKEN` / credential file; constant-time compare
- CSRF: POST requires `X-ANU-Token`
- Systemd unit: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=yes`

## Secrets

- No API keys in git (`anu-llm-providers.yaml` uses env / `*_FILE` only)
- Log redaction in observability + dashboard log tail
- Progress file must never contain keys (controller writes checklist text only)

## Prompt injection

- Repo content is **data** in prompts, never instructions to the control plane
- Mutation path validation, approval policy, and git refspec guards do not trust LLM JSON
