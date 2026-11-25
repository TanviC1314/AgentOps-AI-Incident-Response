"""Prometheus HTTP API client. All operational metrics come from here, never from the LLM."""
from app.integrations.http import IntegrationError, JsonClient


class PrometheusClient(JsonClient):
    source = "prometheus"

    def query(self, promql: str) -> list[dict]:
        body = self.get_json("/api/v1/query", params={"query": promql})
        if body.get("status") != "success":
            raise IntegrationError(self.source, f"query failed: {body.get('error')}", "bad_response")
        return body["data"]["result"]

    def scalar(self, promql: str) -> float | None:
        """First sample value, or None if the series is absent/NaN (i.e. no data - never assumed 0)."""
        result = self.query(promql)
        if not result:
            return None
        value = float(result[0]["value"][1])
        return None if value != value else value  # NaN -> None

    def firing_alerts(self) -> list[dict]:
        body = self.get_json("/api/v1/alerts")
        return [a for a in body["data"]["alerts"] if a.get("state") == "firing"]

    def service_metrics(self, window: str = "1m", version: str | None = None) -> dict:
        vsel = f',version="{version}"' if version else ""
        req = f'demo_http_requests_total{{path=~"/api/.*"{vsel}}}'
        err = f'demo_http_requests_total{{status=~"5..",path=~"/api/.*"{vsel}}}'
        p95_sel = f'demo_http_request_duration_seconds_bucket{{path=~"/api/.*"}}'
        queries = {
            "request_rate_per_s": f"sum(rate({req}[{window}]))",
            "error_rate_per_s": f"sum(rate({err}[{window}]))",
            "error_ratio": f"sum(rate({err}[{window}])) / sum(rate({req}[{window}]))",
            "p95_latency_seconds": f"histogram_quantile(0.95, sum(rate({p95_sel}[{window}])) by (le))",
            "active_requests": "sum(demo_http_active_requests)",
            "healthy_instances": "sum(demo_service_healthy)",
            "unhealthy_instances": "count(demo_service_healthy == 0) or vector(0)",
            "up": 'min(up{job="demo-service"})',
            "memory_bytes": "sum(process_resident_memory_bytes{job=\"demo-service\"})",
            "cpu_cores": f'sum(rate(process_cpu_seconds_total{{job="demo-service"}}[{window}]))',
        }
        out: dict = {"window": window, "source": "prometheus"}
        for key, q in queries.items():
            out[key] = self.scalar(q)  # IntegrationError propagates -> structured tool error
        versions = self.query("demo_service_build_info == 1")
        out["versions"] = sorted({r["metric"].get("version", "") for r in versions})
        return out
