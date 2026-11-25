import { useState } from "react";
import { errorMessage, get, post } from "../api/client";
import type { Pod } from "../api/types";
import { useAsync } from "../api/useAsync";
import { Async, Badge, Button, Card, EmptyState, ErrorState, Stat, fmtPct } from "../components/ui";

interface Metrics { error_ratio: number | null; p95_latency_seconds: number | null; request_rate_per_s: number | null; active_requests: number | null; healthy_instances: number | null; unhealthy_instances: number | null; up: number | null; memory_bytes: number | null; cpu_cores: number | null; versions: string[]; window: string }
interface Health { healthy: boolean; probe: Record<string, { status_code: number | null }>; prometheus: Record<string, any>; kubernetes: Record<string, any> }
interface Logs { count: number; level_counts: Record<string, number>; entries: { ts_ns: string; level?: string; message?: string; version?: string; raw: string }[] }

const SCENARIOS: [string, string][] = [
  ["high_latency", "High latency"], ["high_error_rate", "High error rate"], ["unhealthy_instances", "Unhealthy instance"], ["failed_deployment", "Failed deployment (Kubernetes)"],
];

export default function ServiceHealth() {
  const health = useAsync(() => get<Health>("/service/health"), [], 5000);
  const metrics = useAsync(() => get<Metrics>("/service/metrics", { window: "1m" }), [], 5000);
  const pods = useAsync(() => get<{ pods: Pod[] }>("/kubernetes/pods"), [], 5000);
  const logs = useAsync(() => get<Logs>("/logs", { minutes: 10, limit: 40 }), [], 7000);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(path: string, okText: string) {
    setBusy(true);
    try { await post(path); setNote({ ok: true, text: okText }); } catch (e) { setNote({ ok: false, text: errorMessage(e) }); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Service health</h1>
      <Async state={health}>
        {(h) => (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Overall" value={h.healthy ? "Healthy" : "Unhealthy"} />
            <Stat label="/ready probe" value={h.probe.ready?.status_code ?? "unreachable"} />
            <Stat label="Pods ready" value={h.kubernetes.error ? "n/a" : `${h.kubernetes.pods_ready}/${h.kubernetes.pods_total}`} hint={h.kubernetes.error?.message} />
            <Stat label="Prometheus up" value={h.prometheus.error ? "n/a" : String(h.prometheus.up)} hint={h.prometheus.error?.message} />
          </div>
        )}
      </Async>

      <Card title="Metrics (Prometheus, 1m window)">
        <Async state={metrics}>
          {(m) => (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="5xx error ratio" value={fmtPct(m.error_ratio)} /><Stat label="p95 latency" value={m.p95_latency_seconds == null ? "n/a" : `${m.p95_latency_seconds.toFixed(2)}s`} />
              <Stat label="Request rate" value={m.request_rate_per_s == null ? "n/a" : `${m.request_rate_per_s.toFixed(1)}/s`} /><Stat label="Active requests" value={m.active_requests ?? "n/a"} />
              <Stat label="Memory" value={m.memory_bytes == null ? "n/a" : `${(m.memory_bytes / 1e6).toFixed(0)} MB`} /><Stat label="CPU" value={m.cpu_cores == null ? "n/a" : `${m.cpu_cores.toFixed(2)} cores`} />
              <Stat label="Deployed version(s)" value={m.versions.join(", ") || "n/a"} /><Stat label="Unhealthy instances" value={m.unhealthy_instances ?? "n/a"} />
            </div>
          )}
        </Async>
      </Card>

      <Card title="Failure simulation" right={<span className="text-xs text-slate-500">Changes real service behaviour. Alerts come from Prometheus rules (takes ~1 min).</span>}>
        <div className="flex flex-wrap gap-2">
          {SCENARIOS.map(([key, text]) => <Button key={key} disabled={busy} onClick={() => run(`/simulations/${key}`, `${text} triggered. Watch Prometheus → Alertmanager → Incidents.`)}>{text}</Button>)}
          <Button tone="primary" disabled={busy} onClick={() => run("/simulations/reset", "Service reset to normal behaviour")}>Reset to normal</Button>
        </div>
        {note && <div role="status" className={`mt-3 text-sm ${note.ok ? "text-emerald-300" : "text-red-300"}`}>{note.text}</div>}
      </Card>

      <Card title="Kubernetes pods">
        <Async state={pods} empty={(d) => d.pods.length === 0}>
          {(d) => <table><thead><tr><th>Pod</th><th>Phase</th><th>Ready</th><th>Restarts</th><th>Version</th></tr></thead>
            <tbody>{d.pods.map((p) => <tr key={p.name}><td className="font-mono text-xs">{p.name}</td><td>{p.phase}</td><td><Badge text={p.ready ? "ready" : p.waiting_reason ?? "not ready"} /></td><td>{p.restarts}</td><td>{p.version}</td></tr>)}</tbody></table>}
        </Async>
      </Card>

      <Card title="Recent logs (Loki)">
        {logs.error && !logs.data ? <ErrorState message={logs.error} onRetry={logs.reload} /> : (
          <Async state={logs} empty={(d) => d.entries.length === 0}>
            {(d) => (
              <>
                <div className="mb-2 text-xs text-slate-400">{Object.entries(d.level_counts).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
                <div className="max-h-72 overflow-auto rounded bg-ink-900 p-2 font-mono text-xs">
                  {d.entries.map((e) => <div key={e.ts_ns} className={e.level === "ERROR" ? "text-red-300" : e.level === "WARNING" ? "text-amber-300" : "text-slate-400"}>[{e.level}] {e.message ?? e.raw}</div>)}
                </div>
              </>
            )}
          </Async>
        )}
        {!logs.data && !logs.error && !logs.loading && <EmptyState title="No logs" />}
      </Card>
    </div>
  );
}
