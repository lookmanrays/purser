"""Render measured cost and coverage for one registered model study."""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from experiments.e1.artifacts import MANIFEST


Unit = tuple[str, str, int, str]
FIELDS = ("steps", "elapsed_ns", "model_calls", "input_tokens", "output_tokens", "spend_usd_micros")
VALIDITIES = ("complete", "arm_incomplete", "infrastructure_invalid")
ARMS = MANIFEST["units"]["arm_order"]


@dataclass(frozen=True)
class ModelCaseCost:
    """Costs independently measured for one started model case, in the M0 units.

    Missing values may be absent or None. A reservation for an unreplied request
    is an upper bound on a possible charge and does not belong in either map.
    """

    validity: str
    exact: Mapping[str, int | None]
    lower_bounds: Mapping[str, int | None] = field(default_factory=dict)

    def validate(self):
        if self.validity not in VALIDITIES:
            raise ValueError("unrecognized model case validity")
        for values in (self.exact, self.lower_bounds):
            if values.keys() - set(FIELDS):
                raise ValueError("unrecognized model cost field")
            if any(v is not None and (type(v) is not int or v < 0) for v in values.values()):
                raise ValueError("cost evidence must be nonnegative integers or unknown")
        for name, bound in self.lower_bounds.items():
            exact = self.exact.get(name)
            if bound is not None and exact is not None and bound > exact:
                raise ValueError(f"lower bound exceeds the exact {name}")


def _display_amount(name, value):
    digits = {"elapsed_ns": 9, "spend_usd_micros": 6}.get(name, 0)
    if not digits:
        return str(value)
    whole, fraction = divmod(value, 10 ** digits)
    return f"{whole}.{fraction:0{digits}d}".rstrip("0").rstrip(".")


def _cell(name, cases, scheduled):
    total = exact_count = lower_count = 0
    for case in cases:
        exact = case.exact.get(name)
        if exact is not None:
            total += exact
            exact_count += 1
        elif (minimum := case.lower_bounds.get(name)) is not None:
            total += minimum
            lower_count += 1
    coverage = f"{exact_count}/{scheduled} exact"
    if lower_count:
        coverage += f", {lower_count} lower bound" + ("s" if lower_count != 1 else "")
    if not exact_count and not lower_count:
        return f"? ({coverage})"
    prefix = "" if exact_count == scheduled else "≥"
    return f"{prefix}{_display_amount(name, total)} ({coverage})"


def _row(label, units, cases):
    present = [cases[unit] for unit in units if unit in cases]
    coverage = Counter(c.validity for c in present)
    counts = [coverage[v] for v in VALIDITIES] + [len(units) - len(present)]
    cells = [label, " / ".join(map(str, counts)), *(_cell(name, present, len(units)) for name in FIELDS)]
    return "| " + " | ".join(cells) + " |"


def render_model_costs(schedule: Iterable[Unit], cases: Mapping[Unit, ModelCaseCost]) -> str:
    """Render only the supplied replicate, using its already-verified registration and case evidence."""
    units = [tuple(unit) for unit in schedule]
    registered = set(units)
    if not units or len(registered) != len(units):
        raise ValueError("model cost table requires a nonempty schedule of distinct units")
    if cases.keys() - registered:
        raise ValueError("model cost evidence outside the registered schedule")
    for case in cases.values():
        case.validate()

    by_track = defaultdict(lambda: defaultdict(list))
    by_arm = defaultdict(list)
    for unit in units:
        experiment, track, seed, arm = unit
        study = MANIFEST["experiments"].get(experiment)
        if study is None or arm not in ARMS or type(seed) is not int:
            raise ValueError("unknown model cost unit")
        stress = study.get("stress")
        if not ((track == "primary" and seed in study["seeds"])
                or (track == "clean" and seed == study["clean_seed"])
                or (stress and track == stress["id"] and seed == stress["seed"])):
            raise ValueError("unregistered experiment track or seed")
        by_track[experiment, track][arm].append(unit)
        by_arm[arm].append(unit)

    lines = ["## Model costs and coverage", "",
             "Each cost shows how many scheduled cases have an exact measurement. A ≥ total includes "
             "the available exact measurements and any observed lower bounds. A question mark means "
             "the original evidence supplied no measurement for that field. Elapsed time sums the "
             "participants’ active time. Spend uses measured charges in US dollars.", "",
             "| Population | Complete / incomplete / invalid / missing | Steps | Elapsed s | Model calls | Input tokens | Output tokens | Spend USD |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
             _row("All cases", units, cases)]
    for arm in ARMS:
        if arm in by_arm:
            lines.append(_row(f"All · {arm}", by_arm[arm], cases))
    for (experiment, track), arms in by_track.items():
        for arm in ARMS:
            if arm in arms:
                lines.append(_row(f"{experiment} · {track} · {arm}", arms[arm], cases))
    return "\n".join([*lines, ""])
