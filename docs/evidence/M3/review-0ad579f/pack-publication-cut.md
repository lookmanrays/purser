# E2 recovery between Git pack publication and the candidate map

On source `0ad579f7a39f033b2efe59a6f8f3d04ac1dabef0`, a parent process killed a private collector immediately after its case-local Git pack reached disk. The participant had yet to update the candidate map. The parent read the registration marker, restored the pack in an empty bare repository and verified its commit and tree. For each cut, one run kept the working checkout and a second set it aside before recovery. Each pair used its own registered private replicate on seed 2001, plain loop.

| Pack just published | Working checkout on restart | Candidate in the new pack | What the native reader reported |
| --- | --- | --- | --- |
| Starting fixture | Available | `55686bd…` | Zero packs and zero groups. Strict verification succeeded. |
| Starting fixture | Set aside | `55686bd…` | Zero packs and zero groups. Strict verification succeeded. |
| First edited candidate | Available | `6dcb12a…` | Only the starting commit was listed. Strict verification reported that the exit candidate’s identity was unavailable. |
| First edited candidate | Set aside | `6dcb12a…` | Only the starting commit was listed. Strict verification succeeded with one pack and two groups. |

All four resulting raw archives contained the complete newly written pack. Each restart processed the same original attempt, made zero new participant calls and produced an archive that stayed byte-identical on the next reopen. The source witness ties the pack to its registered case while the original stage still exists. A content-addressed pack shared by an aggregated archive can belong to several cases, so that hash alone cannot establish which of them observed the commit. Participant final outcomes remain unavailable in this control.

The [evidence packet](case-local-pack-cuts.zip) contains the original four archives, freezes, registration bundle, reproducer, stage observations and member hashes. Packet SHA-256 `34360fe37a8e7268a16b2f37c4640cc1f702a74cc1436a4c46266ba2a2866b05`. Original observation SHA-256 `1705397e1150b6dabb833805c761b8a2e8856ad4bc5bf6a651a9c270cae09cbd`.
