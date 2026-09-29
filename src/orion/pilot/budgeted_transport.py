"""Additional durable limits around the existing governed transport.

Explicit opener injection remains trusted. This module does not close the OS
isolation gap and is not a production networking implementation.
"""
from ..discovery.pilot_transport import open_pilot_read
from .journal import JournalDenied, grant_digest


def open_budgeted_read(request,*,permit,timeout,opener,journal):
    source=permit.check_wire(request)
    _,grant=permit.check(source)
    if grant_digest(grant)!=journal.binding:
        raise JournalDenied('journal authorization binding mismatch')
    size=len(request.full_url.encode())+sum(len(k.encode())+len(v.encode())
                                            for k,v in request.header_items())
    journal.begin(permit.current_time(),size)
    try:
        response=open_pilot_read(request,permit=permit,timeout=timeout,opener=opener)
    except BaseException:  # noqa: BLE001 - reserve failures even on interruption; redact wire details
        journal.finish(success=False,received_bytes=0)
        raise JournalDenied('transport attempt failed') from None
    return _Response(response,permit,source,journal)


class _Response:
    def __init__(self,response,permit,source,journal):
        self._response,self._permit,self._source,self._journal=response,permit,source,journal
        self._bytes=0

    def __enter__(self):
        try:
            self._response.__enter__()
            return self
        except BaseException:  # noqa: BLE001 - reserve failures even on interruption; redact wire details
            self._journal.finish(success=False,received_bytes=0)
            raise JournalDenied('response entry failed') from None

    def geturl(self):
        return self._response.geturl()

    @property
    def status(self):
        return self._response.status

    @property
    def headers(self):
        return self._response.headers

    def read(self,size=-1):
        self._journal.check_active(self._permit.current_time())
        self._permit.check(self._source)
        remaining=self._journal.limits.response_bytes-self._bytes
        if type(size) is not int or size < -1:
            raise JournalDenied('invalid response read')
        requested=remaining+1 if size==-1 else min(size,remaining+1)
        try:
            value=self._response.read(requested)
        except Exception:  # noqa: BLE001 - never expose upstream exception text
            raise JournalDenied('response read failed') from None
        if type(value) is not bytes:
            raise JournalDenied('invalid response type')
        self._bytes+=len(value)
        if self._bytes>self._journal.limits.response_bytes:
            raise JournalDenied('response byte budget exceeded')
        return value

    def __exit__(self,kind,value,traceback):
        success=False
        try:
            self._response.__exit__(kind,value,traceback)
            self._permit.check(self._source)
            self._journal.check_active(self._permit.current_time())
            success=kind is None
        finally:
            self._journal.finish(success=success,received_bytes=self._bytes)
        return False
