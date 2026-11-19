# M5 notification observer qualification

The bounded observer recovered the same raw queue results and public test outcomes as the original trusted scorer on the registered starting and prepared fixtures. A cold reader then reproduced the original measurements using their archived response bytes. The measured observer was `029b936859f1ef263ac5490da53b1e99803b2011`, Git tree `c14fc9b8785b0452f6d2c6c66d74fb8c1f058ad8`. The runtime came from `cbad3ffc32fcf90576f9873429a4593f9e529c8f`. All inputs in this qualification were synthetic. The model-call count was zero. [Observer preflight](isolation-preflight.md) · [Runtime qualification](wasi-host.md)

| Control | Observed result |
| --- | --- |
| Registered start `55686bd5152e10a53dfdc2c003974ea405a3a075`, E1 seed 1001 | All eleven queue traces and all 28 legacy tests were available. The full six-criterion report matched the trusted original byte for byte. Preferences, retry, delivery and composition failed. Scope and legacy passed. Report SHA-256 `6c61dbf438c7a5a909324c00134fa412430f633fb8f62594547009c66da4726d`. |
| Prepared commit `5828b906ebc2aca8bbaf69c8d50fa8eb1295af7b`, E1 seed 1001 | All six criteria passed. Report SHA-256 `913d441627b60ff70ea427f3b365fea67551e6d10fb6d8d070fc8cb26b4b504c`. |
| Prepared commit, E2 seed 2001, revised criterion v2 | Both retry and composition failed the exponential requirement. Every case and recorded value matched the original trusted scorer. |
| Benign working-copy change | An always-disabled preference caused three failures in the six original queue tests. Replacing the candidate's test file with a comment left all 28 registered tests in the controller's run. A controlled candidate import failure produced one original unittest loader error and 22 passing Schedule tests. |
| Evidence loss or substitution | The reader refused missing, mixed and interrupted invocation pairs, a guest-issued test summary, missing/extra operations, a changed expected input or candidate identity, modified stream bytes or digest, and an unsuccessful guest. |
| Interrupted execution | The test interrupted a real sleeping candidate, observed the guest host subprocess's termination and the removal of its workspace, and reopened the interrupted receipt as unavailable. The ordinary host tests also exercised memory refusal, finite fuel, a wall deadline with partial output, read-only mounts, an adjacent disposable canary, missing environment and denied sockets. |

## Reading an original

The collector gives `Observer(runtime, checkpoint=...)` a synchronous callback. The callback receives a start record before execution and a bounded terminal record. The controller runs the six original notification tests using 22 concrete calls to the candidate queue. The other 22 public tests execute against verified registered Schedule bytes. Checks that require a changed native library report unavailable. The external scorer compares the registered expected and observed values and owns the verdict.

The case archive must associate the two original content-addressed records with their public action or protected checkpoint. The offline reader accepts the independently recovered snapshot, those two records and the independently selected queue arguments or `legacy=True`. It verifies the original input, entry and runtime identities, resource disposition, and returned bytes. It re-executes the immutable public assertions on saved values for a legacy measurement. Both the collector and the reader use the same response decoder. [Python interface](../../../experiments/model_isolation/observer.py) · [End-to-end tests](../../../tests/test_model_isolation.py)

For the original example below the registered queue enqueued one disabled-channel delivery. The expected count was zero. The returned value and this comparison were reproduced after closing the guest. The JSON contains both original records in full. The reference hashes use the fixture's canonical JSON encoding.

