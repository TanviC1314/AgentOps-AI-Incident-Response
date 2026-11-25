from enum import Enum


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    RECOVERING = "recovering"
    RESOLVED = "resolved"
    FAILED = "failed"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, Enum):
    RESTART_PODS = "restart_pods"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    SCALE_DEPLOYMENT = "scale_deployment"
    NONE = "none"


class ProposalStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"  # failed safety validation
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
