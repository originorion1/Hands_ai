"""One metadata-first read-only runtime state owner; not a live startup override.

Composition occurs only in the independently protected supervisor. Acquisition
and reasoning processes cannot instantiate or select this owner over the wire.
The deployment must protect its dependencies; Python privacy is not containment.
"""

from ..contracts import EvidenceKind, utc_now
from .broker_contract import (
    INSTRUMENT_OPERATIONS,
    observations_from,
    transition_policy_from,
    transition_record_config,
)
from .readiness import release_report


class SupervisedReadOnlyRuntime:
    """Reuse canonical supervisors, admission and journals without new grants/stores."""

    def __init__(self, metadata, records=None, *instruments, transition=None):
        if metadata.operation != "metadata" or (
            records is not None and records.operation != "read"
        ):
            raise ValueError("separate metadata and record authorizations required")
        self.transition = (
            transition_policy_from(transition) if transition is not None else None
        )
        if records is None and self.transition is None:
            raise ValueError("record authorization or transition envelope required")
        m = metadata.grant.request
        if records is not None:
            r = records.grant.window
            if (m.tenant_id, m.company, m.source_id) != (
                r.tenant_id, r.company, records.source_id
            ):
                raise ValueError("runtime scope mismatch")
            if self.transition is not None:
                transition_record_config(records.config, self.transition)
        elif (m.tenant_id, m.company, m.source_id) != (
            self.transition["tenant_id"],
            self.transition["company"],
            self.transition["source_id"],
        ):
            raise ValueError("runtime transition scope mismatch")
        self.metadata, self.records = metadata, records
        self.owners = {"metadata": metadata}
        if records is not None:
            self.owners["read"] = records
        if len(instruments) > len(INSTRUMENT_OPERATIONS):
            raise ValueError("bounded instrument authorizations required")
        for instrument in instruments:
            if instrument.operation not in INSTRUMENT_OPERATIONS:
                raise ValueError("separate scoped instrument authorization required")
            window = instrument.grant.window
            if (
                instrument.operation in self.owners
                or instrument.grant.evidence_kind is not EvidenceKind.EXPERIMENT
                or (window.tenant_id, window.company) != (m.tenant_id, m.company)
                or records is None
                or instrument.source_id == records.source_id
            ):
                raise ValueError("separate scoped instrument authorization required")
            self.owners[instrument.operation] = instrument
        # Neither authority nor discovery completion is restored by construction.
        self.metadata_admitted = False
        self.metadata_observations = ()

    def health(self):
        try:
            budgets = {
                name: owner.journal.inspect()
                for name, owner in self.owners.items()
            }
            safe = all(not b["stopped"] and not b["pending"] for b in budgets.values())
            safe = safe and all(
                utc_now() < min(o.expires_at, o.limits.expires_at)
                for o in self.owners.values()
            )
        except Exception:  # noqa: BLE001 - unavailable custody is never healthy
            budgets, safe = {}, False
        report = {
            "status": "healthy" if safe else "blocked",
            "custody_available": safe,
            "metadata_admitted_this_start": self.metadata_admitted,
            "record_authority_provisioned": self.records is not None,
            "record_authority_armed": self.records is not None and self.records.armed and safe,
            "budgets": budgets,
            "live_ready": release_report()["live_ready"],
            "execution_allowed": False,
            "allow_live_customer_access": False,
        }
        instruments = {
            operation: owner.armed
            for operation, owner in self.owners.items()
            if operation in INSTRUMENT_OPERATIONS
        }
        if instruments:
            report["instrument_authority_armed"] = instruments
        return report

    def owner_for(self, message):
        if (
            type(message) is not dict
            or type(message.get("operation")) is not str
            or message["operation"] not in self.owners
        ):
            raise ValueError("explicit bounded operation required")
        if message["operation"] != "metadata" and not self.metadata_admitted:
            raise ValueError("governed metadata discovery required this start")
        return self.owners[message["operation"]]

    def accept(self, owner, response):
        if owner not in self.owners.values():
            raise ValueError("runtime owner mismatch")
        if response.get("status") == "admitted":
            observations = observations_from(response["observations"])
            if owner is self.metadata:
                if not observations or any(
                    o.evidence.tenant_id != owner.grant.request.tenant_id
                    or o.evidence.payload["authorization_id"] != owner.grant.authorization_id
                    or o.evidence.payload["record_reads_allowed"] is not False
                    for o in observations
                ):
                    raise ValueError("canonical governed discovery required")
                self.metadata_observations = observations
                self.metadata_admitted = True
            # No commercial role, prediction, action or validated knowledge is
            # inferred from acquisition. Existing metadata interpretations retain
            # their concrete UNKNOWNs; records add no invented business meaning.
            response = dict(
                response,
                interpretation="UNKNOWN",
                interpretation_reason="business_semantics_not_validated",
            )
        return response

    def handle(self, message):
        try:
            owner = self.owner_for(message)
            return self.accept(owner, owner.handle(message))
        except Exception:  # noqa: BLE001 - no dependency failure grants access
            return {
                "status": "denied",
                "execution_allowed": False,
                "allow_live_customer_access": False,
            }

    def control(self, operation, message):
        owner = self.owners.get(operation)
        if owner is None:
            raise ValueError("explicit control scope required")
        if message.get("control") == "arm" and owner is not self.metadata and not self.metadata_admitted:
            raise ValueError("discovery never grants record authority")
        return owner.control(message)

    def validate_transition_config(self, config):
        if (
            self.transition is None
            or self.records is not None
            or not self.metadata_admitted
        ):
            raise ValueError("record grant transition unavailable")
        grant = transition_record_config(config, self.transition)
        proposals = [
            proposal
            for observation in self.metadata_observations
            for proposal in observation.evidence.payload["proposals"]
            if proposal["resource"] == grant.window.resource
        ]
        if (
            len(proposals) != 1
            or tuple(proposals[0]["fields"]) != grant.window.fields
            or grant.window.date_field not in proposals[0]["date_fields"]
        ):
            raise ValueError("record grant not supported by admitted metadata")
        return grant

    def provision(self, records):
        """Install one already-persisted exact owner; never arm it or issue a grant."""
        if records.operation != "read":
            raise ValueError("record grant transition unavailable")
        self.validate_transition_config(records.config)
        self.records = records
        self.owners["read"] = records
        return {"status": "provisioned_unarmed", "binding": records.binding,
                "execution_allowed": False, "allow_live_customer_access": False}
