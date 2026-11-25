"""demo-service: a small order API that simulates a workload running in a company environment.

It produces *real* Prometheus metrics and structured JSON logs. Failure scenarios genuinely change
its behaviour (latency, 5xx responses, readiness) so that Prometheus rules fire for real.
"""
import asyncio
import json
import logging
import os
import random
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field

SERVICE = os.getenv("SERVICE_NAME", "demo-service")
VERSION = os.getenv("APP_VERSION", "1.0.0")
BAD_RELEASE = os.getenv("BAD_RELEASE", "false").lower() == "true"  # simulates a faulty deployment
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "local-admin-token")
SELF_TRAFFIC_RPS = float(os.getenv("SELF_TRAFFIC_RPS", "5"))
BAD_RELEASE_ERROR_RATE = 0.6

# ---------------------------------------------------------------- logging
from contextvars import ContextVar  # noqa: E402

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
incident_id_var: ContextVar[str | None] = ContextVar("incident_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "service": SERVICE,
            "level": record.levelname,
            "request_id": request_id_var.get(),
            "incident_id": incident_id_var.get(),
            "version": VERSION,
            "message": record.getMessage(),
        }
        for k in ("path", "status", "duration_ms", "error"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(SERVICE)

# ---------------------------------------------------------------- metrics
REQUESTS = Counter("demo_http_requests_total", "HTTP requests", ["method", "path", "status", "version"])
ERRORS = Counter("demo_http_request_errors_total", "HTTP 5xx responses", ["path", "version"])
LATENCY = Histogram(
    "demo_http_request_duration_seconds", "HTTP request latency", ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
ACTIVE = Gauge("demo_http_active_requests", "In-flight requests")
HEALTHY = Gauge("demo_service_healthy", "1 when the instance reports healthy")
BUILD = Gauge("demo_service_build_info", "Deployment version", ["version"])
FAULT = Gauge("demo_fault_active", "Simulated fault currently active", ["type"])
BUILD.labels(VERSION).set(1)


# ---------------------------------------------------------------- fault state
class FaultState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.latency_ms = 0
        self.error_rate = 0.0
        self.unhealthy = False
        self.update_gauges()

    @property
    def effective_error_rate(self) -> float:
        return max(self.error_rate, BAD_RELEASE_ERROR_RATE if BAD_RELEASE else 0.0)

    def update_gauges(self):
        HEALTHY.set(0 if self.unhealthy else 1)
        FAULT.labels("latency").set(1 if self.latency_ms else 0)
        FAULT.labels("errors").set(1 if self.error_rate else 0)
        FAULT.labels("unhealthy").set(1 if self.unhealthy else 0)
        FAULT.labels("bad_release").set(1 if BAD_RELEASE else 0)

    def as_dict(self) -> dict:
        return {
            "version": VERSION, "latency_ms": self.latency_ms, "error_rate": self.error_rate,
            "unhealthy": self.unhealthy, "bad_release": BAD_RELEASE,
        }


faults = FaultState()
ORDERS = {i: {"id": i, "customer": f"customer-{i % 7}", "total": round(10 + i * 1.5, 2), "status": "paid"} for i in range(1, 51)}


# ---------------------------------------------------------------- app
async def _self_traffic(app: FastAPI):
    """Steady synthetic traffic through the real HTTP stack so metrics always have data."""
    if SELF_TRAFFIC_RPS <= 0:
        return
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://self") as client:
        while True:
            try:
                path = "/api/orders" if random.random() < 0.3 else f"/api/orders/{random.randint(1, 60)}"
                await client.get(path, headers={"X-Request-ID": f"synthetic-{uuid.uuid4().hex[:8]}"})
            except Exception:
                pass
            await asyncio.sleep(1.0 / SELF_TRAFFIC_RPS)


async def _health_logger():
    while True:
        await asyncio.sleep(5)
        if faults.unhealthy:
            log.error("Readiness check failing: database connection pool exhausted", extra={"error": "pool_exhausted"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("service starting", extra={"bad_release": BAD_RELEASE})
    faults.update_gauges()
    tasks = [asyncio.create_task(_self_traffic(app)), asyncio.create_task(_health_logger())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="demo-service", version=VERSION, lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    if request.url.path in ("/metrics", "/live"):
        return await call_next(request)
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t1 = request_id_var.set(rid)
    t2 = incident_id_var.set(request.headers.get("x-incident-id"))
    route_path = request.url.path
    if route_path.startswith("/api/orders/"):
        route_path = "/api/orders/{order_id}"
    ACTIVE.inc()
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        elapsed = time.perf_counter() - start
        ACTIVE.dec()
        REQUESTS.labels(request.method, route_path, str(status), VERSION).inc()
        LATENCY.labels(route_path).observe(elapsed)
        if status >= 500:
            ERRORS.labels(route_path, VERSION).inc()
        if not route_path.startswith("/admin") and route_path not in ("/health", "/ready"):
            level = logging.ERROR if status >= 500 else (logging.WARNING if elapsed > 1 else logging.INFO)
            log.log(level, f"{request.method} {request.url.path} -> {status}",
                    extra={"path": route_path, "status": status, "duration_ms": round(elapsed * 1000, 1)})
        request_id_var.reset(t1)
        incident_id_var.reset(t2)


async def _apply_faults(what: str):
    if faults.latency_ms:
        await asyncio.sleep(faults.latency_ms / 1000)
    if random.random() < faults.effective_error_rate:
        if BAD_RELEASE and faults.error_rate == 0:
            msg = f"Unhandled KeyError 'discount_code' in order pricing (regression in release {VERSION})"
            try:
                {}["discount_code"]  # a genuine exception, so the traceback is real
            except KeyError:
                log.exception(msg, extra={"error": "KeyError"})
        else:
            log.error("Upstream payment-gateway returned 502 while loading " + what, extra={"error": "upstream_502"})
        raise HTTPException(status_code=500, detail="internal error")


@app.get("/api/orders")
async def list_orders(limit: int = 20):
    await _apply_faults("order list")
    return {"items": list(ORDERS.values())[: max(1, min(limit, 100))], "version": VERSION}


@app.get("/api/orders/{order_id}")
async def get_order(order_id: int):
    await _apply_faults(f"order {order_id}")
    order = ORDERS.get(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return order


@app.get("/health")
def health():
    if faults.unhealthy:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "version": VERSION})
    return {"status": "ok", "version": VERSION}


@app.get("/ready")
def ready():
    if faults.unhealthy:
        return JSONResponse(status_code=503, content={"status": "not_ready", "version": VERSION})
    return {"status": "ready", "version": VERSION}


@app.get("/live")
def live():
    """Liveness is independent of simulated faults so kubelet never self-heals a scenario."""
    return {"status": "alive"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------------------------------------------------------------- admin (failure simulation)
def require_admin(x_admin_token: str | None = Header(default=None)):
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")


class FaultRequest(BaseModel):
    type: str = Field(pattern="^(latency|errors|unhealthy)$")
    latency_ms: int = Field(default=2000, ge=0, le=10000)
    error_rate: float = Field(default=0.5, ge=0, le=1)


@app.get("/admin/state", dependencies=[Depends(require_admin)])
def admin_state():
    return faults.as_dict()


@app.post("/admin/faults", dependencies=[Depends(require_admin)])
def admin_set_fault(req: FaultRequest):
    if req.type == "latency":
        faults.latency_ms = req.latency_ms
    elif req.type == "errors":
        faults.error_rate = req.error_rate
    else:
        faults.unhealthy = True
    faults.update_gauges()
    log.warning("fault injected", extra={"error": req.type})
    return faults.as_dict()


@app.post("/admin/reset", dependencies=[Depends(require_admin)])
def admin_reset():
    faults.reset()
    log.info("faults reset")
    return faults.as_dict()
