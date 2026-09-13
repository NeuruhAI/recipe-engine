"""
db_authority.py — ONE database authority for everything recipe-engine persists.

Why this exists (T4 topology convergence, 2026-09-13): the daily hunt kept
writing recipe.sale_events / recipe.run_manifests — and, through the engine's
/evaluate, landos.identity_nodes + landos.scores — into the legacy spine
`lvdimujkfheeyqchjzra` for four days after DeedSonar's production authority
moved to `efxowqudmsfqjmbeidjc` (2026-09-08 Pro cutover), because run.py read
whatever `DATABASE_URL` held. That is exactly how efxo landos.scores fell 47
rows behind lvdi (5,230 vs 5,277). Nothing in this repo may write to a
database it cannot prove is production or a local court.

Rules:
  * Sources, in order: DEEDSONAR_DATABASE_URL (the canonical variable every
    DeedSonar service shares), then the legacy-named DATABASE_URL. Same
    resolver everywhere; no script reads DATABASE_URL directly.
  * The retired spine is REFUSED wherever it appears (DSN, PGHOST, PGUSER).
    There is no override: a parity/forensic read of lvdi is an operator
    psql session, never a recipe-engine code path.
  * Any other Supabase project is refused (one production database).
  * A loopback / unix-socket host is LOCAL (the court database used by the
    tests and the from-zero proofs).
  * Any other host is refused unless RECIPE_ENGINE_ALLOW_NONCANONICAL=1
    names a deliberate disposable replica.
  * Before the first write, `assert_write_targets` proves the resolved
    database actually carries the unique indexes every ON CONFLICT in
    persist.py depends on. A database that has the tables but not the keys
    (the efxo restore of 2026-09-10 lost the whole post-data section) fails
    CLOSED here instead of half-way through a run.

No function in this module connects to a database on its own.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import unquote, urlsplit

CANONICAL_REF = "efxowqudmsfqjmbeidjc"
RETIRED_REFS = {
    "lvdimujkfheeyqchjzra": (
        "legacy spine, retired as DeedSonar's database at the 2026-09-08 Pro "
        "cutover; parity/forensic reads only"),
}
CANONICAL_VAR = "DEEDSONAR_DATABASE_URL"
FALLBACK_VAR = "DATABASE_URL"
ALLOW_NONCANONICAL_VAR = "RECIPE_ENGINE_ALLOW_NONCANONICAL"

PRODUCTION = "PRODUCTION"
LOCAL = "LOCAL"
NONCANONICAL_ALLOWED = "NONCANONICAL_ALLOWED"

#: (schema, table, columns) — the conflict target of every ON CONFLICT in
#: persist.py. Each needs a non-partial UNIQUE index on exactly these columns.
WRITE_TARGETS = (
    ("recipe", "sale_events", ("listing_id",)),
    ("recipe", "run_manifests", ("run_id",)),
)

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
_REF_RE = re.compile(r"^[a-z0-9]{20}$")
_PLACEHOLDER_PREFIXES = ("replace", "postgresql://placeholder")


class DatabaseAuthorityError(RuntimeError):
    """The DSN is missing, is not the authority, or the authority is not write-ready.
    Exit; never write."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class DatabaseAuthority:
    dsn: str
    source: str             # DEEDSONAR_DATABASE_URL | DATABASE_URL
    klass: str              # PRODUCTION | LOCAL | NONCANONICAL_ALLOWED
    ref: Optional[str]      # Supabase project ref when the DSN names one
    host: str

    def describe(self) -> str:
        return (f"DB_AUTHORITY={self.ref or self.host or 'local-socket'} "
                f"class={self.klass} source={self.source}")


