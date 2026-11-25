export type Status = "open" | "investigating" | "awaiting_approval" | "recovering" | "resolved" | "failed";
export type Severity = "critical" | "high" | "medium" | "low";

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface Incident {
  id: string; title: string; service: string; severity: Severity; status: Status; summary: string;
  resolution: Record<string, any> | null; created_at: string; updated_at: string;
  investigation_started_at: string | null; investigation_finished_at: string | null;
  recovery_started_at: string | null; resolved_at: string | null;
}
export interface TimelineEvent { id: string; type: string; message: string; data: Record<string, any> | null; created_at: string }
export interface Alert {
  id: string; alert_name: string; status: string; severity: string; service: string;
  labels: Record<string, string>; annotations: Record<string, string>; starts_at: string; ends_at: string | null; generator_url: string | null;
}
export interface IncidentDetail extends Incident { alerts: Alert[]; timeline: TimelineEvent[] }

export interface Evidence { id: string; source: string; tool: string; summary: string; data: Record<string, any>; confirmed: boolean }
export interface Diagnosis {
  incident_summary: string; observed_symptoms: string[]; evidence: Evidence[];
  root_cause: { description: string; confirmed: boolean; evidence_refs: string[] };
  hypotheses: string[]; confidence: "low" | "medium" | "high";
  recommended_action: { action_type: string; parameters: Record<string, any>; rationale: string };
  risk_assessment: { level: string; factors: string[] };
  verification_criteria: Record<string, any>; data_gaps: string[]; analysis_mode: string; fallback_reason: string | null;
}
export interface AgentExecution {
  id: string; incident_id: string; mode: string; status: string; current_node: string | null;
  diagnosis: Diagnosis | null; error: string | null; fallback_reason: string | null; started_at: string; finished_at: string | null;
}
export interface ToolExecution { id: string; tool_name: string; input: any; output: any; status: string; error: string | null; duration_ms: number; created_at: string }
export interface AgentExecutionDetail extends AgentExecution { state: any; tool_executions: ToolExecution[] }

export interface Approval { id: string; decision: string; decided_by: string; reason: string | null; created_at: string }
export interface Proposal {
  id: string; incident_id: string; action_type: string; parameters: Record<string, any>; risk: string; rationale: string;
  requires_approval: boolean; status: string; validation: { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] } | null;
  verification_criteria: Record<string, any> | null; created_at: string; approvals?: Approval[];
}
export interface VerificationCheck { name: string; passed: boolean; observed: any; threshold: any; detail: string }
export interface RecoveryAction {
  id: string; proposal_id: string; incident_id: string; action_type: string; parameters: Record<string, any>; status: string;
  pre_state: Record<string, any> | null; post_state: Record<string, any> | null;
  verification: { passed: boolean; summary: string; attempts: number; duration_seconds: number; checks: VerificationCheck[] } | null;
  error: string | null; started_at: string; finished_at: string | null; duration_ms: number | null;
}
export interface AuditLog { id: string; incident_id: string | null; actor: string; action: string; details: Record<string, any> | null; request_id: string | null; created_at: string }

export interface Overview {
  total_incidents: number; active_incidents: number; resolved_incidents: number; failed_incidents: number;
  by_status: Record<string, number>; by_severity: Record<string, number>;
  recovery_success_rate: number | null; avg_investigation_seconds: number | null; avg_recovery_seconds: number | null;
  avg_time_to_resolve_seconds: number | null; tool_calls_total: number; tool_failure_rate: number | null; agent_runs_total: number;
  recent_executions: { id: string; incident_id: string; mode: string; status: string; started_at: string }[];
}
export interface Pod { name: string; phase: string; ready: boolean; restarts: number; image: string; version: string | null; waiting_reason: string | null }
export interface Revision { revision: number; image: string; version: string | null; created_at: string; replicas: number; ready_replicas: number; change_cause: string | null; is_current: boolean }
export interface Snapshot { id: string; incident_id: string | null; reason: string; version: string | null; revision: number | null; image: string | null; desired_replicas: number | null; ready_replicas: number | null; captured_at: string }
