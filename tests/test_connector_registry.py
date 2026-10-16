"""Registration is an exact metadata check, not an authorization event."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from orion_connector_sdk import CONTRACT_VERSION, ReviewedConnector, discover, registry


def _entry(*, name="synthetic", version="0.1.0", value=None):
    return SimpleNamespace(
        name=name,
        value=value or "orion_connector_synthetic:SyntheticConnector",
        dist=SimpleNamespace(metadata={"Name": "orion-connector-synthetic"}, version=version),
    )


def _reviewed(**changes):
    fields = {
        "name": "synthetic",
        "distribution": "orion-connector-synthetic",
        "version": "0.1.0",
        "entry_point": "orion_connector_synthetic:SyntheticConnector",
    }
    fields.update(changes)
    return ReviewedConnector(**fields)


def test_reviewed_metadata_registration_never_loads_plugin_or_grants_authority(monkeypatch):
    entry = _entry()
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [entry])
    result = discover(_reviewed())
    assert result.contract_version == CONTRACT_VERSION
    assert result.execution_allowed is False
    with pytest.raises((TypeError, FrozenInstanceError)):
        result.execution_allowed = True


@pytest.mark.parametrize("entries", [[], [_entry(version="0.2.0")],
                                     [_entry(), _entry()]])
def test_unreviewed_version_missing_or_duplicate_denied(monkeypatch, entries):
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: entries)
    with pytest.raises(ValueError, match="reviewed connector unavailable"):
        discover(_reviewed())


def test_incompatible_contract_denied_before_discovery():
    with pytest.raises(ValueError, match="invalid reviewed connector contract"):
        _reviewed(contract_version="orion.connector.v2")
