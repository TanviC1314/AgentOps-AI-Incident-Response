"""Alertmanager webhook ingestion: validate -> store alert -> create/attach incident -> queue investigation."""
import logging
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import metrics
from app.core.queue import ResilientQueue
from app.models import Alert, Incident
from app.models.enums import IncidentStatus, Severity
from app.repositories.incidents import IncidentRepository
from app.schemas.alerts import AlertmanagerAlert, AlertmanagerPayload, WebhookResult
from app.services.audit import record_audit
from app.services.incidents import escalate_severity

logger = logging.getLogger(__name__)


def _severity(labels: dict[str, str]) -> Severity:
    try:
        return Severity((labels.get("severity") or "medium").lower())
    except ValueError:
        return Severity.MEDIUM


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ingest_payload(db: Session, payload: AlertmanagerPayload, queue: ResilientQueue) -> WebhookResult:
    settings = get_settings()
    repo = IncidentRepository(db)
    created: list[str] = []
    attached = updated = ignored = 0

    for raw in payload.alerts:
        fingerprint = raw.fingerprint or f"{raw.labels['alertname']}:{sorted(raw.labels.items())}"
        starts_at = _aware(raw.startsAt)
        existing = db.scalars(
            select(Alert).where(Alert.fingerprint == fingerprint, Alert.starts_at == starts_at)
        ).first()

        if existing is not None:  # idempotent redelivery / resolution update
            if existing.status != raw.status:
                existing.status = raw.status
                existing.ends_at = _aware(raw.endsAt) if raw.endsAt else None
                if existing.incident_id:
                    repo.add_event(
                        existing.incident_id, f"alert_{raw.status}",
                        f"Alert {existing.alert_name} is now {raw.status}",
                        {"alert_id": existing.id, "alert_name": existing.alert_name},
                    )
                record_audit(db, "alertmanager", f"alert.{raw.status}", existing.incident_id, {"alert_id": existing.id})
                updated += 1
            else:
                ignored += 1
            metrics.WEBHOOKS.labels("duplicate" if existing.status == raw.status else "updated").inc()
            continue

        if raw.status == "resolved":  # resolution for an alert we never stored
            ignored += 1
            metrics.WEBHOOKS.labels("ignored").inc()
            continue

        severity = _severity(raw.labels)
        service = raw.labels.get("service") or raw.labels.get("job") or settings.demo_service_name
        alert = Alert(
            fingerprint=fingerprint, alert_name=raw.labels["alertname"], status=raw.status, severity=severity.value,
            service=service, labels=raw.labels, annotations=raw.annotations, starts_at=starts_at,
            ends_at=_aware(raw.endsAt) if raw.endsAt else None, generator_url=raw.generatorURL,
            raw=raw.model_dump(mode="json"),
        )
        incident = repo.find_active_for_service(service)
        summary = raw.annotations.get("summary") or raw.annotations.get("description") or alert.alert_name
        if incident is None:
            incident = repo.add(
                Incident(
                    title=f"{alert.alert_name}: {summary}"[:300], service=service, severity=severity.value,
                    status=IncidentStatus.OPEN.value, summary=raw.annotations.get("description", summary),
                )
            )
            alert.incident_id = incident.id
            db.add(alert)
            repo.add_event(
                incident.id, "incident_created", f"Incident created from alert {alert.alert_name}",
                {"alert_name": alert.alert_name, "severity": severity.value, "source": "prometheus/alertmanager"},
            )
            record_audit(db, "alertmanager", "incident.created", incident.id, {"alert": alert.alert_name})
            metrics.INCIDENTS_TOTAL.labels(severity.value).inc()
            created.append(incident.id)
            db.flush()
            db.commit()  # commit before enqueue so the worker can see the incident
            _enqueue_investigation(queue, incident.id)
        else:
            alert.incident_id = incident.id
            db.add(alert)
            escalate_severity(incident, severity)
            repo.add_event(
                incident.id, "alert_attached", f"Alert {alert.alert_name} attached to active incident",
                {"alert_name": alert.alert_name, "severity": severity.value},
            )
            record_audit(db, "alertmanager", "alert.attached", incident.id, {"alert": alert.alert_name})
            attached += 1
        metrics.WEBHOOKS.labels("created" if incident.id in created else "attached").inc()

    db.commit()
    return WebhookResult(
        received=len(payload.alerts), created_incidents=created, attached_alerts=attached,
        updated_alerts=updated, ignored=ignored,
    )


def _enqueue_investigation(queue: ResilientQueue, incident_id: str) -> bool:
    # Idempotency: never queue the same incident's investigation twice within 60s.
    if not queue.set_once(f"investigate:{incident_id}", 60):
        return False
    queue.enqueue({"type": "investigate", "incident_id": incident_id})
    return True


def enqueue_investigation(queue: ResilientQueue, incident_id: str) -> bool:
    return _enqueue_investigation(queue, incident_id)


__all__ = ["ingest_payload", "enqueue_investigation", "AlertmanagerAlert"]
