"""Workflow tests: alert -> investigation (real tool code, doubled external systems) -> proposal -> approval -> execution -> verification.

External systems (Prometheus/Loki/Kubernetes) are replaced by HTTP/cluster doubles from tests/fakes.py; everything else
(webhook, DB, state machine, LangGraph agent, safety validation, recovery engine, verification, audit) is the production code.
"""
import json

import httpx
import pytest

from app import db as dbmod
from app.agent.runner import run_investigation
from app.config import get_settings
from app.integrations.kubernetes import UnavailableCluster
from app.integrations.registry import set_clients
from app.models import AgentExecution, Incident, ToolExecution
from app.recovery.executor import execute_proposal
from app.recovery.validation import validate_action
from tests.conftest import alert_payload
from tests.fakes import (BAD_ERRORS, BAD_UNHEALTHY, ERROR_LOGS, FakeCluster, loki_transport, make_clients, prom_transport,
                         scenario_prom)

ALERT_AFTER_DEPLOY = "2026-01-01T10:03:00Z"  # FakeCluster revision 2 was created 10:00


@pytest.fixture(autouse=True)
def fast_verification(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "verify_settle_seconds", 0)
    monkeypatch.setattr(s, "verify_poll_interval_seconds", 0)
    monkeypatch.setattr(s, "verify_timeout_seconds", 0.2)
    monkeypatch.setattr(s, "llm_api_key", "")
    yield
    set_clients(None)


def fire(client, alertname="HighErrorRate", severity="high", starts=ALERT_AFTER_DEPLOY, **kw) -> str:
    r = client.post("/api/v1/alerts/webhook", json=alert_payload(alertname, severity, starts=starts, **kw))
    return r.json()["created_incidents"][0]


def incident(client, iid):
    return client.get(f"/api/v1/incidents/{iid}").json()


def setup_bad_release(**cluster_kw):
    cluster = FakeCluster(**cluster_kw)
    set_clients(make_clients(prom=scenario_prom(cluster, BAD_ERRORS), cluster=cluster))
    return cluster


def drain(queue):
    """Run queued jobs synchronously (what the background worker does)."""
    from app.workers.worker import handle_job

    while queue.fallback.size():
        handle_job(queue.fallback.dequeue(0.01))


# ---------------------------------------------------------------- investigation
def test_faulty_release_investigation_proposes_rollback_with_evidence(client, queue):
    cluster = setup_bad_release()
    iid = fire(client)
    drain(queue)

    inc = incident(client, iid)
    assert inc["status"] == "awaiting_approval"
    ex = client.get(f"/api/v1/agent/executions?incident_id={iid}").json()["items"][0]
    detail = client.get(f"/api/v1/agent/executions/{ex['id']}").json()
    d = detail["diagnosis"]
    assert detail["status"] == "completed" and d["analysis_mode"] == "deterministic"
    assert "no LLM_API_KEY" in d["fallback_reason"]
    assert d["recommended_action"] == {"action_type": "rollback_deployment", "parameters": {"target_revision": 1}, "rationale": d["recommended_action"]["rationale"]}
    assert d["root_cause"]["confirmed"] is True and d["confidence"] == "high"
    ids = {e["id"] for e in d["evidence"]}
    assert d["root_cause"]["evidence_refs"] and set(d["root_cause"]["evidence_refs"]) <= ids
    assert d["risk_assessment"]["level"] == "high" and d["verification_criteria"]["expected_version"] == "1.0.0"
    tools = {t["tool_name"] for t in detail["tool_executions"]}
    assert {"get_service_metrics", "get_recent_logs", "get_kubernetes_pods", "get_deployment_history", "get_service_health",
            "get_previous_incidents", "propose_recovery_action"} <= tools
    assert all(t["duration_ms"] >= 0 for t in detail["tool_executions"])
    # evidence quotes real (doubled) tool output, e.g. the logged KeyError and the 60% ratio
    text = " ".join(e["summary"] for e in d["evidence"])
    assert "KeyError" in text and "60.0%" in text
    assert cluster.calls == []  # nothing executed before approval
    types = [e["type"] for e in inc["timeline"]]
    assert types[0] == "incident_created" and "diagnosis" in types and "proposal_created" in types


def test_loki_outage_is_recorded_as_data_gap_not_fabricated(client, queue):
    cluster = FakeCluster()
    set_clients(make_clients(prom=scenario_prom(cluster, BAD_ERRORS), loki=loki_transport(fail=True), cluster=cluster))
    iid = fire(client)
    drain(queue)
    ex = client.get(f"/api/v1/agent/executions?incident_id={iid}").json()["items"][0]
    d = ex["diagnosis"]
    assert any("get_recent_logs" in g and "loki" in g for g in d["data_gaps"])
    assert not any(e["source"] == "loki" for e in d["evidence"])
    assert d["confidence"] in ("medium", "low") and d["root_cause"]["confirmed"] is False  # weaker without logs
    failed = [t for t in client.get(f"/api/v1/agent/executions/{ex['id']}").json()["tool_executions"] if t["status"] == "error"]
    assert [t["tool_name"] for t in failed] == ["get_recent_logs"]


