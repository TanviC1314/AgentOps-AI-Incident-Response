from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ProposalOut(ORMModel):
    id: str
    incident_id: str
    execution_id: str | None
    action_type: str
    parameters: dict
    risk: str
    rationale: str
    requires_approval: bool
    status: str
    validation: dict | None
    verification_criteria: dict | None
    created_at: datetime
    updated_at: datetime


class ApprovalOut(ORMModel):
    id: str
    proposal_id: str
    incident_id: str
    decision: str
    decided_by: str
    reason: str | None
    created_at: datetime


class ProposalDetail(ProposalOut):
    approvals: list[ApprovalOut] = []


class DecisionIn(BaseModel):
    decided_by: str = Field(min_length=1, max_length=100, pattern=r"^[\w .@\-]+$")
    reason: str | None = Field(default=None, max_length=1000)


class RecoveryActionOut(ORMModel):
    id: str
    proposal_id: str
    incident_id: str
    action_type: str
    parameters: dict
    status: str
    pre_state: dict | None
    post_state: dict | None
    verification: dict | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
