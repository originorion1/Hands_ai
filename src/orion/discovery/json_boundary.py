"""Reject ambiguous JSON objects before evidence projection."""


def unique_json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('JSON object keys must be unique')
        result[name] = value
    return result
