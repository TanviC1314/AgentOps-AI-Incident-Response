"""Static checks on infrastructure files (no cluster needed)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _docs(folder: str):
    for path in sorted((ROOT / folder).glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if doc:
                yield path.name, doc


def test_manifest_folder_contains_only_cluster_resources():
    """`kubectl apply -f k8s/` must not pick up tool configuration such as the kind cluster file."""
    for name, doc in _docs("k8s"):
        assert not doc["apiVersion"].startswith("kind.x-k8s.io"), name


def test_namespaced_resources_stay_in_the_demo_namespace():
    for name, doc in _docs("k8s"):
        if doc["kind"] == "Namespace":
            assert doc["metadata"]["name"] == "agentops-demo", name
        else:
            assert doc["metadata"]["namespace"] == "agentops-demo", name


def test_recovery_rbac_is_namespace_scoped_and_minimal():
    docs = [d for _, d in _docs("k8s")]
    assert not [d for d in docs if d["kind"] in ("ClusterRole", "ClusterRoleBinding")]
    role = next(d for d in docs if d["kind"] == "Role")
    for rule in role["rules"]:
        assert "*" not in rule["verbs"] + rule["resources"]
        assert not {"secrets", "pods/exec"} & set(rule["resources"])


def test_setup_scripts_point_at_the_existing_cluster_config():
    assert (ROOT / "kind" / "kind-config.yaml").is_file()
    for script in ("kind-setup.ps1", "kind-setup.sh"):
        assert "kind/kind-config.yaml" in (ROOT / "scripts" / script).read_text(encoding="utf-8")
