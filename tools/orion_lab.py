#!/usr/bin/env python3
"""Fail-closed local development orchestrator for the ORION laboratory."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

CANONICAL_BRANCH = "laboratory/orion-v0.1"
QUEUE_LABEL = "orion-codex-ready"
REPORT_MARKER = "<!-- ORION-AUTOMATION-COMPLETE -->"
META_HEADER = "ORION-AUTOMATION:"
META_KEYS = (
    "mode",
    "base_sha",
    "branch",
    "allow_live_customer_access",
    "allow_merge",
)
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
BRANCH_RE = re.compile(r"codex/[A-Za-z0-9._/-]+\Z")
TEST_COUNT_RE = re.compile(r"(\d+) passed")


class SafeFail(RuntimeError):
    """An expected, safe refusal to automate."""


@dataclass(frozen=True)
class AutomationContract:
    mode: str
    base_sha: str
    branch: str
    allow_live_customer_access: bool
    allow_merge: bool


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ChangeSnapshot:
    files: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class ReviewReport:
    issue: int
    full_sha: str
    tests: int | None
    ruff: str
    py_compile: str
    demo: str
    diff_check: str
    source_capability_scan: str
    changed_files: tuple[str, ...]
    branch: str
    base_sha: str
    canonical_branch: str = CANONICAL_BRANCH
    merge_performed: bool = False
    live_customer_access: bool = False


Run = Callable[..., subprocess.CompletedProcess[str]]


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.STDOUT if capture else subprocess.DEVNULL,
        check=False,
    )


def checked(run: Run, command: Sequence[str], *, cwd: Path) -> str:
    result = run(command, cwd=cwd)
    if result.returncode:
        raise SafeFail(f"command_failed:{command[0]}")
    return (result.stdout or "").strip()


def parse_metadata(text: str) -> AutomationContract:
    """Parse one strict metadata block, rejecting omissions and unsafe grants."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if line.strip() == META_HEADER]
    if len(starts) != 1:
        raise SafeFail("metadata_missing_or_ambiguous")
    values: dict[str, str] = {}
    for line in lines[starts[0] + 1 :]:
        stripped = line.strip()
        if not stripped:
            if values:
                break
            continue
        if stripped.startswith("```"):
            if values:
                break
            continue
        if ":" not in stripped:
            break
        key, value = (part.strip() for part in stripped.split(":", 1))
        if key not in META_KEYS or key in values:
            raise SafeFail("metadata_malformed")
        values[key] = value
    if tuple(values) != META_KEYS:
        raise SafeFail("metadata_malformed")
    if values["mode"] not in {"local-dev", "bootstrap-local-dev"}:
        raise SafeFail("mode_not_local_dev")
    if not SHA_RE.fullmatch(values["base_sha"]):
        raise SafeFail("base_sha_malformed")
    if not BRANCH_RE.fullmatch(values["branch"]) or ".." in values["branch"]:
        raise SafeFail("branch_malformed")
    if values["allow_live_customer_access"] != "false":
        raise SafeFail("live_customer_access_rejected")
    if values["allow_merge"] != "false":
        raise SafeFail("merge_permission_rejected")
    return AutomationContract(
        mode=values["mode"],
        base_sha=values["base_sha"],
        branch=values["branch"],
        allow_live_customer_access=False,
        allow_merge=False,
    )


def issue_contract(issue: dict[str, object]) -> AutomationContract:
    """Use the newest metadata block; comments deliberately supersede the body."""
    candidates = [str(issue.get("body", ""))]
    candidates.extend(str(comment.get("body", "")) for comment in issue.get("comments", []))
    parsed: list[AutomationContract] = []
    for candidate in candidates:
        if META_HEADER in candidate:
            parsed.append(parse_metadata(candidate))
    if not parsed:
        raise SafeFail("metadata_missing")
    return parsed[-1]


def build_prompt(issue: dict[str, object], contract: AutomationContract) -> str:
    return f"""ORION GOVERNANCE (mandatory):
- Work only in the current isolated worktree on {contract.branch}.
- Implement only GitHub issue #{issue['number']} scope; keep the delta minimal.
- Do not access customer state, ERP systems, credentials, or customer/live networks.
- Do not merge, edit {CANONICAL_BRANCH}, or pop/drop/reset any stash.
- Capability is not permission. Observation is not prediction. Learning is not execution.
- Run relevant tests while developing; the WSL orchestrator verifies independently.
- Stop after editing. Do not commit or push; the orchestrator owns both.

ISSUE TITLE:
{issue['title']}

ISSUE BODY:
{issue['body']}
"""


