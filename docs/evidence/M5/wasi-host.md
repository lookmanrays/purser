# M5 fixture execution on the reference Mac

The disposable WASI host ran the frozen notification service and a controlled edit to delivery deduplication. The outside controller compared 11 returned queue traces against the enrolled E1 seed 1001. Four matched with the starting fixture and six matched after the edit. All 11 traces were available in each run. These are component controls using known source bytes.

| Observed boundary | Result |
| --- | --- |
| File and environment access | A guest read its supplied fixture. An overwrite and a new file failed. Opening the actual absolute path of an adjacent disposable host canary failed, its bytes survived, and the host marker environment variable was absent. |
| Network | Creating a socket failed inside the guest. |
| Resources | A small allocation succeeded and an allocation larger than the 64 MiB guest memory failed. A finite arithmetic workload stopped at its fuel limit. A sleeping guest was reaped at its wall deadline, the parent retained the output it had already produced, and the next process ran. |
| Output and disposition | The combined stdout/stderr cap was enforced across both streams. An explicit process exit of 7, exhausted fuel, exhausted output and expired elapsed time had distinct dispositions. |
| Missing runtime | A missing or substituted archive prevented execution. The parent checked the exact bytes it subsequently unpacked. |

The runtime uses arm64 macOS, a private extracted Wasmtime 50.0.1 host binding and CPython 3.15.2 compiled for WASI. It grants read-only access to the guest standard library, the disposable fixture and its entry point. The memory limit applies to the guest’s single WebAssembly linear memory. Native compilation and the trusted controller also use host memory. This is a Python-fixture execution boundary. M6 has a separate real coding-agent requirement.

The caller passes the directory holding two archives to `WasiRuntime`. On this machine the files reside under `~/.local/state/purser/development/m5/isolator-feasibility`. The runtime validates both archives against these identities every time it opens its context.

| Archive | SHA-256 |
| --- | --- |
| [Wasmtime macOS arm64 wheel](https://pypi.org/project/wasmtime/50.0.1/#files) | `ff4fc58ed3cbdb919d056accd4d280be13962a542754aaefd8a9b4654efa9bd9` |
| [CPython 3.15.2 WASI SDK 33 ZIP](https://github.com/brettcannon/cpython-wasi-build/releases/tag/v3.15.2) | `b8a63559c56760bbdc310dedb74fd69d10bcbafae26b705bea0ad353ad256888` |

For the test command below, set `PURSER_WASI_ARCHIVES` to that archive directory. Python 3.11.15 and 3.14.3 each passed all 17 cases on 19 November 2026. The commands included both process-loss and concurrent-spending controls. The separately retained queue, output, memory, fuel and timeout observations are in `foreground-wasi-qualification.json` in the local archive directory, SHA-256 `39dfc89731849ccda125bc9aec6ca951165f8067a0843395bcc872b1abe7b4e1`. The normal foreground executor’s lost-response control is in `../foreground-budget-loss-control.json`, SHA-256 `ec442678e8acbdd17364e5fcbcd51bbb7f55e9482ecf6bf469cefff41e86283e`. These checks made zero external model calls. [Tests](../../../tests/test_wasi_runtime.py) · [Budget tests](../../../tests/test_model_budget.py)

```sh
python -B -m unittest tests.test_model_costs tests.test_model_budget tests.test_wasi_runtime -q
```

## Independent observer comparison

The architect exercised observer commit `1459ee60dd4cfe1da5270fd5062b905f9e01719e` against the original native scorer using only trusted fixture edits, E2 seed 2017 and rank 17. All 24 criterion verdicts and their underlying case reports matched byte for byte. The four candidates exercised the following outcomes.

| Candidate and criterion | Failed checks | Original compatibility tests passed |
| --- | --- | ---: |
| Linear implementation under the original requirement | None | 28 |
| Same implementation after the exponential retry requirement | Retry and queue composition | 28 |
| Revised implementation under the new requirement | None | 28 |
| Revised implementation plus an outside-scope note | File scope | 28 |

The control record identifies the concrete input and output of each of the eight guest invocations by SHA-256 and includes elapsed time and fuel consumption. The original scorer regraded the observed rows. The integration commit is `a2157b82370c0d286e09ae8f9aaac264af84bbd4`. The retained local result is `~/.local/state/purser/development/m5/observer-review-1459ee60d.json`, SHA-256 `690439c0bc2ae60abeda0940334c1b649339d536960878d3ae4b169d77a73923`.
