# Local notification queue

The queue schedules notification callbacks against explicit local times and buffers delivery records in memory. A caller enqueues one channel, a sequence of event IDs, the disabled channels, the number of attempts and a base delay. Advancing the queue returns the deliveries due through that instant in chronological order. New work for the exercise extends the three policy functions in `notify/`.

Run the existing service and scheduler compatibility checks with `python -B -m unittest discover -s tests`.

The local scheduler and selected tests come from [Schedule 1.2.2](https://pypi.org/project/schedule/1.2.2/). The sdist SHA-256 is `15fe9c75fe5fd9b9627f3f19cc0ef1420508f9f9a46f45cd0769ef75ede5f0b7`. The vendored module is unmodified. The bundled tests retain upstream lines 1–99 and 1273–1599 of `test_schedule.py`, covering registration, callable invocation, scheduled execution, ordering, tags, and cancellation with the standard library. Upstream’s MIT notice accompanies both files in `THIRD_PARTY_LICENSES/schedule.txt`. The Purser fixture code is distributed under the repository’s Apache-2.0 license.
