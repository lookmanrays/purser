"""A single bounded native HTTP exchange to an explicitly enrolled loopback service."""

from dataclasses import dataclass, fields
import http.client
import io
import re
import socket
import time

from experiments.e1.artifacts import encoded, sha
from experiments.model_budget import LIMITS


HOST = '127.0.0.1'
ROUTE = '/v1/chat/completions'
MAX_RESPONSE_BYTES = 192 * 1024
MAX_WIRE_BYTES = MAX_RESPONSE_BYTES + 64 * 1024


@dataclass(frozen=True)
class LocalEndpoint:
    """A zero-price service with a qualified hard context window, including its chat template."""

    port: int
    model: str
    runtime_sha256: str
    context_window: int
    provider: str | None = None
    provider_version: str | None = None
    model_revision: str | None = None

    def __post_init__(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('invalid loopback port')
        if not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,190}', self.model):
            raise ValueError('invalid selected model')
        if not isinstance(self.runtime_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.runtime_sha256):
            raise ValueError('missing qualified local runtime identity')
        if type(self.context_window) is not int or not 1 <= self.context_window <= LIMITS['max_input_tokens']:
            raise ValueError('invalid service-enforced model context window')
        for label in (self.provider, self.provider_version, self.model_revision):
            if label is not None and (not isinstance(label, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9 ._:/@+-]{0,190}', label)):
                raise ValueError('invalid local runtime provenance')
        if self.provider_version is not None and self.provider is None:
            raise ValueError('provider version requires a provider identity')

    def profile(self):
        identity = {key: value for key, value in (('provider', self.provider), ('provider_version', self.provider_version),
                                                  ('model_revision', self.model_revision)) if value is not None}
        return {**identity, 'protocol': 'purser.local.chat.v1', 'host': HOST, 'port': self.port, 'route': ROUTE,
                'model': self.model, 'runtime_sha256': self.runtime_sha256,
                'context_window': self.context_window, 'service_charge_usd_micros': 0}

    @classmethod
    def from_profile(cls, profile):
        """Recover the complete registered endpoint, including optional provider provenance."""
        if type(profile) is not dict:
            raise ValueError('invalid registered endpoint')
        operands = {f.name: profile[f.name] for f in fields(cls) if f.name in profile}
        try:
            endpoint = cls(**operands)
        except TypeError as error:
            raise ValueError('incomplete registered endpoint') from error
        if encoded(endpoint.profile()) != encoded(profile):
            raise ValueError('endpoint differs from registration')
        return endpoint

    @property
    def service_sha256(self):
        return sha(encoded(self.profile()))


def remaining(deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise TimeoutError('model deadline')
    return left


class FramingError(ValueError):
    pass


class _DeadlineReader(io.RawIOBase):
    """HTTPResponse uses this reader for status, headers, chunk framing, trailers and body."""

    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.room = MAX_WIRE_BYTES

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(remaining(self.deadline))
        count = self.sock.recv_into(memoryview(buffer)[:self.room + 1])
        self.room -= count
        if self.room < 0:
            raise FramingError('response exceeds its wire byte bound')
        return count

    def makefile(self, mode):
        return io.BufferedReader(self)


@dataclass
class Exchange:
    """Observed transport progress. A send that only started has an unknown delivery outcome."""

    wire_state: str = 'not_sent'
    http_status: int | None = None
    body: bytearray | None = None
    response_complete: bool = False
    identity_encoding: bool | None = None
    json_content: bool | None = None

    def post(self, endpoint, body, deadline):
        headers = (f'POST {ROUTE} HTTP/1.1\r\nHost: {HOST}:{endpoint.port}\r\n'
                   f'Content-Type: application/json\r\nAccept: application/json\r\n'
                   f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode('ascii')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(remaining(deadline))
            sock.connect((HOST, endpoint.port))
            sock.settimeout(remaining(deadline))
            self.wire_state = 'send_started'
            sock.sendall(headers + body)
            self.wire_state = 'sent'
            with http.client.HTTPResponse(_DeadlineReader(sock, deadline)) as response:
                response.begin()
                self.http_status = response.status
                self.json_content = (len(response.headers.get_all('Content-Type', [])) == 1
                                     and response.headers.get_content_type() == 'application/json')
                encodings = response.headers.get_all('Content-Encoding', [])
                self.identity_encoding = not encodings or (len(encodings) == 1 and encodings[0].lower() == 'identity')
                lengths = response.headers.get_all('Content-Length', [])
                transfer = response.headers.get_all('Transfer-Encoding', [])
                if (len(lengths) > 1 or len(transfer) > 1 or (lengths and transfer)
                        or (transfer and transfer[0].lower() != 'chunked')):
                    raise FramingError('ambiguous response framing')
                if lengths and not re.fullmatch(r'[0-9]{1,20}', lengths[0]):
                    raise FramingError('invalid response byte length')
                declared = int(lengths[0]) if lengths else None
                self.body = bytearray()
                while len(self.body) != declared:
                    remaining(deadline)
                    room = MAX_RESPONSE_BYTES - len(self.body)
                    part = response.read1(min(16 * 1024, room + 1))
                    if not part:
                        break
                    self.body.extend(part[:room])
                    if len(part) > room:
                        raise FramingError('response exceeds its byte limit')
                if declared is not None and len(self.body) != declared:
                    raise FramingError('incomplete response body')
                self.response_complete = True
                if response.status != 200:
                    return 'http_status'
                if not self.identity_encoding:
                    return 'unsupported_encoding'
                if not self.json_content:
                    return 'invalid_content_type'
        return 'response'
