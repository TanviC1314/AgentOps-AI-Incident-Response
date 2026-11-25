"""Aggregates for the overview dashboard. All values are computed from persisted workflow data."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AgentExecution, Incident, RecoveryAction, ToolExecution
from app.repositories.incidents import TERMINAL, IncidentRepository

router = APIRouter(tags=["stats"])


def _avg_seconds(pairs) -> float | None:
    vals = [(b - a).total_seconds() for a, b in pairs if a and b]
    return round(sum(vals) / len(vals), 2) if vals else None


@router.get("/stats/overview")
def overview(db: Session = Depends(get_session)):
    repo = IncidentRepository(db)
    by_status = repo.counts_by(Incident.status)
    by_severity = repo.counts_by(Incident.severity)
    total = sum(by_status.values())
    active = sum(v for k, v in by_status.items() if k not in TERMINAL)

    actions = db.scalars(select(RecoveryAction)).all()
    finished = [a for a in actions if a.status in ("succeeded", "failed")]
    success_rate = round(sum(1 for a in finished if a.status == "succeeded") / len(finished), 3) if finished else None

    tool_total = db.scalar(select(func.count()).select_from(ToolExecution)) or 0
    tool_failed = db.scalar(select(func.count()).select_from(ToolExecution).where(ToolExecution.status == "error")) or 0
    runs = db.scalars(select(AgentExecution)).all()
    incidents_done = db.scalars(select(Incident).where(Incident.recovery_started_at.is_not(None), Incident.resolved_at.is_not(None))).all()

    recent = db.scalars(select(AgentExecution).order_by(AgentExecution.started_at.desc()).limit(5)).all()
    return {
        "total_incidents": total,
        "active_incidents": active,
        "resolved_incidents": by_status.get("resolved", 0),
        "failed_incidents": by_status.get("failed", 0),
        "by_status": by_status,
        "by_severity": by_severity,
        "recovery_success_rate": success_rate,
        "avg_investigation_seconds": _avg_seconds((r.started_at, r.finished_at) for r in runs),
        "avg_recovery_seconds": _avg_seconds((a.started_at, a.finished_at) for a in finished),
        "avg_time_to_resolve_seconds": _avg_seconds((i.created_at, i.resolved_at) for i in incidents_done),
        "tool_calls_total": tool_total,
        "tool_failure_rate": round(tool_failed / tool_total, 3) if tool_total else None,
        "agent_runs_total": len(runs),
        "recent_executions": [
            {"id": r.id, "incident_id": r.incident_id, "mode": r.mode, "status": r.status, "started_at": r.started_at}
            for r in recent
        ],
    }
