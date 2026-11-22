# Conformance Vectors

This directory contains frozen vectors copied from the reference implementation (`purser-main`) and golden event logs recorded from reference test scenarios.

## Vector Section Mapping

| Vector / Section | Consuming Packages | Description |
| ---------------- | ------------------ | ----------- |
| `purser-v1/wire-v1.json` (`encoding_vector`, `events`) | P1-01 (codec) | Canonical UTF-8 JSON encoding, hashes, event envelope validation |
| `purser-v1/wire-v1.json` (`clock_by_batch`, batches) | P1-02a (projection) | Pure reducer registry, event folding across batch boundaries |
| `purser-v1/wire-v1.json` (`admission_replies`, `independent_case_branches`) | P1-03 (gate) | Admission checks, receipt matching, conflict detection |
| `purser-v1/lifecycle-v1.json` (trace steps, outcomes, overlays) | P1-04 – P1-08b (kernel ops) | Full domain lifecycle: work, decisions, effects |
| `purser-v1/m2-legacy-status-v1.json` (status checkpoints) | P1-09 (cli) | CLI status, why, replay, and query replies |
| `golden/**` (scenarios) | P1-02a, P1-04, P1-07, P1-08a | Golden event chains and snapshots covering all sixteen v1 record kinds |

## Manifest

All files in this directory are checksummed in `MANIFEST.sha256`. The manifest is verified by CI and loader tests to ensure no vector drifts or changes silently.
