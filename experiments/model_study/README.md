# Notification model study

The study compares the plain plan, Purser and a plan equipped with the same public verifier. Each case starts a fresh participant. The default report displays all 195 scheduled units, including primary, clean and stress rows with no originals.

```sh
python3.11 -m experiments.model_study report
```

## Reproduce the local qualification

Use the registered arm64 macOS WASI host and guest archives for behavioral checks. The study tests start a counted loopback server, register a temporary zero-price authority, exercise the normal CLI and rebuild a report after the server has stopped. The controls vary returned source, close an incorrect plain plan, change an operative retry decision, reopen a sibling, and interrupt model collection before or after a send.

```sh
PURSER_WASI_ARCHIVES=/path/to/qualified/archives python3.11 -m unittest -v tests.test_model_study
```

## Run a registered series

Use the source checkout recorded by the series. Its `freeze.json` binds source files, the three treatments, scorer, fixture, case order, model settings, observer, conditions and the shared spending database. Prepare the conditions file with the exact `LocalEndpoint.profile()`, serialized `SpendingAuthority`, an `observer` object containing its status and qualification identity, and `data` set to `synthetic-notification-and-arm-public-context`. The budget database must already be enrolled for the approved service. A disabled control uses `{"disabled":true}` as its service and omits the database. The control setup in [the study tests](../../tests/test_model_study.py) produces a complete working example.

```sh
python3.11 -m experiments.model_study freeze "$series" --population canned_control \
  --conditions "$conditions" --budget "$budget"
python3.11 -m experiments.model_study collect "$series" --unit E2/primary/2001/purser \
  --wasi-archives "$archives"
python3.11 -m experiments.model_study report "$series"
```

For an authorized original, the architect freezes the `model` population, commits its registration and collects the entire schedule. A repeat uses a fresh directory, its own replicate number and `--reason`. Canned and disabled populations have separate identities. Public actions count from initial assignment release. Each E1/E2 model choice and its first work action share one step, and subsequent checks, submissions, observations and decisions each have their own journal entry.

Each unit has a directory under `cases/EXPERIMENT/TRACK/SEED/ARM/`. Its start marker and progress establish the original attempt. Re-entering collection recovers that attempt from the transport, budget, Git and participant evidence. The collector publishes `raw.json` last. `session.json` points to the common original request and receipt trace. The raw row links the participant journal, retained Git candidates, platform evidence and experiment-specific model trace. An E3 reply recovered after process loss can establish cost and exposure while the participant journal still shows no applied model action.

The offline report reads the registration and content-addressed artifacts. It reconstructs E1/E2 public actions, candidate bytes and isolated observations, and uses the E3 reader for its Git endpoints. The cost tables preserve field-specific lower bounds and unknowns. The original observations and the human v1 decision belong to the integrated project study.
