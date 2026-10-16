"""Match installed connector metadata to an explicit operator-reviewed record.

Discovery reads package metadata only. It never imports connector code or
opens a network connection. A separate authorization path must govern use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from importlib import metadata

CONTRACT_VERSION = "orion.connector.v1"
ENTRY_POINT_GROUP = "orion.connectors.v1"
_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_DISTRIBUTION = re.compile(r"[a-z][a-z0-9-]{0,127}\Z")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+){1,2}\Z")
_VALUE = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*(?:\.[a-zA-Z_][a-zA-Z_0-9]*)*:[a-zA-Z_][a-zA-Z_0-9]*\Z")


def _normalize_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True)
class ReviewedConnector:
    """Exact metadata reviewed independently of the installed package."""

    name: str
    distribution: str
    version: str
    entry_point: str
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if (
            self.contract_version != CONTRACT_VERSION
            or not _NAME.fullmatch(self.name)
            or not _DISTRIBUTION.fullmatch(self.distribution)
            or not _VERSION.fullmatch(self.version)
            or not _VALUE.fullmatch(self.entry_point)
        ):
            raise ValueError("invalid reviewed connector contract")


@dataclass(frozen=True)
class ConnectorRegistration:
    """A metadata match, with no execution, credentials, or tenant grant."""

    name: str
    distribution: str
    version: str
    entry_point: str
    contract_version: str
    execution_allowed: bool = field(default=False, init=False)


def discover(reviewed: ReviewedConnector) -> ConnectorRegistration:
    """Fail closed unless exactly one installed entry point matches review."""
    if not isinstance(reviewed, ReviewedConnector):
        raise TypeError("reviewed connector required")
    matches = []
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
        distribution = entry.dist
        if entry.name != reviewed.name or distribution is None:
            continue
        if (
            _normalize_distribution(distribution.metadata["Name"]) == reviewed.distribution
            and distribution.version == reviewed.version
            and entry.value == reviewed.entry_point
        ):
            matches.append(entry)
    if len(matches) != 1:
        raise ValueError("reviewed connector unavailable")
    return ConnectorRegistration(
        reviewed.name, reviewed.distribution, reviewed.version,
        reviewed.entry_point, reviewed.contract_version,
    )
