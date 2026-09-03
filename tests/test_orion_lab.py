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


def test_source_scan_accepts_explicit_get_at_discovery_edge(tmp_path):
    source = 'from urllib.request import Request\nrequest = Request("https://example.invalid", method="GET")\n'
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is True


def test_source_scan_rejects_get_adapter_outside_discovery(tmp_path):
    source = 'from urllib.request import Request\nRequest("https://example.invalid", method="GET")\n'
    assert scan_source(tmp_path, "src/orion/other.py", source) is False


@pytest.mark.parametrize(
    "source",
    [
        'import urllib.request\n',
        'import urllib\nurllib.request.Request("x", method="GET")\n',
        'import urllib as u\nu.request.Request("x", method="GET")\n',
        'from urllib import request\n',
        'from urllib.request import urlopen\n',
        'from urllib.request import build_opener\n',
        'from urllib.request import Request as HTTPRequest\n',
    ],
)
def test_source_scan_rejects_broad_or_aliased_urllib_imports(tmp_path, source):
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


@pytest.mark.parametrize(
    "call",
    [
        'Request("https://example.invalid")',
        'Request("https://example.invalid", method=method)',
        'Request("https://example.invalid", method="POST")',
        'Request("https://example.invalid", method="PUT")',
        'Request("https://example.invalid", method="PATCH")',
        'Request("https://example.invalid", method="DELETE")',
        'Request("https://example.invalid", data=b"body", method="GET")',
        'Request("https://example.invalid", b"body", method="GET")',
        'Request(*arguments, method="GET")',
        'Request("https://example.invalid", method="GET", **options)',
    ],
)
def test_source_scan_rejects_request_without_provable_bodyless_get(tmp_path, call):
    source = f"from urllib.request import Request\n{call}\n"
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


def test_source_scan_checks_every_request_call(tmp_path):
    source = '''from urllib.request import Request
Request("https://example.invalid/one", method="GET")
Request("https://example.invalid/two", method="POST")
'''
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


@pytest.mark.parametrize(
    "source",
    [
        'from .urllib.request import Request\nRequest("x", method="GET")\n',
        'from urllib.request import Request\ndef Request(url): return url\nRequest("x", method="GET")\n',
        'from urllib.request import Request\ndef make(Request): return Request("x", method="GET")\n',
        'from urllib.request import Request\nfactory = Request\nfactory("x", method="GET")\n',
    ],
)
def test_source_scan_rejects_nonexact_or_shadowed_request_binding(tmp_path, source):
    assert scan_source(tmp_path, "src/orion/discovery/adapter.py", source) is False


def test_source_scan_fails_closed_for_invalid_production_python(tmp_path):
    assert scan_source(tmp_path, "src/orion/discovery/broken.py", "def broken(:\n") is False


@pytest.mark.parametrize("module", ["requests", "httpx", "socket"])
def test_source_scan_preserves_general_network_rejections(tmp_path, module):
    assert scan_source(tmp_path, "src/orion/change.py", f"import {module}\n") is False


@pytest.mark.parametrize(
    "source",
    [
        'command = ["git", "merge", "topic"]\n',
        'command = ["git", "reset", "--hard"]\n',
        'command = ["git", "stash", "pop"]\n',
        'command = ["git", "stash", "drop"]\n',
        'mode = "danger-full-access"\n',
        'mode = "dangerously-bypass-approvals-and-sandbox"\n',
    ],
)
def test_source_scan_preserves_destructive_command_rejections(tmp_path, source):
    assert scan_source(tmp_path, "tools/change.py", source) is False


def test_source_scan_accepts_normal_production_change(tmp_path):
    assert scan_source(tmp_path, "src/orion/change.py", "value = 1\n") is True


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
