"""Reviewed semantic rules, not customer mappings or executable expressions.

A role names a falsifiable measurement/process contract. It is not inferred from
identifier spelling. Rules are trusted policy; external text cannot define them.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticRule:
    role: str
    kind: str
    # Each requirement is (evidence class, process channel, dimension).
    required: tuple[tuple[str, str, str], ...]
    minimum_independent_sources: int = 2
    minimum_subjects: int = 2
    invalidation: str = 'any_counterexample'
    unknown: str = 'missing_grounding_or_competing_match'

    def __post_init__(self):
        if (type(self.role) is not str or not self.role or len(self.role) > 100
                or self.kind not in ('number', 'date', 'reference')
                or type(self.required) is not tuple or not 2 <= len(self.required) <= 8
                or len(set(self.required)) != len(self.required)
                or type(self.minimum_independent_sources) is not int
                or not 2 <= self.minimum_independent_sources <= 8
                or type(self.minimum_subjects) is not int or not 2 <= self.minimum_subjects <= 25
                or self.invalidation != 'any_counterexample'
                or self.unknown != 'missing_grounding_or_competing_match'):
            raise ValueError('bounded independent semantic rule required')
        for item in self.required:
            if (type(item) is not tuple or len(item) != 3
                    or any(type(s) is not str or not s or len(s) > 100 for s in item)
                    or item[0] not in ('process', 'temporal', 'relationship', 'aggregate',
                                       'organizational')):
                raise ValueError('independent process evidence class required')


# A small measurement vocabulary. No customer resource/field binding is present.
# Currency grounding establishes a monetary measure, NOT revenue or accounting treatment.
RULES = (
    SemanticRule('monetary_measure', 'number', (
        ('process', 'settled_transfer', 'currency'),
        ('aggregate', 'reconciled_transfer', 'currency'))),
    SemanticRule('physical_measure', 'number', (
        ('process', 'material_transfer', 'mass'),
        ('aggregate', 'reconciled_material', 'mass'))),
    SemanticRule('completion_date', 'date', (
        ('process', 'terminal_transition', 'calendar'),
        ('temporal', 'terminal_transition', 'calendar'))),
    SemanticRule('recording_date', 'date', (
        ('process', 'ingestion_transition', 'calendar'),
        ('temporal', 'ingestion_transition', 'calendar'))),
    SemanticRule('recipient_reference', 'reference', (
        ('process', 'outbound_participation', 'identity'),
        ('relationship', 'outbound_participation', 'identity'))),
    SemanticRule('originator_reference', 'reference', (
        ('process', 'inbound_participation', 'identity'),
        ('relationship', 'inbound_participation', 'identity'))),
)
