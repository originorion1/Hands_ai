"""Bounded names-only continuation; retained catalog is not approved study scope."""
import json
from contextlib import contextmanager
from datetime import datetime

from ..contracts import utc_now
from . import erpnext_metadata_preflight as p
from .erpnext_adapter import _default_opener


@contextmanager
def transaction(ledger):
    connection = ledger._connect()
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def continue_catalog(config, *, environment, expires_at, max_pages=4,
                     clock=utc_now, opener=None):
    """Resume only failed catalog discovery. Never read transaction records.

    Existing local ledger/config/clock are trusted. Reservation survives crashes;
    ambiguous in-flight state requires investigation rather than budget reset.
    """
    if type(max_pages) is not int or not 1 <= max_pages <= 4:
        raise ValueError('page budget must be between one and four')
    if not isinstance(expires_at, datetime) or expires_at.utcoffset() is None:
        raise ValueError('timezone-aware expiry required')

    def check_expiry():
        now = clock()
        if not isinstance(now, datetime) or now.utcoffset() is None or now >= expires_at:
            raise ValueError('catalog authority expired or clock invalid')

    check_expiry()
    ledger = p._existing_private_retry_ledger(config)
    credentials = config.credential_references.resolve(environment)
    transport = p._AuthenticatedOpener(opener or _default_opener,
                                       credentials.api_key, credentials.api_secret)
    binding = p._admin_binding_digest(config)
    expiry = expires_at.isoformat()
    with transaction(ledger) as c:
        present = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='automatic_catalog'").fetchone()
        if not present:
            row = c.execute('SELECT status, attempted_gets, company_catalog_complete, '
                            'doctype_catalog_count, metadata_succeeded, metadata_failed '
                            'FROM preflight_run WHERE singleton=1').fetchone()
            if row != ('failed', 4, 1, 0, 0, 0):
                raise ValueError('catalog continuation requires failed attempt-four state')
            c.execute('CREATE TABLE automatic_catalog (singleton INTEGER PRIMARY KEY '
                      'CHECK(singleton=1), binding TEXT NOT NULL, expiry TEXT NOT NULL, '
                      'names TEXT NOT NULL, complete INTEGER NOT NULL, inflight INTEGER NOT NULL)')
            c.execute('INSERT INTO automatic_catalog VALUES (1, ?, ?, ?, 0, 0)',
                      (binding, expiry, '[]'))
    pages = 0
    for _ in range(max_pages):
        check_expiry()
        with transaction(ledger) as c:
            stored = c.execute('SELECT binding, expiry, names, complete, inflight '
                               'FROM automatic_catalog WHERE singleton=1').fetchone()
            if stored is None or stored[:2] != (binding, expiry):
                raise ValueError('catalog scope binding mismatch')
            if stored[4]:
                raise ValueError('ambiguous in-flight catalog request; inspection required')
            names = json.loads(stored[2])
            if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
                raise ValueError('invalid retained catalog')
            if names != sorted(set(names)):
                raise ValueError('invalid retained catalog order')
            complete = bool(stored[3])
            competing = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                  "AND name='admin_catalog_claim'").fetchone()
            if competing:
                raise ValueError('another continuation owns this ledger')
            state, attempts = c.execute('SELECT status, attempted_gets FROM preflight_run '
                                        'WHERE singleton=1').fetchone()
            if state != 'failed' or not 4 <= attempts <= p.MAX_TOTAL_ATTEMPTED_GETS:
                raise ValueError('preflight state changed')
            if complete or attempts == p.MAX_TOTAL_ATTEMPTED_GETS:
                break
            c.execute('UPDATE preflight_run SET attempted_gets=attempted_gets+1 WHERE singleton=1')
            c.execute('UPDATE automatic_catalog SET inflight=1 WHERE singleton=1')
        # The claim is durable before any transport, including transport failures.
        rows, complete = p._read_name_catalog(config, resource='DocType', requested=99,
                                             opener=transport,
                                             after_name=names[-1] if names else None)
        check_expiry()
        with transaction(ledger) as c:
            claim = c.execute('SELECT binding, expiry, names, inflight FROM automatic_catalog '
                              'WHERE singleton=1').fetchone()
            if claim != (binding, expiry, stored[2], 1):
                raise ValueError('catalog claim changed')
            names.extend(rows)
            c.execute('UPDATE automatic_catalog SET names=?, complete=?, inflight=0 '
                      'WHERE singleton=1', (json.dumps(names), int(complete)))
        pages += 1
    return {'status': 'complete' if complete else 'bounded_partial',
            'catalog_name_count': len(names), 'catalog_complete': complete,
            'pages_read': pages, 'attempted_gets': ledger.snapshot()['attempted_gets'],
            'record_samples': 0, 'erp_writes': 0, 'execution_allowed': False,
            'candidate_review_required': True}


def main():
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--expires-at', required=True)
    args = parser.parse_args()
    if not args.execute:
        print('{"status":"execution_not_requested","execution_allowed":false}')
        return 0
    try:
        config = p.metadata_preflight_config_from_environment(os.environ)
        report = continue_catalog(config, environment=os.environ,
                                  expires_at=datetime.fromisoformat(args.expires_at))
    except Exception:  # noqa: BLE001 - never expose private transport/configuration details
        print('{"status":"catalog_continuation_stopped","execution_allowed":false}')
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
