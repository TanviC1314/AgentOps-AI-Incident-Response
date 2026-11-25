import os

os.environ.setdefault("WORKER_ENABLED", "false")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "memory://")

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.core import queue as queue_mod
from app.db import Base


@pytest.fixture()
def engine():
    eng = dbmod.init_db("sqlite://")
    import app.models  # noqa: F401

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def queue():
    q = queue_mod.ResilientQueue(None)
    queue_mod.set_queue(q)
    yield q
    queue_mod.set_queue(None)


@pytest.fixture()
def db(engine):
    with dbmod.session_factory()() as session:
        yield session


@pytest.fixture()
def client(engine, queue):
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def alert_payload(alertname="HighErrorRate", severity="high", service="demo-service", fingerprint="fp1",
                  starts="2026-01-01T10:00:00Z", status="firing"):
    return {
        "version": "4", "status": status, "receiver": "agentops", "groupKey": "k",
        "alerts": [{
            "status": status,
            "labels": {"alertname": alertname, "severity": severity, "service": service},
            "annotations": {"summary": "Error rate above 20%", "description": "5xx ratio is 60%"},
            "startsAt": starts,
            "endsAt": "0001-01-01T00:00:00Z" if status == "firing" else "2026-01-01T10:05:00Z",
            "generatorURL": "http://prometheus:9090/graph", "fingerprint": fingerprint,
        }],
    }
