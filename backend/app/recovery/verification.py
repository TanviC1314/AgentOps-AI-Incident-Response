"""Post-recovery verification against explicit criteria, using live Kubernetes, probe and Prometheus data."""
import logging
import time
from collections.abc import Callable

from app.config import Settings
from app.integrations.http import IntegrationError
from app.integrations.registry import Clients

logger = logging.getLogger(__name__)


def default_criteria(settings: Settings, expected_version: str | None = None) -> dict:
    return {
        "max_error_ratio": settings.verify_max_error_rate,
        "max_p95_latency_seconds": settings.verify_max_p95_latency_seconds,
        "require_all_pods_ready": True,
        "expected_version": expected_version,
    }


def _c(name: str, passed: bool, observed, threshold, detail: str) -> dict:
    return {"name": name, "passed": passed, "observed": observed, "threshold": threshold, "detail": detail}


def run_checks(criteria: dict, clients: Clients) -> list[dict]:
    checks: list[dict] = []
    version = None
    try:
        dep = clients.cluster.get_deployment()
        pods = clients.cluster.list_pods()
        version = dep["version"]
        rollout_ok = (dep["updated_replicas"] == dep["desired_replicas"] == dep["ready_replicas"] == dep["available_replicas"]
                      and (dep["observed_generation"] or 0) >= dep["generation"])
        checks.append(_c("deployment_rollout_complete", rollout_ok,
                         {"desired": dep["desired_replicas"], "ready": dep["ready_replicas"], "updated": dep["updated_replicas"]},
                         "updated=ready=available=desired", "Kubernetes deployment status"))
        expected = criteria.get("expected_version")
        if expected:
            checks.append(_c("deployment_version", dep["version"] == expected, dep["version"], expected, "deployed image version"))
        if criteria.get("require_all_pods_ready", True):
            bad = [p["name"] for p in pods if not p["ready"] or p["deleting"]]
            ok = bool(pods) and not bad and len(pods) == dep["desired_replicas"]
            checks.append(_c("pods_healthy", ok, {"pods": len(pods), "not_ready": bad}, "all pods ready", "Kubernetes pod status"))
    except IntegrationError as exc:
        checks.append(_c("kubernetes_state", False, None, "readable", f"cannot verify: {exc.message}"))

    probe = clients.demo.probe()
    ready = probe.get("ready", {}).get("status_code")
    checks.append(_c("service_ready_probe", ready == 200, ready, 200, "direct /ready probe of the service"))

    try:
        m = clients.prometheus.service_metrics(window="30s", version=version)
        checks.append(_c("prometheus_target_up", m["up"] == 1, m["up"], 1, "Prometheus scrape target"))
        checks.append(_c("no_unhealthy_instances", m["unhealthy_instances"] == 0, m["unhealthy_instances"], 0, "demo_service_healthy == 0 count"))
        er = m["error_ratio"]
        mx = criteria["max_error_ratio"]
        checks.append(_c("error_rate", er is not None and er <= mx, er, mx, "5xx ratio (30s window)" if er is not None else "no request data"))
        p95 = m["p95_latency_seconds"]
        lat = criteria["max_p95_latency_seconds"]
        checks.append(_c("p95_latency", p95 is not None and p95 <= lat, p95, lat, "p95 latency (30s window)" if p95 is not None else "no latency data"))
    except IntegrationError as exc:
        checks.append(_c("prometheus_metrics", False, None, "readable", f"cannot verify: {exc.message}"))
    return checks


def verify_recovery(criteria: dict, settings: Settings, clients: Clients, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll until all checks pass N times in a row, or the timeout elapses. Never passes on missing data."""
    started = time.monotonic()
    if settings.verify_settle_seconds:
        sleep(settings.verify_settle_seconds)  # let rate() windows drop pre-recovery samples
    consecutive = attempts = 0
    checks: list[dict] = []
    while True:
        attempts += 1
        checks = run_checks(criteria, clients)
        consecutive = consecutive + 1 if all(c["passed"] for c in checks) else 0
        if consecutive >= settings.verify_required_consecutive_passes:
            break
        if time.monotonic() - started > settings.verify_timeout_seconds:
            break
        sleep(settings.verify_poll_interval_seconds)
    passed = consecutive >= settings.verify_required_consecutive_passes
    return {
        "passed": passed, "attempts": attempts, "duration_seconds": round(time.monotonic() - started, 1),
        "criteria": criteria, "checks": checks,
        "summary": "all recovery criteria satisfied" if passed else "recovery criteria NOT satisfied: " + ", ".join(c["name"] for c in checks if not c["passed"]),
    }
