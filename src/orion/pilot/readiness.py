"""Non-activating release gate: missing deployment controls cannot be waived by config."""
import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..discovery.erpnext_adapter import _normalize_base_url
from ..discovery.erpnext_live_session import CredentialEnvironmentReferences
from ..discovery.json_boundary import unique_json_object
from ..discovery.pilot_read import _text


class GateStatus(StrEnum):
    PASS='PASS'
    FAIL='FAIL'
    BLOCKED='BLOCKED'
    NOT_APPLICABLE='NOT_APPLICABLE'


# Each status evaluates the complete live criterion, not a narrower unit-test claim.
# These are explicit audited implementation gaps, not operator-overridable flags.
GATES=(
    ('SECURITY','FAIL','same_process_injection_bypass'),
    ('EPISTEMIC_SAFETY','BLOCKED','prototype_promotion_not_attested'),
    ('AUTHORIZATION','BLOCKED','protected_issuer_artifact_and_operator_identity_unattested'),
    ('DISCOVERY','BLOCKED','record_identity_and_partition_bindings_unresolved'),
    ('SEMANTIC_UNDERSTANDING','BLOCKED','commercial_roles_not_validated'),
    ('WORLD_MODEL','BLOCKED','world_model_durable_revision_index_missing'),
    ('PROVENANCE','BLOCKED','protected_admission_archive_collector_root_unattested'),
    ('RESTART','BLOCKED','protected_checkpoint_recovery_rollback_witness_unattested'),
    ('TRANSPORT','BLOCKED','confined_gateway_production_erp_lifecycle_unverified'),
    ('SECRETS','FAIL','legacy_same_process_credential_surface'),
    ('TENANT_ISOLATION','BLOCKED','legacy_unscoped_store_surface'),
    ('AUDIT','BLOCKED','protected_custody_production_lifecycle_unattested'),
    ('OBSERVABILITY','BLOCKED','alert_delivery_and_operational_slos_unverified'),
    ('FAILURE_SAFETY','BLOCKED','complete_isolated_runtime_failure_matrix_unattested'),
    ('DATA_MINIMIZATION','BLOCKED','sensitive_classification_retention_erasure_policy_unvalidated'),
    ('COST_CONTROL','BLOCKED','production_os_egress_policy_deployment_unattested'),
    ('ERP_GATEWAY','BLOCKED','second_protocol_full_lifecycle_unproven'),
    ('TEST_COVERAGE','BLOCKED','complete_release_candidate_simulation_missing'),
    ('DEPLOYMENT_CONFIGURATION','BLOCKED','production_artifact_operator_keys_host_policy_unattested'),
)


@dataclass(frozen=True, slots=True)
class PilotConfig:
    tenant_id: str
    company: str
    source_id: str
    key_reference: str
    secret_reference: str
    mode: str

    def __post_init__(self):
        for value in (self.tenant_id,self.company,self.source_id):
            _text(value)
        if self.mode!='read_only' or _normalize_base_url(self.source_id)!=self.source_id:
            raise ValueError('unsupported pilot configuration')
        CredentialEnvironmentReferences(self.key_reference,self.secret_reference)


def load_config(path):
    with Path(path).open('rb') as stream:
        raw=stream.read(16385)
    if len(raw)>16384:
        raise ValueError('configuration oversized')
    payload=json.loads(raw,object_pairs_hook=unique_json_object)
    if type(payload) is not dict or set(payload)!=set(PilotConfig.__dataclass_fields__):
        raise ValueError('configuration fields mismatch')
    return PilotConfig(**payload)


def release_report():
    gates=[{'category':category,'status':status,'critical':True,'reason':reason}
           for category,status,reason in GATES]
    ready=bool(gates) and all(g['status']==GateStatus.PASS for g in gates)
    return {'version':1,'live_ready':ready,'read_only':True,'execution_allowed':False,
            'customer_connections':0,'gates':gates}


def start_pilot(config):
    if type(config) is not PilotConfig:
        raise ValueError('explicit pilot configuration required')
    config.__post_init__()
    if not release_report()['live_ready']:
        raise ValueError('not_live_pilot_ready')
    # No reviewed production worker exists in this tree. Do not create one via a
    # caller-supplied callable or an environment-selected module/plugin.
    raise ValueError('reviewed_live_runtime_unavailable')


def main(argv=None):
    parser=argparse.ArgumentParser(description='Inspect pilot release gates; never bypass them.')
    parser.add_argument('--config')
    parser.add_argument('--start',action='store_true')
    args=parser.parse_args(argv)
    try:
        config=load_config(args.config) if args.config else None
        if args.start:
            start_pilot(config)
    except Exception:  # noqa: BLE001 - configuration/secret values must not reach console
        report=release_report()
        report['status']='startup_denied'
        print(json.dumps(report,sort_keys=True))
        return 2
    report=release_report()
    report['status']='ready' if report['live_ready'] else 'blocked'
    print(json.dumps(report,sort_keys=True))
    return 0 if report['live_ready'] else 2


if __name__=='__main__':
    raise SystemExit(main())
