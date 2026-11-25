"""Incident lifecycle. All status changes go through `transition` (explicit state machine)."""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core import metrics
from app.core.errors import InvalidTransitionError, NotFoundError
from app.models import Incident
from app.models.enums import SEVERITY_RANK, IncidentStatus, Severity
from app.repositories.incidents import IncidentRepository
from app.services.audit import record_audit

logger = logging.getLogger(__name__)

S = IncidentStatus
ALLOWED_TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    S.OPEN: {S.INVESTIGATING},
    S.INVESTIGATING: {S.AWAITING_APPROVAL, S.RECOVERING, S.FAILED, S.OPEN},
    S.AWAITING_APPROVAL: {S.RECOVERING, S.OPEN, S.FAILED},
    S.RECOVERING: {S.RESOLVED, S.FAILED, S.AWAITING_APPROVAL},
    S.RESOLVED: set(),
    S.FAILED: {S.INVESTIGATING},  # manual re-investigation
}


def can_transition(current: IncidentStatus, target: IncidentStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def get_incident_or_404(db: Session, incident_id: str) -> Incident:
    incident = IncidentRepository(db).get(incident_id)
    if incident is None:
        raise NotFoundError(f"Incident {incident_id} not found")
    return incident


def transition(
    db: Session, incident: Incident, target: IncidentStatus, actor: str = "system", message: str | None = None,
    data: dict | None = None,
) -> Incident:
    current = IncidentStatus(incident.status)
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"Cannot move incident from '{current.value}' to '{target.value}'",
            {"from": current.value, "to": target.value, "allowed": sorted(s.value for s in ALLOWED_TRANSITIONS[current])},
        )
    now = datetime.now(timezone.utc)
    incident.status = target.value
    incident.updated_at = now
    if target == S.INVESTIGATING and incident.investigation_started_at is None:
        incident.investigation_started_at = now
    if target == S.RECOVERING and incident.recovery_started_at is None:
        incident.recovery_started_at = now
    if target == S.RESOLVED:
        incident.resolved_at = now
        metrics.INCIDENTS_RESOLVED.inc()
    if target == S.FAILED:
        metrics.INCIDENTS_FAILED.inc()
    repo = IncidentRepository(db)
    repo.add_event(
        incident.id, "status_changed", message or f"Status changed {current.value} -> {target.value}",
        {"from": current.value, "to": target.value, **(data or {})},
    )
    record_audit(db, actor, "incident.status_changed", incident.id, {"from": current.value, "to": target.value})
    logger.info("incident status changed", extra={"incident_id": incident.id, "from": current.value, "to": target.value})
    return incident


def escalate_severity(incident: Incident, severity: Severity) -> None:
    if SEVERITY_RANK[severity] > SEVERITY_RANK[Severity(incident.severity)]:
        incident.severity = severity.value


def resolve(db: Session, incident: Incident, resolution: dict, actor: str = "system") -> Incident:
    incident.resolution = resolution
    transition(db, incident, S.RESOLVED, actor, "Incident resolved: recovery verified")
    return incident


def fail(db: Session, incident: Incident, reason: str, resolution: dict | None = None, actor: str = "system") -> Incident:
    incident.resolution = {"outcome": "failed", "reason": reason, **(resolution or {})}
    transition(db, incident, S.FAILED, actor, f"Incident failed: {reason}")
    return incident
