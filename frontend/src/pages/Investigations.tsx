import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get } from "../api/client";
import type { AgentExecution, AgentExecutionDetail, Page } from "../api/types";
import { useAsync } from "../api/useAsync";
import { DiagnosisView, ToolCalls } from "../components/domain";
import { Async, Badge, Card, IncidentLink, Pagination, fmtTime } from "../components/ui";

export function Investigations() {
  const [page, setPage] = useState(1);
  const state = useAsync(() => get<Page<AgentExecution>>("/agent/executions", { page, page_size: 15 }), [page], 5000);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Agent investigations</h1>
      <Card>
        <Async state={state} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <table><thead><tr><th>Started</th><th>Incident</th><th>Mode</th><th>Status</th><th>Step</th><th /></tr></thead>
                <tbody>{d.items.map((e) => (
                  <tr key={e.id}><td>{fmtTime(e.started_at)}</td><td><IncidentLink id={e.incident_id} /></td>
                    <td><Badge text={e.mode} /></td><td>{e.status}</td><td className="text-xs text-slate-400">{e.current_node}</td>
                    <td><Link className="text-sky-400" to={`/investigations/${e.id}`}>Open</Link></td></tr>))}</tbody></table>
              <Pagination page={d.page} pageSize={d.page_size} total={d.total} onChange={setPage} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}

export default function InvestigationDetail() {
  const { id = "" } = useParams();
  const state = useAsync(() => get<AgentExecutionDetail>(`/agent/executions/${id}`), [id], 4000);
  return (
    <div className="space-y-4">
      <Link to="/investigations" className="text-xs text-sky-400">← Investigations</Link>
      <Async state={state}>
        {(e) => (
          <>
            <h1 className="text-xl font-semibold text-white">Investigation <span className="font-mono text-base text-slate-400">{e.id.slice(0, 8)}</span></h1>
            <div className="text-xs text-slate-500">Incident <IncidentLink id={e.incident_id} /> · {e.status} · started {fmtTime(e.started_at)} · finished {fmtTime(e.finished_at)}</div>
            {e.error && <div role="alert" className="text-sm text-red-300">Error: {e.error}</div>}
            <Card title="Agent tool calls"><ToolCalls calls={e.tool_executions} /></Card>
            <Card title="Structured diagnosis">{e.diagnosis ? <DiagnosisView d={e.diagnosis} /> : <div className="text-sm text-slate-400">No diagnosis yet (current step: {e.current_node ?? "starting"}).</div>}</Card>
          </>
        )}
      </Async>
    </div>
  );
}
