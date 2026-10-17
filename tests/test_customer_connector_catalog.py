"""Catalog schemas are reviewed data, never connector imports or authority."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from deployment.customer.catalog import reviewed_catalog_entry
from orion_connector_sdk import ReviewedConnector, registry


def _case(name="erpnext"):
    distribution = (
        "originorion-connector-erpnext-discovery"
        if name == "erpnext"
        else "originorion-connector-synthetic"
    )
    entry_point = (
        "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter"
        if name == "erpnext"
        else "orion_connector_synthetic:SyntheticConnector"
    )
    reviewed = ReviewedConnector(name, distribution, "0.2.0rc1", entry_point)
    installed = SimpleNamespace(
        name=name,
        value=entry_point,
        dist=SimpleNamespace(metadata={"Name": distribution}, version="0.2.0rc1"),
    )
    document = {
        "schema_version": "orion.connector-configuration.v1",
        "connector": name,
        "fields": [
            {"name": "base_url", "label": "HTTPS base URL", "kind": "https_url", "required": True, "write_only": False},
            {"name": "api_secret", "label": "API secret", "kind": "secret", "required": True, "write_only": True},
        ],
    }
    raw = json.dumps(document, sort_keys=True).encode()
    return reviewed, installed, raw, hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("name", ["erpnext", "synthetic"])
def test_reviewed_packages_share_catalog_without_execution(monkeypatch, name):
    reviewed, installed, raw, digest = _case(name)
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [installed])
    entry = reviewed_catalog_entry(reviewed, schema_bytes=raw, schema_sha256=digest)
    assert entry.name == name
    assert entry.execution_allowed is False
    assert entry.fields[1].write_only is True
    assert not any("value" in field.__dataclass_fields__ for field in entry.fields)


def test_schema_tamper_and_unreviewed_connector_denied(monkeypatch):
    reviewed, installed, raw, digest = _case()
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [installed])
    with pytest.raises(ValueError, match="unreviewed connector schema"):
        reviewed_catalog_entry(reviewed, schema_bytes=raw + b" ", schema_sha256=digest)
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [])
    with pytest.raises(ValueError, match="reviewed connector unavailable"):
        reviewed_catalog_entry(reviewed, schema_bytes=raw, schema_sha256=digest)


def test_secret_must_be_write_only_and_schema_cannot_supply_values(monkeypatch):
    reviewed, installed, raw, _ = _case()
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [installed])
    document = json.loads(raw)
    document["fields"][1]["write_only"] = False
    changed = json.dumps(document).encode()
    with pytest.raises(ValueError, match="invalid connector field"):
        reviewed_catalog_entry(
            reviewed, schema_bytes=changed, schema_sha256=hashlib.sha256(changed).hexdigest()
        )
    document["fields"][1]["write_only"] = True
    document["fields"][1]["value"] = "secret-value"
    changed = json.dumps(document).encode()
    with pytest.raises(ValueError, match="invalid connector field"):
        reviewed_catalog_entry(
            reviewed, schema_bytes=changed, schema_sha256=hashlib.sha256(changed).hexdigest()
        )


def test_duplicate_json_keys_denied(monkeypatch):
    reviewed, installed, _, _ = _case()
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [installed])
    raw = (
        b'{"schema_version":"orion.connector-configuration.v1",'
        b'"connector":"erpnext","connector":"synthetic","fields":[]}'
    )
    with pytest.raises(ValueError, match="duplicate connector schema key"):
        reviewed_catalog_entry(
            reviewed, schema_bytes=raw, schema_sha256=hashlib.sha256(raw).hexdigest()
        )
