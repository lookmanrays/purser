import unittest

from experiments.e1.suite import schedule as e1_schedule
from experiments.e2.suite import schedule as e2_schedule
from experiments.e3.suite import schedule as e3_schedule
from experiments.model_costs import FIELDS, USAGE, ModelCaseCost, measure_model_turns, render_model_costs


class ModelCosts(unittest.TestCase):
    def test_partial_sends_and_missing_ordinals_conserve_each_observed_field(self):
        unavailable = dict.fromkeys(USAGE)
        unsent = {"transport": {"wire_state": "not_sent"}, "usage": unavailable}
        started = {"transport": {"wire_state": "send_started"}, "usage": unavailable}
        sent = {"transport": {"wire_state": "sent"}, "usage": dict(zip(USAGE, (12, None, 0)))}
        no_response = {"transport": {"wire_state": "sent"}, "usage": unavailable}
        for unresolved in (started, None):
            with self.subTest(unresolved=unresolved):
                self.assertEqual(measure_model_turns([unresolved]), ({}, {"model_calls": 0}, None))
                exact, lower, first = measure_model_turns([unsent, sent, unresolved, no_response])
                self.assertEqual(exact, {})
                self.assertEqual(lower, {"model_calls": 2, "input_tokens": 12, "output_tokens": 0, "spend_usd_micros": 0})
                self.assertIs(first, sent)
        self.assertEqual(measure_model_turns([no_response]), ({"model_calls": 1}, {}, no_response))

    def test_absent_originals_keep_the_entire_frozen_denominator(self):
        schedule = [(name, track, seed, arm)
                    for name, source in [("E1", e1_schedule), ("E2", e2_schedule), ("E3", e3_schedule)]
                    for track, seed, rank, arm in source()]
        self.assertEqual(len(schedule), 195)
        table = render_model_costs(schedule, {})
        self.assertIn("| All cases | 0 / 0 / 0 / 195 |" + " ? (0/195 exact) |" * 6, table)
        self.assertIn("| All · purser | 0 / 0 / 0 / 65 |", table)
        self.assertIn("| E2 · closed-affected · loop | 0 / 0 / 0 / 1 |", table)
        self.assertIn("| E3 · inconclusive-observation · purser | 0 / 0 / 0 / 1 |", table)

    def test_partial_and_failed_calls_keep_exact_known_and_unavailable_costs(self):
        first = ("E1", "primary", 1001, "loop")
        interrupted = ("E1", "primary", 1002, "loop")
        missing = ("E1", "primary", 1003, "loop")
        failed = ("E1", "primary", 1004, "loop")
        cases = {
            first: ModelCaseCost("complete", {"steps": 4, "elapsed_ns": 1_000_000_009, "model_calls": 1,
                                              "input_tokens": 40, "output_tokens": None, "spend_usd_micros": 9}),
            interrupted: ModelCaseCost("arm_incomplete", {"input_tokens": None},
                                       {"steps": 2, "elapsed_ns": 7, "model_calls": 1, "input_tokens": 8}),
            failed: ModelCaseCost("infrastructure_invalid", {"model_calls": 0, "spend_usd_micros": 0}),
        }
        table = render_model_costs([first, interrupted, missing, failed], cases)
        expected = ("| All cases | 1 / 1 / 1 / 1 | ≥6 (1/4 exact, 1 lower bound) | "
                    "≥1.000000016 (1/4 exact, 1 lower bound) | ≥2 (2/4 exact, 1 lower bound) | "
                    "≥48 (1/4 exact, 1 lower bound) | ? (0/4 exact) | ≥0.000009 (2/4 exact) |")
        self.assertIn(expected, table)

    def test_zero_cost_and_elapsed_precision(self):
        unit = ("E3", "clean", 3001, "purser")
        exact = {key: 0 for key in FIELDS}
        exact.update(elapsed_ns=10_000_000_000, model_calls=1, input_tokens=125, output_tokens=3)
        table = render_model_costs([unit], {unit: ModelCaseCost("complete", exact, {"model_calls": 1})})
        self.assertIn("| All cases | 1 / 0 / 0 / 0 | 0 (1/1 exact) | 10 (1/1 exact) | "
                      "1 (1/1 exact) | 125 (1/1 exact) | 3 (1/1 exact) | 0 (1/1 exact) |", table)

    def test_rejects_non_registered_or_impossible_observations(self):
        unit = ("E3", "clean", 3001, "purser")
        outside = ("E3", "primary", 3001, "purser")
        for schedule, cases in [([], {}), ([unit, unit], {}), ([unit], {outside: ModelCaseCost("complete", {})}),
                                ([("E3", "clean", 3002, "purser")], {}),
                                ([unit], {unit: ModelCaseCost("unknown", {})}),
                                ([unit], {unit: ModelCaseCost("complete", {"reserved": 2})}),
                                ([unit], {unit: ModelCaseCost("complete", {"model_calls": True})}),
                                ([unit], {unit: ModelCaseCost("arm_incomplete", {}, {"steps": -1})}),
                                ([unit], {unit: ModelCaseCost("arm_incomplete", {"steps": 2}, {"steps": 3})})]:
            with self.subTest(schedule=schedule, cases=cases), self.assertRaises(ValueError):
                render_model_costs(schedule, cases)
