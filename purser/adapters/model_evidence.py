"""Offline interpretation of original bounded model request, response and usage evidence."""

import base64
from datetime import datetime
import re

from experiments.e1.artifacts import encoded, sha
from experiments.model_budget import LIMITS, MAX_SQL_INT, ModelTurn

from .model_http import LocalEndpoint, MAX_RESPONSE_BYTES
from .model_protocol import ActionSpec, ModelPrompt, OperandSpec, strict_json


TRANSPORT_OUTCOMES = {'response', 'deadline', 'transport_unavailable', 'invalid_transport', 'http_status',
                      'invalid_content_type', 'unsupported_encoding'}
MAX_RECORD_BYTES = 768 * 1024


def request_identity(service, request):
    return sha(encoded({'service': service, 'body': request}))


def _proposal_from_response(obj, model, prompt):
    if not isinstance(obj, dict) or obj.get('model') != model:
        return 'unexpected_model', None
    choices = obj.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return 'invalid_response', None
    choice = choices[0]
    if type(choice.get('index')) is not int or choice['index'] != 0:
        return 'invalid_response', None
    if choice.get('finish_reason') != 'stop':
        return 'unfinished_response', None
    message = choice.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant' or not isinstance(message.get('content'), str):
        return 'invalid_response', None
    if message.get('tool_calls') or message.get('function_call') or message.get('refusal'):
        return 'unusable_response', None
    try:
        return 'decision', prompt.proposal(message['content'])
    except (ValueError, TypeError, RecursionError):
        return 'invalid_proposal', None


def interpret(endpoint, prompt, output_limit, status, http_status, raw):
    usage = {'input_tokens': None, 'output_tokens': None,
             'spend_usd_micros': 0 if http_status is not None else None}
    if status != 'response':
        return status, None, usage
    try:
        obj = strict_json(raw)
    except (ValueError, TypeError, RecursionError):
        return 'invalid_response', None, usage
    if not isinstance(obj, dict):
        return 'invalid_response', None, usage
    report = obj.get('usage')
    if report is not None:
        if not isinstance(report, dict):
            return 'invalid_usage', None, usage
        invalid_usage = False
        for source, target in (('prompt_tokens', 'input_tokens'), ('completion_tokens', 'output_tokens')):
            value = report.get(source)
            if value is None:
                continue
            if type(value) is int and 0 <= value <= MAX_SQL_INT:
                usage[target] = value
            else:
                invalid_usage = True
        if ((usage['input_tokens'] or 0) > endpoint.context_window
                or (usage['output_tokens'] or 0) > output_limit):
            return 'usage_overrun', None, usage
        if invalid_usage:
            return 'invalid_usage', None, usage
    outcome, proposal = _proposal_from_response(obj, endpoint.model, prompt)
    return outcome, proposal, usage


def _prompt_from_request(request, model, output_limit):
    participant = strict_json(request['messages'][1]['content'])
    actions = {}
    for name, action in participant['actions'].items():
        fields = {}
        for key, operand in action['arguments'].items():
            if 'choices' in operand:
                fields[key] = OperandSpec(choices=operand['choices'])
            else:
                fields[key] = OperandSpec(max_bytes=operand['max_utf8_bytes'])
        actions[name] = ActionSpec(action['description'], fields)
    prompt = ModelPrompt(participant['task'], participant['observation'], actions)
    if encoded(prompt.request(model, output_limit)) != encoded(request):
        raise ValueError('original request is outside the enrolled protocol')
    return prompt


def _verify(record):
    service = record['service']
    endpoint = LocalEndpoint(service['port'], service['model'], service['runtime_sha256'], service['context_window'])
    if encoded(endpoint.profile()) != encoded(service) or not re.fullmatch('[a-f0-9]{64}', record['authority_sha256']):
        raise ValueError('invalid service or authority identity')
    t = record['turn']
    identity = ModelTurn(t['registration_sha256'], tuple(t['unit']), t['ordinal'], record['request_sha256'])
    identity.key()
    request = record['request']
    if (request_identity(service, request) != identity.request_sha256
            or sha(encoded(request)) != record['body_sha256']):
        raise ValueError('original prepared request differs')
    output_limit = record['limits']['output_tokens']
    if (type(output_limit) is not int or not 1 <= output_limit <= LIMITS['max_output_tokens']
            or encoded(record['limits']['input_tokens']) != encoded(endpoint.context_window)):
        raise ValueError('invalid original token limits')
    prompt = _prompt_from_request(request, endpoint.model, output_limit)
    transport = record['transport']
    status, http = transport['status'], transport['http_status']
    state = transport['wire_state']
    if transport['released'] is not True or state not in ('not_sent', 'send_started', 'sent') or status not in TRANSPORT_OUTCOMES:
        raise ValueError('invalid transport progress')
    if http is not None and (state != 'sent' or type(http) is not int or not 100 <= http <= 999):
        raise ValueError('invalid HTTP evidence')
    raw = None
    if status == 'response':
        raw = base64.b64decode(transport['response_base64'], validate=True)
        if http != 200 or len(raw) > MAX_RESPONSE_BYTES or sha(raw) != transport['response_sha256']:
            raise ValueError('invalid original response bytes')
    elif transport['response_sha256'] is not None or transport['response_base64'] is not None:
        raise ValueError('unexpected response body')
    if status == 'http_status' and (http is None or http == 200):
        raise ValueError('invalid HTTP outcome')
    if status in ('invalid_content_type', 'unsupported_encoding') and http != 200:
        raise ValueError('invalid content outcome')
    expected = interpret(endpoint, prompt, output_limit, status, http, raw)
    if encoded(expected) != encoded([record['outcome'], record['proposal'], record['usage']]):
        raise ValueError('observed reply does not support recorded proposal or usage')
    start, end = record['started_monotonic_ns'], record['returned_monotonic_ns']
    if type(start) is not int or type(end) is not int or not 0 <= start <= end:
        raise ValueError('invalid original monotonic interval')
    for field in ('started_utc', 'returned_utc'):
        if datetime.fromisoformat(record[field]).utcoffset() is None:
            raise ValueError('invalid original timestamp')


def verify_model_record(raw: bytes, *, expected_sha256: str) -> dict:
    """Verify an archived result offline, including the exact request and typed original response."""
    if type(raw) is not bytes or len(raw) > MAX_RECORD_BYTES or sha(raw) != expected_sha256:
        raise ValueError('model artifact digest or size differs')
    try:
        record = strict_json(raw)
        if type(record['v']) is not int or record['v'] != 1 or encoded(record) != raw:
            raise ValueError('invalid model artifact encoding or version')
        _verify(record)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, OverflowError) as exc:
        raise ValueError('invalid model artifact') from exc
    return record
