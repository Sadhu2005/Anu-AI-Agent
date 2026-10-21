# Retrospective — lab-repo autonomy (post implementation)

## Shipped

- Lab target `anu-agent-test` baseline `a991dab`
- Goal+repo assign, matrix gate, diagnose, post-ACCEPT push, writer lock, gated PR
- Dashboard: matrix, diff/stat, task graph text, gated PR/restore buttons

## Remaining polish (not blockers)

- Full SSE stream (still 3s poll)
- Dedicated login page (token prompt remains)
- Rich visual task-graph / mutation hunk viewer
- Live Pi 5-task run + `v1.0` tag after operator applies `pi-apply-lab-autonomy.sh`
- Optional GitHub Projects sync (needs separate App credential)

## Operator next

1. Apply branch protection on lab `main`
2. Deploy bins via `tools/pi-apply-lab-autonomy.sh`
3. `bash tools/demo-lab-5task.sh` on Pi
4. Tag `v1.0` when live evidence is green
