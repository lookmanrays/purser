# Scripted E1 results

The units are the preregistered paired notification tasks. Each arm used the same fixture, seed, local tool budget and worker policy.

| Arm | Completed / 20 | False parent closures | Missing obligation named | Caught | Correct parent closure |
| --- | ---: | ---: | ---: | ---: | ---: |
| loop | 20 | 0 | 20 | 20 | 20 |
| purser | 20 | 0 | 20 | 20 | 20 |
| loop_equal_verifier | 20 | 0 | 20 | 20 | 20 |

The absolute E1 threshold for Purser was **met**. The original no-advantage threshold for the plain loop was **met**.

Each cell gives the outcome and number of steps. The last two columns show paired differences in steps and measured seconds.

| Seed | Plain loop | Purser | Equal verifier | Purser − plain | Purser − equal verifier |
| ---: | --- | --- | --- | ---: | ---: |
| 1001 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.456 s | +0 / +0.205 s |
| 1002 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.643 s | +0 / +0.371 s |
| 1003 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.523 s | +0 / +0.297 s |
| 1004 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.425 s | +0 / +0.309 s |
| 1005 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.456 s | +0 / +0.194 s |
| 1006 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.440 s | +0 / -0.002 s |
| 1007 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.410 s | +0 / +0.097 s |
| 1008 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.558 s | +0 / +0.328 s |
| 1009 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.376 s | +0 / +0.250 s |
| 1010 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.480 s | +0 / +0.190 s |
| 1011 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.751 s | +0 / +0.512 s |
| 1012 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.513 s | +0 / +0.262 s |
| 1013 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.473 s | +0 / +0.257 s |
| 1014 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.567 s | +0 / +0.326 s |
| 1015 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.405 s | +0 / +0.259 s |
| 1016 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.495 s | +0 / +0.282 s |
| 1017 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.662 s | +0 / +0.377 s |
| 1018 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.537 s | +0 / +0.310 s |
| 1019 | caught / 31 | caught / 52 | caught / 52 | +21 / +0.501 s | +0 / +0.315 s |
| 1020 | caught / 31 | caught / 58 | caught / 58 | +27 / +0.570 s | +0 / +0.355 s |

## Clean control

| Arm | Terminal | Steps | Elapsed seconds | Correct close |
| --- | --- | ---: | ---: | --- |
| loop | done | 24 | 0.891 | True |
| purser | done | 42 | 1.456 | True |
| loop_equal_verifier | done | 42 | 1.158 | True |

Purser used 42 clean steps and the plain loop used 24. Purser was within the twice-baseline overhead limit.
All model-call, token and spend counts are zero.
