from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.tools import TOOLS
from app.core.errors import NotFoundError
from app.db import get_session
from app.models import AgentExecution
from app.schemas.agent import AgentExecutionDetail, AgentExecutionOut, ToolExecutionOut
from app.schemas.common import Page

router = APIRouter(tags=["agent"], prefix="/agent")


@router.get("/tools")
def list_tools():
    """The only tools the agent can call. There is no shell or arbitrary-command tool."""
    return [{"name": n, "description": d} for n, (_, d) in TOOLS.items()]


@router.get("/executions", response_model=Page[AgentExecutionOut])
def list_executions(
    incident_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
):
    stmt, count = select(AgentExecution), select(func.count()).select_from(AgentExecution)
    if incident_id:
        stmt, count = stmt.where(AgentExecution.incident_id == incident_id), count.where(AgentExecution.incident_id == incident_id)
    items = db.scalars(stmt.order_by(AgentExecution.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page[AgentExecutionOut](items=[AgentExecutionOut.model_validate(i) for i in items], total=db.scalar(count) or 0, page=page, page_size=page_size)


@router.get("/executions/{execution_id}", response_model=AgentExecutionDetail)
def get_execution(execution_id: str, db: Session = Depends(get_session)):
    ex = db.get(AgentExecution, execution_id)
    if ex is None:
        raise NotFoundError(f"Agent execution {execution_id} not found")
    return AgentExecutionDetail(
        **AgentExecutionOut.model_validate(ex).model_dump(), state=ex.state,
        tool_executions=[ToolExecutionOut.model_validate(t) for t in ex.tool_executions],
    )
