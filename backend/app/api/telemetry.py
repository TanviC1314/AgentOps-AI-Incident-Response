from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import DeploymentSnapshot
from app.schemas.common import ORMModel
from app.services import telemetry

router = APIRouter(tags=["telemetry"])


@router.get("/service/health")
def service_health():
    """Live health of the monitored service (direct probe + Prometheus + Kubernetes)."""
    return telemetry.service_health()


@router.get("/service/metrics")
def service_metrics(window: str = Query("1m", pattern=r"^\d{1,3}[smh]$"), version: str | None = Query(None, pattern=r"^[\w.\-]+$")):
    """Metrics from Prometheus. 503 if Prometheus is unreachable (no fabricated values)."""
    return telemetry.service_metrics(window, version)


@router.get("/logs")
def logs(
    minutes: int = Query(10, ge=1, le=240),
    limit: int = Query(100, ge=1, le=500),
    level: list[str] = Query(default=[]),
    service: str | None = Query(None, pattern=r"^[\w.\-]+$"),
):
    """Application logs queried from Loki."""
    return telemetry.recent_logs(minutes, limit, level, service)


@router.get("/kubernetes/pods")
def pods():
    return {"namespace": _ns(), "pods": telemetry.kubernetes_pods()}


@router.get("/kubernetes/deployment")
def deployment():
    return telemetry.deployment_status()


@router.get("/deployments/history")
def history():
    return {"namespace": _ns(), "revisions": telemetry.deployment_history()}


class SnapshotOut(ORMModel):
    id: str
    incident_id: str | None
    reason: str
    version: str | None
    revision: int | None
    image: str | None
    desired_replicas: int | None
    ready_replicas: int | None
    captured_at: object


@router.get("/deployments/snapshots", response_model=list[SnapshotOut])
def snapshots(incident_id: str | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_session)):
    """Deployment state snapshots recorded by AgentOps during investigations and recoveries."""
    stmt = select(DeploymentSnapshot).order_by(DeploymentSnapshot.captured_at.desc()).limit(limit)
    if incident_id:
        stmt = stmt.where(DeploymentSnapshot.incident_id == incident_id)
    return list(db.scalars(stmt))


def _ns() -> str:
    from app.config import get_settings

    return get_settings().kubernetes_namespace
