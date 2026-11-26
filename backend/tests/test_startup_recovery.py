from app.models import AgentExecution, AuditLog, Incident, RecoveryAction, RecoveryProposal
from app.services.startup import recover_interrupted_work


def make_incident(db, status):
    inc = Incident(title="t", service="demo-service", severity="high", status=status)
    db.add(inc)
    db.flush()
    return inc


def make_proposal(db, inc, status):
    p = RecoveryProposal(incident_id=inc.id, action_type="restart_pods", parameters={}, risk="medium", status=status)
    db.add(p)
    db.flush()
    return p


def jobs(queue):
    out = []
    while queue.fallback.size():
        out.append(queue.fallback.dequeue(0.01))
    return out


def test_interrupted_investigation_is_requeued_and_its_lock_released(db, queue):
    inc = make_incident(db, "investigating")
    ex = AgentExecution(incident_id=inc.id, mode="deterministic", status="running")
    db.add(ex)
    db.commit()
    assert queue.acquire_lock(f"investigation:{inc.id}", 300)

    counts = recover_interrupted_work(db, queue)

    assert counts["investigations_requeued"] == 1
    assert inc.status == "open" and ex.status == "failed" and ex.finished_at
    assert jobs(queue) == [{"type": "investigate", "incident_id": inc.id}]
    assert queue.acquire_lock(f"investigation:{inc.id}", 300)


def test_open_incident_requeued_only_if_never_investigated(db, queue):
    lost = make_incident(db, "open")
    manual = make_incident(db, "open")
    db.add(AgentExecution(incident_id=manual.id, mode="deterministic", status="completed"))
    db.commit()
    recover_interrupted_work(db, queue)
    assert jobs(queue) == [{"type": "investigate", "incident_id": lost.id}]


def test_approved_but_unstarted_action_is_requeued(db, queue):
    inc = make_incident(db, "recovering")
    p = make_proposal(db, inc, "approved")
    db.commit()
    counts = recover_interrupted_work(db, queue)
    assert counts["executions_requeued"] == 1 and inc.status == "recovering"
    assert jobs(queue) == [{"type": "execute_proposal", "proposal_id": p.id}]


def test_half_executed_action_is_never_repeated(db, queue):
    inc = make_incident(db, "recovering")
    p = make_proposal(db, inc, "executing")
    action = RecoveryAction(proposal_id=p.id, incident_id=inc.id, action_type="restart_pods", parameters={}, status="running")
    db.add(action)
    db.commit()

    counts = recover_interrupted_work(db, queue)

    assert counts["recoveries_failed"] == 1
    assert (inc.status, p.status, action.status) == ("failed", "failed", "failed")
    assert jobs(queue) == []
    assert db.query(AuditLog).filter_by(incident_id=inc.id, action="recovery.interrupted").count() == 1


def test_settled_incidents_are_left_alone(db, queue):
    states = ["awaiting_approval", "resolved", "failed"]
    incidents = [make_incident(db, s) for s in states]
    db.commit()
    assert not any(recover_interrupted_work(db, queue).values())
    assert [i.status for i in incidents] == states and jobs(queue) == []
