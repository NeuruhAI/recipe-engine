"""Founder Runtime API v1 — loopback-only, operator-authenticated, fail closed.

Binds 127.0.0.1 only. Every route (except health meta) requires a bearer token.
State-changing routes require an Idempotency-Key header and accept an optional
X-Expected-Version for optimistic concurrency control. No route executes a plan,
mutates AXON, dispatches Workers, fetches a URL, or touches production.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from recipe_engine.founder_runtime.api.adapters import organ_status
from recipe_engine.founder_runtime.api.auth import AuthError, OperatorAuth
from recipe_engine.founder_runtime.api.service import ApiError, MissionService, API_VERSION

MAX_BODY = 64 * 1024  # 64 KiB — reject oversized payloads


class CreateMissionBody(BaseModel):
    objective: str = Field(min_length=1, max_length=8000)
    title: str = Field(default="", max_length=500)
    done_criteria: str = Field(default="", max_length=4000)


class DecisionBody(BaseModel):
    reason: str = Field(default="", max_length=4000)


def build_app(service: MissionService, auth: OperatorAuth, *, probe_live: bool = True,
              prober=None) -> FastAPI:
    app = FastAPI(title="Neuruh Founder Runtime API", version=API_VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def _unauth():
        return JSONResponse(status_code=401, content={
            "error": {"code": "UNAUTHORIZED", "message": "missing or invalid operator token"}})

    def _extract(authorization: str | None) -> str | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        return authorization[len("Bearer "):].strip()

    async def require_auth(authorization: str | None = Header(default=None)):
        token = _extract(authorization)
        try:
            auth.require(token)
        except AuthError as e:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": e.message})
        return True

    def _idem_key(idempotency_key: str | None) -> str:
        if not idempotency_key or not idempotency_key.strip():
            raise HTTPException(status_code=428, detail={
                "code": "IDEMPOTENCY_KEY_REQUIRED",
                "message": "Idempotency-Key header is required for state-changing calls"})
        return idempotency_key.strip()

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status, content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details}})

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse(status_code=413, content={
                "error": {"code": "PAYLOAD_TOO_LARGE", "message": "request body too large"}})
        return await call_next(request)

    # ---- health/readiness (no auth; reveals only meta) ----
    @app.get("/v1/health")
    async def health():
        return {"status": "ok", "api_version": API_VERSION, "auth_configured": auth.configured}

    @app.get("/v1/readiness")
    async def readiness():
        try:
            integrity = service.runtime.store.verify_integrity()
            ok = bool(integrity.get("ok"))
        except Exception:  # noqa: BLE001
            ok = False
        status = {"ready": ok, "api_version": API_VERSION, "persistence_integrity": ok}
        return JSONResponse(status_code=200 if ok else 503, content=status)

    # ---- missions ----
    @app.post("/v1/missions", status_code=201, dependencies=[Depends(require_auth)])
    async def create_mission(body: CreateMissionBody,
                             idempotency_key: str | None = Header(default=None),
                             x_expected_version: int | None = Header(default=None)):
        key = _idem_key(idempotency_key)
        return service.create_mission(objective=body.objective, title=body.title,
                                      done_criteria=body.done_criteria,
                                      idempotency_key=key, expected_version=x_expected_version)

    @app.get("/v1/missions", dependencies=[Depends(require_auth)])
    async def list_missions():
        return service.list_missions()

    @app.get("/v1/missions/{mission_id}", dependencies=[Depends(require_auth)])
    async def get_mission(mission_id: str):
        return service.get_mission(mission_id)

    @app.get("/v1/missions/{mission_id}/decision-packet", dependencies=[Depends(require_auth)])
    async def decision_packet(mission_id: str):
        return service.decision_packet(mission_id)

    @app.get("/v1/missions/{mission_id}/plan", dependencies=[Depends(require_auth)])
    async def proposed_plan(mission_id: str):
        return service.proposed_plan(mission_id)

    @app.post("/v1/missions/{mission_id}/approve", dependencies=[Depends(require_auth)])
    async def approve(mission_id: str, body: DecisionBody,
                      idempotency_key: str | None = Header(default=None),
                      x_expected_version: int | None = Header(default=None)):
        return service.approve_plan(mission_id, reason=body.reason,
                                    idempotency_key=_idem_key(idempotency_key),
                                    expected_version=x_expected_version)

    @app.post("/v1/missions/{mission_id}/reject", dependencies=[Depends(require_auth)])
    async def reject(mission_id: str, body: DecisionBody,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.reject_plan(mission_id, reason=body.reason,
                                   idempotency_key=_idem_key(idempotency_key),
                                   expected_version=x_expected_version)

    @app.post("/v1/missions/{mission_id}/pause", dependencies=[Depends(require_auth)])
    async def pause(mission_id: str,
                    idempotency_key: str | None = Header(default=None),
                    x_expected_version: int | None = Header(default=None)):
        return service.pause(mission_id, idempotency_key=_idem_key(idempotency_key),
                             expected_version=x_expected_version)

    @app.post("/v1/missions/{mission_id}/resume", dependencies=[Depends(require_auth)])
    async def resume(mission_id: str,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.resume(mission_id, idempotency_key=_idem_key(idempotency_key),
                              expected_version=x_expected_version)

    @app.post("/v1/missions/{mission_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel(mission_id: str,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.cancel(mission_id, idempotency_key=_idem_key(idempotency_key),
                              expected_version=x_expected_version)

    @app.get("/v1/missions/{mission_id}/events", dependencies=[Depends(require_auth)])
    async def events(mission_id: str):
        return service.events(mission_id)

    @app.get("/v1/missions/{mission_id}/events/stream", dependencies=[Depends(require_auth)])
    async def events_stream(mission_id: str):
        # Snapshot the append-only log once, then stream it as SSE. Read-only.
        service.get_mission(mission_id)  # 404 if unknown

        async def gen():
            data = service.events(mission_id)
            for i, ev in enumerate(data["events"]):
                yield f"id: {ev.get('sequence', i)}\nevent: {ev.get('event_type')}\ndata: {json.dumps(ev, sort_keys=True)}\n\n"
                await asyncio.sleep(0)
            yield "event: end\ndata: {\"status\":\"stream_complete\"}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1/missions/{mission_id}/receipts", dependencies=[Depends(require_auth)])
    async def receipts(mission_id: str):
        return service.receipts(mission_id)

    @app.get("/v1/missions/{mission_id}/replay", dependencies=[Depends(require_auth)])
    async def replay(mission_id: str):
        return service.replay(mission_id)

    @app.post("/v1/missions/{mission_id}/recover", dependencies=[Depends(require_auth)])
    async def recover(mission_id: str,
                      idempotency_key: str | None = Header(default=None),
                      x_expected_version: int | None = Header(default=None)):
        _idem_key(idempotency_key)  # recovery is idempotent but still requires a key
        return service.recover(mission_id)

    # ---- adapters / organ status ----
    @app.get("/v1/adapters/status", dependencies=[Depends(require_auth)])
    async def adapters_status():
        return organ_status(probe_live=probe_live, prober=prober)

    return app
