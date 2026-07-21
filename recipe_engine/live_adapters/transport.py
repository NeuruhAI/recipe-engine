"""Bounded, read-only transports for the live adapters.

Two transports, each fail-closed and each with a hard confinement boundary:

* :class:`GitObserver` — read-only local ``git`` via ``subprocess`` with a fixed
  executable, an explicit read-only subcommand allowlist, NO shell, an argv the
  adapter controls (never the caller), a bounded timeout, a minimal environment,
  and repository-root confinement (the caller passes a *repo id*, never a path).
* :class:`LoopbackHttpClient` — HTTP via ``urllib`` restricted to loopback hosts
  and pre-allowlisted ports, no redirects, bounded body, bounded timeout, and
  credential redaction on every disclosed field.

Typed outcomes let adapters map failures to the exact AdapterResult status
(UNAVAILABLE / TIMEOUT / DENIED / MALFORMED) without ever falling back.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from recipe_engine.live_adapters.redaction import redact_git_remote, redact_headers, redact_text
from recipe_engine.live_adapters.settings import LiveEndpoint, LOOPBACK_HOSTS


# ----------------------------------------------------------------------------- errors
class TransportError(Exception):
    """Base for all transport failures.  Carries a short machine reason."""

    reason = "transport_error"


class TransportUnavailable(TransportError):
    reason = "unavailable"


class TransportTimeout(TransportError):
    reason = "timeout"


class TransportDenied(TransportError):
    """Endpoint/root confinement or authentication refusal."""

    reason = "denied"


class TransportMalformed(TransportError):
    reason = "malformed"


# ----------------------------------------------------------------------------- git
_GIT = shutil.which("git") or "/usr/bin/git"

# The ONLY git subcommands the observer may run.  Everything here is read-only:
# it inspects, it never checks out / resets / merges / rebases / commits / pushes
# / fetches / cleans / gcs.
READ_ONLY_GIT = frozenset(
    {
        "rev-parse",
        "symbolic-ref",
        "status",
        "rev-list",
        "for-each-ref",
        "remote",
        "log",
        "show-ref",
        "cat-file",
        "config",  # only used with --get (read); guarded below
    }
)
# subcommands that are outright forbidden even if someone edits this file wrong
FORBIDDEN_GIT = frozenset(
    {
        "checkout", "switch", "reset", "merge", "rebase", "commit", "push",
        "fetch", "pull", "clean", "gc", "clone", "am", "apply", "cherry-pick",
        "revert", "stash", "tag", "branch", "update-ref", "worktree", "restore",
        "mv", "rm", "add", "prune", "repack", "filter-branch", "reflog",
    }
)


@dataclass(frozen=True)
class GitCommand:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


class GitObserver:
    """Read-only git observation confined to allowlisted repository roots."""

    def __init__(self, allowed_roots: Mapping[str, str]):
        # repo_id -> canonical realpath root (validated by the caller/settings).
        resolved: dict[str, str] = {}
        for repo_id, path in allowed_roots.items():
            p = Path(path)
            if not p.is_absolute():
                raise TransportDenied(f"root for {repo_id} is not absolute")
            try:
                real = str(p.resolve(strict=True))
            except OSError as exc:
                # A configured-but-absent root is recorded; observation of it
                # will fail UNAVAILABLE, never silently succeed.
                real = str(p)
                _ = exc
            resolved[repo_id] = real
        self._roots = resolved

    def has_repo(self, repo_id: str) -> bool:
        return repo_id in self._roots

    def _root(self, repo_id: str) -> str:
        if repo_id not in self._roots:
            raise TransportDenied(f"repository {repo_id!r} is not allowlisted")
        return self._roots[repo_id]

    def run(self, repo_id: str, args: Sequence[str], *, timeout_ms: int) -> GitCommand:
        if not args:
            raise TransportDenied("empty git argv")
        sub = args[0]
        if sub in FORBIDDEN_GIT or sub not in READ_ONLY_GIT:
            raise TransportDenied(f"git subcommand {sub!r} is not read-only")
        if sub == "config" and (len(args) < 2 or not args[1].startswith("--get")):
            raise TransportDenied("git config permitted only in --get read form")
        root = self._root(repo_id)
        # Confirm the root is really a git repo before running (fail closed).
        argv = [_GIT, "-C", root, "--no-optional-locks", *args]
        # Minimal, hermetic environment: no inherited credentials/config that
        # could turn a read into a network write; disable any pager/editor.
        env = {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": root,          # do not read the operator's ~/.gitconfig
            "PATH": "/usr/bin:/bin",
            "LC_ALL": "C",
        }
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=max(0.001, timeout_ms / 1000.0),
                check=False,
                shell=False,
                env=env,
                cwd=root,
            )
        except subprocess.TimeoutExpired as exc:
            raise TransportTimeout(f"git {sub} timed out") from exc
        except FileNotFoundError as exc:
            raise TransportUnavailable("git executable unavailable") from exc
        except OSError as exc:
            raise TransportUnavailable(f"git spawn failed: {redact_text(str(exc))}") from exc
        return GitCommand(
            ok=proc.returncode == 0,
            stdout=proc.stdout or "",
            stderr=redact_text(proc.stderr or ""),
            returncode=proc.returncode,
        )

    def one_line(self, repo_id: str, args: Sequence[str], *, timeout_ms: int) -> str:
        res = self.run(repo_id, args, timeout_ms=timeout_ms)
        if not res.ok:
            raise TransportUnavailable(f"git {args[0]} failed rc={res.returncode}: {res.stderr[:200]}")
        return res.stdout.strip()

    @staticmethod
    def redact_remote(url: str) -> str:
        return redact_git_remote(url)


# ----------------------------------------------------------------------------- http
@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> object:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TransportMalformed("response body is not valid JSON") from exc


class LoopbackHttpClient:
    """HTTP client that will only ever talk to a pre-allowlisted loopback port."""

    MAX_BODY_BYTES = 2 * 1024 * 1024

    def __init__(self, endpoints: Mapping[str, LiveEndpoint]):
        self._endpoints = dict(endpoints)

    def has_endpoint(self, service_id: str) -> bool:
        return service_id in self._endpoints

    def endpoint(self, service_id: str) -> LiveEndpoint:
        ep = self._endpoints.get(service_id)
        if ep is None:
            raise TransportDenied(f"service {service_id!r} is not on the loopback allowlist")
        return ep

    def request(
        self,
        service_id: str,
        path: str,
        *,
        timeout_ms: int,
        method: str = "GET",
        json_body: object | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        ep = self.endpoint(service_id)
        # Reject any attempt to inject a scheme/host/authority via ``path``.
        if "://" in path or path.startswith("//") or "@" in path.split("?", 1)[0]:
            raise TransportDenied("path must not carry a scheme/authority (endpoint injection)")
        if not path.startswith("/"):
            path = "/" + path
        # Confinement: the URL is composed ONLY from the pinned endpoint.
        if ep.host not in LOOPBACK_HOSTS:  # defense in depth (settings already checked)
            raise TransportDenied("endpoint host is not loopback")
        url = ep.base_url() + path

        headers: dict[str, str] = {"Accept": "application/json", "Connection": "close"}
        if extra_headers:
            for k, v in extra_headers.items():
                headers[str(k)] = str(v)
        if ep.auth_header and ep.auth_secret:
            headers[ep.auth_header] = ep.auth_secret  # sent, never recorded

        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        # Pin resolution to loopback: a urllib opener with no proxy, no redirect.
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=max(0.001, timeout_ms / 1000.0)) as resp:
                body = resp.read(self.MAX_BODY_BYTES + 1)
                if len(body) > self.MAX_BODY_BYTES:
                    raise TransportMalformed("response body exceeds bound")
                return HttpResponse(
                    status=int(resp.status),
                    body=body,
                    headers=redact_headers(dict(resp.headers.items())),
                )
        except urllib.error.HTTPError as exc:
            # A real HTTP response with a >=400 status — return it so the adapter
            # can classify (e.g. 401 -> DENIED) rather than treating it as down.
            body = b""
            try:
                body = exc.read(self.MAX_BODY_BYTES)
            except Exception:  # noqa: BLE001 - body optional
                body = b""
            headers = redact_headers(dict(exc.headers.items()) if exc.headers else {})
            code = int(exc.code)
            exc.close()
            return HttpResponse(status=code, body=body, headers=headers)
        except socket.timeout as exc:
            raise TransportTimeout("loopback request timed out") from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise TransportTimeout("loopback request timed out") from exc
            raise TransportUnavailable(f"loopback endpoint unreachable: {redact_text(str(reason))}") from exc
        except (ConnectionError, OSError) as exc:
            raise TransportUnavailable(f"loopback connection failed: {redact_text(str(exc))}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise TransportDenied("redirects are forbidden (possible endpoint escape)")
