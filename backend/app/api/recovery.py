from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApprovalRecord, RecoveryAction, RecoveryProposal
from app.models.enums import ApprovalDecision, ProposalStatus
from app.recovery import proposals as svc
from app.schemas.common import Page
from app.schemas.recovery import DecisionIn, ProposalDetail, ProposalOut, RecoveryActionOut

router = APIRouter(tags=["recovery"], prefix="/recovery")


def _detail(db: Session, p: RecoveryProposal) -> ProposalDetail:
    approvals = db.scalars(select(ApprovalRecord).where(ApprovalRecord.proposal_id == p.id).order_by(ApprovalRecord.created_at)).all()
    return ProposalDetail(**ProposalOut.model_validate(p).model_dump(), approvals=list(approvals))


@router.get("/proposals", response_model=Page[ProposalOut])
def list_proposals(
    status: ProposalStatus | None = None,
    incident_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    stmt, count = select(RecoveryProposal), select(func.count()).select_from(RecoveryProposal)
    if status:
        stmt, count = stmt.where(RecoveryProposal.status == status.value), count.where(RecoveryProposal.status == status.value)
    if incident_id:
        stmt, count = stmt.where(RecoveryProposal.incident_id == incident_id), count.where(RecoveryProposal.incident_id == incident_id)
    items = db.scalars(stmt.order_by(RecoveryProposal.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[ProposalOut](items=[ProposalOut.model_validate(i) for i in items], total=db.scalar(count) or 0, page=page, page_size=page_size)


@router.get("/proposals/{proposal_id}", response_model=ProposalDetail)
def get_proposal(proposal_id: str, db: Session = Depends(get_session)):
    return _detail(db, svc.get_proposal_or_404(db, proposal_id))


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalDetail)
def approve(proposal_id: str, body: DecisionIn, db: Session = Depends(get_session)):
    """Approve: records the decision, moves the incident to `recovering` and queues execution."""
    p = svc.decide(db, proposal_id, ApprovalDecision.APPROVED, body.decided_by, body.reason)
    return _detail(db, p)


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalDetail)
def reject(proposal_id: str, body: DecisionIn, db: Session = Depends(get_session)):
    p = svc.decide(db, proposal_id, ApprovalDecision.REJECTED, body.decided_by, body.reason)
    return _detail(db, p)


@router.get("/actions", response_model=Page[RecoveryActionOut])
def list_actions(
    incident_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    stmt, count = select(RecoveryAction), select(func.count()).select_from(RecoveryAction)
    if incident_id:
        stmt, count = stmt.where(RecoveryAction.incident_id == incident_id), count.where(RecoveryAction.incident_id == incident_id)
    items = db.scalars(stmt.order_by(RecoveryAction.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[RecoveryActionOut](items=[RecoveryActionOut.model_validate(i) for i in items], total=db.scalar(count) or 0, page=page, page_size=page_size)
