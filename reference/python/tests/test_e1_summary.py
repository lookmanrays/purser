import unittest
from types import SimpleNamespace

from experiments.e1.report import Case
from experiments.e1.summary import render_summary


def observed(track="primary", *, named=True, caught=True, false=False, steps=30):
    facts = dict(false_parent_closure=false, missing_named=named, caught=caught,
                 correct_parent_closure=True, extra_diagnoses=int(not named))
    evidence = SimpleNamespace(facts=facts, final_parent=("closed", "G"), score=lambda primary: facts,
                               correct_candidates=["G"], false_candidates=["X"] if false else [])
    return Case({"track":track, "validity":"complete", "terminal":"done",
                 "cost":{"steps":steps, "elapsed_ns":steps * 1000}}, evidence, "exact", (), ("closed", "G"), True)


class SummaryBounds(unittest.TestCase):
    def test_partial_denominators_can_establish_or_disprove_each_threshold(self):
        rows = {("primary", seed, "loop"): observed() for seed in range(1001, 1020)}
        rows.update({("primary", seed, "purser"):observed(named=False, caught=False) for seed in (1001, 1002)})
        output = render_summary(rows)
        self.assertIn("absolute E1 threshold for Purser was **missed**", output)
        self.assertIn("no-advantage threshold for the plain loop was **met**", output)
        del rows["primary",1002,"purser"]
        del rows["primary",1019,"loop"]
        self.assertEqual(render_summary(rows).count("was **inconclusive**"), 2)
        rows.update({("primary",seed,"loop"):observed(caught=False) for seed in (1019,1020)})
        self.assertIn("no-advantage threshold for the plain loop was **missed**", render_summary(rows))
        rows.update({("primary",seed,"purser"):observed() for seed in range(1002,1021)})
        self.assertIn("absolute E1 threshold for Purser was **met**", render_summary(rows))

    def test_one_available_comparator_and_equality_at_the_clean_limit(self):
        rows = {("primary",1001,"purser"): observed(steps=50),
                ("primary",1001,"loop_equal_verifier"): observed(steps=50),
                ("clean",1001,"purser"): observed("clean",steps=48), ("clean",1001,"loop"): observed("clean",steps=24)}
        output = render_summary(rows)
        self.assertIn("| 1001 | ? / ? / unavailable | +0 / +0 / +0 / +0.000 s |", output)
        self.assertIn("within the twice-baseline limit", output)
        rows["clean",1001,"purser"] = observed("clean",steps=49)
        self.assertIn("exceeded the twice-baseline limit", render_summary(rows))
