"""Version and reviewed-scope validation precede private runtime startup."""

import copy
import json

import pytest
from independent_evidence_support import build_independent_configs
from test_broker_metadata import MetadataHarness
from test_supervised_broker import Harness

from orion.pilot import deployment


@pytest.mark.parametrize("defect", ["old_version", "semantic_version", "duplicate", "unknown_operation",
                                   "too_many", "missing_registry", "wrong_company", "wrong_fields"])
def test_invalid_instrument_manifest_denied_before_private_startup(tmp_path, monkeypatch, defect):
    records, metadata = Harness(tmp_path / "records"), MetadataHarness(tmp_path / "metadata")
    configs, _, semantic = build_independent_configs([metadata.config, records.config], {
        "metadata": json.dumps({"schemas": {"r_01": [{"name": "f_d", "kind": "number"}]}}),
        "read": json.dumps({"resource": "r_01", "rows": records.rows}),
    }, "independent_convergence")
    value = {"version": 3, "mode": "synthetic_read_only", "configs": configs,
             "semantic": semantic, "state_directory": "unused", "keys_directory": "unused",
             "certificate": "unused", "certificate_sha256": "0" * 64,
             "artifact_record_sha256": "0" * 64, "host": deployment.V4_APPROVED, "policy": {}}
    if defect == "old_version":
        value["version"] = 2
    elif defect == "semantic_version":
        value["semantic"]["version"] = 1
    elif defect == "duplicate":
        value["configs"][3]["operation"] = "instrument_0"
    elif defect == "unknown_operation":
        value["configs"][2]["operation"] = "caller_selected"
    elif defect == "too_many":
        value["configs"] += [copy.deepcopy(configs[2])] * 7
    elif defect == "missing_registry":
        value["semantic"]["collector_registry"] = []
    elif defect == "wrong_company":
        value["configs"][2]["grant"]["window"]["company"] = "wrong-synthetic-company"
    else:
        value["configs"][2]["grant"]["window"]["fields"] = ["id", "partition", "on"]
    private_access = []
    monkeypatch.setattr(deployment.shutil, "which", lambda *a, **k: "/synthetic/tool")
    monkeypatch.setattr(deployment, "private_bytes", lambda path: json.dumps(value).encode())
    monkeypatch.setattr(deployment, "private_directory", lambda path: private_access.append(path))
    with pytest.raises((ValueError, deployment.JournalDenied)):
        deployment.load_manifest("synthetic-manifest")
    assert private_access == []
