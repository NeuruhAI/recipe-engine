"""Live GitHub/local-repository read-only observation adapter (P2-B).

Pure local ``git`` inspection of allowlisted repositories.  No network, no fetch,
no write of any kind.  The caller passes a *repository id* that must match the
live root allowlist exactly; arbitrary paths and non-allowlisted repos are
refused.  Origin identity is disclosed credential-free.
"""

from __future__ import annotations

import time
from typing import Any

from recipe_engine.founder_runtime.adapters.envelope import AdapterResult, AdapterResultStatus
from recipe_engine.founder_runtime.adapters.identity import DivergenceState, EvidenceClass
from recipe_engine.founder_runtime.adapters.protocols import AdapterRequestMeta
from recipe_engine.live_adapters.base_live import LiveAdapterBase
from recipe_engine.live_adapters.settings import LiveAdapterSettings
from recipe_engine.live_adapters.transport import GitObserver, TransportDenied, TransportError

_OP = "repo_state"


class LiveGitHubAdapter(LiveAdapterBase):
    name = "github"
    adapter_version = "p2b.live.github.1"
    service_name = "github"
    endpoint_class = "local_git_readonly"
    capabilities = ("repo_state",)

    def __init__(self, settings: LiveAdapterSettings, observer: GitObserver | None = None):
        super().__init__(settings)
        roots = {r.repo_id: r.path for r in settings.repo_roots}
        self._git = observer or GitObserver(roots)

    # --- pure observation --------------------------------------------------
    def repo_state(self, repo: str, meta: AdapterRequestMeta | None = None) -> dict[str, Any]:
        timeout = self.op_timeout("repo_state", meta)
        # Confinement: repo id must be exactly allowlisted (rejects arbitrary paths).
        self.settings.repo_root(repo)
        if not self._git.has_repo(repo):
            raise TransportDenied(f"repository {repo!r} not observable")

        head = self._git.one_line(repo, ["rev-parse", "HEAD"], timeout_ms=timeout)
        try:
            branch = self._git.one_line(repo, ["symbolic-ref", "--short", "-q", "HEAD"], timeout_ms=timeout)
        except TransportError:
            branch = "DETACHED"
        status = self._git.run(repo, ["status", "--porcelain"], timeout_ms=timeout)
        dirty_lines = [ln for ln in status.stdout.splitlines() if ln.strip()]
        clean = status.ok and not dirty_lines

        origin_redacted = ""
        origin = self._git.run(repo, ["remote", "get-url", "origin"], timeout_ms=timeout)
        if origin.ok and origin.stdout.strip():
            origin_redacted = GitObserver.redact_remote(origin.stdout.strip())

        # ahead/behind vs the LOCAL upstream tracking ref only (no fetch).
        ahead = behind = None
        upstream = self._git.run(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], timeout_ms=timeout)
        if upstream.ok and upstream.stdout.strip():
            counts = self._git.run(repo, ["rev-list", "--left-right", "--count", f"{upstream.stdout.strip()}...HEAD"], timeout_ms=timeout)
            if counts.ok and counts.stdout.split():
                parts = counts.stdout.split()
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    behind, ahead = int(parts[0]), int(parts[1])

        active_op = self._active_operation(repo, timeout)

        # selected-ref existence: HEAD and origin/HEAD symbolic (read-only).
        selected_refs = {
            "HEAD": bool(head),
            "refs/heads": self._git.run(repo, ["show-ref", "--heads", "-q"], timeout_ms=timeout).ok,
        }

        log = self._git.run(repo, ["log", "-n", "3", "--pretty=%H %s"], timeout_ms=timeout)
        recent = [ln for ln in log.stdout.splitlines() if ln.strip()][:3]

        return {
            "repo": repo,
            "branch": branch,
            "head_sha": head,
            "origin": origin_redacted,
            "clean": bool(clean),
            "dirty_count": len(dirty_lines),
            "ahead": ahead,
            "behind": behind,
            "active_operation": active_op,
            "selected_refs": selected_refs,
            "recent_log": recent,
            "read_only": True,
            "source": "live_git",
        }

    def _active_operation(self, repo: str, timeout: int) -> str | None:
        # Read-only detection of an in-progress operation via its pseudo-ref.
        # ``git rev-parse --verify -q <REF>`` writes nothing and exits non-zero
        # when the ref is absent.
        for op, ref in (
            ("merge", "MERGE_HEAD"),
            ("cherry_pick", "CHERRY_PICK_HEAD"),
            ("revert", "REVERT_HEAD"),
        ):
            ver = self._git.run(repo, ["rev-parse", "--verify", "-q", ref], timeout_ms=timeout)
            if ver.ok and ver.stdout.strip():
                return op
        return None

    # --- envelope ----------------------------------------------------------
    def repo_state_result(self, repo: str, meta: AdapterRequestMeta) -> AdapterResult:
        t0 = time.monotonic()
        try:
            payload = self.repo_state(repo, meta)
        except Exception as exc:  # noqa: BLE001 - fail closed to a typed status
            return self.failure_result(operation=_OP, meta=meta, exc=exc, duration_ms=_ms(t0))
        head = payload.get("head_sha") or "live"
        dep = self.dependency_identity(
            expected=head, observed=head, identity_verified=bool(head), readiness_verified=True
        ).to_dict()
        prov = self.live_provenance(
            logical=f"live://github/{repo}",
            observed_at=meta.observed_at or "1970-01-01T00:00:00.000Z",
            observed_sha=head,
            evidence_class=EvidenceClass.LIVE_OBSERVED,
            divergence=DivergenceState.NOT_APPLICABLE,
        )
        return self.live_result(
            operation=_OP,
            meta=meta,
            status=AdapterResultStatus.SUCCESS,
            payload=payload,
            dependency_identity=dep,
            provenance=prov,
            observed_sha=head,
            evidence_refs=[f"github:{repo}:{head[:12]}"],
            duration_ms=_ms(t0),
        )


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
