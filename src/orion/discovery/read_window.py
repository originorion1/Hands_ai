"""Additional restrictions on an already authorized read, never a permission grant."""
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class ReviewedReadWindow:
    """Exact allowlist reviewed outside this value for excluded data semantics.

    Field names cannot prove absence of payroll or other sensitive content.
    Existing authorization must also permit the read; this value only narrows it.
    Date fields must be source Date fields, not timestamps or inferred dates.
    """
    tenant_id: str
    company: str
    resource: str
    fields: tuple[str, ...]
    date_field: str
    start: date
    end: date
    expires_at: datetime

    def __post_init__(self):
        if not isinstance(self.fields, tuple) or not self.fields:
            raise ValueError('exact immutable field allowlist required')
        for value in (self.tenant_id, self.company, self.resource, self.date_field, *self.fields):
            if not isinstance(value, str) or not value.strip() or '*' in value:
                raise ValueError('scope must contain exact non-empty identities')
        if len(set(self.fields)) != len(self.fields) or self.date_field not in self.fields:
            raise ValueError('unique fields including the date field required')
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValueError('valid inclusive date window required')
        self.check_time_type(self.expires_at)

    @staticmethod
    def check_time_type(value):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('timezone-aware clock and expiry required')

    def check(self, tenant, company, resource, fields, now):
        self.check_time_type(now)
        if now >= self.expires_at:
            raise ValueError('read window expired')
        if (tenant, company, resource, fields) != (
            self.tenant_id, self.company, self.resource, self.fields
        ):
            raise ValueError('read exceeds reviewed scope')

    def filters(self):
        return [[self.date_field, '>=', self.start.isoformat()],
                [self.date_field, '<=', self.end.isoformat()]]

    def validate_row(self, row):
        value = row.get(self.date_field)
        if not isinstance(value, str):
            raise TypeError('missing or invalid source date')
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError('invalid source date') from None
        if parsed.isoformat() != value or not self.start <= parsed <= self.end:
            raise ValueError('row outside reviewed date window')
