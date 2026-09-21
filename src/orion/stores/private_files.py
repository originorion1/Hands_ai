"""Bounded reads for owner-only local artifacts.

The caller owns the artifact schema.  This module owns only the filesystem
boundary: a single regular file, owned by this process, with no hard links and
mode ``0600``.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class PrivateFileError(ValueError):
    """A local artifact does not satisfy the private-file contract."""


def read_private_file(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    """Read one bounded owner-only file without following its final symlink."""

    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise ValueError("maximum_bytes must be a positive integer")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise PrivateFileError(f"{label} is not private")
            body = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise PrivateFileError(f"{label} is unavailable") from exc
    if len(body) > maximum_bytes:
        raise PrivateFileError(f"{label} exceeds its size bound")
    return body
