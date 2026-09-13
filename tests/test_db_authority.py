"""recipe-engine has ONE database authority, and the retired spine is refused.

Three layers (the same shape as landos-engine's contact-authority court):
  1. the resolver (recipe_engine/db_authority.py): the two sources, in order
     (DEEDSONAR_DATABASE_URL, then the legacy-named DATABASE_URL) and every
     refusal — the retired lvdi spine above all, wherever it appears;
  2. the write-target preflight: a database that has the recipe tables but not
     the unique keys persist.py's ON CONFLICT clauses need is reported unready,
     so a run fails CLOSED before its first upsert;
  3. the real entrypoints: run.py (the 06:00 launchd job) and
     scripts/graduate_signals.py pointed at the legacy DSN exit before they
     connect; no persisting script reads DATABASE_URL directly; and the
     deployed .env in this checkout, when present, resolves to production and
     names the retired spine nowhere.

No test connects to any database. Every DSN below points at 127.0.0.1:1, so a
guard regression would surface as a connection error, never as a network write.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recipe_engine.db_authority import (
    ALLOW_NONCANONICAL_VAR,
    CANONICAL_REF,
    CANONICAL_VAR,
    FALLBACK_VAR,
    LOCAL,
    NONCANONICAL_ALLOWED,
    PRODUCTION,
    RETIRED_REFS,
    WRITE_TARGETS,
    DatabaseAuthorityError,
    assert_write_targets,
    resolve_database_url,
    write_target_status,
)

ROOT = Path(__file__).resolve().parent.parent
LEGACY = "lvdimujkfheeyqchjzra"

POOLER_LEGACY = f"postgresql://postgres.{LEGACY}:pw@127.0.0.1:1/postgres"
DIRECT_LEGACY = f"postgresql://postgres:pw@db.{LEGACY}.supabase.co:5432/postgres"
POOLER_CANONICAL = f"postgresql://postgres.{CANONICAL_REF}:pw@127.0.0.1:1/postgres"
DIRECT_CANONICAL = f"postgresql://postgres:pw@db.{CANONICAL_REF}.supabase.co:5432/postgres"
OTHER_SUPABASE = "postgresql://postgres.abcdefghijklmnopqrst:pw@127.0.0.1:1/postgres"
LOCAL_DSN = "postgresql://court:court@127.0.0.1:1/court"
FOREIGN_HOST = "postgresql://u:pw@10.0.0.5:5432/postgres"


# ---- 1. the resolver ---------------------------------------------------------

def test_canonical_variable_wins_over_legacy_named_fallback():
    auth = resolve_database_url({CANONICAL_VAR: POOLER_CANONICAL, FALLBACK_VAR: LOCAL_DSN})
    assert (auth.klass, auth.ref, auth.source) == (PRODUCTION, CANONICAL_REF, CANONICAL_VAR)
    assert auth.dsn == POOLER_CANONICAL


def test_fallback_is_consulted_only_when_canonical_is_absent_or_placeholder():
    auth = resolve_database_url({FALLBACK_VAR: DIRECT_CANONICAL})
    assert (auth.klass, auth.source) == (PRODUCTION, FALLBACK_VAR)
    for placeholder in ("", "   ", "replace-me", "postgresql://placeholder:placeholder@127.0.0.1:5432/placeholder",
                        "postgresql://postgres:[YOUR-PASSWORD]@db.x.supabase.co:5432/postgres"):
        auth = resolve_database_url({CANONICAL_VAR: placeholder, FALLBACK_VAR: DIRECT_CANONICAL})
        assert auth.source == FALLBACK_VAR, placeholder


def test_missing_both_is_dsn_missing():
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({})
    assert e.value.code == "DSN_MISSING"


@pytest.mark.parametrize("var", [CANONICAL_VAR, FALLBACK_VAR])
@pytest.mark.parametrize("dsn", [POOLER_LEGACY, DIRECT_LEGACY,
                                 f"host=127.0.0.1 port=1 user=postgres.{LEGACY} dbname=postgres"])
def test_retired_spine_is_refused_from_either_variable(var, dsn):
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({var: dsn})
    assert e.value.code == "LEGACY_DSN_REFUSED"
    assert LEGACY in str(e.value) and CANONICAL_REF in str(e.value)


def test_retired_spine_is_refused_even_when_smuggled_through_pg_env():
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: "postgresql:///postgres", "PGHOST": f"db.{LEGACY}.supabase.co"})
    assert e.value.code == "LEGACY_DSN_REFUSED"
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: "dbname=postgres", "PGUSER": f"postgres.{LEGACY}"})
    assert e.value.code == "LEGACY_DSN_REFUSED"


def test_retired_spine_has_no_override():
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: POOLER_LEGACY, ALLOW_NONCANONICAL_VAR: "1"})
    assert e.value.code == "LEGACY_DSN_REFUSED"
    assert set(RETIRED_REFS) == {LEGACY}


def test_other_supabase_projects_are_refused():
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: OTHER_SUPABASE})
    assert e.value.code == "NONCANONICAL_SUPABASE_REFUSED"
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: "postgresql://x:y@aws-0-us-east-1.pooler.supabase.com:5432/postgres"})
    assert e.value.code == "NONCANONICAL_SUPABASE_REFUSED"


def test_loopback_and_socket_hosts_are_local_courts():
    assert resolve_database_url({CANONICAL_VAR: LOCAL_DSN}).klass == LOCAL
    assert resolve_database_url({CANONICAL_VAR: "postgresql:///court?host=/tmp/pg"}).klass == LOCAL
    assert resolve_database_url({CANONICAL_VAR: "host=localhost dbname=court"}).klass == LOCAL


def test_unproven_host_is_refused_unless_deliberately_allowed():
    with pytest.raises(DatabaseAuthorityError) as e:
        resolve_database_url({CANONICAL_VAR: FOREIGN_HOST})
    assert e.value.code == "UNPROVEN_HOST_REFUSED"
    auth = resolve_database_url({CANONICAL_VAR: FOREIGN_HOST, ALLOW_NONCANONICAL_VAR: "1"})
    assert auth.klass == NONCANONICAL_ALLOWED


def test_describe_never_leaks_the_password():
    auth = resolve_database_url({CANONICAL_VAR: POOLER_CANONICAL.replace(":pw@", ":s3cr3t@")})
    assert "s3cr3t" not in auth.describe()
    assert CANONICAL_REF in auth.describe()


# ---- 2. the write-target preflight ------------------------------------------

class _FakeCursor:
    """Answers the unique-index query with whatever column sets the fake DB has."""

    def __init__(self, unique_columns_by_table):
        self.by_table = unique_columns_by_table
        self._rows = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        schema, table = params
        self._rows = [(list(cols),) for cols in self.by_table.get((schema, table), [])]

    def fetchall(self):
        return self._rows


def test_write_targets_are_the_persist_conflict_targets():
    persist_src = (ROOT / "recipe_engine" / "persist.py").read_text()
    for schema, table, cols in WRITE_TARGETS:
        assert f"{schema}.{table}" in persist_src
        assert f"on conflict ({', '.join(cols)})" in persist_src


def test_preflight_passes_when_every_conflict_target_has_a_unique_index():
    cur = _FakeCursor({("recipe", "sale_events"): [("listing_id",)],
                       ("recipe", "run_manifests"): [("id",), ("run_id",)]})
    assert write_target_status(cur) == {"recipe.sale_events(listing_id)": True,
                                        "recipe.run_manifests(run_id)": True}
    assert all(assert_write_targets(cur).values())


def test_preflight_fails_closed_when_a_restore_dropped_the_keys():
    # efxo 2026-09-10: tables present, post-data section (keys + indexes) absent.
    cur = _FakeCursor({})
    assert write_target_status(cur) == {"recipe.sale_events(listing_id)": False,
                                        "recipe.run_manifests(run_id)": False}
    with pytest.raises(DatabaseAuthorityError) as e:
        assert_write_targets(cur)
    assert e.value.code == "WRITE_TARGET_UNREADY"
    assert "recipe.sale_events(listing_id)" in str(e.value)
    # a wrong-column or superset index is not the conflict target either
    cur = _FakeCursor({("recipe", "sale_events"): [("listing_id", "source")],
                       ("recipe", "run_manifests"): [("id",)]})
    assert not any(write_target_status(cur).values())


def test_preflight_query_is_read_only_catalog_sql():
    cur = _FakeCursor({})
    write_target_status(cur)
    for sql, _ in cur.executed:
        assert sql.strip().lower().startswith("select")
        assert "pg_index" in sql


# ---- 3. the real entrypoints ------------------------------------------------

def _run(argv, env_overrides):
    """Run an entrypoint with every DB variable pinned so the checkout's own
    .env (loaded with setdefault) cannot fill one in behind the test."""
    env = {k: v for k, v in os.environ.items()
           if k not in {CANONICAL_VAR, FALLBACK_VAR, "PGHOST", "PGUSER", "PGOPTIONS", ALLOW_NONCANONICAL_VAR}}
    env.update({CANONICAL_VAR: "", FALLBACK_VAR: "", "LANDOS_SECRET": "x",
                "TENANT_ID": "00000000-0000-0000-0000-000000000000"})
    env.update(env_overrides)
    return subprocess.run([sys.executable, *argv], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("env", [{CANONICAL_VAR: POOLER_LEGACY}, {FALLBACK_VAR: DIRECT_LEGACY}])
def test_run_py_refuses_the_legacy_spine_before_connecting(env):
    r = _run(["run.py", "--print-authority"], env)
    assert r.returncode == 1, r.stderr
    assert "LEGACY_DSN_REFUSED" in r.stderr
    assert "connection" not in r.stderr.lower()


def test_run_py_with_no_dsn_is_dsn_missing():
    r = _run(["run.py", "--print-authority"], {})
    assert r.returncode == 1
    assert "DSN_MISSING" in r.stderr


def test_run_py_print_authority_reports_the_ref_before_it_connects():
    # a canonical-shaped DSN on a dead port: the resolver accepts it, the
    # read-only probe then fails to connect — proving the order (resolve, then
    # connect) and that the printed authority line carries the ref, not a secret.
    r = _run(["run.py", "--print-authority"], {CANONICAL_VAR: POOLER_CANONICAL.replace(":pw@", ":s3cr3t@")})
    assert r.returncode != 0
    assert f"DB_AUTHORITY={CANONICAL_REF} class=PRODUCTION source={CANONICAL_VAR}" in r.stdout
    assert "s3cr3t" not in r.stdout + r.stderr


def test_graduate_signals_refuses_the_legacy_spine_before_connecting():
    r = _run(["scripts/graduate_signals.py"], {FALLBACK_VAR: POOLER_LEGACY})
    assert r.returncode == 1, r.stderr
    assert "LEGACY_DSN_REFUSED" in r.stderr


_DIRECT_ENV_READ = re.compile(r"""environ(\[["']DATABASE_URL["']\]|\.get\(["']DATABASE_URL["'])|getenv\(["']DATABASE_URL["']""")


def test_every_persisting_script_resolves_through_the_authority():
    """A new script that reads DATABASE_URL directly fails here."""
    offenders = []
    for path in [ROOT / "run.py", *sorted((ROOT / "scripts").glob("*.py")),
                 *sorted((ROOT / "recipe_engine").rglob("*.py"))]:
        src = path.read_text()
        if path.name == "persist.py":
            continue  # persist.connect(dsn) takes the DSN as an argument; its callers resolve it
        if "psycopg.connect(" in src or "persist.connect(" in src:
            if path.name != "db_authority.py" and "resolve_database_url" not in src:
                offenders.append(f"{path.relative_to(ROOT)}: connects without resolve_database_url")
        if _DIRECT_ENV_READ.search(src) and path.name != "db_authority.py":
            offenders.append(f"{path.relative_to(ROOT)}: reads DATABASE_URL directly")
    assert not offenders, offenders


def test_deployed_env_file_resolves_to_production_and_names_no_retired_spine():
    """The 06:00 launchd job runs run.py from this checkout with nothing but
    .env (launchd passes no DB variables). When that file is present, it must
    resolve to production and must not mention the retired spine anywhere —
    not even as a fallback."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        pytest.skip("no deployed .env in this checkout")
    text = env_file.read_text()
    for ref in RETIRED_REFS:
        assert ref not in text.replace(f"# The legacy spine {ref}", "").replace(f"{ref} is retired", ""), \
            "retired spine named as a value in .env"
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    assert FALLBACK_VAR not in env, "the legacy-named DATABASE_URL must not be set in the deployed .env"
    auth = resolve_database_url(env)
    assert (auth.klass, auth.ref, auth.source) == (PRODUCTION, CANONICAL_REF, CANONICAL_VAR)
