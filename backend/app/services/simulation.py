"""Failure simulation controller.

Scenarios genuinely change the behaviour of the demo workload; nothing here creates incidents or
alerts. Detection must come from Prometheus rules -> Alertmanager -> the AgentOps webhook.
"""
from app.config import get_settings
from app.core.errors import ConflictError, ValidationFailed
from app.integrations.http import IntegrationError
from app.integrations.registry import get_clients

SCENARIOS = {
    "high_latency": "Adds 2s latency to every request on the demo service -> HighLatency alert",
    "high_error_rate": "Makes 60% of requests fail with HTTP 500 -> HighErrorRate alert",
    "unhealthy_instances": "Makes the instance fail health/readiness checks -> UnhealthyInstances alert",
    "failed_deployment": "Rolls out a faulty release (v2.0.0) to the Kubernetes deployment -> HighErrorRate alert",
}


def _image_repo(image: str) -> str:
    return image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image


def trigger(scenario: str) -> dict:
    settings = get_settings()
    if not settings.simulation_enabled:
        raise ConflictError("Failure simulation is disabled (SIMULATION_ENABLED=false)")
    if scenario not in SCENARIOS:
        raise ValidationFailed(f"Unknown scenario '{scenario}'", {"allowed": sorted(SCENARIOS)})
    c = get_clients()
    if scenario == "high_latency":
        applied = c.demo.inject_fault({"type": "latency", "latency_ms": 2000})
    elif scenario == "high_error_rate":
        applied = c.demo.inject_fault({"type": "errors", "error_rate": 0.6})
    elif scenario == "unhealthy_instances":
        applied = c.demo.inject_fault({"type": "unhealthy"})
    else:
        dep = c.cluster.get_deployment()  # raises IntegrationError if Kubernetes is unavailable
        if dep["version"] == settings.demo_bad_version:
            raise ConflictError(f"Version {settings.demo_bad_version} is already deployed")
        image = f"{_image_repo(dep['image'])}:{settings.demo_bad_version}"
        applied = c.cluster.apply_release(
            image, {"APP_VERSION": settings.demo_bad_version, "BAD_RELEASE": "true"},
            f"simulated faulty release {settings.demo_bad_version}",
        )
    return {"scenario": scenario, "description": SCENARIOS[scenario], "applied": applied}


def reset() -> dict:
    """Restore normal behaviour: clear injected faults and re-deploy the baseline release if needed."""
    settings = get_settings()
    c = get_clients()
    result: dict = {}
    try:
        dep = c.cluster.get_deployment()
        if dep["version"] != settings.demo_baseline_version:
            image = f"{_image_repo(dep['image'])}:{settings.demo_baseline_version}"
            c.cluster.apply_release(
                image, {"APP_VERSION": settings.demo_baseline_version, "BAD_RELEASE": "false"},
                "simulation reset to baseline release",
            )
            result["redeployed_baseline"] = c.cluster.wait_for_rollout(settings.rollout_timeout_seconds).get("rollout_complete")
    except IntegrationError as exc:
        result["kubernetes"] = exc.as_dict()
    result["demo_service"] = c.demo.reset()
    return result


def status() -> dict:
    c = get_clients()
    out: dict = {"scenarios": SCENARIOS, "enabled": get_settings().simulation_enabled}
    try:
        out["demo_service"] = c.demo.admin_state()
    except IntegrationError as exc:
        out["demo_service"] = {"error": exc.as_dict()}
    return out
