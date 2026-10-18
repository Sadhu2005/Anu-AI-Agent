# ANU Model Strategy

> **Intelligence is a replaceable capability of ANU, not the security boundary
> of ANU.**

This document defines how model intelligence plugs into ANU: the initial model
strategy, cost and memory limits, privacy requirements, and the architecture
for both API and local models.

It matters most for **V0.6.1**, where the agent must be able to request
intelligence without giving the intelligence layer unrestricted access to the
Raspberry Pi.

| | |
|---|---|
| **Status** | ADR-011 accepted; gateway implementing in [V0.7.1](versions/v0.7.1.md) ([MODEL-GATEWAY.md](MODEL-GATEWAY.md)) |
| **Applies from** | V0.7.1 (planned since V0.6.1); template planner remains fallback |
| **Prices checked** | 5 Oct 2026 (see References) |

---

## 1. Objective

ANU needs intelligence for planning, reasoning, task decomposition, research
synthesis, decision support, code understanding, error interpretation, and
goal planning.

The model is an **intelligence provider behind a controlled interface**. It
must be replaceable without rewriting the scheduler, agent controller, task
engine, Safe Mutation, resource governance, sandbox, or brain.

```text
ANU Core → Model Interface → Model Provider (API model or local model)
```

---

## 2. Primary Decision

**V0.6.1 uses an API-first model strategy.**

The initial reference model is **OpenAI GPT-6 Luna** (`gpt-6-luna`), chosen
because it is far cheaper than the flagship models while remaining suitable
for planning, reasoning, and agent-support work.

The model identifier is configuration, never architecture:

```text
MODEL_PROVIDER=openai
MODEL_NAME=gpt-6-luna
```

ANU must never embed a model name throughout the application.

---

## 3. Why API First

The Raspberry Pi is the execution and persistence host, not a
high-performance inference machine: 8 GB RAM, 4 CPU cores, resource
governance, sandboxed execution.

The **whole** ANU agent budget is 3 GB of RAM: `anu_budget.maximum_ram_mb:
3072` in policy, enforced by the kernel as `MemoryMax=3072M` on
`anu-agent.slice`. That budget also has to cover the Python runtime, ANU
processes, SQLite, and the scheduler. A capable local model cannot fit inside
it next to the runtime. On top of that, the host needs the sandbox, the
filesystem cache, the context/KV cache, and other system services.

> `models.normal_max_gb: 3` in `anu-admission-policy.yaml` is a **model file
> size** limit (Governor `model` operation), not a RAM reservation.

API-first gives a clean initial split:

```text
Raspberry Pi                                   External
 ├── ANU runtime, scheduler, brain, sandbox
 └── model gateway  ───────── HTTPS ─────────►  API model
```

The Pi keeps state, orchestration, execution, verification, recovery, and
security. The external model supplies **reasoning capability** only.

---

## 4. The Agent Must Not Call the Internet

The production agent is network-isolated (`PrivateNetwork=yes`,
`RestrictAddressFamilies=AF_UNIX`). It must **not** receive Internet access
just to talk to a model API.

V0.6.1 introduces a separate **model gateway** boundary:

```text
ANU AGENT ──AF_UNIX──► ANU MODEL GATEWAY ──HTTPS──► MODEL PROVIDER
(no network)           (narrow network capability)
```

The gateway is the only component with outbound network access, and only to
the model provider.

---

## 5. Model Gateway Responsibilities

Provider authentication, model selection, request formatting, token and cost
accounting, timeouts, bounded retries, request size limits, response
validation, privacy filtering, rate limiting, and provider error handling.

The gateway must **not** become a general-purpose service. It must never
offer ANU arbitrary HTTP requests, arbitrary URLs, shell execution, Docker
access, or filesystem administration.

> Its only purpose: **convert an approved ANU intelligence request into a
> controlled model request.**

### 5.1 How the gateway is deployed on the Pi

The gateway follows the pattern already proven by `anu-sandboxd`:

