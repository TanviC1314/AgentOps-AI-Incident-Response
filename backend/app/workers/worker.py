"""Background worker: consumes investigation / recovery-execution jobs from the queue."""
import logging
import threading

from app.core.queue import ResilientQueue, get_queue

logger = logging.getLogger(__name__)


def handle_job(job: dict) -> None:
    kind = job.get("type")
    if kind == "investigate":
        from app.agent.runner import run_investigation

        run_investigation(job["incident_id"])
    elif kind == "execute_proposal":
        from app.recovery.executor import execute_proposal

        execute_proposal(job["proposal_id"])
    else:
        logger.warning("unknown job type", extra={"job": job})


class Worker:
    def __init__(self, queue: ResilientQueue):
        self.queue = queue
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="agentops-worker", daemon=True)

    def start(self) -> "Worker":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        logger.info("worker started")
        while not self._stop.is_set():
            try:
                job = self.queue.dequeue(timeout=1)
                if job is None:
                    continue
                logger.info("job received", extra={"job": job})
                handle_job(job)
            except Exception:  # a failing job must never kill the worker
                logger.exception("job failed")
        logger.info("worker stopped")


def start_worker() -> Worker:
    return Worker(get_queue()).start()
