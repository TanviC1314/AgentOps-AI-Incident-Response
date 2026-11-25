"""Kubernetes access, hard-restricted to ONE namespace and ONE deployment.

The backend authenticates with the restricted `agentops-recovery` service account (see k8s/rbac.yaml),
so even a bug here cannot touch resources outside the demo namespace. This module additionally
refuses to operate on anything but the configured deployment.
"""
import copy
import logging
import time
from datetime import datetime, timezone
from typing import Protocol

from app.integrations.http import IntegrationError

logger = logging.getLogger(__name__)
REVISION_ANN = "deployment.kubernetes.io/revision"
CHANGE_CAUSE_ANN = "kubernetes.io/change-cause"


class ClusterClient(Protocol):
    namespace: str
    deployment: str

    def list_pods(self) -> list[dict]: ...
    def get_deployment(self) -> dict: ...
    def rollout_history(self) -> list[dict]: ...
    def restart_pods(self, scope: str) -> list[str]: ...
    def scale(self, replicas: int) -> dict: ...
    def rollback(self, target_revision: int) -> dict: ...
    def apply_release(self, image: str, env: dict[str, str], change_cause: str) -> dict: ...
    def wait_for_rollout(self, timeout_s: float) -> dict: ...


def version_from_image(image: str | None) -> str | None:
    if not image or ":" not in image.rsplit("/", 1)[-1]:
        return None
    return image.rsplit(":", 1)[1]


class UnavailableCluster:
    """Used when Kubernetes is disabled/unreachable: every call is a structured error, never fake data."""

    def __init__(self, namespace: str, deployment: str, reason: str):
        self.namespace, self.deployment, self.reason = namespace, deployment, reason

    def _fail(self, *a, **k):
        raise IntegrationError("kubernetes", self.reason, "unavailable")

    list_pods = get_deployment = rollout_history = restart_pods = scale = rollback = apply_release = wait_for_rollout = _fail


