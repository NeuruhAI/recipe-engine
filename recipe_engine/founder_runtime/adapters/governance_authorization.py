"""The Governance V2 authorization request, and the founder approval that backs it.

Two processes have to agree, byte for byte, about one thing: the artifact the
founder signs and the request the mission presents.  The mission builds the
request and never signs; the external decision channel signs and never requests.
Both of them need the same canonical form, so it lives here, in one place, as
pure arithmetic over data.

Nothing in this module performs I/O, opens a socket, or reads the environment.
It cannot: the founder-runtime package is under an AST confinement scan that
forbids exactly that, and this module is deliberately inside the scanned graph
so the confinement keeps applying to the code that touches authority.

The canonical form is `autonomous-governance-core/src/canonical.mjs`:
deterministic JSON with sorted keys, no whitespace, no ambiguous values.  This
is a second implementation of somebody else's contract, so it is not enough for
it to look right — `tests/founder_runtime/test_t31b_authority_chain.py` pins it
against a frozen vector produced by the Node implementation and, when node is on
the machine, re-derives that vector live.  Every value that crosses the boundary
in a signing payload is a string; no number is ever signed, so the two languages
cannot disagree about how to spell a float.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import unicodedata
from typing import Any, Mapping

# Governance's own schema identifiers. They are not ours to invent; a mismatch
# here is refused by Governance as APPROVAL_MALFORMED, which is the correct
# outcome — the boundary decides what it accepts.
FOUNDER_APPROVAL_SCHEMA_VERSION = "gov.founder.approval.v1"
FACTS_SCHEMA_VERSION = "gov.action.facts.v1"

# The one approval mode that survives an asynchronous human decision without
# leaving a standing permission behind. See the governance policy matrix.
SCOPE_ONCE = "scope_once"

# The component identity this runtime attests to Governance, and the read-only
# observation the long-horizon mission's authority gate is about. Both must
# match the trusted configuration exactly or the request is denied.
FOUNDER_RUNTIME_COMPONENT = "founder_runtime"
OBSERVATION_TARGET = {"type": "observation", "id": "organism_status"}
OBSERVATION_REPOSITORY = "NeuruhAI/recipe-engine"
OBSERVATION_ACTION_CLASS = "status"

# Governance classifies this consequence; we do not get to assert it. We state
# what we believe so a disagreement is a refusal (APPROVAL_RISK_MISMATCH) rather
# than a silent re-interpretation.
EXPECTED_RISK_TIER = "R0"


class CanonicalizationError(ValueError):
    """A value that cannot be canonicalized. Never normalized silently."""


def _canonical_string(value: str, path: str) -> str:
    if unicodedata.normalize("NFC", value) != value:
        raise CanonicalizationError(f"string is not Unicode NFC normalized (at {path})")
    if chr(0) in value:
        raise CanonicalizationError(f"string contains NUL (at {path})")
    # ensure_ascii=False matches JSON.stringify: it escapes only ", \\ and the
    # C0 controls, using the same \\b \\f \\n \\r \\t shorthands.
    return json.dumps(value, ensure_ascii=False)


def _canonical(value: Any, path: str = "$", depth: int = 0) -> str:
    if depth > 64:
        raise CanonicalizationError(f"nesting exceeds canonical depth limit (at {path})")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        # Governance accepts only safe integers and rejects -0 as ambiguous.
        # Floats are refused outright rather than spelled: the two languages
        # must never disagree about a number, and nothing that crosses this
        # boundary needs one. (Nothing SIGNED here is numeric at all; facts
        # carry spend_usd, and Governance hashes those itself.)
        if not (-(2 ** 53 - 1) <= value <= 2 ** 53 - 1):
            raise CanonicalizationError(f"integer outside safe range (at {path})")
        return json.dumps(value)
    if isinstance(value, float):
        raise CanonicalizationError(f"floats are not canonical here (at {path})")
    if isinstance(value, str):
        return _canonical_string(value, path)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(v, f"{path}[{i}]", depth + 1) for i, v in enumerate(value)) + "]"
    if isinstance(value, Mapping):
        parts = []
        seen: set[str] = set()
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise CanonicalizationError(f"object key is not a string (at {path})")
            if key in ("__proto__", "constructor", "prototype"):
                raise CanonicalizationError(f"forbidden object key: {key} (at {path})")
            folded = unicodedata.normalize("NFC", key)
            if folded in seen:
                raise CanonicalizationError(f"duplicate key after normalization: {key} (at {path})")
            seen.add(folded)
            parts.append(_canonical_string(key, f"{path}.{key}") + ":" + _canonical(value[key], f"{path}.{key}", depth + 1))
        return "{" + ",".join(parts) + "}"
    raise CanonicalizationError(f"value type is not canonical: {type(value).__name__} (at {path})")


def canonicalize(value: Any) -> str:
    """Deterministic canonical JSON, byte-identical to Governance's."""
    return _canonical(value)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_canonical(value: Any) -> str:
    return sha256_hex(canonicalize(value))