def _conninfo_pairs(dsn: str) -> dict:
    """Parse libpq key=value conninfo (the non-URI form). Quoted values allowed."""
    out = {}
    for m in re.finditer(r"(\w+)\s*=\s*('(?:[^'\\]|\\.)*'|\S+)", dsn):
        v = m.group(2)
        if v.startswith("'") and v.endswith("'"):
            v = v[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        out[m.group(1).lower()] = v
    return out


def _host_user(dsn: str, env: Mapping[str, str]) -> tuple[str, str]:
    if dsn.startswith(("postgres://", "postgresql://")):
        parts = urlsplit(dsn)
        host = unquote(parts.hostname or "")
        user = unquote(parts.username or "")
        if not host:  # postgresql:///db?host=/tmp — host in the query string
            q = dict(p.split("=", 1) for p in parts.query.split("&") if "=" in p)
            host = unquote(q.get("host", ""))
            user = user or unquote(q.get("user", ""))
    else:
        kv = _conninfo_pairs(dsn)
        host = kv.get("host") or kv.get("hostaddr") or ""
        user = kv.get("user") or ""
    return (host or env.get("PGHOST", "")).strip().lower(), (user or env.get("PGUSER", "")).strip()


def supabase_ref(host: str, user: str) -> Optional[str]:
    """The project ref from a direct host (db.<ref>.supabase.co) or a pooler user (postgres.<ref>)."""
    if host.endswith((".supabase.co", ".supabase.com")):
        for label in host.split("."):
            if _REF_RE.match(label):
                return label
    if "." in user:
        tail = user.rsplit(".", 1)[1].lower()
        if _REF_RE.match(tail):
            return tail
    return None


def _is_set(value: Optional[str]) -> bool:
    v = (value or "").strip()
    return bool(v) and not v.startswith(_PLACEHOLDER_PREFIXES) and "[YOUR-PASSWORD]" not in v


def classify(dsn: str, source: str, env: Mapping[str, str]) -> DatabaseAuthority:
    blob = " ".join([dsn, env.get("PGHOST", ""), env.get("PGUSER", ""),
                     env.get("PGOPTIONS", "")]).lower()
    for retired, why in RETIRED_REFS.items():
        if retired in blob:
            raise DatabaseAuthorityError(
                "LEGACY_DSN_REFUSED",
                f"{source} points at {retired} ({why}). Production is {CANONICAL_REF}; set "
                f"{CANONICAL_VAR} to that DSN. There is no override.")
    host, user = _host_user(dsn, env)
    ref = supabase_ref(host, user)
    if ref == CANONICAL_REF:
        return DatabaseAuthority(dsn, source, PRODUCTION, ref, host)
    if ref is not None or "supabase." in host:
        raise DatabaseAuthorityError(
            "NONCANONICAL_SUPABASE_REFUSED",
            f"{source} names Supabase project {ref or '(unreadable ref)'}; DeedSonar has one "
            f"production database, {CANONICAL_REF}.")
    if host in _LOOPBACK or host == "" or host.startswith("/"):
        return DatabaseAuthority(dsn, source, LOCAL, None, host)
    if (env.get(ALLOW_NONCANONICAL_VAR) or "").strip() == "1":
        return DatabaseAuthority(dsn, source, NONCANONICAL_ALLOWED, None, host)
    raise DatabaseAuthorityError(
        "UNPROVEN_HOST_REFUSED",
        f"{source} host {host!r} is neither {CANONICAL_REF} nor a local database, so the DSN "
        f"cannot prove which project it reaches. Use the Supabase pooler/direct DSN, or set "
        f"{ALLOW_NONCANONICAL_VAR}=1 for a deliberate disposable replica.")


def resolve_database_url(env: Optional[Mapping[str, str]] = None) -> DatabaseAuthority:
    """Resolve and check the ONE database this process may write.

    DEEDSONAR_DATABASE_URL wins; the legacy-named DATABASE_URL is consulted only
    when the canonical variable is absent or a placeholder. Raises
    DatabaseAuthorityError when neither is set or the resolved DSN is refused.
    """
    if env is None:
        env = os.environ
    for var in (CANONICAL_VAR, FALLBACK_VAR):
        val = (env.get(var) or "").strip()
        if _is_set(val):
            return classify(val, var, env)
    raise DatabaseAuthorityError(
        "DSN_MISSING",
        f"set {CANONICAL_VAR} (the {CANONICAL_REF} DSN) in the environment or the repo .env.")


_UNIQUE_INDEX_COLUMNS_SQL = """
select array_agg(a.attname order by k.ord) as cols
  from pg_index i
  join pg_class t on t.oid = i.indrelid
  join pg_namespace n on n.oid = t.relnamespace
  join lateral unnest(i.indkey) with ordinality as k(attnum, ord) on true
  join pg_attribute a on a.attrelid = t.oid and a.attnum = k.attnum
 where n.nspname = %s and t.relname = %s
   and i.indisunique and i.indpred is null and i.indexprs is null
 group by i.indexrelid
"""


def write_target_status(cur) -> dict:
    """{'recipe.sale_events(listing_id)': True/False, ...} — True when the
    resolved database has a non-partial UNIQUE index on exactly those columns,
    which is what each ON CONFLICT in persist.py requires. Read-only."""
    status = {}
    for schema, table, cols in WRITE_TARGETS:
        cur.execute(_UNIQUE_INDEX_COLUMNS_SQL, (schema, table))
        present = {tuple(row[0]) for row in cur.fetchall()}
        status[f"{schema}.{table}({', '.join(cols)})"] = tuple(cols) in present
    return status


def assert_write_targets(cur) -> dict:
    """Fail CLOSED before the first write when a conflict target is missing."""
    status = write_target_status(cur)
    missing = [k for k, ok in status.items() if not ok]
    if missing:
        raise DatabaseAuthorityError(
            "WRITE_TARGET_UNREADY",
            "the resolved database lacks the UNIQUE index behind ON CONFLICT for: "
            + ", ".join(missing)
            + ". Apply db/010_recipe_schema.sql (or the post-data repair) there first; "
              "nothing was written.")
    return status
