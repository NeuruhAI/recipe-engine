#!/usr/bin/env python3
"""GATE B — re-ratify the CURRENT Qdrant canon from live evidence.

Read-only throughout. Nothing here upserts, deletes, creates or drops a
collection, or mutates a payload; every Qdrant call is an inspection or a
search.

The point is that a replacement pin must bind to *evidence*, not to a collection
name. "Port 6333 returned HTTP 200" is not proof of anything. What is proved
here, live:

  collection exists and is GREEN
  point count, indexed vector count, vector dimension, distance metric
  payload shape is uniform across every point
  the embedding model is recorded IN the data and is uniform
  corpus/source identity (source tree and path roots)
  the T4 held-out evaluation corpus is absent
  semantic retrieval actually returns relevant canon for real questions

On the held-out check: the T4 seal states those cases must never appear in any
prompt or embedding sweep. This checker therefore never embeds case text as a
query — doing so would itself be an exposure. Absence is established
structurally (source tree, path roots, file hash) and by scanning the indexed
excerpts for the case *identifiers*, which are not sensitive.

Usage:  python3 scripts/qdrant_canon_ratify.py [output.json]
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

QDRANT = "http://127.0.0.1:6333"
OLLAMA = "http://127.0.0.1:11434"
COLLECTION = "neuruh_canon_v20260816"
EMBEDDING_MODEL = "nomic-embed-text"
HELD_OUT = os.path.expanduser(
    "~/neuruh-workspace/AGI_LEVEL5_CLOSURE_20260816/T4_AGI_EVALUATION/03_HELD_OUT_CASES.json")

# Questions about approved canon content. None of these is a held-out case.
PROBES = [
    "What is the Neuruh founder runtime and what authority does it hold?",
    "How does DeedSonar rank candidate properties?",
    "What is the governance boundary between planning and execution?",
    "How does the vault canon store approved knowledge?",
    "What is AXON responsible for in the Neuruh organism?",
]


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def _post(url: str, body: dict, timeout: int = 120):
    req = urllib.request.Request(
        url, method="POST", data=json.dumps(body).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def embed(text: str) -> list[float]:
    out = _post(f"{OLLAMA}/api/embeddings", {"model": EMBEDDING_MODEL, "prompt": text})
    return out["embedding"]


def scan_index() -> dict:
    """One read-only pass over every point, aggregating provenance."""
    models, dims, trees, roots = Counter(), Counter(), Counter(), Counter()
    shapes = Counter()
    files: set[str] = set()
    shas: set[str] = set()
    outside_vault: list[str] = []
    suspicious_paths: list[str] = []
    total = 0
    nxt = None
    excerpt_digest = hashlib.sha256()
    while True:
        body = {"limit": 1000, "with_payload": True, "with_vector": False}
        if nxt:
            body["offset"] = nxt
        page = _post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", body)["result"]
        for p in page["points"]:
            pl = p.get("payload") or {}
            total += 1
            shapes[tuple(sorted(pl))] += 1
            models[pl.get("embedding_model")] += 1
            dims[pl.get("embedding_dim")] += 1
            trees[pl.get("source_tree")] += 1
            path = str(pl.get("path") or "")
            abs_path = str(pl.get("abs_path") or "")
            roots[path.split("/")[0] if "/" in path else path] += 1
            files.add(path)
            if pl.get("sha256"):
                shas.add(str(pl["sha256"]))
            if abs_path and "/neuruh-vault/" not in abs_path:
                outside_vault.append(abs_path)
            low = (path + " " + abs_path).lower()
            if any(m in low for m in ("held_out", "held-out", "heldout",
                                      "agi_level5", "neuruh-workspace", "eval_cases")):
                suspicious_paths.append(abs_path or path)
            excerpt_digest.update(str(pl.get("excerpt") or "").encode())
        nxt = page.get("next_page_offset")
        if not nxt:
            break
    return {
        "points_scanned": total,
        "embedding_models": dict(models),
        "embedding_dims": {str(k): v for k, v in dims.items()},
        "source_trees": dict(trees),
        "path_roots": dict(roots),
        "distinct_files": len(files),
        "distinct_file_sha256": len(shas),
        "payload_shape_count": len(shapes),
        "payload_shape": sorted(list(shapes)[0]) if shapes else [],
        "abs_paths_outside_vault": outside_vault[:20],
        "suspicious_paths": suspicious_paths[:20],
        "corpus_excerpt_digest": excerpt_digest.hexdigest(),
        "_file_sha256_set": shas,
    }


def held_out_contamination(index: dict) -> dict:
    """Prove the sealed T4 corpus is not in the index, without exposing it."""
    result: dict = {"held_out_file": HELD_OUT, "file_present": os.path.isfile(HELD_OUT)}
    if not result["file_present"]:
        result["verdict"] = "HELD_OUT_FILE_NOT_FOUND"
        return result
    raw = Path(HELD_OUT).read_bytes()
    result["held_out_file_sha256"] = hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    case_ids = [str(c.get("id")) for c in doc.get("cases", []) if c.get("id")]
    result["held_out_case_count"] = len(case_ids)

    # 1) the sealed file's own hash must not be among the indexed file hashes
    result["file_hash_indexed"] = result["held_out_file_sha256"] in index["_file_sha256_set"]
    # 2) nothing indexed may come from outside the vault tree
    result["indexed_outside_vault"] = len(index["abs_paths_outside_vault"])
    # 3) no indexed path may look like the sealed corpus
    result["suspicious_indexed_paths"] = index["suspicious_paths"]
    # 4) case IDENTIFIERS (not case text) must not appear in any indexed excerpt
    hits = []
    for cid in case_ids:
        found = _post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", {
            "limit": 1, "with_payload": True, "with_vector": False,
            "filter": {"must": [{"key": "excerpt", "match": {"text": cid}}]},
        })["result"]["points"]
        if found:
            hits.append(cid)
    result["case_id_hits"] = hits
    result["contaminated"] = bool(
        result["file_hash_indexed"] or result["indexed_outside_vault"]
        or result["suspicious_indexed_paths"] or hits)
    result["verdict"] = "CONTAMINATED" if result["contaminated"] else "CLEAN"
    result["method"] = (
        "structural (source tree, path roots, file hash) plus case-identifier scan; "
        "case text is never embedded or queried, per the T4 seal policy")
    return result


# Deliberate nonsense. These exist to show what an absolute cosine score is
# worth on this model — which turns out to be nothing.
NEGATIVE_CONTROLS = [
    "zxqv plorbish frimble wug nardle bleep quonk",
    "recipe for Neapolitan pizza dough with 72 hour cold ferment",
    "a3f9c2e18b74d05a6f1e9c8b2d4a7f60e3b1c9d8",
]


def _is_front_matter(point: dict) -> bool:
    """True for a templated YAML front-matter chunk."""
    payload = point.get("payload") or {}
    excerpt = str(payload.get("excerpt") or "")
    head = excerpt.lstrip()[:400]
    return head.startswith("---") and "asset_id:" in head


def _search(vec: list[float], limit: int = 5) -> list[dict]:
    return _post(f"{QDRANT}/collections/{COLLECTION}/points/search", {
        "vector": vec, "limit": limit, "with_payload": True, "with_vector": False})["result"]


def negative_controls() -> dict:
    """Establish the noise floor before claiming anything about relevance."""
    out = []
    for q in NEGATIVE_CONTROLS:
        res = _search(embed(q), limit=1)
        out.append({"query": q, "top_score": round(float(res[0]["score"]), 6) if res else 0.0})
    return {"controls": out, "max_noise_score": max(c["top_score"] for c in out)}


def self_retrieval(sample_size: int = 12) -> dict:
    """Ground-truthed retrieval: embed an indexed excerpt, expect its own chunk.

    This is the probe that actually proves the index works. An absolute cosine
    threshold cannot: on this corpus a random hex string scores ~0.78 while a
    genuine canon question scores ~0.65, so "top_score > x" measures nothing.
    Self-retrieval has a known correct answer, so it is falsifiable — and it
    fails loudly if the collection were embedded with a different model, built at
    a different dimension, or indexed against the wrong distance metric.

    Point-level recall can legitimately miss when a document contains
    near-duplicate chunks and a sibling chunk wins, so document-level recall is
    reported alongside it and is the gating measure.
    """
    raw_pool = _post(f"{QDRANT}/collections/{COLLECTION}/points/scroll", {
        "limit": max(sample_size * 20, 400), "with_payload": True, "with_vector": False,
    })["result"]["points"]

    # Exclude YAML front-matter chunks. Every 03-GALAXY node file opens with the
    # same structured header (asset_id / ip_title / type / vertical / ...), so
    # chunk 0 of those documents is boilerplate that differs only in its ID
    # values. Embeddings of such chunks are dominated by the shared template and
    # are legitimately ambiguous — retrieval returning a sibling node's header is
    # correct behaviour on near-identical text, not a defect. Ground truth is
    # therefore measured on prose chunks. The excluded population is measured
    # separately below so this is an accounted-for exclusion, not a quiet one.
    pool = [p for p in raw_pool if not _is_front_matter(p)]
    excluded = [p for p in raw_pool if _is_front_matter(p)]
    step = max(1, len(pool) // sample_size)
    sample = pool[::step][:sample_size]

    cases, p1, p5, d1, d5 = [], 0, 0, 0, 0
    for point in sample:
        payload = point.get("payload") or {}
        excerpt = str(payload.get("excerpt") or "")
        if not excerpt:
            continue
        res = _search(embed(excerpt), limit=5)
        ids = [h["id"] for h in res]
        paths = [(h.get("payload") or {}).get("path") for h in res]
        point_r1 = bool(ids) and ids[0] == point["id"]
        point_r5 = point["id"] in ids
        doc_r1 = bool(paths) and paths[0] == payload.get("path")
        doc_r5 = payload.get("path") in paths
        p1 += point_r1; p5 += point_r5; d1 += doc_r1; d5 += doc_r5
        cases.append({
            "path": payload.get("path"), "chunk": payload.get("chunk"),
            "top_score": round(float(res[0]["score"]), 6) if res else 0.0,
            "point_recall_at_1": point_r1, "point_recall_at_5": point_r5,
            "doc_recall_at_1": doc_r1, "doc_recall_at_5": doc_r5,
        })
    # Diagnostic: measure the excluded front-matter population rather than
    # simply asserting it is ambiguous.
    fm_step = max(1, len(excluded) // 4)
    fm_cases = []
    for point in excluded[::fm_step][:4]:
        payload = point.get("payload") or {}
        res = _search(embed(str(payload.get("excerpt") or "")), limit=5)
        fm_cases.append({
            "path": payload.get("path"),
            "self_returned": point["id"] in [h["id"] for h in res],
            "top_path": (res[0].get("payload") or {}).get("path") if res else None,
            "top_score": round(float(res[0]["score"]), 6) if res else 0.0,
        })

    n = len(cases)
    return {
        "front_matter_excluded_from_pool": len(excluded),
        "front_matter_diagnostic": fm_cases,
        "front_matter_note": (
            "Templated YAML headers are near-identical across 03-GALAXY nodes, so "
            "self-retrieval on them is genuinely ambiguous. Excluded from ground "
            "truth and measured separately."),
        "sample_size": n,
        "point_recall_at_1": f"{p1}/{n}",
        "point_recall_at_5": f"{p5}/{n}",
        "doc_recall_at_1": f"{d1}/{n}",
        "doc_recall_at_5": f"{d5}/{n}",
        "doc_recall_at_5_ratio": (d5 / n) if n else 0.0,
        "point_recall_at_5_ratio": (p5 / n) if n else 0.0,
        "min_top_score": min((c["top_score"] for c in cases), default=0.0),
        "cases": cases,
    }


def retrieval_probes() -> dict:
    """Topical probes plus the ground-truthed and negative-control evidence."""
    noise = negative_controls()
    ground = self_retrieval()

    probes = []
    digest = hashlib.sha256()
    for q in PROBES:
        vec = embed(q)
        res = _search(vec, limit=5)
        hits = [{
            "score": round(float(h["score"]), 6),
            "path": (h.get("payload") or {}).get("path"),
            "chunk": (h.get("payload") or {}).get("chunk"),
        } for h in res]
        top = hits[0]["score"] if hits else 0.0
        probes.append({
            "question": q,
            "vector_dim": len(vec),
            "hits": len(hits),
            "top_score": top,
            "distinct_documents": len({h["path"] for h in hits}),
            "results": hits,
            "returned_results": bool(hits),
        })
        digest.update(f"{q}|{top:.6f}|{hits[0]['path'] if hits else ''}".encode())

    return {
        "model": EMBEDDING_MODEL,
        "probe_count": len(probes),
        "probes": probes,
        "topical_probes_returned_results": all(p["returned_results"] for p in probes),
        "negative_controls": noise,
        "self_retrieval": ground,
        # Gating measure: ground-truthed recall, NOT an absolute score.
        "semantic_retrieval_pass": (
            ground["sample_size"] >= 8
            and ground["doc_recall_at_5_ratio"] == 1.0
            and ground["point_recall_at_5_ratio"] >= 0.9
            and ground["min_top_score"] > noise["max_noise_score"]
        ),
        "threshold_note": (
            "Absolute cosine is not a validity signal for nomic-embed-text on this "
            "corpus: a random hex string outscores a genuine canon question. "
            "Relevance is therefore gated on ground-truthed self-retrieval recall, "
            "with the negative-control noise floor recorded alongside."
        ),
        "retrieval_probe_digest": digest.hexdigest(),
    }


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else ""

    collections = [c["name"] for c in _get(f"{QDRANT}/collections")["result"]["collections"]]
    info = _get(f"{QDRANT}/collections/{COLLECTION}")["result"]
    params = info["config"]["params"]["vectors"]

    tags = _get(f"{OLLAMA}/api/tags")["models"]
    model_row = next((m for m in tags if m["name"].startswith(EMBEDDING_MODEL)), None)

    index = scan_index()
    contamination = held_out_contamination(index)
    index.pop("_file_sha256_set", None)
    retrieval = retrieval_probes()

    identity = {
        "collection": COLLECTION,
        "points_count": info["points_count"],
        "indexed_vectors_count": info["indexed_vectors_count"],
        "vector_size": params["size"],
        "distance": params["distance"],
        "status": info["status"],
        "embedding_model": EMBEDDING_MODEL,
        "embedding_model_digest": (model_row or {}).get("digest"),
        "source_tree": max(index["source_trees"], key=index["source_trees"].get),
        "path_roots": index["path_roots"],
        "distinct_files": index["distinct_files"],
        "corpus_excerpt_digest": index["corpus_excerpt_digest"],
        "retrieval_probe_digest": retrieval["retrieval_probe_digest"],
    }
    identity_hash = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()).hexdigest()

    checks = {
        "collection_exists": COLLECTION in collections,
        "status_green": info["status"] == "green",
        "points_positive": info["points_count"] > 0,
        "all_points_scanned": index["points_scanned"] == info["points_count"],
        "vector_size_768": params["size"] == 768,
        "distance_cosine": params["distance"] == "Cosine",
        "uniform_payload_shape": index["payload_shape_count"] == 1,
        "uniform_embedding_model": list(index["embedding_models"]) == [EMBEDDING_MODEL],
        "uniform_embedding_dim": list(index["embedding_dims"]) == ["768"],
        "declared_dim_matches_collection": (
            list(index["embedding_dims"]) == [str(params["size"])]),
        "uniform_source_tree": len(index["source_trees"]) == 1,
        "embedding_model_available_live": model_row is not None,
        "probe_vector_dim_matches": all(
            p["vector_dim"] == params["size"] for p in retrieval["probes"]),
        "semantic_retrieval_pass": retrieval["semantic_retrieval_pass"],
        "topical_probes_returned_results": retrieval["topical_probes_returned_results"],
        "ground_truth_recall_beats_noise_floor": (
            retrieval["self_retrieval"]["min_top_score"]
            > retrieval["negative_controls"]["max_noise_score"]),
        "no_held_out_contamination": not contamination["contaminated"],
    }

    doc = {
        "schema_version": "fr.qdrant_canon_attestation.v1",
        "ratified_for": "recipe-engine Founder Runtime live retrieval court",
        "collections_present": collections,
        "identity": identity,
        "identity_hash": identity_hash,
        "index_scan": index,
        "held_out_contamination": contamination,
        "retrieval": retrieval,
        "checks": checks,
        "QDRANT_CURRENT_CANON_RATIFIED": all(checks.values()),
        "mutation_performed": False,
        "read_only": True,
    }
    text = json.dumps(doc, indent=2, sort_keys=True)
    if out_path:
        Path(out_path).write_text(text)
    print(text)
    return 0 if doc["QDRANT_CURRENT_CANON_RATIFIED"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
