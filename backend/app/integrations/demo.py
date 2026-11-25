from app.integrations.http import IntegrationError, JsonClient


class DemoServiceClient(JsonClient):
    source = "demo-service"

    def __init__(self, *args, admin_token: str = "", **kw):
        super().__init__(*args, **kw)
        self.admin_token = admin_token

    def probe(self) -> dict:
        """Direct health + readiness probe. A 503 is a *finding*, not an error."""
        out = {}
        for name in ("health", "ready"):
            try:
                resp = self._request("GET", f"/{name}", ok_statuses=(200, 503), retry=False)
                body = resp.json()
                out[name] = {"status_code": resp.status_code, **body}
            except IntegrationError as exc:
                out[name] = {"status_code": None, "error": exc.as_dict()}
        return out

    def _admin(self, method: str, path: str, json=None):
        return self._request(method, path, json=json, headers={"X-Admin-Token": self.admin_token}, retry=False).json()

    def admin_state(self) -> dict:
        return self._admin("GET", "/admin/state")

    def inject_fault(self, fault: dict) -> dict:
        return self._admin("POST", "/admin/faults", json=fault)

    def reset(self) -> dict:
        return self._admin("POST", "/admin/reset")
