"""Ambiguous wire JSON must not become pilot evidence or proposals."""
import json
from urllib.parse import urlsplit

import pytest
from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_metadata import launch as metadata_launch
from test_pilot_metadata import reader as metadata_reader
from test_pilot_metadata import schema
from test_pilot_read import launch, reader, row


@pytest.mark.parametrize('path', ['record', 'schema'])
def test_duplicate_json_keys_rejected_before_admission(path):
    calls = []
    def transport(req, timeout):
        calls.append(req)
        response = FakeResponse({}, url=req.full_url)
        if path == 'record':
            raw = json.dumps({'data': [row()]}).replace(
                '"company": "Example"', '"company": "Other", "company": "Example"')
        elif urlsplit(req.full_url).path == '/api/resource/DocType':
            raw = json.dumps({'data': [{'name': 'Entry'}]})
        else:
            raw = json.dumps(schema()).replace('"fieldtype": "Currency"',
                '"fieldtype": "Password", "fieldtype": "Currency"')
        response._payload = raw.encode()
        return response
    adapter = reader([]) if path == 'record' else metadata_reader([])
    adapter._opener = transport
    with pytest.raises(ValueError):
        (launch if path == 'record' else metadata_launch)(adapter)
    assert len(calls) == (1 if path == 'record' else 2)
