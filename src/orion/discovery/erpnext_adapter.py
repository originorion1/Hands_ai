"""ERPNext/Frappe read-only adapter for the first ORION prototype.

ERPNext is a starting customer environment, not an ORION architectural
 dependency. This adapter translates observations into ORION's neutral
contracts; no ERPNext semantics leak into the Kernel.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode


class ERPNextDiscoveryAdapter:
    """Read-only Frappe REST discovery adapter.

    Authentication is supplied by the caller. The adapter only issues GET
    requests to explicitly configured resources and never exposes write verbs.
    """

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        api_key: str,
        api_secret: str,
        resources: tuple[str, ...] = ("DocType",),
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._resources = resources
        self._opener = opener

    def discover(self) -> tuple[Observation, ...]:
        observations: list[Observation] = []
        for resource in self._resources:
            observations.extend(self._discover_resource(resource))
        return tuple(observations)

    def _discover_resource(self, resource: str) -> tuple[Observation, ...]:
        if not resource or "/" in resource or "?" in resource or "#" in resource:
            raise ValueError("resource must be a simple configured read-only resource name")

        encoded_resource = quote(resource, safe="")
        request = Request(
            f"{self._base_url}/api/resource/{encoded_resource}",
            headers={
                "Authorization": f"token {self._api_key}:{self._api_secret}",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=20) as response:
                body = response.read()
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"read-only discovery failed for {resource}") from exc

        payload: Mapping[str, Any] = json.loads(body.decode("utf-8"))
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise TypeError("discovery response data must be a list")

        observed_at = datetime.now().astimezone()
        return tuple(
            Observation(
                evidence=Evidence(
                    kind=EvidenceKind.API,
                    source="erpnext-read-only",
                    tenant_id=self._tenant_id,
                    observed_at=observed_at,
                    payload={"resource": resource, "record": row},
                ),
                mode=ObservationMode.READ_ONLY,
            )
            for row in rows
            if isinstance(row, dict)
        )
