# Claude review triage — 16 Oct 2026

Source: `ANU-Review-and-Forward-Plan.docx` (Sonnet 5.5), audited
`v0.6.1-integration` @ `5dc73c4` (steps 2–3 only).

Current tree (this triage): frozen V0.6.1 + V0.6.2 on
`v0.6.2-integration` (P0, P1, invoke unparked, CI/docs hygiene wave).

| ID | Finding | Status now | Target |
|---|---|---|---|
| F1 | freeze/thaw corrupts `[]` / list-of-pairs | **Done** | P0 (this wave) |
| F2 | no `superseded` task status; replan dual graph | **Done** (`superseded` + replan retire + scheduler skip) | P0 |
| F3 | Safe Mutation `.git/`, `.`, absolute changes | **Done** | P1 V0.6.1b |
| F4 | host git repo-controlled config | **Done** | P1 V0.6.1b |
| F5 | `save_plan` not atomic | **Done** | P0 |
| F6 | mutation events drop `goal_id` | **Done** (portable test) | P1 |
| F7 | FK pragma inconsistent | **Done** (shared `db_connect`; not every module migrated) | P1 shared `db_connect` |
| F8 | audit events dropped silently | **Done** (stderr + counter) | P1 |
| F9 | plan schema / same-goal / task_key | **Partial** (0004 indexes + linear template) | P0 narrow guards |
| F10 | checkpoint fsync | **Deferred** | V0.6.8 |
| F11 | timeout arithmetic | **Deferred** | V0.6.6 |
| F12 | sandboxd robustness | **Done** | P1 V0.6.1b |
| F13 | YAML allow-list unread | **Done** (YAML↔COMMANDS assert + registry invoke) | P2 |
| F14 | watchdog / hot reload | **Deferred** | V0.6.8 |
| F15 | goal transitions / plan events / migrations | **Done** (transitions, PLAN_* events, migrate runner) | P3 |
| F16 | test portability / CI / bin hygiene | **Done** (ANU_BASE, CI, portable suite, archive) | P3 |

## Already mitigated (verify only)

- V0.6.1 steps 4–7 + live evidence + tag `v0.6.1`
- Agent admission (goal-less task no longer kills cycle)
- Capability registry + `evaluate_request` audit (V0.6.2 steps 1–2)
- Capability `invoke` → gateway `run` (V0.6.2 step 3) after P0+P1

## Sequence

1. P0 — F1, F5, F2, narrow F9 — **landed**
2. P1 — V0.6.1b boundary (F3, F4, F6, F7, F8, F12) — **landed**
3. P2 — resume V0.6.2 invoke (F13+) — **in progress**
4. P3 — ANU_BASE, CI, docs, migration runner — **partial (this wave)**
5. Defer F10/F11/F14, model gateway, full G1–G3
