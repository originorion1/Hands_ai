import inspect
from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.learning.shadow_backtest import format_safe_shadow_summary, run_shadow_backtest

TENANT = "synthetic-shadow-tenant"
RESOURCE = "Purchase Invoice"


def obs(name, supplier, posting, currency="USD", due=None, *, tenant=TENANT, resource=RESOURCE, mode=ObservationMode.READ_ONLY, kind=EvidenceKind.API, payload_extra=None):
    record = {"name": name, "supplier": supplier, "posting_date": posting, "currency": currency, "due_date": due or posting}
    if payload_extra:
        record.update(payload_extra)
    return Observation(Evidence(kind=kind, source="synthetic-shadow", tenant_id=tenant, observed_at=datetime(2026, 1, 1, tzinfo=UTC), payload={"resource": resource, "record": record}), mode=mode)


def history(rows, *, batches=(None,)):
    if batches == (None,):
        batches = (rows,)
    return tuple(HistoricalEvidenceBatch(TENANT, RESOURCE, seq, datetime(2026, 1, 1, tzinfo=UTC), tuple(group)) for seq, group in enumerate(batches, 1))


def clean_rows():
    return [
        obs("INV-1", "Supplier A", "2026-01-01", "USD", "2026-01-11"),
        obs("INV-2", "Supplier A", "2026-01-02", "USD", "2026-01-12"),
        obs("INV-3", "Supplier A", "2026-01-03", "USD", "2026-01-18"),
        obs("INV-4", "Supplier B", "2026-01-01", "EUR", "2026-01-06"),
        obs("INV-5", "Supplier B", "2026-01-01", "EUR", "2026-01-06"),
        obs("INV-6", "Supplier B", "2026-01-04", "EUR", "2026-01-10"),
    ]


def test_clean_chronological_backtest_and_metrics():
    result = run_shadow_backtest(history(clean_rows()), tenant_id=TENANT, resource=RESOURCE)
    assert result.observation_count == 6 and result.chronological_target_count == 6
    assert result.currency_prediction_count == 2 and result.currency_correct_count == 2
    assert result.due_interval_prediction_count == 2
    assert result.due_interval_exact_count == 0


def test_input_order_and_batches_do_not_change_result():
    rows = clean_rows()
    one = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    two = run_shadow_backtest(history([], batches=(rows[:3][::-1], rows[3:][::-1])), tenant_id=TENANT, resource=RESOURCE)
    assert one.observation_count == two.observation_count
    assert one.currency_accuracy == two.currency_accuracy
    assert one.due_interval_absolute_errors == two.due_interval_absolute_errors


def test_later_observation_never_leaks_into_earlier_target():
    rows = [obs("A1", "S", "2026-01-03", "EUR", "2026-01-08"), obs("A2", "S", "2026-01-04", "EUR", "2026-01-09"), obs("A3", "S", "2026-01-02", "USD", "2026-01-07")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert result.currency_prediction_count == 0


def test_same_day_observations_never_train_each_other():
    rows = [obs("A1", "S", "2026-01-01", "USD"), obs("A2", "S", "2026-01-01", "USD"), obs("A3", "S", "2026-01-02", "USD")]
    assert run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE).currency_prediction_count == 1


def test_fewer_than_two_prior_abstains_and_exactly_two_enables():
    rows = [obs("A1", "S", "2026-01-01"), obs("A2", "S", "2026-01-02"), obs("A3", "S", "2026-01-03")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert result.currency_prediction_count == 1 and result.currency_abstention_count == 2


def test_currency_unique_mode_and_tie_abstain():
    rows = [obs("A1", "S", "2026-01-01", "USD"), obs("A2", "S", "2026-01-02", "USD"), obs("A3", "S", "2026-01-03", "EUR"), obs("A4", "S", "2026-01-04", "USD")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert result.currency_prediction_count == 2 and result.currency_correct_count == 1


def test_malformed_currency_is_counted_and_abstains():
    rows = [obs("A1", "S", "2026-01-01", ""), obs("A2", "S", "2026-01-02", "USD"), obs("A3", "S", "2026-01-03", "USD")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert dict(result.invalid_field_counts)["currency"] == 1 and result.currency_prediction_count == 0


def test_due_interval_median_odd_and_even():
    rows = [obs(f"A{i}", "S", f"2026-01-0{i}", due=f"2026-01-{i + interval:02d}") for i, interval in [(1, 1), (2, 5), (3, 9), (4, 11), (5, 15)]]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert result.due_interval_prediction_count == 3 and result.due_interval_absolute_errors[-1] == 8


def test_malformed_dates_and_negative_intervals_are_excluded_per_metric():
    rows = [obs("A1", "S", "bad", "USD", "bad"), obs("A2", "S", "2026-01-01", "USD", "2026-01-02"), obs("A3", "S", "2026-01-02", "USD", "2025-01-01"), obs("A4", "S", "2026-01-03", "USD", "2026-01-04")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    invalid = dict(result.invalid_field_counts)
    assert invalid["posting_date"] == 1 and invalid["due_interval"] == 1


def test_due_before_posting_never_trains():
    rows = [obs("A1", "S", "2026-01-01", due="2025-12-01"), obs("A2", "S", "2026-01-02", due="2026-01-03"), obs("A3", "S", "2026-01-03", due="2026-01-04"), obs("A4", "S", "2026-01-04", due="2026-01-05")]
    assert run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE).due_interval_prediction_count == 1


def test_currency_accuracy_and_due_error_bands():
    rows = [obs("A1", "S", "2026-01-01", "USD", "2026-01-03"), obs("A2", "S", "2026-01-02", "USD", "2026-01-04"), obs("A3", "S", "2026-01-03", "EUR", "2026-01-10")]
    result = run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)
    assert result.currency_accuracy == 0 and result.due_interval_mean_absolute_error == 5
    assert result.due_interval_within_3_days_count == 0 and result.due_interval_within_7_days_count == 1


def test_cross_scope_nonconsecutive_and_duplicates_fail_closed():
    rows = clean_rows()
    with pytest.raises(HistoricalEvidenceError):
        run_shadow_backtest(history([obs("X", "S", "2026-01-01", tenant="other")]), tenant_id=TENANT, resource=RESOURCE)
    bad = (HistoricalEvidenceBatch(TENANT, RESOURCE, 2, datetime(2026, 1, 1, tzinfo=UTC), tuple(rows)),)
    with pytest.raises(HistoricalEvidenceError):
        run_shadow_backtest(bad, tenant_id=TENANT, resource=RESOURCE)
    dup = history(rows, batches=(rows[:3], [rows[2], *rows[3:]]))
    with pytest.raises(HistoricalEvidenceError):
        run_shadow_backtest(dup, tenant_id=TENANT, resource=RESOURCE)


def test_repeated_backtest_is_deterministic():
    rows = clean_rows()
    assert run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE) == run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)


def test_safe_summary_contains_only_aggregates():
    rows = clean_rows()
    safe = repr(format_safe_shadow_summary(run_shadow_backtest(history(rows), tenant_id=TENANT, resource=RESOURCE)))
    for secret in ("Supplier A", "USD", "INV-1", TENANT):
        assert secret not in safe
    assert "execution_allowed': False" in safe


def test_no_erp_http_write_or_promotion_capability_in_learning_module():
    source = inspect.getsource(__import__("orion.learning.shadow_backtest", fromlist=["run_shadow_backtest"])).lower()
    assert "erpnext" not in source and "urllib" not in source and "requests" not in source
    assert "sqlite" not in source and "post(" not in source and "recommendation_allowed': true" not in source
