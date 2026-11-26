"""Reconcile work that was in flight when the backend stopped. Assumes a single backend instance.

Jobs are removed from the queue when picked up (and the in-memory queue is lost entirely), so without this an incident
could stay `investigating` or `recovering` forever.

* Investigations are read-only: safe to run again.
* An approved action that never started is queued again.
* An action that was already executing is NEVER repeated: its effect on the cluster is unknown, so automation stops.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.queue import ResilientQueue
from app.models import AgentExecution, Incident, RecoveryAction, RecoveryProposal
from app.models.enums import IncidentStatus, ProposalStatus
from app.repositories.incidents import IncidentRepository
from app.services import incidents as incident_service
from app.services.audit import record_audit

logger = logging.getLogger(__name__)
REASON = "backend restarted while work was in progress"


def recover_interrupted_work(db: Session, queue: ResilientQueue) -> dict[str, int]:
    settings = get_settings()
    counts = {"investigations_requeued": 0, "executions_requeued": 0, "recoveries_failed": 0}
    jobs: list[dict] = []
    now = datetime.now(timezone.utc)
    active = (IncidentStatus.OPEN.value, IncidentStatus.INVESTIGATING.value, IncidentStatus.RECOVERING.value)

    for incident in db.scalars(select(Incident).where(Incident.status.in_(active))).all():
        if incident.status == IncidentStatus.INVESTIGATING.value:
            queue.release_lock(f"investigation:{incident.id}")  # held by the dead process
            for ex in db.scalars(select(AgentExecution).where(AgentExecution.incident_id == incident.id, AgentExecution.status == "running")):
                ex.status, ex.error, ex.finished_at = "failed", REASON, now
            incident_service.transition(db, incident, IncidentStatus.OPEN, "system", f"Investigation interrupted: {REASON}; queued again")
            jobs.append({"type": "investigate", "incident_id": incident.id})
            counts["investigations_requeued"] += 1

        elif incident.status == IncidentStatus.OPEN.value:
            # Only incidents never investigated: their job was lost. Others are open for manual handling.
            if db.scalars(select(AgentExecution.id).where(AgentExecution.incident_id == incident.id)).first() is None:
                jobs.append({"type": "investigate", "incident_id": incident.id})
                counts["investigations_requeued"] += 1

        else:  # recovering
            proposals = db.scalars(select(RecoveryProposal).where(
                RecoveryProposal.incident_id == incident.id,
                RecoveryProposal.status.in_([ProposalStatus.APPROVED.value, ProposalStatus.EXECUTING.value]),
            ).order_by(RecoveryProposal.created_at.desc())).all()
            if proposals and proposals[0].status == ProposalStatus.APPROVED.value:
                # The executor re-checks proposal status and re-validates, so a duplicate job is harmless.
                jobs.append({"type": "execute_proposal", "proposal_id": proposals[0].id})
                counts["executions_requeued"] += 1
                continue
            queue.release_lock(f"recovery:{settings.kubernetes_namespace}/{settings.kubernetes_deployment}")
            for proposal in proposals:
                proposal.status = ProposalStatus.FAILED.value
            for action in db.scalars(select(RecoveryAction).where(RecoveryAction.incident_id == incident.id, RecoveryAction.status == "running")):
                action.status, action.error, action.finished_at = "failed", REASON, now
            IncidentRepository(db).add_event(incident.id, "recovery_error", f"Recovery interrupted: {REASON}. Check the deployment manually.")
            record_audit(db, "system", "recovery.interrupted", incident.id, {"reason": REASON})
            incident_service.fail(db, incident, "recovery interrupted; cluster state unknown, automated actions stopped")
            counts["recoveries_failed"] += 1

    db.commit()  # state is durable before any worker can pick up a job
    for job in jobs:
        queue.enqueue(job)
    if any(counts.values()):
        logger.warning("reconciled interrupted work", extra=counts)
    return counts