class Orchestrator:
    def __init__(self, repo: Path, *, run: Run = run_command) -> None:
        self.repo = repo.resolve()
        self.run = run
        self.state_dir = self.repo / ".orion" / "automation"

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        return checked(self.run, ("git", *args), cwd=cwd or self.repo)

    def _ensure_stashes(self) -> None:
        current = self._git("stash", "list", "--format=%H").splitlines()
        snapshot = self.state_dir / "stash-baseline.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if snapshot.exists():
            expected = json.loads(snapshot.read_text(encoding="utf-8"))
            if current != expected:
                raise SafeFail("preserved_stash_changed")
        else:
            snapshot.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")

    def doctor(self) -> None:
        if self._git("rev-parse", "--show-toplevel") != str(self.repo):
            raise SafeFail("wrong_repository")
        if not (self.repo / "AGENTS.md").is_file() or not (self.repo / "src/orion").is_dir():
            raise SafeFail("wrong_repository")
        for executable in ("git", "gh", "codex"):
            if shutil.which(executable) is None:
                raise SafeFail(f"{executable}_missing")
        python_version = tuple(map(int, platform.python_version_tuple()))
        if python_version < (3, 12, 0):
            raise SafeFail("python_too_old")
        checked(self.run, ("gh", "auth", "status"), cwd=self.repo)
        checked(self.run, ("codex", "--version"), cwd=self.repo)
        self._git("show-ref", "--verify", f"refs/heads/{CANONICAL_BRANCH}")
        canonical_path = self._canonical_worktree()
        if self._git("status", "--porcelain", cwd=canonical_path):
            raise SafeFail("canonical_dirty")
        self._ensure_stashes()

    def _canonical_worktree(self) -> Path:
        output = self._git("worktree", "list", "--porcelain")
        path: Path | None = None
        for line in output.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree "))
            elif line == f"branch refs/heads/{CANONICAL_BRANCH}" and path is not None:
                return path
        raise SafeFail("canonical_worktree_missing")

    def fetch_issue(self, number: int) -> dict[str, object]:
        raw = checked(
            self.run,
            (
                "gh", "issue", "view", str(number), "--json",
                "number,title,body,comments,state,url",
            ),
            cwd=self.repo,
        )
        return json.loads(raw)

    def verify_base(self, contract: AutomationContract) -> None:
        checked(self.run, ("git", "fetch", "origin", CANONICAL_BRANCH), cwd=self.repo)
        local = self._git("rev-parse", CANONICAL_BRANCH)
        remote = self._git("rev-parse", f"origin/{CANONICAL_BRANCH}")
        if local != contract.base_sha or remote != contract.base_sha:
            raise SafeFail("wrong_base_sha")

    def prepare_worktree(self, issue_number: int, contract: AutomationContract) -> Path:
        if contract.branch == CANONICAL_BRANCH:
            raise SafeFail("canonical_branch_forbidden")
        target = self.state_dir / "worktrees" / f"issue-{issue_number}"
        branch_ref = self.run(
            ("git", "show-ref", "--verify", f"refs/heads/{contract.branch}"), cwd=self.repo
        )
        if target.exists():
            actual = self._git("branch", "--show-current", cwd=target)
            if actual != contract.branch:
                raise SafeFail("worktree_branch_mismatch")
        elif branch_ref.returncode == 0:
            checked(self.run, ("git", "worktree", "add", str(target), contract.branch), cwd=self.repo)
        else:
            remote_ref = self.run(
                ("git", "show-ref", "--verify", f"refs/remotes/origin/{contract.branch}"),
                cwd=self.repo,
            )
            command = ["git", "worktree", "add", str(target), "-b", contract.branch]
            if remote_ref.returncode == 0:
                remote_sha = self._git("rev-parse", f"origin/{contract.branch}")
                if remote_sha != contract.base_sha:
                    raise SafeFail("feature_branch_not_at_base")
                command.extend(("--track", f"origin/{contract.branch}"))
            else:
                command.append(contract.base_sha)
            checked(self.run, command, cwd=self.repo)
        if self._git("merge-base", contract.base_sha, "HEAD", cwd=target) != contract.base_sha:
            raise SafeFail("feature_branch_wrong_base")
        if self._git("rev-parse", "HEAD", cwd=target) != contract.base_sha:
            raise SafeFail("feature_branch_not_at_base")
        return target

    def invoke_codex(self, issue: dict[str, object], contract: AutomationContract, worktree: Path) -> None:
        result = self.run(
            ("codex", "exec", "--sandbox", "workspace-write", "--cd", str(worktree), "-"),
            cwd=worktree,
            input_text=build_prompt(issue, contract),
            capture=False,
        )
        if result.returncode:
            raise SafeFail("codex_failed")

    def _changed_files(self, worktree: Path, base_sha: str) -> tuple[str, ...]:
        output = self._git("diff", "--name-only", base_sha, cwd=worktree)
        untracked = self._git("ls-files", "--others", "--exclude-standard", cwd=worktree)
        return tuple(sorted(set(filter(None, (*output.splitlines(), *untracked.splitlines())))))

    def _change_snapshot(self, worktree: Path, base_sha: str) -> ChangeSnapshot:
        files = self._changed_files(worktree, base_sha)
        digest = hashlib.sha256()
        for name in files:
            path = worktree / name
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            if path.is_symlink():
                digest.update(b"symlink\0")
                digest.update(path.readlink().as_posix().encode("utf-8"))
            elif path.is_file():
                digest.update(f"file:{path.stat().st_mode:o}\0".encode())
                digest.update(path.read_bytes())
            else:
                digest.update(b"deleted\0")
            digest.update(b"\0")
        return ChangeSnapshot(files=files, digest=digest.hexdigest())

    def verify(self, worktree: Path, base_sha: str) -> tuple[list[CheckResult], ChangeSnapshot, int | None]:
        snapshot = self._change_snapshot(worktree, base_sha)
        if not snapshot.files:
            raise SafeFail("no_changes")
        python_files = [name for name in snapshot.files if name.endswith(".py")]
        commands: list[tuple[str, list[str]]] = []
        commands.append(("py_compile", [sys.executable, "-m", "py_compile", *python_files]))
        commands.extend(
            (
                ("ruff", ["ruff", "check", "."]),
                ("pytest", [sys.executable, "-m", "pytest", "-q"]),
                ("demo", [sys.executable, "-m", "orion.demo"]),
                ("diff_check", ["git", "diff", "--check"]),
            )
        )
        results: list[CheckResult] = []
        tests: int | None = None
        for name, command in commands:
            result = self.run(command, cwd=worktree)
            output = result.stdout or ""
            passed = result.returncode == 0
            if name == "demo" and passed:
                try:
                    passed = json.loads(output)["execution_allowed"] is False
                except (json.JSONDecodeError, KeyError, TypeError):
                    passed = False
            if name == "pytest":
                match = TEST_COUNT_RE.search(output)
                tests = int(match.group(1)) if match else None
            results.append(CheckResult(name, passed, "passed" if passed else "failed"))
            if not passed:
                return results, snapshot, tests
        scan = self.source_capability_scan(worktree, snapshot.files)
        results.append(CheckResult("source_capability_scan", scan, "passed" if scan else "failed"))
        return results, snapshot, tests

    def final_integrity_gate(
        self,
        contract: AutomationContract,
        worktree: Path,
        verified: ChangeSnapshot,
    ) -> None:
        """Revalidate every mutable boundary immediately before commit and push."""
        self._ensure_stashes()
        if self._git("status", "--porcelain", cwd=self._canonical_worktree()):
            raise SafeFail("canonical_changed_after_verification")
        self.verify_base(contract)
        if self._git("rev-parse", "HEAD", cwd=worktree) != contract.base_sha:
            raise SafeFail("feature_head_changed_after_verification")
        if self._change_snapshot(worktree, contract.base_sha) != verified:
            raise SafeFail("verified_changes_changed")

    @staticmethod
    def source_capability_scan(worktree: Path, changed: tuple[str, ...]) -> bool:
        forbidden = (
            re.compile(
                r"\b(?:"
                + "|".join(("requ" + "ests", "htt" + "px", "sock" + "et"))
                + r")\b"
            ),
            re.compile("urllib" + r"\." + "request"),
            re.compile(r"['\"]git['\"].{0,80}['\"](?:merge|reset)['\"]", re.DOTALL),
            re.compile(r"['\"]stash['\"]\s*,\s*['\"](?:pop|drop)['\"]"),
            re.compile(
                "|".join(
                    ("dangerously-bypass-" + "approvals-and-sandbox", "danger-" + "full-access")
                )
            ),
        )
        for name in changed:
            if not name.endswith(".py") or name.startswith("tests/"):
                continue
            text = (worktree / name).read_text(encoding="utf-8")
            if any(pattern.search(text) for pattern in forbidden):
                return False
        return True

    def _write_failure(self, issue: int, category: str) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"issue": issue, "status": "failed", "category": category}
        (self.state_dir / f"issue-{issue}-report.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def commit_push_report(
        self,
        issue: int,
        contract: AutomationContract,
        worktree: Path,
        checks: list[CheckResult],
        changed: tuple[str, ...],
        tests: int | None,
    ) -> ReviewReport:
        checked(self.run, ("git", "add", "--", *changed), cwd=worktree)
        checked(self.run, ("git", "commit", "-m", f"feat: implement ORION issue #{issue}"), cwd=worktree)
        sha = self._git("rev-parse", "HEAD", cwd=worktree)
        checked(self.run, ("git", "push", "origin", f"HEAD:{contract.branch}"), cwd=worktree)
        statuses = {result.name: result.detail for result in checks}
        report = ReviewReport(
            issue=issue,
            full_sha=sha,
            tests=tests,
            ruff=statuses["ruff"],
            py_compile=statuses["py_compile"],
            demo=statuses["demo"],
            diff_check=statuses["diff_check"],
            source_capability_scan=statuses["source_capability_scan"],
            changed_files=changed,
            branch=contract.branch,
            base_sha=contract.base_sha,
        )
        body = render_report(report)
        checked(self.run, ("gh", "issue", "comment", str(issue), "--body", body), cwd=self.repo)
        (self.state_dir / f"issue-{issue}-report.json").write_text(
            json.dumps(asdict(report), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report

    def run_issue(self, number: int) -> ReviewReport:
        with self.lock():
            try:
                self.doctor()
                issue = self.fetch_issue(number)
                if issue.get("state") != "OPEN":
                    raise SafeFail("issue_not_open")
                contract = issue_contract(issue)
                self.verify_base(contract)
                worktree = self.prepare_worktree(number, contract)
                self.invoke_codex(issue, contract, worktree)
                if self._git("status", "--porcelain", cwd=self._canonical_worktree()):
                    raise SafeFail("canonical_changed_by_codex")
                if self._git("rev-parse", "HEAD", cwd=worktree) != contract.base_sha:
                    raise SafeFail("codex_created_commit")
                checks, snapshot, tests = self.verify(worktree, contract.base_sha)
                if not all(check.passed for check in checks):
                    raise SafeFail(next(check.name for check in checks if not check.passed) + "_failed")
                self.final_integrity_gate(contract, worktree, snapshot)
                return self.commit_push_report(
                    number, contract, worktree, checks, snapshot.files, tests
                )
            except SafeFail as error:
                self._write_failure(number, str(error))
                raise

    @contextlib.contextmanager
    def lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_dir / "runner.lock"
        with lock_path.open("w", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise SafeFail("runner_already_active") from error
            yield

    def watch(self, interval: int, once: bool = False) -> None:
        while True:
            raw = checked(
                self.run,
                (
                    "gh", "issue", "list", "--label", QUEUE_LABEL, "--state", "open",
                    "--json", "number,comments", "--limit", "100",
                ),
                cwd=self.repo,
            )
            issues = sorted(json.loads(raw), key=lambda item: item["number"])
            pending = [
                item for item in issues
                if REPORT_MARKER not in "\n".join(
                    str(comment.get("body", "")) for comment in item.get("comments", [])
                )
            ]
            if pending:
                self.run_issue(int(pending[0]["number"]))
            if once:
                return
            time.sleep(interval)


def render_report(report: ReviewReport) -> str:
    """Render only allowlisted, non-secret verification fields."""
    files = "\n".join(f"- `{name}`" for name in report.changed_files)
    tests = str(report.tests) if report.tests is not None else "unparsed"
    return f"""{REPORT_MARKER}
ORION automation review packet

- full_sha: `{report.full_sha}`
- tests: `{tests}`
- ruff: `{report.ruff}`
- py_compile: `{report.py_compile}`
- demo: `{report.demo}` (`execution_allowed=false`)
- diff_check: `{report.diff_check}`
- source_capability_scan: `{report.source_capability_scan}`
- branch: `{report.branch}`
- base_sha: `{report.base_sha}`
- canonical_branch: `{report.canonical_branch}`
- merge_performed=false
- live_customer_access=false

Changed files:
{files}
"""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repo", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    run_parser = commands.add_parser("run")
    run_parser.add_argument("issue", type=int)
    watch = commands.add_parser("watch")
    watch.add_argument("--interval", type=int, default=60)
    watch.add_argument("--once", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    orchestrator = Orchestrator(args.repo)
    try:
        if args.command == "doctor":
            orchestrator.doctor()
            print("OK: ORION laboratory automation prerequisites satisfied")
        elif args.command == "run":
            report = orchestrator.run_issue(args.issue)
            print(f"OK: issue #{args.issue} pushed at {report.full_sha}; merge_performed=false")
        else:
            if args.interval < 10:
                raise SafeFail("watch_interval_too_short")
            orchestrator.watch(args.interval, args.once)
    except SafeFail as error:
        print(f"SAFE_FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
