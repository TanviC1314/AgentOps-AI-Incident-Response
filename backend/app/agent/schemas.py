"""Structured investigation output. Evidence is produced by code from tool results, never by the LLM."""
from typing import Literal

from pydantic import BaseModel, Field

from app.models.enums import ActionType, RiskLevel


class Evidence(BaseModel):
    id: str  # E1, E2 ...
    source: Literal["alert", "prometheus", "loki", "kubernetes", "service_probe", "postgres"]
    tool: str
    summary: str
    data: dict = Field(default_factory=dict)
    confirmed: bool = True  # observed directly from a tool (as opposed to inferred)


class RootCause(BaseModel):
    description: str
    confirmed: bool  # False => hypothesis
    evidence_refs: list[str] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    action_type: ActionType
    parameters: dict = Field(default_factory=dict)
    rationale: str = ""


class RiskAssessment(BaseModel):
    level: RiskLevel
    factors: list[str] = Field(default_factory=list)


class Diagnosis(BaseModel):
    incident_summary: str
    observed_symptoms: list[str]
    evidence: list[Evidence]
    root_cause: RootCause
    hypotheses: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    recommended_action: RecommendedAction
    risk_assessment: RiskAssessment
    verification_criteria: dict
    data_gaps: list[str] = Field(default_factory=list)  # evidence that could not be collected
    analysis_mode: Literal["llm", "deterministic"]
    fallback_reason: str | None = None


class LLMAnalysis(BaseModel):
    """What the LLM is allowed to produce. It cites evidence ids; it cannot add evidence or change state."""

    incident_summary: str
    observed_symptoms: list[str] = Field(default_factory=list)
    root_cause: RootCause
    hypotheses: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]
    recommended_action: RecommendedAction
    risk_factors: list[str] = Field(default_factory=list)
