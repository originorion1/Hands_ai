import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "tools" / "orion_lab.py"
SPEC = importlib.util.spec_from_file_location("orion_lab", MODULE_PATH)
assert SPEC and SPEC.loader
lab = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lab
SPEC.loader.exec_module(lab)

BASE = "a" * 40
METADATA = f"""ORION-AUTOMATION:
mode: local-dev
base_sha: {BASE}
branch: codex/example
allow_live_customer_access: false
allow_merge: false
"""


def completed(command, returncode=0, stdout=""):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout)


@pytest.mark.parametrize(
    "text",
    ["", METADATA.replace("base_sha", "base"), METADATA + METADATA],
)
def test_metadata_missing_or_malformed_fails_closed(text):
    with pytest.raises(lab.SafeFail):
        lab.parse_metadata(text)


def test_live_or_merge_permission_is_rejected():
    with pytest.raises(lab.SafeFail, match="live_customer"):
        lab.parse_metadata(METADATA.replace("allow_live_customer_access: false", "allow_live_customer_access: true"))
    with pytest.raises(lab.SafeFail, match="merge_permission"):
        lab.parse_metadata(METADATA.replace("allow_merge: false", "allow_merge: true"))


def test_latest_comment_metadata_supersedes_body():
    newer = METADATA.replace(BASE, "b" * 40)
    contract = lab.issue_contract({"body": METADATA, "comments": [{"body": newer}]})
    assert contract.base_sha == "b" * 40


def test_prompt_is_issue_derived_and_governed():
    contract = lab.parse_metadata(METADATA)
    prompt = lab.build_prompt({"number": 59, "title": "Exact title", "body": "Exact body"}, contract)
    assert "Exact title" in prompt and "Exact body" in prompt
    assert "isolated worktree" in prompt
    assert "Do not merge" in prompt
    assert "Do not commit or push" in prompt
    assert "customer state" in prompt
    assert "pop/drop/reset any stash" in prompt


def test_wrong_base_fails_before_codex(tmp_path):
    calls = []

    def fake(command, **kwargs):
        calls.append(tuple(command))
        if command[:3] == ("git", "rev-parse", "laboratory/orion-v0.1"):
            return completed(command, stdout="b" * 40 + "\n")
        if command[:3] == ("git", "rev-parse", "origin/laboratory/orion-v0.1"):
            return completed(command, stdout=BASE + "\n")
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    with pytest.raises(lab.SafeFail, match="wrong_base"):
        runner.verify_base(lab.parse_metadata(METADATA))
    assert not any(command[:2] == ("codex", "exec") for command in calls)


def test_codex_failure_stops_before_verification_commit_and_push(tmp_path, monkeypatch):
    runner = lab.Orchestrator(tmp_path, run=lambda command, **kwargs: completed(command, 1))
    monkeypatch.setattr(runner, "doctor", lambda: None)
    monkeypatch.setattr(runner, "fetch_issue", lambda number: {
        "number": number, "title": "x", "body": METADATA, "comments": [], "state": "OPEN"
    })
    monkeypatch.setattr(runner, "verify_base", lambda contract: None)
    monkeypatch.setattr(runner, "prepare_worktree", lambda number, contract: tmp_path)
    with pytest.raises(lab.SafeFail, match="codex_failed"):
        runner.run_issue(1)
    assert json.loads((tmp_path / ".orion/automation/issue-1-report.json").read_text())["category"] == "codex_failed"


def test_canonical_dirty_fails_before_codex(tmp_path, monkeypatch):
    runner = lab.Orchestrator(tmp_path)
    codex_called = False

    def dirty():
        raise lab.SafeFail("canonical_dirty")

    def codex(*args):
        nonlocal codex_called
        codex_called = True

    monkeypatch.setattr(runner, "doctor", dirty)
    monkeypatch.setattr(runner, "invoke_codex", codex)
    with pytest.raises(lab.SafeFail, match="canonical_dirty"):
        runner.run_issue(1)
    assert codex_called is False


