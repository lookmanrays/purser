"""Participant identities shared by model cost reporting and spending reservations."""

from experiments.e1.artifacts import MANIFEST


Unit = tuple[str, str, int, str]


def validate_unit(unit: Unit):
    experiment, track, seed, arm = unit
    study = MANIFEST["experiments"].get(experiment)
    if study is None or arm not in MANIFEST["units"]["arm_order"] or type(seed) is not int:
        raise ValueError("unknown model case")
    stress = study.get("stress")
    if not ((track == "primary" and seed in study["seeds"])
            or (track == "clean" and seed == study["clean_seed"])
            or (stress and track == stress["id"] and seed == stress["seed"])):
        raise ValueError("unregistered experiment track or seed")