| Concern | Design |
|---|---|
| Process | Separate `anu-model-gateway.service`, its own system user (not `anu-agent`, not root) |
| Agent interface | Unix socket, e.g. `/run/anu-model/model.sock`, group `anu-agent`, mode `0660`; caller checked with `SO_PEERCRED` |
| Network | `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, no `PrivateNetwork`; everything else hardened like `anu-agent.service` |
| API key | Delivered only to the gateway with systemd `LoadCredential=` from a root-owned file outside `/srv/anu`. **Never** in `/srv/anu/secrets`, which the agent can read and write ([SECURITY.md](SECURITY.md), gap G1) |
| Budget ledger | Kept in the gateway's own `StateDirectory`, **not** in the brain. The agent can write the brain, so it must not be able to reset its own spending |
| Usage metadata | Copied to the brain for observability (section 28); the gateway's ledger stays authoritative |

---

## 6. Model Interface

```text
Agent → request_reasoning(...) → Model Interface → Model Gateway → Provider Adapter → Model
```

Provider-neutral request fields: model class, task type, input context,
maximum output, reasoning level, timeout, privacy class, cost budget.

No provider-specific detail reaches the scheduler or controller.

---

## 7. Model Classes

| Class | Use | Current reference |
|---|---|---|
| **Primary** | Normal planning and reasoning | GPT-6 Luna |
| **Strong** | Only when a task genuinely needs more reasoning **and** cost policy permits | to be chosen (e.g. GPT-6.1 Sol) |
| **Local** | Privacy requires local processing, no network, API cost undesirable, offline operation | future small quantized model |

ANU never uses the most expensive model just because it is available. The
local model is an optional backend, not the foundation of ANU's security.

---

## 8. Cost Strategy

Model use is a resource, and every request has a budget. Track input tokens,
output tokens, estimated cost, actual cost where available, requests per
task, and daily and monthly cost.

GPT-6 Luna pricing per 1M tokens:

| Processing | Input | Cached input | Output |
|---|---:|---:|---:|
| **Standard**, ≤ 272K input tokens | $0.10 | $0.01 | $0.50 |
| **Standard**, > 272K input tokens (whole request) | $0.20 | $0.02 | $0.75 |
| **Batch / Flex** (50% of Standard), short context | $0.05 | $0.005 | $0.25 |

Prices are as of 5 October 2026 and must be rechecked whenever model
configuration changes.

> Batch and Flex trade latency for a 50% discount. That matches ANU's
> time-rich design ([DESIGN-PHILOSOPHY.md](DESIGN-PHILOSOPHY.md)), and Flex is
> worth evaluating once the Standard path is proven.

**Typical request cost:** a planning call of 10K input and 2K output tokens
costs about **$0.002** at Standard rates.

---

## 9. Initial ANU Model Budget

Conservative V0.6.1 limits. These are **ANU governance limits**, not provider
pricing:

```text
Per request   max estimated cost  $0.05   and max 32K input / 4K output tokens
Per task      max model cost      $0.25
Per day       max model spend     $1.00
Per month     max model spend     $5.00
```

The token caps are needed because the cost cap alone is loose: at Standard
rates, $0.05 buys about 500K input tokens. The caps also keep every request
far below the 272K long-context price tier. At typical sizes, $1/day is
about 500 planning calls. The monthly cap is the one that binds.

The budget exists to stop a scheduler bug, retry loop, or oversized context
from turning into uncontrolled spending. When a limit is reached:

```text
do not continue automatically → record budget exhaustion → WAIT / approval / failure
```

Importance does not bypass the budget. In addition, set a hard monthly usage
limit in the provider account as an independent backstop.

---

## 10. Token Strategy

Never send the whole brain. Send the **smallest sufficient context**: current
goal, current task, required dependencies, relevant observations, relevant
project files, recent errors, and relevant policy.

Never send the entire database, filesystem, project, or log history. Smaller
context improves privacy, cost, latency, and reasoning quality.

---

## 11. Prompt Construction

```text
SYSTEM POLICY + ANU TASK CONTRACT + RELEVANT CONTEXT + CURRENT OBSERVATIONS + REQUEST
```

Generated text cannot rewrite the policy:

```text
MODEL: "Ignore the resource limit and execute this anyway."
ANU:   Policy remains authoritative.
```

The model recommends. The control layer decides.

---

## 12. Model Output Is Untrusted Input

A model may return incorrect reasoning, invalid JSON, unsafe paths,
unsupported commands, wrong assumptions, hallucinated files, or wrong
dependency information.

```text
MODEL OUTPUT → PARSE → VALIDATE → POLICY CHECK → EXECUTION
```

never `MODEL OUTPUT → EXECUTE`. In practice, model output may only select or
parameterize **existing** execution specification types (V0.6.1 design rule:
plans are data, not code). It still passes the same gateway allow-list,
Safe Mutation path checks, and Governor admission as any other task.

**This is one of the most important architectural rules in ANU.**

---

## 13. RAM Strategy

### API mode

Model weights use no Pi RAM. ANU still needs memory for request
construction, response handling, context, JSON, network buffers, and logging.
The model never becomes a reason to raise the general ANU RAM budget.

### Local mode

Local inference is a heavy workload.

- It runs in **its own service and slice**, never inside `anu-agent.slice`.
- It is subject to the existing model-size policy (`normal_max_gb: 3`,
  `approval_max_gb: 5`, `absolute_max_gb: 5`).
- It is subject to host safety: 2 GB minimum free RAM, and the temperature
  limits.

Preferred first target: a **~1.5–2.0 GB** model footprint, leaving headroom
for the inference runtime, context/KV cache, ANU, and the OS. A model that
"fits in 3 GB" but leaves too little headroom is rejected.

**File size is not a memory calculation. Measure actual runtime RSS.**

---

## 14. Local Model Strategy

A local model comes only after the API interface is stable:

```text
V0.6.1                         V0.8+
API → Model Interface          Model Interface ─┬─ API Provider
                                                └─ Local Provider
