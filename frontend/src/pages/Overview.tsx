import { Link } from "react-router-dom";
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { get } from "../api/client";
import type { Incident, Overview as OverviewT, Page } from "../api/types";
import { IncidentRow } from "../components/domain";
import { Async, Card, EmptyState, Stat, fmtPct, fmtSeconds, fmtTime } from "../components/ui";
import { useAsync } from "../api/useAsync";

const SEV_COLORS: Record<string, string> = { critical: "#ef4444", high: "#f97316", medium: "#eab308", low: "#94a3b8" };

export default function Overview() {
  const stats = useAsync(() => get<OverviewT>("/stats/overview"), [], 5000);
  const active = useAsync(async () => {
    const [a, b, c, d] = await Promise.all(["open", "investigating", "awaiting_approval", "recovering"].map((s) => get<Page<Incident>>("/incidents", { status: s, page_size: 20 })));
    return [...a.items, ...b.items, ...c.items, ...d.items];
  }, [], 5000);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-white">Overview</h1>
      <Async state={stats}>
        {(s) => (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Total incidents" value={s.total_incidents} />
              <Stat label="Active incidents" value={s.active_incidents} />
              <Stat label="Resolved" value={s.resolved_incidents} />
              <Stat label="Failed" value={s.failed_incidents} />
              <Stat label="Recovery success rate" value={fmtPct(s.recovery_success_rate)} />
              <Stat label="Avg investigation" value={fmtSeconds(s.avg_investigation_seconds)} />
              <Stat label="Avg recovery" value={fmtSeconds(s.avg_recovery_seconds)} />
              <Stat label="Tool failure rate" value={fmtPct(s.tool_failure_rate)} hint={`${s.tool_calls_total} agent tool calls`} />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <Card title="Severity distribution">
                {s.total_incidents === 0 ? <EmptyState title="No incidents yet" hint="Trigger a failure from Service Health; Prometheus will raise the alert." /> : (
                  <div className="h-56">
                    <ResponsiveContainer>
                      <PieChart>
                        <Pie data={Object.entries(s.by_severity).map(([name, value]) => ({ name, value }))} dataKey="value" nameKey="name" label>
                          {Object.keys(s.by_severity).map((k) => <Cell key={k} fill={SEV_COLORS[k] ?? "#64748b"} />)}
                        </Pie>
                        <Tooltip />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
              <Card title="Incidents by status">
                {s.total_incidents === 0 ? <EmptyState title="No incidents yet" /> : (
                  <div className="h-56">
                    <ResponsiveContainer>
                      <BarChart data={Object.entries(s.by_status).map(([name, value]) => ({ name: name.replace("_", " "), value }))}>
                        <XAxis dataKey="name" stroke="#94a3b8" fontSize={11} />
                        <YAxis allowDecimals={false} stroke="#94a3b8" fontSize={11} />
                        <Tooltip />
                        <Bar dataKey="value" fill="#38bdf8" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
            </div>
            <Card title="Recent agent investigations" right={<Link className="text-xs text-sky-400" to="/investigations">View all</Link>}>
              {s.recent_executions.length === 0 ? <EmptyState title="No investigations yet" /> : (
                <table>
                  <thead><tr><th>Started</th><th>Mode</th><th>Status</th><th /></tr></thead>
                  <tbody>{s.recent_executions.map((e) => (
                    <tr key={e.id}><td>{fmtTime(e.started_at)}</td><td>{e.mode}</td><td>{e.status}</td><td><Link className="text-sky-400" to={`/investigations/${e.id}`}>Details</Link></td></tr>
                  ))}</tbody>
                </table>
              )}
            </Card>
          </>
        )}
      </Async>
      <Card title="Active incidents" right={<Link className="text-xs text-sky-400" to="/incidents">All incidents</Link>}>
        <Async state={active} empty={(d) => d.length === 0}>
          {(items) => <table><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead><tbody>{items.map((i) => <IncidentRow key={i.id} i={i} />)}</tbody></table>}
        </Async>
      </Card>
    </div>
  );
}
