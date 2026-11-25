"""Runs one investigation for an incident, owning all incident state transitions around the agent."""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agent.graph import build_graph
from app.agent.tools import ToolContext
from app.core import metrics
from app.core.context import execution_id_var, incident_id_var
from app.core.queue import get_queue
from app.core.tracing import get_tracer
from app.db import session_factory
from app.models import AgentExecution, Incident, RecoveryProposal
from app.models.enums import IncidentStatus, ProposalStatus
from app.repositories.incidents import IncidentRepository
from app.services import incidents as incident_service
from app.services.audit import record_audit

logger = logging.getLogger(__name__)


def run_investigation(incident_id: str, llm_transport=None) -> str | None:
    with session_factory()() as db:
        return investigate(db, incident_id, llm_transport)


def investigate(db: Session, incident_id: str, llm_transport=None) -> str | None:
    queue = get_queue()
    lock_key = f"investigation:{incident_id}"
    if not queue.acquire_lock(lock_key, 300):
        logger.info("investigation already running", extra={"incident_id": incident_id})
        return None
    t_inc = incident_id_var.set(incident_id)
    try:
        incident = db.get(Incident, incident_id)
        if incident is None:
            logger.error("incident not found for investigation")
            return None
        if incident.status not in (IncidentStatus.OPEN.value, IncidentStatus.FAILED.value):
            logger.info("skipping investigation; incident is %s", incident.status)
            return None
        repo = IncidentRepository(db)
        incident_service.transition(db, incident, IncidentStatus.INVESTIGATING, "agent", "Investigation started")
        execution = AgentExecution(incident_id=incident.id, mode="deterministic", status="running")
        db.add(execution)
        db.flush()
        t_exec = execution_id_var.set(execution.id)
        record_audit(db, "agent", "investigation.started", incident.id, {"execution_id": execution.id})
        db.commit()

        started = time.monotonic()
        try:
            with get_tracer().start_as_current_span("agent.investigate") as span:
                span.set_attribute("incident.id", incident.id)
                ctx = ToolContext(db=db, incident=incident, execution_id=execution.id)
                final = build_graph(ctx, execution, llm_transport).invoke({"completed_nodes": []})
            execution.status = "completed"
            execution.finished_at = datetime.now(timezone.utc)
            incident.investigation_finished_at = execution.finished_at
            diag = execution.diagnosis or {}
            incident.summary = diag.get("incident_summary") or incident.summary
            proposal = final.get("proposal") or {}
            repo.add_event(incident.id, "investigation_completed",
                           f"Investigation completed ({execution.mode}); recommended action: {diag.get('recommended_action', {}).get('action_type')}",
                           {"execution_id": execution.id, "tool_calls": len(ctx.results)})
            record_audit(db, "agent", "investigation.completed", incident.id, {"execution_id": execution.id, "mode": execution.mode})
            db.commit()
            metrics.AGENT_RUNS.labels(execution.mode, "completed").inc()
            metrics.INVESTIGATION_DURATION.labels(execution.mode).observe(time.monotonic() - started)

            incident = db.get(Incident, incident_id)
            if incident.status == IncidentStatus.INVESTIGATING.value:
                # No proposal, or the proposal was blocked: hand over to a human, never guess.
                p = db.get(RecoveryProposal, proposal["proposal_id"]) if proposal.get("proposal_id") else None
                reason = ("recovery proposal was blocked by safety validation" if p and p.status == ProposalStatus.BLOCKED.value
                          else "no safe automated action proposed")
                incident_service.transition(db, incident, IncidentStatus.OPEN, "agent", f"Investigation finished: {reason}; manual review required")
                db.commit()
            elif incident.status == IncidentStatus.RECOVERING.value and proposal.get("proposal_id") and not proposal.get("requires_approval"):
                from app.recovery.proposals import enqueue_execution

                enqueue_execution(proposal["proposal_id"])
        except Exception as exc:
            logger.exception("investigation failed")
            db.rollback()
            execution = db.get(AgentExecution, execution.id)
            execution.status, execution.error = "failed", f"{type(exc).__name__}: {exc}"
            execution.finished_at = datetime.now(timezone.utc)
            metrics.AGENT_RUNS.labels(execution.mode, "failed").inc()
            incident = db.get(Incident, incident_id)
            if incident.status == IncidentStatus.INVESTIGATING.value:
                incident_service.fail(db, incident, f"investigation error: {type(exc).__name__}")
            db.commit()
        finally:
            execution_id_var.reset(t_exec)
        return execution.id
    finally:
        queue.release_lock(lock_key)
        incident_id_var.reset(t_inc)
