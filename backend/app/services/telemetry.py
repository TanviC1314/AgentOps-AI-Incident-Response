"""Read-only telemetry functions shared by REST endpoints and agent tools. Each raises IntegrationError on failure."""
from app.config import get_settings
from app.integrations.http import IntegrationError
from app.integrations.registry import get_clients


def service_health() -> dict:
    """Direct probe + Prometheus view + Kubernetes readiness. Sub-sources fail independently."""
    c = get_clients()
    out: dict = {"service": get_settings().demo_service_name}
    out["probe"] = c.demo.probe()
    try:
        m = c.prometheus.service_metrics(window="1m")
        out["prometheus"] = {k: m[k] for k in ("up", "healthy_instances", "unhealthy_instances", "versions")}
    except IntegrationError as exc:
        out["prometheus"] = {"error": exc.as_dict()}
    try:
        pods = c.cluster.list_pods()
        out["kubernetes"] = {"pods_total": len(pods), "pods_ready": sum(1 for p in pods if p["ready"])}
    except IntegrationError as exc:
        out["kubernetes"] = {"error": exc.as_dict()}
    ready = out["probe"].get("ready", {}).get("status_code") == 200
    prom_ok = (out["prometheus"].get("unhealthy_instances") in (0, 0.0)) if "error" not in out["prometheus"] else None
    out["healthy"] = bool(ready and prom_ok is not False)
    return out


def service_metrics(window: str = "1m", version: str | None = None) -> dict:
    return get_clients().prometheus.service_metrics(window=window, version=version)


def recent_logs(minutes: int = 10, limit: int = 100, levels: list[str] | None = None, service: str | None = None) -> dict:
    return get_clients().loki.query_logs(service or get_settings().demo_service_name, minutes, limit, levels)


def kubernetes_pods() -> list[dict]:
    return get_clients().cluster.list_pods()


def deployment_status() -> dict:
    return get_clients().cluster.get_deployment()


def deployment_history() -> list[dict]:
    return get_clients().cluster.rollout_history()