class KubernetesCluster:
    def __init__(self, namespace: str, deployment: str, kubeconfig_path: str = ""):
        from kubernetes import client, config

        if kubeconfig_path:
            config.load_kube_config(config_file=kubeconfig_path)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
        self.namespace = namespace
        self.deployment = deployment
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self._client = client

    # -- helpers
    def _wrap(self, fn, *a, **k):
        from kubernetes.client.exceptions import ApiException

        try:
            return fn(*a, **k)
        except ApiException as exc:
            code = "forbidden" if exc.status == 403 else "not_found" if exc.status == 404 else "api_error"
            raise IntegrationError("kubernetes", f"{exc.status} {exc.reason}", code) from exc
        except Exception as exc:  # connection refused, timeouts...
            raise IntegrationError("kubernetes", f"{type(exc).__name__}: {exc}", "unavailable") from exc

    def _dep(self):
        return self._wrap(self.apps.read_namespaced_deployment, self.deployment, self.namespace, _request_timeout=8)

    def _selector(self, dep) -> str:
        return ",".join(f"{k}={v}" for k, v in dep.spec.selector.match_labels.items())

    # -- reads
    def list_pods(self) -> list[dict]:
        dep = self._dep()
        pods = self._wrap(self.core.list_namespaced_pod, self.namespace, label_selector=self._selector(dep), _request_timeout=8)
        out = []
        for p in pods.items:
            statuses = p.status.container_statuses or []
            ready = bool(statuses) and all(s.ready for s in statuses)
            waiting = next((s.state.waiting.reason for s in statuses if s.state and s.state.waiting), None)
            out.append({
                "name": p.metadata.name,
                "phase": p.status.phase,
                "ready": ready,
                "restarts": sum(s.restart_count for s in statuses),
                "image": statuses[0].image if statuses else p.spec.containers[0].image,
                "version": version_from_image(p.spec.containers[0].image),
                "waiting_reason": waiting,
                "node": p.spec.node_name,
                "started_at": p.status.start_time.isoformat() if p.status.start_time else None,
                "deleting": p.metadata.deletion_timestamp is not None,
            })
        return sorted(out, key=lambda x: x["name"])

    def get_deployment(self) -> dict:
        dep = self._dep()
        image = dep.spec.template.spec.containers[0].image
        st = dep.status
        return {
            "name": dep.metadata.name,
            "namespace": self.namespace,
            "desired_replicas": dep.spec.replicas,
            "ready_replicas": st.ready_replicas or 0,
            "updated_replicas": st.updated_replicas or 0,
            "available_replicas": st.available_replicas or 0,
            "image": image,
            "version": version_from_image(image),
            "revision": int((dep.metadata.annotations or {}).get(REVISION_ANN, 0)),
            "generation": dep.metadata.generation,
            "observed_generation": st.observed_generation,
            "strategy": dep.spec.strategy.type if dep.spec.strategy else None,
            "conditions": [{"type": c.type, "status": c.status, "reason": c.reason} for c in (st.conditions or [])],
        }

    def _replica_sets(self, dep):
        rss = self._wrap(self.apps.list_namespaced_replica_set, self.namespace, label_selector=self._selector(dep), _request_timeout=8)
        owned = [r for r in rss.items if any(o.kind == "Deployment" and o.name == self.deployment for o in (r.metadata.owner_references or []))]
        return owned

    def rollout_history(self) -> list[dict]:
        dep = self._dep()
        current = int((dep.metadata.annotations or {}).get(REVISION_ANN, 0))
        hist = []
        for rs in self._replica_sets(dep):
            ann = rs.metadata.annotations or {}
            rev = int(ann.get(REVISION_ANN, 0))
            image = rs.spec.template.spec.containers[0].image
            hist.append({
                "revision": rev, "image": image, "version": version_from_image(image),
                "created_at": rs.metadata.creation_timestamp.isoformat(),
                "replicas": rs.status.replicas or 0, "ready_replicas": rs.status.ready_replicas or 0,
                "change_cause": ann.get(CHANGE_CAUSE_ANN), "is_current": rev == current,
            })
        return sorted(hist, key=lambda h: h["revision"], reverse=True)

    # -- writes (predefined, namespace-scoped)
    def restart_pods(self, scope: str) -> list[str]:
        pods = self.list_pods()
        targets = [p["name"] for p in pods if scope == "all" or not p["ready"]]
        for name in targets:
            self._wrap(self.core.delete_namespaced_pod, name, self.namespace, _request_timeout=8)
        return targets

    def scale(self, replicas: int) -> dict:
        self._wrap(self.apps.patch_namespaced_deployment_scale, self.deployment, self.namespace, {"spec": {"replicas": replicas}})
        return self.get_deployment()

    def rollback(self, target_revision: int) -> dict:
        dep = self._dep()
        rs = next((r for r in self._replica_sets(dep) if int((r.metadata.annotations or {}).get(REVISION_ANN, -1)) == target_revision), None)
        if rs is None:
            raise IntegrationError("kubernetes", f"revision {target_revision} not found", "not_found")
        template = self._client.ApiClient().sanitize_for_serialization(rs.spec.template)
        template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
        patch = [
            {"op": "replace", "path": "/spec/template", "value": template},
            {"op": "add", "path": "/metadata/annotations/kubernetes.io~1change-cause", "value": f"agentops rollback to revision {target_revision}"},
        ]
        self._wrap(self.apps.patch_namespaced_deployment, self.deployment, self.namespace, patch)
        return self.get_deployment()

    def apply_release(self, image: str, env: dict[str, str], change_cause: str) -> dict:
        """Used only by the failure simulator to roll out a (bad) release to the demo deployment."""
        dep = self._dep()
        container = copy.deepcopy(dep.spec.template.spec.containers[0])
        existing = {e.name: e for e in (container.env or [])}
        env_list = [{"name": e.name, "value": e.value} for e in existing.values() if e.name not in env and e.value is not None]
        env_list += [{"name": k, "value": v} for k, v in env.items()]
        body = {"metadata": {"annotations": {CHANGE_CAUSE_ANN: change_cause}},
                "spec": {"template": {"spec": {"containers": [{"name": container.name, "image": image, "env": env_list}]}}}}
        self._wrap(self.apps.patch_namespaced_deployment, self.deployment, self.namespace, body)
        return self.get_deployment()

    def wait_for_rollout(self, timeout_s: float) -> dict:
        deadline = time.monotonic() + timeout_s
        last: dict = {}
        while time.monotonic() < deadline:
            last = self.get_deployment()
            pods = self.list_pods()
            settled = (
                last["observed_generation"] is not None and last["observed_generation"] >= last["generation"]
                and last["updated_replicas"] == last["desired_replicas"] == last["ready_replicas"] == last["available_replicas"]
                and len(pods) == last["desired_replicas"] and all(p["ready"] and not p["deleting"] for p in pods)
            )
            if settled:
                return {**last, "rollout_complete": True}
            time.sleep(2)
        return {**last, "rollout_complete": False}


def build_cluster(settings) -> ClusterClient:
    if not settings.kubernetes_enabled:
        return UnavailableCluster(settings.kubernetes_namespace, settings.kubernetes_deployment, "kubernetes integration disabled (KUBERNETES_ENABLED=false)")
    try:
        return KubernetesCluster(settings.kubernetes_namespace, settings.kubernetes_deployment, settings.kubeconfig_path)
    except Exception as exc:
        logger.warning("kubernetes client init failed", exc_info=True)
        return UnavailableCluster(settings.kubernetes_namespace, settings.kubernetes_deployment, f"client init failed: {type(exc).__name__}: {exc}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
