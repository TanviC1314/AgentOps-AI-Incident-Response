import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { errorMessage, get, post } from "../api/client";
import type { AgentExecution, IncidentDetail as Detail, Page, Proposal, RecoveryAction } from "../api/types";
import { useAsync } from "../api/useAsync";
import { Timeline, VerificationChecks } from "../components/domain";
import ProposalCard from "../components/ProposalCard";
import { Async, Badge, Button, Card, EmptyState, SeverityBadge, StatusBadge, fmtSeconds, fmtTime } from "../components/ui";

export default function IncidentDetail() {
  const { id = "" } = useParams();
  const incident = useAsync(() => get<Detail>(`/incidents/${id}`), [id], 4000);
  const executions = useAsync(() => get<Page<AgentExecution>>("/agent/executions", { incident_id: id }), [id], 4000);
  const proposals = useAsync(() => get<Page<Proposal>>("/recovery/proposals", { incident_id: id }), [id], 4000);
  const actions = useAsync(() => get<Page<RecoveryAction>>("/recovery/actions", { incident_id: id }), [id], 4000);
  const [msg, setMsg] = useState<string | null>(null);

  async function reinvestigate() {
    try { await post(`/incidents/${id}/investigate`); setMsg("Investigation queued"); incident.reload(); } catch (e) { setMsg(errorMessage(e)); }
  }
  function refresh() { incident.reload(); proposals.reload(); actions.reload(); executions.reload(); }

  return (
    <div className="space-y-4">
      <Link to="/incidents" className="text-xs text-sky-400">← Incidents</Link>
      <Async state={incident}>
        {(i) => (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-semibold text-white">{i.title}</h1>
              <SeverityBadge severity={i.severity} /><StatusBadge status={i.status} />
              {(i.status === "open" || i.status === "failed") && <Button onClick={reinvestigate}>Re-investigate</Button>}
            </div>
            {msg && <div role="status" className="text-xs text-slate-400">{msg}</div>}
            <p className="text-sm text-slate-300">{i.summary}</p>
            <div className="text-xs text-slate-500">Service {i.service} · created {fmtTime(i.created_at)}{i.resolved_at ? ` · resolved ${fmtTime(i.resolved_at)}` : ""}</div>

            {i.resolution && (
              <Card title="Resolution details">
                <dl className="grid grid-cols-1 gap-2 text-sm md:grid-cols-2">
                  <div><dt className="text-xs text-slate-500">Outcome</dt><dd>{i.resolution.outcome}</dd></div>
                  {i.resolution.action && <div><dt className="text-xs text-slate-500">Action</dt><dd className="font-mono">{i.resolution.action}</dd></div>}
                  {i.resolution.verification && <div><dt className="text-xs text-slate-500">Verification</dt><dd>{i.resolution.verification}</dd></div>}
                  {i.resolution.reason && <div><dt className="text-xs text-slate-500">Reason</dt><dd>{i.resolution.reason}</dd></div>}
                  {i.resolution.recovery_duration_seconds != null && <div><dt className="text-xs text-slate-500">Recovery duration</dt><dd>{fmtSeconds(i.resolution.recovery_duration_seconds)}</dd></div>}
                </dl>
              </Card>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Timeline"><Timeline events={i.timeline} /></Card>
              <div className="space-y-4">
                <Card title="Alerts (from Prometheus via Alertmanager)">
                  <table><thead><tr><th>Alert</th><th>State</th><th>Since</th></tr></thead>
                    <tbody>{i.alerts.map((a) => <tr key={a.id}><td>{a.alert_name}<div className="text-xs text-slate-500">{a.annotations.summary}</div></td><td><Badge text={a.status} /></td><td className="text-xs">{fmtTime(a.starts_at)}</td></tr>)}</tbody></table>
                </Card>
                <Card title="Investigation history">
                  <Async state={executions} empty={(d) => d.items.length === 0}>
                    {(d) => <ul className="space-y-1 text-sm">{d.items.map((e) => <li key={e.id}><Link className="text-sky-400" to={`/investigations/${e.id}`}>{fmtTime(e.started_at)}</Link> · {e.mode} · {e.status}</li>)}</ul>}
                  </Async>
                </Card>
              </div>
            </div>

            <Card title="Recovery proposals">
              <Async state={proposals} empty={(d) => d.items.length === 0}>
                {(d) => <div className="space-y-3">{d.items.map((p) => <ProposalCard key={p.id} p={p} onDecided={refresh} />)}</div>}
              </Async>
            </Card>

            <Card title="Recovery history & rollback verification">
              <Async state={actions} empty={(d) => d.items.length === 0}>
                {(d) => d.items.length ? (
                  <div className="space-y-4">{d.items.map((a) => (
                    <div key={a.id} className="rounded border border-ink-600 p-3">
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm"><span className="font-mono">{a.action_type}</span><Badge text={a.status} />
                        <span className="text-xs text-slate-500">{fmtTime(a.started_at)} · {fmtSeconds(a.duration_ms != null ? a.duration_ms / 1000 : null)}</span></div>
                      {a.pre_state && <div className="mb-2 text-xs text-slate-400">Before: {a.pre_state.version} (rev {a.pre_state.revision}) → After: {a.post_state?.version ?? "?"} (rev {a.post_state?.revision ?? "?"})</div>}
                      <VerificationChecks action={a} />
                    </div>))}</div>
                ) : <EmptyState title="No recovery actions executed" />}
              </Async>
            </Card>
          </>
        )}
      </Async>
    </div>
  );
}
