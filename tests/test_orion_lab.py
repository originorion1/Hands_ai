import importlib.util
import json
import subprocess
import sys
import textwrap
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


def scan_source(tmp_path, source, name="src/orion/discovery/probe.py"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return lab.Orchestrator.source_capability_scan(tmp_path, (name,))


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
        if command[:4] == ("gh", "pr", "list", "--state"):
            return completed(command, stdout="[]")
        if command[:3] == ("gh", "pr", "create"):
            return completed(command, stdout="https://github.com/example/repo/pull/7\n")
        if command[:3] == ("gh", "pr", "view"):
            return completed(
                command,
                stdout=json.dumps(
                    {
                        "number": 7,
                        "url": "https://github.com/example/repo/pull/7",
                        "isDraft": True,
                        "state": "OPEN",
                        "headRefName": "codex/example",
                        "baseRefName": "laboratory/orion-v0.1",
                    }
                ),
            )
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    runner.state_dir.mkdir(parents=True)
    checks = [lab.CheckResult(name, True, "passed") for name in (
        "py_compile", "ruff", "pytest", "demo", "diff_check", "source_capability_scan"
    )]
    report = runner.commit_push_report(59, lab.parse_metadata(METADATA), tmp_path, checks, ("tools/x.py",), 12)
    assert ("git", "push", "origin", "HEAD:codex/example") in commands
    assert any(command[:3] == ("gh", "pr", "create") for command in commands)
    assert not any("merge" in command for command in commands)
    assert report.merge_performed is False and report.live_customer_access is False
    assert report.pull_request_number == 7
    assert report.pull_request_url == "https://github.com/example/repo/pull/7"
    assert report.pull_request_draft is True


def test_matching_open_draft_pull_request_is_refreshed_and_reused(tmp_path):
    commands = []
    existing = {
        "number": 7,
        "url": "https://github.com/example/repo/pull/7",
        "isDraft": True,
        "state": "OPEN",
        "headRefName": "codex/example",
        "baseRefName": "laboratory/orion-v0.1",
    }

    def fake(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == ("gh", "pr", "list"):
            return completed(command, stdout=json.dumps([existing]))
        if command[:3] == ("gh", "pr", "view"):
            return completed(command, stdout=json.dumps(existing))
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    reference = runner.ensure_draft_pull_request(
        69, "Automate reviewer handoff", lab.parse_metadata(METADATA), tmp_path
    )
    assert reference.number == 7 and reference.draft is True
    assert any(command[:3] == ("gh", "pr", "edit") for command in commands)
    assert not any(command[:3] == ("gh", "pr", "create") for command in commands)


def test_matching_ready_pull_request_is_returned_to_draft(tmp_path):
    ready = {
        "number": 7,
        "url": "https://github.com/example/repo/pull/7",
        "isDraft": False,
        "state": "OPEN",
        "headRefName": "codex/example",
        "baseRefName": "laboratory/orion-v0.1",
    }
    draft = {**ready, "isDraft": True}
    commands = []

    def fake(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == ("gh", "pr", "list"):
            return completed(command, stdout=json.dumps([ready]))
        if command[:3] == ("gh", "pr", "view"):
            return completed(command, stdout=json.dumps(draft))
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    reference = runner.ensure_draft_pull_request(
        69, "Automate reviewer handoff", lab.parse_metadata(METADATA), tmp_path
    )
    assert reference.draft is True
    assert any(command[:4] == ("gh", "pr", "ready", "7") for command in commands)
    assert not any(command[:3] == ("gh", "pr", "create") for command in commands)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("headRefName", "codex/other"),
        ("baseRefName", "main"),
        ("state", "CLOSED"),
    ],
)
def test_existing_pull_request_with_wrong_scope_is_rejected(tmp_path, field, value):
    existing = {
        "number": 7,
        "url": "https://github.com/example/repo/pull/7",
        "isDraft": True,
        "state": "OPEN",
        "headRefName": "codex/example",
        "baseRefName": "laboratory/orion-v0.1",
    }
    existing[field] = value
    commands = []

    def fake(command, **kwargs):
        commands.append(tuple(command))
        return completed(command, stdout=json.dumps([existing]))

    runner = lab.Orchestrator(tmp_path, run=fake)
    with pytest.raises(lab.SafeFail, match="pull_request_scope_mismatch"):
        runner.ensure_draft_pull_request(
            69, "Automate reviewer handoff", lab.parse_metadata(METADATA), tmp_path
        )
    assert not any(command[:3] == ("gh", "pr", "create") for command in commands)


def test_multiple_open_pull_requests_for_branch_are_rejected(tmp_path):
    pull = {
        "number": 7,
        "url": "https://github.com/example/repo/pull/7",
        "isDraft": True,
        "state": "OPEN",
        "headRefName": "codex/example",
        "baseRefName": "laboratory/orion-v0.1",
    }
    runner = lab.Orchestrator(
        tmp_path,
        run=lambda command, **kwargs: completed(command, stdout=json.dumps([pull, pull])),
    )
    with pytest.raises(lab.SafeFail, match="pull_request_ambiguous"):
        runner.ensure_draft_pull_request(
            69, "Automate reviewer handoff", lab.parse_metadata(METADATA), tmp_path
        )


def test_pull_request_creation_occurs_after_commit_and_push(tmp_path):
    commands = []
    created = {
        "number": 7,
        "url": "https://github.com/example/repo/pull/7",
        "isDraft": True,
        "state": "OPEN",
        "headRefName": "codex/example",
        "baseRefName": "laboratory/orion-v0.1",
    }

    def fake(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return completed(command, stdout="c" * 40)
        if command[:3] == ("gh", "pr", "list"):
            return completed(command, stdout="[]")
        if command[:3] == ("gh", "pr", "create"):
            return completed(command, stdout=created["url"])
        if command[:3] == ("gh", "pr", "view"):
            return completed(command, stdout=json.dumps(created))
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    runner.state_dir.mkdir(parents=True)
    checks = [lab.CheckResult(name, True, "passed") for name in (
        "py_compile", "ruff", "pytest", "demo", "diff_check", "source_capability_scan"
    )]
    runner.commit_push_report(
        69,
        lab.parse_metadata(METADATA),
        tmp_path,
        checks,
        ("tools/x.py",),
        12,
        issue_title="Automate reviewer handoff",
    )
    positions = {prefix: next(i for i, command in enumerate(commands) if command[:3] == prefix) for prefix in (
        ("git", "commit", "-m"),
        ("git", "push", "origin"),
        ("gh", "pr", "create"),
        ("gh", "issue", "comment"),
    )}
    assert positions[("git", "commit", "-m")] < positions[("git", "push", "origin")]
    assert positions[("git", "push", "origin")] < positions[("gh", "pr", "create")]
    assert positions[("gh", "pr", "create")] < positions[("gh", "issue", "comment")]


def test_pull_request_creation_failure_stops_before_issue_report(tmp_path):
    commands = []

    def fake(command, **kwargs):
        commands.append(tuple(command))
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return completed(command, stdout="c" * 40)
        if command[:3] == ("gh", "pr", "list"):
            return completed(command, stdout="[]")
        if command[:3] == ("gh", "pr", "create"):
            return completed(command, returncode=1)
        return completed(command)

    runner = lab.Orchestrator(tmp_path, run=fake)
    checks = [lab.CheckResult(name, True, "passed") for name in (
        "py_compile", "ruff", "pytest", "demo", "diff_check", "source_capability_scan"
    )]
    with pytest.raises(lab.SafeFail, match="command_failed:gh"):
        runner.commit_push_report(
            69, lab.parse_metadata(METADATA), tmp_path, checks, ("tools/x.py",), 12
        )
    assert not any(command[:3] == ("gh", "issue", "comment") for command in commands)
    assert not any("merge" in command or "reset" in command for command in commands)


def test_verified_run_reaches_pull_request_handoff_only_after_verification(
    tmp_path, monkeypatch
):
    events = []
    snapshot = lab.ChangeSnapshot(("x.py",), "verified")
    runner = lab.Orchestrator(tmp_path)
    monkeypatch.setattr(runner, "doctor", lambda: None)
    monkeypatch.setattr(
        runner,
        "fetch_issue",
        lambda number: {
            "number": number,
            "title": "Automate reviewer handoff",
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
        "_git",
        lambda *args, **kwargs: BASE if args[:2] == ("rev-parse", "HEAD") else "",
    )

    def verify(*args):
        events.append("verify")
        return [lab.CheckResult("ruff", True, "passed")], snapshot, 1

    monkeypatch.setattr(runner, "verify", verify)
    monkeypatch.setattr(runner, "_change_snapshot", lambda *args: snapshot)

    def handoff(*args):
        events.append("handoff")
        return object()

    monkeypatch.setattr(runner, "commit_push_report", handoff)
    runner.run_issue(69)
    assert events == ["verify", "handoff"]


def test_report_is_allowlisted_and_sanitized():
    report = lab.ReviewReport(
        issue=1, full_sha="c" * 40, tests=3, ruff="passed", py_compile="passed",
        demo="passed", diff_check="passed", source_capability_scan="passed",
        changed_files=("safe.py",), branch="codex/example", base_sha=BASE,
        pull_request_number=7,
        pull_request_url="https://github.com/example/repo/pull/7",
        pull_request_draft=True,
    )
    rendered = lab.render_report(report)
    assert "merge_performed=false" in rendered
    assert "live_customer_access=false" in rendered
    assert "execution_allowed=false" in rendered
    assert "https://github.com/example/repo/pull/7" in rendered
    assert "pull_request_draft=true" in rendered
    assert "credential" not in rendered and "transcript" not in rendered


def test_claude_review_workflow_is_read_only_scoped_and_fixed():
    workflow = (
        MODULE_PATH.parents[1] / ".github/workflows/claude-read-only-review.yml"
    ).read_text(encoding="utf-8")
    assert "anthropics/claude-code-action@v1" in workflow
    assert "branches: [laboratory/orion-v0.1]" in workflow
    assert "startsWith(github.head_ref, 'codex/')" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: read" in workflow
    assert "pull-requests: write" not in workflow
    assert "issues: write" in workflow
    assert "ANTHROPIC_API_KEY" in workflow
    assert "ANTHROPIC_API_KEY is not configured" in workflow
    assert "<!-- ORION-CLAUDE-REVIEW -->" in workflow
    assert "BLOCKER" in workflow and "NON_BLOCKING" in workflow
    assert "Do not modify files" in workflow
    assert "Do not approve or merge" in workflow
    assert "customer or live ERP" in workflow
    assert "execution authority" in workflow
    assert "github.event.pull_request.body" not in workflow
    assert "github.event.comment.body" not in workflow
    assert "Edit" in workflow and "--disallowedTools" in workflow
    assert "Bash(gh pr diff:*)" in workflow
    assert "Bash(gh pr view:*)" in workflow
    assert "Bash(gh pr comment:*)" in workflow


def test_watch_queue_remains_lowest_first_and_skips_completed(tmp_path, monkeypatch):
    payload = [
        {"number": 12, "comments": []},
        {"number": 3, "comments": [{"body": lab.REPORT_MARKER}]},
        {"number": 8, "comments": []},
    ]
    runner = lab.Orchestrator(
        tmp_path,
        run=lambda command, **kwargs: completed(command, stdout=json.dumps(payload)),
    )
    processed = []
    monkeypatch.setattr(runner, "run_issue", processed.append)
    runner.watch(interval=60, once=True)
    assert processed == [8]


def test_lock_prevents_concurrent_processing(tmp_path):
    runner = lab.Orchestrator(tmp_path)
    with runner.lock(), pytest.raises(lab.SafeFail, match="already_active"), runner.lock():
        pass


def test_source_scan_rejects_network_and_dangerous_codex_mode(tmp_path):
    source = tmp_path / "tool.py"
    source.write_text("import socket\nmode = 'danger-full-access'\n")
    assert lab.Orchestrator.source_capability_scan(tmp_path, ("tool.py",)) is False


def test_source_scan_allows_exact_bodyless_discovery_get(tmp_path):
    assert scan_source(
        tmp_path,
        """
        from urllib.request import Request

        request = Request(
            "https://example.invalid/resource",
            headers={"Accept": "application/json"},
            method="GET",
        )
        """,
    )


@pytest.mark.parametrize(
    "arguments",
    [
        "*('https://example.invalid', b'body')",
        "*arguments",
        "'https://example.invalid', *arguments",
        "*('https://example.invalid',)",
    ],
)
def test_source_scan_rejects_starred_request_arguments(tmp_path, arguments):
    assert scan_source(
        tmp_path,
        "from urllib.request import Request\n"
        f"Request({arguments}, method='GET')\n",
    ) is False


@pytest.mark.parametrize(
    "annotation",
    [
        "value: consume(Request)",
        "def function(value: consume(Request)):\n    pass",
        "def function() -> consume(Request):\n    pass",
        "class Record:\n    value: consume(Request)",
        "value: Container[Request]",
    ],
)
def test_source_scan_rejects_constructor_escape_in_annotations(tmp_path, annotation):
    source = (
        "from urllib.request import Request\n"
        "def consume(factory):\n"
        "    return factory('https://example.invalid', method='POST')\n"
        + annotation + "\n"
    )
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "source",
    [
        (
            "from urllib.request import Request\nfrom replacement import *\n"
            "Request('url', method='GET')\n"
        ),
        (
            "from replacement import *\nfrom urllib.request import Request\n"
            "Request('url', method='GET')\n"
        ),
        (
            "from replacement import *\ndef read():\n"
            "    from urllib.request import Request\n"
            "    return Request('url', method='GET')\n"
        ),
        "from importlib import *\nimport_module('urllib.request')\n",
        "from builtins import *\ngetattr(__import__('urllib'), 'request')\n",
    ],
)
def test_source_scan_rejects_wildcard_request_binding(tmp_path, source):
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "source",
    [
        "import harmless\nharmless = harmless.member\n",
        "import harmless\nfirst = second.member\nsecond = first.member\nfirst = harmless\n",
    ],
)
def test_source_scan_rejects_extending_alias_cycles_within_bound(tmp_path, source):
    # Keep the regression bounded even when run against the broken verifier.
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    script = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        f"spec = importlib.util.spec_from_file_location('bounded_lab', {str(MODULE_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        f"assert module.Orchestrator.source_capability_scan(Path({str(tmp_path)!r}), ('probe.py',)) is False\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=5, check=False
    )
    assert result.returncode == 0, result.stderr


