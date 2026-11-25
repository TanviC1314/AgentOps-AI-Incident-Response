"""Recovery proposals: safety validation, approval policy, approve/reject."""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.queue import get_queue
from app.integrations.registry import get_clients
from app.models import ApprovalRecord, Incident, RecoveryProposal
from app.models.enums import ApprovalDecision, IncidentStatus, ProposalStatus
from app.recovery.validation import validate_action
from app.repositories.incidents import IncidentRepository
from app.services.audit import record_audit
from app.services.incidents import transition

logger = logging.getLogger(__name__)


def enqueue_execution(proposal_id: str) -> None:
    q = get_queue()
    if q.set_once(f"execute:{proposal_id}", 3600):
        q.enqueue({"type": "execute_proposal", "proposal_id": proposal_id})


def create_proposal(
    db: Session, incident: Incident, action_type: str, params: dict, rationale: str,
    verification_criteria: dict, execution_id: str | None = None, actor: str = "agent",
) -> RecoveryProposal:
    """Validate (safety checks) and store a proposal; moves the incident to AWAITING_APPROVAL or RECOVERING."""
    settings = get_settings()
    repo = IncidentRepository(db)
    result = validate_action(action_type, params, get_clients().cluster, settings)
    requires_approval = result.risk.value in settings.approval_risk_levels
    proposal = RecoveryProposal(
        incident_id=incident.id, execution_id=execution_id, action_type=action_type, parameters=result.params or params,
        risk=result.risk.value, rationale=rationale, requires_approval=requires_approval,
        validation=result.as_dict(), verification_criteria=verification_criteria,
        status=ProposalStatus.PENDING_APPROVAL.value,
    )
    db.add(proposal)
    db.flush()

    if not result.ok:
        proposal.status = ProposalStatus.BLOCKED.value
        repo.add_event(incident.id, "proposal_blocked", f"Recovery proposal '{action_type}' blocked by safety validation",
                       {"proposal_id": proposal.id, "errors": result.errors})
        record_audit(db, actor, "recovery.proposal_blocked", incident.id, {"proposal_id": proposal.id, "errors": result.errors})
        return proposal

    repo.add_event(incident.id, "proposal_created", f"Recovery proposed: {action_type} (risk {result.risk.value})",
                   {"proposal_id": proposal.id, "action": action_type, "params": proposal.parameters, "risk": result.risk.value,
                    "requires_approval": requires_approval})
    record_audit(db, actor, "recovery.proposed", incident.id, {"proposal_id": proposal.id, "action": action_type, "risk": result.risk.value})

    if requires_approval:
        transition(db, incident, IncidentStatus.AWAITING_APPROVAL, actor, f"Awaiting human approval for {action_type}")
    else:
        proposal.status = ProposalStatus.APPROVED.value
        db.add(ApprovalRecord(proposal_id=proposal.id, incident_id=incident.id, decision="approved", decided_by="policy:auto",
                              reason=f"risk '{result.risk.value}' does not require human approval"))
        repo.add_event(incident.id, "auto_approved", f"Auto-approved by policy (risk {result.risk.value})", {"proposal_id": proposal.id})
        record_audit(db, "policy", "recovery.auto_approved", incident.id, {"proposal_id": proposal.id})
        transition(db, incident, IncidentStatus.RECOVERING, "policy", f"Executing {action_type} (auto-approved)")
    return proposal


def get_proposal_or_404(db: Session, proposal_id: str) -> RecoveryProposal:
    p = db.get(RecoveryProposal, proposal_id)
    if p is None:
        raise NotFoundError(f"Recovery proposal {proposal_id} not found")
    return p


def decide(db: Session, proposal_id: str, decision: ApprovalDecision, decided_by: str, reason: str | None) -> RecoveryProposal:
    proposal = get_proposal_or_404(db, proposal_id)
    if proposal.status != ProposalStatus.PENDING_APPROVAL.value:
        raise ConflictError(f"Proposal is '{proposal.status}', only pending proposals can be approved or rejected")
    incident = db.get(Incident, proposal.incident_id)
    if incident is None or incident.status != IncidentStatus.AWAITING_APPROVAL.value:
        raise ConflictError("Incident is not awaiting approval")
    repo = IncidentRepository(db)
    db.add(ApprovalRecord(proposal_id=proposal.id, incident_id=incident.id, decision=decision.value, decided_by=decided_by, reason=reason))
    actor = f"user:{decided_by}"
    if decision == ApprovalDecision.APPROVED:
        proposal.status = ProposalStatus.APPROVED.value
        repo.add_event(incident.id, "proposal_approved", f"{decided_by} approved {proposal.action_type}",
                       {"proposal_id": proposal.id, "reason": reason})
        record_audit(db, actor, "recovery.approved", incident.id, {"proposal_id": proposal.id, "reason": reason})
        transition(db, incident, IncidentStatus.RECOVERING, actor, f"Executing {proposal.action_type} (approved)")
        db.commit()
        enqueue_execution(proposal.id)
    else:
        proposal.status = ProposalStatus.REJECTED.value
        repo.add_event(incident.id, "proposal_rejected", f"{decided_by} rejected {proposal.action_type}",
                       {"proposal_id": proposal.id, "reason": reason})
        record_audit(db, actor, "recovery.rejected", incident.id, {"proposal_id": proposal.id, "reason": reason})
        transition(db, incident, IncidentStatus.OPEN, actor, "Recovery rejected; incident returned to manual handling")
        db.commit()
    return proposal


def pending_proposals(db: Session) -> list[RecoveryProposal]:
    return list(db.scalars(select(RecoveryProposal).where(RecoveryProposal.status == ProposalStatus.PENDING_APPROVAL.value)
                           .order_by(RecoveryProposal.created_at.desc())))
