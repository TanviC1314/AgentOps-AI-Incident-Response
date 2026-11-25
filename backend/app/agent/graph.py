"""LangGraph investigation workflow.

The graph topology is fixed in code: the LLM cannot add tools, skip steps or change incident state. Nodes call tools
through `call_tool` (persisted + metered) and only the `analyze` node optionally consults the LLM.

inspect_alert -> collect_metrics -> collect_logs -> collect_kubernetes -> check_health -> previous_incidents
              -> analyze -> (propose | finish)
"""
import logging
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agent import analysis as A
from app.agent.llm import LLMUnavailable, analyze_with_llm, build_user_prompt, sanitize
from app.agent.schemas import Diagnosis, RiskAssessment
from app.agent.tools import ToolContext, call_tool
from app.config import get_settings
from app.integrations.registry import get_clients
from app.models import AgentExecution
from app.models.enums import ActionType
from app.recovery.validation import assess_risk, validate_action
from app.repositories.incidents import IncidentRepository

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    diagnosis: dict | None
    proposal: dict | None
    llm_error: str | None
    completed_nodes: list[str]


def build_graph(ctx: ToolContext, execution: AgentExecution, llm_transport=None):
    settings = get_settings()
    repo = IncidentRepository(ctx.db)

    def mark(node: str, state: AgentState) -> AgentState:
        done = [*state.get("completed_nodes", []), node]
        execution.current_node = node
        execution.state = {"completed_nodes": done, "tools_called": list(ctx.results)}  # persistent agent state
        ctx.db.commit()
        return {"completed_nodes": done}

    def inspect_alert(state: AgentState):
        alerts = repo.alerts_for(ctx.incident.id)
        repo.add_event(ctx.incident.id, "investigation_step", f"Inspecting {len(alerts)} alert(s)", {"alerts": [a.alert_name for a in alerts]})
        return mark("inspect_alert", state)

    def collect_metrics(state: AgentState):
        call_tool(ctx, "get_service_metrics", window="1m")
        return mark("collect_metrics", state)

    def collect_logs(state: AgentState):
        call_tool(ctx, "get_recent_logs", minutes=10, limit=100)
        return mark("collect_logs", state)

    def collect_kubernetes(state: AgentState):
        call_tool(ctx, "get_kubernetes_pods")
        call_tool(ctx, "get_deployment_status")
        call_tool(ctx, "get_deployment_history")
        return mark("collect_kubernetes", state)

    def check_health(state: AgentState):
        call_tool(ctx, "get_service_health")
        return mark("check_health", state)

    def previous_incidents(state: AgentState):
        call_tool(ctx, "get_previous_incidents")
        return mark("previous_incidents", state)

    def analyze(state: AgentState):
        alerts = repo.alerts_for(ctx.incident.id)
        evidence, sig, gaps, ids = A.build_evidence(ctx.incident, alerts, ctx.results)

        det = A.deterministic_analysis(sig, ids, ctx.incident)
        analysis, mode, fallback = det, "deterministic", None
        err = None
        if settings.llm_enabled:
            try:
                prompt = build_user_prompt(det.incident_summary, evidence, sig.history, gaps)
                candidate = sanitize(analyze_with_llm(settings, prompt, llm_transport), {e.id for e in evidence})
                # Safety gate: the LLM's action must pass the same validation as any other proposal.
                check = validate_action(candidate.recommended_action.action_type.value, candidate.recommended_action.parameters,
                                        get_clients().cluster, settings) if candidate.recommended_action.action_type != ActionType.NONE else None
                if check is not None and not check.ok:
                    err = "LLM proposed an action that failed safety validation: " + "; ".join(check.errors)
                else:
                    analysis, mode = candidate, "llm"
            except LLMUnavailable as exc:
                err = str(exc)
        else:
            err = "no LLM_API_KEY configured; using deterministic rule-based analysis"
        fallback = err if mode == "deterministic" else None

        action = analysis.recommended_action
        expected = action.parameters.get("target_revision")
        expected_version = next((h["version"] for h in sig.history if h["revision"] == expected), None) if expected else None
        cur_replicas = sig.current["desired_replicas"] if sig.current else None
        risk_level = assess_risk(action.action_type, action.parameters, cur_replicas)
        diagnosis = Diagnosis(
            incident_summary=analysis.incident_summary, observed_symptoms=analysis.observed_symptoms, evidence=evidence,
            root_cause=analysis.root_cause, hypotheses=analysis.hypotheses, confidence=analysis.confidence,
            recommended_action=action, risk_assessment=RiskAssessment(level=risk_level, factors=analysis.risk_factors),
            verification_criteria=A.default_verification(settings, expected_version), data_gaps=gaps,
            analysis_mode=mode, fallback_reason=fallback,  # type: ignore[arg-type]
        )
        execution.mode = mode
        execution.diagnosis = diagnosis.model_dump(mode="json")
        execution.fallback_reason = fallback
        repo.add_event(ctx.incident.id, "diagnosis", f"Diagnosis ({mode}, confidence {analysis.confidence}): {analysis.root_cause.description}",
                       {"confirmed": analysis.root_cause.confirmed, "action": action.action_type.value, "evidence_refs": analysis.root_cause.evidence_refs})
        out = mark("analyze", state)
        out["diagnosis"] = execution.diagnosis
        out["llm_error"] = fallback
        return out

    def route_after_analyze(state: AgentState) -> str:
        action = (state.get("diagnosis") or {}).get("recommended_action", {}).get("action_type")
        return "finish" if action in (None, ActionType.NONE.value) else "propose"

    def propose(state: AgentState):
        d = state["diagnosis"]
        a = d["recommended_action"]
        res = call_tool(ctx, "propose_recovery_action", action_type=a["action_type"], parameters=a["parameters"],
                        rationale=a["rationale"], verification_criteria=d["verification_criteria"])
        out = mark("propose", state)
        out["proposal"] = res.data if res.ok else {"error": res.error}
        return out

    def finish(state: AgentState):
        return mark("finish", state)

    g = StateGraph(AgentState)
    for name, fn in [("inspect_alert", inspect_alert), ("collect_metrics", collect_metrics), ("collect_logs", collect_logs),
                     ("collect_kubernetes", collect_kubernetes), ("check_health", check_health),
                     ("previous_incidents", previous_incidents), ("analyze", analyze), ("propose", propose), ("finish", finish)]:
        g.add_node(name, fn)
    g.set_entry_point("inspect_alert")
    for a, b in [("inspect_alert", "collect_metrics"), ("collect_metrics", "collect_logs"), ("collect_logs", "collect_kubernetes"),
                 ("collect_kubernetes", "check_health"), ("check_health", "previous_incidents"), ("previous_incidents", "analyze")]:
        g.add_edge(a, b)
    g.add_conditional_edges("analyze", route_after_analyze, {"propose": "propose", "finish": "finish"})
    g.add_edge("propose", END)
    g.add_edge("finish", END)
    return g.compile()
