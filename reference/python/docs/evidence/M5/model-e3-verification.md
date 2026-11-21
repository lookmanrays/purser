# E3 model participant qualification

Source `23718086b3a5536dfbfec11d83a7b7e6b4a3fbe8` includes adapter `0d8894dbe1b40fb00628027481781427381823b8`. The study lead can call `experiments.model_e3.run_case` with the registered case's `decide(prompt, deadline=...)` and `read_record` callbacks. `recover_case` consumes original sealed responses by ordinal. `reconstruct_case` takes the registration, population, service and authority identities and reads archived objects. Native refs, original Git writer status and model proposal attribution have separate sources.

On the Darwin host, 11 prospectively identified private controls made 29 HTTP requests to a deterministic loopback server. The two unlanded runs sent byte-identical first and second requests. The original Git writer had already exited before the second request in each run. One response selected inspection followed by a guarded retry. The other declared that the original had landed. The second case ended with the fixture branch still at its starting commit.

| Private control | Public steps | Model calls | Applied branch updates | Original reconciliation |
| --- | ---: | ---: | ---: | --- |
| Unlanded plain loop with inspection and retry | 7 | 4 | 1 | Correct |
| Same public timeout with immediate completion | 4 | 2 | 0 | Incorrect |
| Landed plain loop | 5 | 3 | 1 | Correct |
| Landed Purser | 5 | 2 | 1 | Correct |
| Landed loop with the equal verifier | 5 | 2 | 1 | Correct |
| Successful acknowledgment | 4 | 2 | 1 | Correct |
| Writer still live at cutoff | 5 | 2 | 0 | Unknown, publicly pending with a reason |
| Model deferred before the stress intervention | 2 | 1 | 0 | Unexposed |
| Guard refused a premature retry | 6 | 3 | 1 | Incorrect |
| Model disabled | 2 | 0 | 0 | Unavailable |
| Repeated inspections at the eight-call limit | 10 | 8 | 0 | Incorrect, token counts unavailable |

The retained file is `~/.local/state/purser/development/m5/e3-canned-23718086b3a5.zip`, 407,892 bytes. Its SHA-256 is `0db68581766d666fb77881c1cca1482d918a4a7066e62be58c4f43b42835337e`. The prospective schedule SHA-256 is `14e7c7d12e571e73b33266b9e52ae8316d9a88004260cb5e2b320a0ee294d8df`. The archive includes the qualification program, complete schedule, source manifest, 11 case registrations, raw files, readbacks, original model receipts, native witnesses and candidate packs. A fresh Python process with model calls and socket connections disabled checked all 371 archive member names, every case input and M0 schema, all 11 saved readbacks and final native reflog update counts. It reproduced all 29 recorded model calls. All provider responses in this archive are canned.

Two subprocess cuts exercise the consumption boundary. Killing collection while the second POST was in flight preserved the first source-backed model action, the witnessed unlanded original writer, four public steps and one confirmed transport attempt as a lower bound. Reopening the case made zero further requests. Killing collection after the second reply was sealed, before participant delivery, retained two original calls, 62 input tokens, 34 output tokens and zero measured charge. The native history included the unanswered public decision request. Only the first model choice had produced a consequent public action.

The following commands passed at the source revision.

```sh
python3.11 -B -m unittest -q tests.test_model_e3 tests.test_e3_report tests.test_e3_runner tests.test_model_adapter tests.test_model_adapter_restart
python3.14 -B -m unittest -q tests.test_model_e3
python3.11 -B -m unittest -q tests.test_e2_checkpoint_clock
```

The runs exercised 37, eight and six tests respectively. Independently, the original M4 CLI at `b99e7ddfc82f413e8d5b2a2b2dce9b4e7a3fb3fd` reconstructed the archived 66 of 66 scripted E3 cases with two Git packs and zero missing starts. It compared its rendered bytes to the published report, SHA-256 `6987e461ede49878282a179a8c9f10f9e9a6b3517ec63d087dcece8ccc648ef1`. The original archive SHA-256 was `1746104434cbc76cc04959b092268db6f71832befb04b1cde1830fcba0c441bc`.

These measurements cover the local E3 component, its transport and historical scripted reproduction. Genuine model trials and their scientific outcomes require the registered, authorized study.
