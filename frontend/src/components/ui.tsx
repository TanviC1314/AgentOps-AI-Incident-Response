import { ReactNode } from "react";
import { Link } from "react-router-dom";

const STATUS: Record<string, string> = {
  open: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  investigating: "bg-violet-500/15 text-violet-300 border-violet-500/40",
  awaiting_approval: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  recovering: "bg-blue-500/15 text-blue-300 border-blue-500/40",
  resolved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
};
const SEVERITY: Record<string, string> = {
  critical: "bg-red-600/20 text-red-300 border-red-500/50",
  high: "bg-orange-500/15 text-orange-300 border-orange-500/40",
  medium: "bg-yellow-500/15 text-yellow-300 border-yellow-500/40",
  low: "bg-slate-500/15 text-slate-300 border-slate-500/40",
};

export const label = (s: string) => s.replace(/_/g, " ");

export function Badge({ text, tone }: { text: string; tone?: string }) {
  return <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium capitalize ${tone ?? "bg-slate-600/20 text-slate-300 border-slate-500/40"}`}>{label(text)}</span>;
}
export const StatusBadge = ({ status }: { status: string }) => <Badge text={status} tone={STATUS[status]} />;
export const SeverityBadge = ({ severity }: { severity: string }) => <Badge text={severity} tone={SEVERITY[severity]} />;

export function Card({ title, children, right, className = "" }: { title?: string; children: ReactNode; right?: ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-ink-600 bg-ink-800 p-4 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-slate-100">{title}</h2>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label: l, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-ink-600 bg-ink-800 p-4" data-testid={`stat-${l}`}>
      <div className="text-xs uppercase tracking-wide text-slate-400">{l}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-50">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export const Loading = ({ text = "Loading..." }: { text?: string }) => (
  <div role="status" className="flex items-center gap-2 py-6 text-sm text-slate-400">
    <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-500 border-t-transparent" />
    {text}
  </div>
);

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-lg border border-red-500/40 bg-red-500/10 p-4 text-sm text-red-200">
      <div className="font-medium">Something went wrong</div>
      <div className="mt-1 text-red-300/90">{message}</div>
      {onRetry && <button onClick={onRetry} className="mt-3 rounded bg-red-500/20 px-3 py-1 text-xs hover:bg-red-500/30">Retry</button>}
    </div>
  );
}

export const EmptyState = ({ title, hint }: { title: string; hint?: string }) => (
  <div className="rounded-lg border border-dashed border-ink-600 p-8 text-center">
    <div className="text-sm text-slate-300">{title}</div>
    {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
  </div>
);

/** Renders loading / error / empty / content for an async resource. */
export function Async<T>({ state, empty, children }: { state: { data: T | null; error: string | null; loading: boolean; reload: () => void }; empty?: (d: T) => boolean | undefined; children: (d: T) => ReactNode }) {
  if (state.error && !state.data) return <ErrorState message={state.error} onRetry={state.reload} />;
  if (state.loading && !state.data) return <Loading />;
  if (!state.data) return null;
  if (empty?.(state.data)) return <EmptyState title="Nothing to show yet" />;
  return <>{children(state.data)}</>;
}

export function Pagination({ page, pageSize, total, onChange }: { page: number; pageSize: number; total: number; onChange: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="mt-3 flex items-center justify-between text-xs text-slate-400">
      <span>{total} total</span>
      <div className="flex items-center gap-2">
        <button disabled={page <= 1} onClick={() => onChange(page - 1)} className="rounded border border-ink-600 px-2 py-1 disabled:opacity-40">Prev</button>
        <span>Page {page} / {pages}</span>
        <button disabled={page >= pages} onClick={() => onChange(page + 1)} className="rounded border border-ink-600 px-2 py-1 disabled:opacity-40">Next</button>
      </div>
    </div>
  );
}

export const fmtTime = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "-");
export const fmtSeconds = (s: number | null | undefined) => (s == null ? "n/a" : s < 90 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`);
export const fmtPct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);

export const IncidentLink = ({ id, children }: { id: string; children?: ReactNode }) => (
  <Link className="text-sky-400 hover:underline" to={`/incidents/${id}`}>{children ?? id.slice(0, 8)}</Link>
);

export function Button({ children, onClick, disabled, tone = "default", type = "button" }: { children: ReactNode; onClick?: () => void; disabled?: boolean; tone?: "default" | "danger" | "primary"; type?: "button" | "submit" }) {
  const tones = { default: "border-ink-600 bg-ink-700 hover:bg-ink-600", primary: "border-emerald-500/50 bg-emerald-600/80 hover:bg-emerald-600", danger: "border-red-500/50 bg-red-600/70 hover:bg-red-600" };
  return <button type={type} onClick={onClick} disabled={disabled} className={`rounded border px-3 py-1.5 text-xs font-medium text-slate-100 disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone]}`}>{children}</button>;
}
