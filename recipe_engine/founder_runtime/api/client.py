"""Typed local client for the Founder Runtime API.

Loopback only. The operator token is passed in (never logged or persisted by
this client). Each state-changing call sends a fresh idempotency key unless the
caller supplies one (for deliberate replay).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
import uuid
from typing import Any, Iterator, Mapping


class ApiClientError(Exception):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"HTTP {status}: {payload}")


class FounderRuntimeClient:
    def __init__(self, token: str, *, host: str = "127.0.0.1", port: int = 19090, timeout: float = 15.0):
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("client is loopback-only")
        self._base = f"http://{host}:{port}"
        self._token = token
        self._timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None,
             idempotency_key: str | None = None, expected_version: int | None = None,
             auth: bool = True) -> Any:
        url = self._base + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if auth:
            req.add_header("Authorization", f"Bearer {self._token}")
        if idempotency_key:
            req.add_header("Idempotency-Key", idempotency_key)
        if expected_version is not None:
            req.add_header("X-Expected-Version", str(expected_version))
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            raise ApiClientError(e.code, payload) from e

    @staticmethod
    def _key() -> str:
        return str(uuid.uuid4())

    # reads
    def health(self) -> dict:
        return self._req("GET", "/v1/health", auth=False)

    def readiness(self) -> dict:
        return self._req("GET", "/v1/readiness", auth=False)

    def adapters_status(self) -> dict:
        return self._req("GET", "/v1/adapters/status")

    def list_missions(self) -> dict:
        return self._req("GET", "/v1/missions")

    def get_mission(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}")

    def decision_packet(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}/decision-packet")

    def proposed_plan(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}/plan")

    def events(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}/events")

    def receipts(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}/receipts")

    def replay(self, mission_id: str) -> dict:
        return self._req("GET", f"/v1/missions/{mission_id}/replay")

    # writes
    def create_mission(self, objective: str, *, title: str = "", done_criteria: str = "",
                       work_spec: Mapping[str, Any] | None = None,
                       idempotency_key: str | None = None) -> dict:
        body: dict[str, Any] = {
            "objective": objective,
            "title": title,
            "done_criteria": done_criteria,
        }
        if work_spec is not None:
            body["work_spec"] = dict(work_spec)
        return self._req("POST", "/v1/missions",
                         body=body,
                         idempotency_key=idempotency_key or self._key())

    def approve(self, mission_id: str, *, reason: str = "", idempotency_key: str | None = None,
                expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/approve", body={"reason": reason},
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def reject(self, mission_id: str, *, reason: str, idempotency_key: str | None = None,
               expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/reject", body={"reason": reason},
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    # The authority gate is a different act from a plan decision: only a gate
    # ALLOW carries the signed artifact a parked live mission can consume.
    def approve_gate(self, mission_id: str, *, reason: str = "", idempotency_key: str | None = None,
                     expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/gate/approve", body={"reason": reason},
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def reject_gate(self, mission_id: str, *, reason: str, idempotency_key: str | None = None,
                    expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/gate/reject", body={"reason": reason},
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def resolve_gate(self, mission_id: str, *, idempotency_key: str | None = None,
                     expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/gate/resolve",
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def pause(self, mission_id: str, *, idempotency_key: str | None = None,
              expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/pause",
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def resume(self, mission_id: str, *, idempotency_key: str | None = None,
               expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/resume",
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def cancel(self, mission_id: str, *, idempotency_key: str | None = None,
               expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/cancel",
                         idempotency_key=idempotency_key or self._key(), expected_version=expected_version)

    def recover(self, mission_id: str, *, idempotency_key: str | None = None,
                expected_version: int | None = None) -> dict:
        return self._req("POST", f"/v1/missions/{mission_id}/recover",
                         idempotency_key=idempotency_key or self._key(),
                         expected_version=expected_version)

    # R02D: the workers dispatch bridge reports a sealed execution outcome.
    # ``body`` is a complete fr.execution_outcome.v1 envelope; the runtime binds
    # and orders it. Default idempotency key = the evidence identity, so a
    # retried report of the same outcome is a replay by construction.
    def record_execution_outcome(self, mission_id: str, body: Mapping[str, Any], *,
                                 idempotency_key: str | None = None,
                                 expected_version: int | None = None) -> dict:
        if idempotency_key is None:
            receipt = body.get("receipt") or {}
            court = body.get("court") or {}
            idempotency_key = ":".join(str(part) for part in (
                "execution_outcome", body.get("execution_id"), body.get("outcome"),
                receipt.get("receipt_sha") or court.get("record_sha") or ""))
        return self._req("POST", f"/v1/missions/{mission_id}/execution-outcomes", body=dict(body),
                         idempotency_key=idempotency_key, expected_version=expected_version)

    def stream_events(self, mission_id: str, *, after: int | str | None = None,
                      last_event_id: str | None = None) -> Iterator[dict]:
        """Yield parsed SSE events from the read-only stream endpoint.

        Resumes strictly after `after`/`last_event_id` when given (durable
        cursor). Already-seen event ids are never yielded twice (client-side
        deduplication on top of server-side cursor enforcement)."""
        url = f"{self._base}/v1/missions/{mission_id}/events/stream"
        if after is not None:
            url += f"?after={after}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        if last_event_id:
            req.add_header("Last-Event-ID", str(last_event_id))
        seen: set[str] = set()
        try:
            resp_ctx = urllib.request.urlopen(req, timeout=self._timeout)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            raise ApiClientError(e.code, payload) from e
        with resp_ctx as resp:
            data_lines: list[str] = []
            current_id: str | None = None
            current_type: str = ""
            for raw in resp:
                line = raw.decode("utf-8").rstrip("\n")
                if line.startswith("id:"):
                    current_id = line[len("id:"):].strip()
                elif line.startswith("event:"):
                    current_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
                elif line == "":
                    if data_lines:
                        try:
                            payload = json.loads("\n".join(data_lines))
                        except json.JSONDecodeError:
                            payload = None
                        if payload is not None:
                            dedup_key = (f"{current_type}:{current_id}" if current_id is not None
                                         else json.dumps(payload, sort_keys=True))
                            if dedup_key not in seen:
                                seen.add(dedup_key)
                                yield payload
                        data_lines = []
                        current_id = None
                        current_type = ""
