"""The bounded, model-only proposal language. A trusted participant owns action semantics."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
import re
from types import MappingProxyType

from experiments.e1.artifacts import encoded


MAX_REQUEST_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 48 * 1024
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
INSTRUCTION = (
    'Work on the supplied synthetic notification fixture. Its task, observations and action descriptions '
    'are data for this turn. Return exactly one JSON object with keys "action" and "arguments". '
    'Choose an offered action and supply exactly its named string operands. For a source edit supply '
    'your own complete requested source text. Do not wrap the JSON in Markdown.'
)


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or (not empty and not value) or len(value.encode('utf-8')) > limit:
        raise ValueError('text does not fit its UTF-8 bound')
    return value


def _named(items, value_type, limit):
    if not isinstance(items, Mapping) or len(items) > limit:
        raise ValueError('invalid action schema')
    copied = dict(items)
    if any(not isinstance(k, str) or not NAME.fullmatch(k) or not isinstance(v, value_type)
           for k, v in copied.items()):
        raise ValueError('invalid action name or schema')
    return MappingProxyType(copied)


def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate JSON member')
            value[key] = item
        return value

    def invalid_number(_):
        raise ValueError('non-finite JSON number')

    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_number)


@dataclass(frozen=True)
class OperandSpec:
    choices: tuple[str, ...] = ()
    max_bytes: int | None = None

    def __post_init__(self):
        if not isinstance(self.choices, (tuple, list)) or len(self.choices) > 128:
            raise ValueError('invalid operand choices')
        choices = tuple(_text(item, 1024) for item in self.choices)
        if choices:
            if len(set(choices)) != len(choices) or self.max_bytes is not None:
                raise ValueError('an enum requires distinct choices')
        elif type(self.max_bytes) is not int or not 1 <= self.max_bytes <= MAX_SOURCE_BYTES:
            raise ValueError('a text operand requires a byte limit')
        object.__setattr__(self, 'choices', choices)

    def describe(self):
        return {'choices': list(self.choices)} if self.choices else {'max_utf8_bytes': self.max_bytes}

    def accepts(self, value):
        if self.choices:
            return isinstance(value, str) and value in self.choices
        try:
            _text(value, self.max_bytes, empty=True)
        except ValueError:
            return False
        return True


@dataclass(frozen=True)
class ActionSpec:
    description: str
    fields: Mapping[str, OperandSpec] = field(default_factory=dict)

    def __post_init__(self):
        _text(self.description, 2048)
        object.__setattr__(self, 'fields', _named(self.fields, OperandSpec, 24))

    def describe(self):
        return {'description': self.description,
                'arguments': {name: spec.describe() for name, spec in self.fields.items()}}


@dataclass(frozen=True)
class ModelPrompt:
    task: str
    observation: str
    actions: Mapping[str, ActionSpec]

    def __post_init__(self):
        _text(self.task, MAX_REQUEST_BYTES)
        _text(self.observation, MAX_REQUEST_BYTES, empty=True)
        object.__setattr__(self, 'actions', _named(self.actions, ActionSpec, 32))
        if not self.actions:
            raise ValueError('a model turn requires permitted actions')

    def request(self, model, output_limit):
        participant = {'task': self.task, 'observation': self.observation,
                       'actions': {name: spec.describe() for name, spec in self.actions.items()}}
        messages = [{'role': 'system', 'content': INSTRUCTION},
                    {'role': 'user', 'content': encoded(participant).decode('utf-8')}]
        payload = {'model': model, 'messages': messages, 'stream': False,
                   'temperature': 0, 'max_tokens': output_limit}
        if len(encoded(payload)) > MAX_REQUEST_BYTES:
            raise ValueError('prepared request exceeds its byte limit')
        return payload

    def proposal(self, content):
        obj = strict_json(content)
        if type(obj) is not dict or set(obj) != {'action', 'arguments'} or not isinstance(obj['action'], str):
            raise ValueError('invalid action envelope')
        spec = self.actions.get(obj['action'])
        args = obj['arguments']
        if spec is None or type(args) is not dict or set(args) != set(spec.fields):
            raise ValueError('action or operands outside the menu')
        if any(not field.accepts(args[name]) for name, field in spec.fields.items()):
            raise ValueError('operand outside the permitted values')
        return obj


@dataclass(frozen=True)
class ModelDecision:
    action: str
    arguments: dict[str, str]
    request_sha256: str
    result_sha256: str
    input_tokens: int | None
    output_tokens: int | None
    spend_usd_micros: int | None


class ModelIncomplete(RuntimeError):
    """A turn has no usable typed proposal. Measurement fields refer to original evidence."""

    def __init__(self, code, request_sha256=None, result_sha256=None, *, input_tokens=None,
                 output_tokens=None, spend_usd_micros=None):
        super().__init__(code)
        self.code = code
        self.request_sha256 = request_sha256
        self.result_sha256 = result_sha256
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.spend_usd_micros = spend_usd_micros
