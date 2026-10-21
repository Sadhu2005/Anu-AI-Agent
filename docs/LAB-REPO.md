# Lab repo model

| Role | Repo |
|------|------|
| Agent | Anu-AI-Agent (Python / Pi `/srv/anu`) |
| Work target | anu-agent-test (or any allow-listed GitHub repo you assign) |

## Branch strategy

| Branch | Who |
|--------|-----|
| `main` | Stable; humans merge; protected |
| `agent/<goal>-*` | ANU only |
| `feature/*` | Humans |
| `experiment/model-*` | Local model experiments on PC |

Do not create every feature branch on day one. No concurrent writers on one worktree (`.anu/WRITER.lock`).

## ChatGPT ANU-001…012 mapping

| Issue | ANU version |
|-------|-------------|
| ANU-001 Bootstrap | v0.9.11 |
| ANU-002 State machine | already V0.9 + v0.9.12 hooks |
| ANU-003 LM Studio | already V0.9.2 gateway |
| ANU-004 Tools/workspace | already + lock v0.9.15 |
| ANU-005 Continuous | already controller |
| ANU-006 Test-repair | v0.9.13 |
| ANU-007 Recovery | already + pack |
| ANU-008 Failover | already + mocks |
| ANU-009 GitHub sync | v1.0 gated PR (Projects optional later) |
| ANU-010 Dashboard | V0.9.10+ matrix panel |
| ANU-011 Security | v1.0-rc hardening |
| ANU-012 Unattended E2E | v0.9.15 |

Reject Vitest-as-agent-stack; lab uses unittest/pytest. SQLite brain remains task ledger source of truth. GitHub Projects is optional visibility after v0.9.15.
