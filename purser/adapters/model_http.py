"""A single bounded native HTTP exchange to an explicitly enrolled loopback service."""

from dataclasses import dataclass
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

    def __post_init__(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('invalid loopback port')
        if not isinstance(self.model, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,190}', self.model):
            raise ValueError('invalid selected model')
        if not isinstance(self.runtime_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.runtime_sha256):
            raise ValueError('missing qualified local runtime identity')
        if type(self.context_window) is not int or not 1 <= self.context_window <= LIMITS['max_input_tokens']:
            raise ValueError('invalid service-enforced model context window')

    def profile(self):
        return {'protocol': 'purser.local.chat.v1', 'host': HOST, 'port': self.port, 'route': ROUTE,
                'model': self.model, 'runtime_sha256': self.runtime_sha256,
                'context_window': self.context_window, 'service_charge_usd_micros': 0}

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
    body: bytes | None = None

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
                if response.status != 200:
                    return 'http_status'
                if response.headers.get_content_type() != 'application/json':
                    return 'invalid_content_type'
                if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    return 'unsupported_encoding'
                lengths = response.headers.get_all('Content-Length', [])
                transfer = response.headers.get_all('Transfer-Encoding', [])
                if (len(lengths) > 1 or len(transfer) > 1 or (lengths and transfer)
                        or (transfer and transfer[0].lower() != 'chunked')):
                    raise FramingError('ambiguous response framing')
                if lengths and (not re.fullmatch(r'[0-9]{1,10}', lengths[0])
                                or int(lengths[0]) > MAX_RESPONSE_BYTES):
                    raise FramingError('invalid response byte length')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                remaining(deadline)
                if len(raw) > MAX_RESPONSE_BYTES or (lengths and len(raw) != int(lengths[0])):
                    raise FramingError('invalid response body size')
                self.body = raw
        return 'response'
