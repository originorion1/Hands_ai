"""Install the actual wheel offline and exercise its private deployment path."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).with_name("packaged_runtime_driver.py")


@pytest.fixture(scope="module")
def clean_artifact(tmp_path_factory):
    root = tmp_path_factory.mktemp("clean-runtime-artifact")
    wheels = root / "wheels"
    wheels.mkdir()
    subprocess.run(
        [
            os.environ.get("ORION_BUILD_PYTHON", sys.executable),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(wheels),
            str(ROOT),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (wheel,) = wheels.glob("*.whl")
    environment = root / "installed"
    subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    python = environment / "bin/python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    return root, wheel, python


def installed_run(artifact, *arguments, timeout=30):
    root, _, python = artifact
    return subprocess.run(
        [str(python), "-I", "-m", "orion.pilot.deployment", *arguments],
        cwd=root,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def test_failure_diagnostic_rejects_untrusted_runtime_values():
    from packaged_runtime_driver import bounded_reasoning_denial

    report = bounded_reasoning_denial(
        {"status": ["private-value"]},
        {
            "status": {"private": "value"},
            "failure_boundary": ["private-value"],
            "dead_roles": ["audit", {"private": "value"}],
        },
        {"metadata": 1, "read": True, "instrument_0": 0, "private-operation": 9},
    )
    assert report == {
        "status": "FAIL",
        "reason": "record_reasoning_denied",
        "response_status": "invalid",
        "health_status": "invalid",
        "failure_boundary": "invalid",
        "dead_roles": [],
        "source_io": {"metadata": 1, "instrument_0": 0},
        "LIVE_PILOT_READY": False,
    }


def test_wheel_contains_runtime_without_checkout_or_fixture_dependencies(clean_artifact):
    root, wheel, python = clean_artifact
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "orion/pilot/deployment.py" in names
        assert "orion/pilot/production.py" in names
        assert "orion/pilot/ipc.py" in names
        assert "orion/pilot/isolation.py" in names
        assert not any(name.startswith(("tests/", "tools/")) for name in names)
    inspected = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "import json,sys,orion.pilot.deployment as d; "
                "print(json.dumps({'module':d.__file__,'paths':sys.path}))"
            ),
        ],
        cwd=root,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(inspected.stdout)
    assert str(ROOT) not in value["module"]
    assert all(str(ROOT) not in path for path in value["paths"])
    artifact = installed_run(clean_artifact, "--artifact")
    assert artifact.returncode == 0, artifact.stderr
    identity = json.loads(artifact.stdout)
    assert len(identity["record_sha256"]) == 64
    assert hashlib.sha256(wheel.read_bytes()).hexdigest()
    entrypoint = subprocess.run(
        [str(python.parent / "orion-runtime"), "--artifact"],
        cwd=root,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(entrypoint.stdout) == identity


def test_installed_identity_rejects_source_tree_substitution(clean_artifact):
    root, _, python = clean_artifact
    substituted = subprocess.run(
        [str(python), "-m", "orion.pilot.deployment", "--artifact"],
        cwd=root,
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert substituted.returncode == 2
    report = json.loads(substituted.stdout)
    assert report["status"] == "startup_denied"
    assert report["LIVE_PILOT_READY"] is False


def test_clean_installed_startup_fails_closed_without_valid_private_deployment(clean_artifact):
    for arguments in ((), ("--health",), ("--serve", "/nonexistent/manifest.json")):
        completed = installed_run(clean_artifact, *arguments)
        assert completed.returncode == 2
        value = json.loads(completed.stdout)
        assert value["LIVE_PILOT_READY"] is False
        assert value["execution_allowed"] is False


def test_installed_artifact_tampering_denies_identity_and_startup(clean_artifact, tmp_path):
    _, _, python = clean_artifact
    damaged = tmp_path / "tampered-install"
    shutil.copytree(python.parent.parent, damaged, symlinks=True)
    (runtime,) = damaged.glob("lib/python*/site-packages/orion/pilot/runtime.py")
    runtime.write_bytes(runtime.read_bytes() + b"\n# synthetic artifact tamper\n")
    result = subprocess.run(
        [str(damaged / "bin/python"), "-I", "-m", "orion.pilot.deployment", "--artifact"],
        cwd=tmp_path,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "startup_denied"
    assert report["LIVE_PILOT_READY"] is False


@pytest.mark.parametrize(
    "case",
    (
        "full",
        "erpnext_candidate",
        "erpnext_grant_transition",
        "production_discovery",
        "ipv6",
        "revision",
        "retention",
        "redirect",
        "restart_revalidation",
        "rotation",
        "witness_audit_rollback",
        "witness_evidence_rollback",
        "witness_unavailable",
        "revoke",
        "audit",
        "authorization",
        "evidence",
        "security",
        "semantic",
        "semantic_failure",
        "semantic_retention",
        "semantic_changed",
        "semantic_missing",
        "independent_convergence",
        "independent_revision",
        "independent_unknown",
        "independent_failure",
        "independent_expired",
    ),
)
def test_installed_runtime_against_unmodified_private_https_source(clean_artifact, tmp_path, case):
    from https_broker_support import certificates
    from test_broker_metadata import MetadataHarness
    from test_supervised_broker import Harness

    from orion.pilot.isolation import LAB_PATH, prerequisites

    if not prerequisites() or not all(
        shutil.which(tool, path=LAB_PATH) for tool in ("curl", "setpriv")
    ):
        pytest.skip("Required isolation tools unavailable; no kernel deployment proof claimed")

    metadata = MetadataHarness(
        tmp_path / "metadata",
        schemas={
            "r_01": [
                {"name": name, "kind": kind, "classification": "public"}
                for name, kind in (
                    ("f_a", "reference"),
                    ("f_b", "reference"),
                    ("f_c", "date"),
                    ("f_d", "number"),
                )
            ]
        },
    )
    records = Harness(tmp_path / "records")
    records.key, records.secret = metadata.key, metadata.secret
    configs, bodies = [], {}
    for harness in (metadata, records):
        body = json.loads(harness.source.read_text())
        body.pop("credential_digest")
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        bodies[harness.config["operation"]] = raw.decode()
        harness.config["source_digest"] = hashlib.sha256(raw).hexdigest()
        harness.config["source_path"] = "/source/unavailable.json"
        harness.config["limits"].update(
            minimum_interval_seconds=1,
            failures=5,
            max_requests=8
            if harness.config["operation"] == "metadata"
            else (2 if case in ("revision", "rotation") else 1),
            total_response_bytes=524288,
        )
        configs.append(harness.config)
    native_bodies = None
    source_credential = "SyntheticCredentialNoCustomer0123456789"
    if case in ("erpnext_candidate", "erpnext_grant_transition", "production_discovery"):
        import urllib.parse

        from orion.pilot.broker_contract import ERPNEXT_VERSION

        source = (
            "https://opaque.test:44443"
            if case == "production_discovery"
            else "https://opaque.test"
        )
        metadata_config, record_config = configs
        metadata_config.update(
            version=ERPNEXT_VERSION,
            mode="candidate_erpnext_read_only",
            protocol="erpnext_metadata_v1",
        )
        metadata_config.pop("source_path")
        metadata_config.pop("source_digest")
        resource = "Opaque Ledger 7F3A"
        date_field = "opaque_date_7f3a"
        amount_field = "opaque_amount_2c9e"
        fields = sorted(["name", "company", "docstatus", date_field, amount_field])
        window = record_config["grant"]["window"]
        window.update(
            resource=resource,
            fields=fields,
            date_field=date_field,
        )
        record_config["grant"].update(
            source_id=source,
            identity_field="name",
            company_field="company",
            provenance_source="erpnext-historical-sample-read-only",
            evidence_kind="api",
            max_records=1,
        )
        metadata_config["grant"]["request"]["source_id"] = source
        record_config.update(
            version=ERPNEXT_VERSION,
            mode="candidate_erpnext_read_only",
            protocol="erpnext_records_v1",
            field_classifications={field: "public" for field in fields},
        )
        record_config.pop("source_path")
        record_config.pop("source_digest")
        catalog = "/api/resource/DocType?" + urllib.parse.urlencode(
            {
                "fields": '["name"]',
                "limit_start": 0,
                "limit_page_length": metadata_config["grant"]["max_catalog_entries"] + 1,
                "order_by": "name asc",
            }
        )
        schema_path = "/api/method/frappe.desk.form.load.getdoctype?" + urllib.parse.urlencode(
            {"doctype": resource}
        )
        filters = [["company", "=", window["company"]], ["docstatus", "=", 1],
                   [date_field, ">=", window["start"]],
                   [date_field, "<=", window["end"]]]
        record_path = "/api/resource/" + urllib.parse.quote(resource, safe="") + "?" + urllib.parse.urlencode(
            {
                "fields": json.dumps(fields, separators=(",", ":")),
                "filters": json.dumps(filters, separators=(",", ":")),
                "order_by": date_field + " desc, name desc",
                "limit_start": 0,
                "limit_page_length": 1,
            }
        )
        native_bodies = {
            catalog: json.dumps({"data": [{"name": resource}]}),
            schema_path: json.dumps({"message": {"docs": [{
                "name": resource,
                "is_submittable": 1,
                "fields": [
                    {"fieldname": "company", "fieldtype": "Link", "options": "Company"},
                    {"fieldname": date_field, "fieldtype": "Date"},
                    {"fieldname": amount_field, "fieldtype": "Currency"},
                ],
            }]}}),
            record_path: json.dumps({"data": [{
                "name": "SINV-0001",
                "company": window["company"],
                "docstatus": 1,
                date_field: window["start"],
                amount_field: 9,
            }]}),
        }
        source_credential = "CandidateKey123456:CandidateSecret123456"
    semantic = None
    if case.startswith("independent_"):
        from independent_evidence_support import build_independent_configs

        configs, bodies, semantic = build_independent_configs(configs, bodies, case)
    host = "fd42:6f72:696f::2" if case == "ipv6" else "192.0.2.2"
    certificate, key = certificates(
        tmp_path,
        hostname="opaque.test" if case == "production_discovery" else host,
    )
    payload = {
        "case": case,
        "host": host,
        "root": str(tmp_path),
        "configs": configs,
        "bodies": bodies,
        "issuer": metadata.key.decode(),
        "worker_secret": metadata.secret,
        "source_credential": source_credential,
        "replacement_source_credential": "SyntheticCredentialReplacement9876543210",
        "certificate": str(certificate),
        "source_key": str(key),
        "wheel_sha256": hashlib.sha256(clean_artifact[1].read_bytes()).hexdigest(),
        "attacker_script": str(Path(__file__).with_name("installed_runtime_attacker.py")),
        "semantic": semantic,
        "native_bodies": native_bodies,
    }
    root, _, python = clean_artifact
    completed = subprocess.run(
        [str(python), "-I", str(DRIVER)],
        input=json.dumps(payload),
        cwd=root,
        env={"PATH": os.defpath},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout)
    print(json.dumps(report, sort_keys=True))
    assert report["status"] == "PASS", report
    assert report["source_unmodified"] is True
    assert report["clean_installed_artifact"] is True
    assert report["LIVE_PILOT_READY"] is False
    assert all(type(value) is bool for value in report["checks"].values()), report
    assert all(report["checks"].values()), report
