"""Executes an approved recovery proposal: validate -> snapshot -> act -> wait for rollout -> verify -> resolve/escalate."""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import metrics
from app.core.context import incident_id_var
from app.core.queue import get_queue
from app.core.tracing import get_tracer
from app.db import session_factory
from app.integrations.http import IntegrationError
from app.integrations.registry import get_clients
from app.models import DeploymentSnapshot, Incident, RecoveryAction, RecoveryProposal
from app.models.enums import ActionType, IncidentStatus, ProposalStatus
from app.recovery.engine import RecoveryEngine
from app.recovery.proposals import create_proposal
from app.recovery.validation import validate_action
from app.recovery.verification import default_criteria, verify_recovery
from app.repositories.incidents import IncidentRepository
from app.services import incidents as incident_service
from app.services.audit import record_audit

logger = logging.getLogger(__name__)
MAX_RECOVERY_ATTEMPTS = 2


def _snapshot(db: Session, incident_id: str, reason: str, clients) -> dict | None:
    try:
        dep = clients.cluster.get_deployment()
        pods = clients.cluster.list_pods()
    except IntegrationError as exc:
        logger.warning("snapshot failed", extra={"error": exc.message})
        return None
    db.add(DeploymentSnapshot(
        incident_id=incident_id, reason=reason, version=dep["version"], revision=dep["revision"], image=dep["image"],
        desired_replicas=dep["desired_replicas"], ready_replicas=dep["ready_replicas"],
        data={"deployment": dep, "pods": pods},
    ))
    return {"version": dep["version"], "revision": dep["revision"], "image": dep["image"], "desired_replicas": dep["desired_replicas"],
            "ready_replicas": dep["ready_replicas"], "pods": [{"name": p["name"], "ready": p["ready"], "restarts": p["restarts"]} for p in pods]}


def execute_proposal(proposal_id: str) -> None:
    with session_factory()() as db:
        _execute(db, proposal_id)


def _execute(db: Session, proposal_id: str) -> None:
    settings = get_settings()
    clients = get_clients()
    queue = get_queue()
    proposal = db.get(RecoveryProposal, proposal_id)
    if proposal is None:
        logger.error("proposal not found", extra={"proposal_id": proposal_id})
        return
    incident = db.get(Incident, proposal.incident_id)
    token = incident_id_var.set(proposal.incident_id)
    repo = IncidentRepository(db)
    lock_key = f"recovery:{settings.kubernetes_namespace}/{settings.kubernetes_deployment}"
    locked = False
    action: RecoveryAction | None = None
    started = time.monotonic()
    try:
        if proposal.status != ProposalStatus.APPROVED.value or incident.status != IncidentStatus.RECOVERING.value:
            logger.warning("refusing to execute: proposal/incident not in approved/recovering state",
                           extra={"proposal_status": proposal.status, "incident_status": incident.status})
            return
        # Distributed lock: one recovery per deployment at a time.
        deadline = time.monotonic() + 30
        while not (locked := queue.acquire_lock(lock_key, 900)):
            if time.monotonic() > deadline:
                raise RuntimeError("another recovery is already running on this deployment")
            time.sleep(1)

        proposal.status = ProposalStatus.EXECUTING.value
        action = RecoveryAction(proposal_id=proposal.id, incident_id=incident.id, action_type=proposal.action_type,
                                parameters=proposal.parameters, status="running")
        db.add(action)
        repo.add_event(incident.id, "recovery_started", f"Executing {proposal.action_type}", {"proposal_id": proposal.id})
        record_audit(db, "recovery-engine", "recovery.started", incident.id, {"proposal_id": proposal.id, "action": proposal.action_type})
        db.commit()

        with get_tracer().start_as_current_span("recovery.execute") as span:
            span.set_attribute("recovery.action", proposal.action_type)
            # Re-validate immediately before acting: cluster state may have changed since the proposal.
            params = {k: v for k, v in proposal.parameters.items() if not k.startswith("_")}
            if proposal.action_type == ActionType.ROLLBACK_DEPLOYMENT.value:
                params = {"target_revision": proposal.parameters["target_revision"]}
            check = validate_action(proposal.action_type, params, clients.cluster, settings)
            if not check.ok:
                raise RuntimeError("pre-execution validation failed: " + "; ".join(check.errors))
            action.pre_state = _snapshot(db, incident.id, "pre_recovery", clients)
            repo.add_event(incident.id, "recovery_pre_state", "Recorded pre-recovery state",
                           {"current_version": (action.pre_state or {}).get("version"),
                            "target_version": check.params.get("target_version"), "validation": check.as_dict()["checks"]})
            db.commit()

            result = RecoveryEngine(clients.cluster).execute(proposal.action_type, check.params)
            repo.add_event(incident.id, "recovery_executed", f"{proposal.action_type} applied; waiting for rollout", result)
            rollout = clients.cluster.wait_for_rollout(settings.rollout_timeout_seconds)
            action.post_state = _snapshot(db, incident.id, "post_recovery", clients)
            db.commit()
            if not rollout.get("rollout_complete"):
                raise RuntimeError("rollout did not complete within the timeout")

            expected = check.params.get("target_version") if proposal.action_type == ActionType.ROLLBACK_DEPLOYMENT.value else None
            criteria = {**default_criteria(settings, expected), **(proposal.verification_criteria or {})}
            if expected:
                criteria["expected_version"] = expected
            report = verify_recovery(criteria, settings, clients)

        action.verification = report
        repo.add_event(incident.id, "verification_" + ("passed" if report["passed"] else "failed"), report["summary"],
                       {"checks": report["checks"], "attempts": report["attempts"]})
        _finish(db, incident, proposal, action, report, started)
    except Exception as exc:
        logger.exception("recovery failed")
        db.rollback()
        incident = db.get(Incident, proposal.incident_id)
        proposal = db.get(RecoveryProposal, proposal_id)
        if action is not None:
            action = db.get(RecoveryAction, action.id)
        _abort(db, incident, proposal, action, str(exc), started)
    finally:
        if locked:
            queue.release_lock(lock_key)
        incident_id_var.reset(token)


