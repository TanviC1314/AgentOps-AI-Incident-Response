"""Recovery engine: maps validated, predefined actions onto namespace-restricted cluster operations."""
from app.integrations.kubernetes import ClusterClient
from app.models.enums import ActionType


class RecoveryEngine:
    def __init__(self, cluster: ClusterClient):
        self.cluster = cluster

    def execute(self, action_type: str, params: dict) -> dict:
        action = ActionType(action_type)
        if action == ActionType.RESTART_PODS:
            restarted = self.cluster.restart_pods(params.get("scope", "unhealthy"))
            return {"restarted_pods": restarted}
        if action == ActionType.ROLLBACK_DEPLOYMENT:
            dep = self.cluster.rollback(int(params["target_revision"]))
            return {"rolled_back_to_revision": params["target_revision"], "deployment": dep}
        if action == ActionType.SCALE_DEPLOYMENT:
            dep = self.cluster.scale(int(params["replicas"]))
            return {"scaled_to": params["replicas"], "deployment": dep}
        raise ValueError(f"action '{action_type}' is not executable")
