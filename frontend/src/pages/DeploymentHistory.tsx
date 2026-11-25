import { get } from "../api/client";
import type { Revision, Snapshot } from "../api/types";
import { useAsync } from "../api/useAsync";
import { Async, Badge, Card, IncidentLink, fmtTime } from "../components/ui";

export default function DeploymentHistory() {
  const rev = useAsync(() => get<{ namespace: string; revisions: Revision[] }>("/deployments/history"), [], 8000);
  const snaps = useAsync(() => get<Snapshot[]>("/deployments/snapshots", { limit: 30 }), [], 8000);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Deployment history</h1>
      <Card title="Rollout history (Kubernetes ReplicaSets)">
        <Async state={rev} empty={(d) => d.revisions.length === 0}>
          {(d) => (
            <>
              <div className="mb-2 text-xs text-slate-500">Namespace {d.namespace}</div>
              <table><thead><tr><th>Revision</th><th>Version</th><th>Image</th><th>Created</th><th>Change cause</th><th /></tr></thead>
                <tbody>{d.revisions.map((r) => (
                  <tr key={r.revision}><td>{r.revision}</td><td>{r.version}</td><td className="font-mono text-xs">{r.image}</td><td className="text-xs">{fmtTime(r.created_at)}</td>
                    <td className="text-xs text-slate-400">{r.change_cause ?? "-"}</td><td>{r.is_current && <Badge text="current" tone="bg-emerald-500/15 text-emerald-300 border-emerald-500/40" />}</td></tr>))}</tbody></table>
            </>
          )}
        </Async>
      </Card>
      <Card title="Snapshots recorded around recoveries (PostgreSQL)">
        <Async state={snaps} empty={(d) => d.length === 0}>
          {(d) => (
            <table><thead><tr><th>When</th><th>Reason</th><th>Version</th><th>Revision</th><th>Ready</th><th>Incident</th></tr></thead>
              <tbody>{d.map((s) => <tr key={s.id}><td className="text-xs">{fmtTime(s.captured_at)}</td><td>{s.reason.replace("_", " ")}</td><td>{s.version}</td><td>{s.revision}</td><td>{s.ready_replicas}/{s.desired_replicas}</td><td>{s.incident_id && <IncidentLink id={s.incident_id} />}</td></tr>)}</tbody></table>
          )}
        </Async>
      </Card>
    </div>
  );
}
