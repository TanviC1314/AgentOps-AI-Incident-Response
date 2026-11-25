from datetime import datetime

from app.schemas.common import ORMModel


class ToolExecutionOut(ORMModel):
    id: str
    execution_id: str
    incident_id: str
    tool_name: str
    input: dict | None
    output: dict | None
    status: str
    error: str | None
    duration_ms: float
    created_at: datetime


class AgentExecutionOut(ORMModel):
    id: str
    incident_id: str
    mode: str
    status: str
    current_node: str | None
    diagnosis: dict | None
    error: str | None
    fallback_reason: str | None
    started_at: datetime
    finished_at: datetime | None


class AgentExecutionDetail(AgentExecutionOut):
    state: dict | None
    tool_executions: list[ToolExecutionOut]
