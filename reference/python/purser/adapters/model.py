"""A replaceable, explicitly enrolled model-only executor for the synthetic study."""

import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import http.client
import math
import os
import re
from pathlib import Path
import sqlite3
import time

from experiments.e1.artifacts import encoded, sha
from experiments.e1.recovery import sync_directory
from experiments.model_budget import BudgetRefusal, LIMITS, ModelBudget, ModelTurn
from purser.kernel.artifacts import Artifacts
from purser.kernel.wire import KernelError

from .model_evidence import MAX_RECORD_BYTES, interpret, request_identity, verify_model_record
from .model_http import Exchange, FramingError, LocalEndpoint
from .model_protocol import ActionSpec, ModelDecision, ModelIncomplete, ModelPrompt, OperandSpec


__all__ = ['ActionSpec', 'LocalEndpoint', 'LocalModelAdapter', 'ModelDecision', 'ModelIncomplete',
           'ModelPrompt', 'OperandSpec', 'verify_model_record']


@dataclass(frozen=True)
class LocalModelAdapter:
    endpoint: LocalEndpoint | None
    budget: ModelBudget | None
    result_dir: Path
    objects: Artifacts = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        endpoint, budget = self.endpoint, self.budget
        if endpoint is not None and (not isinstance(endpoint, LocalEndpoint) or not isinstance(budget, ModelBudget)
                or budget.authority.service_sha256 != endpoint.service_sha256
                or budget.authority.input_price_ceiling != 0 or budget.authority.output_price_ceiling != 0):
            raise ValueError('local service and zero-price policy must match the enrolled authority')
        object.__setattr__(self, 'result_dir', Path(self.result_dir))
        object.__setattr__(self, 'objects', Artifacts(self.result_dir / 'model'))

    def read_artifact(self, digest):
        return self.objects.read(digest)

    def read_record(self, result_sha256):
        return verify_model_record(self.read_artifact(result_sha256), expected_sha256=result_sha256)

    def _slot(self, turn):
        key = [self.budget.authority.authority_sha256, *turn.key(), turn.request_sha256]
        return self.result_dir / ('turn-' + sha(encoded(key)))

    def _capture(self, turn, prompt, request, output_limit, deadline):
        # Publish the deterministic name before the only permitted socket call. A
        # second process may see an empty or partial original while its writer runs.
        slot = self._slot(turn)
        try:
            fd = os.open(slot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, 'wb') as stream:
            sync_directory(self.result_dir)
            self.objects.put(encoded({'service': self.endpoint.profile(), 'body': request}))
            record = self._exchange(turn, prompt, request, output_limit, deadline)
            stream.write(encoded(record))
            stream.flush()
            os.fsync(stream.fileno())

    def _matches(self, record, turn):
        return (record['turn'] == {'registration_sha256': turn.registration_sha256,
                                   'unit': list(turn.unit), 'ordinal': turn.number}
                and record['request_sha256'] == turn.request_sha256
                and record['authority_sha256'] == self.budget.authority.authority_sha256)

    def _legacy_original(self, turn, deadline):
        # Prototype results could reach the private content store before their
        # turn link. New exchanges already have a slot before they reach a socket.
        original = None
        try:
            entries = os.scandir(self.objects.directory)
        except FileNotFoundError:
            return None
        with entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    raise ModelIncomplete('recovery_deadline', turn.request_sha256)
                if not re.fullmatch('[0-9a-f]{64}', entry.name) or not entry.is_file(follow_symlinks=False):
                    continue
                with open(entry.path, 'rb') as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
                try:
                    record = verify_model_record(raw, expected_sha256=entry.name)
                except ValueError:
                    continue
                if self._matches(record, turn):
                    if original is not None and original != raw:
                        raise ValueError('conflicting original model results')
                    original = raw
        return original

    def _recover(self, turn, expected_digest, deadline):
        if expected_digest is not None:
            try:
                raw = self.read_artifact(expected_digest)
            except KernelError:
                raw = None
        else:
            raw = None
        if raw is None:
            try:
                with self._slot(turn).open('rb') as stream:
                    raw = stream.read(MAX_RECORD_BYTES + 1)
            except FileNotFoundError:
                raw = self._legacy_original(turn, deadline)
            if not raw:
                raise ModelIncomplete('unresolved_release', turn.request_sha256)
        digest = sha(raw)
        if expected_digest is not None and digest != expected_digest:
            raise ValueError('original evidence differs from settled receipt')
        try:
            record = verify_model_record(raw, expected_sha256=digest)
        except ValueError as exc:
            if expected_digest is None:
                raise ModelIncomplete('unresolved_release', turn.request_sha256) from exc
            raise
        if not self._matches(record, turn):
            raise ValueError('model result belongs to another logical turn')
        self.objects.put(raw)
        self.budget.settle(turn, result_sha256=digest, **record['usage'])
        return record, digest

    def recover_released(self, turn: ModelTurn, *, deadline):
        """Read or restore the original receipt for an existing reservation. Never reserve or send."""
        if self.budget is None:
            return None
        prior = self.budget.inspect(turn)
        if prior is None:
            return None
        record, _ = self._recover(turn, prior['result_sha256'], deadline)
        return record

    def _exchange(self, turn, prompt, request, output_limit, deadline):
        started_utc = datetime.now(timezone.utc).isoformat()
        started_ns = time.monotonic_ns()
        exchange = Exchange()
        try:
            status = exchange.post(self.endpoint, encoded(request), deadline)
        except TimeoutError:
            status = 'deadline'
        except (http.client.HTTPException, FramingError, ValueError):
            status = 'invalid_transport'
        except OSError:
            status = 'transport_unavailable'
        ended_ns = time.monotonic_ns()
        ended_utc = datetime.now(timezone.utc).isoformat()
        raw = None if exchange.body is None else bytes(exchange.body)
        outcome, proposal, usage = interpret(self.endpoint, prompt, output_limit, status, exchange.http_status, raw,
                                             complete=exchange.response_complete, identity_encoding=exchange.identity_encoding)
        return {
            'v': 1, 'turn': {'registration_sha256': turn.registration_sha256,
                              'unit': list(turn.unit), 'ordinal': turn.number},
            'authority_sha256': self.budget.authority.authority_sha256,
            'service': self.endpoint.profile(), 'request': request, 'request_sha256': turn.request_sha256,
            'body_sha256': sha(encoded(request)),
            'limits': {'input_tokens': self.endpoint.context_window, 'output_tokens': output_limit},
            'transport': {'released': True, 'status': status, 'wire_state': exchange.wire_state,
                          'http_status': exchange.http_status, 'response_complete': exchange.response_complete,
                          'identity_encoding': exchange.identity_encoding, 'json_content': exchange.json_content,
                          'response_sha256': sha(raw) if raw is not None else None,
                          'response_base64': base64.b64encode(raw).decode('ascii') if raw is not None else None},
            'outcome': outcome, 'proposal': proposal, 'usage': usage, 'started_utc': started_utc,
            'returned_utc': ended_utc, 'started_monotonic_ns': started_ns, 'returned_monotonic_ns': ended_ns,
        }

    def decide(self, *, registration_sha256, unit, ordinal, prompt: ModelPrompt, output_limit, deadline) -> ModelDecision:
        if self.endpoint is None:
            raise ModelIncomplete('disabled')
        if type(prompt) is not ModelPrompt:
            raise ValueError('a typed fixture prompt is required')
        if type(deadline) not in (float, int) or not math.isfinite(deadline):
            raise ValueError('a finite absolute monotonic deadline is required')
        if type(output_limit) is not int or not 1 <= output_limit <= LIMITS['max_output_tokens']:
            raise ValueError('invalid requested output limit')
        deadline = min(deadline, time.monotonic() + LIMITS['max_elapsed_s'])
        request = prompt.request(self.endpoint.model, output_limit)
        request_sha256 = request_identity(self.endpoint.profile(), request)
        turn = ModelTurn(registration_sha256, tuple(unit), ordinal, request_sha256)
        try:
            turn.key()
            if deadline <= time.monotonic() and self.budget.inspect(turn) is None:
                raise ModelIncomplete('deadline', request_sha256)
            self.result_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            reservation = self.budget.reserve(turn, input_limit=self.endpoint.context_window, output_limit=output_limit)
        except BudgetRefusal as exc:
            raise ModelIncomplete('budget_refused', request_sha256) from exc
        except (OSError, sqlite3.Error) as exc:
            raise ModelIncomplete('storage_unavailable', request_sha256) from exc

        try:
            if reservation['release']:
                self._capture(turn, prompt, request, output_limit, deadline)
            record, digest = self._recover(turn, reservation['result_sha256'], deadline)
        except (OSError, sqlite3.Error, KernelError, ValueError) as exc:
            raise ModelIncomplete('evidence_unavailable', request_sha256) from exc
        if record['outcome'] != 'decision':
            raise ModelIncomplete(record['outcome'], request_sha256, digest, **record['usage'])
        return ModelDecision(**record['proposal'], request_sha256=request_sha256,
                             result_sha256=digest, **record['usage'])

    def executor(self, *, commitment, work, assignment, generation, candidate_for, **turn):
        """Bind a prepared fixture turn to the normal driver callable and a trusted candidate builder."""
        def execute(pack):
            binding = (pack['commitment']['id'], pack['work']['id'], pack['assignment']['id'],
                       pack['work']['generation'])
            if binding != (commitment, work, assignment, generation):
                raise ModelIncomplete('wrong_assignment')
            return candidate_for(self.decide(**turn))
        return execute
