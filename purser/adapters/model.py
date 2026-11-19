"""A replaceable, explicitly enrolled model-only executor for the synthetic study."""

import base64
from datetime import datetime, timezone
import http.client
import math
import os
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


class LocalModelAdapter:
    def __init__(self, endpoint: LocalEndpoint | None, budget: ModelBudget | None, result_dir: Path):
        if endpoint is not None and (not isinstance(endpoint, LocalEndpoint) or not isinstance(budget, ModelBudget)
                or budget.authority.service_sha256 != endpoint.service_sha256
                or budget.authority.input_price_ceiling != 0 or budget.authority.output_price_ceiling != 0):
            raise ValueError('local service and zero-price policy must match the enrolled authority')
        self.endpoint = endpoint
        self.budget = budget
        self.result_dir = Path(result_dir)
        self.objects = Artifacts(self.result_dir / 'model')

    def read_artifact(self, digest):
        return self.objects.read(digest)

    def read_record(self, result_sha256):
        return verify_model_record(self.read_artifact(result_sha256), expected_sha256=result_sha256)

    def _slot(self, turn):
        key = [self.budget.authority.authority_sha256, *turn.key(), turn.request_sha256]
        return self.result_dir / ('turn-' + sha(encoded(key)))

    def _publish(self, turn, record):
        result_sha256 = self.objects.put(encoded(record))
        slot = self._slot(turn)
        try:
            os.link(self.objects.directory / result_sha256, slot)
        except FileExistsError:
            if self._read_slot(slot) != result_sha256:
                raise ValueError('turn already has different retained evidence')
        sync_directory(self.result_dir)
        return result_sha256

    @staticmethod
    def _read_slot(path):
        try:
            with path.open('rb') as stream:
                raw = stream.read(MAX_RECORD_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError('turn evidence is oversized')
        return sha(raw)

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
        raw = exchange.body
        outcome, proposal, usage = interpret(self.endpoint, prompt, output_limit, status, exchange.http_status, raw)
        return {
            'v': 1, 'turn': {'registration_sha256': turn.registration_sha256,
                              'unit': list(turn.unit), 'ordinal': turn.number},
            'authority_sha256': self.budget.authority.authority_sha256,
            'service': self.endpoint.profile(), 'request': request, 'request_sha256': turn.request_sha256,
            'body_sha256': sha(encoded(request)),
            'limits': {'input_tokens': self.endpoint.context_window, 'output_tokens': output_limit},
            'transport': {'released': True, 'status': status, 'wire_state': exchange.wire_state,
                          'http_status': exchange.http_status, 'response_sha256': sha(raw) if raw is not None else None,
                          'response_base64': base64.b64encode(raw).decode('ascii') if raw is not None else None},
            'outcome': outcome, 'proposal': proposal, 'usage': usage, 'started_utc': started_utc,
            'returned_utc': ended_utc, 'started_monotonic_ns': started_ns, 'returned_monotonic_ns': ended_ns,
        }

    def decide(self, *, registration_sha256, unit, ordinal, prompt: ModelPrompt, output_limit, deadline) -> ModelDecision:
        if self.endpoint is None:
            raise ModelIncomplete('disabled')
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
                self.objects.put(encoded({'service': self.endpoint.profile(), 'body': request}))
                result = self._exchange(turn, prompt, request, output_limit, deadline)
                digest = self._publish(turn, result)
            else:
                digest = reservation['result_sha256'] or self._read_slot(self._slot(turn))
                if digest is None:
                    raise ModelIncomplete('unresolved_release', request_sha256)
            record = self.read_record(digest)
            if (record['turn'] != {'registration_sha256': registration_sha256, 'unit': list(turn.unit), 'ordinal': ordinal}
                    or record['request_sha256'] != request_sha256
                    or record['authority_sha256'] != self.budget.authority.authority_sha256):
                raise ValueError('turn and retained evidence disagree')
            self.budget.settle(turn, result_sha256=digest, **record['usage'])
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
