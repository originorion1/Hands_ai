"""Customer-scoped deterministic learning projections."""

from .customer_patterns import (
    CustomerPatternSnapshot,
    SampleSufficiency,
    format_safe_pattern_summary,
    project_customer_patterns,
)

__all__ = [
    "CustomerPatternSnapshot",
    "SampleSufficiency",
    "format_safe_pattern_summary",
    "project_customer_patterns",
]
