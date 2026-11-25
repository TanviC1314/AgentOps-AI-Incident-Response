from sqlalchemy.orm import Session

from app.core.context import request_id_var
from app.models import AuditLog


def record_audit(db: Session, actor: str, action: str, incident_id: str | None = None, details: dict | None = None) -> AuditLog:
    entry = AuditLog(
        actor=actor, action=action, incident_id=incident_id, details=details, request_id=request_id_var.get()
    )
    db.add(entry)
    db.flush()
    return entry