def test_source_scan_preserves_acyclic_aliases_and_quoted_annotations(tmp_path):
    assert scan_source(
        tmp_path,
        "from urllib.request import Request\n"
        "import json\nthird = second\nsecond = first\nfirst = json\n"
        "value: 'Request'\nRequest('url', method='GET')\n",
    ) is True


def test_source_scan_requires_every_request_call_to_be_safe(tmp_path):
    safe = """
        from urllib.request import Request

        first = Request("https://example.invalid/one", method="GET")
        second = Request("https://example.invalid/two", method="GET")
    """
    unsafe = safe.replace('method="GET")\n', 'method="POST")\n', 1)
    assert scan_source(tmp_path, safe) is True
    assert scan_source(tmp_path, unsafe) is False


def test_source_scan_rejects_request_outside_discovery(tmp_path):
    assert scan_source(
        tmp_path,
        'from urllib.request import Request\nrequest = Request("url", method="GET")\n',
        name="src/orion/service.py",
    ) is False


@pytest.mark.parametrize(
    "source",
    [
        "import urllib.request\n",
        "from urllib.request import urlopen\n",
        "from urllib.request import build_opener\n",
        "from urllib.request import Request, urlopen\n",
        "from urllib.request import Request as WebRequest\n",
        "from .urllib.request import Request\n",
    ],
)
def test_source_scan_rejects_unapproved_urllib_imports(tmp_path, source):
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "request_arguments",
    [
        '"url"',
        '"url", method=method',
        '"url", method="POST"',
        '"url", method="PUT"',
        '"url", method="PATCH"',
        '"url", method="DELETE"',
        '"url", method="get"',
    ],
)
def test_source_scan_rejects_unprovable_or_non_get_methods(tmp_path, request_arguments):
    source = f"from urllib.request import Request\nrequest = Request({request_arguments})\n"
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "request_arguments",
    [
        '"url", data=None, method="GET"',
        '"url", None, method="GET"',
        '"url", method="GET", **headers',
    ],
)
def test_source_scan_rejects_request_bodies_or_unprovable_keywords(
    tmp_path, request_arguments
):
    source = f"from urllib.request import Request\nrequest = Request({request_arguments})\n"
    assert scan_source(tmp_path, source) is False


