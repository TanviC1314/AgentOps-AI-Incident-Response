import { Link } from "react-router-dom";
import { Badge, SeverityBadge, StatusBadge, fmtTime, label } from "./ui";
import type { Diagnosis, RecoveryAction, TimelineEvent, ToolExecution } from "../api/types";

const EVENT_TONE: Record<string, string> = {
  incident_created: "bg-sky-400", status_changed: "bg-slate-400", diagnosis: "bg-violet-400", proposal_created: "bg-amber-400",
  proposal_approved: "bg-emerald-400", proposal_rejected: "bg-red-400", proposal_blocked: "bg-red-400", verification_passed: "bg-emerald-400",
  verification_failed: "bg-red-400", recovery_error: "bg-red-400", recovery_escalated: "bg-orange-400",
};

export function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <ol className="relative ml-2 border-l border-ink-600" aria-label="Incident timeline">
      {events.map((e) => (
        <li key={e.id} className="mb-4 ml-4">
          <span className={`absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full ${EVENT_TONE[e.type] ?? "bg-slate-500"}`} />
          <div className="text-xs text-slate-500">{fmtTime(e.created_at)} · {label(e.type)}</div>
          <div className="text-sm text-slate-200">{e.message}</div>
        </li>
      ))}
    </ol>
  );
}

export function VerificationChecks({ action }: { action: RecoveryAction }) {
  const v = action.verification;
  if (!v) return <div className="text-xs text-slate-500">{action.error ? `No verification (error: ${action.error})` : "Verification not run yet"}</div>;
  return (
    <div data-testid="verification">
      <div className={`mb-2 text-sm font-medium ${v.passed ? "text-emerald-300" : "text-red-300"}`}>
        {v.passed ? "Verification passed" : "Verification failed"} <span className="font-normal text-slate-400">· {v.attempts} attempt(s), {v.duration_seconds}s</span>
      </div>
      <table>
        <thead><tr><th>Check</th><th>Observed</th><th>Threshold</th><th>Result</th></tr></thead>
        <tbody>
          {v.checks.map((c) => (
            <tr key={c.name}>
              <td>{label(c.name)}<div className="text-xs text-slate-500">{c.detail}</div></td>
              <td className="font-mono text-xs">{JSON.stringify(c.observed)}</td>
              <td className="font-mono text-xs">{JSON.stringify(c.threshold)}</td>
              <td><Badge text={c.passed ? "pass" : "fail"} tone={c.passed ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" : "bg-red-500/15 text-red-300 border-red-500/40"} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ToolCalls({ calls }: { calls: ToolExecution[] }) {
  return (
    <table>
      <thead><tr><th>Tool</th><th>Status</th><th>Duration</th><th>Detail</th></tr></thead>
      <tbody>
        {calls.map((t) => (
          <tr key={t.id}>
            <td className="font-mono text-xs">{t.tool_name}</td>
            <td><Badge text={t.status} tone={t.status === "ok" ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" : "bg-red-500/15 text-red-300 border-red-500/40"} /></td>
            <td>{t.duration_ms.toFixed(0)} ms</td>
            <td className="text-xs text-slate-400">{t.error ?? summarize(t.output)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function summarize(o: Record<string, any> | null): string {
  if (!o) return "";
  if (o.entries) return `${o.count} log lines`;
  if (o.pods) return `${o.pods.length} pod(s)`;
  if (o.revisions) return `${o.revisions.length} revision(s)`;
  if (o.proposal_id) return `proposal ${String(o.proposal_id).slice(0, 8)} (${o.status})`;
  if (o.error_ratio !== undefined) return `error ratio ${o.error_ratio == null ? "n/a" : (o.error_ratio * 100).toFixed(1) + "%"}`;
  return Object.keys(o).slice(0, 4).join(", ");
}

export function DiagnosisView({ d }: { d: Diagnosis }) {
  return (
    <div className="space-y-4" data-testid="diagnosis">
      <div>
        <div className="text-sm text-slate-100">{d.incident_summary}</div>
        <div className="mt-1 text-xs text-slate-500">
          Analysis mode: <b>{d.analysis_mode}</b>{d.fallback_reason ? ` (${d.fallback_reason})` : ""} · confidence <b>{d.confidence}</b>
        </div>
      </div>
      <Section title="Observed symptoms"><ul className="list-disc pl-5 text-sm">{d.observed_symptoms.map((s) => <li key={s}>{s}</li>)}</ul></Section>
      <Section title="Evidence collected (from tools)">
        <ul className="space-y-1">
          {d.evidence.map((e) => (
            <li key={e.id} className="text-sm"><span className="mr-2 rounded bg-ink-600 px-1.5 py-0.5 font-mono text-xs">{e.id}</span>
              <span className="mr-2 text-xs text-slate-500">{e.source}</span>{e.summary}</li>
          ))}
        </ul>
      </Section>
      <Section title="Possible root cause">
        <div className="flex items-start gap-2 text-sm">
          <Badge text={d.root_cause.confirmed ? "confirmed" : "hypothesis"} tone={d.root_cause.confirmed ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" : "bg-amber-500/15 text-amber-300 border-amber-500/40"} />
          <span>{d.root_cause.description}</span>
        </div>
        <div className="mt-1 text-xs text-slate-500">Cites: {d.root_cause.evidence_refs.join(", ") || "no evidence"}</div>
        {d.hypotheses.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-slate-300">{d.hypotheses.map((h) => <li key={h}>Hypothesis: {h}</li>)}</ul>}
      </Section>
      <Section title="Recommended recovery action">
        <div className="text-sm"><span className="font-mono">{d.recommended_action.action_type}</span> <span className="font-mono text-xs text-slate-400">{JSON.stringify(d.recommended_action.parameters)}</span></div>
        <div className="text-sm text-slate-300">{d.recommended_action.rationale}</div>
      </Section>
      <Section title="Risk assessment">
        <Badge text={d.risk_assessment.level} />
        <ul className="mt-1 list-disc pl-5 text-sm text-slate-300">{d.risk_assessment.factors.map((f) => <li key={f}>{f}</li>)}</ul>
      </Section>
      <Section title="Verification criteria"><pre className="overflow-x-auto rounded bg-ink-900 p-2 text-xs">{JSON.stringify(d.verification_criteria, null, 2)}</pre></Section>
      {d.data_gaps.length > 0 && (
        <Section title="Data gaps (tools that failed - nothing was invented)">
          <ul className="list-disc pl-5 text-sm text-amber-300">{d.data_gaps.map((g) => <li key={g}>{g}</li>)}</ul>
        </Section>
      )}
    </div>
  );
}

const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div><h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">{title}</h3>{children}</div>
);

export function IncidentRow({ i }: { i: { id: string; title: string; service: string; severity: string; status: string; created_at: string } }) {
  return (
    <tr>
      <td><Link className="text-sky-400 hover:underline" to={`/incidents/${i.id}`}>{i.title}</Link><div className="text-xs text-slate-500">{i.service}</div></td>
      <td><SeverityBadge severity={i.severity} /></td>
      <td><StatusBadge status={i.status} /></td>
      <td className="text-xs text-slate-400">{fmtTime(i.created_at)}</td>
    </tr>
  );
}
