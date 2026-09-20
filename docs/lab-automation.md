# ORION local laboratory automation

`tools/orion_lab.py` turns a strictly marked GitHub issue into one isolated,
verified feature-branch change. GitHub remains the task queue, Codex edits only
the isolated worktree, and the local runner owns verification, commit, push, and
the sanitized review packet. It never merges or authorizes live/customer access.

After a successful verification, commit, and feature-branch push, the runner
creates exactly one draft pull request or refreshes the existing open draft for
that head. Its base is always `laboratory/orion-v0.1`; conflicting or duplicate
open pull requests fail closed. The sanitized issue report includes the pull
request number and URL so the review handoff needs no prompt copying.

## Prerequisites and commands

Use Python 3.12 or newer in the clean canonical `laboratory/orion-v0.1`
worktree. `git`, `gh`, and `codex` must be on `PATH`; both CLIs must already be
authenticated. No customer environment variables are needed.

```bash
python tools/orion_lab.py doctor
python tools/orion_lab.py run 123
python tools/orion_lab.py watch --interval 60
python tools/orion_lab.py watch --once
```

`doctor` validates the repository, tools, authentication, canonical worktree,
Python, and the preserved-stash baseline. On its first successful inspection it
records the current stash object IDs under the gitignored `.orion/automation/`;
later runs fail if any recorded stash disappears.

`run` fetches the issue, verifies the exact local and remote canonical base,
creates/reuses `.orion/automation/worktrees/issue-N`, invokes `codex exec` with
the workspace-write sandbox, and independently runs py_compile, Ruff, full
pytest, the demo, diff checking, and a source/capability scan. A failure is
written locally and is neither committed nor pushed. Success commits and pushes
only the declared feature branch and posts an allowlisted report without the
Codex transcript.

For changed non-test Python, direct network construction fails closed except for
a direct, unaliased `Request(...)` imported alone from `urllib.request` under
`src/orion/discovery/`. Every such request must be statically provable as a
bodyless literal `method="GET"` call. Alternative network modules and dynamic or
indirect import, mapping, and attribute resolution fail closed. Passing this
scan grants code-review eligibility only; human merge approval and separate
live/customer authorization remain required.

Constructor uses in evaluated annotations and wildcard imports that leave the
`Request` binding unprovable are rejected. Alias propagation must reach a stable
result within its finite assignment-derived bound; non-convergence fails closed.

Immediately before commit, a final integrity gate rechecks the exact preserved
stash baseline, canonical cleanliness and local/remote base SHA, feature HEAD,
and a content-and-mode fingerprint of the complete verified changed/untracked
file set. Any drift produces only the existing sanitized local failure report.

`watch` polls open issues labeled `orion-codex-ready`, processes at most the
lowest-numbered pending issue under the same exclusive local lock, and skips
issues carrying the completion marker. `--once` performs one poll. A minimum
ten-second interval is enforced.

## One-time Claude reviewer setup

Install the official Claude GitHub integration for this repository and add the
Anthropic credential as the GitHub Actions secret `ANTHROPIC_API_KEY`. Keep the
local watcher running with `python tools/orion_lab.py watch --interval 60`.
Never paste the credential into an issue, pull request, command argument, or
chat.

`.github/workflows/claude-read-only-review.yml` then reviews pull requests only
when they target `laboratory/orion-v0.1`, originate from a same-repository
`codex/` branch, and carry no code-write token permission. The model job has read-only contents, pull-request and issue permissions. It
reads the linked originating issue, exact-head diff and repository files and
returns bounded structured findings. It cannot post comments, approve, or merge.
A separate fresh-runner publisher has issue-comment write permission, no checkout,
no model and no Anthropic credential. It validates the output, current head/base,
same-repository branch and explicit same-repository closing issue reference, then
creates or updates one bot-owned marker comment on the event's PR only. Duplicate
bot markers, stale heads, malformed output and unlinked issues fail before writing.
The publisher does not run PR code or evaluate model text. An existing comment
from another author is never overwritten. Unsupported issue-link forms fail closed;
use an explicit `Closes #N` reference in the PR body.

These are token/job and deterministic publication boundaries, not proof of
malicious-process or network isolation. Repository maintainers, GitHub runners,
the official action and its handling of the Anthropic credential remain trusted.
The model's findings do not prove it actually inspected the claimed evidence.
Review remains advisory and cannot approve integration. A missing Anthropic secret fails with a clear
configuration error.

## Automation-ready issue format

ChatGPT must include exactly this ordered block in the issue body. A later
owner comment may provide a replacement block when a base is deliberately
refreshed.

```text
ORION-AUTOMATION:
mode: local-dev
base_sha: <exact 40-character lowercase SHA>
branch: codex/<feature-name>
allow_live_customer_access: false
allow_merge: false
```

Missing, duplicated, reordered, malformed, or permissive metadata fails closed.
The bootstrap-only `bootstrap-local-dev` mode is accepted for creating the
runner itself; normal tasks use `local-dev`.

## Human gates and recovery

Humans still authorize the issue, review the pushed commit, approve merge, and
perform any canonical promotion. Live/customer access always requires a
separate authorization outside this v0.1 runner. The runner never merges.

After a safe failure, inspect `.orion/automation/issue-N-report.json` and the
isolated worktree. Correct the environment or ask ChatGPT to amend the issue
contract, then rerun the same command. Do not reset, pop, or drop preserved
stashes. Manual cleanup of an abandoned worktree is an explicit operator action,
not an automated recovery step.
