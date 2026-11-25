from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import simulation
from app.services.audit import record_audit

router = APIRouter(tags=["simulation"], prefix="/simulations")


@router.get("")
def simulation_status():
    return simulation.status()


@router.post("/reset")
def reset(db: Session = Depends(get_session)):
    result = simulation.reset()
    record_audit(db, "user", "simulation.reset", None, result)
    db.commit()
    return result


@router.post("/{scenario}", status_code=202)
def trigger(scenario: str, db: Session = Depends(get_session)):
    """Trigger a failure scenario. Detection happens via Prometheus + Alertmanager, not here."""
    result = simulation.trigger(scenario)
    record_audit(db, "user", "simulation.triggered", None, {"scenario": scenario})
    db.commit()
    return result
