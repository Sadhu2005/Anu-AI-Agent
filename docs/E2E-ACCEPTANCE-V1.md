# V1.0 E2E acceptance checklist

1. [x] Goal → executable task queue (`software_goal` expand)
2. [x] Multi-task without human `next` (controller CONTINUE + pack)
3. [x] Real verification checks (Safe Mutation + matrix)
4. [x] Diagnose + repair path (DIAGNOSIS.md + REPLAN)
5. [x] Retry exhaustion → blocker/PAUSE (fingerprint + budget)
6. [x] Restart recovery (existing V0.9.8 + progress fail-soft)
7. [x] Provider failover mocks (test_llm_failover_mocks_v0_9)
8. [x] Pause / resume (dashboard + controller)
9. [x] Git status consistent with brain (git panel + events)
10. [x] Report passed / failed / skipped (matrix summary)

Freeze tag `v1.0` when Pi live 5-task lab pack is green.
