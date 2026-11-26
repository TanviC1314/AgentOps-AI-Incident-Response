import pytest

from app.integrations.http import IntegrationError
from app.integrations.kubernetes import UnavailableCluster, version_from_image
from app.integrations.registry import set_clients
from tests.fakes import ERROR_LOGS, FakeCluster, FakeDemo, loki_transport, make_clients, prom_transport


@pytest.fixture(autouse=True)
def _reset_clients():
    yield
    set_clients(None)


def test_prometheus_missing_series_is_none_not_zero():
    c = make_clients(prom=prom_transport({})).prometheus
    assert c.scalar("anything") is None


def test_prometheus_unavailable_raises_structured_error():
    c = make_clients(prom=prom_transport(fail=True)).prometheus
    with pytest.raises(IntegrationError) as exc:
        c.service_metrics()
    assert exc.value.source == "prometheus" and exc.value.code == "unavailable"


def test_loki_parses_json_logs_and_summarises_errors():
    c = make_clients(loki=loki_transport(ERROR_LOGS)).loki
    res = c.query_logs("demo-service", minutes=5, limit=50)
    assert res["count"] == 3 and res["level_counts"] == {"ERROR": 2, "INFO": 1}
    assert "KeyError" in res["top_error_messages"][0]["message"]
    assert res["entries"][0]["request_id"]


def test_loki_rejects_logql_injection():
    c = make_clients().loki
    with pytest.raises(IntegrationError) as exc:
        c.query_logs('demo"} | drop', 5, 10)
    assert exc.value.code == "invalid_input"


def test_loki_unavailable_is_error_not_fabricated_logs():
    c = make_clients(loki=loki_transport(fail=True)).loki
    with pytest.raises(IntegrationError):
        c.query_logs("demo-service")


def test_version_from_image():
    assert version_from_image("agentops/demo-service:2.0.0") == "2.0.0"
    assert version_from_image("localhost:5000/demo") is None


def test_disabled_cluster_returns_structured_errors():
    cl = UnavailableCluster("ns", "dep", "disabled")
    with pytest.raises(IntegrationError) as exc:
        cl.list_pods()
    assert exc.value.as_dict()["source"] == "kubernetes"


def test_telemetry_endpoints_use_real_clients(client):
    set_clients(make_clients(prom=prom_transport({"min(up": 1, "count(demo_service_healthy == 0)": 0}), cluster=FakeCluster()))
    assert client.get("/api/v1/kubernetes/pods").json()["pods"][0]["ready"] is True
    hist = client.get("/api/v1/deployments/history").json()["revisions"]
    assert [h["revision"] for h in hist] == [2, 1]
    assert client.get("/api/v1/logs?limit=10").json()["count"] == 3
    health = client.get("/api/v1/service/health").json()
    assert health["healthy"] is True and health["kubernetes"]["pods_ready"] == 1


def test_endpoints_return_503_when_dependencies_down(client):
    set_clients(make_clients(prom=prom_transport(fail=True), loki=loki_transport(fail=True),
                             cluster=UnavailableCluster("n", "d", "kubernetes integration disabled")))
    for path in ("/api/v1/service/metrics", "/api/v1/logs", "/api/v1/kubernetes/pods"):
        r = client.get(path)
        assert r.status_code == 503 and r.json()["error"]["code"] == "dependency_unavailable"
    # health degrades gracefully instead of failing
    assert client.get("/api/v1/service/health").status_code == 200


def test_simulation_triggers_real_faults_and_audits(client):
    demo = FakeDemo()
    set_clients(make_clients(demo=demo))
    assert client.post("/api/v1/simulations/high_latency").status_code == 202
    assert client.post("/api/v1/simulations/high_error_rate").status_code == 202
    assert client.post("/api/v1/simulations/unhealthy_instances").status_code == 202
    assert [f["type"] for f in demo.faults] == ["latency", "errors", "unhealthy"]
    # no incident is ever created by the simulator itself
    assert client.get("/api/v1/incidents").json()["total"] == 0
    assert client.post("/api/v1/simulations/nonsense").status_code == 422
    assert client.post("/api/v1/simulations/reset").status_code == 200 and demo.resets == 1
    actions = [a["action"] for a in client.get("/api/v1/audit-logs").json()["items"]]
    assert "simulation.triggered" in actions and "simulation.reset" in actions


def test_metrics_endpoint_sets_service_error_rate_gauge(client):
    from prometheus_client import REGISTRY

    from app.core.metrics import SERVICE_ERROR_RATE

    set_clients(make_clients(prom=prom_transport({"error_ratio": 0.42})))
    r = client.get("/metrics")
    assert r.status_code == 200
    text = r.text
    assert "agentops_service_error_rate" in text
    assert pytest.approx(SERVICE_ERROR_RATE._value.get(), rel=1e-3) == 0.42


def test_metrics_endpoint_survives_prometheus_unavailable(client):
    from app.core.metrics import SERVICE_ERROR_RATE

    SERVICE_ERROR_RATE.set(0.0)
    set_clients(make_clients(prom=prom_transport(fail=True)))
    r = client.get("/metrics")
    assert r.status_code == 200  # never 503 from /metrics
    assert SERVICE_ERROR_RATE._value.get() == 0.0  # unchanged


def test_failed_deployment_requires_kubernetes(client):
    set_clients(make_clients(cluster=UnavailableCluster("n", "d", "disabled")))
    assert client.post("/api/v1/simulations/failed_deployment").status_code == 503
    cluster = FakeCluster()
    cluster.history[0]["is_current"], cluster.history[1]["is_current"] = False, True
    set_clients(make_clients(cluster=cluster))
    assert client.post("/api/v1/simulations/failed_deployment").status_code == 202
    assert cluster.calls[0][0] == "apply_release" and cluster.calls[0][1].endswith(":2.0.0")
