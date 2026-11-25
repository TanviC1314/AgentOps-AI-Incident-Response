import { useState } from "react";
import { get } from "../api/client";
import type { AuditLog, Page } from "../api/types";
import { useAsync } from "../api/useAsync";
import { Async, Card, IncidentLink, Pagination, fmtTime } from "../components/ui";

export default function AuditLogs() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const state = useAsync(() => get<Page<AuditLog>>("/audit-logs", { page, page_size: 25, action: action || undefined }), [page, action], 6000);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Audit logs</h1>
      <Card>
        <input aria-label="Filter by action" placeholder="Filter by exact action, e.g. recovery.approved" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}
          className="mb-3 w-full max-w-sm rounded border border-ink-600 bg-ink-900 px-2 py-1 text-sm" />
        <Async state={state} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <div className="overflow-x-auto"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Incident</th><th>Details</th></tr></thead>
                <tbody>{d.items.map((a) => (
                  <tr key={a.id}><td className="whitespace-nowrap text-xs">{fmtTime(a.created_at)}</td><td>{a.actor}</td><td className="font-mono text-xs">{a.action}</td>
                    <td>{a.incident_id && <IncidentLink id={a.incident_id} />}</td><td className="max-w-md truncate font-mono text-xs text-slate-400">{a.details ? JSON.stringify(a.details) : ""}</td></tr>))}</tbody></table></div>
              <Pagination page={d.page} pageSize={d.page_size} total={d.total} onChange={setPage} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}
