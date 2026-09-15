"""Founder Runtime API v1 — loopback-only, operator-authenticated, fail closed.

Binds 127.0.0.1 only. Every route (except health meta) requires a bearer token.
State-changing routes require an Idempotency-Key header AND an X-Expected-Version
header (mandatory optimistic concurrency; exactly one terminal authority decision
per mission/plan/scope). The SSE stream supports durable cursor resume via
Last-Event-ID or an explicit ?after= cursor. No route executes a plan, mutates
AXON, dispatches Workers, fetches a URL, or touches production. The execution-outcomes
route only LEARNS, from sealed evidence, what a dispatch the runtime already
authorised did (R02D).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from recipe_engine.founder_runtime.api.adapters import organ_status
from recipe_engine.founder_runtime.api.auth import AuthError, OperatorAuth
from recipe_engine.founder_runtime.api.service import ApiError, MissionService, API_VERSION

MAX_BODY = 64 * 1024  # 64 KiB — reject oversized payloads


class WorkSpecBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["fr.work_spec.v1"] = Field(alias="schema")
    workflow_name: str = Field(min_length=1)
    params: dict[str, Any]
    intelligence_layer: Literal[
        "L0_DETERMINISTIC",
        "L1_NEURUH_DETERMINISTIC",
        "L2_LOCAL_MODEL",
        "L3_CHEAP_REMOTE_MODEL",
        "L4_FRONTIER_MODEL",
    ]
    evidence_refs: list[str]
    production_mutation: bool
    external_message: bool
    money_move: bool
    irreversible: bool
    private_ip_risk: bool
    code_custody_required: bool


class WorkSpecEnvelopeBody(WorkSpecBody):
    candidate_id: str


class AuthorityEnvelopeBody(BaseModel):
    requirements: list[dict[str, Any]]
    latest_approval: dict[str, Any] | None
    state: str
    work_spec_sha256: str | None
    work_spec_authorized: bool
    work_spec_execution_authorized: bool
    execution_authorized: bool
    dispatch_approval_id: str | None
    mutation_authorized: bool
    external_action_authorized: bool
    production_persistence_authorized: bool
    executed: bool
    execution_enabled: bool


class FreshnessBody(BaseModel):
    observed_at: str
    stale: bool


class MissionEnvelopeBody(BaseModel):
    api_version: str
    runtime_version: str
    mission_id: str
    state: str
    version: int
    objective: str
    work_spec: WorkSpecEnvelopeBody | None
    work_spec_sha256: str | None
    evidence_refs: list[str]
    authority: AuthorityEnvelopeBody
    allowed_next_actions: list[str]
    blocked_actions: list[str]
    freshness: FreshnessBody
    receipt_id: str
    # Lineage and completion (governed completion + continuation). Optional so
    # every pre-existing envelope keeps its exact shape.
    parent_mission_id: str | None = None
    lineage: dict[str, Any] | None = None
    completion: dict[str, Any] | None = None
    continuation: dict[str, Any] | None = None


class MissionListResponse(BaseModel):
    count: int
    missions: list[MissionEnvelopeBody]


class MissionDetailResponse(BaseModel):
    envelope: MissionEnvelopeBody
    mission: dict[str, Any]


class CreateMissionBody(BaseModel):
    objective: str = Field(min_length=1, max_length=8000)
    title: str = Field(default="", max_length=500)
    done_criteria: str = Field(default="", max_length=4000)
    work_spec: WorkSpecBody | None = None
    # What this mission wants next once it is verifiably complete
    # (fr.continuation_plan.v1 request: objective, done_criteria, work_spec,
    # optional nested continuation). Validated by the service, which owns the
    # exact vocabulary; the child passes its own Governance gate when scheduled.
    continuation: dict[str, Any] | None = None


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

    def _expected_version(x_expected_version: int | None) -> int:
        if x_expected_version is None:
            raise HTTPException(status_code=428, detail={
                "code": "EXPECTED_VERSION_REQUIRED",
                "message": "X-Expected-Version header is required for state-changing calls"})
        return x_expected_version

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
    # The two routes that can reach Governance run OFF the event loop. The
    # mission service is synchronous and takes a process-wide lock, and the
    # resolver's authorize call is a blocking loopback request bounded at
    # several seconds. Run on the loop, one slow authority round trip stopped
    # the whole ASGI worker answering anything — including /v1/health, which the
    # workers dispatch bridge probes with a 0.25s timeout before deciding the
    # Founder Runtime is absent. A healthy runtime must never read as absent
    # because it was busy asking permission.
    @app.post("/v1/missions", status_code=201, dependencies=[Depends(require_auth)])
    async def create_mission(body: CreateMissionBody,
                             idempotency_key: str | None = Header(default=None)):
        key = _idem_key(idempotency_key)
        return await run_in_threadpool(
            service.create_mission, objective=body.objective, title=body.title,
            done_criteria=body.done_criteria,
            work_spec=body.work_spec.model_dump(by_alias=True) if body.work_spec is not None else None,
            continuation=body.continuation,
            idempotency_key=key)

    @app.get(
        "/v1/missions",
        dependencies=[Depends(require_auth)],
        response_model=MissionListResponse,
    )
    async def list_missions():
        return service.list_missions()

    @app.get(
        "/v1/missions/{mission_id}",
        dependencies=[Depends(require_auth)],
        response_model=MissionDetailResponse,
    )
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
                                    expected_version=_expected_version(x_expected_version))

    @app.post("/v1/missions/{mission_id}/reject", dependencies=[Depends(require_auth)])
    async def reject(mission_id: str, body: DecisionBody,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.reject_plan(mission_id, reason=body.reason,
                                   idempotency_key=_idem_key(idempotency_key),
                                   expected_version=_expected_version(x_expected_version))

    # ---- the mission authority gate --------------------------------------
    # A plan decision and an authority decision are not the same act, and the
    # kernel does not accept one for the other: assert_approval_usable demands
    # the approval's exact_requested_action be the transition it is consumed
    # for, so a `plan_decision:allow` can never unblock a parked mission.
    # These two routes are how a human's gate decision reaches the mission from
    # OUTSIDE the mission process. An ALLOW here is signed by this process — the
    # only one holding the founder key — into the artifact Governance verifies;
    # a DENY is recorded and signs nothing, because there is no such thing as a
    # signed refusal, and minting one would create evidence that could be
    # mistaken for permission. Neither route executes anything.
    @app.post("/v1/missions/{mission_id}/gate/approve", dependencies=[Depends(require_auth)])
    async def approve_gate(mission_id: str, body: DecisionBody,
                           idempotency_key: str | None = Header(default=None),
                           x_expected_version: int | None = Header(default=None)):
        return service.approve_mission_gate(mission_id, reason=body.reason,
                                            idempotency_key=_idem_key(idempotency_key),
                                            expected_version=_expected_version(x_expected_version))

    @app.post("/v1/missions/{mission_id}/gate/reject", dependencies=[Depends(require_auth)])
    async def reject_gate(mission_id: str, body: DecisionBody,
                          idempotency_key: str | None = Header(default=None),
                          x_expected_version: int | None = Header(default=None)):
        return service.reject_mission_gate(mission_id, reason=body.reason,
                                           idempotency_key=_idem_key(idempotency_key),
                                           expected_version=_expected_version(x_expected_version))

    # The approval RESOLVER. Governance is asked whether this mission's
    # consequence needs anyone at all; its answer is applied through the kernel.
    # A safe mission leaves QUEUED; a consequential one stays parked with the
    # exact founder gate it waits on. Never signs, never executes.
    @app.post("/v1/missions/{mission_id}/gate/resolve", dependencies=[Depends(require_auth)])
    async def resolve_gate(mission_id: str,
                           idempotency_key: str | None = Header(default=None),
                           x_expected_version: int | None = Header(default=None)):
        return await run_in_threadpool(
            service.resolve_authority, mission_id,
            idempotency_key=_idem_key(idempotency_key),
            expected_version=_expected_version(x_expected_version))

    @app.post("/v1/missions/{mission_id}/pause", dependencies=[Depends(require_auth)])
    async def pause(mission_id: str,
                    idempotency_key: str | None = Header(default=None),
                    x_expected_version: int | None = Header(default=None)):
        return service.pause(mission_id, idempotency_key=_idem_key(idempotency_key),
                             expected_version=_expected_version(x_expected_version))

    @app.post("/v1/missions/{mission_id}/resume", dependencies=[Depends(require_auth)])
    async def resume(mission_id: str,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.resume(mission_id, idempotency_key=_idem_key(idempotency_key),
                              expected_version=_expected_version(x_expected_version))

    @app.post("/v1/missions/{mission_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel(mission_id: str,
                     idempotency_key: str | None = Header(default=None),
                     x_expected_version: int | None = Header(default=None)):
        return service.cancel(mission_id, idempotency_key=_idem_key(idempotency_key),
                              expected_version=_expected_version(x_expected_version))

    @app.get("/v1/missions/{mission_id}/events", dependencies=[Depends(require_auth)])
    async def events(mission_id: str):
        return service.events(mission_id)

    @app.get("/v1/missions/{mission_id}/events/stream", dependencies=[Depends(require_auth)])
    async def events_stream(mission_id: str, request: Request,
                            after: str | None = None):
        # Durable, resumable, read-only stream over the append-only log. Resume
        # strictly after the acknowledged cursor (Last-Event-ID header or the
        # explicit ?after= query). Unknown/foreign/beyond-tail cursors and any
        # integrity failure are explicit 409 errors; corrupted missions never
        # stream. Snapshot then stream; ordering is the committed sequence.
        cursor_raw = after if after not in (None, "") else request.headers.get("last-event-id")
        after_seq = service.resolve_sse_cursor(mission_id, cursor_raw)

        async def gen():
            page_after = after_seq
            while True:
                page = service.events_page(mission_id, after_sequence=page_after)
                for ev in page["events"]:
                    yield f"id: {ev.get('sequence')}\nevent: {ev.get('event_type')}\ndata: {json.dumps(ev, sort_keys=True)}\n\n"
                    await asyncio.sleep(0)
                page_after = page["tail_sequence"]
                if not page["has_more"]:
                    break
            yield f"id: {page_after}\nevent: end\ndata: {{\"status\":\"stream_complete\",\"tail_sequence\":{page_after}}}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/v1/missions/{mission_id}/receipts", dependencies=[Depends(require_auth)])
    async def receipts(mission_id: str):
        return service.receipts(mission_id)

    # ---- R02D: execution-outcome write-back (G-03) ------------------------
    # The ONE route through which the Founder Runtime learns that a dispatched
    # execution progressed, finished, or was judged. The caller (the workers
    # dispatch bridge, a separate writer) sends sealed evidence, never a state:
    # the kernel recomputes the receipt hash the emitter's way, binds mission /
    # work spec / consumed approval / dispatched version / execution id, orders
    # the outcome against what it already learned, and moves state only along
    # the execution path. It never reaches COMPLETED. The body is validated by
    # the kernel against the exact fr.execution_outcome.v1 shape so refusals
    # carry the runtime's own codes rather than a transport 422.
    @app.post("/v1/missions/{mission_id}/execution-outcomes", dependencies=[Depends(require_auth)])
    async def record_execution_outcome(mission_id: str, body: dict[str, Any],
                                       idempotency_key: str | None = Header(default=None),
                                       x_expected_version: int | None = Header(default=None)):
        key = _idem_key(idempotency_key)
        return await run_in_threadpool(
            service.record_execution_outcome, mission_id, body=body,
            idempotency_key=key, expected_version=x_expected_version)

    # ---- governed completion (RECONCILING -> COMPLETED) --------------------
    # The ONE route through which an executed, independently judged mission
    # completes. The caller sends the full sealed Sentinel record whose digest
    # the runtime accepted with COURT_PASSED; the kernel re-verifies it, derives
    # the live_observed receipt, acceptance and verified outcome from the
    # mission's own chain, and asks the canonical completion verifier. A
    # declared continuation is then scheduled as an ordinary gated mission.
    # Never executes, never authorises: completion binds what the gate allowed
    # and the court judged.
    @app.post("/v1/missions/{mission_id}/reconcile", dependencies=[Depends(require_auth)])
    async def reconcile(mission_id: str, body: dict[str, Any],
                        idempotency_key: str | None = Header(default=None),
                        x_expected_version: int | None = Header(default=None)):
        key = _idem_key(idempotency_key)
        return await run_in_threadpool(
            service.reconcile_mission, mission_id, body=body,
            idempotency_key=key, expected_version=x_expected_version)

    @app.get("/v1/missions/{mission_id}/replay", dependencies=[Depends(require_auth)])
    async def replay(mission_id: str):
        return service.replay(mission_id)

    @app.post("/v1/missions/{mission_id}/recover", dependencies=[Depends(require_auth)])
    async def recover(mission_id: str,
                      idempotency_key: str | None = Header(default=None),
                      x_expected_version: int | None = Header(default=None)):
        _idem_key(idempotency_key)  # recovery is idempotent but still requires a key
        _expected_version(x_expected_version)  # recovery may mutate mission projection
        return service.recover(mission_id)

    # ---- adapters / organ status ----
    @app.get("/v1/adapters/status", dependencies=[Depends(require_auth)])
    async def adapters_status():
        return organ_status(probe_live=probe_live, prober=prober)

    return app
