"""Bounded durable transport-attempt journal; never an authorization issuer.

A separate trusted owner must retain the HMAC key and latest head. This module
cannot isolate those from malicious same-process Python. Live readiness therefore
requires an external custody/isolation boundary in addition to these controls.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import stat
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..discovery.json_boundary import unique_json_object
from ..discovery.read_window import ReviewedReadWindow


class JournalDenied(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TransportLimits:
    max_requests: int
    request_bytes: int
    response_bytes: int
    total_response_bytes: int
    minimum_interval_seconds: int
    failures: int
    expires_at: datetime

    def __post_init__(self):
        bounds = {'max_requests':(1,100),'request_bytes':(1,65536),
                  'response_bytes':(1,2097152),'total_response_bytes':(1,20971520),
                  'minimum_interval_seconds':(1,3600),'failures':(1,10)}
        for key,(low,high) in bounds.items():
            value=getattr(self,key)
            if type(value) is not int or not low <= value <= high:
                raise JournalDenied('invalid transport limits')
        if self.total_response_bytes < self.response_bytes:
            raise JournalDenied('response reservation exceeds budget')
        ReviewedReadWindow.check_time_type(self.expires_at)


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def grant_digest(grant):
    return hashlib.sha256(json.dumps(asdict(grant),sort_keys=True,
                                    default=lambda value:value.isoformat()).encode()).hexdigest()


class AttemptJournal:
    """Single-writer ledger with durable reservations and externally pinned tip."""

    def __init__(self,path,*,key,binding,limits,expected_head=None):
        if type(key) is not bytes or len(key)<32:
            raise JournalDenied('independent audit key required')
        if type(binding) is not str or len(binding)!=64:
            raise JournalDenied('grant digest required')
        if type(limits) is not TransportLimits:
            raise JournalDenied('explicit limits required')
        limits.__post_init__()
        self._path=Path(path)
        self._key=key
        self._binding=binding
        self._limits=limits
        self._active=False
        parent=self._path.parent.stat()
        if parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)&0o077:
            raise JournalDenied('private journal directory required')
        new=not self._path.exists()
        if new:
            if expected_head is not None:
                raise JournalDenied('journal missing at trusted checkpoint')
            fd=os.open(self._path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            os.close(fd)
        info=self._path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)&0o077 or info.st_nlink!=1):
            raise JournalDenied('private regular journal required')
        self._head='0'*64 if new else expected_head
        if not isinstance(self._head,str) or len(self._head)!=64:
            raise JournalDenied('independently retained journal head required')
        with self._connect() as db:
            if new:
                db.execute('CREATE TABLE events (sequence INTEGER PRIMARY KEY, body TEXT NOT NULL, mac TEXT NOT NULL)')
                body={'sequence':1,'event':'configure','binding':binding,
                      'limits':asdict(limits)|{'expires_at':limits.expires_at.isoformat()},'previous':self._head}
                self._append(db,body)
            else:
                events=self._verify(db)
                expected=asdict(limits)|{'expires_at':limits.expires_at.isoformat()}
                if events[0]['binding']!=binding or events[0]['limits']!=expected:
                    raise JournalDenied('journal configuration changed')

    @property
    def binding(self):
        return self._binding

    @property
    def limits(self):
        return self._limits

    def _connect(self):
        db=sqlite3.connect(self._path,timeout=1,isolation_level='IMMEDIATE')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _append(self,db,body):
        encoded=_json(body)
        mac=hmac.new(self._key,encoded.encode(),hashlib.sha256).hexdigest()
        db.execute('INSERT INTO events VALUES (?,?,?)',(body['sequence'],encoded,mac))
        self._head=mac

    def _verify(self,db):
        rows=db.execute('SELECT sequence,body,mac FROM events ORDER BY sequence LIMIT 205').fetchall()
        if not rows or len(rows)>202:
            raise JournalDenied('audit length invalid')
        previous='0'*64
        events=[]
        for sequence,encoded,mac in rows:
            if len(encoded)>2048:
                raise JournalDenied('audit entry oversized')
            body=json.loads(encoded,object_pairs_hook=unique_json_object)
            expected=hmac.new(self._key,encoded.encode(),hashlib.sha256).hexdigest()
            if (type(mac) is not str or not hmac.compare_digest(mac,expected) or body.get('previous')!=previous
                    or sequence!=len(events)+1 or body.get('sequence')!=sequence
                    or body.get('binding')!=self.binding):
                raise JournalDenied('audit integrity mismatch')
            previous=mac
            events.append(body)
        if not hmac.compare_digest(previous,self._head):
            raise JournalDenied('audit rollback or concurrent writer detected')
        return events

    @property
    def head(self):
        return self._head

    def inspect(self):
        with self._connect() as db:
            events=self._verify(db)
        attempts=sum(e['event']=='attempt' for e in events)
        failures=sum(e['event']=='failure' for e in events)
        return {'attempts':attempts,'failures':failures,
                'reserved_bytes':attempts*self.limits.response_bytes,
                'stopped':any(e['event']=='stop' for e in events),
                'pending':events[-1]['event']=='attempt','head':self.head}

    def lifecycle(self, event, *, at, references):
        """Bounded digest-only broker events; no records, paths or secret values.

        This shares the existing integrity chain and its fixed length bound.
        It cannot hide an unfinished attempt or grant/revive authority.
        """
        allowed = {'broker_start', 'broker_arm', 'broker_request', 'broker_denied', 'broker_admitted',
                   'broker_stop', 'broker_revoke', 'broker_shutdown', 'broker_failed'}
        ReviewedReadWindow.check_time_type(at)
        if (event not in allowed or type(references) is not dict or len(references) > 8
                or not set(references) <= {'caller', 'scope', 'request', 'observations', 'version', 'reason'}
                or any(type(v) is not str or len(v) != 64 or
                       any(c not in '0123456789abcdef' for c in v) for v in references.values())):
            raise JournalDenied('invalid lifecycle event')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events = self._verify(db)
            if len(events) >= 201 or events[-1]['event'] == 'attempt':
                raise JournalDenied('pending or exhausted audit')
            self._append(db, {'sequence': len(events) + 1, 'event': event,
                'binding': self.binding, 'previous': self.head, 'at': at.isoformat(),
                'references': references, 'execution_allowed': False,
                'execution_status': 'not_attempted'})

    def lifecycle_records(self):
        """Detached verified values, not mutable journal authority."""
        with self._connect() as db:
            return tuple(self._verify(db))

    def begin(self,now,request_bytes):
        ReviewedReadWindow.check_time_type(now)
        if type(request_bytes) is not int or not 0<=request_bytes<=self.limits.request_bytes:
            raise JournalDenied('request byte budget exceeded')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events=self._verify(db)
            attempts=[e for e in events if e['event']=='attempt']
            failures=sum(e['event']=='failure' for e in events)
            if (events[-1]['event']=='attempt' or any(e['event']=='stop' for e in events)
                    or now>=self.limits.expires_at or failures>=self.limits.failures
                    or len(attempts)>=self.limits.max_requests
                    or (len(attempts)+1)*self.limits.response_bytes>self.limits.total_response_bytes):
                raise JournalDenied('stopped expired pending circuit or exhausted budget')
            if attempts and now.timestamp()-attempts[-1]['at']<self.limits.minimum_interval_seconds:
                raise JournalDenied('rate limit or clock rollback')
            body={'sequence':len(events)+1,'event':'attempt','binding':self.binding,
                  'previous':self.head,'at':now.timestamp(),'request_bytes':request_bytes}
            self._append(db,body)
        self._active=True

    def check_active(self,now):
        ReviewedReadWindow.check_time_type(now)
        state=self.inspect()
        if not self._active or state['stopped'] or not state['pending'] or now>=self.limits.expires_at:
            raise JournalDenied('attempt no longer admissible')

    def finish(self,*,success,received_bytes):
        if type(success) is not bool or type(received_bytes) is not int or received_bytes<0:
            raise JournalDenied('invalid result')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events=self._verify(db)
            if not self._active or events[-1]['event']!='attempt':
                raise JournalDenied('no active attempt')
            body={'sequence':len(events)+1,'event':'success' if success else 'failure',
                  'binding':self.binding,'previous':self.head,'received_bytes':received_bytes}
            self._append(db,body)
        self._active=False

    def stop(self):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            events=self._verify(db)
            if any(e['event']=='stop' for e in events):
                return
            self._append(db,{'sequence':len(events)+1,'event':'stop',
                             'binding':self.binding,'previous':self.head})
        self._active=False
