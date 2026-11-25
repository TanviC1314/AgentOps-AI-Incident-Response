from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert, AuditLog, Incident, IncidentEvent
from app.models.enums import IncidentStatus

TERMINAL = (IncidentStatus.RESOLVED.value, IncidentStatus.FAILED.value)


class IncidentRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, incident_id: str) -> Incident | None:
        return self.db.get(Incident, incident_id)

    def add(self, incident: Incident) -> Incident:
        self.db.add(incident)
        self.db.flush()
        return incident

    def list_page(self, status: str | None, severity: str | None, page: int, page_size: int):
        stmt = select(Incident)
        count = select(func.count()).select_from(Incident)
        if status:
            stmt, count = stmt.where(Incident.status == status), count.where(Incident.status == status)
        if severity:
            stmt, count = stmt.where(Incident.severity == severity), count.where(Incident.severity == severity)
        total = self.db.scalar(count) or 0
        items = self.db.scalars(
            stmt.order_by(Incident.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return items, total

    def find_active_for_service(self, service: str) -> Incident | None:
        """One active incident per service: later alerts attach to it."""
        return self.db.scalars(
            select(Incident)
            .where(Incident.service == service, Incident.status.not_in(TERMINAL))
            .order_by(Incident.created_at.desc())
        ).first()

    def add_event(self, incident_id: str, type_: str, message: str, data: dict | None = None) -> IncidentEvent:
        ev = IncidentEvent(incident_id=incident_id, type=type_, message=message, data=data)
        self.db.add(ev)
        self.db.flush()
        return ev

    def timeline(self, incident_id: str) -> list[IncidentEvent]:
        return list(
            self.db.scalars(
                select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.created_at)
            )
        )

    def counts_by(self, column) -> dict[str, int]:
        rows = self.db.execute(select(column, func.count()).group_by(column)).all()
        return {r[0]: r[1] for r in rows}

    def alerts_for(self, incident_id: str) -> list[Alert]:
        return list(self.db.scalars(select(Alert).where(Alert.incident_id == incident_id).order_by(Alert.created_at)))

    def previous_incidents(self, service: str, exclude_id: str | None, limit: int = 5) -> list[Incident]:
        stmt = select(Incident).where(Incident.service == service)
        if exclude_id:
            stmt = stmt.where(Incident.id != exclude_id)
        return list(self.db.scalars(stmt.order_by(Incident.created_at.desc()).limit(limit)))


class AuditRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_page(self, incident_id: str | None, action: str | None, page: int, page_size: int):
        stmt, count = select(AuditLog), select(func.count()).select_from(AuditLog)
        if incident_id:
            stmt, count = stmt.where(AuditLog.incident_id == incident_id), count.where(AuditLog.incident_id == incident_id)
        if action:
            stmt, count = stmt.where(AuditLog.action == action), count.where(AuditLog.action == action)
        total = self.db.scalar(count) or 0
        items = self.db.scalars(
            stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return items, total

