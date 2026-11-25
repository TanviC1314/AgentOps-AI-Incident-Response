import { NavLink, Outlet } from "react-router-dom";

const NAV = [
  ["/", "Overview"], ["/incidents", "Incidents"], ["/investigations", "Agent Investigations"], ["/approvals", "Recovery Approvals"],
  ["/service-health", "Service Health"], ["/deployments", "Deployment History"], ["/audit", "Audit Logs"],
] as const;

export default function Layout() {
  return (
    <div className="min-h-screen md:flex">
      <aside className="border-b border-ink-600 bg-ink-800 md:min-h-screen md:w-60 md:border-b-0 md:border-r">
        <div className="px-4 py-4">
          <div className="text-lg font-semibold text-white">AgentOps</div>
          <div className="text-xs text-slate-400">AI incident response</div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:overflow-visible" aria-label="Main">
          {NAV.map(([to, text]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => `whitespace-nowrap rounded px-3 py-2 text-sm ${isActive ? "bg-ink-600 text-white" : "text-slate-300 hover:bg-ink-700"}`}>{text}</NavLink>
          ))}
        </nav>
      </aside>
      <main className="flex-1 p-4 md:p-6"><div className="mx-auto max-w-6xl"><Outlet /></div></main>
    </div>
  );
}
