"""Generic read-only HTTP observation adapter.

HTTP is treated only as an observation transport. The Kernel never depends on
vendor API semantics. A source-specific adapter can later expose richer
observation methods while producing the same normalized contracts.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .snapshot import DiscoveredObject, DiscoverySnapshot


class DiscoveryTransportError(RuntimeError):
    """Raised when a read-only discovery request cannot be completed safely."""


Fetcher = Callable[[str], bytes]


def _default_fetcher(url: str) -> bytes:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - URL is operator-configured
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise DiscoveryTransportError(f"read-only discovery failed: {exc}") from exc


class ReadOnlyHttpDiscoveryAdapter:
    """Observe configured JSON resources without exposing write operations."""

    def __init__(
        self,
        *,
        base_url: str,
        paths: Iterable[str],
        fetcher: Fetcher = _default_fetcher,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._paths = tuple(paths)
        self._fetcher = fetcher
        if not self._base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use HTTP(S)")
        if any(not path.startswith("/") or "://" in path for path in self._paths):
            raise ValueError("discovery paths must be relative HTTP paths")

    def discover(self, *, tenant_id: str, observed_at: datetime) -> DiscoverySnapshot:
        objects: list[DiscoveredObject] = []
        for path in self._paths:
            payload = self._fetch_json(path)
            objects.extend(self._normalize(path, payload))
        return DiscoverySnapshot(
            tenant_id=tenant_id,
            source_system="http-read-only",
            observed_at=observed_at,
            objects=tuple(objects),
        )

    def _fetch_json(self, path: str) -> object:
        try:
            return json.loads(self._fetcher(f"{self._base_url}{path}"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise DiscoveryTransportError(f"non-JSON discovery response at {path}") from exc

    @staticmethod
    def _normalize(path: str, payload: object) -> list[DiscoveredObject]:
        records: list[Mapping[str, object]]
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            records = [item for item in payload["data"] if isinstance(item, dict)]
        elif isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            records = [payload]
        else:
            records = []

        result: list[DiscoveredObject] = []
        for index, record in enumerate(records):
            object_type = str(record.get("type") or path.strip("/").split("/")[-1] or "unknown")
            name = str(record.get("name") or record.get("id") or f"object-{index}")
            attributes = {str(key): str(value) for key, value in record.items()}
            result.append(
                DiscoveredObject(
                    object_id=__import__("uuid").uuid5(
                        __import__("uuid").NAMESPACE_URL,
                        f"{path}:{name}:{index}",
                    ),
                    object_type=object_type,
                    name=name,
                    attributes=attributes,
                )
            )
        return result
