"""HTTP helper with timeouts, bounded retries and structured errors for external systems."""
import logging
import time

import httpx

logger = logging.getLogger(__name__)


class IntegrationError(Exception):
    """A dependency (Prometheus, Loki, Kubernetes, demo-service) failed. Never fabricate data on this."""

    def __init__(self, source: str, message: str, code: str = "unavailable"):
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
        self.code = code

    def as_dict(self) -> dict:
        return {"source": self.source, "code": self.code, "message": self.message}


class JsonClient:
    source = "http"

    def __init__(self, base_url: str, timeout: float = 5.0, retries: int = 2, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.transport = transport

    def _request(self, method: str, path: str, *, params=None, json=None, headers=None, ok_statuses=(200,), retry: bool = True):
        attempts = (self.retries if retry else 0) + 1
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                    resp = client.request(method, self.base_url + path, params=params, json=json, headers=headers)
                if resp.status_code in ok_statuses:
                    return resp
                if resp.status_code < 500:  # client errors are not retried
                    raise IntegrationError(self.source, f"HTTP {resp.status_code}: {resp.text[:200]}", "bad_response")
                last = IntegrationError(self.source, f"HTTP {resp.status_code}", "server_error")
            except IntegrationError as exc:
                if exc.code == "bad_response":
                    raise
                last = exc
            except httpx.HTTPError as exc:
                last = IntegrationError(self.source, f"{type(exc).__name__}: {exc}", "unavailable")
            if attempt < attempts - 1:
                time.sleep(0.2 * (attempt + 1))
        assert last is not None
        logger.warning("integration call failed", extra={"source": self.source, "error": str(last)})
        raise last if isinstance(last, IntegrationError) else IntegrationError(self.source, str(last))

    def get_json(self, path: str, params=None, **kw):
        return self._request("GET", path, params=params, **kw).json()
