"""Fixture Vault — opaque document IDs only. No file path freedom."""

from __future__ import annotations

from typing import Any

from .base import BaseFixtureAdapter


class FixtureVault(BaseFixtureAdapter):
    name = "vault"

    def get_document(self, doc_id: str) -> dict[str, Any]:
        allowed = {
            "doc_founder_core": {
                "doc_id": "doc_founder_core",
                "title": "Founder core doctrine (fixture)",
                "content_hash": "hash_founder_core",
                "pointer": "fixture://vault/doc_founder_core",
            },
            "doc_architecture": {
                "doc_id": "doc_architecture",
                "title": "Architecture layer map (fixture)",
                "content_hash": "hash_architecture",
                "pointer": "fixture://vault/doc_architecture",
            },
        }
        if doc_id not in allowed:
            return {"doc_id": doc_id, "error": "not_found", "pointer": f"fixture://vault/{doc_id}"}
        return allowed[doc_id]
