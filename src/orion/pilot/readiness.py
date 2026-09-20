"""Non-activating release gate: missing deployment controls cannot be waived by config."""
import argparse
import json
import os
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..discovery.erpnext_adapter import _normalize_base_url
from ..discovery.erpnext_live_session import CredentialEnvironmentReferences
from ..discovery.json_boundary import unique_json_object
from ..discovery.pilot_read import _text
from .broker_contract import digest
from .isolation import KernelUnavailable
from .journal import JournalDenied


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


DISCOVERY_NOT_APPLICABLE = {
    'EPISTEMIC_SAFETY': 'no_epistemic_promotion_in_read_only_discovery',
    'SEMANTIC_UNDERSTANDING': 'no_business_semantic_claim_in_read_only_discovery',
    'WORLD_MODEL': 'no_world_model_mutation_in_read_only_discovery',
}

DISCOVERY_EVIDENCE = {
    'SECURITY': 'closed_launcher_exact_artifact_profile_and_host_controls',
    'AUTHORIZATION': 'approved_access_ledger_and_separate_grant_owners',
    'DISCOVERY': 'metadata_only_bounded_erpnext_scope',
    'PROVENANCE': 'canonical_api_admission_and_evidence_lineage',
    'RESTART': 'retained_witnessed_state_and_unarmed_restart',
    'TRANSPORT': 'reviewed_hostname_addresses_port_tls_and_default_deny_egress',
    'SECRETS': 'private_reference_only_keys_and_gateway_credential_custody',
    'TENANT_ISOLATION': 'ledger_and_grants_bind_tenant_company_and_source',
    'AUDIT': 'durable_attempt_transition_and_witness_progress',
    'OBSERVABILITY': 'secret_free_local_status_counters_and_audit',
    'FAILURE_SAFETY': 'fail_closed_validation_durable_stop_and_gateway_cutoff',
    'DATA_MINIMIZATION': 'metadata_first_exclusions_and_bounded_retention_inputs',
    'COST_CONTROL': 'ledger_and_journal_request_byte_and_time_ceilings',
    'ERP_GATEWAY': 'fixed_read_only_erpnext_request_encoder',
    'TEST_COVERAGE': 'installed_v6_qualification_is_external_exact_commit_evidence',
    'DEPLOYMENT_CONFIGURATION': 'approved_wheel_profile_service_and_namespace',
}


def discovery_release_report(manifest_path=None):
    """Inspect the scope-specific v1 milestone without DNS or customer I/O."""
    requirements = {
        category: {
            'category': category,
            'status': (
                GateStatus.NOT_APPLICABLE
                if category in DISCOVERY_NOT_APPLICABLE
                else GateStatus.BLOCKED
            ),
            'critical': category not in DISCOVERY_NOT_APPLICABLE,
            'reason': (
                DISCOVERY_NOT_APPLICABLE[category]
                if category in DISCOVERY_NOT_APPLICABLE
                else DISCOVERY_EVIDENCE[category]
            ),
        }
        for category, _, _ in GATES
    }
    report = {
        'version': 1,
        'milestone': 'bounded_read_only_discovery_v1',
        'status': 'blocked',
        'qualification_ready': False,
        'ready_for_unarmed_startup': False,
        'read_only': True,
        'metadata_only_startup': True,
        'record_authority': 'separate_witnessed_transition_then_arm',
        'customer_writes_allowed': False,
        'execution_allowed': False,
        'allow_live_customer_access': False,
        'customer_connections': 0,
        'gates': list(requirements.values()),
        'missing_prerequisites': [
            'private_v6_manifest',
            'trusted_access_ledger_approval',
            'trusted_host_attestation_approval',
            'reviewed_destination_and_tls_identity',
            'exact_installed_artifact_and_profile',
        ],
    }
    if manifest_path is None:
        return report
    try:
        from .deployment import load_manifest
        from .production import load_production_evidence

        manifest = load_manifest(manifest_path, enrollment=True)
        if manifest.get('version') != 6:
            raise ValueError('governed discovery manifest version required')
        evidence = load_production_evidence(manifest)
    except FileNotFoundError:
        report['missing_prerequisites'] = ['private_deployment_evidence_missing']
        return report
    except (OSError, ValueError, KeyError, TypeError, JournalDenied, KernelUnavailable):
        report['missing_prerequisites'] = ['bound_private_deployment_evidence_invalid']
        return report
    for gate in requirements.values():
        if gate['status'] != GateStatus.NOT_APPLICABLE:
            gate.update(status=GateStatus.PASS)
    qualification = evidence['qualification']
    enrollment_ready = qualification
    if not qualification:
        try:
            from .progress_witness import (
                enrollment_path,
                verify_enrollment_receipt,
                witness_contract,
            )

            verify_enrollment_receipt(
                enrollment_path(manifest['state_directory']), witness_contract(manifest)
            )
            enrollment_ready = True
        except (OSError, ValueError, KeyError, TypeError, JournalDenied):
            requirements['RESTART'].update(
                status=GateStatus.BLOCKED,
                reason='witness_enrollment_required',
            )
            report.update(
                status='enrollment_required',
                qualification_ready=True,
                missing_prerequisites=['witness_enrollment_required'],
            )
            return report
    report.update(
        status='qualification_ready' if qualification else 'ready_for_unarmed_startup',
        qualification_ready=enrollment_ready,
        ready_for_unarmed_startup=not qualification and enrollment_ready,
        missing_prerequisites=(
            ['reviewed_external_destination_and_host_evidence'] if qualification else []
        ),
        artifact_record_sha256=manifest['artifact_record_sha256'],
        deployment_profile_sha256=manifest['deployment_profile_sha256'],
        destination_sha256=digest(evidence['destination']),
        access_ledger_sha256=evidence['access_ledger_sha256'],
        host_attestation_sha256=evidence['host_attestation_sha256'],
        network_mode=evidence['destination']['network_mode'],
    )
    return report


def start_discovery_pilot(manifest_path):
    """Replace this process only with the fixed installed unarmed launcher."""
    report = discovery_release_report(manifest_path)
    if not report['ready_for_unarmed_startup']:
        raise ValueError('reviewed_discovery_startup_unavailable')
    manifest_path = str(Path(manifest_path).resolve())
    argv = [
        str(Path(sys.executable).absolute()),
        '-I',
        '-m',
        'orion.pilot.deployment',
        '--serve',
        manifest_path,
    ]
    os.execve(argv[0], argv, {'PATH': os.defpath})
    raise RuntimeError('reviewed discovery launcher returned')


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
    parser.add_argument('--discovery-manifest')
    parser.add_argument('--start',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.config and args.discovery_manifest:
            raise ValueError('one readiness contract required')
        if args.discovery_manifest:
            report = discovery_release_report(args.discovery_manifest)
            if args.start:
                start_discovery_pilot(args.discovery_manifest)
            print(json.dumps(report, sort_keys=True))
            return 0 if report['qualification_ready'] else 2
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
