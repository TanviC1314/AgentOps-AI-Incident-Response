from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
incident_id_var: ContextVar[str | None] = ContextVar("incident_id", default=None)
execution_id_var: ContextVar[str | None] = ContextVar("execution_id", default=None)
