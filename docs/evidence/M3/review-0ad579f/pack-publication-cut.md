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

An independent reviewer restored all four packs into empty Git repositories and exercised the original queue against each observed commit. Both starting-commit cases supplied eight criterion results. Both first-edit cases supplied sixteen across the two observed commits. The native records had captured zero and eight, respectively. In a separate hand case, the original estimator counted two reopenings when W1 and W3 changed from closed to open in the same publication. In the recorded case where W3 reopened twice, it counted two transitions and one distinct work item.

The reviewer approved recovery design `dd24d464d6da9d8ba589873e48652366c6141ea449bfa6066854e361c41be19f` on base `bdf316d`. The revised design binds each observed commit to its case, grades each public declaration on its own commit, and counts each subject that reopened. The [independent readback](independent-cut-readback.zip) contains the observations, exact hand input and two design dispositions. Its SHA-256 is `f763cdcbacbfd1cce1bf435c09e2bd896b1729cffd0fc6c35504adf33b1d4b32`. Implementation qualification and the registered study follow this design review.
