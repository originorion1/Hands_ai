"""Structural schema hypotheses; no business semantics or read authority."""
import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class FieldDeclaration:
    resource: str
    name: str
    kind: str
    source_type: str

    def __post_init__(self):
        for value in (self.resource, self.name, self.source_type):
            if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
                raise ValueError('bounded explicit schema identity required')
        if self.kind not in {'number', 'date', 'reference'}:
            raise ValueError('unsupported structural kind')


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    declaration: FieldDeclaration
    role: str
    location: tuple[str, str]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class SchemaInterpretation:
    resource: str
    candidates: tuple[FieldCandidate, ...]
    unknowns: tuple[str, ...]


def interpret_schema(resource, declarations):
    """Infer only structural possibilities from declared types, never field names."""
    if not isinstance(resource, str) or not resource or resource != resource.strip() or len(resource) > 256:
        raise ValueError('explicit bounded resource required')
    if type(declarations) is not tuple or len(declarations) > 1000:
        raise ValueError('bounded immutable declarations required')
    candidates = []
    names = set()
    for declaration in declarations:
        if type(declaration) is not FieldDeclaration:
            raise TypeError('explicit field evidence required')
        declaration.__post_init__()
        if declaration.resource != resource or declaration.name in names:
            raise ValueError('ambiguous or cross-resource field evidence')
        names.add(declaration.name)
        digest = hashlib.sha256(json.dumps(asdict(declaration),sort_keys=True).encode()).hexdigest()
        candidates.append(FieldCandidate(declaration, declaration.kind + '_candidate',
            (resource, declaration.name), digest))
    dates = sum(c.declaration.kind == 'date' for c in candidates)
    unknowns = ('business_meaning_unconfirmed', 'record_identity_unconfirmed',
                'tenant_filter_unconfirmed', 'record_authorization_missing')
    unknowns += ('date_role_ambiguous',) if dates > 1 else ('business_date_unconfirmed',)
    return SchemaInterpretation(resource, tuple(candidates), unknowns)