def test_no_evidence_means_no_action(client, queue):
    set_clients(make_clients(prom=prom_transport(fail=True), loki=loki_transport(fail=True), cluster=UnavailableCluster("n", "d", "disabled")))
    iid = fire(client, "HighLatency", "medium")
    drain(queue)
    inc = incident(client, iid)
    # latency alert alone -> restart proposal -> blocked, because Kubernetes cannot be validated -> handed to a human
    assert inc["status"] == "open"
    assert any(e["type"] == "proposal_blocked" for e in inc["timeline"])
    props = client.get(f"/api/v1/recovery/proposals?incident_id={iid}").json()["items"]
    assert props[0]["status"] == "blocked"


def test_unhealthy_instances_propose_restart(client, queue):
    pods = [FakeCluster._pod("demo-service-x", False, "1.0.0")]
    cluster = FakeCluster(pods=pods)
    cluster.history[0]["created_at"] = "2026-01-01T06:00:00+00:00"  # old deployment: not a regression
    set_clients(make_clients(prom=scenario_prom(cluster, BAD_UNHEALTHY), cluster=cluster))
    iid = fire(client, "UnhealthyInstances")
    drain(queue)
    p = client.get(f"/api/v1/recovery/proposals?incident_id={iid}").json()["items"][0]
    assert p["action_type"] == "restart_pods" and p["parameters"]["scope"] == "unhealthy" and p["risk"] == "medium"
    assert p["status"] == "pending_approval" and p["requires_approval"] is True


# ---------------------------------------------------------------- LLM
def llm_transport(content: dict | str, status=200):
    def handler(request: httpx.Request):
        if status != 200:
            return httpx.Response(status, text="boom")
        body = content if isinstance(content, str) else json.dumps(content)
        return httpx.Response(200, json={"choices": [{"message": {"content": body}}]})
    return httpx.MockTransport(handler)


def llm_answer(refs=("E1",), action=None, confirmed=True):
    return {"incident_summary": "LLM summary", "observed_symptoms": ["errors"], "hypotheses": [],
            "root_cause": {"description": "bad release", "confirmed": confirmed, "evidence_refs": list(refs)},
            "confidence": "high", "risk_factors": ["x"],
            "recommended_action": action or {"action_type": "rollback_deployment", "parameters": {"target_revision": 1}, "rationale": "r"}}


def investigate_with_llm(client, monkeypatch, transport):
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    setup_bad_release()
    iid = fire(client)
    run_investigation(iid, llm_transport=transport)
    ex = client.get(f"/api/v1/agent/executions?incident_id={iid}").json()["items"][0]
    return iid, ex["diagnosis"]


def test_llm_analysis_used_when_valid(client, monkeypatch):
    _, d = investigate_with_llm(client, monkeypatch, llm_transport(llm_answer()))
    assert d["analysis_mode"] == "llm" and d["incident_summary"] == "LLM summary" and d["fallback_reason"] is None
    # evidence is still produced by code, not the LLM
    assert any(e["source"] == "prometheus" for e in d["evidence"])


def test_llm_unsupported_confirmation_is_downgraded(client, monkeypatch):
    _, d = investigate_with_llm(client, monkeypatch, llm_transport(llm_answer(refs=("E99",))))
    assert d["root_cause"]["confirmed"] is False and d["root_cause"]["evidence_refs"] == [] and d["confidence"] == "medium"


@pytest.mark.parametrize("transport", [llm_transport("not json at all"), llm_transport({}, status=500), llm_transport({"incident_summary": 1})])
def test_llm_failure_falls_back_to_rules(client, monkeypatch, transport):
    iid, d = investigate_with_llm(client, monkeypatch, transport)
    assert d["analysis_mode"] == "deterministic" and d["fallback_reason"]
    assert incident(client, iid)["status"] == "awaiting_approval"  # incident handling is unaffected


def test_llm_cannot_propose_unsafe_action(client, monkeypatch):
    bad = {"action_type": "rollback_deployment", "parameters": {"target_revision": 99}, "rationale": "x"}
    _, d = investigate_with_llm(client, monkeypatch, llm_transport(llm_answer(action=bad)))
    assert d["analysis_mode"] == "deterministic" and "failed safety validation" in d["fallback_reason"]
    assert d["recommended_action"]["parameters"] == {"target_revision": 1}