def test_source_scan_rejects_invalid_production_python(tmp_path):
    assert scan_source(tmp_path, "def broken(:\n    pass\n", name="src/orion/broken.py") is False


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nmodule = importlib.import_module("urllib.request")\n',
        'from importlib import import_module as load\nmodule = load("urllib.request")\n',
        'module = __import__("urllib.request")\n',
        'import importlib\nname = "json"\nmodule = importlib.import_module(name)\n',
        'name = "json"\nmodule = __import__(name)\n',
    ],
)
def test_source_scan_rejects_network_or_dynamic_import_resolution(tmp_path, source):
    assert scan_source(tmp_path, source, name="tools/probe.py") is False


@pytest.mark.parametrize(
    "arguments",
    [
        '"urllib", fromlist=["request"]',
        '"http", None, None, ("client",)',
        'name="urllib", fromlist=("request",)',
        '"json", fromlist=["urllib.request"]',
        '"json", fromlist=names',
        '"json", fromlist=[name]',
        '"json", fromlist=[*names]',
        '"json", fromlist=["*"]',
        '"json", fromlist="decoder"',
        '"json", fromlist=None',
        '"json", fromlist=[1]',
        '"json", fromlist=[""]',
        '"json", fromlist=[".decoder"]',
        '"json", None, None, (), fromlist=["decoder"]',
        '"json", name="urllib", fromlist=["request"]',
        '"json", *extras',
        '"json", **options',
        '"json", fromlist=[], level=1',
        '"json", fromlist=[], level=level',
        '"json", None, None, [], 0, "extra"',
        '"json", unexpected=[]',
    ],
)
@pytest.mark.parametrize(
    ("prefix", "loader"),
    [
        ("", "__import__"),
        ("import builtins\n", "builtins.__import__"),
        ("from builtins import __import__ as load\n", "load"),
    ],
)
def test_source_scan_rejects_unsafe_import_fromlist(tmp_path, arguments, prefix, loader):
    assert scan_source(tmp_path, f"{prefix}module = {loader}({arguments})\n") is False


