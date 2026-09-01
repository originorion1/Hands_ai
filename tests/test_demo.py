from orion.demo import run_mock_erpnext_shadow_demo


def test_mock_erpnext_demo_preserves_shadow_and_provenance_boundaries() -> None:
    report = run_mock_erpnext_shadow_demo(tenant_id="customer-a")

    assert report.tenant_id == "customer-a"
    assert report.observations == report.evidence == report.graph_nodes == 2
    assert report.knowledge_entries == 1
    assert report.provenance_evidence_ids == 1
    assert report.execution_allowed is False
    assert report.action == "prepare Purchase Invoice draft for human review"
