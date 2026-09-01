"""Local, mock-ERPNext demonstration of ORION's shadow-only v0.1 slice.

The module never opens a network connection and does not expose an execution
path. It exercises the ERPNext adapter's normalized read-only contract using a
small in-memory response that is shaped like Frappe's REST API.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import Any, Self

from .contracts import Observation
from .discovery.erpnext_adapter import ERPNextDiscoveryAdapter
from .discovery.pipeline import DiscoveryPipeline
from .kernel import OrionKernel
from .knowledge.promotion import KnowledgeStore
from .shadow.decision import ShadowDecision, propose_from_knowledge
from .stores.memory import InMemoryEvidenceStore
from .understanding.graph import GraphStore
from .understanding.hypotheses import Hypothesis, generate_hypotheses
from .validation.claims import Assurance, validate_hypothesis


@dataclass(frozen=True, slots=True)
class DemoReport:
    """Auditable result of a local shadow-only demonstration."""

    tenant_id: str
    observations: int
    evidence: int
    graph_nodes: int
    knowledge_entries: int
    provenance_evidence_ids: int
    execution_allowed: bool
    action: str


class _MockResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._payload


def _mock_erpnext_opener(*_: object, **__: object) -> _MockResponse:
    """Return safe local data; no request is sent to the configured URL."""
    return _MockResponse(
        {
            "data": [
                {"name": "PINV-0001", "doctype": "Purchase Invoice", "docstatus": 0},
                {"name": "PINV-0002", "doctype": "Purchase Invoice", "docstatus": 0},
            ]
        }
    )


def run_mock_erpnext_shadow_demo(*, tenant_id: str = "demo-tenant") -> DemoReport:
    """Run the v0.1 learning loop against local ERPNext-shaped fixture data."""
    adapter = ERPNextDiscoveryAdapter(
        base_url="https://mock.erpnext.invalid",
        tenant_id=tenant_id,
        api_key="local-demo-key",
        api_secret="local-demo-secret",
        resources=("Purchase Invoice",),
        opener=_mock_erpnext_opener,
    )
    observations = adapter.discover()

    evidence_store = InMemoryEvidenceStore()
    OrionKernel(evidence_store=evidence_store).discover(adapter, tenant_id=tenant_id)

    graph = GraphStore()
    pipeline = DiscoveryPipeline(source=_StaticDiscoverySource(observations), graph=graph)
    pipeline_result = pipeline.run()

    hypotheses = generate_hypotheses(observations)
    if not hypotheses:
        raise RuntimeError("mock discovery did not produce a provenance-carrying hypothesis")
    validated = _validate(hypotheses[0])
    knowledge_store = KnowledgeStore()
    entry = knowledge_store.promote(validated, scope="customer")
    decision = propose_from_knowledge(
        knowledge_store.list(tenant_id=tenant_id, scope="customer"),
        tenant_id=tenant_id,
        action="prepare Purchase Invoice draft for human review",
    )
    _ensure_shadow_only(decision)

    return DemoReport(
        tenant_id=tenant_id,
        observations=len(observations),
        evidence=len(evidence_store.query(tenant_id=tenant_id)),
        graph_nodes=pipeline_result.nodes_added,
        knowledge_entries=len(knowledge_store.list(tenant_id=tenant_id, scope="customer")),
        provenance_evidence_ids=len(entry.evidence_ids),
        execution_allowed=decision.execution_allowed,
        action=decision.action,
    )


@dataclass(frozen=True, slots=True)
class _StaticDiscoverySource:
    observations: tuple[Observation, ...]

    def discover(self) -> tuple[Observation, ...]:
        return self.observations


def _validate(hypothesis: Hypothesis) -> Hypothesis:
    decision = validate_hypothesis(hypothesis, assurance=Assurance.LOW)
    if decision.status != "validated":
        raise RuntimeError(f"mock hypothesis was not validated: {decision.reason}")
    return replace(hypothesis, status="validated")


def _ensure_shadow_only(decision: ShadowDecision) -> None:
    if decision.execution_allowed:
        raise RuntimeError("shadow demo must never grant execution authority")


if __name__ == "__main__":
    report = run_mock_erpnext_shadow_demo()
    print(json.dumps(asdict(report), indent=2))
