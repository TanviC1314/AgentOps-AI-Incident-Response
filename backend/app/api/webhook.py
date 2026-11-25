import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.queue import get_queue
from app.db import get_session
from app.schemas.alerts import AlertmanagerPayload, WebhookResult
from app.services.alerts import ingest_payload

router = APIRouter(tags=["alerts"])
logger = logging.getLogger(__name__)


@router.post("/alerts/webhook", response_model=WebhookResult, status_code=202)
def alertmanager_webhook(payload: AlertmanagerPayload, db: Session = Depends(get_session)):
    """Receives Alertmanager webhook v4 payloads. Invalid payloads are rejected with 422."""
    result = ingest_payload(db, payload, get_queue())
    logger.info("alert webhook processed", extra=result.model_dump())
    return result