# ---------------------------------------------------------------- approval + execution + verification
def approve(client, iid, who="alice"):
    p = client.get(f"/api/v1/recovery/proposals?incident_id={iid}&status=pending_approval").json()["items"][0]
    return p, client.post(f"/api/v1/recovery/proposals/{p['id']}/approve", json={"decided_by": who, "reason": "looks right"})


def test_approved_rollback_executes_verifies_and_resolves(client, queue):
    cluster = setup_bad_release()
    iid = fire(client)
    drain(queue)
    p, r = approve(client, iid)
    assert r.status_code == 200 and r.json()["approvals"][0]["decided_by"] == "alice"
    assert incident(client, iid)["status"] == "recovering"
    assert cluster.calls == []  # queued, not yet run
    drain(queue)

    inc = incident(client, iid)
    assert inc["status"] == "resolved" and inc["resolved_at"]
    assert cluster.calls == [("rollback", 1)]
    assert inc["resolution"]["action"] == "rollback_deployment" and inc["resolution"]["pre_state"]["version"] == "2.0.0"
    action = client.get(f"/api/v1/recovery/actions?incident_id={iid}").json()["items"][0]
    assert action["status"] == "succeeded" and action["verification"]["passed"] is True
    names = {c["name"] for c in action["verification"]["checks"]}
    assert {"deployment_rollout_complete", "deployment_version", "pods_healthy", "error_rate", "p95_latency"} <= names
    assert action["pre_state"]["version"] == "2.0.0" and action["post_state"]["version"] == "1.0.0"
    types = [e["type"] for e in inc["timeline"]]
    for t in ("proposal_approved", "recovery_started", "recovery_pre_state", "verification_passed"):
        assert t in types
    audit = {a["action"] for a in client.get(f"/api/v1/audit-logs?incident_id={iid}&page_size=200").json()["items"]}
    assert {"incident.created", "investigation.completed", "recovery.proposed", "recovery.approved", "recovery.started", "recovery.succeeded"} <= audit
    snaps = client.get(f"/api/v1/deployments/snapshots?incident_id={iid}").json()
    assert {s["reason"] for s in snaps} == {"pre_recovery", "post_recovery"}


def test_rejection_executes_nothing_and_returns_incident_to_manual(client, queue):
    cluster = setup_bad_release()
    iid = fire(client)
    drain(queue)
    p = client.get(f"/api/v1/recovery/proposals?incident_id={iid}").json()["items"][0]
    r = client.post(f"/api/v1/recovery/proposals/{p['id']}/reject", json={"decided_by": "bob", "reason": "too risky"})
    assert r.json()["status"] == "rejected"
    drain(queue)
    assert cluster.calls == [] and incident(client, iid)["status"] == "open"
    assert client.post(f"/api/v1/recovery/proposals/{p['id']}/approve", json={"decided_by": "bob"}).status_code == 409