def scope_key_for(facts: Mapping[str, Any]) -> str:
    """The exact scope an approval covers — Governance's ``scopeKeyFor``.

    Deliberately excludes the freshness nonce and issued_at.  That is what makes
    the scope stable enough for a human to decide about it out of band, while
    still naming the exact lane, consequence class and target coordinates.
    """
    return hash_canonical({
        "lane": facts["lane"],
        "action_class": facts["action_class"],
        "target": dict(facts["target"]),
        "repository": facts.get("repository"),
        "branch": facts.get("branch"),
        "service_label": facts.get("service_label"),
    })


def authorization_facts(
    *,
    lane: str,
    action_id: str,
    summary: str,
    source_sha: str,
    nonce: str,
    issued_at: str,
) -> dict[str, Any]:
    """The exact facts the mission presents for its read-only authority gate.

    Read-only throughout and stated as such: no externality, no spend, no
    production effect beyond reading, a single target.  Governance classifies
    the consequence from these; the mission does not get to assert a tier.
    """
    return {
        "schema_version": FACTS_SCHEMA_VERSION,
        "action_id": action_id,
        "lane": lane,
        "component": FOUNDER_RUNTIME_COMPONENT,
        "source_sha": source_sha,
        "action_class": OBSERVATION_ACTION_CLASS,
        "summary": summary,
        "target": dict(OBSERVATION_TARGET),
        "repository": OBSERVATION_REPOSITORY,
        "externality": "none",
        "reversibility": "reversible",
        "blast_radius": "single_target",
        "production_effect": "read",
        "spend_usd": 0,
        "nonce": nonce,
        "issued_at": issued_at,
    }


def approval_signing_payload(approval: Mapping[str, Any]) -> dict[str, Any]:
    """Exactly the fields Governance signs — no more, no fewer."""
    return {
        "schema_version": approval["schema_version"],
        "approval_id": approval["approval_id"],
        "approver_id": approval["approver_id"],
        "mode": approval["mode"],
        "bound": dict(approval["bound"]),
    }


def unsigned_scope_once_approval(
    *,
    approval_id: str,
    approver_id: str,
    scope_key: str,
    source_sha: str,
    nonce: str,
    issued_at: str,
    expires_at: str,
    risk_tier: str = EXPECTED_RISK_TIER,
) -> dict[str, Any]:
    """The approval artifact, unsigned.

    The bound block is closed: Governance refuses it outright if a field is
    missing or an extra one is smuggled in, so this shape is the contract, not
    a convenience.
    """
    return {
        "schema_version": FOUNDER_APPROVAL_SCHEMA_VERSION,
        "approval_id": approval_id,
        "approver_id": approver_id,
        "mode": SCOPE_ONCE,
        "bound": {
            "scope_key": scope_key,
            "source_sha": source_sha,
            "risk_tier": risk_tier,
            "nonce": nonce,
            "issued_at": issued_at,
            "expires_at": expires_at,
        },
    }


def sign_founder_approval(unsigned: Mapping[str, Any], approver_secret: str) -> dict[str, Any]:
    """HMAC-SHA256 over the canonical signing payload.

    This function is the ENTIRE signing capability of this runtime, and it is
    the only place a founder secret is ever used.  It takes the secret as an
    argument and never looks for one: a caller that has no secret cannot sign,
    which is the property the mission side depends on.
    """
    if not approver_secret:
        raise ValueError("cannot sign a founder approval without an approver secret")
    signature = hmac.new(
        approver_secret.encode("utf-8"),
        canonicalize(approval_signing_payload(unsigned)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**dict(unsigned), "signature": signature}