```

The provider is chosen by configuration. The initial candidate class is a
**~3B–4B quantized model**, subject to real Pi measurements. No specific local
model enters the ANU core until it passes:

```text
memory · latency · quality · failure · resource-governance · privacy tests
```

---

## 15. Privacy Strategy

> **Do not send data outside the Raspberry Pi unless the request explicitly
> permits external processing.**

| Class | External model | Examples |
|---|---|---|
| `PUBLIC` | Permitted | public docs, public source code (including the ANU repository), public research, public requirements |
| `INTERNAL` | Permitted when appropriate | operational state, task history |
| `PRIVATE` | Approval or local processing | private user data, private project code |
| `SECRET` | **Never** | API keys, passwords, private keys, session credentials, database passwords |

The gateway enforces the class. If a request has no class, it is treated as
`PRIVATE` (fail closed).

---

## 16. Secret Redaction

```text
ANU context → secret detection → redaction → model request
```

The model receives `DATABASE_PASSWORD=[REDACTED]`, never the value. Redaction
runs in the gateway for every request, whatever the declared class.

The gateway never logs unredacted requests, not even for debugging.

---

## 17. API Provider Privacy

OpenAI's platform documentation states:

- API inputs and outputs are **not used to train** OpenAI models unless the
  organization explicitly opts in.
- **Abuse-monitoring logs** may hold prompts and responses for **up to 30
  days** by default. Longer retention is possible where the law requires it
  or harm must be prevented.
- **Zero Data Retention** (ZDR) needs prior approval and covers only eligible
  endpoints (e.g. `/v1/responses`, `/v1/chat/completions`). Stateful
  endpoints (assistants, threads, conversations, files, vector stores) are
  not eligible.

Therefore:

```text
"not used for training"  ≠  "never leaves the Raspberry Pi"
```

For sensitive workloads: prefer a local model, or an API project with
stronger retention controls. ANU should use a stateless endpoint (`store:
false`), so the provider holds no application state between calls.

---

## 18. No Secrets in the Model Prompt

Never include API keys, SSH keys, database passwords, session tokens, cloud
credentials, or system credentials in a prompt.

```text
BAD:  "Here is the database password. Use it."
GOOD: "Database access is available through the approved database capability."
```

The model learns that a capability exists, not the credential behind it.

---

## 19. Model Access to Files

```text
Goal → relevant project → relevant files → model context
```

never `Model → /srv/anu`. The least-privilege principle used for execution
applies to intelligence too. The gateway has no read access to the
workspace; the agent decides which content to send.

---

## 20. Model Access to the Brain

The model never queries `anu-brain.db` directly. ANU builds structured
context (goal, task, dependencies, recent observations, relevant errors,
previous decision), so that intelligence never becomes an alternative
database-administration interface.

---

## 21. Model Availability Failure

The model is an **optional dependency**. Network failure, API outage, rate
limits, authentication errors, budget exhaustion, timeouts, and invalid
responses must never corrupt state:

```text
model unavailable → record error → preserve task state → WAIT / retry safely → resume when available
```

This uses the WAITING lifecycle that exists since V0.5.7. A temporary
intelligence outage must not destroy task state.

---

## 22. Retry Policy

Model requests use **bounded** retries, never infinite retries or infinite
spending. Each retry is governed by the task retry policy (`MAX_ATTEMPTS=3`),
the model budget, resource policy, and the provider error class. Some errors
are not retryable: authentication failures and budget exhaustion WAIT or
fail. Every retry is recorded.

---

## 23. Model Selection Strategy

```text
simple task             → cheap model
normal planning         → primary model
complex reasoning       → strong model, subject to budget
sensitive / offline     → local model
```

Model selection is itself a resource-governed decision.

---

## 24. Example Routing

```text
TASK
 ├── simple classification   → low-cost model
 ├── normal planning         → GPT-6 Luna
 ├── complex reasoning       → approved stronger model
 └── sensitive / private     → local model
