import { useState } from "react";
import { get } from "../api/client";
import type { Incident, Page } from "../api/types";
import { useAsync } from "../api/useAsync";
import { IncidentRow } from "../components/domain";
import { Async, Card, Pagination } from "../components/ui";

const STATUSES = ["open", "investigating", "awaiting_approval", "recovering", "resolved", "failed"];
const SEVERITIES = ["critical", "high", "medium", "low"];
const PAGE_SIZE = 15;

export default function Incidents() {
  const [status, setStatus] = useState("");
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);
  const state = useAsync(
    () => get<Page<Incident>>("/incidents", { status: status || undefined, severity: severity || undefined, page, page_size: PAGE_SIZE }),
    [status, severity, page], 5000,
  );
  const sel = "rounded border border-ink-600 bg-ink-900 px-2 py-1 text-sm";
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-white">Incidents</h1>
      <Card>
        <div className="mb-3 flex flex-wrap gap-3">
          <label className="text-xs text-slate-400">Status
            <select aria-label="Status filter" className={`ml-2 ${sel}`} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
              <option value="">All</option>{STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </select>
          </label>
          <label className="text-xs text-slate-400">Severity
            <select aria-label="Severity filter" className={`ml-2 ${sel}`} value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }}>
              <option value="">All</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
        <Async state={state} empty={(d) => d.items.length === 0}>
          {(d) => (
            <>
              <div className="overflow-x-auto"><table><thead><tr><th>Incident</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead>
                <tbody>{d.items.map((i) => <IncidentRow key={i.id} i={i} />)}</tbody></table></div>
              <Pagination page={d.page} pageSize={d.page_size} total={d.total} onChange={setPage} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}
