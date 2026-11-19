# Contributing to Purser

Run commands from the root of a full Git clone in a POSIX shell. The CLI uses Python 3.11+ with SQLite. The governed Git effect and its tests also need Git with reftable support. Project qualification used CPython 3.11.15, SQLite 3.53.1 and Git 2.50.1 on an arm64 Mac. The fixture-execution tests use two additional archives on that host.

## Run the first checks

```sh
python3.11 -B docs/contracts/check_vectors.py
python3.11 -B -m unittest -q tests.test_cli tests.test_store
python3.11 -B -m docs.evidence.M4.project_trace docs/evidence/M4/project-ledger.zip
```

The first command checks the frozen wire examples. The second exercises the CLI and SQLite store. The third restores a real Purser development milestone into a temporary database and checks its closure at revision 68, its ten evidence objects and its CLI answers. The committed archive supplies the inputs for any contributor.

## Run the full suite

On the qualified arm64 Mac, put the Wasmtime 50.0.1 macOS arm64 wheel and the CPython 3.15.2 WASI SDK 33 ZIP in one directory. Keep their original filenames. The runtime verifies their SHA-256 digests. [Archive sources and checksums](docs/evidence/M5/wasi-host.md)

```sh
export PURSER_WASI_ARCHIVES="/absolute/path/to/the/archive-directory"
python3.11 -B -m unittest discover -s tests -t . -v
```

The recorded full run passed 310 tests with zero skips. Read the skipped count on your own run. Without the fixture runtime, some guest and model-study tests skip. The tests use disposable repositories, databases and controlled local responses. [Test inventory and qualification](docs/evidence/M5/review-3308ef7/verification.md#repository-tests)

## Find the implementation

| Area | Where to start |
| --- | --- |
| CLI, current and historical queries, foreground driver | [CLI](purser/cli.py), [queries](purser/queries.py), [driver](purser/driver.py) |
| Admission, SQLite history, projections, work, evidence, decisions and grants | [Kernel](purser/kernel/), [wire and domain contracts](docs/contracts/) |
| Local Git publication and recovery | [Effect adapter](purser/effects/), [launcher and CLI contract](docs/contracts/git-effects-v1.md#running-a-governed-effect) |
| Local model transport, study participants, scorer and reports | [Model adapter](purser/adapters/model.py), [experiments](experiments/), [model-study commands](experiments/model_study/README.md) |

The original plan assigns work to implementation roles. The table above gives the layout in the current checkout. Use the delivery record for milestone acceptance, amendments and remaining work. [Delivery record](docs/delivery.md#current-implementation)

## Reproduce the published studies

To read the results, open the published report. To regenerate it, use the result commit in the same row. The reader, frozen inputs and retained evidence then come from the same checkout.

| Scripted study | Result commit | Module | Published report in this repository |
| --- | --- | --- | --- |
| E1 original | `bb36a12` | `experiments.e1` | [e1-scripted-v1](experiments/results/e1-scripted-v1/report.md) |
| E1 accepted replication | `f47abf8` | `experiments.e1` | [e1-scripted-v2](experiments/results/e1-scripted-v2/report.md) |
| E2 changed premise | `c7459fa` | `experiments.e2` | [e2-scripted-v1](experiments/results/e2-scripted-v1/report.md) |
| E3 interrupted Git update | `79e963b` | `experiments.e3` | [e3-scripted-v1](experiments/results/e3-scripted-v1/report.md) |

For example, use a disposable clone to check the original E3 archive and compare the generated Markdown with the published file. The strict check reports 66 recorded cases.

```sh
reader=$(mktemp -d)
git clone --quiet . "$reader/purser"
(
  cd "$reader/purser" || exit 1
  git switch --quiet --detach 79e963b
  python3.11 -B -m experiments.e3 verify experiments/results/e3-scripted-v1
  python3.11 -B -m experiments.e3 report experiments/results/e3-scripted-v1 > "$reader/e3-report.md"
  cmp "$reader/e3-report.md" experiments/results/e3-scripted-v1/report.md
)
```

The current E1 reader reproduces the accepted replication. For the first E1 series it emits a later interpretation, so use the original result commit for a byte-for-byte comparison. E2 and E3 enforce their registered source. To collect a new series, use an unused directory and replicate number, record the reason and commit its registration before collection. [E1](experiments/e1/README.md) · [E2](experiments/e2/README.md) · [E3](experiments/e3/README.md)

For M5, `python3.11 -B -m experiments.model_study report` shows the uncollected real-model schedule. To reproduce the separate 195-case **controlled** run on the current source, use the committed archive. This command reads the original responses offline.

```sh
study=$(mktemp -d)
tar -xJf docs/evidence/M5/review-3308ef7/full-schedule-control.tar.xz -C "$study"
python3.11 -B -m experiments.model_study report "$study/series" > "$study/report.md"
shasum -a 256 "$study/report.md"
```

The recorded report digest is `97d87e14632cd44c138e4fc59fadbe41c5589c6bb03faa4e91d8d98031f46d0c`. [Full-schedule audit](docs/evidence/M5/review-3308ef7/full-schedule-audit.json)