```

Adaptive intelligence, without uncontrolled intelligence.

---

## 25. V0.6.1 Model Contract

**Input** (ANU core → gateway): task ID, goal ID, task type, sanitized
context, resource budget, privacy class, requested reasoning level.

**Gateway:** validate request → check policy → check budget → sanitize →
select provider/model → call → validate response → record usage → return a
structured result.

**Output:** a structured response that ANU validates (a JSON schema per task
type).

The model never directly performs filesystem mutation, shell execution,
Docker operations, system administration, or credential access. **It
recommends; ANU decides.**

---

## 26. Recommended V0.6.1 Architecture

```text
                    USER
                      │
                    GOAL
                      │
               ANU AGENT CORE
                      │ AF_UNIX
               ANU MODEL GATEWAY
          (policy · budget · privacy filter)
              ┌───────┴───────┐
         API Adapter     Local Adapter (later)
              │               │
         External API    Local Model
```

The execution path stays separate:

```text
ANU AGENT CORE ─┬─► TASK ENGINE → SAFE MUTATION → SANDBOX
                └─► MODEL GATEWAY
```

The model cannot jump around the execution controls.

---

## 27. Resource Governance

The model layer respects RAM, CPU, parallel-task, cost, timeout, context, and
retry limits. `pause`, `deny`, and `approval` must always remain possible.
Model calls happen inside a scheduler cycle, so they only run when the
Guardian reports `NORMAL`.

The model layer must never weaken the Resource Guardian.

---

## 28. Observability

Every model request records: `task_id`, `goal_id`, provider, model,
timestamp, input tokens, output tokens, estimated cost, latency,
success/failure, failure reason, privacy class.

Stored metadata contains no secrets and does not reproduce full prompts. This
answers: how much did the model cost, which tasks used it, how often did it
fail, which model was used, and was a task waiting on model availability.
(Schema to be added to [DATABASE.md](DATABASE.md) in V0.6.1.)

---

## 29. Cost Optimization

- Small, relevant context.
- Prompt reuse, and prompt caching where appropriate. GPT-6 Luna cached
  input costs $0.01 per 1M tokens, 10% of the normal input rate.
- Structured, bounded outputs.
- Model routing, and avoiding unnecessary calls.
- Persisting useful observations so they need not be recomputed.

Caching must never violate the privacy class of a request.

---

## 30. What the Model May Decide

**May help decide:** what should happen next, how a goal decomposes, which
information is relevant, what a test failure probably means, which safe
option appears preferable.

**Must never decide:** whether security policy can be bypassed, whether a
credential may be exposed, whether a host restriction can be ignored, whether
an unlimited resource request is acceptable, whether an irreversible action
is safe, whether the agent should grant itself privileges.

Those belong to the control architecture.

---

## 31. When Intelligence Is Wrong

```text
RIGHT:  bad reasoning → validation catches it → task intact → retry / alternative / human review
WRONG:  bad reasoning → host changed → damage
```

Model quality and execution safety are separate concerns.

---

## 32. Evolution Through V1.0

```text
V0.6.1  API-first intelligence boundary (gateway, budget, privacy classes)
V0.7    Stable model interface, governed model use
V0.8    Model routing and richer planning
V0.9    API + local model choice by privacy, capability, availability, cost
V1.0    Provider-independent intelligent agent with governed model selection
```

The interface stays stable while providers change.

---

## 33. Model Independence

```text
ANU → Model Interface → Provider Adapter → Model
```

Providers may be OpenAI, other API providers, self-hosted models, local
quantized models, or models that do not exist yet. The provider may change;
the ANU control architecture does not.

---

## 34. Security Rule

> **The model may influence ANU's decisions, but it does not inherit ANU's
> permissions.**

- A model being intelligent does not make it trusted.
- A model being local does not automatically make it safe.
- A model being remote does not automatically make it unsafe.

Safety comes from policy, least privilege, validation, sandboxing, resource
governance, privacy controls, auditability, and recovery
([SECURITY.md](SECURITY.md)).

---

## 35. Final Strategy

```text
                     ANU
                      │
             provider-neutral API
             ┌────────┴────────┐
          API mode          Local mode
          GPT-6 Luna        future small quantized model
          external          on the Pi, own service and slice
