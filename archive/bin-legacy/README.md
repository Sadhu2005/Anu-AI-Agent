# Legacy task engine scripts

Historical Task Engine versions kept for archaeology. **Production and the
agent runtime use `bin/anu-task-engine-v0.5.3.py` only.**

| File | Notes |
|---|---|
| `anu-task-engine-v0.3.py` | Pre-retry semantics |
| `anu-task-engine-v0.4.py` | Explicit retry rules |
| `anu-task-engine-v0.5.py` | Adds `run_id` |

`bin/anu-task-engine-v0.5.1.py` remains in `bin/` because V0.5.1 regression
tests import it directly (`tests/test_*_v0_5_1.py`, `test_*_v0_5_2.py`).
