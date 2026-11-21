"""Durable reservations for model turns sharing one authorized study budget."""

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from experiments.e1.artifacts import MANIFEST, encoded
from experiments.e1.recovery import sync_directory
from experiments.model_units import Unit, validate_unit


LIMITS = MANIFEST["budgets"]["model"]
DIGEST = re.compile("[a-f0-9]{64}")
MAX_SQL_INT = (1 << 63) - 1


class BudgetRefusal(ValueError):
    """A new provider release is unavailable under the recorded authorization."""


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_SQL_INT


def _digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


@dataclass(frozen=True)
class SpendingAuthority:
    """The launcher binds this policy to the principal's decision and selected service.

    Price ceilings are microdollars per million tokens including applicable tariffs.
    The service must have token-only pricing and supply defensible request bounds.
    """

    authority_sha256: str
    service_sha256: str
    maximum_spend_usd_micros: int
    input_price_ceiling: int
    output_price_ceiling: int

    def __post_init__(self):
        if not all(_digest(v) for v in (self.authority_sha256, self.service_sha256)):
            raise ValueError("authority and service require SHA-256 identities")
        if not all(_integer(v) for v in (self.maximum_spend_usd_micros, self.input_price_ceiling,
                                          self.output_price_ceiling)):
            raise ValueError("invalid monetary ceiling")

    def maximum_charge(self, input_tokens, output_tokens):
        def charge(tokens, price):
            return (tokens * price + 999_999) // 1_000_000

        return charge(input_tokens, self.input_price_ceiling) + charge(output_tokens, self.output_price_ceiling)


@dataclass(frozen=True)
class ModelTurn:
    registration_sha256: str
    unit: Unit
    number: int
    request_sha256: str

    def key(self):
        if not _digest(self.registration_sha256) or not _digest(self.request_sha256):
            raise ValueError("missing registration or prepared request identity")
        if type(self.number) is not int or not 1 <= self.number <= LIMITS['max_model_calls']:
            raise BudgetRefusal("model turn exceeds the case call limit")
        validate_unit(self.unit)
        return self.registration_sha256, encoded(self.unit).decode(), self.number


SCHEMA = """
CREATE TABLE authority (id INTEGER PRIMARY KEY CHECK(id = 1), policy BLOB NOT NULL);
CREATE TABLE turns (
    registration TEXT NOT NULL, unit TEXT NOT NULL, number INTEGER NOT NULL,
    request_sha256 TEXT NOT NULL, input_limit INTEGER NOT NULL, output_limit INTEGER NOT NULL,
    maximum_charge INTEGER NOT NULL, result_sha256 TEXT,
    input_tokens INTEGER, output_tokens INTEGER, spend_usd_micros INTEGER,
    overrun INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (registration, unit, number)
);
"""
WHERE_TURN = " WHERE registration=? AND unit=? AND number=?"


