import type { Incident, IncidentDetail, Proposal, RecoveryAction } from "../api/types";

export const incident = (over: Partial<Incident> = {}): Incident => ({
  id: "inc-1111-aaaa", title: "HighErrorRate: Error rate above 20%", service: "demo-service", severity: "high", status: "awaiting_approval",
  summary: "5xx ratio 60%", resolution: null, created_at: "2026-01-01T10:00:00Z", updated_at: "2026-01-01T10:00:00Z",
  investigation_started_at: null, investigation_finished_at: null, recovery_started_at: null, resolved_at: null, ...over,
});

export const detail = (over: Partial<IncidentDetail> = {}): IncidentDetail => ({
  ...incident(), alerts: [{ id: "a1", alert_name: "HighErrorRate", status: "firing", severity: "high", service: "demo-service", labels: {}, annotations: { summary: "Error rate above 20%" }, starts_at: "2026-01-01T10:00:00Z", ends_at: null, generator_url: null }],
  timeline: [
    { id: "e1", type: "incident_created", message: "Incident created from alert HighErrorRate", data: null, created_at: "2026-01-01T10:00:01Z" },
    { id: "e2", type: "proposal_created", message: "Recovery proposed: rollback_deployment (risk high)", data: null, created_at: "2026-01-01T10:00:20Z" },
  ], ...over,
});

export const proposal = (over: Partial<Proposal> = {}): Proposal => ({
  id: "prop-1", incident_id: "inc-1111-aaaa", action_type: "rollback_deployment", parameters: { target_revision: 1, target_version: "1.0.0", _current: { revision: 2, version: "2.0.0", replicas: 1 } },
  risk: "high", rationale: "Roll back to revision 1", requires_approval: true, status: "pending_approval",
  validation: { ok: true, checks: [{ name: "target_exists", ok: true, detail: "found" }] }, verification_criteria: null, created_at: "2026-01-01T10:00:20Z", approvals: [], ...over,
});

export const action = (over: Partial<RecoveryAction> = {}): RecoveryAction => ({
  id: "act-1", proposal_id: "prop-1", incident_id: "inc-1111-aaaa", action_type: "rollback_deployment", parameters: {}, status: "succeeded",
  pre_state: { version: "2.0.0", revision: 2 }, post_state: { version: "1.0.0", revision: 3 },
  verification: { passed: true, summary: "all recovery criteria satisfied", attempts: 2, duration_seconds: 12,
    checks: [{ name: "error_rate", passed: true, observed: 0, threshold: 0.05, detail: "5xx ratio" }, { name: "p95_latency", passed: false, observed: 1.4, threshold: 0.5, detail: "p95" }] },
  error: null, started_at: "2026-01-01T10:02:00Z", finished_at: "2026-01-01T10:03:00Z", duration_ms: 60000, ...over,
});
