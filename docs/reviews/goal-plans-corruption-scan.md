# Goal plans freeze/thaw corruption scan (F1)

Tools:

- `tools/scan_goal_plans_corruption.py` — read-only
- `tools/repair_goal_plans_corruption.py` — dry-run / `--apply` supersede

## Local (dev) run

Without a live Brain DB the scanner exits 0 and reports the path missing.
Run on Pi against production:

```bash
sudo -u anu-agent python3 /srv/anu/tools/scan_goal_plans_corruption.py \
  --db /srv/anu/brain/anu-brain.db
```

If corrupt active rows are found (parameters.files / depends_on as `{}`):

```bash
sudo -u anu-agent python3 /srv/anu/tools/repair_goal_plans_corruption.py \
  --db /srv/anu/brain/anu-brain.db --apply
```

Capture stdout JSON into this folder as evidence. No repair applied from
this workstation (no Pi Brain path available in the remediation wave).