def _finish(db: Session, incident: Incident, proposal: RecoveryProposal, action: RecoveryAction, report: dict, started: float) -> None:
    repo = IncidentRepository(db)
    action.finished_at = datetime.now(timezone.utc)
    action.duration_ms = round((time.monotonic() - started) * 1000, 1)
    metrics.RECOVERY_DURATION.labels(proposal.action_type).observe(action.duration_ms / 1000)
    if report["passed"]:
        action.status = "succeeded"
        proposal.status = ProposalStatus.EXECUTED.value
        metrics.RECOVERY_ACTIONS.labels(proposal.action_type, "succeeded").inc()
        record_audit(db, "recovery-engine", "recovery.succeeded", incident.id, {"proposal_id": proposal.id, "verification": report["summary"]})
        incident_service.resolve(db, incident, {
            "outcome": "resolved", "action": proposal.action_type, "parameters": proposal.parameters,
            "verification": report["summary"], "pre_state": action.pre_state, "post_state": action.post_state,
            "recovery_duration_seconds": round(action.duration_ms / 1000, 1),
        })
        db.commit()
        return

    action.status = "failed"
    action.error = report["summary"]
    proposal.status = ProposalStatus.FAILED.value
    metrics.RECOVERY_ACTIONS.labels(proposal.action_type, "failed").inc()
    record_audit(db, "recovery-engine", "recovery.failed_verification", incident.id, {"proposal_id": proposal.id, "summary": report["summary"]})
    db.commit()
    _escalate_or_fail(db, incident, proposal, report["summary"])


def _abort(db: Session, incident: Incident, proposal: RecoveryProposal, action: RecoveryAction | None, error: str, started: float) -> None:
    if action is not None:
        action.status = "failed"
        action.error = error
        action.finished_at = datetime.now(timezone.utc)
        action.duration_ms = round((time.monotonic() - started) * 1000, 1)
    proposal.status = ProposalStatus.FAILED.value
    metrics.RECOVERY_ACTIONS.labels(proposal.action_type, "failed").inc()
    IncidentRepository(db).add_event(incident.id, "recovery_error", f"Recovery error: {error}", {"proposal_id": proposal.id})
    record_audit(db, "recovery-engine", "recovery.error", incident.id, {"proposal_id": proposal.id, "error": error})
    db.commit()
    if incident.status == IncidentStatus.RECOVERING.value:
        _escalate_or_fail(db, incident, proposal, error)


def _escalate_or_fail(db: Session, incident: Incident, failed: RecoveryProposal, reason: str) -> None:
    """If a non-rollback recovery failed, propose (never auto-run) a rollback; otherwise stop automation."""
    from sqlalchemy import func, select

    settings = get_settings()
    attempts = db.scalar(select(func.count()).select_from(RecoveryProposal).where(
        RecoveryProposal.incident_id == incident.id, RecoveryProposal.status.in_(["failed", "executed"]))) or 0
    if failed.action_type != ActionType.ROLLBACK_DEPLOYMENT.value and attempts < MAX_RECOVERY_ATTEMPTS:
        probe = validate_action(ActionType.ROLLBACK_DEPLOYMENT.value, {}, get_clients().cluster, settings)
        if probe.ok:
            IncidentRepository(db).add_event(incident.id, "recovery_escalated",
                                             "Recovery did not verify; escalating to a rollback proposal (requires approval)")
            p = create_proposal(
                db, incident, ActionType.ROLLBACK_DEPLOYMENT.value, {"target_revision": probe.params["target_revision"]},
                f"Previous action '{failed.action_type}' failed verification ({reason}). Rolling back to the last known revision.",
                default_criteria(settings, probe.params.get("target_version")), failed.execution_id, actor="recovery-engine",
            )
            if p.status == ProposalStatus.BLOCKED.value:
                incident_service.fail(db, incident, "escalation rollback was blocked by validation")
            db.commit()
            return
    incident_service.fail(db, incident, "automated recovery failed verification; automated actions stopped", {"detail": reason})
    record_audit(db, "recovery-engine", "recovery.automation_stopped", incident.id, {"reason": reason})
    db.commit()
