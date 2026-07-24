"""Emit the machine-readable OpenAPI contract for the Founder Runtime API."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from recipe_engine.founder_runtime.api.app import create_app


def generate() -> dict:
    import os
    # Pin a throwaway token so no operator secret is generated or printed.
    os.environ.setdefault("FR_API_OPERATOR_TOKEN", "frk_openapi_spec_generation_only")
    db = tempfile.NamedTemporaryFile(prefix="fr_openapi_", suffix=".sqlite", delete=False).name
    app = create_app(db, probe_live=False)
    return app.openapi()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else None
    spec = generate()
    text = json.dumps(spec, indent=2, sort_keys=True)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(out)
    else:
        print(text)
