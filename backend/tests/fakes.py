"""Test doubles. FakeCluster is a *test* stand-in for Kubernetes; production code never uses it."""
import copy

import httpx

from app.integrations.demo import DemoServiceClient
from app.integrations.http import IntegrationError
from app.integrations.loki import LokiClient
from app.integrations.prometheus import PrometheusClient
from app.integrations.registry import Clients


class FakeCluster:
    namespace = "agentops-demo"
    deployment = "demo-service"

    def __init__(self, history=None, pods=None, replicas=1):
        self.history = history or [
            {"revision": 2, "image": "agentops/demo-service:2.0.0", "version": "2.0.0", "created_at": "2026-01-01T10:00:00+00:00",
             "replicas": 1, "ready_replicas": 1, "change_cause": "bad", "is_current": True},
            {"revision": 1, "image": "agentops/demo-service:1.0.0", "version": "1.0.0", "created_at": "2026-01-01T09:00:00+00:00",
             "replicas": 0, "ready_replicas": 0, "change_cause": None, "is_current": False},
        ]
        self.replicas = replicas
        self.pods = pods if pods is not None else [self._pod("demo-service-abc", True)]
        self.calls: list[tuple] = []
        self.fail_on: set[str] = set()
        self.heals_on_restart = True
        self.heals_on_rollback = True
        self.healed = False

    @staticmethod
    def _pod(name, ready, version="2.0.0"):
        return {"name": name, "phase": "Running", "ready": ready, "restarts": 0, "image": f"agentops/demo-service:{version}",
                "version": version, "waiting_reason": None, "node": "n1", "started_at": None, "deleting": False}

    def _check(self, name):
        if name in self.fail_on:
            raise IntegrationError("kubernetes", "simulated failure", "unavailable")

    def current(self):
        return next(h for h in self.history if h["is_current"])

    def list_pods(self):
        self._check("list_pods")
        return copy.deepcopy(self.pods)

    def get_deployment(self):
        self._check("get_deployment")
        cur = self.current()
        ready = sum(1 for p in self.pods if p["ready"])
        return {"name": "demo-service", "namespace": "agentops-demo", "desired_replicas": self.replicas, "ready_replicas": ready,
                "updated_replicas": self.replicas, "available_replicas": ready, "image": cur["image"], "version": cur["version"],
                "revision": cur["revision"], "generation": 3, "observed_generation": 3, "strategy": "RollingUpdate", "conditions": []}

    def rollout_history(self):
        self._check("rollout_history")
        return copy.deepcopy(self.history)

    def restart_pods(self, scope):
        self._check("restart_pods")
        self.calls.append(("restart_pods", scope))
        if self.heals_on_restart:
            self.healed = True
        targets = [p["name"] for p in self.pods if scope == "all" or not p["ready"]]
        return targets

    def scale(self, replicas):
        self._check("scale")
        self.calls.append(("scale", replicas))
        self.replicas = replicas
        return self.get_deployment()

    def rollback(self, target_revision):
        self._check("rollback")
        self.calls.append(("rollback", target_revision))
        for h in self.history:
            h["is_current"] = h["revision"] == target_revision
        if self.heals_on_rollback:
            self.healed = True
        return self.get_deployment()

    def apply_release(self, image, env, change_cause):
        self.calls.append(("apply_release", image))
        return self.get_deployment()

    def wait_for_rollout(self, timeout_s):
        self._check("wait_for_rollout")
        return {**self.get_deployment(), "rollout_complete": True}


class FakeDemo:
    def __init__(self):
        self.faults = []
        self.resets = 0

    def probe(self):
        return {"health": {"status_code": 200, "status": "ok"}, "ready": {"status_code": 200, "status": "ready"}}

    def admin_state(self):
        return {"version": "1.0.0"}

    def inject_fault(self, fault):
        self.faults.append(fault)
        return {"version": "1.0.0", **fault}

    def reset(self):
        self.resets += 1
        return {"version": "1.0.0", "unhealthy": False}


def prom_transport(values: dict[str, float | None] | None = None, versions=("2.0.0",), fail=False, handler=None):
    """Prometheus HTTP API double. `values` maps a substring of the PromQL to a scalar."""
    values = values or {}

    def _handle(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("connection refused")
        if handler:
            return handler(request)
        q = request.url.params.get("query", "")
        if "demo_service_build_info" in q:
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {"version": v}, "value": [0, "1"]} for v in versions]}})
        for needle, val in values.items():
            if needle in q:
                if val is None:
                    return httpx.Response(200, json={"status": "success", "data": {"result": []}})
                return httpx.Response(200, json={"status": "success", "data": {"result": [{"metric": {}, "value": [0, str(val)]}]}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})

    return httpx.MockTransport(_handle)


def loki_transport(lines=None, fail=False):
    import time

    lines = lines or []

    def _handle(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("connection refused")
        now = time.time_ns()
        values = [[str(now - i * 1000), line] for i, line in enumerate(lines)]
        return httpx.Response(200, json={"status": "success", "data": {"result": [{"stream": {"service": "demo-service"}, "values": values}]}})

    return httpx.MockTransport(_handle)


HEALTHY_METRICS = {
    "rate(demo_http_requests_total{status": 0.0, "histogram_quantile": 0.05, "sum(demo_http_active_requests)": 1,
    "sum(demo_service_healthy)": 1, "count(demo_service_healthy == 0)": 0, "min(up": 1,
}
ERROR_LOGS = [
    '{"timestamp":"t","service":"demo-service","level":"ERROR","message":"Unhandled KeyError \'discount_code\' in order pricing (regression in release 2.0.0)","request_id":"r1","version":"2.0.0","exception":"Traceback..."}',
    '{"timestamp":"t","service":"demo-service","level":"ERROR","message":"GET /api/orders/3 -> 500","request_id":"r2","version":"2.0.0"}',
    '{"timestamp":"t","service":"demo-service","level":"INFO","message":"GET /api/orders/4 -> 200","request_id":"r3","version":"2.0.0"}',
]


def make_clients(prom=None, loki=None, cluster=None, demo=None) -> Clients:
    return Clients(
        prometheus=PrometheusClient("http://prom", timeout=1, retries=0, transport=prom or prom_transport()),
        loki=LokiClient("http://loki", timeout=1, retries=0, transport=loki or loki_transport(ERROR_LOGS)),
        demo=demo or FakeDemo(),  # type: ignore[arg-type]
        cluster=cluster or FakeCluster(),  # type: ignore[arg-type]
    )


__all__ = ["FakeCluster", "FakeDemo", "make_clients", "prom_transport", "loki_transport", "DemoServiceClient"]