class ModelBudget:
    def __init__(self, path: Path, authority: SpendingAuthority):
        self.path = Path(path).resolve()
        self.authority = authority

    @classmethod
    def enroll(cls, path, authority):
        """Publish a fully initialized file once. Re-enrollment preserves every prior turn."""
        budget = cls(path, authority)
        if not budget.path.exists():
            fd, name = tempfile.mkstemp(prefix='.purser-budget-', dir=budget.path.parent)
            os.close(fd)
            try:
                with closing(sqlite3.connect(name)) as db:
                    if db.execute('PRAGMA journal_mode=WAL').fetchone()[0] != 'wal':
                        raise BudgetRefusal('study budget requires local SQLite WAL')
                    db.execute('PRAGMA synchronous=FULL')
                    db.executescript(SCHEMA)
                    db.execute('INSERT INTO authority VALUES (1,?)', (encoded(asdict(authority)),))
                    db.commit()
                try:
                    os.link(name, budget.path)
                except FileExistsError:
                    pass
                sync_directory(budget.path.parent)
            finally:
                os.unlink(name)
        with budget._transaction(writing=False):
            pass
        return budget

    @contextmanager
    def _transaction(self, *, writing=True):
        # Opening an existing authority must never create a new budget after file loss.
        uri = self.path.as_uri() + ('?mode=rw' if writing else '?mode=ro')
        with closing(sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            if writing:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
            else:
                db.execute('BEGIN')
            row = db.execute('SELECT policy FROM authority WHERE id=1').fetchone()
            if row is None or row[0] != encoded(asdict(self.authority)):
                raise BudgetRefusal('study budget belongs to another authority or service')
            yield db
            db.commit()

    def reserve(self, turn: ModelTurn, *, input_limit: int, output_limit: int):
        """Only a true release permits I/O. A pending replay retains its full liability."""
        key = turn.key()
        if not _integer(input_limit) or not _integer(output_limit) or output_limit == 0:
            raise ValueError('invalid prepared token bound')
        if input_limit > LIMITS['max_input_tokens'] or output_limit > LIMITS['max_output_tokens']:
            raise BudgetRefusal('request exceeds the case token limit')
        quoted = self.authority.maximum_charge(input_limit, output_limit)
        with self._transaction() as db:
            prior = db.execute('SELECT * FROM turns' + WHERE_TURN, key).fetchone()
            if prior is not None:
                if (prior['request_sha256'], prior['input_limit'], prior['output_limit']) != (
                        turn.request_sha256, input_limit, output_limit):
                    raise BudgetRefusal('model turn already names a different request or bound')
                return {'release': False, 'result_sha256': prior['result_sha256']}
            if db.execute('SELECT 1 FROM turns WHERE overrun=1 LIMIT 1').fetchone():
                raise BudgetRefusal('observed service usage exceeded a reserved bound')
            previous = db.execute('SELECT number, result_sha256, COALESCE(input_tokens,input_limit), '
                                  'COALESCE(output_tokens,output_limit) FROM turns WHERE registration=? AND unit=? '
                                  'ORDER BY number', key[:2]).fetchall()
            if turn.number != len(previous) + 1 or any(r[1] is None for r in previous):
                raise BudgetRefusal('an earlier turn is missing or unresolved')
            if (sum(r[2] for r in previous) + input_limit > LIMITS['max_input_tokens']
                    or sum(r[3] for r in previous) + output_limit > LIMITS['max_output_tokens']):
                raise BudgetRefusal('case token budget exhausted')
            liability = db.execute('SELECT COALESCE(SUM(COALESCE(spend_usd_micros, maximum_charge)), 0) FROM turns').fetchone()[0]
            if liability + quoted > self.authority.maximum_spend_usd_micros:
                raise BudgetRefusal('authorized spending budget exhausted')
            db.execute('INSERT INTO turns (registration,unit,number,request_sha256,input_limit,output_limit,maximum_charge) '
                       'VALUES (?,?,?,?,?,?,?)', (*key, turn.request_sha256, input_limit, output_limit, quoted))
        return {'release': True, 'result_sha256': None}

    def settle(self, turn: ModelTurn, *, result_sha256, input_tokens=None, output_tokens=None, spend_usd_micros=None):
        """Bind a retained response or failure artifact. Unknown amounts keep their original reservation."""
        key = turn.key()
        observed = (input_tokens, output_tokens, spend_usd_micros)
        if not _digest(result_sha256) or any(v is not None and not _integer(v) for v in observed):
            raise ValueError('invalid model result identity or measured usage')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM turns' + WHERE_TURN, key).fetchone()
            if row is None or row['request_sha256'] != turn.request_sha256:
                raise BudgetRefusal('no matching released model turn')
            if row['result_sha256'] is not None:
                previous = tuple(row[k] for k in ('result_sha256', 'input_tokens', 'output_tokens', 'spend_usd_micros'))
                if previous != (result_sha256, *observed):
                    raise BudgetRefusal('original provider settlement differs')
                return
            limits = (row['input_limit'], row['output_limit'], row['maximum_charge'])
            overrun = any(actual is not None and actual > maximum for actual, maximum in zip(observed, limits))
            db.execute('UPDATE turns SET result_sha256=?, input_tokens=?, output_tokens=?, spend_usd_micros=?, overrun=?'
                       + WHERE_TURN, (result_sha256, *observed, int(overrun), *key))

    def inspect(self, turn: ModelTurn):
        """Read an original turn independently of the provider and its response store."""
        with self._transaction(writing=False) as db:
            row = db.execute('SELECT * FROM turns' + WHERE_TURN, turn.key()).fetchone()
            if row is None:
                return None
            if row['request_sha256'] != turn.request_sha256:
                raise BudgetRefusal('model turn names another request')
            result = dict(row)
            result['unit'] = json.loads(result['unit'])
            return result
