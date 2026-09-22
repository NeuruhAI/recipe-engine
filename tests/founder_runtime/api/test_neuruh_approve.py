"""neuruh-approve: the founder token comes from the Keychain, and from nowhere else.

A token passed as an argument lands in ~/.zsh_history; one passed as an
environment variable reaches child processes and process listings. The tool
must refuse both BEFORE it touches the Keychain, refuse if the token turns up in
its own argv or environment anyway, read the Keychain once per call (no cache),
and never print the token.
"""

from __future__ import annotations

import io
import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "neuruh-approve"
TOKEN = "frf_" + "Z" * 43


def _load():
    loader = SourceFileLoader("neuruh_approve_under_test", str(SCRIPT))
    spec = spec_from_loader(loader.name, loader)
    mod = module_from_spec(spec)
    loader.exec_module(mod)
    return mod


na = _load()


class Recorder:
    def __init__(self, token=TOKEN):
        self.token = token
        self.reads = 0
        self.http_calls = []

    def read(self):
        self.reads += 1
        return self.token

    def http(self, method, url, token, body=None, headers=None, timeout=120.0):
        self.http_calls.append({"method": method, "url": url, "token": token, "body": body,
                                "headers": headers or {}})
        if method == "GET":
            return 200, {"envelope": {"version": 7, "mission_id": "msn_x"}}
        return 200, {"approval": {"approval_id": "appr_1", "decided_by": "jeramie", "decision": "allow",
                                  "evidence_refs": []},
                     "authority_state": "allow", "envelope": {"state": "AWAITING_APPROVAL", "version": 8}}


def _run(argv, environ=None, rec=None):
    rec = rec or Recorder()
    out, err = io.StringIO(), io.StringIO()
    code = na.main(argv, environ=environ if environ is not None else {}, read_token=rec.read,
                   http=rec.http, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), rec


@pytest.mark.parametrize("argv", [
    ["msn_x", "--reason", "ok", "--token", TOKEN],
    ["msn_x", "--reason", "ok", f"--token={TOKEN}"],
    ["msn_x", "--reason", "ok", "--founder-token", TOKEN],
    ["msn_x", "--reason", "ok", "--founder_token", TOKEN],
    ["msn_x", "--reason", "ok", "-t", TOKEN],
    ["msn_x", "--reason", "ok", "--password", TOKEN],
    ["msn_x", "--reason", "ok", "--bearer", TOKEN],
    ["msn_x", "--reason", "ok", "--secret", TOKEN],
    ["msn_x", "--gate", "--reason", "ok", "--auth", TOKEN],
])
def test_a_token_flag_is_refused_before_the_keychain_is_touched(argv):
    code, out, err, rec = _run(argv)
    assert code == na.EXIT_REFUSED
    assert rec.reads == 0 and rec.http_calls == []
    assert "Keychain only" in err
    assert TOKEN not in out and TOKEN not in err


@pytest.mark.parametrize("name", na.FORBIDDEN_ENV)
def test_a_token_env_var_is_refused_before_the_keychain_is_touched(name):
    code, out, err, rec = _run(["msn_x", "--reason", "ok"], environ={name: TOKEN})
    assert code == na.EXIT_REFUSED
    assert rec.reads == 0 and rec.http_calls == []
    assert name in err and TOKEN not in err


@pytest.mark.parametrize("argv", [
    [TOKEN, "--reason", "ok"],                    # pasted as the mission id
    ["msn_x", "--reason", f"approve {TOKEN}"],    # pasted into the reason
])
def test_the_token_in_argv_is_refused_and_never_sent(argv):
    code, out, err, rec = _run(argv)
    assert code == na.EXIT_REFUSED
    assert rec.reads == 1 and rec.http_calls == []
    assert "rotate" in err and TOKEN not in err and TOKEN not in out


def test_the_token_in_any_env_value_is_refused_and_never_sent():
    code, out, err, rec = _run(["msn_x", "--reason", "ok"], environ={"SOME_OTHER_VAR": TOKEN})
    assert code == na.EXIT_REFUSED
    assert rec.http_calls == []
    assert TOKEN not in err


