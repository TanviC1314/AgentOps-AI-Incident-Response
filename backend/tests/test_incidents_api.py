import pytest

from app.core.errors import InvalidTransitionError
from app.models import Incident
from app.models.enums import IncidentStatus
from app.services.incidents import can_transition, transition
from tests.conftest import alert_payload


def test_webhook_creates_incident_and_queues_investigation(client, queue):
    r = client.post("/api/v1/alerts/webhook", json=alert_payload())
    assert r.status_code == 202
    body = r.json()
    assert len(body["created_incidents"]) == 1
    iid = body["created_incidents"][0]

    inc = client.get(f"/api/v1/incidents/{iid}").json()
    assert inc["status"] == "open" and inc["severity"] == "high" and inc["service"] == "demo-service"
    assert inc["alerts"][0]["alert_name"] == "HighErrorRate"
    assert inc["timeline"][0]["type"] == "incident_created"
    assert queue.fallback.size() == 1
    assert queue.fallback.dequeue(0.1) == {"type": "investigate", "incident_id": iid}


def test_webhook_is_idempotent(client, queue):
    client.post("/api/v1/alerts/webhook", json=alert_payload())
    r = client.post("/api/v1/alerts/webhook", json=alert_payload())
    assert r.json()["created_incidents"] == [] and r.json()["ignored"] == 1
    assert client.get("/api/v1/incidents").json()["total"] == 1
    assert queue.fallback.size() == 1


def test_second_alert_for_same_service_attaches_and_escalates(client):
    client.post("/api/v1/alerts/webhook", json=alert_payload(severity="medium", fingerprint="a"))
    r = client.post("/api/v1/alerts/webhook", json=alert_payload("HighLatency", "critical", fingerprint="b"))
    assert r.json()["attached_alerts"] == 1
    items = client.get("/api/v1/incidents").json()["items"]
    assert len(items) == 1 and items[0]["severity"] == "critical"


def test_resolved_alert_updates_alert_and_adds_timeline_event(client):
    client.post("/api/v1/alerts/webhook", json=alert_payload())
    r = client.post("/api/v1/alerts/webhook", json=alert_payload(status="resolved"))
    assert r.json()["updated_alerts"] == 1
    iid = client.get("/api/v1/incidents").json()["items"][0]["id"]
    types = [e["type"] for e in client.get(f"/api/v1/incidents/{iid}/timeline").json()]
    assert "alert_resolved" in types
    # Alert resolution never resolves the incident: only verified recovery does.
    assert client.get(f"/api/v1/incidents/{iid}").json()["status"] == "open"


@pytest.mark.parametrize("bad", [
    {},
    {"status": "firing", "alerts": []},
    {"status": "weird", "alerts": [{"status": "firing", "labels": {"alertname": "x"}, "startsAt": "2026-01-01T00:00:00Z"}]},
    {"status": "firing", "alerts": [{"status": "firing", "labels": {}, "startsAt": "2026-01-01T00:00:00Z"}]},
    {"status": "firing", "alerts": [{"status": "firing", "labels": {"alertname": "x"}, "startsAt": "not-a-date"}]},
])
def test_webhook_rejects_invalid_payloads(client, bad):
    r = client.post("/api/v1/alerts/webhook", json=bad)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert client.get("/api/v1/incidents").json()["total"] == 0


def test_filtering_pagination_and_errors(client):
    for i, sev in enumerate(["low", "high", "high"]):
        client.post("/api/v1/alerts/webhook", json=alert_payload(severity=sev, service=f"svc{i}", fingerprint=f"f{i}"))
    assert client.get("/api/v1/incidents?severity=high").json()["total"] == 2
    assert client.get("/api/v1/incidents?status=resolved").json()["total"] == 0
    page = client.get("/api/v1/incidents?page=2&page_size=2").json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert client.get("/api/v1/incidents?severity=bogus").status_code == 422
    r = client.get("/api/v1/incidents/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert r.headers["x-request-id"]


def test_state_machine_rules(db):
    inc = Incident(title="t", service="s", severity="low", status="open")
    db.add(inc)
    db.flush()
    with pytest.raises(InvalidTransitionError):
        transition(db, inc, IncidentStatus.RESOLVED)
    transition(db, inc, IncidentStatus.INVESTIGATING)
    transition(db, inc, IncidentStatus.AWAITING_APPROVAL)
    transition(db, inc, IncidentStatus.RECOVERING)
    transition(db, inc, IncidentStatus.RESOLVED)
    assert inc.resolved_at is not None
    for target in IncidentStatus:
        assert not can_transition(IncidentStatus.RESOLVED, target)


def test_audit_log_records_webhook_actions(client):
    client.post("/api/v1/alerts/webhook", json=alert_payload())
    logs = client.get("/api/v1/audit-logs").json()
    assert logs["total"] >= 1 and any(l["action"] == "incident.created" for l in logs["items"])


def test_incident_persists_across_sessions(engine, client):
    client.post("/api/v1/alerts/webhook", json=alert_payload())
    from app import db as dbmod

    with dbmod.session_factory()() as other_session:
        assert other_session.query(Incident).count() == 1