def test_double_approval_is_rejected(client, queue):
    setup_bad_release()
    iid = fire(client)
    drain(queue)
    p, _ = approve(client, iid)
    again = client.post(f"/api/v1/recovery/proposals/{p['id']}/approve", json={"decided_by": "carol"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"


def test_approval_input_validation_and_unknown_proposal(client):
    assert client.post("/api/v1/recovery/proposals/nope/approve", json={"decided_by": "x"}).status_code == 404
    assert client.post("/api/v1/recovery/proposals/nope/approve", json={}).status_code == 422


def test_failed_rollback_verification_marks_incident_failed_and_stops(client, queue):
    cluster = setup_bad_release()
    cluster.heals_on_rollback = False  # metrics stay bad after the rollback
    iid = fire(client)
    drain(queue)
    approve(client, iid)
    drain(queue)
    inc = incident(client, iid)
    assert inc["status"] == "failed" and "automated actions stopped" in inc["resolution"]["reason"]
    assert cluster.calls == [("rollback", 1)]  # no further automated action
    assert client.get(f"/api/v1/recovery/proposals?incident_id={iid}&status=pending_approval").json()["total"] == 0
    action = client.get(f"/api/v1/recovery/actions?incident_id={iid}").json()["items"][0]
    assert action["status"] == "failed" and action["verification"]["passed"] is False
    assert any(not c["passed"] for c in action["verification"]["checks"])


def test_failed_restart_escalates_to_rollback_proposal_requiring_approval(client, queue):
    pods = [FakeCluster._pod("demo-service-x", False, "2.0.0")]
    cluster = FakeCluster(pods=pods)
    cluster.history[0]["created_at"] = "2026-01-01T06:00:00+00:00"
    cluster.heals_on_restart = False
    set_clients(make_clients(prom=scenario_prom(cluster, BAD_UNHEALTHY), cluster=cluster))
    iid = fire(client, "UnhealthyInstances")
    drain(queue)
    approve(client, iid)
    drain(queue)
    assert cluster.calls == [("restart_pods", "unhealthy")]
    inc = incident(client, iid)
    assert inc["status"] == "awaiting_approval"  # a rollback is proposed, never auto-run
    pending = client.get(f"/api/v1/recovery/proposals?incident_id={iid}&status=pending_approval").json()["items"]
    assert pending[0]["action_type"] == "rollback_deployment" and pending[0]["risk"] == "high"
    assert any(e["type"] == "recovery_escalated" for e in inc["timeline"])


def test_executor_refuses_unapproved_proposal(client, queue, db):
    cluster = setup_bad_release()
    iid = fire(client)
    drain(queue)
    p = client.get(f"/api/v1/recovery/proposals?incident_id={iid}").json()["items"][0]
    execute_proposal(p["id"])
    assert cluster.calls == [] and incident(client, iid)["status"] == "awaiting_approval"


def test_pre_execution_revalidation_blocks_stale_proposal(client, queue):
    cluster = setup_bad_release()
    iid = fire(client)
    drain(queue)
    approve(client, iid)
    cluster.history = [h for h in cluster.history if h["revision"] == 2]  # target revision vanished
    drain(queue)
    inc = incident(client, iid)
    assert inc["status"] == "failed" and cluster.calls == []


def test_low_risk_scale_is_auto_approved(client, queue, db):
    from app.recovery.proposals import create_proposal

    cluster = FakeCluster()
    set_clients(make_clients(prom=scenario_prom(cluster, BAD_ERRORS), cluster=cluster))
    iid = fire(client)
    inc = db.get(Incident, iid)
    from app.services.incidents import transition
    from app.models.enums import IncidentStatus

    transition(db, inc, IncidentStatus.INVESTIGATING)
    p = create_proposal(db, inc, "scale_deployment", {"replicas": 2}, "capacity", {})
    db.commit()
    assert p.risk == "low" and p.status == "approved" and inc.status == "recovering"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("action,params,ok_fragment", [
    ("scale_deployment", {"replicas": 99}, "replicas_in_safe_range"),
    ("scale_deployment", {"replicas": 0}, "replicas_in_safe_range"),
    ("scale_deployment", {"replicas": 1}, "replicas_change"),
    ("scale_deployment", {"replicas": 2, "namespace": "kube-system"}, "parameters_valid"),
    ("restart_pods", {"scope": "everything"}, "parameters_valid"),
    ("restart_pods", {"scope": "unhealthy"}, "pods_match_scope"),  # all pods ready in FakeCluster default
    ("rollback_deployment", {"target_revision": 42}, "target_exists"),
    ("rollback_deployment", {"target_revision": 2}, "target_differs_from_current"),
    ("rollback_deployment", {"target_version": "9.9.9"}, "target_exists"),
    ("delete_namespace", {}, "action_permitted"),
    ("none", {}, "action_permitted"),
])
def test_unsafe_actions_are_rejected(action, params, ok_fragment):
    res = validate_action(action, params, FakeCluster(pods=[FakeCluster._pod("p", True)]), get_settings())
    assert res.ok is False
    assert any(c["name"] == ok_fragment and not c["ok"] for c in res.checks), res.checks


def test_valid_rollback_records_current_and_target():
    res = validate_action("rollback_deployment", {}, FakeCluster(), get_settings())
    assert res.ok and res.params["target_revision"] == 1 and res.params["target_version"] == "1.0.0"
    assert res.params["_current"] == {"revision": 2, "version": "2.0.0", "replicas": 1} and res.risk.value == "high"


def test_validation_fails_closed_when_cluster_unavailable():
    res = validate_action("restart_pods", {}, UnavailableCluster("ns", "dep", "down"), get_settings())
    assert res.ok is False and res.checks[-1]["name"] == "cluster_reachable"


# ---------------------------------------------------------------- persistence / metrics
def test_workflow_state_persists_across_sessions(client, queue):
    setup_bad_release()
    iid = fire(client)
    drain(queue)
    with dbmod.session_factory()() as fresh:
        assert fresh.query(AgentExecution).filter_by(incident_id=iid).count() == 1
        assert fresh.query(ToolExecution).filter_by(incident_id=iid).count() >= 8
        ex = fresh.query(AgentExecution).filter_by(incident_id=iid).one()
        assert ex.state["completed_nodes"][-1] == "propose"


def test_overview_stats_reflect_real_data(client, queue):
    setup_bad_release()
    iid = fire(client)
    drain(queue)
    approve(client, iid)
    drain(queue)
    s = client.get("/api/v1/stats/overview").json()
    assert s["total_incidents"] == 1 and s["resolved_incidents"] == 1 and s["recovery_success_rate"] == 1.0
    assert s["tool_calls_total"] >= 8 and s["by_severity"] == {"high": 1}
    assert "agentops_tool_calls_total" in client.get("/metrics").text


def test_alert_logs_error_fixture_is_used():
    assert len(ERROR_LOGS) == 3