def test_verification_failure_prevents_commit_push(tmp_path, monkeypatch):
    called = False
    runner = lab.Orchestrator(tmp_path)
    monkeypatch.setattr(runner, "doctor", lambda: None)
    monkeypatch.setattr(runner, "fetch_issue", lambda number: {
        "number": number, "title": "x", "body": METADATA, "comments": [], "state": "OPEN"
    })
    monkeypatch.setattr(runner, "verify_base", lambda contract: None)
    monkeypatch.setattr(runner, "prepare_worktree", lambda number, contract: tmp_path)
    monkeypatch.setattr(runner, "invoke_codex", lambda *args: None)
    monkeypatch.setattr(runner, "_canonical_worktree", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args, **kwargs: BASE if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(runner, "verify", lambda *args: ([lab.CheckResult("ruff", False)], ("x.py",), None))

    def forbidden(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "commit_push_report", forbidden)
    with pytest.raises(lab.SafeFail, match="ruff_failed"):
        runner.run_issue(1)
    assert called is False


def configure_verified_run(runner, tmp_path, monkeypatch, snapshot):
    monkeypatch.setattr(
        runner,
        "fetch_issue",
        lambda number: {
            "number": number,
            "title": "x",
            "body": METADATA,
            "comments": [],
            "state": "OPEN",
        },
    )
    monkeypatch.setattr(runner, "verify_base", lambda contract: None)
    monkeypatch.setattr(runner, "prepare_worktree", lambda number, contract: tmp_path)
    monkeypatch.setattr(runner, "invoke_codex", lambda *args: None)
    monkeypatch.setattr(runner, "_canonical_worktree", lambda: tmp_path)
    monkeypatch.setattr(
        runner,
        "verify",
        lambda *args: ([lab.CheckResult("ruff", True, "passed")], snapshot, 1),
    )


def test_stash_loss_after_verification_prevents_commit_push(tmp_path, monkeypatch):
    stash_checks = 0
    commit_called = False
    snapshot = lab.ChangeSnapshot(("x.py",), "verified")
    runner = lab.Orchestrator(tmp_path)
    configure_verified_run(runner, tmp_path, monkeypatch, snapshot)

    def fake_git(*args, **kwargs):
        nonlocal stash_checks
        if args[:2] == ("stash", "list"):
            stash_checks += 1
            return "preserved-stash" if stash_checks == 1 else ""
        if args[:2] == ("rev-parse", "HEAD"):
            return BASE
        return ""

    def commit(*args):
        nonlocal commit_called
        commit_called = True

    monkeypatch.setattr(runner, "_git", fake_git)
    monkeypatch.setattr(runner, "doctor", runner._ensure_stashes)
    monkeypatch.setattr(runner, "_change_snapshot", lambda *args: snapshot)
    monkeypatch.setattr(runner, "commit_push_report", commit)
    with pytest.raises(lab.SafeFail, match="preserved_stash_changed"):
        runner.run_issue(1)
    assert stash_checks == 2
    assert commit_called is False


@pytest.mark.parametrize(
    "drifted",
    [
        lab.ChangeSnapshot(("x.py", "extra.py"), "drifted"),
        lab.ChangeSnapshot(("x.py",), "changed-content"),
    ],
)
def test_post_verification_change_drift_prevents_commit_push(tmp_path, monkeypatch, drifted):
    commit_called = False
    verified = lab.ChangeSnapshot(("x.py",), "verified")
    runner = lab.Orchestrator(tmp_path)
    configure_verified_run(runner, tmp_path, monkeypatch, verified)
    monkeypatch.setattr(runner, "doctor", lambda: None)
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args, **kwargs: BASE if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(runner, "_change_snapshot", lambda *args: drifted)

    def commit(*args):
        nonlocal commit_called
        commit_called = True

    monkeypatch.setattr(runner, "commit_push_report", commit)
    with pytest.raises(lab.SafeFail, match="verified_changes_changed"):
        runner.run_issue(1)
    assert commit_called is False


def test_unchanged_verified_tree_reaches_success_path(tmp_path, monkeypatch):
    verified = lab.ChangeSnapshot(("x.py",), "verified")
    expected = object()
    runner = lab.Orchestrator(tmp_path)
    configure_verified_run(runner, tmp_path, monkeypatch, verified)
    monkeypatch.setattr(runner, "doctor", lambda: None)
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args, **kwargs: BASE if args[:2] == ("rev-parse", "HEAD") else "",
    )
    monkeypatch.setattr(runner, "_change_snapshot", lambda *args: verified)
    monkeypatch.setattr(runner, "commit_push_report", lambda *args: expected)
    assert runner.run_issue(1) is expected


