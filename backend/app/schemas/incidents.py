from datetime import datetime

from app.models.enums import IncidentStatus, Severity  # noqa: F401
from app.schemas.alerts import AlertOut
from app.schemas.common import ORMModel


class IncidentOut(ORMModel):
    id: str
    title: str
    service: str
    severity: str
    status: str
    summary: str
    resolution: dict | None
    created_at: datetime
    updated_at: datetime
    investigation_started_at: datetime | None
    investigation_finished_at: datetime | None
    recovery_started_at: datetime | None
    resolved_at: datetime | None


class IncidentEventOut(ORMModel):
    id: str
    incident_id: str
    type: str
    message: str
    data: dict | None
    created_at: datetime


class IncidentDetail(IncidentOut):
    alerts: list[AlertOut] = []
    timeline: list[IncidentEventOut] = []


class AuditLogOut(ORMModel):
    id: str
    incident_id: str | None
    actor: str
    action: str
    details: dict | None
    request_id: str | None
    created_at: datetime
