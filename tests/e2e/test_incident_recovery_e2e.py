"""End-to-end incident recovery test against the REAL running stack (Docker Compose + kind).

    docker compose -f docker-compose.yml -f docker-compose.k8s.yml up -d --build   (after scripts/kind-setup.ps1)
    $env:AGENTOPS_E2E = "1"; pytest tests/e2e -v -s

Nothing is mocked: the failure is a faulty release rolled out to Kubernetes, detected by a Prometheus rule,
delivered by Alertmanager to the AgentOps webhook, investigated with real Prometheus/Loki/Kubernetes data, approved,
rolled back and verified. The test is repeatable: it resets the simulation before and after and refuses to run
while another incident for the service is still active (alerts would attach to it).
"""
import os
import time

import httpx
import pytest

BACKEND = os.getenv("AGENTOPS_URL", "http://localhost:8000/api/v1")
PROM = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
ALERTMANAGER = os.getenv("ALERTMANAGER_URL", "http://localhost:9093")
DEMO = os.getenv("DEMO_SERVICE_URL", "http://localhost:8001")

pytestmark = pytest.mark.skipif(os.getenv("AGENTOPS_E2E") != "1", reason="set AGENTOPS_E2E=1 with the full stack running")


def wait_for(description, fn, timeout, interval=3.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = fn()
            if last:
                print(f"  ok: {description}")
                return last
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout}s waiting for: {description} (last: {last!r})")


@pytest.fixture(scope="module")
def http():
    with httpx.Client(timeout=15) as c:
        yield c


@pytest.fixture(scope="module", autouse=True)
def stack(http):
    try:
        assert http.get(f"{BACKEND.rsplit('/api', 1)[0]}/ready").status_code == 200
        assert http.get(f"{PROM}/-/ready").status_code == 200
        http.get(f"{DEMO}/health").raise_for_status()
    except (httpx.HTTPError, AssertionError) as exc:
        pytest.fail(f"stack is not running: {exc!r}")
    if http.get(f"{BACKEND}/kubernetes/deployment").status_code != 200:
        pytest.skip("Kubernetes mode is not active (see docker-compose.k8s.yml); recovery cannot be exercised")
    active = [s for s in ("open", "investigating", "awaiting_approval", "recovering")
              if http.get(f"{BACKEND}/incidents", params={"status": s}).json()["total"]]
    if active:
        pytest.fail(f"incidents in states {active} are still active; resolve/clear them first for a clean run")
    http.post(f"{BACKEND}/simulations/reset", timeout=180).raise_for_status()
    yield
    http.post(f"{BACKEND}/simulations/reset", timeout=180)


def test_failed_deployment_is_detected_investigated_approved_rolled_back_and_verified(http):
    before = {i["id"] for i in http.get(f"{BACKEND}/incidents", params={"page_size": 100}).json()["items"]}
    assert http.get(f"{BACKEND}/kubernetes/deployment").json()["version"] == "1.0.0"

    # 1-2. start from the real demo service, trigger a real failure (faulty release rollout)
    assert http.post(f"{BACKEND}/simulations/failed_deployment").status_code == 202

    # 3. Prometheus detects it (rule evaluation, not an inserted record)
    def prom_alert():
        alerts = http.get(f"{PROM}/api/v1/alerts").json()["data"]["alerts"]
        return [a for a in alerts if a["labels"]["alertname"] == "HighErrorRate" and a["state"] == "firing"]
    wait_for("Prometheus HighErrorRate firing", prom_alert, 240)

    # 4. Alertmanager received and routes it
    wait_for("Alertmanager holds HighErrorRate", lambda: [a for a in http.get(f"{ALERTMANAGER}/api/v2/alerts").json()
                                                         if a["labels"]["alertname"] == "HighErrorRate"], 90)

    # 5. AgentOps created the incident from the webhook
    def new_incident():
        items = http.get(f"{BACKEND}/incidents", params={"page_size": 100}).json()["items"]
        return next((i for i in items if i["id"] not in before), None)
    incident = wait_for("AgentOps incident created from webhook", new_incident, 120)
    iid = incident["id"]
    detail = http.get(f"{BACKEND}/incidents/{iid}").json()
    assert detail["alerts"][0]["alert_name"] in {"HighErrorRate", "ServiceUnavailable", "UnhealthyInstances"}
    assert detail["timeline"][0]["type"] == "incident_created"

    # 6-8. investigation runs with real tools and proposes a recovery
    wait_for("incident awaiting approval", lambda: http.get(f"{BACKEND}/incidents/{iid}").json()["status"] == "awaiting_approval", 180)
    ex = http.get(f"{BACKEND}/agent/executions", params={"incident_id": iid}).json()["items"][0]
    exd = http.get(f"{BACKEND}/agent/executions/{ex['id']}").json()
    tools = {t["tool_name"]: t for t in exd["tool_executions"]}
    for name in ("get_service_metrics", "get_recent_logs", "get_kubernetes_pods", "get_deployment_history"):
        assert tools[name]["status"] == "ok", f"{name} failed: {tools[name]['error']}"
    sources = {e["source"] for e in exd["diagnosis"]["evidence"]}
    assert {"prometheus", "loki", "kubernetes"} <= sources
    assert exd["diagnosis"]["recommended_action"]["action_type"] == "rollback_deployment"
    proposal = http.get(f"{BACKEND}/recovery/proposals", params={"incident_id": iid, "status": "pending_approval"}).json()["items"][0]
    assert proposal["parameters"]["target_version"] == "1.0.0"

    # 9-10. human approves; recovery executes
    r = http.post(f"{BACKEND}/recovery/proposals/{proposal['id']}/approve", json={"decided_by": "e2e-test", "reason": "automated e2e run"})
    assert r.status_code == 200

    # 11-12. service verified healthy and incident resolved only after verification
    final = wait_for("incident resolved", lambda: (d := http.get(f"{BACKEND}/incidents/{iid}").json())["status"] in ("resolved", "failed") and d, 420, 5)
    assert final["status"] == "resolved", final["resolution"]
    action = http.get(f"{BACKEND}/recovery/actions", params={"incident_id": iid}).json()["items"][0]
    assert action["verification"]["passed"] is True
    assert http.get(f"{BACKEND}/kubernetes/deployment").json()["version"] == "1.0.0"
    assert http.get(f"{DEMO}/ready").status_code == 200

    # 13. audit log has the whole story
    actions = {a["action"] for a in http.get(f"{BACKEND}/audit-logs", params={"incident_id": iid, "page_size": 200}).json()["items"]}
    assert {"incident.created", "investigation.completed", "recovery.proposed", "recovery.approved", "recovery.started", "recovery.succeeded"} <= actions
