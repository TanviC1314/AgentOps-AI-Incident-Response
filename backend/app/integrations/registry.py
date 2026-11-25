"""Lazily-built clients for external systems; tests replace them via `set_clients`."""
from dataclasses import dataclass

from app.config import get_settings
from app.integrations.demo import DemoServiceClient
from app.integrations.kubernetes import ClusterClient, build_cluster
from app.integrations.loki import LokiClient
from app.integrations.prometheus import PrometheusClient


@dataclass
class Clients:
    prometheus: PrometheusClient
    loki: LokiClient
    demo: DemoServiceClient
    cluster: ClusterClient


_clients: Clients | None = None


def build_clients() -> Clients:
    s = get_settings()
    kw = {"timeout": s.external_call_timeout_seconds, "retries": s.external_call_max_retries}
    return Clients(
        prometheus=PrometheusClient(s.prometheus_url, **kw),
        loki=LokiClient(s.loki_url, **kw),
        demo=DemoServiceClient(s.demo_service_url, admin_token=s.demo_admin_token, **kw),
        cluster=build_cluster(s),
    )


def get_clients() -> Clients:
    global _clients
    if _clients is None:
        _clients = build_clients()
    return _clients


def set_clients(clients: Clients | None) -> None:
    global _clients
    _clients = clients
