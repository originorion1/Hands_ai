"""Installed ERPNext response normalization behind the governed gateway.

The gateway owns native HTTPS and credentials. This module receives only the
already bounded response bytes plus canonical grants/requests and reuses the
existing permit-gated ERPNext adapters. It cannot select a destination.
"""

import hashlib
from dataclasses import asdict

from ..discovery.erpnext_pilot import ERPNextPilotReader
from ..discovery.erpnext_pilot_metadata import ERPNextPilotMetadataReader
from ..discovery.pilot_read import launch_pilot_read
from .broker_contract import private_bytes


class _ReceivedResponse:
    def __init__(self, raw, url):
        self.raw, self.url = raw, url

    def __enter__(self):
        return self

    def __exit__(self, *_arguments):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.raw[:limit]


def _received(bootstrap):
    raw = private_bytes(bootstrap["path"])
    if hashlib.sha256(raw).hexdigest() != bootstrap["source_digest"]:
        raise ValueError("received ERPNext response changed")
    return raw


def _opener(raw):
    def open_received(request, timeout):
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("bounded ERPNext parse timeout required")
        return _ReceivedResponse(raw, request.full_url)

    return open_received


def acquire_records(bootstrap, grant, request):
    raw = _received(bootstrap)
    adapter = ERPNextPilotReader(
        source_id=grant.source_id,
        api_key="gateway-normalized",
        api_secret="gateway-normalized-response",
        opener=_opener(raw),
    )
    return launch_pilot_read(
        request,
        authorization_id=grant.authorization_id,
        lookup=lambda _: grant,
        adapter=adapter,
    )


def acquire_metadata(bootstrap, grant, request, permit, target):
    raw = _received(bootstrap)
    adapter = ERPNextPilotMetadataReader(
        source_id=request.source_id,
        api_key="gateway-normalized",
        api_secret="gateway-normalized-response",
        opener=_opener(raw),
    )
    if target is None:
        catalog, complete = adapter.catalog(permit, grant.max_catalog_entries + 1)
        return {"catalog": list(catalog), "complete": complete}
    proposal = adapter.schema(permit, target)
    if proposal is None:
        return {
            "resource": target,
            "available": False,
            "fields": [],
            "date_fields": [],
            "declarations": [],
        }
    declarations = []
    if proposal.interpretation is not None:
        declarations = [
            asdict(candidate.declaration) for candidate in proposal.interpretation.candidates
        ]
    return {
        "resource": target,
        "available": True,
        "fields": list(proposal.fields),
        "date_fields": list(proposal.date_fields),
        "declarations": declarations,
    }