```

| | V0.6.1 default |
|---|---|
| Provider | configurable (reference: OpenAI) |
| Model | configurable (reference: `gpt-6-luna`) |
| Mode | API, through `anu-model-gateway` |
| Budget | $0.05 per request (≤ 32K in / 4K out), $0.25 per task, $1 per day, $5 per month |
| Local model | ≤ 3 GB file (policy), preferred ~1.5–2.0 GB measured footprint |

| Privacy class | Rule |
|---|---|
| `PUBLIC` | API permitted |
| `INTERNAL` | API permitted when appropriate |
| `PRIVATE` | approval or local processing |
| `SECRET` | never sent to an external model |

```text
ANU agent ≠ model

ANU agent + controlled model interface + policy + resource governance + sandbox
  = safe intelligent system
```

---

## 36. North Star

ANU should not ask:

> "Which is the smartest model we can put on the Raspberry Pi?"

but:

> **"What is the smallest, safest and most cost-effective intelligence
> capability that lets ANU make better decisions without weakening its
> control architecture?"**

---

## 37. What Must Change to Enable This

Enabling models is a security-boundary change ([SECURITY.md](SECURITY.md)).
Progress is tracked in [V0.7.1](versions/v0.7.1.md).

1. [x] Accept [ADR-011](DECISIONS.md) for OpenAI-compatible / API mode
       (ADR-004 remains for **on-Pi** local model install until later).
2. [~] Add `anu-model-gateway-v0.1.py` + draft systemd unit; socket peer check
       and full hardening = V0.7.1 step 2.
3. [x] Config: `config/anu-model-gateway.yaml` (`base_url`, `model`, budgets).
4. [~] API key via env / `LoadCredential=` (documented; Pi install in step 2).
5. [ ] Model usage table in the brain / [DATABASE.md](DATABASE.md).
6. [x] Keep template planner when gateway is down (V0.7.0 path unchanged).

**Provider switch:** same gateway binary; change `base_url` + `model` + API key
to move between LM Studio (LAN) and cloud OpenAI-compatible hosts.

---

## References

- OpenAI API pricing: <https://developers.openai.com/api/docs/pricing>
- GPT-6 Luna model page: <https://developers.openai.com/api/docs/models/gpt-6-luna>
- Introducing GPT-6 Sol and Luna: <https://openai.com/index/introducing-gpt-6-sol-and-luna/>
- OpenAI API data controls (training, retention, ZDR eligibility): <https://developers.openai.com/api/docs/guides/your-data>
- Zero Data Retention for frontier models: <https://openai.com/index/offering-zero-data-retention-for-frontier-models/>
