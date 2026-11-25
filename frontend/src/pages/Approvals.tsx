import { get } from "../api/client";
import type { Page, Proposal } from "../api/types";
import { useAsync } from "../api/useAsync";
import ProposalCard from "../components/ProposalCard";
import { Async, Card, EmptyState } from "../components/ui";

export default function Approvals() {
  const pending = useAsync(() => get<Page<Proposal>>("/recovery/proposals", { status: "pending_approval", page_size: 50 }), [], 4000);
  const history = useAsync(() => get<Page<Proposal>>("/recovery/proposals", { page_size: 20 }), [], 8000);
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-white">Recovery approvals</h1>
      <p className="text-sm text-slate-400">Medium/high-risk actions never run without a human decision. Approving queues execution, followed by automatic verification.</p>
      <Async state={pending} empty={(d) => d.items.length === 0}>
        {(d) => <div className="space-y-3">{d.items.map((p) => <ProposalCard key={p.id} p={p} onDecided={() => { pending.reload(); history.reload(); }} />)}</div>}
      </Async>
      {pending.data && pending.data.items.length === 0 && <EmptyState title="No proposals awaiting approval" />}
      <Card title="Proposal history">
        <Async state={history} empty={(d) => d.items.length === 0}>
          {(d) => <div className="space-y-3">{d.items.filter((p) => p.status !== "pending_approval").map((p) => <ProposalCard key={p.id} p={p} onDecided={history.reload} />)}</div>}
        </Async>
      </Card>
    </div>
  );
}
