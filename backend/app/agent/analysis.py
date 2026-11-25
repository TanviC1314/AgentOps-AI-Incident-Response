"""Evidence construction and deterministic (rule-based) analysis.

Evidence is derived mechanically from tool results. The deterministic analyser is both the fallback when no
LLM is available and the reference the LLM output is checked against (evidence ids, allowed actions).
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.agent.schemas import Evidence, LLMAnalysis, RecommendedAction, RootCause
from app.agent.tools import ToolResult
from app.config import Settings
from app.models import Alert, Incident
from app.models.enums import ActionType

RECENT_DEPLOY_WINDOW = timedelta(minutes=30)
ERROR_RATIO_THRESHOLD = 0.2
P95_THRESHOLD = 1.0


@dataclass
class Signals:
    alert_names: set[str] = field(default_factory=set)
    alert_start: datetime | None = None
    error_ratio: float | None = None
    p95: float | None = None
    request_rate: float | None = None
    unhealthy_instances: float | None = None
    up: float | None = None
    probe_ready: int | None = None
    pods: list[dict] = field(default_factory=list)
    pods_known: bool = False
    current: dict | None = None  # deployment status
    history: list[dict] = field(default_factory=list)
    error_log_count: int = 0
    error_log_versions: set[str] = field(default_factory=set)
    top_errors: list[dict] = field(default_factory=list)
    previous_resolved_by: list[str] = field(default_factory=list)

    @property
    def pods_not_ready(self) -> list[dict]:
        return [p for p in self.pods if not p["ready"]]

    @property
    def previous_revision(self) -> dict | None:
        if not self.current:
            return None
        return next((h for h in self.history if h["revision"] < self.current["revision"] and h.get("version") != self.current["version"]), None)

    @property
    def current_history_entry(self) -> dict | None:
        return next((h for h in self.history if self.current and h["revision"] == self.current["revision"]), None)

    @property
    def recent_deploy(self) -> bool:
        entry = self.current_history_entry
        if not entry or not self.alert_start or not self.previous_revision:
            return False
        created = datetime.fromisoformat(entry["created_at"])
        return timedelta(0) <= self.alert_start - created <= RECENT_DEPLOY_WINDOW


class EvidenceBuilder:
    def __init__(self):
        self.items: list[Evidence] = []
        self.ids: dict[str, str] = {}
        self.gaps: list[str] = []

    def add(self, key: str, source: str, tool: str, summary: str, data: dict | None = None, confirmed: bool = True) -> str:
        eid = f"E{len(self.items) + 1}"
        self.items.append(Evidence(id=eid, source=source, tool=tool, summary=summary, data=data or {}, confirmed=confirmed))  # type: ignore[arg-type]
        self.ids[key] = eid
        return eid

    def gap(self, tool: str, result: ToolResult) -> None:
        err = result.error or {}
        self.gaps.append(f"{tool}: {err.get('source', '?')} {err.get('code', 'error')} - {err.get('message', '')}")


def build_evidence(incident: Incident, alerts: list[Alert], results: dict[str, ToolResult]) -> tuple[list[Evidence], Signals, list[str], dict[str, str]]:
    eb, sig = EvidenceBuilder(), Signals()
    sig.alert_names = {a.alert_name for a in alerts}
    sig.alert_start = min((a.starts_at for a in alerts), default=None)
    for a in alerts:
        eb.add(f"alert:{a.alert_name}", "alert", "alertmanager_webhook",
               f"Alert {a.alert_name} ({a.severity}) fired at {a.starts_at.isoformat()}: {a.annotations.get('description') or a.annotations.get('summary', '')}",
               {"labels": a.labels, "annotations": a.annotations, "starts_at": a.starts_at.isoformat()})

    r = results.get("get_service_metrics")
    if r and r.ok:
        m = r.data
        sig.error_ratio, sig.p95, sig.request_rate = m["error_ratio"], m["p95_latency_seconds"], m["request_rate_per_s"]
        sig.unhealthy_instances, sig.up = m["unhealthy_instances"], m["up"]
        fmt = lambda v, f: "no data" if v is None else f.format(v)  # noqa: E731
        eb.add("metrics", "prometheus", "get_service_metrics",
               f"Prometheus ({m['window']} window): 5xx ratio {fmt(m['error_ratio'], '{:.1%}')}, p95 latency {fmt(m['p95_latency_seconds'], '{:.2f}s')}, "
               f"{fmt(m['request_rate_per_s'], '{:.1f}')} req/s, unhealthy instances {fmt(m['unhealthy_instances'], '{:.0f}')}, up={fmt(m['up'], '{:.0f}')}, versions {m['versions']}", m)
    elif r:
        eb.gap("get_service_metrics", r)

    r = results.get("get_service_health")
    if r and r.ok:
        probe = r.data["probe"]
        sig.probe_ready = probe.get("ready", {}).get("status_code")
        eb.add("health", "service_probe", "get_service_health",
               f"Direct probe: /health={probe.get('health', {}).get('status_code')}, /ready={sig.probe_ready}", r.data)
    elif r:
        eb.gap("get_service_health", r)

    r = results.get("get_recent_logs")
    if r and r.ok:
        d = r.data
        errs = [e for e in d["entries"] if e.get("level") in ("ERROR", "CRITICAL")]
        sig.error_log_count = len(errs)
        sig.error_log_versions = {e["version"] for e in errs if e.get("version")}
        sig.top_errors = d["top_error_messages"]
        top = "; ".join(f"'{t['message']}' x{t['count']}" for t in d["top_error_messages"][:3]) or "none"
        eb.add("logs", "loki", "get_recent_logs",
               f"Loki: {d['count']} log lines in last {d['window_minutes']}m, levels {d['level_counts']}, {len(errs)} ERROR; top messages: {top}; "
               f"error log versions: {sorted(sig.error_log_versions) or 'n/a'}",
               {"level_counts": d["level_counts"], "top_error_messages": d["top_error_messages"], "sample": [e.get("raw", "")[:300] for e in errs[:3]]})
    elif r:
        eb.gap("get_recent_logs", r)

    r = results.get("get_kubernetes_pods")
    if r and r.ok:
        sig.pods, sig.pods_known = r.data["pods"], True
        eb.add("pods", "kubernetes", "get_kubernetes_pods",
               f"Kubernetes: {sum(p['ready'] for p in sig.pods)}/{len(sig.pods)} pods ready; restarts {[p['restarts'] for p in sig.pods]}; "
               f"images {sorted({p['image'] for p in sig.pods})}", {"pods": sig.pods})
    elif r:
        eb.gap("get_kubernetes_pods", r)

    r = results.get("get_deployment_status")
    if r and r.ok:
        sig.current = r.data
        d = r.data
        eb.add("deployment", "kubernetes", "get_deployment_status",
               f"Deployment {d['name']}: revision {d['revision']} running {d['version']} ({d['ready_replicas']}/{d['desired_replicas']} ready)", d)
    elif r:
        eb.gap("get_deployment_status", r)

    r = results.get("get_deployment_history")
    if r and r.ok:
        sig.history = r.data["revisions"]
        desc = ", ".join(f"rev {h['revision']}={h['version']}@{h['created_at'][:19]}{'*' if h['is_current'] else ''}" for h in sig.history[:5])
        eb.add("history", "kubernetes", "get_deployment_history", f"Rollout history (newest first, * current): {desc}", {"revisions": sig.history[:5]})
        if sig.recent_deploy:
            cur, prev = sig.current_history_entry, sig.previous_revision
            eb.add("recent_deploy", "kubernetes", "get_deployment_history",
                   f"Revision {cur['revision']} ({cur['version']}) was created {cur['created_at'][:19]}, before the first alert at "
                   f"{sig.alert_start.isoformat()[:19]}; previous revision {prev['revision']} ran {prev['version']}", {"current": cur, "previous": prev})
    elif r:
        eb.gap("get_deployment_history", r)

    r = results.get("get_previous_incidents")
    if r and r.ok:
        prev = r.data["incidents"]
        sig.previous_resolved_by = [i["resolution"]["action"] for i in prev if i["status"] == "resolved" and i.get("resolution") and i["resolution"].get("action")]
        eb.add("previous", "postgres", "get_previous_incidents",
               f"{len(prev)} earlier incident(s) for this service; resolved by: {sig.previous_resolved_by or 'n/a'}", {"incidents": prev[:5]})
    elif r:
        eb.gap("get_previous_incidents", r)
    return eb.items, sig, eb.gaps, eb.ids


def deterministic_analysis(sig: Signals, ids: dict[str, str], incident: Incident) -> LLMAnalysis:
    """Rule-based root-cause analysis. Every claim cites evidence ids; inferences are marked as hypotheses."""
    names = sig.alert_names
    ev = lambda *keys: [ids[k] for k in keys if k in ids]  # noqa: E731
    alert_refs = [v for k, v in ids.items() if k.startswith("alert:")]

    high_err = sig.error_ratio > ERROR_RATIO_THRESHOLD if sig.error_ratio is not None else "HighErrorRate" in names
    high_lat = sig.p95 > P95_THRESHOLD if sig.p95 is not None else "HighLatency" in names
    unhealthy = bool((sig.unhealthy_instances or 0) > 0 or (sig.probe_ready not in (None, 200)) or sig.pods_not_ready or "UnhealthyInstances" in names)
    down = bool(sig.up == 0 or (sig.pods_known and sig.pods and not any(p["ready"] for p in sig.pods) and "UnhealthyInstances" not in names) or "ServiceUnavailable" in names)

    symptoms = []
    if sig.error_ratio is not None:
        symptoms.append(f"5xx error ratio {sig.error_ratio:.1%}")
    if sig.p95 is not None:
        symptoms.append(f"p95 latency {sig.p95:.2f}s")
    if sig.probe_ready not in (None, 200):
        symptoms.append(f"/ready probe returns {sig.probe_ready}")
    if sig.pods_not_ready:
        symptoms.append(f"{len(sig.pods_not_ready)} pod(s) not ready")
    if sig.error_log_count:
        symptoms.append(f"{sig.error_log_count} ERROR log lines")
    symptoms = symptoms or [f"alert(s) fired: {', '.join(sorted(names))}"]
    summary = f"{incident.service}: {', '.join(sorted(names))} - " + "; ".join(symptoms)

    prev = sig.previous_revision
    # 1. Faulty release
    if (high_err or down) and sig.recent_deploy and prev:
        cur = sig.current
        support = [
            ("error ratio above threshold", sig.error_ratio is not None and sig.error_ratio > ERROR_RATIO_THRESHOLD, "metrics"),
            ("error logs tagged with the new version", bool(sig.error_log_versions) and sig.error_log_versions <= {cur["version"]}, "logs"),
            ("deployment changed shortly before the alert", True, "recent_deploy"),
        ]
        met = [s for s in support if s[1]]
        confirmed = len(met) == 3
        top = sig.top_errors[0]["message"] if sig.top_errors else None
        return LLMAnalysis(
            incident_summary=summary, observed_symptoms=symptoms,
            root_cause=RootCause(
                description=f"Regression introduced by release {cur['version']} (revision {cur['revision']}), rolled out shortly before the alert"
                            + (f"; errors logged: '{top}'" if top else ""),
                confirmed=confirmed, evidence_refs=ev(*[s[2] for s in met], "deployment") + alert_refs),
            hypotheses=[] if confirmed else ["The error spike may be unrelated to the deployment (correlation, not proven)"],
            confidence="high" if confirmed else ("medium" if len(met) == 2 else "low"),
            recommended_action=RecommendedAction(
                action_type=ActionType.ROLLBACK_DEPLOYMENT, parameters={"target_revision": prev["revision"]},
                rationale=f"Roll back to revision {prev['revision']} ({prev['version']}) which ran before the errors began. Evidence: {', '.join(ev('metrics', 'logs', 'recent_deploy'))}."),
            risk_factors=["rollback restarts pods and briefly reduces capacity", f"reverts {cur['version']} -> {prev['version']}; features in {cur['version']} become unavailable"],
        )
    # 2. Unhealthy instances
    if unhealthy and not high_err:
        scope = "unhealthy" if sig.pods_not_ready else "all"
        confirmed = sig.probe_ready not in (None, 200)
        return LLMAnalysis(
            incident_summary=summary, observed_symptoms=symptoms,
            root_cause=RootCause(
                description="Instance is failing readiness/health checks" + (" (direct /ready probe returned non-200)" if confirmed else ""),
                confirmed=confirmed, evidence_refs=ev("health", "pods", "metrics", "logs") + alert_refs),
            hypotheses=["Exhausted resource inside the running process (e.g. connection pool) - not provable from the collected evidence"],
            confidence="high" if confirmed and sig.pods_not_ready else "medium",
            recommended_action=RecommendedAction(
                action_type=ActionType.RESTART_PODS, parameters={"scope": scope},
                rationale=f"Restart {scope} pod(s); no recent deployment change was found, so the fault is instance-level."),
            risk_factors=["restart briefly interrupts the affected pod(s)"],
        )
    # 3. Latency / errors / downtime without a recent deploy (or no rollback target)
    if high_lat or high_err or down:
        what = "elevated 5xx error rate" if high_err else "high latency" if high_lat else "service unavailable"
        deploy_note = "no recent deployment change correlates with the incident" if not sig.recent_deploy else "no previous revision is available to roll back to"
        return LLMAnalysis(
            incident_summary=summary, observed_symptoms=symptoms,
            root_cause=RootCause(description=f"{what.capitalize()} on the running instance(s); cause not determined ({deploy_note})",
                                 confirmed=False, evidence_refs=ev("metrics", "logs", "deployment") + alert_refs),
            hypotheses=["Instance-level degradation (resource contention or a stuck dependency)", "Capacity shortfall under load (would call for scale-out)"],
            confidence="medium" if sig.error_log_count or sig.p95 else "low",
            recommended_action=RecommendedAction(
                action_type=ActionType.RESTART_PODS, parameters={"scope": "all"},
                rationale=f"Restarting pods clears instance-level state and is the least invasive action available; {deploy_note}."),
            risk_factors=["root cause unconfirmed: restart may not fix the issue", "restart briefly interrupts the pod(s)"],
        )
    # 4. Nothing actionable
    return LLMAnalysis(
        incident_summary=summary, observed_symptoms=symptoms,
        root_cause=RootCause(description="No failing condition is currently observable in metrics, logs or Kubernetes state", confirmed=False,
                             evidence_refs=ev("metrics", "health", "pods") + alert_refs),
        hypotheses=["The condition was transient and has cleared", "Evidence collection was incomplete (see data gaps)"],
        confidence="low",
        recommended_action=RecommendedAction(action_type=ActionType.NONE, parameters={}, rationale="No safe automated action is justified by the evidence; manual review required."),
        risk_factors=[],
    )


def default_verification(settings: Settings, expected_version: str | None) -> dict:
    return {"max_error_ratio": settings.verify_max_error_rate, "max_p95_latency_seconds": settings.verify_max_p95_latency_seconds,
            "require_all_pods_ready": True, "expected_version": expected_version}


__all__ = ["build_evidence", "deterministic_analysis", "Signals", "default_verification", "timezone"]
