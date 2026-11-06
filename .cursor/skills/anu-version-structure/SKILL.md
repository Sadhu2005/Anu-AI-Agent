---
name: anu-version-structure
description: >-
  Keeps ANU work on the branch named in the version doc, and runs the
  anu-testing-gate after every code edit and before any commit or push.
  Use when writing or editing bin, config, tests, tools, or deploy files,
  when the user asks to commit or push, when starting a version or feature,
  or when switching branches. Branch names come from docs/versions and
  docs/WORKFLOW.md (vX.Y.Z-integration, or the version doc Branch field
  such as experiment/hermes-worker).
---

# ANU version structure

One version stays on one branch. Code edits are not done until the existing
test skill passes. A commit or push waits for that same pass.

Test steps live in [anu-testing-gate](../anu-testing-gate/SKILL.md). Do not
invent a second test procedure. Branch names live in the version doc, then
in [docs/WORKFLOW.md](../../../docs/WORKFLOW.md) and the working rules in
[docs/README.md](../../../docs/README.md).

## After every code write

When a change touches `bin/`, `config/`, `tests/`, `tools/`, or `deploy/`:

1. Read and follow `anu-testing-gate` for this change (local gate, then Pi
   `/tmp` pack or the pushed commit). Do not `git checkout` `/srv/anu`.
2. The change is done only when the required commands exit `0`.
3. Docs-only edits stay on the skip rule in that skill.

## Before commit or push

Do this only when the user asks to commit or push. Do not commit or push
on your own.

1. Run the local gate from `anu-testing-gate`.
2. A non-zero exit blocks the commit and the push. Fix the failure and
   re-run. Do not use `--no-verify`.
3. Commit only files that belong to the current version. Leave out
   `.env`, secrets, and runtime state (`brain/`, `checkpoints/`, `logs/`,
   `secrets/`).
4. Push only after that local gate is green. Pi verify then follows
   `anu-testing-gate`.

## Branch for a version

```text
Version structure:
- [ ] Read docs/versions/vX.Y.Z.md Branch field
- [ ] git branch --show-current and git status --short
- [ ] Stay, or switch only if the worktree is clean
- [ ] Create the branch only when it does not exist
- [ ] Then implement, and run anu-testing-gate after code writes
```

1. The **Branch** field in `docs/versions/vX.Y.Z.md` is the branch name.
   If that field is missing, use `vX.Y.Z-integration`.
2. A version doc that lists two names (V1.3: `experiment/hermes-worker` or
   `v1.3-integration`) means: stay on the branch that already has the work.
   Do not open a second branch for the same version.
3. If `git branch --show-current` already matches, stay there.
4. If the worktree is dirty, do not switch and do not discard changes.
   Finish or ask the user first.
5. If the named branch exists, `git checkout` that branch in this
   workspace. Never switch versions by checking out `/srv/anu`.
6. If it does not exist, create it from the previous frozen tag named in
   **Depends on**:

```bash
git fetch origin --tags
git checkout -b vX.Y.Z-integration <previous-tag-or-commit>
```

   Do not push the new branch unless the user asks.
7. Do not put the next version's code on the previous version's branch.
   Do not start the next version while the current version doc still has
   open exit criteria, unless the user explicitly says to start it.

## Examples

Writing `bin/anu-local-module-v0.1.py` while V1.3's Branch field is
`experiment/hermes-worker` and that is the current branch: stay, then run
`anu-testing-gate`.

The user asks to implement V1.4 from another branch: `docs/versions/v1.4.md`
says `v1.4-integration` and depends on V1.3. Do not start it while V1.3
exit criteria are open. When they are done and the user says to start,
create `v1.4-integration` from the recorded V1.3 commit. Do not checkout
`/srv/anu`.
