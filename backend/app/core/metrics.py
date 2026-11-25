"""Prometheus metrics exposed by AgentOps itself at /metrics."""
from prometheus_client import Counter, Gauge, Histogram

INCIDENTS_TOTAL = Counter("agentops_incidents_total", "Incidents created", ["severity"])
INCIDENTS_RESOLVED = Counter("agentops_incidents_resolved_total", "Incidents resolved")
INCIDENTS_FAILED = Counter("agentops_incidents_failed_total", "Incidents failed")
INCIDENTS_ACTIVE = Gauge("agentops_incidents_active", "Incidents not resolved/failed")

INVESTIGATION_DURATION = Histogram(
    "agentops_investigation_duration_seconds", "Agent investigation duration", ["mode"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
RECOVERY_DURATION = Histogram(
    "agentops_recovery_duration_seconds", "Recovery action duration (incl. verification)", ["action"],
    buckets=(1, 5, 10, 30, 60, 120, 240, 600),
)
RECOVERY_ACTIONS = Counter("agentops_recovery_actions_total", "Recovery actions", ["action", "result"])

TOOL_CALLS = Counter("agentops_tool_calls_total", "Agent tool calls", ["tool", "status"])
TOOL_DURATION = Histogram(
    "agentops_tool_duration_seconds", "Agent tool call duration", ["tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
AGENT_RUNS = Counter("agentops_agent_runs_total", "Agent runs", ["mode", "status"])
WEBHOOKS = Counter("agentops_alert_webhooks_total", "Alertmanager webhook alerts", ["result"])
SERVICE_ERROR_RATE = Gauge("agentops_service_error_rate", "Monitored service 5xx ratio (from Prometheus)")

HTTP_REQUESTS = Counter("agentops_http_requests_total", "AgentOps API requests", ["method", "path", "status"])
HTTP_DURATION = Histogram("agentops_http_request_duration_seconds", "AgentOps API latency", ["method", "path"])
