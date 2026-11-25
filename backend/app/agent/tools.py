"""Agent tools. Every call is timed, traced, persisted (tool_executions) and returns a structured result.

Tool failures never raise into the workflow and never produce invented data: the result carries
ok=False and an error object, and the workflow records a data gap instead.
"""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core import metrics
from app.core.tracing import get_tracer
from app.integrations.http import IntegrationError
from app.models import Incident, ToolExecution
from app.repositories.incidents import IncidentRepository
from app.services import telemetry

logger = logging.getLogger(__name__)


@dataclass
class ToolResult:
    ok: bool
    data: dict | None = None
    error: dict | None = None
    duration_ms: float = 0.0


@dataclass
class ToolContext:
    db: Session
    incident: Incident
    execution_id: str
    results: dict[str, ToolResult] = field(default_factory=dict)


# ---- tool implementations (read-only, except propose_recovery_action which only *stores* a proposal)

def get_service_health(ctx: ToolContext) -> dict:
    return telemetry.service_health()


def get_service_metrics(ctx: ToolContext, window: str = "1m") -> dict:
    return telemetry.service_metrics(window)


def get_recent_logs(ctx: ToolContext, minutes: int = 10, limit: int = 100, levels: list[str] | None = None) -> dict:
    return telemetry.recent_logs(minutes, limit, levels)


def get_kubernetes_pods(ctx: ToolContext) -> dict:
    return {"pods": telemetry.kubernetes_pods()}


def get_deployment_status(ctx: ToolContext) -> dict:
    return telemetry.deployment_status()


def get_deployment_history(ctx: ToolContext) -> dict:
    return {"revisions": telemetry.deployment_history()}


def get_previous_incidents(ctx: ToolContext, limit: int = 5) -> dict:
    prev = IncidentRepository(ctx.db).previous_incidents(ctx.incident.service, ctx.incident.id, limit)
    return {"incidents": [
        {"id": i.id, "title": i.title, "severity": i.severity, "status": i.status, "created_at": i.created_at.isoformat(),
         "resolution": i.resolution} for i in prev
    ]}


def propose_recovery_action(ctx: ToolContext, action_type: str, parameters: dict, rationale: str, verification_criteria: dict) -> dict:
    from app.recovery.proposals import create_proposal

    p = create_proposal(ctx.db, ctx.incident, action_type, parameters, rationale, verification_criteria, ctx.execution_id)
    return {"proposal_id": p.id, "status": p.status, "risk": p.risk, "requires_approval": p.requires_approval,
            "validation": p.validation, "action_type": p.action_type, "parameters": p.parameters}


TOOLS: dict[str, tuple[Callable, str]] = {
    "get_service_health": (get_service_health, "Probe /health and /ready, Prometheus health gauges and pod readiness."),
    "get_service_metrics": (get_service_metrics, "Error ratio, p95 latency, request rate, active requests, CPU/memory from Prometheus."),
    "get_recent_logs": (get_recent_logs, "Query structured application logs from Loki (never fabricated)."),
    "get_kubernetes_pods": (get_kubernetes_pods, "Pod status, readiness, restarts and image version from the demo namespace."),
    "get_deployment_status": (get_deployment_status, "Deployment replicas, current image version and revision."),
    "get_deployment_history": (get_deployment_history, "Rollout history: revisions, versions, timestamps (current and previous version)."),
    "get_previous_incidents": (get_previous_incidents, "Earlier incidents for this service and how they were resolved (PostgreSQL)."),
    "propose_recovery_action": (propose_recovery_action, "Store a recovery proposal (restart_pods | rollback_deployment | scale_deployment). Validated before it can run."),
}


def call_tool(ctx: ToolContext, name: str, **inputs) -> ToolResult:
    fn = TOOLS[name][0]
    start = time.perf_counter()
    status, data, error = "ok", None, None
    with get_tracer().start_as_current_span(f"tool.{name}") as span:
        try:
            data = fn(ctx, **inputs)
        except IntegrationError as exc:
            status, error = "error", exc.as_dict()
        except Exception as exc:  # a tool bug must not abort the investigation
            logger.exception("tool crashed", extra={"tool": name})
            status, error = "error", {"source": "agentops", "code": "tool_exception", "message": f"{type(exc).__name__}: {exc}"}
        span.set_attribute("tool.status", status)
    duration_ms = (time.perf_counter() - start) * 1000
    metrics.TOOL_CALLS.labels(name, status).inc()
    metrics.TOOL_DURATION.labels(name).observe(duration_ms / 1000)
    ctx.db.add(ToolExecution(
        execution_id=ctx.execution_id, incident_id=ctx.incident.id, tool_name=name, input=inputs or None,
        output=_trim(data), status=status, error=(error or {}).get("message"), duration_ms=round(duration_ms, 2),
    ))
    ctx.db.flush()
    logger.info("tool executed", extra={"tool": name, "status": status, "duration_ms": round(duration_ms, 1)})
    result = ToolResult(ok=status == "ok", data=data, error=error, duration_ms=duration_ms)
    ctx.results[name] = result
    return result


def _trim(data: dict | None, max_entries: int = 40) -> dict | None:
    """Keep stored tool output bounded (logs can be large)."""
    if data and isinstance(data.get("entries"), list) and len(data["entries"]) > max_entries:
        data = {**data, "entries": data["entries"][:max_entries], "entries_truncated": True}
    return data
