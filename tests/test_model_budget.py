import json
import os
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from experiments.model_budget import BudgetRefusal, ModelBudget, ModelTurn, SpendingAuthority
from purser.driver import tick
from purser.kernel.store import Store
from tests import test_driver


POLICY = SpendingAuthority('a' * 64, 'b' * 64, 100, 1_000_000, 1_000_000)
FIRST = ModelTurn('1' * 64, ('E1', 'primary', 1001, 'loop'), 1, '2' * 64)
SECOND = replace(FIRST, unit=('E1', 'primary', 1001, 'purser'))
RESULT = '3' * 64


def request_after_barrier(path, turn, barrier, reply, witness, hold_released):
    """Exercise the public budget in a separate process beside a counted synthetic service."""
    try:
        barrier.wait(10)
        permitted = ModelBudget(path, POLICY).reserve(turn, input_limit=50, output_limit=25)['release']
        if permitted:
            with open(witness, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps([os.getpid(), turn.unit]) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
        reply.send('released' if permitted else 'replay')
        if permitted and hold_released:
            time.sleep(15)
    except BudgetRefusal:
        reply.send('refused')
    finally:
        reply.close()


class ModelSpending(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'study.sqlite3'
        self.budget = ModelBudget.enroll(self.path, POLICY)

    def test_reduced_settlement_across_registrations_and_immutable_enrollment(self):
        qualification = replace(FIRST, registration_sha256='4' * 64)
        self.assertTrue(self.budget.reserve(qualification, input_limit=60, output_limit=20)['release'])
        with self.assertRaises(BudgetRefusal):
            self.budget.reserve(FIRST, input_limit=30, output_limit=5)
        usage = dict(result_sha256=RESULT, input_tokens=11, output_tokens=5, spend_usd_micros=16)
        self.budget.settle(qualification, **usage)
        reopened = ModelBudget.enroll(self.path, POLICY)
        reopened.settle(qualification, **usage)
        self.assertTrue(reopened.reserve(FIRST, input_limit=70, output_limit=14)['release'])
        self.assertEqual(reopened.reserve(qualification, input_limit=60, output_limit=20),
                         {'release': False, 'result_sha256': RESULT})
        for altered in [dict(spend_usd_micros=15), dict(input_tokens=None), dict(result_sha256='5' * 64)]:
            with self.assertRaises(BudgetRefusal):
                reopened.settle(qualification, **(usage | altered))
        for policy in [replace(POLICY, maximum_spend_usd_micros=105), replace(POLICY, service_sha256='0' * 64)]:
            with self.assertRaises(BudgetRefusal):
                ModelBudget.enroll(self.path, policy)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        missing = self.path.with_name('missing.sqlite3')
        with self.assertRaises(sqlite3.OperationalError):
            ModelBudget(missing, POLICY).reserve(SECOND, input_limit=1, output_limit=1)
        self.assertFalse(missing.exists())

    def test_token_caps_missing_usage_and_logical_turns(self):
        free = ModelBudget.enroll(self.path.with_name('free.db'), replace(POLICY, maximum_spend_usd_micros=0,
                                                                         input_price_ceiling=0, output_price_ceiling=0))
        self.assertTrue(free.reserve(FIRST, input_limit=63_990, output_limit=11_999)['release'])
        following = replace(FIRST, number=2, request_sha256='4' * 64)
        with self.assertRaises(BudgetRefusal):
            free.reserve(following, input_limit=10, output_limit=1)
        free.settle(FIRST, result_sha256=RESULT, output_tokens=4, spend_usd_micros=0)
        self.assertIsNone(free.inspect(FIRST)['input_tokens'])
        with self.assertRaises(BudgetRefusal):
            free.reserve(following, input_limit=11, output_limit=1)
        self.assertTrue(free.reserve(following, input_limit=10, output_limit=11_996)['release'])
        free.settle(following, result_sha256=RESULT)
        for number in range(1, 9):
            turn = replace(SECOND, number=number)
            self.assertTrue(free.reserve(turn, input_limit=1, output_limit=1)['release'])
            free.settle(turn, result_sha256=RESULT, input_tokens=1, output_tokens=1, spend_usd_micros=0)
        with self.assertRaises(BudgetRefusal):
            free.reserve(replace(SECOND, number=9), input_limit=1, output_limit=1)
        self.assertEqual(replace(POLICY, input_price_ceiling=125_000, output_price_ceiling=500_000)
                         .maximum_charge(9, 3), 4)

    def test_unknown_charge_claims_the_full_amount_and_overrun_stops_new_releases(self):
        self.budget.reserve(FIRST, input_limit=50, output_limit=40)
        self.budget.settle(FIRST, result_sha256=RESULT, input_tokens=1, output_tokens=1)
        self.assertIsNone(self.budget.inspect(FIRST)['spend_usd_micros'])
        with self.assertRaises(BudgetRefusal):
            self.budget.reserve(SECOND, input_limit=10, output_limit=1)
        self.budget.reserve(SECOND, input_limit=6, output_limit=4)
        self.budget.settle(SECOND, result_sha256=RESULT, input_tokens=7, output_tokens=4, spend_usd_micros=11)
        self.assertEqual(self.budget.inspect(SECOND)['overrun'], 1)
        self.assertFalse(self.budget.reserve(SECOND, input_limit=6, output_limit=4)['release'])
        with self.assertRaisesRegex(BudgetRefusal, 'exceeded'):
            self.budget.reserve(replace(SECOND, number=2), input_limit=0, output_limit=1)

    def test_validation_precedes_release_and_preserves_the_original_request(self):
        self.assertIsNone(self.budget.inspect(FIRST))
        with self.assertRaises(BudgetRefusal):
            self.budget.settle(FIRST, result_sha256=RESULT)
        with self.assertRaises(BudgetRefusal):
            self.budget.reserve(replace(FIRST, number=2), input_limit=1, output_limit=1)
        for turn, limits in [(replace(FIRST, unit=('E1', 'primary', 1001, 'unknown')), (1, 1)),
                             (replace(FIRST, request_sha256='bad'), (1, 1)), (FIRST, (False, 1)),
                             (FIRST, (0, 0)), (FIRST, (64_001, 1)), (FIRST, (1, 12_001))]:
            with self.subTest(turn=turn, limits=limits), self.assertRaises(ValueError):
                self.budget.reserve(turn, input_limit=limits[0], output_limit=limits[1])
        self.budget.reserve(FIRST, input_limit=1, output_limit=2)
        changed = replace(FIRST, request_sha256='0' * 64)
        for turn, limits in [(changed, (1, 2)), (FIRST, (2, 1))]:
            with self.assertRaises(BudgetRefusal):
                self.budget.reserve(turn, input_limit=limits[0], output_limit=limits[1])
        with self.assertRaises(BudgetRefusal):
            self.budget.inspect(changed)
        with self.assertRaises(BudgetRefusal):
            self.budget.settle(changed, result_sha256=RESULT)

    def exercise_concurrent(self, turns, *, kill_first=False):
        ctx = get_context('spawn')
        barrier = ctx.Barrier(len(turns))
        witness = self.path.with_name('calls.jsonl')
        children = []
        readers = []
        try:
            for turn in turns:
                reader, writer = ctx.Pipe(duplex=False)
                child = ctx.Process(target=request_after_barrier, args=(self.path, turn, barrier, writer, witness, kill_first))
                child.start()
                writer.close()
                readers.append(reader)
                children.append(child)
            replies = []
            for reader in readers:
                self.assertTrue(reader.poll(15))
                replies.append(reader.recv())
            if kill_first:
                children[replies.index('released')].kill()
            for child in children:
                child.join(10)
                self.assertFalse(child.is_alive())
            self.assertEqual(len(witness.read_text().splitlines()), 1)
            return sorted(replies)
        finally:
            for reader in readers:
                reader.close()
            for child in children:
                if child.is_alive():
                    child.kill()
                child.join()
                child.close()

    def test_concurrent_same_turn_process_loss_and_cold_replay(self):
        self.assertEqual(self.exercise_concurrent((FIRST, FIRST), kill_first=True), ['released', 'replay'])
        cold = ModelBudget(self.path, POLICY)
        self.assertEqual(cold.reserve(FIRST, input_limit=50, output_limit=25), {'release': False, 'result_sha256': None})
        self.assertEqual(cold.inspect(FIRST)['maximum_charge'], 75)
        with self.assertRaises(BudgetRefusal):
            cold.reserve(SECOND, input_limit=50, output_limit=25)

    def test_concurrent_registrations_share_the_human_cap(self):
        qualification = replace(FIRST, registration_sha256='6' * 64)
        self.assertEqual(self.exercise_concurrent((FIRST, qualification)), ['refused', 'released'])

    def test_foreground_redispatch_after_lost_reply_does_not_repeat_service(self):
        driver = test_driver.ForegroundDriver()
        self.addCleanup(driver.doCleanups)
        driver.setUp()
        service_calls, entries = [], []

        def executor(pack):
            entries.append((pack['assignment']['id'], pack['work']['generation']))
            result = ModelBudget(self.path, POLICY).reserve(FIRST, input_limit=40, output_limit=10)
            if result['release']:
                service_calls.append('counted canned service')
                raise TimeoutError('lost service reply')
            raise TimeoutError('original response is unknown')

        for message in ['lost service reply', 'original response is unknown']:
            with self.assertRaisesRegex(TimeoutError, message):
                tick(Store(driver.fixture.path), {'executor:first': executor})
        self.assertEqual(entries, [('AS-first', 1)] * 2)
        self.assertEqual(len(service_calls), 1)
        self.assertIsNone(ModelBudget(self.path, POLICY).inspect(FIRST)['result_sha256'])
