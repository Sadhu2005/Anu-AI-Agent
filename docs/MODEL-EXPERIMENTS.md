# Model experiments (Windows PC lab)

Hardware target: Ryzen 5 3600, 16 GB RAM, GTX 1650 4 GB.

| Experiment | Measure | Status |
|------------|---------|--------|
| Qwen2.5-Coder 7B | coding correctness, latency, tool-call reliability | template |
| Qwen3 4B | speed, simple-task success, memory | template |
| LM Studio endpoint | OpenAI-compat with ANU gateway | see MODEL-GATEWAY.md |
| Groq / Gemini / OpenRouter | failover, quota, quality | mock CI + live optional |
| Mixed local/cloud | completion rate, cost | log under `.anu/experiments/` |
| Long autonomous run | tasks without intervention, false completion | pack v0.9.15 |

CI never requires paid API or local GPU — use mocks (`tests/test_llm_failover_mocks_v0_9.py`).

## Metrics template

```json
{
  "model_id": "",
  "quantization": "",
  "hardware": "Ryzen5-3600/16G/GTX1650-4G",
  "context_limit": null,
  "elapsed_s": null,
  "tool_call_errors": 0,
  "tests": {"pass": 0, "fail": 0, "skip": 0},
  "tokens": null,
  "cost_usd": null
}
```
