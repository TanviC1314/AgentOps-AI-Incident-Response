"""Application settings, loaded from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "agentops-backend"
    environment: str = "local"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    database_url: str = "sqlite:///./agentops.db"
    redis_url: str = "memory://"

    prometheus_url: str = "http://localhost:9090"
    loki_url: str = "http://localhost:3100"
    demo_service_url: str = "http://localhost:8001"
    demo_admin_token: str = "local-admin-token"

    kubernetes_enabled: bool = False
    kubernetes_namespace: str = "agentops-demo"
    kubernetes_deployment: str = "demo-service"
    kubeconfig_path: str = ""
    demo_service_name: str = "demo-service"
    demo_baseline_version: str = "1.0.0"
    demo_bad_version: str = "2.0.0"
    simulation_enabled: bool = True

    llm_provider: str = "openai"  # openai (any OpenAI-compatible endpoint) | anthropic
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_timeout_seconds: float = 30.0

    max_replicas: int = 5
    min_replicas: int = 1
    require_approval_for_risk: str = Field(default="medium,high")
    external_call_timeout_seconds: float = 5.0
    external_call_max_retries: int = 2

    # Verification
    verify_max_error_rate: float = 0.05
    verify_max_p95_latency_seconds: float = 0.5
    verify_timeout_seconds: float = 150.0
    verify_poll_interval_seconds: float = 5.0
    verify_settle_seconds: float = 30.0
    verify_required_consecutive_passes: int = 2
    rollout_timeout_seconds: float = 120.0

    worker_enabled: bool = True
    otel_exporter_otlp_endpoint: str = ""

    @property
    def approval_risk_levels(self) -> set[str]:
        return {r.strip().lower() for r in self.require_approval_for_risk.split(",") if r.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
