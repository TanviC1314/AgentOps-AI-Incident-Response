"""Recovery action validation: only predefined actions with validated parameters are ever executable.

The AI agent can *propose* an action; this module decides whether it is permitted. There is no code path
that executes free-form commands.
"""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.integrations.http import IntegrationError
from app.integrations.kubernetes import ClusterClient
from app.models.enums import ActionType, RiskLevel


class RestartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["unhealthy", "all"] = "unhealthy"


class RollbackParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_revision: int | None = Field(default=None, ge=1)
    target_version: str | None = Field(default=None, pattern=r"^[\w.\-]{1,64}$")


class ScaleParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int


PARAM_MODELS = {
    ActionType.RESTART_PODS: RestartParams,
    ActionType.ROLLBACK_DEPLOYMENT: RollbackParams,
    ActionType.SCALE_DEPLOYMENT: ScaleParams,
}


@dataclass
class ValidationResult:
    ok: bool
    params: dict = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    risk: RiskLevel = RiskLevel.HIGH

    @property
    def errors(self) -> list[str]:
        return [c["detail"] for c in self.checks if not c["ok"]]

    def as_dict(self) -> dict:
        return {"ok": self.ok, "params": self.params, "checks": self.checks, "risk": self.risk.value}


def _check(result: ValidationResult, name: str, ok: bool, detail: str) -> bool:
    result.checks.append({"name": name, "ok": ok, "detail": detail})
    return ok


def assess_risk(action: ActionType, params: dict, current_replicas: int | None = None) -> RiskLevel:
    if action == ActionType.ROLLBACK_DEPLOYMENT:
        return RiskLevel.HIGH
    if action == ActionType.RESTART_PODS:
        return RiskLevel.MEDIUM
    if action == ActionType.SCALE_DEPLOYMENT:
        if current_replicas is not None and params.get("replicas", 0) < current_replicas:
            return RiskLevel.MEDIUM  # scaling down reduces capacity
        return RiskLevel.LOW
    return RiskLevel.HIGH


def validate_action(action_type: str, params: dict, cluster: ClusterClient, settings: Settings) -> ValidationResult:
    result = ValidationResult(ok=False)
    try:
        action = ActionType(action_type)
    except ValueError:
        _check(result, "action_permitted", False, f"'{action_type}' is not a permitted recovery action")
        return result
    if action not in PARAM_MODELS:
        _check(result, "action_permitted", False, f"'{action.value}' is not an executable action")
        return result
    _check(result, "action_permitted", True, f"'{action.value}' is in the allow-list")

    try:
        parsed = PARAM_MODELS[action](**(params or {}))
    except ValidationError as exc:
        _check(result, "parameters_valid", False, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()))
        return result
    _check(result, "parameters_valid", True, "parameters match the action schema")
    normalized = parsed.model_dump(exclude_none=True)

    _check(result, "scope_restricted", True, f"namespace={cluster.namespace} deployment={cluster.deployment} (fixed, not parameterisable)")
    try:
        dep = cluster.get_deployment()
    except IntegrationError as exc:
        _check(result, "cluster_reachable", False, f"cannot read deployment state: {exc.message}")
        return result
    _check(result, "cluster_reachable", True, "deployment state read from Kubernetes")
    result.risk = assess_risk(action, normalized, dep["desired_replicas"])
    normalized["_current"] = {"revision": dep["revision"], "version": dep["version"], "replicas": dep["desired_replicas"]}

    if action == ActionType.SCALE_DEPLOYMENT:
        n = parsed.replicas  # type: ignore[attr-defined]
        _check(result, "replicas_in_safe_range", settings.min_replicas <= n <= settings.max_replicas,
               f"replicas={n}, allowed {settings.min_replicas}..{settings.max_replicas}")
        _check(result, "replicas_change", n != dep["desired_replicas"], f"current replicas={dep['desired_replicas']}")
    elif action == ActionType.RESTART_PODS:
        try:
            pods = cluster.list_pods()
        except IntegrationError as exc:
            _check(result, "pods_readable", False, exc.message)
            return result
        targets = [p for p in pods if parsed.scope == "all" or not p["ready"]]  # type: ignore[attr-defined]
        _check(result, "pods_match_scope", bool(targets), f"{len(targets)} pod(s) match scope '{parsed.scope}'")  # type: ignore[attr-defined]
        normalized["_targets"] = [p["name"] for p in targets]
    elif action == ActionType.ROLLBACK_DEPLOYMENT:
        try:
            history = cluster.rollout_history()
        except IntegrationError as exc:
            _check(result, "history_readable", False, exc.message)
            return result
        by_rev = {h["revision"]: h for h in history}
        target = None
        if parsed.target_revision is not None:  # type: ignore[attr-defined]
            target = by_rev.get(parsed.target_revision)  # type: ignore[attr-defined]
        elif parsed.target_version is not None:  # type: ignore[attr-defined]
            target = next((h for h in history if h["version"] == parsed.target_version and h["revision"] != dep["revision"]), None)  # type: ignore[attr-defined]
        else:  # default: the most recent previous revision
            target = next((h for h in history if h["revision"] < dep["revision"]), None)
        if not _check(result, "target_exists", target is not None, "target revision/version found in rollout history" if target else "target revision/version not found in rollout history"):
            return result
        _check(result, "target_differs_from_current", target["revision"] != dep["revision"], f"current revision={dep['revision']}, target={target['revision']}")
        _check(result, "target_version_known", bool(target.get("version")), f"target image {target['image']}")
        normalized.update(target_revision=target["revision"], target_version=target["version"], target_image=target["image"])

    result.params = normalized
    result.ok = all(c["ok"] for c in result.checks)
    return result
