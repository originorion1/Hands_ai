"""One metadata-first read-only runtime state owner; not a live startup override.

Composition occurs only in the independently protected supervisor. Acquisition
and reasoning processes cannot instantiate or select this owner over the wire.
The deployment must protect its dependencies; Python privacy is not containment.
"""

from ..contracts import utc_now
from .broker_contract import observations_from
from .readiness import release_report


class SupervisedReadOnlyRuntime:
    """Reuse canonical supervisors, admission and journals without new grants/stores."""

    def __init__(self, metadata, records):
        if metadata.operation != "metadata" or records.operation != "read":
            raise ValueError("separate metadata and record authorizations required")
        m, r = metadata.grant.request, records.grant.window
        if (m.tenant_id, m.company, m.source_id) != (r.tenant_id, r.company, records.source_id):
            raise ValueError("runtime scope mismatch")
        self.metadata, self.records = metadata, records
        # Neither authority nor discovery completion is restored by construction.
        self.metadata_admitted = False
        self.metadata_observations = ()

    def health(self):
        try:
            budgets = {
                name: owner.journal.inspect()
                for name, owner in (("metadata", self.metadata), ("read", self.records))
            }
            safe = all(not b["stopped"] and not b["pending"] for b in budgets.values())
            safe = safe and all(
                utc_now() < min(o.expires_at, o.limits.expires_at)
                for o in (self.metadata, self.records)
            )
        except Exception:  # noqa: BLE001 - unavailable custody is never healthy
            budgets, safe = {}, False
        return {
            "status": "healthy" if safe else "blocked",
            "custody_available": safe,
            "metadata_admitted_this_start": self.metadata_admitted,
            "record_authority_armed": self.records.armed and safe,
            "budgets": budgets,
            "live_ready": release_report()["live_ready"],
            "execution_allowed": False,
            "allow_live_customer_access": False,
        }

    def owner_for(self, message):
        if type(message) is not dict or message.get("operation") not in ("metadata", "read"):
            raise ValueError("explicit bounded operation required")
        if message["operation"] == "read" and not self.metadata_admitted:
            raise ValueError("governed metadata discovery required this start")
        return self.metadata if message["operation"] == "metadata" else self.records

    def accept(self, owner, response):
        if owner not in (self.metadata, self.records):
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
        owner = {"metadata": self.metadata, "read": self.records}.get(operation)
        if owner is None:
            raise ValueError("explicit control scope required")
        if message.get("control") == "arm" and owner is self.records and not self.metadata_admitted:
            raise ValueError("discovery never grants record authority")
        return owner.control(message)
