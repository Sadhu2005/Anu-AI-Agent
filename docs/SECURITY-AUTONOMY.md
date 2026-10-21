# Security notes — V0.9 Autonomy (+ lab-repo)

## Lab-repo model

- ANU pushes only `agent/*`; never `main`, never force-push, never auto-merge
- Gated PR payloads require human authorization (`create_pr_gated`)
- One writer lock per workspace (`.anu/WRITER.lock`)
- Test matrix must exit 0 before claiming pass; failures write `.anu/DIAGNOSIS.md`

## Auto-approval policy

- Kill switch: `config/anu-autonomy.yaml` → `auto_approve: false`
- Always-deny classes cannot be overridden by LLM output (deploy stays human-pending)
- Policy evaluates capability class independently of planner text (prompt-injection resistant control plane)
- Secret scan on mutation diffs; protected paths (`.github/workflows/`, `deploy/`, `infra/`)
- Rate limit on policy approvals and pushes per hour
- GitHub owner allow-list still enforced (`Sadhu2005`)
- Projects automation needs a separate credential (not default `GITHUB_TOKEN`) if enabled later

## Dashboard

- Binds `127.0.0.1` only — not reachable on the LAN without an SSH tunnel
- Token via `ANU_DASHBOARD_TOKEN` / credential file; constant-time compare
- CSRF: POST requires `X-ANU-Token`
- Systemd unit: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=yes`

## Secrets

- No API keys in git (`anu-llm-providers.yaml` uses env / `*_FILE` only)
- GitHub PAT: `/srv/anu/secrets/github-token` (`root:anu-agent`, mode `640`); wired via `ANU_GITHUB_TOKEN_FILE` in `anu-agent.service.d/lab-github.conf`
- Lab push drop-in sets `PrivateNetwork=no` + `AF_INET/AF_INET6` so `agent/*` HTTPS push works; still no force-push / default-branch push
- Log redaction in observability + dashboard log tail
- Progress file must never contain keys (controller writes checklist text only)

## Prompt injection

- Repo content is **data** in prompts, never instructions to the control plane
- Mutation path validation, approval policy, and git refspec guards do not trust LLM JSON