```json
{
  "case": {
    "attempts": 1,
    "base": 2,
    "channel": "mail",
    "disabled": [
      "mail"
    ],
    "ids": [
      "1001-a"
    ],
    "times": [
      0,
      1
    ]
  },
  "expected": {
    "enqueued": 0,
    "pending": 0,
    "samples": [
      [],
      []
    ]
  },
  "original": [
    {
      "candidate": "55686bd5152e10a53dfdc2c003974ea405a3a075",
      "entry_sha256": "795a797c352bf3bc8a8ad7dcaac17eb56f81d7a7602dc92c36f801eb31e76c84",
      "fuel_limit": 16000000000,
      "guest_archive_sha256": "b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888",
      "host_archive_sha256": "ff4fc58ed3cbdb919d056accd4d280be13962a542754aaefd8a9b4654efa9bd9",
      "invocation": "c8cab77936304eb3b46da325256c4bbc",
      "manifest_sha256": "1d917cc4ab09d5bd9a53b94dae9d585acaffa72a71739f5630b81095daa3cf22",
      "memory_bytes": 67108864,
      "mode": "queue",
      "output_limit": 262144,
      "phase": "started",
      "protocol": "purser.m5.fixture-observation.v1",
      "stdin": {
        "base64": "eyJjYXNlcyI6W3siYXR0ZW1wdHMiOjEsImJhc2UiOjIsImNoYW5uZWwiOiJtYWlsIiwiZGlzYWJsZWQiOlsibWFpbCJdLCJpZHMiOlsiMTAwMS1hIl0sInRpbWVzIjpbMCwxXX1dLCJtb2RlIjoicXVldWUiLCJyb290IjoiL2ZpeHR1cmUifQ==",
        "bytes": 136,
        "sha256": "8b1d2fcf763933653ec3e86e8c78b45378dc5b893d0a425ba2b7517fba39e1d2"
      },
      "tree": "2afea0fdd3478d29447a40bde8007266c468c72b",
      "wall_limit_seconds": 20
    },
    {
      "candidate": "55686bd5152e10a53dfdc2c003974ea405a3a075",
      "elapsed_ns": 3972067917,
      "entry_sha256": "795a797c352bf3bc8a8ad7dcaac17eb56f81d7a7602dc92c36f801eb31e76c84",
      "exit_code": 0,
      "fuel_limit": 16000000000,
      "fuel_used": 4492281139,
      "guest_archive_sha256": "b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888",
      "guest_state": "completed",
      "host_archive_sha256": "ff4fc58ed3cbdb919d056accd4d280be13962a542754aaefd8a9b4654efa9bd9",
      "invocation": "c8cab77936304eb3b46da325256c4bbc",
      "manifest_sha256": "1d917cc4ab09d5bd9a53b94dae9d585acaffa72a71739f5630b81095daa3cf22",
      "measured_sha256": "5b2c06b6631633758ad0e35f5b5a2ce710abedd31f838c0de1a4860a0776ee64",
      "memory_bytes": 67108864,
      "mode": "queue",
      "output_limit": 262144,
      "phase": "measured",
      "protocol": "purser.m5.fixture-observation.v1",
      "stderr": {
        "base64": "",
        "bytes": 0,
        "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
      },
      "stdin": {
        "base64": "eyJjYXNlcyI6W3siYXR0ZW1wdHMiOjEsImJhc2UiOjIsImNoYW5uZWwiOiJtYWlsIiwiZGlzYWJsZWQiOlsibWFpbCJdLCJpZHMiOlsiMTAwMS1hIl0sInRpbWVzIjpbMCwxXX1dLCJtb2RlIjoicXVldWUiLCJyb290IjoiL2ZpeHR1cmUifQ==",
        "bytes": 136,
        "sha256": "8b1d2fcf763933653ec3e86e8c78b45378dc5b893d0a425ba2b7517fba39e1d2"
      },
      "stdout": {
        "base64": "eyJtZWFzdXJlZCI6W3siZW5xdWV1ZWQiOjEsInNhbXBsZXMiOltbWyIxMDAxLWEiLCJtYWlsIiwwXV0sW11dLCJwZW5kaW5nIjowfV19",
        "bytes": 78,
        "sha256": "0aa5bbdf1fdb7ab887369c85002f2a06ad025900107a8591491c36148696e5ba"
      },
      "tree": "2afea0fdd3478d29447a40bde8007266c468c72b",
      "wall_limit_seconds": 20
    }
  ],
  "refs": [
    {
      "bytes": 968,
      "path": "objects/cbddb4c9a3beded35cb39cbb0f39f4fd3e17cad512a63e72db0a999d81c88cb2.json",
      "sha256": "cbddb4c9a3beded35cb39cbb0f39f4fd3e17cad512a63e72db0a999d81c88cb2"
    },
    {
      "bytes": 1464,
      "path": "objects/3818a0f293e8409c0f741930218e8964c3b7df3430c84f8921265d26b62beff8.json",
      "sha256": "3818a0f293e8409c0f741930218e8964c3b7df3430c84f8921265d26b62beff8"
    }
  ],
  "result": [
    {
      "enqueued": 1,
      "pending": 0,
      "samples": [
        [
          [
            "1001-a",
            "mail",
            0
          ]
        ],
        []
      ]
    }
  ]
}
```

## Reproduction and scope

These tests ran with ordinary user permissions on Darwin 25.5.0 arm64, Apple Git 2.50.1, native CPython 3.11.15 and 3.14.3. The two pinned runtime archives supply Wasmtime 50.0.1 and CPython 3.15.2 WASI. Their identities are included in each invocation. The guest has one 64 MiB linear memory, 16 billion Wasmtime fuel units, a 20-second elapsed deadline and a combined 256 KiB stdout/stderr ceiling. The controller and native compiler use additional host memory. [Archive identities and local setup](wasi-host.md)

```sh
PURSER_WASI_ARCHIVES="$HOME/.local/state/purser/development/m5/isolator-feasibility" python3.11 -B -m unittest -q tests.test_model_isolation tests.test_wasi_runtime tests.test_e1_measurements
PURSER_WASI_ARCHIVES="$HOME/.local/state/purser/development/m5/isolator-feasibility" python3.14 -B -m unittest -q tests.test_model_isolation tests.test_wasi_runtime
```

The 3.11 command passed all 28 tests in 57.0 seconds. The 3.14 command passed all 16 in 31.1 seconds when run alone. An earlier 3.14 run overlapped the 3.11 run and returned three test errors, including a native isolated host process exit. Those guest observations were unavailable. A subsequent reading on this 24 GiB Mac showed 16.4 GiB of swap in use. The process exit's cause remains unconfirmed. A separate 3.11 process without the runtime-archive environment variable read both published example records, verified both content addresses and recovered the original failed queue comparison without invoking a guest.

The candidate and queue instrumentation share a guest Python interpreter. Returned values therefore describe the observed guest interface. The controller supplies the concrete calls and keeps protected expectations outside it. The qualified fixture uses pure Python and naive queue datetimes. Code that depends on native extensions, the host timezone or other host-specific behavior can behave differently in WASI. A public working-copy observation binds its captured file bytes and modes. Protected reports require the archived Git commit. This qualification covers execution of the notification fixture and its first observer. The full coding agent, model trials and human disposition belong to their respective milestones.
