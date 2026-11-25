import os
import sys

os.environ["SELF_TRAFFIC_RPS"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from fastapi.testclient import TestClient

from app import main

H = {"X-Admin-Token": "local-admin-token"}


@pytest.fixture()
def client():
    main.faults.reset()
    main.BAD_RELEASE = False
    with TestClient(main.app) as c:
        yield c
    main.faults.reset()


def metric_value(client, name, labels=""):
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + labels):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_normal_behaviour(client):
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200
    assert client.get("/api/orders").status_code == 200
    assert client.get("/api/orders/3").json()["id"] == 3
    assert client.get("/api/orders/9999").status_code == 404
    assert "demo_http_requests_total" in client.get("/metrics").text
    assert metric_value(client, "demo_service_healthy") == 1.0


def test_error_fault_produces_5xx_and_metrics(client):
    assert client.post("/admin/faults", json={"type": "errors", "error_rate": 1.0}, headers=H).status_code == 200
    before = metric_value(client, "demo_http_request_errors_total", '{path="/api/orders/{order_id}",version="1.0.0"}')
    assert client.get("/api/orders/1").status_code == 500
    after = metric_value(client, "demo_http_request_errors_total", '{path="/api/orders/{order_id}",version="1.0.0"}')
    assert after == before + 1


def test_unhealthy_fault_fails_readiness_not_liveness(client):
    client.post("/admin/faults", json={"type": "unhealthy"}, headers=H)
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 503
    assert client.get("/live").status_code == 200
    assert metric_value(client, "demo_service_healthy") == 0.0


def test_latency_fault_slows_requests(client):
    import time

    client.post("/admin/faults", json={"type": "latency", "latency_ms": 300}, headers=H)
    t = time.perf_counter()
    client.get("/api/orders/1")
    assert time.perf_counter() - t >= 0.3


def test_reset_restores_normal_behaviour(client):
    client.post("/admin/faults", json={"type": "unhealthy"}, headers=H)
    client.post("/admin/faults", json={"type": "errors", "error_rate": 1.0}, headers=H)
    assert client.post("/admin/reset", headers=H).json()["unhealthy"] is False
    assert client.get("/ready").status_code == 200
    assert client.get("/api/orders/1").status_code == 200


def test_admin_requires_token_and_validates(client):
    assert client.post("/admin/reset").status_code == 401
    assert client.post("/admin/faults", json={"type": "nope"}, headers=H).status_code == 422


def test_bad_release_fails_requests(client):
    main.BAD_RELEASE = True
    codes = {client.get("/api/orders/1").status_code for _ in range(40)}
    assert 500 in codes
