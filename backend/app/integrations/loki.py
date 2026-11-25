"""Loki client. Logs are only ever returned as stored by Loki."""
import json
import re
import time

from app.integrations.http import IntegrationError, JsonClient

_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class LokiClient(JsonClient):
    source = "loki"

    def query_logs(self, service: str, minutes: int = 10, limit: int = 100, levels: list[str] | None = None) -> dict:
        if not _SAFE.match(service):
            raise IntegrationError(self.source, "invalid service name", "invalid_input")
        levels = [lv.upper() for lv in (levels or []) if lv.upper() in LEVELS]
        logql = f'{{service="{service}"}}'
        if levels:
            logql += f' | json | level=~"{"|".join(levels)}"'
        end = time.time_ns()
        start = end - minutes * 60 * 1_000_000_000
        body = self.get_json(
            "/loki/api/v1/query_range",
            params={"query": logql, "start": start, "end": end, "limit": max(1, min(limit, 500)), "direction": "backward"},
        )
        if body.get("status") != "success":
            raise IntegrationError(self.source, "query failed", "bad_response")
        entries = []
        for stream in body["data"]["result"]:
            for ts, line in stream["values"]:
                entry = {"ts_ns": ts, "raw": line}
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        entry.update({
                            k: parsed.get(k) for k in
                            ("timestamp", "level", "message", "request_id", "incident_id", "version", "exception", "path", "status")
                            if parsed.get(k) is not None
                        })
                except ValueError:
                    pass
                entries.append(entry)
        entries.sort(key=lambda e: int(e["ts_ns"]), reverse=True)
        entries = entries[:limit]
        counts: dict[str, int] = {}
        messages: dict[str, int] = {}
        for e in entries:
            counts[e.get("level", "UNKNOWN")] = counts.get(e.get("level", "UNKNOWN"), 0) + 1
            if e.get("level") in ("ERROR", "CRITICAL", "WARNING") and e.get("message"):
                key = re.sub(r"\d+", "N", e["message"])[:160]
                messages[key] = messages.get(key, 0) + 1
        top = sorted(messages.items(), key=lambda kv: -kv[1])[:5]
        return {
            "query": logql, "window_minutes": minutes, "count": len(entries), "level_counts": counts,
            "top_error_messages": [{"message": m, "count": c} for m, c in top], "entries": entries,
        }