@pytest.mark.parametrize(
    "arguments",
    [
        '"json"',
        '"json", fromlist=["decoder"]',
        '"json", None, None, ("decoder",), 0',
        'name="json", fromlist=()',
        '"urllib", fromlist=["parse"]',
    ],
)
def test_source_scan_preserves_literal_safe_import_fromlist(tmp_path, arguments):
    assert scan_source(tmp_path, f"module = __import__({arguments})\n") is True


@pytest.mark.parametrize(
    "source",
    [
        'from urllib.request import Request\nfactory = globals()["Request"]\n',
        'from urllib.request import Request\nfactory = locals().get("Request")\n',
        'from urllib.request import Request\nfactory = vars()["Request"]\n',
        'import sys\nmodule = sys.modules["urllib.request"]\n',
        'import sys\nmodule = sys.modules.get("urllib.request")\n',
        (
            'import sys as system\nregistry = system.modules\n'
            'module = registry.get("urllib.request")\n'
        ),
        (
            'import sys\nlookup = sys.modules.get\n'
            'module = lookup("urllib.request")\n'
        ),
        'import sys\nlookup = sys.modules.get\nkey = "json"\nmodule = lookup(key)\n',
        'import sys\nfactory = sys.modules["urllib"].request.Request\n',
        'import builtins\nloader = builtins.__dict__["__import__"]\n',
        'import builtins\nloader = vars(builtins).get("__import__")\n',
        'import importlib\nloader = importlib.__dict__["import_module"]\n',
        'import importlib\nkey = "import_module"\nloader = importlib.__dict__.get(key)\n',
        'import builtins\nloader = getattr(builtins, "__import__")\n',
        'import importlib\nloader = getattr(importlib, "import_module")\n',
    ],
)
def test_source_scan_rejects_indirect_capability_recovery(tmp_path, source):
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "pattern",
    [
        "Request",
        "[*Request]",
        "{**Request}",
    ],
)
def test_source_scan_rejects_request_pattern_capture(tmp_path, pattern):
    source = f"""
        from urllib.request import Request

        request = Request("url", method="GET")
        match value:
            case {pattern}:
                pass
    """
    assert scan_source(tmp_path, source) is False


