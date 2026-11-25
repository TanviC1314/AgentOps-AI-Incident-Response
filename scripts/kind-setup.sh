#!/usr/bin/env bash
# Linux/macOS equivalent of kind-setup.ps1. Requires: docker, kind, kubectl.
set -euo pipefail
cd "$(dirname "$0")/.."
for t in docker kind kubectl; do command -v "$t" >/dev/null || { echo "'$t' is required"; exit 1; }; done

kind get clusters 2>/dev/null | grep -qx agentops || kind create cluster --config k8s/kind-config.yaml
CTX=kind-agentops

docker build -t agentops/demo-service:1.0.0 demo-service
docker tag agentops/demo-service:1.0.0 agentops/demo-service:2.0.0
kind load docker-image agentops/demo-service:1.0.0 agentops/demo-service:2.0.0 --name agentops

kubectl --context $CTX apply -f k8s/
kubectl --context $CTX -n agentops-demo rollout status deployment/demo-service --timeout=120s

SERVER=$(kubectl --context $CTX config view --minify -o jsonpath='{.clusters[0].cluster.server}')
PORT=${SERVER##*:}
CA=$(kubectl --context $CTX config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')
TOKEN=$(kubectl --context $CTX -n agentops-demo create token agentops-recovery --duration=168h)
mkdir -p .kube
cat > .kube/agentops-recovery.kubeconfig <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: agentops
    cluster:
      server: https://host.docker.internal:${PORT}
      certificate-authority-data: ${CA}
users:
  - name: agentops-recovery
    user:
      token: ${TOKEN}
contexts:
  - name: agentops
    context: {cluster: agentops, user: agentops-recovery, namespace: agentops-demo}
current-context: agentops
EOF

SA=system:serviceaccount:agentops-demo:agentops-recovery
echo "RBAC checks for $SA"
check() { got=$(kubectl --context $CTX auth can-i --as=$SA "${@:2}" 2>/dev/null || true); echo "  $1: $got"; }
check "delete pods (agentops-demo)" delete pods -n agentops-demo
check "delete pods (kube-system) [expect no]" delete pods -n kube-system
check "list nodes [expect no]" list nodes
check "get secrets [expect no]" get secrets -n agentops-demo
echo "Next: docker compose -f docker-compose.yml -f docker-compose.k8s.yml up -d --build"