def test_happy_path_reads_the_keychain_once_and_never_prints_the_token():
    code, out, err, rec = _run(["msn_x", "--gate", "--reason", "ship it"])
    assert code == na.EXIT_OK
    assert rec.reads == 1
    get, post = rec.http_calls
    assert get["method"] == "GET" and post["method"] == "POST"
    assert post["url"].endswith("/v1/missions/msn_x/gate/approve")
    assert post["url"].startswith("http://127.0.0.1:")
    assert post["token"] == TOKEN and get["token"] == TOKEN
    assert post["headers"]["X-Expected-Version"] == "7"
    assert post["headers"]["Idempotency-Key"].startswith("neuruh-approve:msn_x:gate:")
    assert post["body"] == {"reason": "ship it"}
    assert TOKEN not in out and TOKEN not in err
    assert json.loads(out)["decided_by"] == "jeramie"


def test_plan_approval_uses_the_plan_route():
    code, out, _, rec = _run(["msn_x", "--reason", "ok", "--expected-version", "3"])
    assert code == na.EXIT_OK
    assert len(rec.http_calls) == 1  # explicit version: no GET
    assert rec.http_calls[0]["url"].endswith("/v1/missions/msn_x/approve")
    assert rec.http_calls[0]["headers"]["X-Expected-Version"] == "3"


def test_the_token_is_not_cached_between_calls():
    rec = Recorder()
    _run(["msn_x", "--reason", "ok"], rec=rec)
    _run(["msn_y", "--reason", "ok"], rec=rec)
    assert rec.reads == 2


def test_a_keychain_failure_sends_nothing():
    class Denied(Recorder):
        def read(self):
            self.reads += 1
            raise na.Refused("Keychain read denied", na.EXIT_KEYCHAIN)
    rec = Denied()
    code, _, err, _ = _run(["msn_x", "--reason", "ok"], rec=rec)
    assert code == na.EXIT_KEYCHAIN and rec.http_calls == []


def test_there_is_no_way_to_name_a_host_or_a_token():
    helptext = na.build_parser().format_help()
    for flag in ("--token", "--host", "--founder-token", "--password"):
        assert flag not in helptext
    assert na.HOST == "127.0.0.1"


def test_reason_is_required_and_unknown_flags_are_refused():
    assert _run(["msn_x"])[0] == na.EXIT_REFUSED
    assert _run(["msn_x", "--reason", "ok", "--frobnicate"])[0] == na.EXIT_REFUSED


def test_the_keychain_reader_asks_for_exactly_the_founder_item(monkeypatch):
    seen = {}

    class P:
        returncode = 0
        stdout = TOKEN + "\n"

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return P()
    monkeypatch.setattr(na.subprocess, "run", fake_run)
    assert na.read_keychain_token() == TOKEN
    assert seen["cmd"] == ["/usr/bin/security", "find-generic-password", "-s", "neuruh-fr-founder-token",
                           "-a", "jeramie", "-w"]


def test_end_to_end_against_a_founder_runtime(server_factory, tmp_path, founder_token):
    """The real transport, a real server, the founder credential from a 'Keychain'."""
    from tests.founder_runtime.api.test_founder_approval_auth import _park
    client, port = server_factory("e2e")
    mid = _park(str(tmp_path / "e2e.sqlite"), "neuruh-approve-e2e")
    out, err = io.StringIO(), io.StringIO()
    code = na.main([mid, "--gate", "--reason", "e2e", "--port", str(port)], environ={},
                   read_token=lambda: founder_token, out=out, err=err)
    assert code == na.EXIT_OK, err.getvalue()
    summary = json.loads(out.getvalue())
    assert summary["decided_by"] == "jeramie" and summary["decision"] == "allow"
    assert founder_token not in out.getvalue()
    # And the operator token, sent the same way, is refused by the server.
    out2, err2 = io.StringIO(), io.StringIO()
    code2 = na.main([mid, "--reason", "e2e", "--port", str(port)], environ={},
                    read_token=lambda: client._token, out=out2, err=err2)
    assert code2 == na.EXIT_HTTP
    assert json.loads(out2.getvalue())["error"]["code"] == "OPERATOR_TOKEN_NOT_FOUNDER"
