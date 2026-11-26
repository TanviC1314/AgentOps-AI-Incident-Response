# Creates the local kind cluster, deploys the demo workload into the dedicated namespace and generates a
# kubeconfig for the RESTRICTED recovery service account. Requires: docker, kind, kubectl.
#
# ErrorActionPreference stays "Continue": Windows PowerShell 5.1 turns redirected stderr from native tools
# into terminating errors under "Stop" (e.g. kind's "No kind clusters found."). Exit codes are checked instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Assert-LastExit([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit code $LASTEXITCODE)" }
}

foreach ($tool in "docker", "kind", "kubectl") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "'$tool' is required but not installed." }
}

if (-not (kind get clusters 2>$null | Select-String -SimpleMatch "agentops")) {
    kind create cluster --config kind/kind-config.yaml
    Assert-LastExit "kind create cluster"
}
$ctx = "kind-agentops"

docker build -t agentops/demo-service:1.0.0 demo-service
Assert-LastExit "docker build"
docker tag agentops/demo-service:1.0.0 agentops/demo-service:2.0.0   # 2.0.0 is the "faulty release" used by the failed_deployment scenario
Assert-LastExit "docker tag"
kind load docker-image agentops/demo-service:1.0.0 agentops/demo-service:2.0.0 --name agentops
Assert-LastExit "kind load docker-image"

kubectl --context $ctx apply -f k8s/
Assert-LastExit "kubectl apply"
kubectl --context $ctx -n agentops-demo rollout status deployment/demo-service --timeout=120s
Assert-LastExit "demo-service rollout"

# --- restricted kubeconfig (service-account token, never admin credentials)
$server = kubectl --context $ctx config view --minify -o jsonpath='{.clusters[0].cluster.server}'
Assert-LastExit "read cluster server"
$port = ([uri]$server).Port
$ca = kubectl --context $ctx config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}'
Assert-LastExit "read cluster CA"
$token = kubectl --context $ctx -n agentops-demo create token agentops-recovery --duration=168h
Assert-LastExit "create service account token"
New-Item -ItemType Directory -Force .kube | Out-Null
@"
apiVersion: v1
kind: Config
clusters:
  - name: agentops
    cluster:
      server: https://host.docker.internal:$port
      certificate-authority-data: $ca
users:
  - name: agentops-recovery
    user:
      token: $token
contexts:
  - name: agentops
    context: {cluster: agentops, user: agentops-recovery, namespace: agentops-demo}
current-context: agentops
"@ | Set-Content -Encoding ascii .kube/agentops-recovery.kubeconfig

# --- prove the RBAC restrictions ("auth can-i" exits 1 for "no", so only the answer is compared)
$sa = "system:serviceaccount:agentops-demo:agentops-recovery"
Write-Host "`nRBAC checks for $sa"
$checks = @(
    @("delete pods (agentops-demo)", "delete pods -n agentops-demo", "yes"),
    @("patch deployments (agentops-demo)", "patch deployments/demo-service -n agentops-demo", "yes"),
    @("delete pods (kube-system)", "delete pods -n kube-system", "no"),
    @("list nodes", "list nodes", "no"),
    @("get secrets (agentops-demo)", "get secrets -n agentops-demo", "no"),
    @("delete deployments (agentops-demo)", "delete deployments -n agentops-demo", "no")
)
$failed = 0
foreach ($c in $checks) {
    $parts = $c[1].Split(" ")
    $got = kubectl --context $ctx auth can-i --as=$sa @parts 2>$null
    $mark = if ($got -eq $c[2]) { "OK  " } else { $failed++; "FAIL" }
    Write-Host ("  [{0}] {1}: {2} (expected {3})" -f $mark, $c[0], $got, $c[2])
}
if ($failed -gt 0) { throw "$failed RBAC check(s) did not match the expected restrictions" }
Write-Host "`nDone. Start AgentOps against the cluster with:"
Write-Host "  docker compose -f docker-compose.yml -f docker-compose.k8s.yml up -d --build"
