"""Host (Colima/Qdrant) recovery gate for the live Qdrant adapter.

The live Qdrant activation gate MUST NOT run until a *sealed GREEN* host recovery
audit exists beneath the host-colima-integrity-recovery audit root, proving:
  - no continuing I/O corruption,
  - Qdrant collection count 3049,
  - bounded retrieval passes,
  - Redis integrity passes,
  - volumes preserved.

This gate scans that audit root and decides OPEN vs SEALED.  It reads only the
sealed deliverable files under the audit root the caller pins in settings; it
performs no container, docker, or Colima interaction whatsoever.  Until a GREEN
seal exists the Qdrant adapter refuses to connect to the live service and
returns DENIED — never a fixture substitute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateDecision:
    open: bool
    reason: str
    sealed_audit_path: str | None = None
    evidence: dict | None = None

    def to_dict(self) -> dict:
        return {
            "open": self.open,
            "reason": self.reason,
            "sealed_audit_path": self.sealed_audit_path,
            "evidence": dict(self.evidence or {}),
        }


class HostRecoveryGate:
    def __init__(self, audit_root: str | None, *, required_count: int = 3049):
        self._audit_root = audit_root
        self._required_count = required_count

    def evaluate(self) -> GateDecision:
        if not self._audit_root:
            return GateDecision(False, "no host-recovery audit root configured")
        root = Path(self._audit_root)
        if not root.is_dir():
            return GateDecision(False, f"host-recovery audit root absent: {self._audit_root}")

        candidates = sorted(
            (d for d in root.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )
        seen: list[str] = []
        for audit in candidates:
            verdict = audit / "FINAL_VERDICT.md"
            if not verdict.is_file():
                continue
            seen.append(audit.name)
            decision = self._assess(audit, verdict)
            if decision.open:
                return decision
        if seen:
            return GateDecision(
                False,
                "no sealed GREEN host-recovery audit; latest audits are HOLD/RED",
                evidence={"audits_examined": seen},
            )
        return GateDecision(False, "no host-recovery audit with a FINAL_VERDICT found")

    def _assess(self, audit: Path, verdict_file: Path) -> GateDecision:
        try:
            verdict = verdict_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return GateDecision(False, f"verdict unreadable: {exc}")

        head = verdict[:4000]
        # Must be an affirmative GREEN verdict, not HOLD/RED.
        is_green = bool(re.search(r"(?i)\bVERDICT\b[^\n]*\bGREEN\b", head)) or bool(
            re.search(r"(?i)^#?\s*GREEN\b", head)
        )
        if re.search(r"(?i)\bVERDICT\b[^\n]*\b(HOLD|RED)\b", head) or not is_green:
            return GateDecision(
                False,
                "latest host-recovery verdict is not GREEN",
                sealed_audit_path=str(audit),
                evidence={"verdict_head": head.splitlines()[:3]},
            )

        # Corroborate the five criteria from the sibling deliverables.
        checks = self._five_criteria(audit, verdict)
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return GateDecision(
                False,
                "GREEN verdict not corroborated by recovery deliverables",
                sealed_audit_path=str(audit),
                evidence={"failed_criteria": failed, "checks": checks},
            )
        return GateDecision(
            True,
            "sealed GREEN host recovery verified",
            sealed_audit_path=str(audit),
            evidence={"checks": checks},
        )

    def _five_criteria(self, audit: Path, verdict: str) -> dict:
        def _read(name: str) -> str:
            f = audit / name
            try:
                return f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
            except OSError:
                return ""

        qdrant = _read("QDRANT_INTEGRITY.md")
        redis = _read("REDIS_INTEGRITY.md")
        io_assess = _read("IO_ERROR_ASSESSMENT.md")
        volumes = _read("DOCKER_VOLUME_LEDGER.csv") + _read("SHUTDOWN_STARTUP_LEDGER.md")
        blob = verdict + qdrant + redis + io_assess + volumes

        count_ok = str(self._required_count) in qdrant and "PASS" in qdrant.upper()
        # "no continuing I/O corruption": the assessment must NOT report ongoing
        # corruption.  A HOLD audit says "continuing I/O errors: YES".
        io_ok = not re.search(r"(?i)continuing\s+i/?o\s+(error|corruption)[^\n]*\b(yes|present|ongoing)\b", blob)
        io_ok = io_ok and not re.search(r"(?i)\bCORRUPT\b", io_assess)
        retrieval_ok = bool(re.search(r"(?i)(bounded\s+)?(scroll|retrieval)[^\n]*\b(ok|pass)", qdrant))
        redis_ok = "PASS" in redis.upper() or bool(re.search(r"(?i)\bPONG\b", redis))
        volumes_ok = bool(re.search(r"(?i)volume", volumes)) and not re.search(r"(?i)volume[s]?\s+(lost|deleted|recreated)", blob)

        return {
            "qdrant_count_3049": bool(count_ok),
            "no_continuing_io_corruption": bool(io_ok),
            "bounded_retrieval_passes": bool(retrieval_ok),
            "redis_integrity_passes": bool(redis_ok),
            "volumes_preserved": bool(volumes_ok),
        }
