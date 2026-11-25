from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.queue import get_queue
from app.db import get_session
from app.models.enums import IncidentStatus, Severity
from app.repositories.incidents import AuditRepository, IncidentRepository
from app.schemas.alerts import AlertOut
from app.schemas.common import Page
from app.schemas.incidents import AuditLogOut, IncidentDetail, IncidentEventOut, IncidentOut
from app.services.alerts import enqueue_investigation
from app.services.audit import record_audit
from app.services.incidents import get_incident_or_404

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=Page[IncidentOut])
def list_incidents(
    status: IncidentStatus | None = None,
    severity: Severity | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    items, total = IncidentRepository(db).list_page(status.value if status else None, severity.value if severity else None, page, page_size)
    return Page[IncidentOut](items=[IncidentOut.model_validate(i) for i in items], total=total, page=page, page_size=page_size)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: str, db: Session = Depends(get_session)):
    incident = get_incident_or_404(db, incident_id)
    repo = IncidentRepository(db)
    return IncidentDetail(
        **IncidentOut.model_validate(incident).model_dump(),
        alerts=[AlertOut.model_validate(a) for a in repo.alerts_for(incident_id)],
        timeline=[IncidentEventOut.model_validate(e) for e in repo.timeline(incident_id)],
    )


@router.get("/incidents/{incident_id}/timeline", response_model=list[IncidentEventOut])
def get_timeline(incident_id: str, db: Session = Depends(get_session)):
    get_incident_or_404(db, incident_id)
    return IncidentRepository(db).timeline(incident_id)


@router.post("/incidents/{incident_id}/investigate", status_code=202)
def reinvestigate(incident_id: str, db: Session = Depends(get_session)):
    """Queue an investigation for an open or failed incident (manual retry)."""
    incident = get_incident_or_404(db, incident_id)
    if incident.status not in (IncidentStatus.OPEN.value, IncidentStatus.FAILED.value):
        raise ConflictError(f"Incident is '{incident.status}', investigation can only start from open/failed")
    queued = get_queue().set_once(f"manual-investigate:{incident_id}", 10)
    if queued:
        get_queue().enqueue({"type": "investigate", "incident_id": incident_id})
        record_audit(db, "user", "investigation.requested", incident_id)
        db.commit()
    return {"queued": queued, "incident_id": incident_id}


@router.get("/alerts", response_model=Page[AlertOut], tags=["alerts"])
def list_alerts(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_session)):
    from sqlalchemy import func, select

    from app.models import Alert

    total = db.scalar(select(func.count()).select_from(Alert)) or 0
    items = db.scalars(select(Alert).order_by(Alert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[AlertOut](items=[AlertOut.model_validate(a) for a in items], total=total, page=page, page_size=page_size)


@router.get("/audit-logs", response_model=Page[AuditLogOut], tags=["audit"])
def list_audit_logs(
    incident_id: str | None = None,
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
):
    items, total = AuditRepository(db).list_page(incident_id, action, page, page_size)
    return Page[AuditLogOut](items=[AuditLogOut.model_validate(a) for a in items], total=total, page=page, page_size=page_size)


__all__ = ["router", "enqueue_investigation"]

