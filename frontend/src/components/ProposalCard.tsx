import { useState } from "react";
import { errorMessage, post } from "../api/client";
import type { Proposal } from "../api/types";
import { Badge, Button, IncidentLink, fmtTime } from "./ui";

const RISK: Record<string, string> = {
  high: "bg-red-500/15 text-red-300 border-red-500/40", medium: "bg-amber-500/15 text-amber-300 border-amber-500/40", low: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
};

export default function ProposalCard({ p, onDecided }: { p: Proposal; onDecided: () => void }) {
  const [who, setWho] = useState(() => localStorage.getItem("agentops.user") ?? "operator");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function decide(kind: "approve" | "reject") {
    setBusy(true);
    setErr(null);
    try {
      localStorage.setItem("agentops.user", who);
      await post(`/recovery/proposals/${p.id}/${kind}`, { decided_by: who, reason: reason || null });
      onDecided();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-ink-600 bg-ink-800 p-4" data-testid="proposal-card">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-slate-100">{p.action_type}</span>
        <Badge text={`risk ${p.risk}`} tone={RISK[p.risk]} />
        <Badge text={p.status} />
        <span className="ml-auto text-xs text-slate-500">{fmtTime(p.created_at)} · incident <IncidentLink id={p.incident_id} /></span>
      </div>
      <pre className="mt-2 overflow-x-auto rounded bg-ink-900 p-2 text-xs">{JSON.stringify(Object.fromEntries(Object.entries(p.parameters).filter(([k]) => !k.startsWith("_"))), null, 2)}</pre>
      <p className="mt-2 text-sm text-slate-300">{p.rationale}</p>
      {p.parameters._current && (
        <p className="mt-1 text-xs text-slate-400">Current: revision {p.parameters._current.revision} ({p.parameters._current.version}){p.parameters.target_version ? ` → target: revision ${p.parameters.target_revision} (${p.parameters.target_version})` : ""}</p>
      )}
      {p.validation && (
        <details className="mt-2 text-xs text-slate-400">
          <summary className="cursor-pointer">Safety validation: {p.validation.ok ? "passed" : "FAILED"} ({p.validation.checks.length} checks)</summary>
          <ul className="mt-1 space-y-0.5">{p.validation.checks.map((c) => <li key={c.name}>{c.ok ? "✓" : "✗"} {c.name}: {c.detail}</li>)}</ul>
        </details>
      )}
      {p.approvals && p.approvals.length > 0 && (
        <ul className="mt-2 text-xs text-slate-400">{p.approvals.map((a) => <li key={a.id}>{a.decision} by {a.decided_by} at {fmtTime(a.created_at)}{a.reason ? ` - "${a.reason}"` : ""}</li>)}</ul>
      )}
      {p.status === "pending_approval" && (
        <div className="mt-3 space-y-2 border-t border-ink-600 pt-3">
          <div className="flex flex-wrap gap-2">
            <input aria-label="Your name" value={who} onChange={(e) => setWho(e.target.value)} className="w-40 rounded border border-ink-600 bg-ink-900 px-2 py-1 text-xs" />
            <input aria-label="Reason" placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} className="min-w-[12rem] flex-1 rounded border border-ink-600 bg-ink-900 px-2 py-1 text-xs" />
          </div>
          <div className="flex gap-2">
            <Button tone="primary" disabled={busy || !who.trim()} onClick={() => decide("approve")}>Approve & execute</Button>
            <Button tone="danger" disabled={busy || !who.trim()} onClick={() => decide("reject")}>Reject</Button>
          </div>
          {err && <div role="alert" className="text-xs text-red-300">{err}</div>}
        </div>
      )}
    </div>
  );
}
