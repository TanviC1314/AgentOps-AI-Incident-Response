# Architecture

Modular monolith. One backend process (FastAPI) with clearly separated modules:

```
backend/app/
  api/          routers (HTTP only)
  schemas/      Pydantic request/response models
  models/       SQLAlchemy models
  repositories/ database access
  services/     incident lifecycle, alerts, approvals, audit
  agent/        LangGraph workflow, tools, deterministic fallback
  recovery/     recovery engine (predefined actions only) + verification
  integrations/ Prometheus, Loki, Kubernetes, demo-service clients
  workers/      Redis-backed background investigation worker
```

`demo-service` is a separate small FastAPI app that simulates the monitored workload.

## Incident state machine

`open -> investigating -> awaiting_approval -> recovering -> resolved | failed`

Transitions are enforced in `services/incident_service.py`. The LLM never sets a state; it
only returns a structured diagnosis and proposal that the workflow code validates.
