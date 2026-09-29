"""Reviewed synthetic registry and separately granted fixed instrument snapshots."""

import copy
import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime

from orion.contracts import EvidenceKind
from orion.discovery.pilot_read import PilotAuthorization
from orion.discovery.read_window import ReviewedReadWindow
from orion.understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    SEMANTIC_EVALUATOR_VERSION,
    Instrument,
)


def build_independent_configs(baseconfigs, bodies, case):
    """No business mapping: numbers compete under the packaged measurement rules.

    The registry is separately reviewed fixture configuration, never a source
    response. Replacement snapshots preserve original collector/fact roots.
    """
    from orion.pilot.semantic_runtime import semantic_policy_sha256

    configs, bodies = copy.deepcopy(baseconfigs), copy.deepcopy(bodies)
    structural = configs[1]
    fields = ["f_a", "f_b", "f_c", "f_d", "f_e"]
    structural["grant"]["window"]["fields"] = fields
    structural["field_classifications"]["f_e"] = "public"
    schema = json.loads(bodies["metadata"])
    schema["schemas"]["r_01"].append(
        {"name": "f_e", "kind": "number", "classification": "public"}
    )
    rows = [
        dict(zip(fields, ("x_1", "c_01", "2024-06-02", 11, 3), strict=True)),
        dict(zip(fields, ("x_2", "c_01", "2024-06-03", 18, 5), strict=True)),
    ]
    bodies["metadata"] = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    bodies["read"] = json.dumps(
        {"resource": "r_01", "rows": rows}, sort_keys=True, separators=(",", ":")
    )
    for config in configs:
        config["source_digest"] = hashlib.sha256(bodies[config["operation"]].encode()).hexdigest()

    count = 1 if case == "independent_unknown" else (4 if case == "independent_revision" else 2)
    instruments, registry = [], []
    for index in range(count):
        collector = index % 2
        source = f"https://collector-{collector}.synthetic.test"
        resource = f"i_{collector:02d}"
        evidence_class = "process" if collector == 0 else "aggregate"
        if index < 2:
            instruments.append(Instrument(source, resource, f"synthetic-collector-{collector}",
                                          (evidence_class,)))
        instrument_fields = ANCHOR_FIELDS + (AGGREGATE_FIELDS if collector else ())
        expiry = structural["grant"]["window"]["expires_at"]
        window = ReviewedReadWindow("t_01", "c_01", resource, instrument_fields, "on",
                                    date(2024, 6, 1), date(2024, 6, 7),
                                    datetime.fromisoformat(expiry))
        grant = PilotAuthorization(f"instrument-grant-{index}", source, window, "id",
                                   "partition", f"synthetic-collector-{collector}",
                                   EvidenceKind.EXPERIMENT, 2)
        instrument_rows = []
        for subject, value, components in (
            ("x_1", 11 if index < 2 else 3, (4, 7) if index < 2 else (1, 2)),
            ("x_2", 18 if index < 2 else 5, (8, 10) if index < 2 else (2, 3)),
        ):
            old_id = f"a{collector}-{subject}"
            record_id = old_id if index < 2 else f"b{collector}-{subject}"
            row = {"id": record_id, "partition": "c_01", "on": "2024-06-04",
                   "subject_source": structural["grant"]["source_id"],
                   "subject_resource": "r_01", "subject_id": subject,
                   "evidence_class": evidence_class,
                   "channel": "settled_transfer" if collector == 0 else "reconciled_transfer",
                   "dimension": "currency", "value": value, "related_resource": "",
                   "replaces": "" if index < 2 else old_id}
            if collector:
                row.update(component_a=components[0], component_b=components[1])
            instrument_rows.append(row)
            registry.append({"source_id": source, "resource": resource,
                             "record_id": record_id,
                             "roots": [[f"reviewed-synthetic-collector-{collector}", subject]],
                             "parents": []})
        body = json.dumps({"resource": resource, "rows": instrument_rows},
                          sort_keys=True, separators=(",", ":"))
        config = copy.deepcopy(structural)
        config.update(operation=f"instrument_{index}", grant=json.loads(json.dumps(asdict(grant),
                      default=lambda obj: obj.isoformat())),
                      field_classifications={field: "public" for field in instrument_fields},
                      source_digest=hashlib.sha256(body.encode()).hexdigest())
        config["limits"].update(max_requests=1, failures=5)
        configs.append(config)
        bodies[config["operation"]] = body
    instrument_wire = [dict(asdict(item), classes=list(item.classes)) for item in instruments]
    semantic = {
        "version": 2, "study_id": "synthetic-installed-independent-study",
        "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
        "policy_sha256": semantic_policy_sha256(tuple(instruments)),
        "instruments": instrument_wire, "collector_registry": registry,
    }
    return configs, bodies, semantic
