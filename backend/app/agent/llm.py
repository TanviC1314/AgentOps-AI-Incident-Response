"""Configurable LLM provider (OpenAI-compatible chat completions, or Anthropic messages) with structured output.

The LLM reasons over evidence that code collected. Any failure raises LLMUnavailable so the caller can fall back to
the deterministic analyser; an LLM problem can never block incident handling.
"""
import json
import logging
import re

import httpx
from pydantic import ValidationError

from app.agent.schemas import Evidence, LLMAnalysis
from app.config import Settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an SRE incident analyst inside an automated incident-response platform.
Rules:
- Use ONLY the evidence provided (each item has an id like E3). Never invent logs, metrics, pod states, versions or events.
- Cite evidence ids in root_cause.evidence_refs. Mark root_cause.confirmed=true only if directly supported by cited evidence; otherwise false (hypothesis).
- You may recommend exactly one action: restart_pods {"scope": "unhealthy"|"all"}, rollback_deployment {"target_revision": <int from rollout history>},
  scale_deployment {"replicas": <int>}, or none {}. No other actions or commands exist.
- Respond with a single JSON object with keys: incident_summary, observed_symptoms[], root_cause{description,confirmed,evidence_refs[]},
  hypotheses[], confidence ("low"|"medium"|"high"), recommended_action{action_type,parameters,rationale}, risk_factors[].
"""


class LLMUnavailable(Exception):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in response")
    return json.loads(m.group(0))


def build_user_prompt(incident_summary: str, evidence: list[Evidence], revisions: list[dict], gaps: list[str]) -> str:
    return json.dumps({
        "incident": incident_summary,
        "evidence": [{"id": e.id, "source": e.source, "summary": e.summary} for e in evidence],
        "rollout_history": [{"revision": r["revision"], "version": r["version"], "is_current": r["is_current"]} for r in revisions],
        "data_gaps": gaps,
    }, indent=1)


def _post(settings: Settings, transport: httpx.BaseTransport | None, user_prompt: str) -> str:
    if settings.llm_provider == "anthropic":
        url = (settings.llm_base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        headers = {"x-api-key": settings.llm_api_key, "anthropic-version": "2023-06-01"}
        body = {"model": settings.llm_model, "max_tokens": 1500, "temperature": 0, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}]}
    else:
        url = (settings.llm_base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
        body = {"model": settings.llm_model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]}
    with httpx.Client(timeout=settings.llm_timeout_seconds, transport=transport) as client:
        resp = client.post(url, headers=headers, json=body)
    if resp.status_code != 200:
        raise LLMUnavailable(f"LLM HTTP {resp.status_code}")
    data = resp.json()
    if settings.llm_provider == "anthropic":
        return "".join(b.get("text", "") for b in data["content"])
    return data["choices"][0]["message"]["content"]


def analyze_with_llm(settings: Settings, user_prompt: str, transport: httpx.BaseTransport | None = None) -> LLMAnalysis:
    if not settings.llm_enabled:
        raise LLMUnavailable("no LLM_API_KEY configured")
    last: Exception | None = None
    for attempt in range(2):  # bounded retries
        try:
            return LLMAnalysis.model_validate(_extract_json(_post(settings, transport, user_prompt)))
        except (httpx.HTTPError, LLMUnavailable) as exc:
            last = LLMUnavailable(f"LLM request failed: {type(exc).__name__}: {exc}")
        except (ValueError, ValidationError, KeyError) as exc:
            last = LLMUnavailable(f"LLM returned invalid structured output: {type(exc).__name__}")
        logger.warning("llm attempt failed", extra={"attempt": attempt + 1, "error": str(last)})
    raise last or LLMUnavailable("unknown LLM failure")


def sanitize(analysis: LLMAnalysis, valid_ids: set[str]) -> LLMAnalysis:
    """Drop citations that do not exist; a 'confirmed' claim without real evidence becomes a hypothesis."""
    refs = [r for r in analysis.root_cause.evidence_refs if r in valid_ids]
    confirmed = analysis.root_cause.confirmed and bool(refs)
    if analysis.root_cause.confirmed and not confirmed:
        analysis.hypotheses.append("LLM claimed a confirmed root cause without citing valid evidence; downgraded to hypothesis")
    analysis.root_cause.evidence_refs = refs
    analysis.root_cause.confirmed = confirmed
    if not confirmed and analysis.confidence == "high":
        analysis.confidence = "medium"
    return analysis
