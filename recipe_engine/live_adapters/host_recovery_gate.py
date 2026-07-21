"""Host (Colima/Qdrant) recovery gate for the live Qdrant adapter.

The live Qdrant activation gate MUST NOT open until a *sealed GREEN* host-recovery
audit exists proving: no continuing I/O corruption, Qdrant collection count 3049
(before and after), Redis PONG, all five named volumes preserved (none recreated
or deleted), and a GREEN final verdict.

P2-B host-gate compatibility repair (fix/founder-runtime-p2b-host-gate-compat):

  * ROOT ALLOWLIST — the gate searches ONLY two canonical audit roots under the
    pinned workspace, by fixed name: ``host-colima-integrity-recovery`` and
    ``host-colima-integrity-recovery-final``.  No caller-controlled root, no
    arbitrary path.  Symlinked roots / audit dirs that escape the allowlisted
    root are rejected.
  * SEALED DISCOVERY — an audit is a candidate only if it carries a
    ``FINAL_VERDICT.md`` AND a ``DELIVERABLES.sha256`` that checksum-validates
    every listed file (hand-edited / unsealed dirs are rejected).
  * CANONICAL EVIDENCE MAP — evidence is located through alias sets, not one
    historical filename, and validated by CONTENT, not by filename existence.
  * GENERATION SAFETY — the newest audit overall must be GREEN; a newer HOLD/RED
    seals; two GREEN audits with conflicting counts seal.

The gate performs no container / docker / Colima interaction; it only reads the
sealed deliverable files under the allowlisted roots.  It never edits or renames
a sealed audit.  Until a valid GREEN seal is found it fails CLOSED and the Qdrant
adapter returns DENIED — never a fixture substitute.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# Fixed, non-overridable allowlist of audit-root directory NAMES.  The parent
# (audit_base) defaults to the pinned workspace; tests may point audit_base at a
# temporary directory, but the two root names below are always the only roots.
ALLOWLISTED_ROOT_NAMES = ("host-colima-integrity-recovery", "host-colima-integrity-recovery-final")
DEFAULT_AUDIT_BASE = "~/neuruh-workspace/audit-output"

REQUIRED_COLLECTION = "neuruh_vault_canon"
REQUIRED_COUNT = 3049
MIN_PRESERVED_VOLUMES = 5

# Canonical evidence alias map — content is validated, not filename existence.
QDRANT_EVIDENCE = ("QDRANT_INTEGRITY.md", "QDRANT_VERIFICATION.md")
REDIS_EVIDENCE = ("REDIS_INTEGRITY.md", "REDIS_VERIFICATION.md")
VOLUME_EVIDENCE = ("DOCKER_VOLUME_LEDGER.csv", "VOLUME_LEDGER.csv", "VOLUME_PRESERVATION.csv")
IO_CLEARANCE_EVIDENCE = (
    "IO_ERROR_CLEARANCE.md",
    "IO_ERROR_ASSESSMENT.md",
    "FILESYSTEM_CHECK_AND_REPAIR.md",
    "FILESYSTEM_REPAIR.md",
    "IMAGE_STORE_RECOVERY.md",
)
LIFECYCLE_EVIDENCE = ("SHUTDOWN_STARTUP_LEDGER.md",)
VERDICT_FILE = "FINAL_VERDICT.md"
CHECKSUM_FILE = "DELIVERABLES.sha256"

_IO_SUBSYSTEMS = r"(virtio|jbd2|ext4|overlayfs|containerd)"


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


@dataclass(frozen=True)
class _AuditView:
    name: str            # timestamp dir name (sortable)
    path: Path           # realpath, contained under its root
    root: Path
    verdict: str         # GREEN | HOLD | RED | UNKNOWN


class HostRecoveryGate:
    def __init__(
        self,
        audit_base: str | None = None,
        *,
        required_count: int = REQUIRED_COUNT,
        collection: str = REQUIRED_COLLECTION,
    ):
        self._base = Path(audit_base).expanduser() if audit_base else Path(DEFAULT_AUDIT_BASE).expanduser()
        self._required_count = required_count
        self._collection = collection

    # ---------------------------------------------------------------- roots
    def _allowlisted_roots(self) -> list[Path]:
        try:
            base_real = self._base.resolve()
        except OSError:
            return []
        roots: list[Path] = []
        for name in ALLOWLISTED_ROOT_NAMES:
            p = self._base / name
            if not p.is_dir():
                continue
            try:
                real = p.resolve()
            except OSError:
                continue
            # Reject a symlinked root that escapes the allowlisted base or points
            # at a differently-named directory.
            if real.name != name:
                continue
            if not str(real).startswith(str(base_real) + "/") and real != base_real / name:
                continue
            roots.append(real)
        return roots

    def _discover(self) -> list[_AuditView]:
        views: list[_AuditView] = []
        for root in self._allowlisted_roots():
            root_real = root.resolve()
            for d in sorted(root.iterdir()):
                if not d.is_dir():
                    continue
                try:
                    d_real = d.resolve()
                except OSError:
                    continue
                # Containment: the audit dir must be inside its allowlisted root
                # (reject symlink escape).
                if not str(d_real).startswith(str(root_real) + "/"):
                    continue
                verdict_file = d_real / VERDICT_FILE
                if not verdict_file.is_file() or verdict_file.is_symlink():
                    continue
                views.append(_AuditView(d.name, d_real, root_real, self._verdict_of(d_real)))
        return views

    # ------------------------------------------------------------- evaluate
    def evaluate(self) -> GateDecision:
        views = self._discover()
        if not views:
            return GateDecision(False, "no sealed host-recovery audit under an allowlisted root")

        # newest overall by timestamp name (across BOTH allowlisted roots)
        views.sort(key=lambda v: v.name, reverse=True)
        newest = views[0]

        # A newer HOLD/RED contradicts any older GREEN -> seal (generation safety).
        if newest.verdict in ("HOLD", "RED"):
            return GateDecision(
                False,
                f"newest host-recovery audit is {newest.verdict}, not GREEN",
                sealed_audit_path=str(newest.path),
                evidence={"newest": newest.name, "verdict": newest.verdict},
            )
        if newest.verdict != "GREEN":
            return GateDecision(
                False,
                "newest host-recovery audit has no explicit GREEN verdict",
                sealed_audit_path=str(newest.path),
                evidence={"newest": newest.name},
            )

        # Two conflicting GREEN audits (differing asserted counts) -> seal.
        green = [v for v in views if v.verdict == "GREEN"]
        counts_by_audit: dict[str, list[int]] = {g.name: sorted(self._asserted_counts(g.path)) for g in green}
        distinct: set[int] = set()
        for s in counts_by_audit.values():
            distinct |= set(s)
        if len(distinct) > 1:
            return GateDecision(
                False,
                "conflicting GREEN host-recovery audits assert different Qdrant counts",
                sealed_audit_path=str(newest.path),
                evidence={"counts": counts_by_audit},
            )

        # Full validation of the newest GREEN audit.
        ok, reason, ev = self._validate(newest)
        if not ok:
            return GateDecision(False, reason, sealed_audit_path=str(newest.path), evidence=ev)
        return GateDecision(True, "sealed GREEN host recovery verified", sealed_audit_path=str(newest.path), evidence=ev)

    # ------------------------------------------------------------- helpers
    def _read(self, audit: Path, name: str) -> str:
        f = audit / name
        if not f.is_file() or f.is_symlink():  # reject symlinked evidence
            return ""
        try:
            return f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _read_first(self, audit: Path, names: tuple[str, ...]) -> str:
        blob = ""
        for n in names:
            blob += self._read(audit, n) + "\n"
        return blob

    def _verdict_of(self, audit: Path) -> str:
        text = self._read(audit, VERDICT_FILE)[:6000]
        m = re.search(r"(?im)^\s*#{0,4}\s*VERDICT\s*[:\-]?\s*([A-Z]+)", text)
        if m:
            token = m.group(1).upper()
            if token in ("GREEN", "HOLD", "RED"):
                return token
        if re.search(r"(?im)^\s*#{0,3}\s*GREEN\b", text) and not re.search(r"(?i)\b(HOLD|RED)\b", text[:200]):
            return "GREEN"
        return "UNKNOWN"

    def _asserted_counts(self, audit: Path) -> set[int]:
        blob = self._read_first(audit, QDRANT_EVIDENCE) + self._read(audit, VERDICT_FILE)
        return self._extract_counts(blob)

    @staticmethod
    def _extract_counts(blob: str) -> set[int]:
        counts: set[int] = set()
        # points_count: N   /   point count N
        for m in re.findall(r"(?i)points?[ _-]?count[^\d]{0,15}(\d{2,7})", blob):
            counts.add(int(m))
        # count ... before ... N ... after ... N
        for a, b in re.findall(r"(?i)count[^\n]*?before[^\d]{0,20}(\d{2,7})[^\d]{0,20}after[^\d]{0,20}(\d{2,7})", blob):
            counts.add(int(a)); counts.add(int(b))
        # before / after: N / N   (and table "| ... | N | N |")
        for a, b in re.findall(r"(?i)before\s*/\s*after[^\d\n]{0,12}(\d{2,7})\s*[/|]\s*\*{0,2}(\d{2,7})", blob):
            counts.add(int(a)); counts.add(int(b))
        # "count N before and after"
        for m in re.findall(r"(?i)count\s+(\d{2,7})\s+before\s+and\s+after", blob):
            counts.add(int(m))
        # table rows explicitly about a count (exclude vector/index/threshold noise)
        for line in blob.splitlines():
            if "|" in line and re.search(r"(?i)\bcount\b", line) and not re.search(r"(?i)vector|threshold|index", line):
                for x in re.findall(r"\b(\d{2,7})\b", line):
                    counts.add(int(x))
        return counts

    # ------------------------------------------------------------- validate
    def _validate(self, view: _AuditView) -> tuple[bool, str, dict]:
        audit = view.path
        ev: dict = {"audit": view.name, "root": audit.parent.name}

        # 1) checksum-valid (sealed) — reject hand-edited / unsealed directories.
        chk_ok, chk_detail = self._checksum_valid(audit)
        ev["checksum"] = chk_detail
        if not chk_ok:
            return False, f"DELIVERABLES.sha256 invalid or missing: {chk_detail}", ev

        # 2) verdict GREEN (already parsed, re-confirm)
        if view.verdict != "GREEN":
            return False, "final verdict is not GREEN", ev
        verdict_text = self._read(audit, VERDICT_FILE)

        # 3) Qdrant collection + count before/after == required, no contradiction
        qdrant = self._read_first(audit, QDRANT_EVIDENCE)
        if not qdrant.strip():
            return False, "missing Qdrant evidence (no accepted alias present)", ev
        if self._collection not in qdrant and self._collection not in verdict_text:
            return False, f"Qdrant collection is not {self._collection}", ev
        counts = self._extract_counts(qdrant + verdict_text)
        ev["qdrant_counts"] = sorted(counts)
        if not counts:
            return False, "Qdrant count evidence absent", ev
        if counts != {self._required_count}:
            return False, f"Qdrant count contradiction/mismatch (expected {self._required_count}, saw {sorted(counts)})", ev

        # 4) Redis PONG
        redis = self._read_first(audit, REDIS_EVIDENCE) + verdict_text
        if not re.search(r"(?i)\bPONG\b", redis):
            return False, "Redis PING did not return PONG in evidence", ev

        # 5) five named volumes preserved; none recreated/deleted
        vol_ok, vol_detail = self._volumes_ok(audit, verdict_text)
        ev["volumes"] = vol_detail
        if not vol_ok:
            return False, f"volume preservation not proven: {vol_detail}", ev

        # 6) no continuing virtio/JBD2/ext4/overlayfs/containerd I/O errors
        io_ok, io_detail = self._io_clear(audit, verdict_text)
        ev["io_clearance"] = io_detail
        if not io_ok:
            return False, f"continuing I/O errors not cleared: {io_detail}", ev

        ev["result"] = "GREEN"
        return True, "sealed GREEN host recovery verified", ev

    def _checksum_valid(self, audit: Path) -> tuple[bool, str]:
        chk = audit / CHECKSUM_FILE
        if not chk.is_file() or chk.is_symlink():
            return False, "no DELIVERABLES.sha256"
        try:
            lines = [ln for ln in chk.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        except OSError as e:
            return False, f"unreadable checksum file: {e}"
        if not lines:
            return False, "empty checksum manifest"
        verified = 0
        for ln in lines:
            parts = ln.split(None, 1)
            if len(parts) != 2:
                return False, f"malformed checksum line: {ln[:40]}"
            digest, name = parts[0].strip().lower(), parts[1].strip().lstrip("*").strip()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                return False, f"non-sha256 digest: {digest[:16]}"
            if ".." in name or name.startswith("/"):
                return False, f"checksum path escapes audit dir: {name}"
            target = audit / name
            if not target.is_file() or target.is_symlink():
                return False, f"missing/symlinked manifest file: {name}"
            try:
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as e:
                return False, f"unreadable file {name}: {e}"
            if actual != digest:
                return False, f"checksum mismatch: {name}"
            verified += 1
        return True, f"{verified} files verified"

    def _volumes_ok(self, audit: Path, verdict_text: str) -> tuple[bool, str]:
        csv_blob = ""
        for name in VOLUME_EVIDENCE:
            csv_blob = self._read(audit, name)
            if csv_blob.strip():
                break
        if not csv_blob.strip():
            return False, "no volume ledger (accepted alias) present"
        rows = [ln for ln in csv_blob.splitlines() if ln.strip()]
        if len(rows) < 2:
            return False, "volume ledger has no data rows"
        header = [h.strip().lower() for h in rows[0].split(",")]

        def col(keys):
            for i, h in enumerate(header):
                if any(k in h for k in keys):
                    return i
            return None

        preserved_col = col(("present_after", "preserved"))
        recreated_col = col(("recreated",))  # matches recreated / silently_recreated
        preserved = 0
        for row in rows[1:]:
            cells = [c.strip() for c in row.split(",")]

            def cell(i):
                return cells[i].lower() if (i is not None and i < len(cells)) else ""

            pres = cell(preserved_col)
            recr = cell(recreated_col)
            if recr.startswith(("yes", "true", "1")):
                return False, f"a named volume was recreated: {row[:60]}"
            if pres.startswith(("no", "false", "0")):
                return False, f"a named volume was not preserved: {row[:60]}"
            if preserved_col is None or pres.startswith(("yes", "true", "1")):
                preserved += 1
        if preserved < MIN_PRESERVED_VOLUMES:
            return False, f"only {preserved} preserved volumes (< {MIN_PRESERVED_VOLUMES})"
        if re.search(r"(?i)volume[s]?\s+(deleted|recreated|lost)", verdict_text):
            return False, "verdict text indicates a volume was deleted/recreated"
        return True, f"{preserved} volumes preserved, none recreated/deleted"

    def _io_clear(self, audit: Path, verdict_text: str) -> tuple[bool, str]:
        blob = self._read_first(audit, IO_CLEARANCE_EVIDENCE) + verdict_text
        if not blob.strip():
            return False, "no I/O clearance evidence"
        affirmative = (
            re.search(r"(?i)no\s+continuing\s+[^\n]*i/?o\s+errors", blob)
            or re.search(r"(?i)continuing\s+i/?o\s+errors?\s*[:\-]?\s*\*{0,2}(none|no|0)\b", blob)
            or re.search(r"(?i)\b0\b[^\n]*dmesg[^\n]*error", blob)
            or re.search(r"(?i)dmesg[^\n]*error[^\n]*\b0\b", blob)
            or (re.search(r"(?i)superblock\s+clean", blob) and re.search(r"(?i)needs_recovery\s+cleared", blob))
        )
        if not affirmative:
            return False, "no affirmative I/O-clearance statement"
        # Negation-aware: neutralize affirmative/negated clearance sentences so
        # they cannot trip the ongoing-error scan (e.g. "No continuing virtio /
        # JBD2 / ext4 ... I/O errors" must NOT read as an ongoing error).  What
        # remains are only positive assertions or historical "before" mentions;
        # only the former, with a continuing/ongoing qualifier, seal the gate.
        scan = re.sub(r"(?i)no\s+continuing[^\n.]*i/?o\s+errors?", " ", blob)
        scan = re.sub(r"(?i)continuing\s+i/?o\s+errors?\s*[:\-]?\s*\*{0,2}(none|no|0)\b", " ", scan)
        ongoing = (
            re.search(r"(?i)continuing\s+i/?o\s+(error|corruption)[^\n]*\b(yes|present|ongoing)\b", scan)
            or re.search(rf"(?i)(continuing|ongoing|still|persistent)\b[^\n]{{0,40}}{_IO_SUBSYSTEMS}[^\n]{{0,40}}(error|eio|abort|corrupt)", scan)
            or re.search(rf"(?i){_IO_SUBSYSTEMS}[^\n]{{0,25}}(error|eio|abort|corrupt)[^\n]{{0,25}}(continuing|ongoing|still|persistent)", scan)
        )
        if ongoing:
            return False, "evidence indicates continuing I/O errors"
        return True, "affirmative I/O clearance; no continuing errors"
