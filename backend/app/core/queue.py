"""Job queue, distributed locks and idempotency keys.

Redis is used when REDIS_URL is a redis:// URL. If Redis is unreachable the queue degrades to an
in-process memory backend so incident management keeps working (jobs are still persisted state in
PostgreSQL; only the transport degrades).
"""
import json
import logging
import queue as pyqueue
import threading
import time
from typing import Protocol

logger = logging.getLogger(__name__)
QUEUE_KEY = "agentops:jobs"


class QueueBackend(Protocol):
    def enqueue(self, job: dict) -> None: ...
    def dequeue(self, timeout: float) -> dict | None: ...
    def acquire_lock(self, key: str, ttl_seconds: int) -> bool: ...
    def release_lock(self, key: str) -> None: ...
    def set_once(self, key: str, ttl_seconds: int) -> bool: ...


class MemoryBackend:
    def __init__(self):
        self._q: pyqueue.Queue[dict] = pyqueue.Queue()
        self._keys: dict[str, float] = {}
        self._mu = threading.Lock()

    def enqueue(self, job: dict) -> None:
        self._q.put(job)

    def dequeue(self, timeout: float) -> dict | None:
        try:
            return self._q.get(timeout=timeout)
        except pyqueue.Empty:
            return None

    def _take(self, key: str, ttl: int) -> bool:
        now = time.monotonic()
        with self._mu:
            expiry = self._keys.get(key)
            if expiry is not None and expiry > now:
                return False
            self._keys[key] = now + ttl
            return True

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        return self._take(f"lock:{key}", ttl_seconds)

    def release_lock(self, key: str) -> None:
        with self._mu:
            self._keys.pop(f"lock:{key}", None)

    def set_once(self, key: str, ttl_seconds: int) -> bool:
        return self._take(f"idem:{key}", ttl_seconds)

    def size(self) -> int:
        return self._q.qsize()


class RedisBackend:
    def __init__(self, client):
        self.r = client

    def enqueue(self, job: dict) -> None:
        self.r.lpush(QUEUE_KEY, json.dumps(job))

    def dequeue(self, timeout: float) -> dict | None:
        item = self.r.brpop(QUEUE_KEY, timeout=max(1, int(timeout)))
        return json.loads(item[1]) if item else None

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        return bool(self.r.set(f"agentops:lock:{key}", "1", nx=True, ex=ttl_seconds))

    def release_lock(self, key: str) -> None:
        self.r.delete(f"agentops:lock:{key}")

    def set_once(self, key: str, ttl_seconds: int) -> bool:
        return bool(self.r.set(f"agentops:idem:{key}", "1", nx=True, ex=ttl_seconds))


class ResilientQueue:
    """Primary backend (Redis) with in-memory fallback for degraded operation."""

    def __init__(self, primary: QueueBackend | None, fallback: MemoryBackend | None = None):
        self.primary = primary
        self.fallback = fallback or MemoryBackend()
        self.degraded = primary is None

    def enqueue(self, job: dict) -> None:
        if self.primary is not None:
            try:
                self.primary.enqueue(job)
                return
            except Exception:
                logger.warning("redis enqueue failed; using in-memory queue", exc_info=True)
                self.degraded = True
        self.fallback.enqueue(job)

    def dequeue(self, timeout: float) -> dict | None:
        job = self.fallback.dequeue(0.01) if self.fallback.size() else None
        if job:
            return job
        if self.primary is not None:
            try:
                return self.primary.dequeue(timeout)
            except Exception:
                logger.warning("redis dequeue failed", exc_info=True)
                self.degraded = True
                time.sleep(min(timeout, 1))
                return None
        return self.fallback.dequeue(timeout)

    def _call(self, name: str, *args) -> bool:
        if self.primary is not None:
            try:
                return getattr(self.primary, name)(*args)
            except Exception:
                logger.warning("redis %s failed; using in-memory", name, exc_info=True)
                self.degraded = True
        return getattr(self.fallback, name)(*args)

    def acquire_lock(self, key: str, ttl_seconds: int) -> bool:
        return self._call("acquire_lock", key, ttl_seconds)

    def release_lock(self, key: str) -> None:
        if self.primary is not None:
            try:
                self.primary.release_lock(key)
            except Exception:
                logger.warning("redis release_lock failed", exc_info=True)
        self.fallback.release_lock(key)

    def set_once(self, key: str, ttl_seconds: int) -> bool:
        return self._call("set_once", key, ttl_seconds)


_queue: ResilientQueue | None = None


def build_queue(redis_url: str) -> ResilientQueue:
    if redis_url.startswith("redis"):
        import redis

        client = redis.Redis.from_url(redis_url, socket_timeout=6, socket_connect_timeout=3)
        return ResilientQueue(RedisBackend(client))
    return ResilientQueue(None)


def get_queue() -> ResilientQueue:
    global _queue
    if _queue is None:
        from app.config import get_settings

        _queue = build_queue(get_settings().redis_url)
    return _queue


def set_queue(q: ResilientQueue | None) -> None:
    global _queue
    _queue = q