def test_demo_must_explicitly_deny_execution(tmp_path, monkeypatch):
    (tmp_path / "x.py").write_text("x = 1\n")
    runner = lab.Orchestrator(tmp_path)
    monkeypatch.setattr(runner, "_changed_files", lambda *args: ("x.py",))

    def fake(command, **kwargs):
        if command[-2:] == ["-m", "orion.demo"]:
            return completed(command, stdout='{"execution_allowed": true}')
        return completed(command, stdout="1 passed\n")

    runner.run = fake
    results, _, _ = runner.verify(tmp_path, BASE)
    assert next(item for item in results if item.name == "demo").passed is False


def test_success_uses_feature_push_and_never_merge(tmp_path):
    commands = []

    def fake(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return completed(command, stdout="c" * 40)
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    runner.state_dir.mkdir(parents=True)
    checks = [lab.CheckResult(name, True, "passed") for name in (
        "py_compile", "ruff", "pytest", "demo", "diff_check", "source_capability_scan"
    )]
    report = runner.commit_push_report(59, lab.parse_metadata(METADATA), tmp_path, checks, ("tools/x.py",), 12)
    assert ("git", "push", "origin", "HEAD:codex/example") in commands
    assert not any("merge" in command for command in commands)
    assert report.merge_performed is False and report.live_customer_access is False


def test_report_is_allowlisted_and_sanitized():
    report = lab.ReviewReport(
        issue=1, full_sha="c" * 40, tests=3, ruff="passed", py_compile="passed",
        demo="passed", diff_check="passed", source_capability_scan="passed",
        changed_files=("safe.py",), branch="codex/example", base_sha=BASE,
    )
    rendered = lab.render_report(report)
    assert "merge_performed=false" in rendered
    assert "live_customer_access=false" in rendered
    assert "execution_allowed=false" in rendered
    assert "credential" not in rendered and "transcript" not in rendered


def test_lock_prevents_concurrent_processing(tmp_path):
    runner = lab.Orchestrator(tmp_path)
    with runner.lock(), pytest.raises(lab.SafeFail, match="already_active"), runner.lock():
        pass


def test_source_scan_rejects_network_and_dangerous_codex_mode(tmp_path):
    source = tmp_path / "tool.py"
    source.write_text("import socket\nmode = 'danger-full-access'\n")
    assert lab.Orchestrator.source_capability_scan(tmp_path, ("tool.py",)) is False


def scan_source(tmp_path, name, source):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return lab.Orchestrator.source_capability_scan(tmp_path, (name,))


def test_source_scan_accepts_direct_bodyless_literal_get_at_discovery_edge(tmp_path):
    source = 'from urllib.request import Request\nRequest("https://example.invalid", method="GET")\n'
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is True


@pytest.mark.parametrize(
    "source",
    [
        'value = getattr(record, "status")\n',
        'value = globals()["APPLICATION_SETTING"]\n',
        'value = locals().get("temporary_value")\n',
        'value = vars(record)\n',
        'value = eval("1 + 1")\n',
        'namespace = {}\nexec("result = 1", namespace)\n',
        'from builtins import getattr as resolve\nvalue = resolve(record, "status")\n',
        'import importlib\nmodule = importlib.import_module("decimal")\n',
        'module = __import__("decimal")\n',
    ],
)
def test_source_scan_accepts_provably_unrelated_reflection_helpers(tmp_path, source):
    assert scan_source(tmp_path, "src/orion/service.py", source) is True


@pytest.mark.parametrize(
    "source",
    [
        'import urllib.request\nurllib.request.Request("x", method="GET")\n',
        'from urllib import request\nrequest.Request("x", method="GET")\n',
        'from urllib.request import Request as R\nR("x", method="GET")\n',
        'from urllib.request import urlopen\n',
        'from urllib.request import Request\n',
        '__import__("urllib.request").request.urlopen("x")\n',
        'import importlib\nimportlib.import_module("urllib.request").urlopen("x")\n',
        'from importlib import import_module as load\nload("urllib.request")\n',
        'import builtins\nloader = builtins.__import__\nloader("urllib.request")\n',
        'import importlib\nloader = importlib.import_module\nloader("urllib.request")\n',
        'from builtins import getattr as resolve\nresolve(object(), "request")\n',
        'getattr(__import__("urllib"), "request").urlopen("x")\n',
        'object.__getattribute__(__import__("urllib"), "request")\n',
        'globals()["__builtins__"]["__import__"]("urllib.request")\n',
        'locals()["Request"]("x", method="GET")\n',
        'vars(__builtins__)["__import__"]("urllib.request")\n',
        'vars(__builtins__).get("__import__")("urllib.request")\n',
        'eval("__import__(\\"urllib.request\\")")\n',
        'exec("import urllib.request")\n',
        'module_name = get_module_name()\n__import__(module_name)\n',
        'attribute = get_attribute_name()\ngetattr(module, attribute)\n',
        'source = get_source()\neval(source)\n',
        'from urllib.request import Request\nvalue = globals()["Request"]\n',
    ],
)
def test_source_scan_rejects_import_and_name_resolution_escape_paths(tmp_path, source):
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


@pytest.mark.parametrize(
    "call",
    [
        'Request("x")',
        'Request("x", method=method)',
        'Request("x", method="POST")',
        'Request("x", data=b"body", method="GET")',
        'Request("x", b"body", method="GET")',
        'Request(*arguments, method="GET")',
        'Request("x", method="GET", **options)',
    ],
)
def test_source_scan_rejects_request_without_provable_bodyless_get(tmp_path, call):
    source = f"from urllib.request import Request\n{call}\n"
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


@pytest.mark.parametrize(
    "source",
    [
        'from urllib.request import Request\nfactory = Request\nfactory("x", method="GET")\n',
        'from urllib.request import Request\ndef Request(url): return url\n',
        'from urllib.request import Request\ndef make(Request): return Request("x", method="GET")\n',
    ],
)
def test_source_scan_rejects_shadowed_or_indirect_request_binding(tmp_path, source):
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


def test_source_scan_rejects_get_outside_discovery_and_invalid_python(tmp_path):
    source = 'from urllib.request import Request\nRequest("x", method="GET")\n'
    assert scan_source(tmp_path, "src/orion/other.py", source) is False
    assert scan_source(tmp_path, "src/orion/discovery/broken.py", "def broken(:\n") is False


def test_source_scan_can_scan_its_own_verifier_source():
    root = Path(lab.__file__).resolve().parents[1]
    assert lab.Orchestrator.source_capability_scan(root, ("tools/orion_lab.py",)) is True


def test_stash_baseline_is_preserved_and_never_mutated(tmp_path):
    commands = []

    def fake(command, **kwargs):
        commands.append(tuple(command))
        return completed(command, stdout="stash-object\n")

    runner = lab.Orchestrator(tmp_path, run=fake)
    runner._ensure_stashes()
    runner._ensure_stashes()
    assert json.loads((runner.state_dir / "stash-baseline.json").read_text()) == ["stash-object"]
    assert not any("pop" in command or "drop" in command or "reset" in command for command in commands)
