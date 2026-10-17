"""Offline synthetic source that implements the existing Kernel discovery port."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.pilot_read import PilotReadPermit


class SyntheticConnector:
    """Produce one tenant-bound observation without credentials or network I/O."""

    def __init__(self, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id

    def discover(self) -> tuple[Observation, ...]:
        return (Observation(Evidence(
            kind=EvidenceKind.SYSTEM_OBSERVATION,
            source="synthetic-connector-v1",
            payload={"synthetic": True},
            tenant_id=self._tenant_id,
        )),)


class SyntheticRecordConnector:
    """Immutable local source for the existing one-use governed pilot read port.

    Registration of this class is metadata only. ``launch_pilot_read`` must
    supply its permit before any record is exposed; this adapter has no write
    operation or network transport.
    """

    def __init__(self, *, tenant_id: str, source_id: str, dataset_id: str,
                 records: tuple[Mapping[str, object], ...],
                 observed_at: datetime | None = None) -> None:
        if not all(isinstance(value, str) and re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) for value in
                (tenant_id, source_id, dataset_id)):
            raise ValueError("exact synthetic source identity required")
        if type(records) is not tuple or not records or len(records) > 100:
            raise ValueError("bounded synthetic records required")
        if any(not isinstance(record, Mapping) or any(type(key) is not str for key in record)
               for record in records):
            raise ValueError("scalar synthetic records required")
        try:
            body = json.dumps([dict(record) for record in records], sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode()
            decoded = json.loads(body)
        except (TypeError, ValueError) as exc:
            raise ValueError("scalar synthetic records required") from exc
        if any(type(record) is not dict or any(type(value) not in
               (str, int, float, bool, type(None)) for value in record.values())
               for record in decoded):
            raise ValueError("scalar synthetic records required")
        self.tenant_id = tenant_id
        self.source_id = source_id
        self.dataset_id = dataset_id
        self.source_sha256 = hashlib.sha256(body).hexdigest()
        self._body = body
        if observed_at is not None and (not isinstance(observed_at, datetime)
                                        or observed_at.tzinfo is None
                                        or observed_at.utcoffset() is None):
            raise ValueError("timezone-aware observation required")
        self._observed_at = observed_at

    def read(self, permit: PilotReadPermit) -> tuple[Observation, ...]:
        if not isinstance(permit, PilotReadPermit):
            raise TypeError("governed pilot permit required")
        request, _ = permit.claim_io(self.source_id)
        if request.tenant_id != self.tenant_id:
            raise ValueError("synthetic source tenant mismatch")
        records = json.loads(self._body)
        if len(records) > request.max_records:
            raise ValueError("synthetic source exceeds read bound")
        observed_at = self._observed_at or permit.current_time()
        return tuple(Observation(Evidence(
            kind=EvidenceKind.SOURCE,
            source="synthetic-record-v1",
            payload={"resource": request.resource, "record": record},
            observed_at=observed_at,
            tenant_id=self.tenant_id,
        )) for record in records)