@pytest.mark.parametrize(
    "source",
    [
        'import http.client\nconnection = http.client.HTTPSConnection("example.invalid")\n',
        'from http.client import HTTPSConnection\nconnection = HTTPSConnection("example.invalid")\n',
        'from http import client\nconnection = client.HTTPSConnection("example.invalid")\n',
        'connection = http.client.HTTPSConnection("example.invalid")\n',
        'import http\nname = "client"\nclient = getattr(http, name)\n',
    ],
)
def test_source_scan_rejects_alternative_http_client_construction(tmp_path, source):
    assert scan_source(tmp_path, source, name="src/orion/service.py") is False


@pytest.mark.parametrize("module", ["requests", "httpx", "socket"])
def test_source_scan_preserves_existing_direct_network_rejections(tmp_path, module):
    assert scan_source(tmp_path, f"import {module}\n", name="src/orion/service.py") is False


def test_source_scan_allows_statically_unrelated_reflection(tmp_path):
    assert scan_source(
        tmp_path,
        """
        import importlib
        import sys

        json_module = importlib.import_module("json")
        loads = getattr(json_module, "loads")
        cached = sys.modules.get("json")
        labels = {"Request": "display only"}
        label = labels["Request"]
        field = getattr(object(), "field", None)
        """,
        name="tools/probe.py",
    ) is True


@pytest.mark.parametrize(
    "source",
    [
        'subprocess.run(["git", "merge", "feature"])\n',
        'subprocess.run(["git", "reset", "--hard"])\n',
        'subprocess.run(["git", "stash", "pop"])\n',
        'subprocess.run(["git", "stash", "drop"])\n',
        'mode = "danger-full-access"\n',
        'mode = "dangerously-bypass-approvals-and-sandbox"\n',
    ],
)
def test_source_scan_preserves_destructive_command_rejections(tmp_path, source):
    assert scan_source(tmp_path, source, name="tools/probe.py") is False


def test_source_scan_allows_normal_non_network_production_change(tmp_path):
    assert scan_source(
        tmp_path,
        'import json\npayload = json.dumps({"status": "observed"})\n',
        name="src/orion/reporting.py",
    ) is True


def test_source_scan_is_bootstrap_compatible_and_scans_itself():
    source = MODULE_PATH.read_text(encoding="utf-8")
    old_gate_sentinels = ("requests", "httpx", "socket", "urllib.request")
    assert all(sentinel not in source for sentinel in old_gate_sentinels)
    assert lab.Orchestrator.source_capability_scan(
        MODULE_PATH.parents[1], ("tools/orion_lab.py",)
    ) is True


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
