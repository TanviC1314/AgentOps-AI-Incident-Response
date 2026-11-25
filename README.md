# AgentOps — AI Incident Response & Safe Rollback Platform

AgentOps receives **real alerts** from Prometheus/Alertmanager, investigates them with a
LangGraph agent that calls **real tools** (Prometheus, Loki, Kubernetes, PostgreSQL),
proposes a recovery action, asks a human for approval when the risk requires it, executes
only predefined and validated actions, **verifies** the result and records everything in an
audit log.

> Status: Phase 1 in progress. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
> [docs/ROADMAP.md](docs/ROADMAP.md). This README is expanded as chunks land.

## Data sources (nothing is invented by the LLM)

| Source | Provides |
| --- | --- |
| Prometheus | Metrics and alert rules |
| Alertmanager | Delivers alerts to the AgentOps webhook |
| Loki | Structured application logs |
| Kubernetes | Pod / deployment / rollout state (namespace `agentops-demo` only) |
| PostgreSQL | Incidents, alerts, agent runs, tool calls, approvals, audit log |
| Redis | Background investigation queue, locks, idempotency keys |
| LLM | Reasons over collected evidence; never a source of operational facts |

## Workflow

```
Prometheus rule fires -> Alertmanager -> POST /api/v1/alerts/webhook
  -> incident (PostgreSQL) -> queued investigation (Redis)
  -> LangGraph agent collects evidence -> diagnosis -> recovery proposal
  -> safety validation -> approval (if required) -> execute -> verify
  -> rollback on failure -> resolved / failed -> audit log
```
