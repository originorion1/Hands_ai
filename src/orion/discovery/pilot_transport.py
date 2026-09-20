"""Permit-gated dispatch to an explicitly supplied trusted HTTP transport.

Trusted adapters bind their validated wire encoding to a permit. Legacy readers
have no default egress. Explicit injected transports remain trusted code.
"""
from urllib.parse import urlsplit
from urllib.request import Request

from .pilot_read import PilotReadPermit


def open_pilot_read(request, *, permit, timeout, opener=None):
    if opener is None or not callable(opener):
        raise ValueError('reviewed pilot transport must be explicitly supplied')
    if type(permit) is not PilotReadPermit:
        raise TypeError('launcher-issued pilot permit required')
    source = permit.check_wire(request)
    target = urlsplit(request.full_url)
    if (request.get_method() != 'GET' or request.data is not None
            or target.scheme != 'https' or target.fragment
            or target.username is not None or target.password is not None
            or f'{target.scheme}://{target.netloc}' != source):
        raise ValueError('invalid read-only HTTP target')
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError('bounded transport timeout required')
    # Copy after checking: callers cannot mutate the bound Request during opening.
    outbound = Request(request.full_url, headers=dict(request.header_items()), method='GET')
    permit.claim_io(source)
    permit.check_wire(outbound)
    return opener(outbound, timeout=timeout)
