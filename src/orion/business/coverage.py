"""Corroborated extract manifests and UTC count timing, never absence-of-data proof.

Witnesses enter through the existing independent-instrument admission archive.
A matching digest attests a declared extract, not the truth/completeness of its
upstream system. No request, grant or authority is constructed here.
"""
import json
from datetime import UTC, datetime
from hashlib import sha256


def manifest_digest(study, resource):
    """Bind exact admitted content and technical read window; replay is deduplicated."""
    snapshots = [(o, req) for o, req in study.base.evidence_snapshot() if req.resource == resource]
    if not snapshots:
        raise ValueError('no admitted resource records')
    windows = {(r.start.isoformat(), r.end.isoformat(), r.date_field, tuple(sorted(r.fields)))
               for _, r in snapshots}
    if len(windows) != 1:
        raise ValueError('ambiguous extract window')
    records = {}
    for obs, _ in snapshots:
        p = obs.evidence.payload
        key = p['provenance']['source_record_id']
        content = dict(p['record'])
        if key in records and records[key] != content:
            raise ValueError('conflicting extract identity')
        records[key] = content
    start, end, field, fields = next(iter(windows))
    manifest = {'version': 1, 'tenant': study.base.tenant, 'company': study.base.company,
                'source': study.base.source, 'resource': resource,
                'start': start, 'end': end, 'date_field': field, 'fields': list(fields),
                'records': sorted(records.items())}
    value = sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                              allow_nan=False).encode()).hexdigest()
    return value, (start, end, field), len(records)


def review_coverage(study, normalized, review):
    """Compose within assessment after semantic admission/revision validation."""
    observations = study.evidence_snapshot()
    origins = dict(review.evidence_origins)
    replaced = {(o.evidence.payload['provenance']['source_id'],
                 o.evidence.payload['record']['replaces']) for o in observations
                if o.evidence.payload['record']['replaces']}
    active = [o for o in observations if (o.evidence.payload['provenance']['source_id'],
              o.evidence.payload['record']['id']) not in replaced]

    def corroborate(resource, subject, channel, dimension, classes, expected=None, day=None):
        anchors = [o for o in active if
            o.evidence.payload['record']['subject_resource'] == resource
            and o.evidence.payload['record']['subject_id'] == subject
            and o.evidence.payload['record']['channel'] == channel
            and o.evidence.payload['record']['dimension'] == dimension
            and o.evidence.payload['record']['evidence_class'] in classes]
        values, invalid = set(), False
        for obs in anchors:
            value = obs.evidence.payload['record']['value']
            if type(value) is not str or len(value) > 128:
                invalid = True
                continue
            values.add(value)
            if day is not None:
                try:
                    instant = datetime.fromisoformat(value)
                    if (instant.tzinfo is None or instant.utcoffset() != UTC.utcoffset(instant)
                            or instant.isoformat() != value or instant.date().isoformat() != day
                            or instant > obs.evidence.observed_at):
                        invalid = True
                except ValueError:
                    invalid = True
            elif value != expected:
                invalid = True
        independent = any(
            a.evidence.payload['record']['evidence_class'] != b.evidence.payload['record']['evidence_class']
            and {r[0] for r in origins[a.evidence.evidence_id]}.isdisjoint(
                {r[0] for r in origins[b.evidence.evidence_id]})
            for a in anchors for b in anchors)
        status = ('CONTRADICTED' if invalid or len(values) > 1 else
                  'CORROBORATED' if len(values) == 1 and independent else 'UNKNOWN')
        return {'status': status, 'value': next(iter(values)) if status == 'CORROBORATED' else None,
                'evidence_ids': sorted(str(o.evidence.evidence_id) for o in anchors),
                'independent': independent, 'execution_allowed': False}

    coverage = []
    for resource in sorted({r['resource'] for r in normalized}):
        try:
            digest, window, count = manifest_digest(study, resource)
        except ValueError:
            coverage.append({'resource': resource, 'status': 'UNKNOWN',
                             'reason': 'ambiguous_or_missing_extract', 'execution_allowed': False})
            continue
        result = corroborate(resource, '*', 'ledger_manifest_closed', 'sha256',
                             ('process', 'organizational'), expected=digest)
        coverage.append({**result, 'resource': resource, 'manifest_sha256': digest,
                         'technical_window': list(window), 'record_count': count,
                         'meaning': 'declared extract only; upstream completeness unproven'})
    timing = []
    for row in normalized:
        if not {'counted_stock', 'business_event_date'} <= set(row['cells']):
            continue
        result = corroborate(row['resource'], row['identity'], 'physical_count_time', 'UTC',
                             ('process', 'temporal'),
                             day=row['cells']['business_event_date']['value'])
        timing.append({**result, 'resource': row['resource'], 'identity': row['identity']})
    return {'version': 1, 'manifests': coverage, 'count_timing': timing,
            'upstream_completeness': 'NOT_PROVEN', 'loss_conclusion_allowed': False,
            'execution_allowed': False}
