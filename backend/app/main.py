import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api import incidents, webhook
from app.config import get_settings
from app.core import metrics
from app.core.context import request_id_var
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.tracing import setup_tracing
from app.db import get_engine, session_factory

logger = logging.getLogger(__name__)


def _routers():
    """Routers are imported lazily so that feature modules can be added independently."""
    from importlib import import_module

    names = ["agent", "recovery", "telemetry", "simulation", "stats"]
    out = []
    for name in names:
        try:
            out.append(import_module(f"app.api.{name}").router)
        except ModuleNotFoundError as exc:  # pragma: no cover
            if exc.name != f"app.api.{name}":
                raise
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    worker = None
    if settings.worker_enabled:
        from app.core.queue import get_queue
        from app.services.startup import recover_interrupted_work
        from app.workers.worker import start_worker

        try:
            with session_factory()() as db:
                recover_interrupted_work(db, get_queue())
        except Exception:  # never block startup: incident management must stay available
            logger.exception("could not reconcile interrupted work")
        worker = start_worker()
    yield
    if worker is not None:
        worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_name, settings.log_level)
    setup_tracing(settings.app_name, settings.otel_exporter_otlp_endpoint)

    app = FastAPI(
        title="AgentOps API",
        version="0.1.0",
        description="AI incident response and safe rollback platform. Swagger UI at /docs.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )
    install_error_handlers(app)

    from fastapi.responses import JSONResponse

    from app.integrations.http import IntegrationError

    @app.exception_handler(IntegrationError)
    async def integration_error(_: Request, exc: IntegrationError):
        status = 404 if exc.code == "not_found" else 503
        return JSONResponse(status_code=status, content={"error": {
            "code": "dependency_unavailable", "message": f"{exc.source} unavailable: {exc.message}",
            "details": exc.as_dict(), "request_id": request_id_var.get(),
        }})

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            metrics.HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            metrics.HTTP_DURATION.labels(request.method, path).observe(time.perf_counter() - start)
            if path not in ("/metrics", "/health"):
                logger.info("request", extra={"method": request.method, "path": request.url.path, "status": status})
            request_id_var.reset(token)

    app.include_router(incidents.router, prefix="/api/v1")
    app.include_router(webhook.router, prefix="/api/v1")
    for r in _routers():
        app.include_router(r, prefix="/api/v1")

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok", "service": settings.app_name}

    @app.get("/ready", tags=["system"])
    def ready():
        from sqlalchemy import text

        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            return Response(content=f'{{"status":"unavailable","reason":"{type(exc).__name__}"}}', status_code=503, media_type="application/json")
        return {"status": "ready"}

    @app.get("/metrics", tags=["system"], include_in_schema=False)
    def prometheus_metrics():
        from sqlalchemy import func, select

        from app.integrations.http import IntegrationError
        from app.integrations.registry import get_clients
        from app.models import Incident
        from app.repositories.incidents import TERMINAL

        with session_factory()() as db:
            metrics.INCIDENTS_ACTIVE.set(
                db.scalar(select(func.count()).select_from(Incident).where(Incident.status.not_in(TERMINAL))) or 0
            )
        try:
            ratio = get_clients().prometheus.scalar(
                'sum(rate(demo_http_requests_total{status=~"5..",path=~"/api/.*"}[1m]))'
                " / sum(rate(demo_http_requests_total{path=~\"/api/.*\"}[1m]))"
            )
            if ratio is not None:
                metrics.SERVICE_ERROR_RATE.set(ratio)
        except (IntegrationError, Exception):
            pass  # Prometheus unavailable: leave gauge at its last known value
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
