#!/usr/bin/env python3
"""Verify the live-pilot path, blob, and top-level-symbol inventory.

``--revision`` is the authoritative mode and reads the exact declared Git tree.
The default working-tree mode is guarded for a checkout whose HEAD is exactly the
declared inventory revision; it must not reinterpret a descendant as that snapshot.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

EXPECTED_SCHEMA_VERSION = 2
SOURCE_ROOTS = ("src", "tools")


def selected_python_path(path: str) -> bool:
    """Return whether a tracked POSIX path belongs to the declared inventory."""
    candidate = PurePosixPath(path)
    return (
        len(candidate.parts) >= 2
        and candidate.parts[0] in SOURCE_ROOTS
        and candidate.suffix == ".py"
    )


def top_level_symbols(source: bytes, *, path: str) -> list[str]:
    """Return source-ordered top-level function and class definitions."""
    tree = ast.parse(source, filename=path)
    definitions = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return [node.name for node in tree.body if isinstance(node, definitions)]


def verify_inventory_document(
    inventory: Mapping[str, Any],
    sources: Mapping[str, bytes],
) -> list[str]:
    """Return deterministic violations for an inventory and selected source set."""
    violations: list[str] = []
    if inventory.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        violations.append(
            f"schema_version must be {EXPECTED_SCHEMA_VERSION}"
        )
    revision = inventory.get("inventory_revision")
    if not isinstance(revision, str) or len(revision) != 40:
        violations.append("inventory_revision must be a full 40-character commit id")

    raw_entries = inventory.get("files")
    if not isinstance(raw_entries, list):
        return [*violations, "files must be a list"]

    entries: dict[str, Mapping[str, Any]] = {}
    for index, raw_entry in enumerate(raw_entries):
        if not isinstance(raw_entry, Mapping):
            violations.append(f"files[{index}] must be an object")
            continue
        path = raw_entry.get("file")
        if not isinstance(path, str):
            violations.append(f"files[{index}].file must be a string")
            continue
        if path in entries:
            violations.append(f"duplicate inventory path: {path}")
            continue
        entries[path] = raw_entry

    source_paths = set(sources)
    inventory_paths = set(entries)
    for path in sorted(source_paths - inventory_paths):
        violations.append(f"omitted tracked source: {path}")
    for path in sorted(inventory_paths - source_paths):
        violations.append(f"inventory path is not a selected tracked source: {path}")

    for path in sorted(source_paths & inventory_paths):
        entry = entries[path]
        source = sources[path]
        actual_digest = hashlib.sha256(source).hexdigest()
        if entry.get("sha256") != actual_digest:
            violations.append(f"stale sha256: {path}")
        try:
            actual_symbols = top_level_symbols(source, path=path)
        except (SyntaxError, UnicodeDecodeError) as exc:
            violations.append(f"cannot parse {path}: {exc}")
            continue
        declared_symbols = entry.get("symbols")
        if declared_symbols != actual_symbols:
            violations.append(f"top-level symbol drift: {path}")
    return violations


def _git(repository: Path, arguments: Sequence[str], *, text: bool = False) -> bytes | str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments],
        stderr=subprocess.PIPE,
        text=text,
    )


def resolve_revision(repository: Path, revision: str) -> str:
    """Resolve a commit name without reading or changing the working tree."""
    resolved = _git(
        repository,
        ["rev-parse", "--verify", f"{revision}^{{commit}}"],
        text=True,
    )
    assert isinstance(resolved, str)
    return resolved.strip()


def working_tree_sources(repository: Path) -> dict[str, bytes]:
    """Read selected tracked files from an already-qualified exact checkout."""
    output = _git(repository, ["ls-files", "-z", "--", *SOURCE_ROOTS])
    assert isinstance(output, bytes)
    paths = sorted(
        path.decode("utf-8")
        for path in output.rstrip(b"\0").split(b"\0")
        if path and selected_python_path(path.decode("utf-8"))
    )
    return {path: (repository / path).read_bytes() for path in paths}


def revision_sources(repository: Path, revision: str) -> tuple[str, dict[str, bytes]]:
    """Read selected files from an exact commit, independent of the worktree."""
    resolved = resolve_revision(repository, revision)
    archive = _git(
        repository,
        ["archive", "--format=tar", resolved, *SOURCE_ROOTS],
    )
    assert isinstance(archive, bytes)
    sources: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tree:
        for member in tree.getmembers():
            if not member.isfile() or not selected_python_path(member.name):
                continue
            extracted = tree.extractfile(member)
            if extracted is None:
                raise ValueError(f"cannot read archived source: {member.name}")
            sources[member.name] = extracted.read()
    return resolved, sources


def load_inventory(path: Path) -> Mapping[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise TypeError("inventory root must be an object")
    return document


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="repository worktree (default: current directory)",
    )
    result.add_argument(
        "--inventory",
        type=PurePosixPath,
        default=PurePosixPath("docs/00-architecture/LIVE_PILOT_SOURCE_INVENTORY.json"),
        help="inventory path relative to the repository",
    )
    result.add_argument(
        "--revision",
        help="read the exact Git tree; must equal inventory_revision",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    repository = arguments.repository.resolve()
    inventory = load_inventory(repository / Path(arguments.inventory))
    declared_revision = inventory.get("inventory_revision")
    mode = "working-tree"
    if arguments.revision is None:
        head = resolve_revision(repository, "HEAD")
        if head != declared_revision:
            print(
                "inventory verification refused:\n"
                f"- HEAD {head} is not inventory_revision {declared_revision}\n"
                "- descendant trees are not historical inventory failures; fetch the "
                "declared object and run:\n"
                f"  python3 scripts/verify_live_pilot_source_inventory.py "
                f"--repository . --revision {declared_revision}",
                file=sys.stderr,
            )
            return 1
        sources = working_tree_sources(repository)
    else:
        resolved, sources = revision_sources(repository, arguments.revision)
        if resolved != declared_revision:
            print(
                "inventory verification failed:\n"
                f"- requested revision {resolved} does not equal inventory_revision "
                f"{declared_revision}",
                file=sys.stderr,
            )
            return 1
        mode = f"revision:{resolved}"

    violations = verify_inventory_document(inventory, sources)
    if violations:
        print("inventory verification failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print(f"inventory verified: {len(sources)} tracked Python paths ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
