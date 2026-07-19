"""Canonical serialization for deterministic hashes. Pure; no I/O."""

from __future__ import annotations

import json
from decimal import Decimal
from enum import Enum
from typing import Any


def _normalize(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Decimal):
        # Fixed quantize representation for hash stability
        s = format(obj, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s in ("", "-0"):
            s = "0"
        return s
    if isinstance(obj, float):
        # floats should be rejected upstream; still canonicalize if present
        d = Decimal(str(obj))
        return _normalize(d)
    if isinstance(obj, (int, str, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _normalize(obj[k]) for k in sorted(obj.keys(), key=lambda x: str(x))}
    if isinstance(obj, (list, tuple)):
        return [_normalize(x) for x in obj]
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return _normalize(obj.to_dict())
    if hasattr(obj, "__dict__") and not isinstance(obj, type):
        # dataclasses without to_dict
        raw = {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        return _normalize(raw)
    raise TypeError(f"unsupported type for canonical serialization: {type(obj)!r}")


def canonical_json(obj: Any) -> str:
    """Stable JSON string: sorted keys, no whitespace variance, UTF-8 safe."""
    normalized = _normalize(obj)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads_canonical(text: str) -> Any:
    return json.loads(text)
